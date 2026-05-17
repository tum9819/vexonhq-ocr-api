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
from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Request
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
# Phase 27: Stock / Inventory query via LINE
# ─────────────────────────────────────────────

# คำที่หมายถึง "เช็ค stock ทั้งหมด"
_STOCK_SUMMARY_KEYWORDS = (
    "stock", "สต็อก", "สต็อค", "สต๊อก", "สต๊อค",
    "เช็คของ", "ของเหลือ", "วัตถุดิบเหลือ", "สินค้าคงเหลือ",
    "เหลือเท่าไร", "มีของไหม", "ของหมดไหม", "รายงานสินค้า",
)

# [Bug1-fix] category modifier → ส่ง tag= หรือ keyword= ให้ _query_inventory
# value = str  → filter by tag (exact ILIKE)
# value = dict → {"keyword": "..."} filter by item_name ILIKE
_STOCK_CATEGORY_MAP: dict[str, str | dict] = {
    "เครื่องดื่ม": "เครื่องดื่ม",
    "หม่าล่า":     "หม่าล่า",
    "ผัก":         "ผัก",
    "ของทอด":      "ของทอด",
    "อาหาร":       "อาหาร",
    "ไส้เสียบ":    "หม่าล่า",
    "วัตถุดิบ":    "วัตถุดิบ",
    # keyword-based (ILIKE ชื่อสินค้า) — เพิ่มได้ไม่จำกัด
    "น้ำ":         {"keyword": "น้ำ", "tag": "เครื่องดื่ม"},   # เฉพาะน้ำในหมวดเครื่องดื่ม
    "น้ำดื่ม":     {"keyword": "น้ำ", "tag": "เครื่องดื่ม"},
    "เบียร์":      {"keyword": "เบียร์"},   # เบียร์ทุกยี่ห้อ
    "โซจู":        {"keyword": "โซจู"},
    "วิสกี้":      {"keyword": "วิสกี้"},
    "ไส้กรอก":     {"keyword": "ไส้กรอก"},
}

# ชื่อสินค้าที่ค้นหาใน stock (ไม่ใช่การเงิน)
_STOCK_PRODUCT_KEYWORDS = (
    "เบียร์", "สิงห์", "ไฮเนเกน", "อาซาฮี", "ลีโอ", "เฟดเดอร์บราว",
    "โซดา", "เป๊ปซี่", "มิรินด้า", "น้ำเปล่า", "น้ำแร่",
    "โซจู", "แกรนด์", "แสงโสม", "หงษ์ทอง", "รีเจนซี่",
    "หมูสามชั้น", "สันคอ", "สันใน", "เนื้อริบ", "วากิว",
    "ปูอัด", "หนวดหมึก", "ปลาหมึก", "กุ้งสด", "ท้องแซลมอน",
    "ใส้กรอก", "ไส้กรอก", "เบคอน", "ปีกไก่", "หัวใจไก่", "สันในไก่",
    "เห็ดออรินจิ", "บล็อคโคลี่", "กระเจี๊ยบ", "ข้าวโพดอ่อน",
    "เต้าหู้", "พริกหยวก", "ปลาไข่",
    # [Bug4-fix] สินค้าที่ขาดอยู่
    "น้ำแข็ง", "นักเก็ต", "ซูปเปอร์", "ยำ", "ใส้ทอด",
    "หมูมะนาว", "เอ็นข้อไก่", "ไส้กรอกแดง", "ไส้กรอกอีสาน",
    "เฟรนช์ฟรายส์", "เฟรนฟราย", "ข้าวเปล่า",
)

# คำที่บ่งบอกว่าถามเรื่องการเงิน (ถ้ามีคำนี้ → financial search แม้จะมีชื่อสินค้า)
_FINANCIAL_OVERRIDE_KEYWORDS = (
    "ยอดโอน", "รายจ่าย", "รายรับ", "โอนไป", "บิล",
    "ใบแจ้งหนี้", "invoice", "ค่าใช้จ่าย", "จ่ายเงิน",
    # [Bug3-fix] คำที่บอกการฝาก/โอนเงินเข้า
    "ฝากเข้า", "ฝาก", "โอนเข้า", "รับโอน", "รับเงิน",
    "เงินเดือน", "ค่าเช่า", "ค่าน้ำ", "ค่าไฟ",
)

