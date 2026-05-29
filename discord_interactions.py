"""
Discord Interactions + Coolify API helpers (P1.4 v2 + v3, Sessions 29 + 31).

Pure helpers — no FastAPI imports, so the signature-verify + Coolify
restart/logs logic can be unit-tested in isolation. The HTTP layer
uses urllib.request to match auto_diagnose.py style (no new transport
lib).

External dependencies added by this module: PyNaCl (Ed25519 signature
verification of Discord interaction payloads) and psutil (VPS resource
probing for the /resources slash command).

What this module provides:
  - verify_signature()                    Ed25519 verify of Discord headers
  - is_bot_configured()                   True if BOT_TOKEN + channel set
  - is_coolify_configured()               True if Coolify API token + UUID set
  - send_message_with_diagnosis_buttons() Bot API POST with [Restart + Show patch]
  - send_message_with_restart_button()    backward-compat alias for above
  - edit_message_via_token()              PATCH the original message
  - send_followup_message()               POST follow-up (used by Show patch)
  - coolify_restart()                     POST .../applications/{uuid}/restart
  - coolify_fetch_logs()                  GET .../applications/{uuid}/logs (v3)
  - build_resources_snapshot()            Read-only VPS snapshot for /resources
  - _get_scheduler()                      Lazy accessor for line_bot APScheduler

Env vars (all loaded at module level, gracefully no-op if missing):
  DISCORD_BOT_TOKEN          Bot tab → Reset Token
  DISCORD_APP_PUBLIC_KEY     General Information → Public Key (hex)
  DISCORD_APP_ID             General Information → Application ID
  DISCORD_OPS_CHANNEL_ID     channel right-click → Copy Channel ID
  COOLIFY_API_TOKEN          Coolify → Keys & Tokens → API tokens
  COOLIFY_BACKEND_APP_UUID   Coolify → vexonhq-ocr-api → URL UUID
  COOLIFY_API_BASE_URL       optional override, default http://178.128.31.76:8000
  COOLIFY_LOG_TAIL_LINES     optional (v3), default 200 — how many stdout
                              lines to fetch for Show patch suggestion
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import urllib.error
import urllib.request
from typing import Any, Optional

import psutil

log = logging.getLogger("discord_interactions")

# ──────────────────────────────────────────────────────────
# Scheduler accessor — wrapped so tests can monkeypatch without
# triggering line_bot_routes module init (DB, APScheduler.start).
# ──────────────────────────────────────────────────────────
def _get_scheduler():
    """Return the line_bot APScheduler instance, or None if unavailable."""
    try:
        import line_bot_routes  # noqa: PLC0415 — lazy to avoid import-time cost
        return getattr(line_bot_routes, "_scheduler", None)
    except Exception:
        return None


# ──────────────────────────────────────────────────────────
# /resources snapshot — read-only VPS health collection.
# Used by the Discord slash command added Session 45.
# ──────────────────────────────────────────────────────────
def build_resources_snapshot() -> dict[str, Any]:
    """
    Collect a one-shot VPS resource snapshot for the /resources slash
    command. Never raises — every metric is independently try/excepted
    so a single failed probe degrades to None instead of crashing the
    whole response.

    Threshold warnings (appended to snap["warnings"] as strings):
      cpu_pct  > 80   -> "CPU high"
      ram_pct  > 80   -> "RAM high"
      disk_pct > 80   -> "Disk filling"
      swap_pct > 50   -> "Swap heavy use"
      scheduler not running -> "APScheduler not running"
    """
    GB = 1024 ** 3
    MB = 1024 ** 2

    snap: dict[str, Any] = {
        "cpu_pct": None,
        "ram_pct": None,
        "ram_used_gb": None,
        "ram_total_gb": None,
        "disk_pct": None,
        "disk_used_gb": None,
        "disk_total_gb": None,
        "swap_pct": None,
        "swap_used_mb": None,
        "swap_total_gb": None,
        "scheduler_running": False,
        "scheduler_jobs": 0,
        "git_sha": "unknown",
        "warnings": [],
    }

    try:
        snap["cpu_pct"] = round(psutil.cpu_percent(interval=0.1), 1)
    except Exception:
        log.warning("snapshot: cpu_percent failed", exc_info=True)

    try:
        m = psutil.virtual_memory()
        snap["ram_pct"] = round(m.percent, 1)
        snap["ram_used_gb"] = round(m.used / GB, 2)
        snap["ram_total_gb"] = round(m.total / GB, 2)
    except Exception:
        log.warning("snapshot: virtual_memory failed", exc_info=True)

    try:
        d = shutil.disk_usage("/")
        snap["disk_used_gb"] = round(d.used / GB, 1)
        snap["disk_total_gb"] = round(d.total / GB, 1)
        snap["disk_pct"] = round(d.used / d.total * 100, 1) if d.total else None
    except Exception:
        log.warning("snapshot: disk_usage failed", exc_info=True)

    try:
        s = psutil.swap_memory()
        snap["swap_pct"] = round(s.percent, 1)
        snap["swap_used_mb"] = round(s.used / MB, 0)
        snap["swap_total_gb"] = round(s.total / GB, 1)
    except Exception:
        log.warning("snapshot: swap_memory failed", exc_info=True)

    try:
        sched = _get_scheduler()
        if sched is not None:
            snap["scheduler_running"] = bool(getattr(sched, "running", False))
            snap["scheduler_jobs"] = len(sched.get_jobs())
    except Exception:
        log.warning("snapshot: scheduler probe failed", exc_info=True)

    for var in ("SOURCE_COMMIT", "COOLIFY_GIT_COMMIT_SHA", "GIT_SHA"):
        v = os.environ.get(var, "")
        if v:
            snap["git_sha"] = v[:7]
            break

    if snap["cpu_pct"] is not None and snap["cpu_pct"] > 80:
        snap["warnings"].append("⚠️ CPU high — wait before next deploy")
    if snap["ram_pct"] is not None and snap["ram_pct"] > 80:
        snap["warnings"].append("⚠️ RAM high — risk of OOM kill")
    if snap["disk_pct"] is not None and snap["disk_pct"] > 80:
        snap["warnings"].append("⚠️ Disk filling — run docker prune")
    if snap["swap_pct"] is not None and snap["swap_pct"] > 50:
        snap["warnings"].append("⚠️ Swap heavy use — investigate process")
    if not snap["scheduler_running"]:
        snap["warnings"].append("⚠️ APScheduler not running — digests will not fire")

    return snap


def format_resources_message(snap: dict[str, Any]) -> str:
    """Render a snapshot dict as a Discord-flavored markdown message.

    None values become em-dash. The 'Warnings' block lists each warning
    on its own line when non-empty, or shows 'Warnings: none' when empty.
    """
    def _pct(v):
        return f"{v:.1f}%" if v is not None else "—"

    def _gb(used, total):
        if used is None or total is None:
            return ""
        return f"({used:.2f} / {total:.2f} GB)"

    def _disk_gb(used, total):
        if used is None or total is None:
            return ""
        return f"({used:.1f} / {total:.1f} GB)"

    def _swap_label(used_mb, total_gb):
        if used_mb is None or total_gb is None:
            return ""
        return f"({used_mb:.0f} MB / {total_gb:.1f} GB)"

    if snap.get("scheduler_running"):
        sched_line = f"⏰ Scheduler      {snap.get('scheduler_jobs', 0)} jobs running"
    else:
        sched_line = "⏰ Scheduler      ⚠️ not running"

    lines = [
        "📊 **VPS Resources** — vexonhq-core",
        "─────────────────────────────────",
        f"🖥️  CPU            {_pct(snap.get('cpu_pct'))}",
        f"💾 RAM            {_pct(snap.get('ram_pct'))}   {_gb(snap.get('ram_used_gb'), snap.get('ram_total_gb'))}".rstrip(),
        f"💿 Disk           {_pct(snap.get('disk_pct'))}   {_disk_gb(snap.get('disk_used_gb'), snap.get('disk_total_gb'))}".rstrip(),
        f"📦 Swap           {_pct(snap.get('swap_pct'))}   {_swap_label(snap.get('swap_used_mb'), snap.get('swap_total_gb'))}".rstrip(),
        sched_line,
        f"🚀 Last deploy    {snap.get('git_sha', 'unknown')}",
        "─────────────────────────────────",
    ]
    warnings = snap.get("warnings") or []
    if warnings:
        lines.append("⚠️ Warnings:")
        for w in warnings:
            lines.append(f"  {w}")
    else:
        lines.append("⚠️ Warnings: none")

    return "\n".join(lines)


def format_help_message() -> str:
    """Render the /vex help subcommand response.

    Static text — lists every Bot capability so TUM can rediscover
    features without reading code. Update this when a new subcommand,
    button, or auto-message is added.
    """
    return "\n".join([
        "🤖 **VEXONHQ Ops Bot — Commands**",
        "─────────────────────────────────",
        "`/vex resources`    📊 VPS snapshot (CPU/RAM/disk/swap/scheduler/last deploy)",
        "`/vex help`         📖 Show this list",
        "",
        "🔘 **Buttons** (appear on diagnose posts when `/health/deep` returns 503):",
        "  🔄 Restart       — restart the backend container via Coolify API",
        "  🩹 Show patch    — Claude reads container logs + suggests a unified-diff fix",
        "",
        "📢 **Auto messages**:",
        "  🤖 AI Diagnosis   — Claude diagnoses the failure within ~5 s of an outage",
        "  🚨 Uptime alert   — Uptime Robot pings @everyone on DOWN / UP transitions",
    ])


DISCORD_BOT_TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "")
DISCORD_APP_PUBLIC_KEY = os.environ.get("DISCORD_APP_PUBLIC_KEY", "")
DISCORD_APP_ID = os.environ.get("DISCORD_APP_ID", "")
DISCORD_OPS_CHANNEL_ID = os.environ.get("DISCORD_OPS_CHANNEL_ID", "")

COOLIFY_API_TOKEN = os.environ.get("COOLIFY_API_TOKEN", "")
COOLIFY_BACKEND_APP_UUID = os.environ.get("COOLIFY_BACKEND_APP_UUID", "")
COOLIFY_API_BASE_URL = os.environ.get(
    "COOLIFY_API_BASE_URL", "http://178.128.31.76:8000"
).rstrip("/")

# v3 (Session 31): tail size for Show patch — how many stdout lines to
# send to Claude Haiku for diagnosis. 200 is a balance between context
# (enough to see error tracebacks) and token cost (Haiku 4.5 input is
# cheap but not free; 200 lines × ~80 chars ≈ 16 KB ≈ 4-5k tokens).
try:
    COOLIFY_LOG_TAIL_LINES = max(20, int(os.environ.get("COOLIFY_LOG_TAIL_LINES", "200")))
except ValueError:
    COOLIFY_LOG_TAIL_LINES = 200

DISCORD_API_BASE = "https://discord.com/api/v10"

# custom_id values used in component buttons. Keep in sync with handlers
# in discord_routes.py.
CUSTOM_ID_RESTART_SERVICE = "restart_service"
CUSTOM_ID_SHOW_PATCH = "show_patch"


# ──────────────────────────────────────────────────────────
# Ed25519 signature verification
# ──────────────────────────────────────────────────────────
def verify_signature(
    public_key_hex: str,
    signature_hex: str,
    timestamp: str,
    raw_body: bytes,
) -> bool:
    """
    Verify the Discord Ed25519 signature on an interaction request.

    Discord signs `timestamp + raw_body` with its application private
    key; we verify with the application Public Key (advertised in
    Developer Portal). Returns True iff the signature is valid.

    Never raises — any malformed input returns False so the caller can
    safely turn the result into HTTP 401.
    """
    try:
        from nacl.signing import VerifyKey  # type: ignore
        from nacl.exceptions import BadSignatureError  # type: ignore
    except Exception:
        log.exception(
            "verify_signature: PyNaCl not importable — install pynacl"
        )
        return False

    if not public_key_hex or not signature_hex or not timestamp:
        return False

    try:
        verify_key = VerifyKey(bytes.fromhex(public_key_hex))
        signature_bytes = bytes.fromhex(signature_hex)
        message = timestamp.encode("utf-8") + raw_body
    except (ValueError, TypeError):
        return False

    try:
        verify_key.verify(message, signature_bytes)
        return True
    except BadSignatureError:
        return False
    except Exception:
        log.exception("verify_signature: unexpected verify failure")
        return False


# ──────────────────────────────────────────────────────────
# Config inspection
# ──────────────────────────────────────────────────────────
def is_bot_configured() -> bool:
    """True iff we can post to Discord via the Bot API (with components)."""
    return bool(DISCORD_BOT_TOKEN and DISCORD_OPS_CHANNEL_ID)


def is_coolify_configured() -> bool:
    """True iff we can call the Coolify restart API."""
    return bool(COOLIFY_API_TOKEN and COOLIFY_BACKEND_APP_UUID)


# ──────────────────────────────────────────────────────────
# Discord Bot — send message with diagnosis buttons (Restart + Show patch)
# ──────────────────────────────────────────────────────────
def _diagnosis_buttons() -> list[dict[str, Any]]:
    """
    Action Row with the two diagnosis buttons (v3 — Session 31).

    Restart is Primary (blurple) because it's the action that resolves
    the most common outage class (stuck container). Show patch is
    Secondary (grey) because it's advisory — TUM still has to review +
    apply the suggested diff manually.

    Discord allows up to 5 buttons per action row; we use 2.
    """
    return [
        {
            "type": 1,  # Action Row
            "components": [
                {
                    "type": 2,  # Button
                    "style": 1,  # Primary (blurple)
                    "label": "🔁 Restart service",
                    "custom_id": CUSTOM_ID_RESTART_SERVICE,
                },
                {
                    "type": 2,  # Button
                    "style": 2,  # Secondary (grey)
                    "label": "🩹 Show patch",
                    "custom_id": CUSTOM_ID_SHOW_PATCH,
                },
            ],
        }
    ]


def send_message_with_diagnosis_buttons(text: str) -> Optional[dict[str, Any]]:
    """
    POST a message to the ops channel via the Bot API with diagnosis buttons.

    Returns the JSON response from Discord (dict with message id etc.)
    on success, or None on any failure / missing config. Never raises.
    """
    if not is_bot_configured():
        log.warning(
            "send_message_with_diagnosis_buttons: bot not configured — skipping"
        )
        return None

    url = f"{DISCORD_API_BASE}/channels/{DISCORD_OPS_CHANNEL_ID}/messages"
    payload = {
        "content": text[:1900],
        "components": _diagnosis_buttons(),
    }
    body = json.dumps(payload).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bot {DISCORD_BOT_TOKEN}",
            "User-Agent": "VEXONHQ-OpsBot (vexonhq.com, 1.0)",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")[:300]
        log.error(
            "send_message_with_diagnosis_buttons: Discord %s: %s",
            e.code,
            detail,
        )
        return None
    except Exception:
        log.exception(
            "send_message_with_diagnosis_buttons: Discord POST failed"
        )
        return None


# Backward-compatible alias — auto_diagnose.py + older tests call the
# Session 29 name. New callers should use the diagnosis_buttons name.
send_message_with_restart_button = send_message_with_diagnosis_buttons


def send_simple_message(text: str) -> Optional[dict[str, Any]]:
    """
    POST a plain text message to the ops channel — no buttons (Session 31).

    Used by non-interactive notifications like the weekly DO snapshot
    rotation report. Same auth/transport as send_message_with_diagnosis_buttons
    but `components` is omitted so the message is informational only.

    Returns Discord's JSON response on success, None on failure or
    missing config. Never raises.
    """
    if not is_bot_configured():
        log.warning(
            "send_simple_message: bot not configured — skipping"
        )
        return None

    url = f"{DISCORD_API_BASE}/channels/{DISCORD_OPS_CHANNEL_ID}/messages"
    payload = {"content": text[:1900]}
    body = json.dumps(payload).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bot {DISCORD_BOT_TOKEN}",
            "User-Agent": "VEXONHQ-OpsBot (vexonhq.com, 1.0)",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")[:300]
        log.error("send_simple_message: Discord %s: %s", e.code, detail)
        return None
    except Exception:
        log.exception("send_simple_message: Discord POST failed")
        return None


def send_followup_message(
    application_id: str,
    interaction_token: str,
    content: str,
) -> bool:
    """
    Send a follow-up message to a deferred interaction (v3 — Session 31).

    Used by the Show patch flow: we acknowledge the click with type=5
    (DEFERRED_CHANNEL_MESSAGE_WITH_SOURCE — shows "Bot is thinking...")
    and then call this helper to post the actual diff as a new visible
    message in the channel.

    Distinct from `edit_message_via_token`:
      - edit_message_via_token PATCHes the @original message (used by
        Restart to swap text + strip buttons)
      - send_followup_message POSTs a new message via the interaction
        webhook (used by Show patch — original message stays untouched
        so the Restart button is still clickable)

    Discord limits follow-up content to 2000 chars; we truncate to 1900
    to leave headroom for ANSI/code-block fences the caller may add.

    Returns True on success, False otherwise. Never raises.
    """
    if not application_id or not interaction_token:
        return False

    url = (
        f"{DISCORD_API_BASE}/webhooks/{application_id}/"
        f"{interaction_token}"
    )
    payload = {
        "content": content[:1900],
    }
    body = json.dumps(payload).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "VEXONHQ-OpsBot (vexonhq.com, 1.0)",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return 200 <= resp.status < 300
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")[:300]
        log.error("send_followup_message: Discord %s: %s", e.code, detail)
        return False
    except Exception:
        log.exception("send_followup_message: Discord POST failed")
        return False


def edit_message_via_token(
    application_id: str,
    interaction_token: str,
    content: str,
) -> bool:
    """
    Edit the original message that fired the interaction.

    Used after a Restart button is clicked: we acknowledge the click
    immediately (deferred response) and then asynchronously call this
    to swap "🔁 Restart service" → "✅ Restart queued at HH:MM" so the
    button can't be clicked twice.

    Returns True on success, False otherwise. Never raises.
    """
    if not application_id or not interaction_token:
        return False

    url = (
        f"{DISCORD_API_BASE}/webhooks/{application_id}/"
        f"{interaction_token}/messages/@original"
    )
    payload = {
        "content": content[:1900],
        "components": [],  # remove buttons (one-shot action)
    }
    body = json.dumps(payload).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "VEXONHQ-OpsBot (vexonhq.com, 1.0)",
        },
        method="PATCH",
    )

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return 200 <= resp.status < 300
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")[:300]
        log.error("edit_message_via_token: Discord %s: %s", e.code, detail)
        return False
    except Exception:
        log.exception("edit_message_via_token: Discord PATCH failed")
        return False


# ──────────────────────────────────────────────────────────
# Coolify v4 API — restart application / fetch logs
# ──────────────────────────────────────────────────────────
class CoolifyRestartError(RuntimeError):
    """Raised when the Coolify restart API call fails."""


class CoolifyLogFetchError(RuntimeError):
    """Raised when the Coolify logs API call fails (v3, Session 31)."""


def coolify_restart(uuid: str) -> dict[str, Any]:
    """
    POST /api/v1/applications/{uuid}/restart on the Coolify instance.

    Coolify returns 200 + `{"message": "...", "deployment_uuid": "..."}`
    immediately — restart is queued, not blocking. We raise
    CoolifyRestartError on any non-2xx so the caller can surface a
    readable failure in the Discord message.
    """
    if not COOLIFY_API_TOKEN:
        raise CoolifyRestartError("COOLIFY_API_TOKEN env var not set")
    if not uuid:
        raise CoolifyRestartError("application uuid required")

    url = f"{COOLIFY_API_BASE_URL}/api/v1/applications/{uuid}/restart"
    req = urllib.request.Request(
        url,
        data=b"",
        headers={
            "Authorization": f"Bearer {COOLIFY_API_TOKEN}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read().decode("utf-8")
            try:
                data = json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                data = {"raw": raw[:300]}
            return data
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")[:300]
        raise CoolifyRestartError(
            f"Coolify API {e.code}: {detail}"
        ) from e
    except urllib.error.URLError as e:
        raise CoolifyRestartError(f"Coolify API unreachable: {e}") from e


def coolify_fetch_logs(uuid: str, tail_lines: Optional[int] = None) -> str:
    """
    GET .../api/v1/applications/{uuid}/logs and return the tail-N lines.

    Defensive about response shape — Coolify v4 has returned both:
      - JSON: {"logs": "..."} or {"log": [...]} on some endpoints
      - Plain text: the raw stdout
    We try JSON first; if that fails, treat the body as plain text.
    Either way, we tail the last `tail_lines` lines client-side so the
    caller gets a bounded slice regardless of whether Coolify supports
    server-side filtering.

    Used by the Show patch flow (v3, Session 31): the result is fed to
    Claude Haiku as context for unified-diff suggestion.

    Raises CoolifyLogFetchError on HTTP error, unreachable host, or
    empty token. Returns a non-empty string on success (may be just
    whitespace if the container has been silent — caller decides what
    to do with that).
    """
    if not COOLIFY_API_TOKEN:
        raise CoolifyLogFetchError("COOLIFY_API_TOKEN env var not set")
    if not uuid:
        raise CoolifyLogFetchError("application uuid required")

    n = tail_lines if tail_lines is not None else COOLIFY_LOG_TAIL_LINES
    # Try the documented query param first — Coolify v4 accepts ?lines=N
    # on most endpoints; if it doesn't, we still tail client-side.
    url = f"{COOLIFY_API_BASE_URL}/api/v1/applications/{uuid}/logs?lines={n}"
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {COOLIFY_API_TOKEN}",
            "Accept": "application/json, text/plain;q=0.9, */*;q=0.5",
        },
        method="GET",
    )

    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")[:300]
        raise CoolifyLogFetchError(
            f"Coolify logs API {e.code}: {detail}"
        ) from e
    except urllib.error.URLError as e:
        raise CoolifyLogFetchError(
            f"Coolify logs API unreachable: {e}"
        ) from e

    # Try JSON first — common Coolify shapes
    log_text: str
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        # Not JSON — treat body as raw stdout
        log_text = raw
    else:
        if isinstance(data, dict):
            # Try common keys in order of likelihood
            for key in ("logs", "log", "stdout", "output", "content"):
                v = data.get(key)
                if isinstance(v, str) and v.strip():
                    log_text = v
                    break
                if isinstance(v, list):
                    log_text = "\n".join(str(item) for item in v)
                    break
            else:
                # JSON dict with no recognized key — stringify the whole thing
                log_text = json.dumps(data, ensure_ascii=False, indent=2)
        elif isinstance(data, list):
            log_text = "\n".join(str(item) for item in data)
        else:
            log_text = str(data)

    # Client-side tail — guarantees bounded output regardless of server
    lines = log_text.splitlines()
    if len(lines) > n:
        lines = lines[-n:]
    return "\n".join(lines)
