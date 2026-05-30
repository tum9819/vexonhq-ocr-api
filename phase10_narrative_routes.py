"""
VEXONHQ Phase 10 — Monthly P&L Narrative AI
============================================
Endpoint:
    POST /pnl/narrative?month=YYYY-MM   → generate Thai P&L narrative via Claude + send to LINE

Scheduled: 1st of each month at 08:00 Bangkok (Coolify cron: 0 1 1 * * UTC = 08:00 BKK)

Narrative covers:
  - รายรับรวม / รายจ่ายรวม / กำไร-ขาดทุนสุทธิ
  - เทียบกับเดือนก่อนหน้า (% change)
  - Top 3 หมวดค่าใช้จ่าย
  - แหล่งรายรับ (POS / Grab / Lineman / อื่นๆ)
  - 1-2 ข้อสังเกตหรือคำแนะนำ
"""

from __future__ import annotations

import calendar
import json
import logging
import os
import urllib.request
import urllib.error
from datetime import date, datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

try:
    from main import get_db_conn  # type: ignore
except ImportError:
    import psycopg2
    def get_db_conn():
        return psycopg2.connect(os.environ["DATABASE_URL"])

logger = logging.getLogger("narrative")
router = APIRouter(prefix="/pnl", tags=["narrative"])

LINE_PUSH_URL = "https://api.line.me/v2/bot/message/push"

MONTHS_TH = [
    "", "มกราคม", "กุมภาพันธ์", "มีนาคม", "เมษายน", "พฤษภาคม", "มิถุนายน",
    "กรกฎาคม", "สิงหาคม", "กันยายน", "ตุลาคม", "พฤศจิกายน", "ธันวาคม",
]

# Sources to exclude from P&L aggregates. Mirrors pnl_routes.py:96-99 so the
# monthly narrative agrees with /pnl/daily and /dashboard/overview. Adding this
# closes the Session-6 incident class (equity inflates income, transfer pairs
# pollute expense). Spliced into queries via f-string — values are constants
# so SQL injection is not a concern.
_EXCLUDED_SOURCES_SQL = (
    "AND d.source NOT IN ("
    "'owner_capital', 'owner_advance', 'transfer_error', "
    "'bank_statement', 'vendor_payment', "
    "'grab_payout', 'lineman_payout', "
    "'pos_cash_deposit', 'cash_withdrawal'"
    ")"
)


# ─── Helpers ───────────────────────────────────────────────────────────────────

def _month_range(month: str) -> tuple[date, date]:
    y, m = int(month[:4]), int(month[5:7])
    last = calendar.monthrange(y, m)[1]
    return date(y, m, 1), date(y, m, last)


def _prev_month_str(month: str) -> str:
    y, m = int(month[:4]), int(month[5:7])
    m -= 1
    if m == 0:
        m, y = 12, y - 1
    return f"{y}-{m:02d}"


def _month_label_th(month: str) -> str:
    y, m = int(month[:4]), int(month[5:7])
    return f"{MONTHS_TH[m]} {y + 543}"


def _fmt(n: float) -> str:
    return f"{n:,.0f}"


def _pct_change(current: float, prev: float) -> str:
    if prev == 0:
        return "N/A"
    pct = ((current - prev) / abs(prev)) * 100
    sign = "+" if pct >= 0 else ""
    return f"{sign}{pct:.1f}%"


def _push_line(text: str) -> None:
    """Push narrative to LINE."""
    token = os.environ.get("LINE_CHANNEL_TOKEN", "")
    user_id = os.environ.get("LINE_USER_ID", "")
    if not token or not user_id:
        raise HTTPException(500, "LINE_CHANNEL_TOKEN or LINE_USER_ID not set")

    payload = json.dumps({
        "to": user_id,
        "messages": [{"type": "text", "text": text}],
    }).encode("utf-8")

    req = urllib.request.Request(
        LINE_PUSH_URL,
        data=payload,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15):
            pass
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise HTTPException(502, f"LINE error {e.code}: {body}")


def _call_claude(prompt: str) -> str:
    """Call Claude API and return the text response."""
    from llm import call_anthropic, LLMError
    try:
        return call_anthropic("narrative", prompt, max_tokens=1024, timeout=30)
    except LLMError as e:
        raise HTTPException(e.status_for_http(), f"Claude API error: {e.detail}")


# ─── Data gathering ─────────────────────────────────────────────────────────────

