"""
VEXONHQ Alerts Webhook (Session 19)
====================================
Receive webhooks from Uptime Robot (free plan) and forward them to Telegram.

Uptime Robot's free plan doesn't include Telegram integration, but it DOES
include generic Webhook integration. This module receives those webhook POSTs
and re-posts them as Telegram messages using the bot token + chat_id you
already configured for VEXONHQ Alerts.

Setup steps (one-time):
  1. Add the env vars in Coolify → vexonhq-ocr-api → Environment Variables:
       TELEGRAM_BOT_TOKEN     = <from BotFather>
       TELEGRAM_CHAT_ID       = 6437798782
       ALERTS_WEBHOOK_SECRET  = <pick any random string, e.g. 32-char hex>
  2. Add to main.py imports near the top:
       from alerts_webhook_routes import router as alerts_router
  3. Add to main.py near the other include_router() calls:
       app.include_router(alerts_router)
  4. Add the public path so it bypasses JWT auth — in main.py find
     `PUBLIC_PATHS = {...}` and add "/alerts/uptime-webhook" + "/alerts/test-telegram"
  5. Push + Coolify auto-deploys
  6. Hit /alerts/test-telegram?secret=<your-secret> in browser — Telegram should
     receive a test message confirming setup works
  7. In Uptime Robot dashboard → Integrations → Webhooks → Add:
       URL: https://b4zhad8qkoxjushdq8465056.../alerts/uptime-webhook?secret=<your-secret>
       Method: POST
       Content-Type: application/x-www-form-urlencoded  (Uptime Robot default)
  8. Attach this integration to your /health monitor

Endpoints exposed:
  POST /alerts/uptime-webhook  — receives Uptime Robot alerts → forwards to Telegram
  GET  /alerts/test-telegram   — manual test trigger

Security:
  Both endpoints require ?secret=<ALERTS_WEBHOOK_SECRET> query param.
  Without it: HTTP 401 Unauthorized.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request

from fastapi import APIRouter, HTTPException, Query, Request

log = logging.getLogger("alerts_webhook")

router = APIRouter(prefix="/alerts", tags=["alerts"])

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
ALERTS_WEBHOOK_SECRET = os.environ.get("ALERTS_WEBHOOK_SECRET", "")


# ─────────────────────────────────────────────────────────
# Telegram helper
# ─────────────────────────────────────────────────────────

def _send_telegram(text: str) -> dict:
    """Send a Markdown message to Telegram. Raises RuntimeError on config issue."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID env vars must be set in Coolify"
        )

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True,
    }
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")[:300]
        raise RuntimeError(f"Telegram API {e.code}: {detail}") from e


# ─────────────────────────────────────────────────────────
# Body parser — Uptime Robot can send JSON or form-urlencoded
# ─────────────────────────────────────────────────────────

async def _parse_webhook_body(request: Request) -> dict:
    """Accept either form-urlencoded or JSON body. Returns flat dict."""
    ct = request.headers.get("content-type", "").lower()
    try:
        if "application/json" in ct:
            return await request.json()
        # default: form-urlencoded (Uptime Robot's default format)
        form = await request.form()
        return {k: v for k, v in form.items()}
    except Exception as e:
        log.warning("alerts_webhook: failed to parse body (%s) — returning empty dict", e)
        return {}


# ─────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────

@router.post("/uptime-webhook")
async def uptime_webhook(request: Request, secret: str = Query("")):
    """
    Receive Uptime Robot alert and forward to Telegram.

    Uptime Robot sends these params (free plan, form-urlencoded by default):
        monitorID, monitorURL, monitorFriendlyName,
        alertType ("1"=Down, "2"=Up, "3"=SSL),
        alertTypeFriendlyName, alertDetails, alertDuration (seconds)
    """
    if not ALERTS_WEBHOOK_SECRET:
        raise HTTPException(500, "ALERTS_WEBHOOK_SECRET env var not configured on backend")
    if secret != ALERTS_WEBHOOK_SECRET:
        raise HTTPException(401, "Invalid secret query param")

    body = await _parse_webhook_body(request)

    monitor_name = (
        body.get("monitorFriendlyName")
        or body.get("monitor_friendly_name")
        or "Unknown"
    )
    monitor_url = body.get("monitorURL") or body.get("monitor_url") or ""
    alert_type = str(body.get("alertType") or body.get("alert_type") or "")
    alert_friendly = (
        body.get("alertTypeFriendlyName")
        or body.get("alert_type_friendly_name")
        or "Status change"
    )
    alert_details = body.get("alertDetails") or body.get("alert_details") or ""
    alert_duration = body.get("alertDuration") or body.get("alert_duration") or ""

    # Pick emoji per alert type
    icon = {"1": "🚨", "2": "✅", "3": "⚠️"}.get(alert_type, "ℹ️")

    lines = [
        f"{icon} *VEXONHQ Alert: {alert_friendly}*",
        "",
        f"*Monitor:* {monitor_name}",
    ]
    if monitor_url:
        # Escape underscores for Markdown (sslip.io URLs have many)
        safe_url = monitor_url.replace("_", "\\_")
        lines.append(f"*URL:* `{safe_url}`")
    if alert_details:
        lines.append(f"*Reason:* {alert_details}")
    if alert_duration:
        try:
            secs = int(float(alert_duration))
            mins, s = divmod(secs, 60)
            lines.append(f"*Downtime:* {mins}m {s}s" if mins else f"*Downtime:* {s}s")
        except (ValueError, TypeError):
            lines.append(f"*Duration:* {alert_duration}")

    text = "\n".join(lines)

    try:
        result = _send_telegram(text)
        log.info("alerts_webhook: forwarded %s alert to Telegram for monitor %s",
                 alert_friendly, monitor_name)
        return {"ok": True, "monitor": monitor_name, "alert": alert_friendly}
    except Exception as e:
        log.error("alerts_webhook: Telegram send failed: %s", e)
        raise HTTPException(500, f"Telegram send failed: {e}")


@router.get("/test-telegram")
def test_telegram(secret: str = Query("")):
    """
    Manual test trigger — sends a one-off test message to Telegram.
    Hit this once after env vars are set to verify the proxy works.
    """
    if not ALERTS_WEBHOOK_SECRET:
        raise HTTPException(500, "ALERTS_WEBHOOK_SECRET not configured")
    if secret != ALERTS_WEBHOOK_SECRET:
        raise HTTPException(401, "Invalid secret")

    try:
        result = _send_telegram(
            "🟢 *VEXONHQ Alert Test*\n\n"
            "ถ้าคุณเห็นข้อความนี้ — webhook proxy ทำงานปกติ\n"
            "Uptime Robot alerts จะถูกส่งมาที่นี่ตั้งแต่ตอนนี้"
        )
        return {"ok": True, "message_sent": True}
    except Exception as e:
        raise HTTPException(500, f"Telegram send failed: {e}")
