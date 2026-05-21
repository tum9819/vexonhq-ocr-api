"""
Auto-diagnosis pipeline (P1.4 MVP, Session 24) — L2 of the stability roadmap.

When /health/deep detects an unhealthy state, this module:
  1. Asks Claude Haiku to read the error details and explain likely cause + fix
  2. Posts the diagnosis to the VEXONHQ Ops Discord channel

Intentionally separated from the existing alerting pipeline:
  - Uptime Robot already fires its own "Monitor is DOWN" message to Discord
  - This module fires a SECOND message ~5s later with the AI diagnosis
  - The two messages together give TUM (on mobile, somewhere outside the
    shop) both the fact of the outage and a hypothesis about why

Why not patch automatically?
  - Decided in Session 24: restart can be auto, code patches must be
    human-reviewed. The Phase 32 incident (commit 742b618 silently
    deleting /inventory/ai-order-advice) is the case study.

Env vars required (gracefully skipped if missing):
  ANTHROPIC_API_KEY        — from console.anthropic.com, set $5/mo spend cap
  DISCORD_OPS_WEBHOOK_URL  — Discord channel webhook URL (already used by
                              Uptime Robot integration; reusing the same URL)

Cost budget: ~฿1-4/month at expected outage frequency (Haiku 4.5,
small prompt + small response). Rate-limited to 1 diagnosis per error_type
per 10 minutes to bound runaway billing.
"""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.request
from typing import Any, Optional

try:
    import discord_interactions as _di  # P1.4 v2 — Bot API send path
except Exception:  # pragma: no cover — module ships in same repo
    _di = None  # type: ignore[assignment]

log = logging.getLogger("auto_diagnose")

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
DISCORD_OPS_WEBHOOK_URL = os.environ.get("DISCORD_OPS_WEBHOOK_URL", "")

# Model choice rationale: Haiku 4.5 is more than enough to read a
# Postgres / Supabase error string and explain it in 3-5 sentences.
# Upgrade to Sonnet later only if Haiku diagnoses prove off-target.
ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_DIAGNOSE_MODEL", "claude-haiku-4-5")

# Rate-limit window per error_type (seconds). Prevents the every-5-min
# Uptime Robot polling from spamming Discord during a sustained outage.
RATE_LIMIT_SECONDS = 600  # 10 minutes

# In-process state — resets on backend restart, which is fine because
# a restart usually means the outage was resolved.
_last_diagnosis_at: dict[str, float] = {}


# ──────────────────────────────────────────────────────────────────
# Rate limiting
# ──────────────────────────────────────────────────────────────────
def should_diagnose(error_type: str) -> bool:
    """
    Return True if we should fire a fresh diagnosis for this error type.

    Same error within RATE_LIMIT_SECONDS is suppressed to avoid Discord
    spam + Anthropic billing during a sustained outage. A different
    error_type (e.g. postgres_failed vs supabase_failed) still fires
    its own diagnosis.
    """
    now = time.time()
    last = _last_diagnosis_at.get(error_type, 0.0)
    if now - last < RATE_LIMIT_SECONDS:
        log.info(
            "auto_diagnose: skipping %s — last diagnosis was %.0fs ago",
            error_type, now - last,
        )
        return False
    _last_diagnosis_at[error_type] = now
    return True


# ──────────────────────────────────────────────────────────────────
# Anthropic Claude API call
# ──────────────────────────────────────────────────────────────────
DIAGNOSIS_SYSTEM_PROMPT = """You are an SRE assistant for VEXONHQ, a Thai restaurant accounting system.

The /health/deep endpoint just reported an unhealthy state. You receive
the structured check results (which probes failed, error strings, latencies).

Output a Discord-friendly diagnosis in Thai mixed with English (the
operator is Thai, code/errors are English). Keep it tight: 3-5 short
sentences. Structure:

1. **อาการ** (one sentence on what failed)
2. **น่าจะเป็นเพราะ** (most likely cause, 1-2 sentences — be specific to
   the error string, not generic)
3. **ลองทำ** (suggested action — prefer no-code actions first: wait
   N minutes for self-recovery, restart container in Coolify, check
   Supabase dashboard. Only suggest code changes if the error clearly
   points at a code bug)

If the error is ambiguous, say so honestly — do not invent a cause.
Output as Discord-flavored markdown. NO preamble, NO sign-off.
"""