def _gather_month_data(month: str) -> dict:
    """Query v_daybook for the given month and return a summary dict."""
    first, last = _month_range(month)
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            # ── Overall income / expense / net ──
            cur.execute(
                f"""SELECT
                     COALESCE(SUM(CASE WHEN d.direction='income'  THEN d.amount ELSE 0 END), 0) AS total_income,
                     COALESCE(SUM(CASE WHEN d.direction='expense' THEN d.amount ELSE 0 END), 0) AS total_expense
                   FROM public.v_daybook d
                   WHERE d.entry_date BETWEEN %s AND %s
                     {_EXCLUDED_SOURCES_SQL}""",
                (first, last),
            )
            row = cur.fetchone()
            total_income = float(row[0])
            total_expense = float(row[1])
            net = total_income - total_expense
            margin = (net / total_income * 100) if total_income > 0 else 0.0

            # ── Income breakdown by source ──
            cur.execute(
                f"""SELECT d.source,
                          COALESCE(SUM(d.amount), 0) AS total
                   FROM public.v_daybook d
                   WHERE d.direction = 'income'
                     AND d.entry_date BETWEEN %s AND %s
                     {_EXCLUDED_SOURCES_SQL}
                   GROUP BY d.source
                   ORDER BY total DESC""",
                (first, last),
            )
            income_by_source = [{"source": r[0], "amount": float(r[1])} for r in cur.fetchall()]

            # ── Top 5 expense categories ──
            cur.execute(
                f"""SELECT
                     COALESCE(ec.name_th, d.category_code, d.source) AS cat_name,
                     COALESCE(SUM(d.amount), 0) AS total
                   FROM public.v_daybook d
                   LEFT JOIN public.expense_categories ec ON ec.code = d.category_code
                   WHERE d.direction = 'expense'
                     AND d.entry_date BETWEEN %s AND %s
                     {_EXCLUDED_SOURCES_SQL}
                   GROUP BY 1
                   ORDER BY total DESC
                   LIMIT 5""",
                (first, last),
            )
            top_expenses = [{"name": r[0], "amount": float(r[1])} for r in cur.fetchall()]

            # ── Transaction count ──
            cur.execute(
                f"""SELECT COUNT(*)
                   FROM public.v_daybook d
                   WHERE d.entry_date BETWEEN %s AND %s
                     {_EXCLUDED_SOURCES_SQL}""",
                (first, last),
            )
            txn_count = int(cur.fetchone()[0])

    finally:
        conn.close()

    return {
        "month": month,
        "label": _month_label_th(month),
        "total_income": total_income,
        "total_expense": total_expense,
        "net": net,
        "margin_pct": margin,
        "income_by_source": income_by_source,
        "top_expenses": top_expenses,
        "txn_count": txn_count,
    }


# ─── Prompt builder ──────────────────────────────────────────────────────────────

SOURCE_LABEL_TH = {
    "pos_sale":             "POS ขายหน้าร้าน",
    "rider_income_grab":    "Grab Delivery",
    "rider_income_lineman": "Lineman Delivery",
    "ar_payment":           "รับชำระหนี้ (AR)",
    "manual":               "บันทึกรายรับ",
}


def _build_prompt(current: dict, prev: dict | None) -> str:
    inc_lines = "\n".join(
        f"  - {SOURCE_LABEL_TH.get(s['source'], s['source'])}: ฿{_fmt(s['amount'])}"
        for s in current["income_by_source"]
    ) or "  - ไม่มีข้อมูล"

    exp_lines = "\n".join(
        f"  {i+1}. {e['name']}: ฿{_fmt(e['amount'])}"
        for i, e in enumerate(current["top_expenses"])
    ) or "  - ไม่มีข้อมูล"

    prev_section = ""
    if prev:
        inc_chg = _pct_change(current["total_income"], prev["total_income"])
        exp_chg = _pct_change(current["total_expense"], prev["total_expense"])
        net_chg = _pct_change(current["net"], prev["net"])
        prev_section = f"""
เทียบกับเดือนก่อน ({prev["label"]}):
  รายรับ: ฿{_fmt(prev["total_income"])} → ฿{_fmt(current["total_income"])} ({inc_chg})
  รายจ่าย: ฿{_fmt(prev["total_expense"])} → ฿{_fmt(current["total_expense"])} ({exp_chg})
  กำไร: ฿{_fmt(prev["net"])} → ฿{_fmt(current["net"])} ({net_chg})"""

    return f"""คุณเป็นที่ปรึกษาบัญชีของร้านมรสวรรค์ เสียบ เผาไฟ (ร้านหม่าล่าเสียบปิ้ง ย่านทวีวัฒนา กรุงเทพ)
กรุณาเขียนสรุปผลประกอบการประจำเดือน{current["label"]} ภาษาไทยที่เป็นมิตร กระชับ และเข้าใจง่าย
ความยาวประมาณ 180–220 คำ ส่งให้เจ้าของร้านอ่านทาง LINE

ข้อมูลเดือน{current["label"]}:
  รายรับรวม:  ฿{_fmt(current["total_income"])}
  รายจ่ายรวม: ฿{_fmt(current["total_expense"])}
  กำไรสุทธิ:  ฿{_fmt(current["net"])} (margin {current["margin_pct"]:.1f}%)
  จำนวนธุรกรรม: {current["txn_count"]} รายการ

แหล่งรายรับ:
{inc_lines}

Top 5 หมวดรายจ่าย:
{exp_lines}
{prev_section}

คำแนะนำในการเขียน:
- ขึ้นต้นด้วยชื่อเดือนและภาพรวม
- กล่าวถึงรายรับ/รายจ่าย/กำไร พร้อมตัวเลขสำคัญ
- เปรียบเทียบกับเดือนก่อน (ถ้ามีข้อมูล)
- ระบุหมวดค่าใช้จ่ายที่สูงที่สุด
- ปิดท้ายด้วยข้อสังเกตหรือคำแนะนำ 1-2 ข้อที่เป็นประโยชน์
- ไม่ต้องใส่หัวข้อหรือ bullet points — เขียนเป็น paragraph ต่อเนื่อง"""