# [Bug2-hint] แผนที่ชื่อสินค้า → ชื่อผู้รับในบัญชีธนาคาร
# โหลดจาก Supabase (ตาราง vendor_aliases) พร้อม cache 5 นาที
# แก้ได้ผ่าน Supabase Table Editor โดยไม่ต้อง redeploy
_vendor_hint_cache: dict[str, str] = {}
_vendor_hint_loaded_at: float = 0.0
_VENDOR_HINT_TTL: float = 300.0   # 5 นาที


def _load_vendor_hints() -> dict[str, str]:
    """Load vendor_aliases from DB; return cached copy if fresh."""
    import time as _time
    global _vendor_hint_cache, _vendor_hint_loaded_at

    if _time.monotonic() - _vendor_hint_loaded_at < _VENDOR_HINT_TTL:
        return _vendor_hint_cache

    try:
        conn = _get_db_conn()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT product_keyword, vendor_name "
                "FROM public.vendor_aliases "
                "WHERE is_active = true "
                "ORDER BY length(product_keyword) DESC"   # longer match first
            )
            rows = cur.fetchall()
        conn.close()
        _vendor_hint_cache = {r[0]: r[1] for r in rows}
        _vendor_hint_loaded_at = _time.monotonic()
        log.debug("vendor_aliases reloaded: %d entries", len(_vendor_hint_cache))
    except Exception as e:
        log.warning("vendor_aliases load failed (using cache): %s", e)

    return _vendor_hint_cache

# คำที่บ่งบอกว่าต้องการให้ AI แนะนำเมนู
_RECIPE_SUGGEST_KEYWORDS = (
    "แนะนำเมนู", "คิดเมนู", "ทำเมนูอะไร", "ทำอะไรได้บ้าง",
    "เมนูวันนี้", "ทำเมนูใหม่", "ไอเดียเมนู", "เมนูจากของที่มี",
)

# คำที่บ่งบอกว่าถามต้นทุนเมนู
_RECIPE_COST_KEYWORDS = (
    "ต้นทุน", "gp", "กำไรต่อจาน", "กำไรเมนู", "costเมนู",
)


def _classify_intent(text: str) -> str:
    """
    Classify LINE message intent:
    - recipe_suggest : ขอให้ AI แนะนำเมนูจาก stock
    - recipe_cost    : ถามต้นทุน/GP% ของเมนู
      'stock_summary'  — เช็ค stock ทั้งหมด
      'stock_product'  — หา stock รายการสินค้าเฉพาะ (เบียร์ช้าง, สิงห์, ...)
      'financial'      — ค้นหาข้อมูลการเงิน (ยอดโอนเบียร์ช้าง, รายจ่ายเบียร์)
      'other'          — ส่งต่อ AI Search หรือ handler อื่น
    """
    lower = text.lower().strip()

    # Check financial override first — ถ้ามีคำเงิน → financial เสมอ
    if any(kw in lower for kw in _FINANCIAL_OVERRIDE_KEYWORDS):
        return "financial"

    # Check recipe suggest keywords
    if any(kw in lower for kw in _RECIPE_SUGGEST_KEYWORDS):
        return "recipe_suggest"

    # Check recipe cost keywords
    if any(kw in lower for kw in _RECIPE_COST_KEYWORDS):
        return "recipe_cost"

    # Check stock summary keywords — but first check for category modifier
    if any(kw in lower for kw in _STOCK_SUMMARY_KEYWORDS):
        # [Bug1-fix] "เช็ค stock เครื่องดื่ม" → stock_category (filtered)
        if any(cat_kw in lower for cat_kw in _STOCK_CATEGORY_MAP):
            return "stock_category"
        return "stock_summary"

    # Check product name keywords (without financial context)
    if any(kw in lower for kw in _STOCK_PRODUCT_KEYWORDS):
        return "stock_product"

    return "other"


