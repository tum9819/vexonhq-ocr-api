"""Bangkok-timezone date helpers.

The Coolify container runs on UTC (audit B8-M3, 2026-05-29), so a bare
``date.today()`` between 00:00-07:00 Bangkok time returns YESTERDAY's Thai
business date. Any route that buckets money or reports by business day must
use these helpers instead of ``date.today()`` / naive ``datetime.now()``.

(line_bot_routes.py keeps its own private ``_bkk_today()`` from the original
B8-M3 fix; this module is the shared version for every other router.)
"""

from datetime import date, datetime
from zoneinfo import ZoneInfo

BKK = ZoneInfo("Asia/Bangkok")


def bkk_now() -> datetime:
    """Current time in Asia/Bangkok (tz-aware)."""
    return datetime.now(BKK)


def bkk_today() -> date:
    """Today's date in Asia/Bangkok — the shop's business date."""
    return datetime.now(BKK).date()
