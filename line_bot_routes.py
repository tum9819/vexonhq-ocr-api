"""
line_bot_routes.py — Phase 7 + Phase 13: LINE Bot daily digest + OCR Bot
=========================================================================
Endpoints:
  GET  /line/test              — send a test ping to TUM's LINE
  POST /line/digest/today      — build + send today's financial digest
  POST /line/digest/{date}     — build + send digest for a specific date (YYYY-MM-DD)
  POST /line/webhook           — LINE Messaging API webhook

Webhook handles:
  - text message → AI Search (Phase 11), or quick expense entry (e.g. "ค่าน้ำมัน 450")
  - image message → GPT Vision OCR → save to vendor_bills (Phase 13 LINE OCR Bot)

Built-in scheduler:
  Runs daily at 06:00 Bangkok time (Asia/Bangkok) — sends yesterday's digest automatically.

Required env vars (set in Coolify):
  LINE_CHANNEL_TOKEN   — long-lived channel access token from LINE Developers Console
  LINE_CHANNEL_SECRET  — channel secret (for webhook signature verification)
  LINE_USER_ID         — TUM's personal LINE user ID (starts with U...)
"""

import base64
import hashlib
import hmac
import json
import logging
import os
import urllib.request
import urllib.error
import uuid
from datetime import date, datetime, timedelta
from typing import Optional

import psycopg2
from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import APIRouter, Header, HTTPException, Request
from budget_routes import run_budget_alert_check as _budget_alert_check

log = logging.getLogger("vexonhq-line")
router = APIRouter(prefix="/line", tags=["line"])

LINE_PUSH_URL    = "https://api.line.me/v2/bot/message/push"
LINE_REPLY_URL   = "https://api.line.me/v2/bot/message/reply"
LINE_CONTENT_URL = "https://api-data.line.me/v2/bot/message/{message_id}/content"


def _get_config() -> tuple[str, str]:
    token = os.environ.get("LINE_CHANNEL_TOKEN", "")
    user_id = os.environ.get("LINE_USER_ID", "")
    if not token or not user_id:
        raise HTTPException(500, "LINE_CHANNEL_TOKEN or LINE_USER_ID not configured in env")
    return token, user_id


def _get_db_conn():
    return psycopg2.connect(os.environ["DATABASE_URL"])


# ─────────────────────────────────────────────
# LINE Push / Reply helpers
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


def _reply_line(reply_token: str, text: str) -> None:
    token = os.environ.get("LINE_CHANNEL_TOKEN", "")
    payload = json.dumps({
        "replyToken": reply_token,
        "messages": [{"type": "text", "text": text}],
    }).encode("utf-8")
    req = urllib.request.Request(
        LINE_REPLY_URL,
        data=payload,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10):
            pass
    except Exception as e:
        log.error("LINE reply failed: %s", e)


# ─────────────────────────────────────────────
# Phase 13: Image download from LINE Content API
# ─────────────────────────────────────────────

def _download_line_image(message_id: str) -> bytes:
    """Download image bytes from LINE Content API."""
    token = os.environ.get("LINE_CHANNEL_TOKEN", "")
    url = LINE_CONTENT_URL.format(message_id=message_id)
    req = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {token}"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.read()
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        log.error("LINE Content API %s: %s", e.code, body)
        raise RuntimeError(f"LINE Content API error {e.code}: {body}")
    except Exception as e:
        log.error("LINE image download failed: %s", e)
        raise RuntimeError(f"image download failed: {e}")


def _ocr_invoice_image(image_bytes: bytes) -> dict:
    """
    Run GPT Vision OCR on image bytes.
    Reuses _run_gpt_vision from main.py.
    Returns parsed dict (vendor_name, invoice_no, amount, items, etc.)
    """
    try:
        from main import _run_gpt_vision
        return _run_gpt_vision(image_bytes, "image/jpeg", "")
    except Exception as e:
        log.error("GPT Vision OCR failed: %s", e)
        raise RuntimeError(f"OCR failed: {e}")


def _save_invoice_from_line(parsed: dict, image_bytes: bytes) -> str:
    """
    Save OCR result to vendor_bills + invoice_items.
    Upload image to Supabase Storage (best-effort).
    Returns invoice_id (UUID).
    """
    try:
        from main import _save_invoice, _upload_to_storage
    except ImportError as e:
        raise RuntimeError(f"Cannot import from main: {e}")

    file_name = f"line-ocr-{uuid.uuid4().hex[:8]}.jpg"
    file_url = None
    try:
        file_url, _ = _upload_to_storage(image_bytes, file_name, "image/jpeg")
    except Exception as e:
        log.warning("storage upload failed (continuing without file_url): %s", e)

    invoice_id, batch_id, page_no, merged = _save_invoice(
        parsed=parsed,
        ocr_text="",
        file_url=file_url,
        file_name=file_name,
        mime_type="image/jpeg",
    )
    return invoice_id