def _extract_product_keyword(text: str) -> str:
    """Extract the product keyword from the query to search in stock."""
    lower = text.lower().strip()
    for kw in _STOCK_PRODUCT_KEYWORDS:
        if kw in lower:
            return kw
    return text.strip()


def _handle_stock_summary() -> str:
    """Return a LINE-friendly full stock summary from pos_inventory_items."""
    try:
        from stock_routes import _query_inventory, format_stock_for_line
        items, snapshot_at = _query_inventory(low_only=False)
        return format_stock_for_line(items, snapshot_at, "📦 สรุป Stock ทั้งหมด")
    except Exception as e:
        log.error("Stock summary failed: %s", e)
        return f"❌ เกิดข้อผิดพลาดในการเช็ค stock: {str(e)[:80]}"


# Session 15 fix (2026-05-17): when FoodStory tag is missing/different from
# user query, fall back to keyword search by item_name. Example: user typed
# "เช็ค stock เครื่องดื่ม" but DB has no tag="เครื่องดื่ม" → search items
# whose name contains common drink keywords (เบียร์, น้ำ, โซดา, ...).
_CATEGORY_NAME_FALLBACK: dict[str, list[str]] = {
    "เครื่องดื่ม": ["เบียร์", "โซดา", "น้ำดื่ม", "น้ำเปล่า", "น้ำแร่",
                    "เป๊ปซี่", "เป็ปซี่", "มิรินด้า", "วิสกี้", "โซจู",
                    "โซจูมีเฮ", "แสงโสม", "หงษ์ทอง", "รีเจนซี่", "แกรนด์",
                    "ไฮเนเกน", "ไฮนาเกน", "อาซาฮี", "ลีโอ", "สิงห์",
                    "ช้าง", "เฟดเดอร์บราว", "Federbrau", "(pro)"],
    "หม่าล่า":     ["ไส้กรอก", "หมูสามชั้น", "สันคอ", "เนื้อ", "ปีกไก่",
                    "หัวใจไก่", "เห็ด", "ปลาหมึก", "กุ้ง", "ปูอัด",
                    "เต้าหู้", "ลูกชิ้น"],
    "ผัก":         ["บล็อคโคลี่", "กระเจี๊ยบ", "ข้าวโพด", "เห็ด",
                    "พริก", "ผักกาด", "ผักบุ้ง", "คะน้า"],
    "ของทอด":      ["เฟรนช์ฟราย", "นักเก็ต", "ไส้กรอก", "ปีกไก่"],
}


def _query_inventory_by_keywords(keywords: list[str]) -> tuple[list[dict], str]:
    """Run _query_inventory for each keyword and merge unique items by name."""
    from stock_routes import _query_inventory
    seen: set[str] = set()
    merged: list[dict] = []
    snap = ""
    for kw in keywords:
        try:
            items, sa = _query_inventory(keyword=kw, low_only=False)
            if sa:
                snap = sa
            for it in items:
                name = it.get("item_name") or ""
                if name and name not in seen:
                    seen.add(name)
                    merged.append(it)
        except Exception as e:
            log.warning("inventory keyword query failed (%s): %s", kw, e)
    return merged, snap


