"""
breakeven_routes.py — Monthly breakeven analysis: revenue vs fixed costs
=========================================================================
Endpoints:
    GET /breakeven/current?year=YYYY&month=MM   — real-time breakeven status

Scheduled LINE notifications are registered in line_bot_routes.py:
    Weekly (Wednesday 09:30 BKK)        — mid-month progress
    Monthly close (1st of month 08:00)  — previous month summary

Fixed costs are expense_categories rows where is_fixed=true (set by migration
2026_06_16_expense_categories_is_fixed.sql).
"""

from __future__ import annotations

import calendar
import logging
import os
from datetime import date, datetime
from typing import Optional
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Query

try:
    from main import get_db_conn  # type: ignore
except ImportError:
    import psycopg2
    def get_db_conn():
        return psycopg2.connect(os.environ["DATABASE_URL"])

from llm import openai_chat

log = logging.getLogger("vexonhq.breakeven")
router = APIRouter(prefix="/breakeven", tags=["breakeven"])

_BKK = ZoneInfo("Asia/Bangkok")
_THAI_MONTHS = ["","ม.ค.","ก.พ.","มี.ค.","เม.ย.","พ.ค.","มิ.ย.","ก.ค.","ส.ค.","ก.ย.","ต.ค.","พ.ย.","ธ.ค."]

# Same P&L source exclusion list used across pnl_routes.py and line_bot_routes.py
_EXCLUDE_SOURCES = (
    'owner_capital', 'owner_advance', 'transfer_error',
    'bank_statement', 'vendor_payment', 'grab_payout', 'lineman_payout',
    'payment_gateway_payout',
    'pos_cash_deposit', 'cash_withdrawal', 'loan_in', 'loan_repayment',
)


def _now_bkk() -> datetime:
    return datetime.now(_BKK)


def calc_breakeven_status(year: int, month: int) -> dict:
    """
    Calculate monthly breakeven: all-branch revenue vs is_fixed expense categories.

    Returns dict with fixed_costs, revenue, gap, surplus, progress_pct,
    days_remaining, daily_target_needed, is_covered, month_label.
    """
    today = _now_bkk().date()
    month_start = date(year, month, 1)
    days_in_month = calendar.monthrange(year, month)[1]
    month_end = date(year, month, days_in_month)

    if today <= month_start:
        days_elapsed = 0
    elif today > month_end:
        days_elapsed = days_in_month
    else:
        days_elapsed = (today - month_start).days + 1

    days_remaining = max(0, (month_end - today).days)

    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            # Fixed costs: expenses in categories flagged is_fixed=true
            cur.execute(
                """
                SELECT COALESCE(SUM(d.amount), 0)
                FROM public.v_daybook d
                JOIN public.expense_categories ec ON ec.code = d.category_code
                WHERE d.direction = 'expense'
                  AND ec.is_fixed = true
                  AND EXTRACT(YEAR  FROM d.entry_date) = %s
                  AND EXTRACT(MONTH FROM d.entry_date) = %s
                  AND d.source NOT IN %s
                """,
                (year, month, _EXCLUDE_SOURCES),
            )
            fixed_costs = float(cur.fetchone()[0] or 0)

            # Revenue: all income for the month, all branches
            cur.execute(
                """
                SELECT COALESCE(SUM(amount), 0)
                FROM public.v_daybook
                WHERE direction = 'income'
                  AND EXTRACT(YEAR  FROM entry_date) = %s
                  AND EXTRACT(MONTH FROM entry_date) = %s
                  AND source NOT IN %s
                """,
                (year, month, _EXCLUDE_SOURCES),
            )
            revenue = float(cur.fetchone()[0] or 0)
    finally:
        conn.close()

    breakeven_configured = fixed_costs > 0
    gap = max(0.0, fixed_costs - revenue) if breakeven_configured else 0.0
    surplus = max(0.0, revenue - fixed_costs) if breakeven_configured else 0.0
    progress_pct = round(revenue / fixed_costs * 100, 1) if breakeven_configured else 0.0
    daily_target = round(gap / days_remaining) if (gap > 0 and days_remaining > 0) else 0.0

    return {
        "year": year,
        "month": month,
        "month_label": f"{_THAI_MONTHS[month]} {year + 543}",
        "breakeven_configured": breakeven_configured,
        "fixed_costs": round(fixed_costs, 2),
        "revenue": round(revenue, 2),
        "gap": round(gap, 2),
        "surplus": round(surplus, 2),
        "progress_pct": progress_pct,
        "days_in_month": days_in_month,
        "days_elapsed": days_elapsed,
        "days_remaining": days_remaining,
        "daily_target_needed": daily_target,
        "is_covered": revenue >= fixed_costs if breakeven_configured else False,
    }


