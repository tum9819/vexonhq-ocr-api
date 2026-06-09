"""
pos_freshness.py — pure POS sales-freshness signal (Reliability Phase, 2026-06-09)
=================================================================================
DB-free decision logic so it unit-tests with NO database or API key. The IO
wrapper (`_scheduled_pos_freshness_check` in line_bot_routes.py) does the SQL
(`SELECT max(sales_date) FROM pos_bills`) + the best-effort Discord post; this
module only decides stale-or-not and builds the Thai alert message.

Mirrors the drift_monitor.py split (pure `evaluate_drift` vs IO `run_drift_check`)
so the watcher's behavior is testable without standing up the scheduler.
"""

from __future__ import annotations

from datetime import date
from typing import Optional, Tuple

# CE year on purpose (matches the plan's "31 พ.ค. 2026" example) so a stale
# window that straddles a year boundary is unambiguous.
_THAI_MONTHS = ["", "ม.ค.", "ก.พ.", "มี.ค.", "เม.ย.", "พ.ค.", "มิ.ย.",
                "ก.ค.", "ส.ค.", "ก.ย.", "ต.ค.", "พ.ย.", "ธ.ค."]


def _thai_date(d: date) -> str:
    return f"{d.day} {_THAI_MONTHS[d.month]} {d.year}"


def pos_freshness_signal(
    latest_sales_date: Optional[date],
    today: date,
    threshold_days: int = 2,
) -> Tuple[bool, Optional[int], Optional[str]]:
    """Decide whether POS sales data is stale.

    Returns (stale, days_behind, message):
      - latest_sales_date is None (no POS bills at all) -> (False, None, None):
        never alert (nothing imported yet is not a staleness signal).
      - stale when (today - latest_sales_date).days > threshold_days; `message`
        is the Discord-ready Thai nudge.
      - fresh -> (False, days_behind, None): caller stays silent (silence = healthy).
    """
    if latest_sales_date is None:
        return False, None, None

    days_behind = (today - latest_sales_date).days
    if days_behind > threshold_days:
        message = (
            "⚠️ POS ยอดขายไม่อัปเดต\n"
            f"ข้อมูลยอดขายล่าสุดถึง {_thai_date(latest_sales_date)} — ค้าง {days_behind} วัน\n"
            "กรุณา import POS เพื่อให้ Dashboard/รายงานยอดขายตรงกับความจริง"
        )
        return True, days_behind, message
    return False, days_behind, None
