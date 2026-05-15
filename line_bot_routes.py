"""
line_bot_routes.py — Phase 7: LINE Bot daily digest
=====================================================
Endpoints:
  GET  /line/test              — send a test ping to TUM's LINE
  POST /line/digest/today      — build + send today's financial digest
  POST /line/digest/{date}     — build + send digest for a specific date (YYYY-MM-DD)

Built-in scheduler:
  Runs daily at 06:00 Bangkok time (Asia/Bangkok) — sends yesterday's digest automatically.

Required env vars (set in Coolify):
  LINE_CHANNEL_TOKEN  — long-lived channel access token from LINE Developers Console
  LINE_USER_ID        — TUM's personal LINE user ID (starts with U...)
"""

import json
import logging
import os
import urllib.request
import urllib.error
from datetime import date, datetime, timedelta
from typing import Optional

import psycopg2
from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import APIRouter, HTTPException

log = logging.getLogger("vexonhq-line")
router = APIRouter(prefix="/line", tags=["line"])

LINE_PUSH_URL = "https://api.line.me/v2/bot/message/push"


def _get_config() -> tuple[str, str]:
    token = os.environ.get("LINE_CHANNEL_TOKEN", "")
    user_id = os.environ.get("LINE_USER_ID", "")
    if not token or not user_id:
        raise HTTPException(500, "LINE_CHANNEL_TOKEN or LINE_USER_ID not configured in env")
    return token, user_id


def _get_db_conn():
    return psycopg2.connect(os.environ["DATABASE_URL"])


# ─────────────────────────────────────────────
# LINE Push helper
# ─────────────────────────────────────────────