# ─── Endpoint ───────────────────────────────────────────────────────────────────

@router.post("/narrative")
def generate_narrative(
    month: str = Query(..., description="YYYY-MM (ค่าเริ่มต้น = เดือนที่แล้ว)"),
    send_line: bool = Query(True, description="ส่งไปยัง LINE หลังสร้าง narrative"),
):
    """
    สร้าง P&L narrative ด้วย Claude AI แล้วส่ง LINE (ถ้า send_line=true)

    ใช้ทดสอบ: POST /pnl/narrative?month=2026-04&send_line=false
    ใช้จริง (cron): POST /pnl/narrative?month=2026-04&send_line=true
    """
    # Validate month format
    try:
        y, m = int(month[:4]), int(month[5:7])
        if not (1 <= m <= 12):
            raise ValueError
    except Exception:
        raise HTTPException(400, f"month must be YYYY-MM, got: {month!r}")

    logger.info("Generating P&L narrative for %s", month)

    # Gather data
    try:
        current_data = _gather_month_data(month)
    except Exception as e:
        logger.exception("Failed to gather data for %s", month)
        raise HTTPException(500, f"DB error: {e}")

    # Gather previous month for comparison
    prev_month = _prev_month_str(month)
    try:
        prev_data = _gather_month_data(prev_month)
        # If prev month has no data, skip comparison
        if prev_data["txn_count"] == 0:
            prev_data = None
    except Exception:
        prev_data = None

    # Build prompt and call Claude
    prompt = _build_prompt(current_data, prev_data)
    narrative = _call_claude(prompt)

    # Format final LINE message
    net_sign = "+" if current_data["net"] >= 0 else ""
    header = (
        f"📊 สรุปผลประกอบการ {current_data['label']}\n"
        f"{'─' * 28}\n"
        f"💰 รายรับ:  ฿{_fmt(current_data['total_income'])}\n"
        f"💸 รายจ่าย: ฿{_fmt(current_data['total_expense'])}\n"
        f"📈 กำไรสุทธิ: {net_sign}฿{_fmt(current_data['net'])} ({current_data['margin_pct']:.1f}%)\n"
        f"{'─' * 28}\n\n"
    )
    full_message = header + narrative

    # Send to LINE
    if send_line:
        _push_line(full_message)
        logger.info("Narrative sent to LINE for %s", month)

    return {
        "month": month,
        "label": current_data["label"],
        "narrative": narrative,
        "full_message": full_message,
        "sent_to_line": send_line,
        "stats": {
            "total_income": current_data["total_income"],
            "total_expense": current_data["total_expense"],
            "net": current_data["net"],
            "margin_pct": round(current_data["margin_pct"], 1),
            "txn_count": current_data["txn_count"],
        },
    }


@router.get("/narrative/preview")
def preview_narrative(
    month: str = Query(..., description="YYYY-MM"),
):
    """Preview the data that will be used for narrative (no Claude call, no LINE)."""
    try:
        y, m = int(month[:4]), int(month[5:7])
        if not (1 <= m <= 12):
            raise ValueError
    except Exception:
        raise HTTPException(400, f"month must be YYYY-MM, got: {month!r}")

    current_data = _gather_month_data(month)
    prev_month = _prev_month_str(month)
    try:
        prev_data = _gather_month_data(prev_month)
        if prev_data["txn_count"] == 0:
            prev_data = None
    except Exception:
        prev_data = None

    return {
        "current": current_data,
        "previous": prev_data,
        "prompt_preview": _build_prompt(current_data, prev_data),
    }
