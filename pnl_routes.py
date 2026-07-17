"""
VEXONHQ — P&L Router  (v6.0.0)
================================
Endpoints:
    GET /pnl/daily?from=YYYY-MM-DD&to=YYYY-MM-DD&branch_code=...
    GET /pnl/monthly?year=YYYY&branch_code=...
    GET /pnl/by-category?month=YYYY-MM&branch_code=...

All endpoints query v_daybook (7 UNION branches) so they automatically
include POS sales, vendor bills, cashflow, rider income/GP, AR/AP, manual.
"""

from __future__ import annotations

import calendar
import logging
import os
from datetime import date
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

try:
    from main import get_db_conn  # type: ignore
except ImportError:
    import psycopg2
    def get_db_conn():
        return psycopg2.connect(os.environ["DATABASE_URL"])

logger = logging.getLogger("pnl")
router = APIRouter(prefix="/pnl", tags=["pnl"])


# ─── helpers ──────────────────────────────────────────────────────────────────

def _rows_to_dicts(cur) -> list[dict]:
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def _parse_date(s: str, field: str = "date") -> date:
    try:
        return date.fromisoformat(s)
    except ValueError:
        raise HTTPException(400, f"Invalid {field}: '{s}'. Use YYYY-MM-DD.")


def _month_range(year: int, mon: int) -> tuple[date, date]:
    last = calendar.monthrange(year, mon)[1]
    return date(year, mon, 1), date(year, mon, last)


def _prev_month(year: int, mon: int) -> tuple[int, int]:
    return (year - 1, 12) if mon == 1 else (year, mon - 1)


def _margin(profit: float, sales: float) -> float:
    return round(profit / sales * 100, 1) if sales else 0.0


# ─── GET /pnl/daily ───────────────────────────────────────────────────────────