def _push_text(text: str) -> dict:
    """Push a single text message to TUM's LINE."""
    token, user_id = _get_config()

    payload = json.dumps({
        "to": user_id,
        "messages": [{"type": "text", "text": text}],
    }).encode("utf-8")

    req = urllib.request.Request(
        LINE_PUSH_URL,
        data=payload,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        log.error("LINE API %s: %s", e.code, body)
        raise HTTPException(502, f"LINE API error {e.code}: {body}")
    except Exception as e:
        log.exception("LINE push failed")
        raise HTTPException(502, f"LINE push failed: {e}")


# ─────────────────────────────────────────────
# Digest builder
# ─────────────────────────────────────────────

SOURCE_LABELS = {
    "pos_sale":             "🏪 POS ขาย",
    "rider_income_grab":    "🟢 Grab รายได้",
    "rider_income_lineman": "🟡 Lineman รายได้",
    "ar_payment":           "💰 รับชำระหนี้",
    "vendor_bill":          "🧾 ใบแจ้งหนี้",
    "ap_payment":           "💸 จ่ายชำระหนี้",
    "manual":               "✏️ บันทึกมือ",
    "pos_cashflow":         "💵 เงินสด (ถาด)",
    "pos_cashflow_refund":  "↩️ คืนเงิน",
    "rider_gp_grab":        "📱 GP Grab",
    "rider_gp_lineman":     "📱 GP Lineman",
}


def _build_digest(target_date: date) -> str:
    conn = _get_db_conn()
    try:
        cur = conn.cursor()

        # ── 1. v_daybook summary for the day ──
        cur.execute("""
            SELECT direction, source, COALESCE(SUM(amount), 0) AS total
            FROM public.v_daybook
            WHERE entry_date = %s
            GROUP BY direction, source
            ORDER BY direction, source
        """, (target_date,))
        rows = cur.fetchall()

        income_total = 0.0
        expense_total = 0.0
        income_lines: list[str] = []
        expense_lines: list[str] = []

        for direction, source, total in rows:
            label = SOURCE_LABELS.get(source, source)
            amt = float(total)
            if direction == "income":
                income_total += amt
                income_lines.append(f"  {label}: ฿{amt:,.0f}")
            else:
                expense_total += amt
                expense_lines.append(f"  {label}: ฿{amt:,.0f}")

        # ── 2. Pending vendor bills ──
        cur.execute(
            "SELECT COUNT(*) FROM public.vendor_bills WHERE review_status = 'pending'"
        )
        pending_bills = int(cur.fetchone()[0])

        # ── 3. Open anomalies ──
        open_anomalies = 0
        try:
            cur.execute(
                "SELECT COUNT(*) FROM public.bill_anomalies WHERE user_action IS NULL"
            )
            open_anomalies = int(cur.fetchone()[0])
        except Exception:
            pass

        net = income_total - expense_total
        margin = (net / income_total * 100) if income_total > 0 else 0.0
        date_str = target_date.strftime("%d/%m/%Y")
        sep = "─" * 26

        # ── 4. Assemble message ──
        if not income_lines and not expense_lines:
            lines = [
                "📊 สรุปการเงิน MARA STATION",
                f"📅 {date_str}",
                sep,
                "ไม่มีข้อมูลวันนี้ครับ",
            ]
        else:
            lines = [
                "📊 สรุปการเงิน MARA STATION",
                f"📅 {date_str}",
                sep,
            ]
            if income_lines:
                lines.append("💚 รายรับ")
                lines.extend(income_lines)
                lines.append(f"  รวม: ฿{income_total:,.0f}")
            else:
                lines.append("💚 รายรับ: ฿0")

            lines.append("")

            if expense_lines:
                lines.append("🔴 รายจ่าย")
                lines.extend(expense_lines)
                lines.append(f"  รวม: ฿{expense_total:,.0f}")
            else:
                lines.append("🔴 รายจ่าย: ฿0")

            lines.append(sep)
            net_icon = "✅" if net >= 0 else "⚠️"
            lines.append(f"{net_icon} กำไรสุทธิ: ฿{net:,.0f} ({margin:.1f}%)")

        # ── 5. Alerts ──
        if pending_bills > 0:
            lines.append(f"\n⏳ รอ review: {pending_bills} ใบ")
        if open_anomalies > 0:
            lines.append(f"🚨 Anomaly: {open_anomalies} รายการ")

        return "\n".join(lines)

    finally:
        conn.close()


# ─────────────────────────────────────────────
# Scheduled job — runs daily at 06:00 Bangkok
# ─────────────────────────────────────────────

def _scheduled_daily_digest():
    """APScheduler job: send yesterday's digest to LINE at 06:00 Bangkok time."""
    yesterday = date.today() - timedelta(days=1)
    log.info("Scheduled digest — sending for %s", yesterday)
    try:
        text = _build_digest(yesterday)
        _push_text(text)
        log.info("Scheduled digest sent OK for %s", yesterday)
    except Exception as e:
        log.error("Scheduled digest FAILED for %s: %s", yesterday, e)


# Start scheduler when module loads (FastAPI startup)
_scheduler = BackgroundScheduler(timezone="Asia/Bangkok")
_scheduler.add_job(
    _scheduled_daily_digest,
    trigger="cron",
    hour=6,
    minute=0,
    id="daily_line_digest",
    replace_existing=True,
)
_scheduler.start()
log.info("LINE digest scheduler started — fires daily at 06:00 Asia/Bangkok")


# ─────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────

@router.get("/test")
def line_test():
    """Send a test ping to TUM's LINE to confirm the bot is working."""
    now = datetime.now().strftime("%d/%m/%Y %H:%M")
    text = f"✅ VEXONHQ LINE Bot พร้อมใช้งานแล้วครับ!\n📅 {now}"
    result = _push_text(text)
    return {"success": True, "message_sent": text, "line_response": result}


@router.post("/digest/today")
def digest_today():
    """Build and send today's financial digest to LINE."""
    today = date.today()
    text = _build_digest(today)
    result = _push_text(text)
    return {"success": True, "date": str(today), "message_sent": text, "line_response": result}


@router.post("/digest/{target_date}")
def digest_by_date(target_date: str):
    """Build and send digest for a specific date (YYYY-MM-DD)."""
    try:
        d = date.fromisoformat(target_date)
    except ValueError:
        raise HTTPException(400, "Invalid date — use YYYY-MM-DD (e.g. 2026-05-15)")
    text = _build_digest(d)
    result = _push_text(text)
    return {"success": True, "date": str(d), "message_sent": text, "line_response": result}


@router.get("/scheduler/status")
def scheduler_status():
    """Check if the daily digest scheduler is running."""
    jobs = [
        {
            "id": j.id,
            "next_run": str(j.next_run_time),
        }
        for j in _scheduler.get_jobs()
    ]
    return {
        "running": _scheduler.running,
        "timezone": "Asia/Bangkok",
        "schedule": "daily at 06:00",
        "jobs": jobs,
    }