def _list_available_tags() -> list[str]:
    """Return distinct non-null tags from latest snapshot for hints."""
    try:
        from stock_routes import _get_latest_snapshot_id
        snapshot_id, _ = _get_latest_snapshot_id()
        if not snapshot_id:
            return []
        conn = _get_db_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT DISTINCT tag FROM public.pos_inventory_items
                    WHERE snapshot_id = %s AND tag IS NOT NULL AND tag <> ''
                    ORDER BY tag
                """, (snapshot_id,))
                return [r[0] for r in cur.fetchall()]
        finally:
            conn.close()
    except Exception as e:
        log.warning("list_available_tags failed: %s", e)
        return []


def _handle_stock_category(query: str) -> str:
    """Return LINE-friendly stock filtered by tag with smart name-keyword fallback."""
    lower = query.lower()
    tag = None
    keyword = None
    label = None
    for cat_kw, filter_val in _STOCK_CATEGORY_MAP.items():
        if cat_kw in lower:
            label = cat_kw
            if isinstance(filter_val, dict):
                keyword = filter_val.get("keyword")
                tag = filter_val.get("tag")
            else:
                tag = filter_val
            break
    try:
        from stock_routes import _query_inventory, format_stock_for_line
        items, snapshot_at = _query_inventory(tag=tag, keyword=keyword, low_only=False)

        # Session 15 fix v2: ALWAYS merge keyword-fallback results for known
        # categories. The DB often has some items tagged "เครื่องดื่ม" and
        # many more without that tag — we want both. Previously the fallback
        # only fired if tag returned 0, so a single tagged item hid all the
        # untagged drinks.
        if label and label in _CATEGORY_NAME_FALLBACK:
            log.info("stock_category '%s' merging keyword fallback (had %d tag items)",
                     label, len(items))
            fb_items, fb_snap = _query_inventory_by_keywords(_CATEGORY_NAME_FALLBACK[label])
            if fb_snap:
                snapshot_at = snapshot_at or fb_snap
            seen = {it.get("item_name", "") for it in items if it.get("item_name")}
            for it in fb_items:
                name = it.get("item_name") or ""
                if name and name not in seen:
                    seen.add(name)
                    items.append(it)
            log.info("stock_category '%s' merged total: %d items", label, len(items))

        title = f"📦 Stock {label or tag or keyword or 'ทั้งหมด'}"
        if not items:
            sep22 = "─" * 22
            tags = _list_available_tags()
            hint_lines = [f"{title}", sep22, "ไม่พบข้อมูลหมวดนี้ครับ"]
            if tags:
                hint_lines.append("")
                hint_lines.append("💡 หมวดที่มีในระบบ:")
                for t in tags[:10]:
                    hint_lines.append(f"  • {t}")
                hint_lines.append("")
                hint_lines.append("ลอง: เช็ค stock <ชื่อหมวด>")
            else:
                hint_lines.append("ลอง: เช็ค stock ทั้งหมด")
            return "\n".join(hint_lines)
        return format_stock_for_line(items, snapshot_at, title)
    except Exception as e:
        log.error("Stock category failed: %s", e)
        return f"❌ เกิดข้อผิดพลาด: {str(e)[:80]}"


def _handle_stock_product(query: str) -> str:
    """Return LINE-friendly stock for a specific product keyword."""
    try:
        from stock_routes import _query_inventory, format_product_stock_for_line
        keyword = _extract_product_keyword(query)
        items, snapshot_at = _query_inventory(keyword=keyword)
        return format_product_stock_for_line(items, snapshot_at, query)
    except Exception as e:
        log.error("Stock product search failed: %s", e)
        return f"❌ เกิดข้อผิดพลาด: {str(e)[:80]}"


def _handle_recipe_suggest() -> str:
    """Call AI suggest endpoint and format for LINE."""
    import json as _json
    import urllib.request as _req
    import urllib.error as _uerr
    import os as _os

    api_key = _os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return "❌ ยังไม่ได้ตั้งค่า ANTHROPIC_API_KEY"

    try:
        conn = _get_db_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT ii.item_name, ii.qty, ii.unit
                    FROM public.pos_inventory_items ii
                    JOIN public.pos_inventory_snapshots s ON s.id = ii.snapshot_id
                    WHERE s.branch_code = 'thawi_watthana'
                    ORDER BY s.snapshot_at DESC, ii.item_name
                    LIMIT 80
                """)
                stock_items = cur.fetchall()

                cur.execute("""
                    SELECT name, unit, price_per_unit, yield_pct
                    FROM public.ingredients WHERE price_per_unit > 0
                """)
                ingredients = cur.fetchall()
        finally:
            conn.close()

        if not stock_items:
            return "❌ ไม่พบข้อมูล stock — กรุณา upload FoodStory stock ก่อนครับ"

        stock_text = "\n".join(f"- {n}: {q} {u}" for n, q, u in stock_items)
        ingr_text = "\n".join(
            f"- {n} ({u}): {p:.0f} บาท"
            for n, u, p, _ in ingredients
        ) or "ยังไม่มีราคาวัตถุดิบ"

        payload = _json.dumps({
            "model": "claude-haiku-4-5-20251001",
            "max_tokens": 512,
            "system": "คุณคือเชฟ AI ของร้านมาลาปิ้งย่าง ตอบภาษาไทย กระชับ",
            "messages": [{"role": "user", "content":
                f"วัตถุดิบในร้าน:\n{stock_text}\n\nราคาวัตถุดิบ:\n{ingr_text}\n\n"
                "แนะนำ 3 เมนูที่ทำได้จากวัตถุดิบนี้ รูปแบบ:\n"
                "1. ชื่อเมนู — ต้นทุน ~XX บาท | ราคาขาย XXX | GP XX%\n"
                "วัตถุดิบ: ...\n\n"
                "ตอบสั้น ไม่เกิน 10 บรรทัด"
            }],
        }).encode("utf-8")

        req = _req.Request(
            "https://api.anthropic.com/v1/messages",
            data=payload,
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            method="POST",
        )
        with _req.urlopen(req, timeout=25) as resp:
            data = _json.loads(resp.read().decode("utf-8"))
            answer = data["content"][0]["text"].strip()

        return f"🍽️ AI แนะนำเมนูวันนี้\n{'─'*22}\n{answer}"

    except Exception as e:
        log.error("Recipe suggest LINE failed: %s", e)
        return f"❌ ไม่สามารถแนะนำเมนูได้: {str(e)[:80]}"