def gen_breakeven_ai_message(ctx: dict) -> str:
    """
    Generate Thai AI analysis + motivational message for breakeven status.
    Uses GPT-4o-mini. Falls back to a static message on API failure.
    """
    if ctx["is_covered"]:
        situation = (
            f"ยอดขาย {ctx['revenue']:,.0f} บาท ครอบคลุมต้นทุนประจำ {ctx['fixed_costs']:,.0f} บาทแล้ว "
            f"เกินไป {ctx['surplus']:,.0f} บาท ({ctx['progress_pct']:.0f}%) "
            f"เหลือเวลาอีก {ctx['days_remaining']} วัน"
        )
    else:
        situation = (
            f"ยอดขายสะสม {ctx['revenue']:,.0f} บาท ยังขาด {ctx['gap']:,.0f} บาท "
            f"จากเป้า {ctx['fixed_costs']:,.0f} บาท ({ctx['progress_pct']:.0f}% ของเป้า) "
            f"เหลือ {ctx['days_remaining']} วัน ต้องทำวันละ {ctx['daily_target_needed']:,.0f} บาท"
        )

    try:
        resp = openai_chat(
            "breakeven_analysis",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "คุณเป็น CFO ที่เป็นกันเองของธุรกิจร้านอาหาร "
                        "วิเคราะห์ตัวเลขตรงๆ ให้คำแนะนำเชิงปฏิบัติ 1-2 ข้อ "
                        "และปิดด้วยข้อความกระตุ้นทีม ใช้ภาษาเป็นกันเอง ไม่เกิน 120 คำ"
                    ),
                },
                {"role": "user", "content": f"สถานะเดือนนี้: {situation}"},
            ],
            model="gpt-4o-mini",
            max_tokens=250,
            temperature=0.7,
        )
        return resp.choices[0].message.content.strip()
    except Exception:
        log.exception("breakeven AI message failed — using fallback")
        if ctx["is_covered"]:
            return f"ยอดดีมากครับ! ผ่าน break-even {ctx['progress_pct']:.0f}% สู้ต่ออีก {ctx['days_remaining']} วัน!"
        return (
            f"สู้ๆ ครับ! เหลือ {ctx['days_remaining']} วัน "
            f"ต้องทำวันละ {ctx['daily_target_needed']:,.0f} บาท โฟกัสได้เลย!"
        )


def build_breakeven_line_message(ctx: dict, ai_msg: str) -> str:
    """Format breakeven status as a LINE push message."""
    sep = "─" * 28

    if ctx["is_covered"]:
        status_line = f"✅ เกิน:          ฿{ctx['surplus']:,.0f} ({ctx['progress_pct']:.0f}%)"
    else:
        status_line = f"📉 ขาดอีก:       ฿{ctx['gap']:,.0f} ({ctx['progress_pct']:.0f}%)"

    lines = [
        f"📊 ยอดขาย vs ต้นทุน — {ctx['month_label']}",
        sep,
        f"💰 ยอดสะสม:        ฿{ctx['revenue']:,.0f}",
        f"🎯 เป้า break-even: ฿{ctx['fixed_costs']:,.0f}",
        status_line,
        f"📅 เหลือ:           {ctx['days_remaining']} วัน",
    ]

    if not ctx["is_covered"] and ctx["days_remaining"] > 0:
        lines.append(f"💡 ต้องทำ/วัน:     ฿{ctx['daily_target_needed']:,.0f}")

    lines += ["", "🤖 AI วิเคราะห์:", ai_msg]
    return "\n".join(lines)


# ─── API endpoint ──────────────────────────────────────────────────────────────

@router.get("/current")
def get_breakeven_current(
    year:  Optional[int] = Query(None, ge=2020, le=2100),
    month: Optional[int] = Query(None, ge=1,    le=12),
):
    """
    Real-time breakeven status for the given month (defaults to current Bangkok month).
    Returns JSON — same data that the Wednesday LINE notification uses.
    """
    now = _now_bkk()
    y = year  if year  is not None else now.year
    m = month if month is not None else now.month
    try:
        return calc_breakeven_status(y, m)
    except Exception as e:
        log.exception("GET /breakeven/current failed year=%s month=%s", y, m)
        raise HTTPException(500, f"Breakeven calculation failed: {e}")
