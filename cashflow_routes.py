"""
cashflow_routes.py — Phase 23: Cash Flow Forecast
===================================================
Endpoints:
  GET /cashflow/forecast?days=30   — 30-day rolling cash flow forecast
  GET /cashflow/summary            — current cash position snapshot

Logic:
  - Actual income/expense: rolling 30-day average from v_daybook
  - Known outflows: AP bills due within `days` window (ar_ap_entries)
  - Projected daily cash: average_daily_income - average_daily_expense
  - Days with known AP bills: subtract the bill amount as a spike
"""

import os
from datetime import date, timedelta
from typing import Optional

import psycopg2
from fastapi import APIRouter, Query

router = APIRouter(prefix="/cashflow", tags=["cashflow"])


def get_db_conn():
    return psycopg2.connect(os.environ["DATABASE_URL"])


def _rows_to_dicts(cur) -> list[dict]:
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


# ─────────────────────────────────────────────────────────────
# GET /cashflow/forecast?days=30
# ─────────────────────────────────────────────────────────────

@router.get("/forecast")
def cashflow_forecast(days: int = Query(30, ge=7, le=90, description="Forecast window in days")):
    """
    30-day cash flow forecast.

    Returns a day-by-day projection:
      - projected_income:  rolling average daily income (last 30 actual days)
      - projected_expense: rolling average daily expense + known AP bills on their due date
      - net:               income - expense for the day
      - cumulative_net:    running total from today

    Also returns `ap_due_entries`: the specific AP bills used in the forecast.
    """
    today = date.today()
    lookback_start = today - timedelta(days=30)

    conn = get_db_conn()
    try:
        cur = conn.cursor()

        # ── 1. Rolling average from last 30 days ──
        cur.execute("""
            SELECT
                COALESCE(SUM(CASE WHEN direction='income'  THEN amount ELSE 0 END), 0) AS inc,
                COALESCE(SUM(CASE WHEN direction='expense' THEN amount ELSE 0 END), 0) AS exp,
                COUNT(DISTINCT entry_date) AS active_days
            FROM public.v_daybook
            WHERE entry_date BETWEEN %s AND %s
              AND source NOT IN ('owner_capital','owner_advance','transfer_error')
        """, (lookback_start.isoformat(), (today - timedelta(days=1)).isoformat()))
        row = cur.fetchone()
        total_inc = float(row[0] or 0)
        total_exp = float(row[1] or 0)
        active_days = int(row[2] or 1) or 1  # avoid div/0

        avg_daily_income  = total_inc / active_days
        avg_daily_expense = total_exp / active_days

        # ── 2. Actual daily totals for reference (last 30 days) ──
        cur.execute("""
            SELECT entry_date,
                   COALESCE(SUM(CASE WHEN direction='income'  THEN amount ELSE 0 END), 0) AS inc,
                   COALESCE(SUM(CASE WHEN direction='expense' THEN amount ELSE 0 END), 0) AS exp
            FROM public.v_daybook
            WHERE entry_date BETWEEN %s AND %s
              AND source NOT IN ('owner_capital','owner_advance','transfer_error')
            GROUP BY entry_date
            ORDER BY entry_date
        """, (lookback_start.isoformat(), (today - timedelta(days=1)).isoformat()))
        actual_rows = {str(r[0]): {"income": float(r[1]), "expense": float(r[2])}
                       for r in cur.fetchall()}

        # ── 3. AP bills due within forecast window ──
        forecast_end = today + timedelta(days=days)
        cur.execute("""
            SELECT
                e.due_date,
                COALESCE(c.name, e.counterparty_name_snapshot, 'ไม่ระบุ') AS vendor,
                (e.amount_total - e.amount_paid) AS remaining
            FROM public.ar_ap_entries e
            LEFT JOIN public.counterparties c ON c.id = e.counterparty_id
            WHERE e.direction = 'payable'
              AND e.status IN ('pending', 'partial')
              AND e.due_date BETWEEN %s AND %s
            ORDER BY e.due_date
        """, (today.isoformat(), forecast_end.isoformat()))
        ap_rows = cur.fetchall()

        # Build dict: due_date → list of AP bills
        ap_by_date: dict[str, list[dict]] = {}
        for due_date, vendor, remaining in ap_rows:
            key = str(due_date)
            if key not in ap_by_date:
                ap_by_date[key] = []
            ap_by_date[key].append({"vendor": vendor, "amount": round(float(remaining or 0), 2)})

        # ── 4. Build forecast days ──
        forecast = []
        cumulative = 0.0

        for i in range(days):
            d = today + timedelta(days=i)
            d_str = str(d)
            is_weekend = d.weekday() >= 5  # Sat=5, Sun=6

            # Income: slightly lower on weekends (rough heuristic ~80%)
            proj_income = avg_daily_income * (0.8 if is_weekend else 1.0)

            # Expense: base average + known AP bills
            ap_today = ap_by_date.get(d_str, [])
            ap_spike = sum(b["amount"] for b in ap_today)
            proj_expense = avg_daily_expense + ap_spike

            net = proj_income - proj_expense
            cumulative += net

            forecast.append({
                "date":              d_str,
                "weekday":           d.strftime("%a"),
                "projected_income":  round(proj_income, 2),
                "projected_expense": round(proj_expense, 2),
                "ap_bills":          ap_today,
                "net":               round(net, 2),
                "cumulative_net":    round(cumulative, 2),
                "is_warning":        cumulative < 0,
            })

        # ── 5. Summary ──
        negative_days = [f for f in forecast if f["cumulative_net"] < 0]
        first_negative = negative_days[0]["date"] if negative_days else None
        total_ap_due = sum(
            b["amount"]
            for bills in ap_by_date.values()
            for b in bills
        )

        return {
            "forecast_days":       days,
            "date_from":           str(today),
            "date_to":             str(forecast_end),
            "avg_daily_income":    round(avg_daily_income, 2),
            "avg_daily_expense":   round(avg_daily_expense, 2),
            "lookback_days":       30,
            "total_ap_due":        round(total_ap_due, 2),
            "ap_bill_count":       len(ap_rows),
            "first_negative_date": first_negative,
            "forecast":            forecast,
            "actual_last_30":      actual_rows,
        }
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────
# GET /cashflow/summary
# ─────────────────────────────────────────────────────────────