def _handle_recipe_cost(query: str) -> str:
    """Look up recipe cost/GP% by name from DB."""
    try:
        conn = _get_db_conn()
        try:
            with conn.cursor() as cur:
                # Try to find recipe by name (fuzzy)
                clean = query.lower()
                for kw in _RECIPE_COST_KEYWORDS:
                    clean = clean.replace(kw, "").strip()
                clean = clean.strip()

                if not clean:
                    # No recipe name → list all
                    cur.execute("""
                        SELECT name, selling_price FROM public.recipes ORDER BY name LIMIT 10
                    """)
                    rows = cur.fetchall()
                    if not rows:
                        return "📋 ยังไม่มีสูตรอาหารในระบบ\nเพิ่มได้ที่ /recipes ในเว็บครับ"
                    lines = ["📋 เมนูในระบบ:"]
                    for name, price in rows:
                        lines.append(f"• {name} — ราคา {price:.0f} บาท")
                    lines.append("\nพิมพ์ 'ต้นทุน[ชื่อเมนู]' เพื่อดูรายละเอียดครับ")
                    return "\n".join(lines)

                cur.execute("""
                    SELECT r.id, r.name, r.selling_price,
                           COALESCE(SUM(ri.qty_used * i.price_per_unit / NULLIF(i.yield_pct/100.0, 0)), 0) as cost
                    FROM public.recipes r
                    LEFT JOIN public.recipe_ingredients ri ON ri.recipe_id = r.id
                    LEFT JOIN public.ingredients i ON i.id = ri.ingredient_id
                    WHERE LOWER(r.name) LIKE %s
                    GROUP BY r.id, r.name, r.selling_price
                    LIMIT 3
                """, (f"%{clean}%",))
                rows = cur.fetchall()

                if not rows:
                    return f'❌ ไม่พบเมนู "{clean}" ในระบบครับ\nลองพิมพ์ "ต้นทุน" เพื่อดูรายการทั้งหมด'

                lines = []
                for rid, name, sell, cost in rows:
                    sell = float(sell or 0)
                    cost = float(cost or 0)
                    gp = (sell - cost) / sell * 100 if sell > 0 else 0
                    emoji = "🟢" if gp >= 60 else ("🟡" if gp >= 40 else "🔴")
                    lines.append(
                        f"{emoji} {name}\n"
                        f"   ต้นทุน: ฿{cost:.2f} | ราคาขาย: ฿{sell:.0f}\n"
                        f"   GP: {gp:.1f}%"
                    )
                return "💰 ต้นทุนเมนู\n" + "─"*22 + "\n" + "\n\n".join(lines)
        finally:
            conn.close()
    except Exception as e:
        log.error("Recipe cost LINE failed: %s", e)
        return f"❌ เกิดข้อผิดพลาด: {str(e)[:80]}"


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
        # [Bug2-fix] suggest vendor alias when user searched by product name
        q_lower = query.lower()
        hint_lines = []
        for product, vendor in _load_vendor_hints().items():
            if product in q_lower:
                hint_lines.append(f"  '{product}' บันทึกในบัญชีว่า '{vendor}'")
        hint = ""
        if hint_lines:
            hint = "\n\n💡 ข้อมูลธนาคารบันทึกตามชื่อผู้รับ:\n" + "\n".join(hint_lines)
        return f'🔍 "{query}"\n{sep}\nไม่พบรายการที่ตรงกันครับ{hint}'

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
#
# Session 15 Fix (2026-05-17):
#   - LINE webhook ต้อง return 200 ภายใน ~1-2 วินาที
#   - reply_token หมดอายุใน 30 วินาที
#   - Claude API / GPT Vision ใช้เวลา 10-30s → ทำใน sync handler ไม่ทัน
#   → ใช้ BackgroundTasks: parse + verify ทันที, return 200, process หลังบ้าน
#   → ถ้า processing >25s ใช้ _push_text แทน _reply_line ป้องกัน token expired
# ─────────────────────────────────────────────