def _format_ocr_reply(parsed: dict, invoice_id: str) -> str:
    """Format a friendly LINE reply summarising the OCR result."""
    vendor = parsed.get("vendor_name") or "ไม่ทราบร้าน"
    amount = parsed.get("amount")
    inv_no = parsed.get("invoice_no") or "-"
    bill_date = parsed.get("bill_date") or "-"
    items = parsed.get("items") or []
    item_count = len(items)

    amt_str = f"฿{float(amount):,.2f}" if amount is not None else "ไม่ทราบ"

    lines = [
        "🧾 OCR สำเร็จ!",
        "─" * 24,
        f"🏪 ร้าน: {vendor}",
        f"📋 เลขที่บิล: {inv_no}",
        f"📅 วันที่: {bill_date}",
        f"💰 ยอดรวม: {amt_str}",
        f"📦 รายการ: {item_count} รายการ",
        "─" * 24,
        "✅ บันทึกแล้ว — รอ review ในระบบ",
        f"🔑 ID: {invoice_id[:8]}...",
    ]
    return "\n".join(lines)


# ─────────────────────────────────────────────
# Phase 13: Quick expense text entry
# e.g. "ค่าน้ำมัน 450" or "ค่าข้าว 120 บาท"
# ─────────────────────────────────────────────

def _parse_quick_expense(text: str) -> Optional[dict]:
    """
    Detect quick expense: first word starts with ค่า/จ่าย/ซื้อ
    and message contains a number.
    Examples: "ค่าน้ำมัน 450", "จ่ายค่าไฟ 1200 บาท", "ซื้อผัก 80"
    """
    text = text.strip()
    parts = text.split()
    if len(parts) < 2:
        return None

    first = parts[0]
    if not (first.startswith("ค่า") or
            first.startswith("จ่าย") or
            first.startswith("ซื้อ")):
        return None

    # Find a number anywhere in the message (last number wins)
    amount: Optional[float] = None
    for part in reversed(parts):
        cleaned = part.replace(",", "").replace("บาท", "").replace("฿", "").strip()
        try:
            val = float(cleaned)
            if val > 0:
                amount = val
                break
        except ValueError:
            continue

    if amount is None:
        return None

    return {"description": first, "amount": amount}


def _save_quick_expense(description: str, amount: float) -> str:
    """Save quick expense to manual_entries. Returns the new entry ID."""
    conn = _get_db_conn()
    try:
        cur = conn.cursor()
        entry_id = str(uuid.uuid4())
        cur.execute("""
            INSERT INTO public.manual_entries
                (entry_date, direction, amount, label,
                 payment_method, branch_code)
            VALUES (%s, 'expense', %s, %s, 'cash', 'thawi_watthana')
            RETURNING id
        """, (date.today(), amount, description))
        entry_id = str(cur.fetchone()[0])
        conn.commit()
        return entry_id
    finally:
        conn.close()


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
    "bank_statement":       "🏦 Bank Statement",
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

        # ── 4. Bank statement needs_review ──
        bank_needs_review = 0
        try:
            cur.execute(
                "SELECT COUNT(*) FROM public.bank_statement_entries WHERE match_status = 'needs_review'"
            )
            bank_needs_review = int(cur.fetchone()[0])
        except Exception:
            pass

        net = income_total - expense_total
        margin = (net / income_total * 100) if income_total > 0 else 0.0
        date_str = target_date.strftime("%d/%m/%Y")
        sep = "─" * 26

        # ── 5. Assemble message ──
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

        # ── 6. Alerts ──
        if pending_bills > 0:
            lines.append(f"\n⏳ รอ review: {pending_bills} ใบ")
        if open_anomalies > 0:
            lines.append(f"🚨 Anomaly: {open_anomalies} รายการ")
        if bank_needs_review > 0:
            lines.append(f"🏦 Statement รอจัด: {bank_needs_review} รายการ")

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


# ─────────────────────────────────────────────
# Phase 20: AP Due Date Reminder — 09:00 Bangkok
# ─────────────────────────────────────────────

