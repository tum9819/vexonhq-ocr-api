"""
Discord Interactions + Coolify API helpers (P1.4 v2, Session 29).

Pure helpers — no FastAPI imports, so the signature-verify + Coolify
restart logic can be unit-tested in isolation. The HTTP layer uses
urllib.request to match auto_diagnose.py style (no new transport lib).

The only external dependency added by this module is PyNaCl, for
Ed25519 signature verification of Discord interaction payloads.

What this module provides:
  - verify_signature()  Ed25519 verify of Discord interaction headers
  - is_bot_configured() True if DISCORD_BOT_TOKEN + channel id are set
  - send_message_with_restart_button()  POST via Bot API with components
  - edit_message_via_token()  PATCH the original interaction message
  - coolify_restart()  POST to Coolify v4 /applications/{uuid}/restart

Env vars (all loaded at module level, gracefully no-op if missing):
  DISCORD_BOT_TOKEN          Bot tab → Reset Token
  DISCORD_APP_PUBLIC_KEY     General Information → Public Key (hex)
  DISCORD_APP_ID             General Information → Application ID
  DISCORD_OPS_CHANNEL_ID     channel right-click → Copy Channel ID
  COOLIFY_API_TOKEN          Coolify → Keys & Tokens → API tokens
  COOLIFY_BACKEND_APP_UUID   Coolify → vexonhq-ocr-api → URL UUID
  COOLIFY_API_BASE_URL       optional override, default http://178.128.31.76:8000
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from typing import Any, Optional

log = logging.getLogger("discord_interactions")

DISCORD_BOT_TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "")
DISCORD_APP_PUBLIC_KEY = os.environ.get("DISCORD_APP_PUBLIC_KEY", "")
DISCORD_APP_ID = os.environ.get("DISCORD_APP_ID", "")
DISCORD_OPS_CHANNEL_ID = os.environ.get("DISCORD_OPS_CHANNEL_ID", "")

COOLIFY_API_TOKEN = os.environ.get("COOLIFY_API_TOKEN", "")
COOLIFY_BACKEND_APP_UUID = os.environ.get("COOLIFY_BACKEND_APP_UUID", "")
COOLIFY_API_BASE_URL = os.environ.get(
    "COOLIFY_API_BASE_URL", "http://178.128.31.76:8000"
).rstrip("/")

DISCORD_API_BASE = "https://discord.com/api/v10"

# custom_id values used in component buttons. Keep in sync with handlers
# in discord_routes.py.
CUSTOM_ID_RESTART_SERVICE = "restart_service"


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
# Discord Bot — send message with Restart button
# ──────────────────────────────────────────────────────────
def _restart_button_components() -> list[dict[str, Any]]:
    """The single-action row Discord expects in `components`."""
    return [
        {
            "type": 1,  # Action Row
            "components": [
                {
                    "type": 2,  # Button
                    "style": 1,  # Primary (blurple)
                    "label": "🔁 Restart service",
                    "custom_id": CUSTOM_ID_RESTART_SERVICE,
                }
            ],
        }
    ]


def send_message_with_restart_button(text: str) -> Optional[dict[str, Any]]:
    """
    POST a message to the ops channel via the Bot API with a Restart button.

    Returns the JSON response from Discord (dict with message id etc.)
    on success, or None on any failure / missing config. Never raises.
    """
    if not is_bot_configured():
        log.warning(
            "send_message_with_restart_button: bot not configured — skipping"
        )
        return None

    url = f"{DISCORD_API_BASE}/channels/{DISCORD_OPS_CHANNEL_ID}/messages"
    payload = {
        "content": text[:1900],
        "components": _restart_button_components(),
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
            "send_message_with_restart_button: Discord %s: %s",
            e.code,
            detail,
        )
        return None
    except Exception:
        log.exception(
            "send_message_with_restart_button: Discord POST failed"
        )
        return None


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
# Coolify v4 API — restart application
# ──────────────────────────────────────────────────────────
class CoolifyRestartError(RuntimeError):
    """Raised when the Coolify restart API call fails."""


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