def _process_one_event(event: dict) -> None:
    """Handle a single LINE event in the background (called after webhook returned 200)."""
    if event.get("type") != "message":
        return

    msg = event.get("message", {})
    msg_type = msg.get("type")
    reply_token = event.get("replyToken", "")
    if not reply_token:
        return

    # ────────────────────────────────────────
    # 📷 IMAGE MESSAGE — Phase 13 OCR Bot
    # ────────────────────────────────────────
    if msg_type == "image":
        message_id = msg.get("id", "")
        log.info("LINE webhook image: message_id=%s", message_id)

        try:
            # 1. Acknowledge IMMEDIATELY (reply_token expires in 30s)
            _reply_line(reply_token, "⏳ กำลัง OCR ใบกำกับ... รอสักครู่นะครับ")

            # 2. Download image from LINE
            image_bytes = _download_line_image(message_id)

            # 3. OCR via GPT Vision (slow — 10-30s)
            parsed = _ocr_invoice_image(image_bytes)

            # 4. Save to vendor_bills
            invoice_id = _save_invoice_from_line(parsed, image_bytes)

            # 5. Push result (reply_token already used above; use push)
            reply = _format_ocr_reply(parsed, invoice_id)
            _push_text(reply)

        except Exception as e:
            log.error("LINE OCR flow failed: %s", e)
            try:
                _push_text(f"❌ OCR ล้มเหลว กรุณาลองใหม่\n({str(e)[:80]})")
            except Exception:
                pass
        return

    # ────────────────────────────────────────
    # 💬 TEXT MESSAGE
    # ────────────────────────────────────────
    if msg_type != "text":
        return

    text = msg.get("text", "").strip()
    if not text:
        return

    log.info("LINE webhook text: %r", text)

    # Help
    if text.lower() in ("help", "ช่วยเหลือ", "?", "วิธีใช้"):
        try:
            _reply_line(reply_token,
                "🤖 VEXONHQ LINE Bot\n"
                "─────────────────────────\n"
                "📷 ส่งรูปใบกำกับ/บิล → OCR อัตโนมัติ\n\n"
                "💬 บันทึกค่าใช้จ่ายด่วน:\n"
                "  ค่าน้ำมัน 450\n"
                "  ค่าแก๊ส 350 บาท\n"
                "  ซื้อผัก 200\n\n"
                "📦 เช็ค stock / วัตถุดิบ:\n"
                "  เช็ค stock\n"
                "  สต็อกเหลือเท่าไร\n"
                "  มีของไหม\n\n"
                "🔍 ค้นหาข้อมูล:\n"
                "  เงินเดือนเดือนเมษา\n"
                "  เบียร์ช้าง / เบียร์สิงห์\n"
                "  วันไหนขายดีสุดเดือนเมษา\n"
                "  รายรับ Grab เดือนเมษา\n"
                "  ค่าเช่าเดือนนี้\n"
                "  บิล Makro ทั้งหมด\n"
                "  รายจ่ายเกิน 5000 บาท"
            )
        except Exception as e:
            log.error("Help reply failed: %s", e)
        return

    # Quick expense entry — fast (< 1s)
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
            try:
                _reply_line(reply_token, f"❌ บันทึกไม่สำเร็จ: {str(e)[:80]}")
            except Exception:
                _push_text(f"❌ บันทึกไม่สำเร็จ: {str(e)[:80]}")
        return

    # ── Intent classification (Phase 27 + 31) ──
    intent = _classify_intent(text)
    log.info("LINE intent: %r → %s", text, intent)

    # Fast intents (DB query only) — use reply_line
    if intent in ("stock_summary", "stock_product", "stock_category", "recipe_cost"):
        try:
            if intent == "stock_summary":
                reply_text = _handle_stock_summary()
            elif intent == "stock_product":
                reply_text = _handle_stock_product(text)
            elif intent == "stock_category":
                reply_text = _handle_stock_category(text)
            else:
                reply_text = _handle_recipe_cost(text)
            _reply_line(reply_token, reply_text)
        except Exception as e:
            log.error("Fast intent handler failed (%s): %s", intent, e)
            try:
                _push_text(f"❌ เกิดข้อผิดพลาด: {str(e)[:80]}")
            except Exception:
                pass
        return

    # Slow intents (AI call) — ack with reply_line, push result later
    if intent == "recipe_suggest":
        try:
            _reply_line(reply_token, "🍳 กำลังคิดเมนูให้... รอสักครู่นะครับ")
            reply_text = _handle_recipe_suggest()
            _push_text(reply_text)
        except Exception as e:
            log.error("recipe_suggest failed: %s", e)
            try:
                _push_text(f"❌ เกิดข้อผิดพลาด: {str(e)[:80]}")
            except Exception:
                pass
        return

    # intent == "financial" or "other" → AI Search (Phase 11) — also slow
    try:
        _reply_line(reply_token, "🔍 กำลังค้นหา... รอสักครู่นะครับ")
        from phase11_search_routes import _call_claude_filter, _build_and_run_query
        search_filter = _call_claude_filter(text)
        results = _build_and_run_query(search_filter, 20)
        total_income  = sum(r["amount"] for r in results if r["direction"] == "income")
        total_expense = sum(r["amount"] for r in results if r["direction"] == "expense")
        reply = _format_search_for_line(text, len(results), total_income, total_expense, results)
        _push_text(reply)
    except Exception as e:
        log.error("Search for LINE failed: %s", e)
        try:
            _push_text(f"❌ เกิดข้อผิดพลาด กรุณาลองใหม่\n({str(e)[:80]})")
        except Exception:
            pass