def _scheduled_ap_due_reminder():
    """APScheduler job: send AP due reminder to LINE at 09:00 Bangkok time."""
    log.info("Scheduled AP due reminder — running")
    try:
        from phase3_arap_routes import _query_due_bills, _build_due_reminder_message  # noqa: PLC0415
        rows = _query_due_bills(days_ahead=3)
        if rows:
            text = _build_due_reminder_message(rows)
            _push_text(text)
            log.info("AP due reminder sent OK: %d bill(s)", len(rows))
        else:
            log.info("AP due reminder: ไม่มีบิลครบกำหนดใน 3 วันข้างหน้า")
    except Exception as e:
        log.error("AP due reminder FAILED: %s", e)


# ─────────────────────────────────────────────
# Phase 21: Weekly Summary — every Monday 08:00
# ─────────────────────────────────────────────

_THAI_MONTHS_W = ["","ม.ค.","ก.พ.","มี.ค.","เม.ย.","พ.ค.","มิ.ย.","ก.ค.","ส.ค.","ก.ย.","ต.ค.","พ.ย.","ธ.ค."]


def _thai_date_w(d: date) -> str:
    return f"{d.day} {_THAI_MONTHS_W[d.month]}"


def _build_weekly_summary() -> str:
    """Build weekly P&L summary for last Mon–Sun."""
    conn = _get_db_conn()
    try:
        today = date.today()
        # Last week Mon–Sun (if today is Mon, go back 7 days)
        days_since_mon = today.weekday()  # 0=Mon, 6=Sun
        week_end = today - timedelta(days=days_since_mon + 1)    # last Sunday
        week_start = week_end - timedelta(days=6)                 # last Monday

        cur = conn.cursor()

        # Income / expense totals
        cur.execute("""
            SELECT
                COALESCE(SUM(CASE WHEN direction='income'  THEN amount ELSE 0 END), 0) AS inc,
                COALESCE(SUM(CASE WHEN direction='expense' THEN amount ELSE 0 END), 0) AS exp
            FROM public.v_daybook
            WHERE entry_date BETWEEN %s AND %s
              AND source NOT IN ('owner_capital','owner_advance','transfer_error')
        """, (week_start.isoformat(), week_end.isoformat()))
        row = cur.fetchone()
        income  = float(row[0])
        expense = float(row[1])
        net     = income - expense
        margin  = (net / income * 100) if income > 0 else 0.0

        # Top 3 expense categories
        cur.execute("""
            SELECT COALESCE(cat.name, d.category_code, 'อื่นๆ') AS cat_name,
                   COALESCE(SUM(d.amount), 0) AS total
            FROM public.v_daybook d
            LEFT JOIN public.categories cat ON cat.code = d.category_code
            WHERE d.entry_date BETWEEN %s AND %s
              AND d.direction = 'expense'
              AND d.source NOT IN ('owner_capital','owner_advance','transfer_error')
            GROUP BY COALESCE(cat.name, d.category_code, 'อื่นๆ')
            ORDER BY total DESC
            LIMIT 3
        """, (week_start.isoformat(), week_end.isoformat()))
        top_cats = cur.fetchall()

        # Pending AP bills
        ap_count, ap_total = 0, 0.0
        try:
            cur.execute("""
                SELECT COUNT(*),
                       COALESCE(SUM(amount_total - amount_paid), 0)
                FROM public.ar_ap_entries
                WHERE direction='payable' AND status IN ('pending','partial')
            """)
            ap_row = cur.fetchone()
            ap_count = int(ap_row[0] or 0)
            ap_total = float(ap_row[1] or 0)
        except Exception:
            pass

        be_year = week_end.year + 543
        sep = "─" * 26
        net_icon = "✅" if net >= 0 else "⚠️"

        lines = [
            "📊 สรุปสัปดาห์ MARA STATION",
            f"📅 {_thai_date_w(week_start)} – {_thai_date_w(week_end)} {be_year}",
            sep,
            f"💚 รายรับ:   ฿{income:,.0f}",
            f"🔴 รายจ่าย:  ฿{expense:,.0f}",
            f"{net_icon} กำไร:      ฿{net:,.0f} ({margin:.1f}%)",
        ]
        if top_cats:
            lines.append(sep)
            lines.append("📋 รายจ่ายสูงสุด 3 อันดับ:")
            for cat_name, total in top_cats:
                lines.append(f"  • {cat_name}: ฿{float(total):,.0f}")
        if ap_count > 0:
            lines.append(sep)
            lines.append(f"⏳ AP ค้างจ่าย: {ap_count} บิล / ฿{ap_total:,.0f}")

        return "\n".join(lines)
    finally:
        conn.close()