@router.get("/summary")
def cashflow_summary():
    """Quick cash position: this month income/expense + pending AP."""
    today = date.today()
    month_start = date(today.year, today.month, 1)

    conn = get_db_conn()
    try:
        cur = conn.cursor()

        cur.execute("""
            SELECT
                COALESCE(SUM(CASE WHEN direction='income'  THEN amount ELSE 0 END), 0) AS inc,
                COALESCE(SUM(CASE WHEN direction='expense' THEN amount ELSE 0 END), 0) AS exp
            FROM public.v_daybook
            WHERE entry_date BETWEEN %s AND %s
              AND source NOT IN ('owner_capital','owner_advance','transfer_error')
        """, (month_start.isoformat(), today.isoformat()))
        row = cur.fetchone()
        mtd_income  = float(row[0] or 0)
        mtd_expense = float(row[1] or 0)

        cur.execute("""
            SELECT COALESCE(SUM(amount_total - amount_paid), 0), COUNT(*)
            FROM public.ar_ap_entries
            WHERE direction='payable' AND status IN ('pending','partial')
        """)
        ap_row = cur.fetchone()
        ap_outstanding = float(ap_row[0] or 0)
        ap_count       = int(ap_row[1] or 0)

        # AP due in next 7 days
        cur.execute("""
            SELECT COALESCE(SUM(amount_total - amount_paid), 0), COUNT(*)
            FROM public.ar_ap_entries
            WHERE direction='payable' AND status IN ('pending','partial')
              AND due_date BETWEEN %s AND %s
        """, (today.isoformat(), (today + timedelta(days=7)).isoformat()))
        due_row = cur.fetchone()
        ap_due_7d       = float(due_row[0] or 0)
        ap_due_7d_count = int(due_row[1] or 0)

        net_position = mtd_income - mtd_expense - ap_outstanding

        return {
            "as_of":            str(today),
            "mtd_income":       round(mtd_income, 2),
            "mtd_expense":      round(mtd_expense, 2),
            "mtd_net":          round(mtd_income - mtd_expense, 2),
            "ap_outstanding":   round(ap_outstanding, 2),
            "ap_count":         ap_count,
            "ap_due_next_7d":   round(ap_due_7d, 2),
            "ap_due_7d_count":  ap_due_7d_count,
            "net_cash_position": round(net_position, 2),
            "health":           "good" if net_position > 0 else "warning",
        }
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────
# Health
# ─────────────────────────────────────────────────────────────

@router.get("/health")
def cashflow_health():
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM public.v_daybook")
            rows = cur.fetchone()[0]
        return {"db": "ok", "v_daybook_rows": rows}
    finally:
        conn.close()