def _process_line_events(data: dict) -> None:
    """Process all events in a webhook payload (called in BackgroundTask)."""
    for event in data.get("events", []):
        try:
            _process_one_event(event)
        except Exception as e:
            log.error("LINE event handler crashed: %s", e)


@router.post("/webhook")
async def line_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_line_signature: str = Header(None, alias="x-line-signature"),
):
    """
    LINE Messaging API webhook

    Returns 200 immediately and processes events in the background.
    This is REQUIRED because LINE expects acknowledgement within ~1-2s
    and reply_token expires after 30s — synchronous handlers that call
    Claude/GPT (10-30s) would miss both deadlines.

    Routes (processed in background):
      📷 image message  → OCR (GPT Vision) → vendor_bills
      💬 text message:
          - "ค่าXXX 999"  → quick expense → manual_entries
          - "help"        → usage help
          - stock query   → stock_routes
          - anything else → AI Search (Phase 11)

    ตั้งค่า Webhook URL ใน LINE Developers Console:
      https://<your-domain>/line/webhook
    """
    body = await request.body()

    if not _verify_signature(body, x_line_signature or ""):
        log.warning("LINE webhook: invalid signature")
        return {"status": "invalid signature"}

    try:
        data = json.loads(body)
    except json.JSONDecodeError as e:
        log.error("LINE webhook: invalid JSON body: %s", e)
        return {"status": "invalid body"}

    # Schedule processing in background. We return 200 to LINE immediately.
    background_tasks.add_task(_process_line_events, data)
    return {"status": "ok"}