def _scheduled_weekly_summary():
    """APScheduler job: send weekly summary to LINE every Monday 08:00 Bangkok."""
    log.info("Scheduled weekly summary — running")
    try:
        text = _build_weekly_summary()
        _push_text(text)
        log.info("Weekly summary sent OK")
    except Exception as e:
        log.error("Weekly summary FAILED: %s", e)


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
_scheduler.add_job(
    _scheduled_ap_due_reminder,
    trigger="cron",
    hour=9,
    minute=0,
    id="daily_ap_due_reminder",
    replace_existing=True,
)
_scheduler.add_job(
    _scheduled_weekly_summary,
    trigger="cron",
    day_of_week="mon",
    hour=8,
    minute=0,
    id="weekly_summary",
    replace_existing=True,
)
_scheduler.add_job(
    _budget_alert_check,
    trigger="cron",
    hour=20,
    minute=0,
    id="daily_budget_alert",
    replace_existing=True,
)
_scheduler.start()
log.info("LINE digest scheduler started — fires daily at 06:00 Asia/Bangkok")
log.info("AP due reminder scheduler started — fires daily at 09:00 Asia/Bangkok")
log.info("Weekly summary scheduler started — fires every Monday 08:00 Asia/Bangkok")
log.info("Budget alert scheduler started — fires daily at 20:00 Asia/Bangkok")


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


@router.post("/weekly-summary")
def line_weekly_summary():
    """Build and send last week's P&L summary to LINE (Phase 21)."""
    text = _build_weekly_summary()
    _push_text(text)
    return {"sent": True, "preview": text}


@router.get("/scheduler/status")
def scheduler_status():
    """Show APScheduler job list and next run times."""
    jobs = []
    for job in _scheduler.get_jobs():
        nxt = job.next_run_time
        jobs.append({
            "id": job.id,
            "next_run": str(nxt) if nxt else None,
        })
    return {
        "running": _scheduler.running,
        "timezone": "Asia/Bangkok",
        "schedules": {
            "daily_line_digest":    "06:00 — ส่ง daily digest",
            "daily_ap_due_reminder":"09:00 — AP due reminder (Phase 20)",
            "weekly_summary":       "จันทร์ 08:00 — Weekly P&L summary (Phase 21)",
        },
        "jobs": jobs,
    }


# ─────────────────────────────────────────────
# Webhook helpers
# ─────────────────────────────────────────────

def _verify_signature(body: bytes, signature: str) -> bool:
    secret = os.environ.get("LINE_CHANNEL_SECRET", "")
    if not secret:
        return True  # skip verification if secret not set
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).digest()
    return base64.b64encode(digest).decode() == signature


SOURCE_LABELS_SHORT = {
    "pos_sale":             "POS",
    "rider_income_grab":    "Grab",
    "rider_income_lineman": "Lineman",
    "ar_payment":           "รับชำระ",
    "ap_payment":           "จ่ายชำระ",
    "manual":               "บันทึกเอง",
    "bank_statement":       "Bank",
}


def _format_search_for_line(query: str, count: int, total_income: float,
                              total_expense: float, results: list) -> str:
    sep = "─" * 24
    if count == 0:
        return f'🔍 "{query}"\n{sep}\nไม่พบรายการที่ตรงกันครับ'

    lines = [f'🔍 "{query}"', sep, f"พบ {count} รายการ"]
    if total_income > 0:
        lines.append(f"💚 รายรับรวม: ฿{total_income:,.0f}")
    if total_expense > 0:
        lines.append(f"🔴 รายจ่ายรวม: ฿{total_expense:,.0f}")
    lines.append(sep)

    for r in results[:8]:
        icon = "💚" if r["direction"] == "income" else "🔴"
        detail = (r.get("detail") or SOURCE_LABELS_SHORT.get(r["source"], r["source"]))[:20]
        lines.append(f"{icon} {r['entry_date']}: {detail} ฿{r['amount']:,.0f}")

    if count > 8:
        lines.append(f"... และอีก {count - 8} รายการ")
    return "\n".join(lines)


# ─────────────────────────────────────────────
# Webhook — main handler
# ─────────────────────────────────────────────