def _call_claude(check_results: dict[str, Any]) -> Optional[str]:
    """
    Send check results to Claude Haiku, return diagnosis text.

    Returns None on any error (logged, never raised — this runs as
    BackgroundTask and must not crash the response).
    """
    if not ANTHROPIC_API_KEY:
        log.warning(
            "auto_diagnose: ANTHROPIC_API_KEY not set — skipping diagnosis"
        )
        return None

    user_msg = (
        "Here are the /health/deep check results from VEXONHQ backend:\n\n"
        f"```json\n{json.dumps(check_results, indent=2, ensure_ascii=False)}\n```\n\n"
        "Diagnose the most likely cause and suggest the safest next action."
    )

    payload = {
        "model": ANTHROPIC_MODEL,
        "max_tokens": 600,
        "system": DIAGNOSIS_SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": user_msg}],
    }
    body = json.dumps(payload).encode("utf-8")

    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=body,
        headers={
            "Content-Type": "application/json",
            "x-api-key": ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        # Anthropic response shape: {"content": [{"type":"text","text":"..."}]}
        blocks = data.get("content", [])
        text_parts = [b.get("text", "") for b in blocks if b.get("type") == "text"]
        return "\n".join(text_parts).strip() or None
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")[:400]
        log.error("auto_diagnose: Anthropic API %s: %s", e.code, detail)
        return None
    except Exception:
        log.exception("auto_diagnose: Anthropic API call failed")
        return None


# ──────────────────────────────────────────────────────────────────
# Discord webhook post
# ──────────────────────────────────────────────────────────────────
def _post_to_discord(text: str) -> bool:
    """
    Post a message to the VEXONHQ Ops Discord channel.

    P1.4 v2 (Session 29): if the Discord Bot is configured
    (DISCORD_BOT_TOKEN + DISCORD_OPS_CHANNEL_ID), post via the Bot API
    so the message can carry an inline Restart button. Falls back to
    the P1.4 MVP plain channel webhook (DISCORD_OPS_WEBHOOK_URL) when
    the Bot isn't configured — same behaviour as before, no regression.

    Returns True on success, False otherwise. Never raises — this is
    the final step of a background task; we just log and move on.
    """
    # P1.4 v2 — preferred: Bot API + inline Restart button
    if _di is not None and _di.is_bot_configured():
        result = _di.send_message_with_restart_button(text)
        if result is not None:
            return True
        log.warning(
            "auto_diagnose: bot send failed — falling back to webhook"
        )

    # P1.4 MVP fallback — plain channel webhook (no button)
    if not DISCORD_OPS_WEBHOOK_URL:
        log.warning(
            "auto_diagnose: DISCORD_OPS_WEBHOOK_URL not set — skipping post"
        )
        return False

    # Discord allows up to 2000 chars per message; truncate to be safe.
    payload = {"content": text[:1900]}
    body = json.dumps(payload).encode("utf-8")

    req = urllib.request.Request(
        DISCORD_OPS_WEBHOOK_URL,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            # Discord returns 204 No Content on success
            if 200 <= resp.status < 300:
                return True
            log.warning("auto_diagnose: Discord returned %s", resp.status)
            return False
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")[:300]
        log.error("auto_diagnose: Discord webhook %s: %s", e.code, detail)
        return False
    except Exception:
        log.exception("auto_diagnose: Discord post failed")
        return False


# ──────────────────────────────────────────────────────────────────
# Public entrypoint — called as FastAPI BackgroundTask
# ──────────────────────────────────────────────────────────────────
def try_diagnose(error_type: str, check_results: dict[str, Any]) -> None:
    """
    Fire-and-forget diagnosis for an unhealthy /health/deep result.

    Designed to run as a FastAPI BackgroundTask so it does not delay
    the 503 response that Uptime Robot is waiting on.

    Never raises — any error is logged and swallowed, because failing
    the diagnosis must not cascade into a real production problem.
    """
    try:
        if not should_diagnose(error_type):
            return

        diagnosis = _call_claude(check_results)
        if not diagnosis:
            return  # API call already logged its failure

        # Tag the message so operator can distinguish AI diagnosis from
        # Uptime Robot's own "Monitor is DOWN" alert
        header = f"🤖 **AI Diagnosis** — `{error_type}`\n\n"
        ok = _post_to_discord(header + diagnosis)
        if ok:
            log.info("auto_diagnose: posted diagnosis for %s", error_type)
    except Exception:
        # Defensive — should not be reachable given inner try/excepts,
        # but BackgroundTask exceptions become silent server errors.
        log.exception("auto_diagnose: unexpected error in try_diagnose")
