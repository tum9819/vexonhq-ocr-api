import logging
import os
import re
from datetime import date, timedelta
from typing import Iterable

import psycopg2
from fastapi import APIRouter, HTTPException, Query

log = logging.getLogger("vexonhq-reconcile")

router = APIRouter(prefix="/reconcile", tags=["reconcile"])

PLATFORM_TO_SOURCE_TYPE = {
    "grab": "grab_payout",
    "lineman": "lineman_payout",
}
PLATFORM_LABELS = {
    "grab": "Grab",
    "lineman": "LINE MAN",
}
ESTIMATED_PLATFORMS = {"lineman"}
DIFF_WARN_PCT = 2.0
DEFAULT_BRANCH = "thawi_watthana"


def get_db_conn():
    return psycopg2.connect(os.environ["DATABASE_URL"])


def _parse_month(month: str) -> date:
    if not re.fullmatch(r"\d{4}-\d{2}", month or ""):
        raise HTTPException(400, "month must be YYYY-MM")
    year, month_no = [int(part) for part in month.split("-")]
    try:
        return date(year, month_no, 1)
    except ValueError:
        raise HTTPException(400, "month must be a valid YYYY-MM")


def _next_month(month_start: date) -> date:
    if month_start.month == 12:
        return date(month_start.year + 1, 1, 1)
    return date(month_start.year, month_start.month + 1, 1)


def _money(value) -> float:
    return round(float(value or 0), 2)


def _pct(diff: float, system_payout: float) -> float:
    return round((diff / system_payout) * 100, 2)


def reconcile_platform_payout_rows(
    system_rows: Iterable[tuple],
    bank_rows: Iterable[tuple],
) -> dict:
    system_by_platform = {
        str(platform).lower(): _money(total)
        for platform, total in system_rows
    }
    bank_by_platform = {
        platform: _money(total)
        for source_type, total in bank_rows
        for platform, mapped_source in PLATFORM_TO_SOURCE_TYPE.items()
        if source_type == mapped_source
    }

    result = {}
    for platform in PLATFORM_TO_SOURCE_TYPE:
        system_has_data = platform in system_by_platform
        bank_has_data = platform in bank_by_platform
        system_payout = system_by_platform.get(platform, 0.0)
        bank_payout = bank_by_platform.get(platform, 0.0)
        estimated = platform in ESTIMATED_PLATFORMS

        if not system_has_data and not bank_has_data:
            diff = 0.0
            diff_pct = 0.0
            status = "no_data"
            warning = False
        elif system_has_data and not bank_has_data:
            diff = None
            diff_pct = None
            status = "no_bank_data"
            warning = False
        elif system_payout == 0:
            diff = round(bank_payout - system_payout, 2)
            diff_pct = None
            status = "bank_only"
            warning = False
        else:
            diff = round(bank_payout - system_payout, 2)
            diff_pct = _pct(diff, system_payout)
            warning = abs(diff_pct) > DIFF_WARN_PCT
            status = "diff_over_threshold" if warning else "ok"

        result[platform] = {
            "platform": platform,
            "system_payout": system_payout,
            "bank_payout": bank_payout,
            "diff": diff,
            "diff_pct": diff_pct,
            "estimated": estimated,
            "status": status,
            "warning": warning,
        }

    return result


def fetch_platform_payout_reconciliation(
    month: str,
    lag_days: int = 7,
    branch_code: str = DEFAULT_BRANCH,
) -> dict:
    month_start = _parse_month(month)
    sales_end = _next_month(month_start)
    bank_end = sales_end + timedelta(days=lag_days)

    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT LOWER(platform) AS platform,
                       COALESCE(SUM(net_payout), 0) AS system_payout
                FROM public.rider_deliveries
                WHERE delivery_date >= %s
                  AND delivery_date < %s
                  AND branch_code = %s
                  AND LOWER(platform) IN ('grab', 'lineman')
                GROUP BY LOWER(platform)
                ORDER BY LOWER(platform)
                """,
                (month_start, sales_end, branch_code),
            )
            system_rows = cur.fetchall()

            cur.execute(
                """
                SELECT source_type,
                       COALESCE(SUM(credit), 0) AS bank_payout
                FROM public.bank_statement_entries
                WHERE txn_date >= %s
                  AND txn_date < %s
                  AND branch_code = %s
                  AND source_type IN ('grab_payout', 'lineman_payout')
                  AND credit > 0
                GROUP BY source_type
                ORDER BY source_type
                """,
                (month_start, bank_end, branch_code),
            )
            bank_rows = cur.fetchall()
    finally:
        conn.close()

    return {
        "month": month,
        "lag_days": lag_days,
        "branch_code": branch_code,
        "sales_window": {
            "from": month_start.isoformat(),
            "to": sales_end.isoformat(),
        },
        "bank_window": {
            "from": month_start.isoformat(),
            "to": bank_end.isoformat(),
        },
        "platforms": reconcile_platform_payout_rows(system_rows, bank_rows),
    }


def build_platform_payout_digest_lines(
    month: str,
    lag_days: int = 7,
    branch_code: str = DEFAULT_BRANCH,
) -> list[str]:
    data = fetch_platform_payout_reconciliation(month, lag_days, branch_code)
    lines = ["Platform payout reconcile"]
    for platform in ("grab", "lineman"):
        row = data["platforms"][platform]
        label = PLATFORM_LABELS[platform]
        if row["status"] == "no_data":
            lines.append(f"{label}: ไม่มีข้อมูล")
        elif row["status"] == "no_bank_data":
            lines.append(f"{label}: ยังไม่มีเงินเข้า bank")
        elif row["diff_pct"] is None:
            lines.append(f"{label}: เทียบไม่ได้")
        else:
            mark = " !" if row["warning"] else ""
            lines.append(f"{label}: diff {row['diff_pct']:+.1f}%{mark}")
    return lines


@router.get("/platform-payout")
def platform_payout_reconciliation(
    month: str = Query(..., description="YYYY-MM"),
    lag_days: int = Query(7, ge=0, le=31),
    branch_code: str = Query(DEFAULT_BRANCH),
):
    return fetch_platform_payout_reconciliation(month, lag_days, branch_code)