@router.post("/webhook")
async def line_webhook(
    request: Request,
    x_line_signature: str = Header(None, alias="x-line-signature"),
):
    """
    LINE Messaging API webhook

    Routes:
      📷 image message  → OCR (GPT Vision) → vendor_bills
      💬 text message:
          - "ค่าXXX 999"  → quick expense → manual_entries
          - "help"        → usage help
          - anything else → AI Search (Phase 11)

    ตั้งค่า Webhook URL ใน LINE Developers Console:
      https://<your-domain>/line/webhook
    """
    body = await request.body()

    if not _verify_signature(body, x_line_signature or ""):
        log.warning("LINE webhook: invalid signature")
        return {"status": "invalid signature"}

    data = json.loads(body)

    for event in data.get("events", []):
        if event.get("type") != "message":
            continue

        msg = event.get("message", {})
        msg_type = msg.get("type")
        reply_token = event.get("replyToken", "")
        if not reply_token:
            continue

        # ────────────────────────────────────────
        # 📷 IMAGE MESSAGE — Phase 13 OCR Bot
        # ────────────────────────────────────────
        if msg_type == "image":
            message_id = msg.get("id", "")
            log.info("LINE webhook image: message_id=%s", message_id)

            try:
                # 1. Download image from LINE
                image_bytes = _download_line_image(message_id)

                # 2. OCR via GPT Vision
                _reply_line(reply_token, "⏳ กำลัง OCR ใบกำกับ... รอสักครู่นะครับ")
                parsed = _ocr_invoice_image(image_bytes)

                # 3. Save to vendor_bills
                invoice_id = _save_invoice_from_line(parsed, image_bytes)

                # 4. Reply result
                reply = _format_ocr_reply(parsed, invoice_id)
                _push_text(reply)

            except Exception as e:
                log.error("LINE OCR flow failed: %s", e)
                _push_text(f"❌ OCR ล้มเหลว กรุณาลองใหม่\n({str(e)[:80]})")

        # ────────────────────────────────────────
        # 💬 TEXT MESSAGE
        # ────────────────────────────────────────
        elif msg_type == "text":
            text = msg.get("text", "").strip()
            if not text:
                continue

            log.info("LINE webhook text: %r", text)

            # Help
            if text.lower() in ("help", "ช่วยเหลือ", "?", "วิธีใช้"):
                _reply_line(reply_token,
                    "🤖 VEXONHQ LINE Bot\n"
                    "─────────────────────────\n"
                    "📷 ส่งรูปใบกำกับ/บิล → OCR อัตโนมัติ\n\n"
                    "💬 พิมพ์บันทึกค่าใช้จ่ายด่วน:\n"
                    "  ค่าน้ำมัน 450\n"
                    "  ค่าแก๊ส 350 บาท\n"
                    "  ซื้อผัก 200\n\n"
                    "🔍 หรือค้นหาข้อมูล:\n"
                    "  รายรับ Grab เดือนเมษา\n"
                    "  ค่าแก๊สทั้งหมด\n"
                    "  บิล Makro เดือนนี้"
                )
                continue

            # Quick expense entry
            quick = _parse_quick_expense(text)
            if quick:
                try:
                    entry_id = _save_quick_expense(quick["description"], quick["amount"])
                    amt_str = f"฿{quick['amount']:,.0f}"
                    _reply_line(reply_token,
                        f"✅ บันทึกรายจ่ายแล้ว!\n"
                        f"─────────────────────────\n"
                        f"📝 {quick['description']}: {amt_str}\n"
                        f"📅 {date.today().strftime('%d/%m/%Y')}\n"
                        f"🔑 ID: {entry_id[:8]}..."
                    )
                except Exception as e:
                    log.error("Quick expense save failed: %s", e)
                    _reply_line(reply_token, f"❌ บันทึกไม่สำเร็จ: {str(e)[:80]}")
                continue

            # AI Search (Phase 11)
            try:
                from phase11_search_routes import _call_claude_filter, _build_and_run_query
                search_filter = _call_claude_filter(text)
                results = _build_and_run_query(search_filter, 20)
                total_income  = sum(r["amount"] for r in results if r["direction"] == "income")
                total_expense = sum(r["amount"] for r in results if r["direction"] == "expense")
                reply = _format_search_for_line(text, len(results), total_income, total_expense, results)
            except Exception as e:
                log.error("Search for LINE failed: %s", e)
                reply = f"❌ เกิดข้อผิดพลาด กรุณาลองใหม่\n({str(e)[:80]})"

            _reply_line(reply_token, reply)

    return {"status": "ok"}