@router.get("/daily")
def pnl_daily(
    from_: str = Query(..., alias="from", description="YYYY-MM-DD"),
    to:    str = Query(...,              description="YYYY-MM-DD"),
    branch_code: str = Query("thawi_watthana"),
):
    """
    Daily revenue vs expense for a date range.
    Revenue  = all direction='income' rows in v_daybook.
    Expense  = all direction='expense' rows.
    Profit   = revenue - expense.
    """
    from_date = _parse_date(from_, "from")
    to_date   = _parse_date(to,    "to")

    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                    d.entry_date                                               AS sales_date,
                    COALESCE(SUM(CASE WHEN d.direction='income'
                                      THEN d.amount ELSE 0 END), 0)           AS sales_net,
                    COALESCE(SUM(CASE WHEN d.direction='expense'
                                      THEN d.amount ELSE 0 END), 0)           AS expense_total,
                    COALESCE(MAX(CASE WHEN d.source='pos_sale'
                                      THEN s.bill_count ELSE NULL END), 0)    AS sales_bill_count
                FROM public.v_daybook d
                LEFT JOIN public.pos_sales_daily s
                  ON s.sales_date = d.entry_date
                 AND s.branch_code = d.branch_code
                WHERE d.branch_code = %s
                  AND d.entry_date BETWEEN %s AND %s
                  AND d.source NOT IN ('owner_capital', 'owner_advance', 'transfer_error',
                            'bank_statement', 'vendor_payment',
                            'grab_payout', 'lineman_payout', 'payment_gateway_payout',
                            'pos_cash_deposit', 'cash_withdrawal',
                            'loan_in', 'loan_repayment')
                GROUP BY d.entry_date
                ORDER BY d.entry_date
            """, (branch_code, from_date, to_date))
            raw = _rows_to_dicts(cur)
    finally:
        conn.close()

    rows = []
    for r in raw:
        sn  = float(r["sales_net"]   or 0)
        exp = float(r["expense_total"] or 0)
        gp  = sn - exp
        rows.append({
            "sales_date":       str(r["sales_date"]),
            "sales_net":        round(sn,  2),
            "sales_bill_count": int(r["sales_bill_count"] or 0),
            "expense_total":    round(exp, 2),
            "gross_profit":     round(gp,  2),
            "gross_margin_pct": _margin(gp, sn),
        })

    ts = sum(r["sales_net"]     for r in rows)
    te = sum(r["expense_total"] for r in rows)
    tp = ts - te
    return {
        "from":  str(from_date),
        "to":    str(to_date),
        "rows":  rows,
        "totals": {
            "sales_net":        round(ts, 2),
            "sales_bill_count": sum(r["sales_bill_count"] for r in rows),
            "expense_total":    round(te, 2),
            "gross_profit":     round(tp, 2),
            "gross_margin_pct": _margin(tp, ts),
        },
    }


# ─── GET /pnl/monthly ─────────────────────────────────────────────────────────

@router.get("/monthly")
def pnl_monthly(
    year: int = Query(..., ge=2020, le=2100),
    branch_code: str = Query("thawi_watthana"),
):
    """
    Monthly P&L aggregates for a full calendar year.
    Each row = one month (YYYY-MM).
    """
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                WITH pnl_by_month AS (
                    SELECT
                        TO_CHAR(d.entry_date, 'YYYY-MM')                        AS month,
                        COALESCE(SUM(CASE WHEN d.direction='income'
                                          THEN d.amount ELSE 0 END), 0)         AS sales_net,
                        COALESCE(SUM(CASE WHEN d.direction='expense'
                                          THEN d.amount ELSE 0 END), 0)         AS expense_total,
                        COUNT(DISTINCT CASE WHEN d.direction='expense'
                                            THEN d.ref_id END)                  AS bill_count_expense
                    FROM public.v_daybook d
                    WHERE d.branch_code = %s
                      AND EXTRACT(YEAR FROM d.entry_date) = %s
                      AND d.source NOT IN ('owner_capital', 'owner_advance', 'transfer_error',
                                'bank_statement', 'vendor_payment',
                                'grab_payout', 'lineman_payout', 'payment_gateway_payout',
                                'pos_cash_deposit', 'cash_withdrawal',
                                'loan_in', 'loan_repayment')
                    GROUP BY TO_CHAR(d.entry_date, 'YYYY-MM')
                ), sales_bills_by_month AS (
                    SELECT
                        TO_CHAR(sales_date, 'YYYY-MM') AS month,
                        COALESCE(SUM(bill_count), 0)::int AS bill_count_sales
                    FROM public.pos_sales_daily
                    WHERE branch_code = %s
                      AND EXTRACT(YEAR FROM sales_date) = %s
                    GROUP BY TO_CHAR(sales_date, 'YYYY-MM')
                )
                SELECT
                    p.month,
                    p.sales_net,
                    p.expense_total,
                    COALESCE(s.bill_count_sales, 0) AS bill_count_sales,
                    p.bill_count_expense
                FROM pnl_by_month p
                LEFT JOIN sales_bills_by_month s USING (month)
                ORDER BY p.month
            """, (branch_code, year, branch_code, year))
            raw = _rows_to_dicts(cur)
    finally:
        conn.close()

    rows = []
    for r in raw:
        sn  = float(r["sales_net"]     or 0)
        exp = float(r["expense_total"] or 0)
        gp  = sn - exp
        rows.append({
            "month":             r["month"],
            "sales_net":         round(sn,  2),
            "expense_total":     round(exp, 2),
            "gross_profit":      round(gp,  2),
            "gross_margin_pct":  _margin(gp, sn),
            "bill_count_sales":  int(r["bill_count_sales"]   or 0),
            "bill_count_expense": int(r["bill_count_expense"] or 0),
        })

    ts = sum(r["sales_net"]     for r in rows)
    te = sum(r["expense_total"] for r in rows)
    tp = ts - te
    return {
        "year": year,
        "rows": rows,
        "totals": {
            "sales_net":         round(ts, 2),
            "expense_total":     round(te, 2),
            "gross_profit":      round(tp, 2),
            "gross_margin_pct":  _margin(tp, ts),
            "bill_count_sales":  sum(r["bill_count_sales"]   for r in rows),
            "bill_count_expense": sum(r["bill_count_expense"] for r in rows),
        },
    }


# ─── GET /pnl/by-category ─────────────────────────────────────────────────────

