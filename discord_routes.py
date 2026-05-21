"""
Discord Interactions Endpoint (P1.4 v2 + v3, Sessions 29 + 31).

When the user clicks an inline button on a Discord message posted by
our Bot, Discord POSTs a signed JSON payload to the URL configured as
"Interactions Endpoint URL" in the Application's Developer Portal.
This module exposes that endpoint and routes the click to the right
handler.

Endpoints:
  POST /alerts/discord-interaction
        - public path (auth = Ed25519 signature on headers)
        - handles PING (type=1) verification + button clicks (type=3)
        - Two buttons supported (v3, Session 31):
          • custom_id="restart_service" → ack type=6, BackgroundTask
            calls Coolify restart, edits original message
          • custom_id="show_patch"      → ack type=5 ("Bot is thinking"),
            BackgroundTask fetches Coolify logs, asks Claude Haiku for
            unified-diff suggestion, posts follow-up message

  GET  /alerts/discord-restart-test?secret=<ALERTS_WEBHOOK_SECRET>
        - manual trigger: posts a test message with BOTH buttons so
          TUM can validate end-to-end without waiting for a real
          /health/deep failure.

Discord 3-second rule: the interaction response must hit Discord within
3 seconds of the click or Discord shows "This interaction failed". For
restart we send type=6 (DEFERRED_UPDATE_MESSAGE) immediately and finish
the actual Coolify call + message edit on a BackgroundTask. For Show
patch we send type=5 (DEFERRED_CHANNEL_MESSAGE_WITH_SOURCE) so TUM sees
"Bot is thinking..." while Claude generates the diff, then we post the
diff as a follow-up message in the same channel.
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request
from fastapi.responses import JSONResponse

import discord_interactions as di

log = logging.getLogger("discord_routes")

router = APIRouter(prefix="/alerts", tags=["alerts", "discord"])

ALERTS_WEBHOOK_SECRET = os.environ.get("ALERTS_WEBHOOK_SECRET", "")

# Discord interaction types (subset we care about)
INTERACTION_PING = 1
INTERACTION_MESSAGE_COMPONENT = 3

# Discord interaction response types
RESPONSE_PONG = 1
RESPONSE_CHANNEL_MESSAGE = 4
RESPONSE_DEFERRED_CHANNEL_MESSAGE = 5  # v3: shows "Bot is thinking..."
RESPONSE_DEFERRED_UPDATE_MESSAGE = 6   # v2: silent ack, edit original


# ──────────────────────────────────────────────────────────
# Background handler — runs after we ack the click
# ──────────────────────────────────────────────────────────
def _do_restart_and_update(
    application_id: str,
    interaction_token: str,
) -> None:
    """
    Call Coolify restart, then edit the original message to reflect outcome.

    Designed to never raise — failures are logged and surfaced back to
    the operator via the Discord message edit, not via HTTP error.
    """
    try:
        if not di.is_coolify_configured():
            di.edit_message_via_token(
                application_id,
                interaction_token,
                "❌ Restart skipped: COOLIFY_API_TOKEN / UUID not configured "
                "on backend.",
            )
            return

        try:
            result = di.coolify_restart(di.COOLIFY_BACKEND_APP_UUID)
        except di.CoolifyRestartError as e:
            log.error("restart: Coolify API failed: %s", e)
            di.edit_message_via_token(
                application_id,
                interaction_token,
                f"❌ Restart failed: {str(e)[:200]}",
            )
            return

        deployment_uuid = result.get("deployment_uuid") or "(unknown)"
        when = time.strftime("%H:%M:%S")
        di.edit_message_via_token(
            application_id,
            interaction_token,
            f"✅ Restart queued at {when} — deployment `{deployment_uuid}`. "
            f"Coolify is rolling the container; expect health to recover "
            f"in ~20-30s.",
        )
    except Exception:
        # Defensive — BackgroundTask exceptions are silent server errors
        log.exception(
            "_do_restart_and_update: unexpected failure (application_id=%s)",
            application_id,
        )


# ──────────────────────────────────────────────────────────
# Background handler — Show patch (v3, Session 31)
# ──────────────────────────────────────────────────────────
def _do_show_patch_and_followup(
    application_id: str,
    interaction_token: str,
) -> None:
    """
    Fetch Coolify stdout tail → call Claude Haiku → post follow-up.

    Triggered after a click on the 🩹 Show patch button. The interaction
    has already been acked with type=5 (so the user sees "Bot is
    thinking..."); this BackgroundTask does the heavy lifting and posts
    the diff (or environmental explanation) as a follow-up message.

    Designed to never raise — failures surface back to the operator as
    an error follow-up message, not as an HTTP error.
    """
    try:
        if not di.is_coolify_configured():
            di.send_followup_message(
                application_id,
                interaction_token,
                "❌ Show patch skipped: COOLIFY_API_TOKEN / UUID not "
                "configured on backend.",
            )
            return

        # 1) Fetch the tail of Coolify stdout
        try:
            logs = di.coolify_fetch_logs(di.COOLIFY_BACKEND_APP_UUID)
        except di.CoolifyLogFetchError as e:
            log.error("show_patch: Coolify logs fetch failed: %s", e)
            di.send_followup_message(
                application_id,
                interaction_token,
                f"❌ Couldn't fetch Coolify logs: {str(e)[:300]}",
            )
            return

        if not logs.strip():
            di.send_followup_message(
                application_id,
                interaction_token,
                "ℹ️ Coolify logs are empty — nothing to diagnose. "
                "Check Coolify dashboard 'Logs' tab manually.",
            )
            return

        # 2) Ask Claude Haiku for a patch suggestion. Import here to
        # keep auto_diagnose import lazy (avoids cycles if it ever
        # imports discord_interactions in the future).
        try:
            from auto_diagnose import suggest_patch_from_logs
        except Exception:
            log.exception("show_patch: failed to import suggest_patch_from_logs")
            di.send_followup_message(
                application_id,
                interaction_token,
                "❌ Patch suggestion unavailable: auto_diagnose module "
                "not importable.",
            )
            return

        diagnosis = suggest_patch_from_logs(logs)
        if not diagnosis:
            di.send_followup_message(
                application_id,
                interaction_token,
                "❌ Claude Haiku didn't return a diagnosis (API error or "
                "ANTHROPIC_API_KEY not set — see server logs).",
            )
            return

        # 3) Post the diff as a follow-up. Header tag mirrors the
        # auto_diagnose header so operator can scan the channel and
        # tell sources apart.
        header = "🩹 **Patch suggestion** (Claude Haiku)\n\n"
        di.send_followup_message(
            application_id,
            interaction_token,
            header + diagnosis,
        )
    except Exception:
        log.exception(
            "_do_show_patch_and_followup: unexpected failure "
            "(application_id=%s)",
            application_id,
        )
        # Best-effort error follow-up — if even this fails, the user
        # sees a stuck "Bot is thinking..." until Discord's 15-min TTL.
        try:
            di.send_followup_message(
                application_id,
                interaction_token,
                "❌ Show patch encountered an unexpected error — see "
                "server logs for the traceback.",
            )
        except Exception:
            pass


# ──────────────────────────────────────────────────────────
# POST /alerts/discord-interaction
# ──────────────────────────────────────────────────────────
@router.post("/discord-interaction")
async def discord_interaction(
    request: Request,
    background_tasks: BackgroundTasks,
):
    """
    Receive a Discord interaction (PING or button click).

    Auth: Ed25519 signature on headers X-Signature-Ed25519 and
    X-Signature-Timestamp, verified against DISCORD_APP_PUBLIC_KEY.
    Wrong signature -> 401. Missing PyNaCl or unset public key -> 401
    (treated identically to a bad sig — fail-closed).
    """
    raw_body = await request.body()
    signature_hex = request.headers.get("X-Signature-Ed25519", "")
    timestamp = request.headers.get("X-Signature-Timestamp", "")

    if not di.verify_signature(
        di.DISCORD_APP_PUBLIC_KEY,
        signature_hex,
        timestamp,
        raw_body,
    ):
        raise HTTPException(401, "Invalid request signature")

    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise HTTPException(400, "Malformed JSON body")

    itype = payload.get("type")
    if itype == INTERACTION_PING:
        # Developer Portal saves the endpoint only if we PONG correctly.
        return JSONResponse({"type": RESPONSE_PONG})

    if itype == INTERACTION_MESSAGE_COMPONENT:
        data = payload.get("data") or {}
        custom_id = data.get("custom_id") or ""
        application_id = payload.get("application_id") or di.DISCORD_APP_ID
        interaction_token = payload.get("token") or ""

        if custom_id == di.CUSTOM_ID_RESTART_SERVICE:
            # type=6: silent ack, BackgroundTask edits original message
            # to show "Restart queued at HH:MM" + strip buttons.
            background_tasks.add_task(
                _do_restart_and_update,
                application_id,
                interaction_token,
            )
            return JSONResponse(
                {"type": RESPONSE_DEFERRED_UPDATE_MESSAGE}
            )

        if custom_id == di.CUSTOM_ID_SHOW_PATCH:
            # type=5: shows "Bot is thinking..." while Claude Haiku
            # reads Coolify logs + drafts a patch. Original message
            # stays untouched so Restart button remains clickable.
            background_tasks.add_task(
                _do_show_patch_and_followup,
                application_id,
                interaction_token,
            )
            return JSONResponse(
                {"type": RESPONSE_DEFERRED_CHANNEL_MESSAGE}
            )

        # Unknown button — respond visibly so TUM can see the misconfig.
        return JSONResponse(
            {
                "type": RESPONSE_CHANNEL_MESSAGE,
                "data": {
                    "content": (
                        f"⚠️ Unsupported component custom_id: "
                        f"`{custom_id[:64]}`"
                    ),
                },
            }
        )

    # Any other interaction type (modal submit etc.) — acknowledge gracefully
    log.info("discord_interaction: unhandled interaction type=%s", itype)
    return JSONResponse(
        {
            "type": RESPONSE_CHANNEL_MESSAGE,
            "data": {"content": "Unsupported interaction type"},
        }
    )


# ──────────────────────────────────────────────────────────
# GET /alerts/discord-restart-test — manual end-to-end probe
# ──────────────────────────────────────────────────────────
@router.get("/discord-restart-test")
def discord_restart_test(secret: str = Query("")):
    """
    Post a one-off test message with a Restart button.

    Use this after Coolify env vars + Discord Application + Interactions
    Endpoint URL are all configured, to verify the end-to-end loop
    (Bot posts → click → backend acks → Coolify restart → message edits)
    without waiting for a real /health/deep outage.
    """
    if not ALERTS_WEBHOOK_SECRET:
        raise HTTPException(
            500, "ALERTS_WEBHOOK_SECRET env var not configured on backend"
        )
    if secret != ALERTS_WEBHOOK_SECRET:
        raise HTTPException(401, "Invalid secret query param")

    if not di.is_bot_configured():
        raise HTTPException(
            500,
            "Bot not configured — set DISCORD_BOT_TOKEN + "
            "DISCORD_OPS_CHANNEL_ID in Coolify",
        )

    result = di.send_message_with_diagnosis_buttons(
        "🧪 **Manual test** — Diagnosis buttons (P1.4 v3)\n\n"
        "Click **🔁 Restart service** to verify Coolify restart works "
        "end-to-end.\n"
        "Click **🩹 Show patch** to verify Coolify-logs fetch + "
        "Claude Haiku patch suggestion."
    )
    if result is None:
        raise HTTPException(502, "Discord Bot POST failed — see server logs")

    return {
        "ok": True,
        "discord_message_id": result.get("id"),
        "channel_id": result.get("channel_id"),
    }