@router.get("/by-category")
def pnl_by_category(
    month: str = Query(..., description="YYYY-MM"),
    branch_code: str = Query("thawi_watthana"),
):
    """
    Expense breakdown by category for a given month,
    with % of sales, % of total expense, and delta vs previous month.
    """
    try:
        year, mon = map(int, month.split("-"))
        if not (1 <= mon <= 12):
            raise ValueError
    except ValueError:
        raise HTTPException(400, "Invalid month format. Use YYYY-MM.")

    from_date, to_date   = _month_range(year, mon)
    py, pm               = _prev_month(year, mon)
    prev_from, prev_to   = _month_range(py, pm)

    conn = get_db_conn()
    try:
        with conn.cursor() as cur:

            # Total income for the month (denominator for pct_of_sales)
            cur.execute("""
                SELECT COALESCE(SUM(amount), 0)
                FROM public.v_daybook
                WHERE direction = 'income'
                  AND branch_code = %s
                  AND entry_date BETWEEN %s AND %s
                  AND source NOT IN ('owner_capital', 'owner_advance', 'transfer_error',
                            'bank_statement', 'vendor_payment',
                            'grab_payout', 'lineman_payout', 'payment_gateway_payout',
                            'pos_cash_deposit', 'cash_withdrawal',
                            'loan_in', 'loan_repayment')
            """, (branch_code, from_date, to_date))
            sales_net = float(cur.fetchone()[0] or 0)

            # Current month — expense by category
            cur.execute("""
                SELECT
                    COALESCE(d.category_code, '__uncategorized') AS category_code,
                    COALESCE(ec.name_th, 'ไม่ระบุหมวด')          AS name_th,
                    SUM(d.amount)                                  AS expense
                FROM public.v_daybook d
                LEFT JOIN public.expense_categories ec
                  ON ec.code = d.category_code
                WHERE d.direction = 'expense'
                  AND d.branch_code = %s
                  AND d.entry_date BETWEEN %s AND %s
                  AND d.source NOT IN ('owner_capital', 'owner_advance', 'transfer_error',
                            'bank_statement', 'vendor_payment',
                            'grab_payout', 'lineman_payout', 'payment_gateway_payout',
                            'pos_cash_deposit', 'cash_withdrawal',
                            'loan_in', 'loan_repayment')
                GROUP BY d.category_code, ec.name_th
                ORDER BY expense DESC
            """, (branch_code, from_date, to_date))
            curr_rows = _rows_to_dicts(cur)

            # Previous month — expense totals per category (for vs_prev_month)
            cur.execute("""
                SELECT
                    COALESCE(category_code, '__uncategorized') AS category_code,
                    SUM(amount)                                 AS expense
                FROM public.v_daybook
                WHERE direction = 'expense'
                  AND branch_code = %s
                  AND entry_date BETWEEN %s AND %s
                  AND source NOT IN ('owner_capital', 'owner_advance', 'transfer_error',
                            'bank_statement', 'vendor_payment',
                            'grab_payout', 'lineman_payout', 'payment_gateway_payout',
                            'pos_cash_deposit', 'cash_withdrawal',
                            'loan_in', 'loan_repayment')
                GROUP BY category_code
            """, (branch_code, prev_from, prev_to))
            prev_map = {
                r["category_code"]: float(r["expense"] or 0)
                for r in _rows_to_dicts(cur)
            }
    finally:
        conn.close()

    total_expense  = sum(float(r["expense"] or 0) for r in curr_rows)
    uncategorized  = 0.0
    categories     = []

    for r in curr_rows:
        exp      = float(r["expense"] or 0)
        cat_code = r["category_code"]
        if cat_code == "__uncategorized":
            uncategorized = exp
            continue
        prev_exp = prev_map.get(cat_code, 0.0)
        categories.append({
            "category_code":  cat_code,
            "name_th":        r["name_th"],
            "expense":        round(exp, 2),
            "pct_of_sales":   round(exp / sales_net    * 100, 1) if sales_net    else 0.0,
            "pct_of_expense": round(exp / total_expense * 100, 1) if total_expense else 0.0,
            "vs_prev_month":  round(exp - prev_exp, 2),
        })

    return {
        "month":                month,
        "sales_net":            round(sales_net,     2),
        "categories":           categories,
        "uncategorized_expense": round(uncategorized, 2),
    }
