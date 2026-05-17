"""
VEXONHQ Phase 2 — Backend Routes (Dashboard + Receipts + Budgets)
==================================================================
Companion module to pos_import.py — adds the endpoints that the
Codex-generated frontend pages call:

    /receipts/*       — Receipt History page
    /dashboard/*      — Dashboard page
    /budgets/*        — Budgets page

Drop into vexonhq-ocr-api repo next to main.py + pos_import.py, add:
    from phase2_routes import router as phase2_router
    app.include_router(phase2_router)

Endpoints:
    GET    /receipts/search        — filter/paginate v_receipt_history
    GET    /receipts/categories    — expense_categories for dropdown
    GET    /receipts/{id}          — single bill + items + attachments
    GET    /dashboard/overview     — month KPIs + trend + budgets
    GET    /budgets/status         — current month per-category
    GET    /budgets/categories     — same as /receipts/categories
    PUT    /budgets                — upsert one budget row
    DELETE /budgets/{id}           — remove a budget

Dependencies: psycopg2-binary (already in requirements.txt)
"""

from __future__ import annotations

import io
import logging
import urllib.request
import zipfile
from datetime import date, datetime, timedelta
from typing import Any, Optional
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill

# Reuse main.get_db_conn (same pattern as pos_import.py)
try:
    from main import get_db_conn  # type: ignore
except ImportError:
    # Fallback for standalone testing or circular-import situations
    import os
    import psycopg2
    def get_db_conn():
        return psycopg2.connect(os.environ["DATABASE_URL"])


logger = logging.getLogger("phase2_routes")
router = APIRouter(tags=["phase2"])

DEFAULT_BRANCH = "thawi_watthana"


# ============================================================
# Helpers
# ============================================================

def _rows_to_dicts(cur) -> list[dict]:
    """Convert cursor results to list[dict] using column names."""
    if cur.description is None:
        return []
    cols = [d[0] for d in cur.description]
    return [_serialize_row(dict(zip(cols, r))) for r in cur.fetchall()]


def _serialize_row(row: dict) -> dict:
    """Convert UUID/date/datetime/Decimal to JSON-safe types."""
    out = {}
    for k, v in row.items():
        if isinstance(v, UUID):
            out[k] = str(v)
        elif isinstance(v, (datetime, date)):
            out[k] = v.isoformat()
        elif hasattr(v, "__float__") and not isinstance(v, (int, float, bool)):
            out[k] = float(v)
        else:
            out[k] = v
    return out


def _month_start(month: Optional[str]) -> date:
    """Parse 'YYYY-MM' string to date. Defaults to current month if None."""
    if not month:
        today = date.today()
        return today.replace(day=1)
    try:
        return datetime.strptime(month + "-01", "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(400, f"Invalid month format: {month!r} (expected YYYY-MM)")


def _next_month(d: date) -> date:
    return (d.replace(day=28) + timedelta(days=4)).replace(day=1)


def _prev_month(d: date) -> date:
    return (d - timedelta(days=1)).replace(day=1)


# ============================================================
# SECTION A — RECEIPTS
# ============================================================

@router.get("/receipts/categories")
def list_categories():
    """List active expense categories (for filter dropdowns)."""
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT code, name_th, name_en, sort_order
                FROM public.expense_categories
                WHERE is_active = true
                ORDER BY sort_order, code
            """)
            return _rows_to_dicts(cur)
    finally:
        conn.close()


@router.get("/receipts/search")
def search_receipts(
    q: Optional[str] = Query(None, description="vendor_name substring match"),
    from_date: Optional[date] = Query(None, alias="from"),
    to_date: Optional[date] = Query(None, alias="to"),
    category: Optional[str] = Query(None),
    payment: Optional[str] = Query(None),
    min_amount: Optional[float] = Query(None),
    max_amount: Optional[float] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """Paginated search over confirmed receipts (v_receipt_history)."""
    conditions: list[str] = []
    params: list[Any] = []

    if q:
        conditions.append("vendor_name ILIKE %s")
        params.append(f"%{q}%")
    if from_date:
        conditions.append("bill_date >= %s")
        params.append(from_date)
    if to_date:
        conditions.append("bill_date <= %s")
        params.append(to_date)
    if category:
        conditions.append("category_code = %s")
        params.append(category)
    if payment:
        conditions.append("payment_type = %s")
        params.append(payment)
    if min_amount is not None:
        conditions.append("amount >= %s")
        params.append(min_amount)
    if max_amount is not None:
        conditions.append("amount <= %s")
        params.append(max_amount)

    where_sql = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT count(*) FROM public.v_receipt_history {where_sql}",
                params,
            )
            total = cur.fetchone()[0]

            cur.execute(
                f"""
                SELECT id, vendor_name, merchant_tax_id, invoice_no,
                       bill_date, due_date, amount, subtotal, vat, currency,
                       payment_type, payment_status, review_status,
                       category_code, category_name, branch_code,
                       notes, created_at, updated_at, batch_id,
                       preview_url, page_count
                FROM public.v_receipt_history
                {where_sql}
                LIMIT %s OFFSET %s
                """,
                params + [limit, offset],
            )
            results = _rows_to_dicts(cur)
        return {"results": results, "total": total}
    finally:
        conn.close()


@router.get("/receipts/{receipt_id}")
def get_receipt(receipt_id: str):
    """Single receipt detail — header + line items + attachments."""
    try:
        uid = UUID(receipt_id)
    except ValueError:
        raise HTTPException(400, "Invalid receipt id")

    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT * FROM public.v_receipt_history WHERE id = %s""",
                (str(uid),),
            )
            header_rows = _rows_to_dicts(cur)
            if not header_rows:
                raise HTTPException(404, "Receipt not found")
            header = header_rows[0]

            cur.execute(
                """SELECT line_no, sku, product_name, quantity, unit,
                          unit_price, amount, vat_amount, raw_text, source_page
                   FROM public.invoice_items
                   WHERE vendor_bill_id = %s
                   ORDER BY source_page NULLS LAST, line_no NULLS LAST""",
                (str(uid),),
            )
            header["items"] = _rows_to_dicts(cur)

            cur.execute(
                """SELECT file_url, page_no, mime_type, file_name
                   FROM public.attachments
                   WHERE parent_type = 'vendor_bill' AND parent_id = %s
                   ORDER BY page_no NULLS LAST, created_at""",
                (str(uid),),
            )
            header["attachments"] = _rows_to_dicts(cur)

            cur.execute(
                """SELECT severity, code, message, field, resolved
                   FROM public.invoice_validation_warnings
                   WHERE vendor_bill_id = %s
                   ORDER BY created_at""",
                (str(uid),),
            )
            header["warnings"] = _rows_to_dicts(cur)
        return header
    finally:
        conn.close()


# ============================================================
# SECTION B — DASHBOARD
# ============================================================

def _summarize_month(cur, period_month: date, branch_code: str) -> dict:
    """Helper: pull sales+expense totals for one month + branch from v_daybook (all sources).
    Excludes owner equity movements and transfer errors from P&L calculations."""
    pe = _next_month(period_month)
    cur.execute(
        """SELECT
               COALESCE(SUM(CASE WHEN direction = 'income'  THEN amount ELSE 0 END), 0)::numeric AS sales_net,
               COUNT(CASE WHEN direction = 'income'  THEN 1 END)::int                            AS sales_bill_count,
               COALESCE(SUM(CASE WHEN direction = 'expense' THEN amount ELSE 0 END), 0)::numeric AS expense_total,
               COUNT(CASE WHEN direction = 'expense' THEN 1 END)::int                            AS expense_bill_count
           FROM public.v_daybook
           WHERE branch_code = %s
             AND entry_date >= %s AND entry_date < %s
             AND source NOT IN ('owner_capital', 'owner_advance', 'transfer_error')""",
        (branch_code, period_month, pe),
    )
    row = cur.fetchone()
    sales_net          = float(row[0] or 0)
    sales_bill_count   = int(row[1] or 0)
    expense_total      = float(row[2] or 0)
    expense_bill_count = int(row[3] or 0)

    gross_profit = sales_net - expense_total
    # Session 15 fix: return 0.0 instead of None when sales_net = 0
    # — prevents frontend "NaN%" display bug (was: margin_pct = None)
    margin_pct = round(gross_profit / sales_net * 100, 2) if sales_net else 0.0
    return {
        "sales_net": sales_net,
        "sales_bill_count": sales_bill_count,
        "expense_total": expense_total,
        "expense_bill_count": expense_bill_count,
        "gross_profit": gross_profit,
        "gross_margin_pct": margin_pct,
    }


@router.get("/dashboard/overview")
def dashboard_overview(
    month: Optional[str] = Query(None, description="YYYY-MM, default current"),
    branch: str = Query(DEFAULT_BRANCH),
):
    """One-shot dashboard payload: current+prev month, YTD, 6-month trend, top categories, budget alerts."""
    month_start = _month_start(month)
    prev_start = _prev_month(month_start)
    year_start = date(month_start.year, 1, 1)
    ytd_end = _next_month(date(month_start.year, 12, 1))

    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            # ── Current & prev month summaries ──────────────────────────────
            try:
                current = _summarize_month(cur, month_start, branch)
                prev = _summarize_month(cur, prev_start, branch)
            except Exception as e:
                logger.error("dashboard_overview: _summarize_month failed: %s", e)
                raise

            # ── YTD (v_daybook — all sources) ────────────────────────────────
            try:
                cur.execute(
                    """SELECT
                           COALESCE(SUM(CASE WHEN direction='income'  THEN amount ELSE 0 END),0)::numeric,
                           COALESCE(SUM(CASE WHEN direction='expense' THEN amount ELSE 0 END),0)::numeric
                       FROM public.v_daybook
                       WHERE branch_code = %s
                         AND entry_date >= %s AND entry_date < %s
                         AND source NOT IN ('owner_capital', 'owner_advance', 'transfer_error')""",
                    (branch, year_start, ytd_end),
                )
                ytd_row = cur.fetchone()
                ytd_sales   = float(ytd_row[0] or 0)
                ytd_expense = float(ytd_row[1] or 0)
            except Exception as e:
                logger.error("dashboard_overview: YTD query failed: %s", e)
                ytd_sales, ytd_expense = 0.0, 0.0
                conn.rollback()

            # ── 6-month trend ─────────────────────────────────────────────────
            trend = []
            try:
                for i in range(5, -1, -1):
                    m = month_start
                    for _ in range(i):
                        m = _prev_month(m)
                    summ = _summarize_month(cur, m, branch)
                    trend.append({
                        "month": m.strftime("%Y-%m"),
                        "sales_net": summ["sales_net"],
                        "expense_total": summ["expense_total"],
                        "gross_profit": summ["gross_profit"],
                    })
            except Exception as e:
                logger.error("dashboard_overview: trend query failed: %s", e)
                conn.rollback()

            # ── Top categories ────────────────────────────────────────────────
            top_categories = []
            try:
                pe = _next_month(month_start)
                cur.execute(
                    """SELECT vb.category_code,
                              COALESCE(ec.name_th, vb.category_code) AS name_th,
                              SUM(vb.amount)::numeric AS spent
                       FROM public.vendor_bills vb
                       LEFT JOIN public.expense_categories ec ON ec.code = vb.category_code
                       WHERE vb.review_status = 'confirmed'
                         AND vb.bill_date IS NOT NULL
                         AND vb.bill_date >= %s AND vb.bill_date < %s
                         AND vb.category_code IS NOT NULL
                       GROUP BY vb.category_code, ec.name_th
                       ORDER BY spent DESC
                       LIMIT 5""",
                    (month_start, pe),
                )
                top_rows = cur.fetchall()
                total_categorized = sum(float(r[2] or 0) for r in top_rows) or 1.0
                top_categories = [
                    {
                        "category_code": r[0],
                        "name_th": r[1],
                        "spent": float(r[2] or 0),
                        "pct": round(float(r[2] or 0) / total_categorized * 100, 1),
                    }
                    for r in top_rows
                ]
            except Exception as e:
                logger.error("dashboard_overview: top_categories query failed: %s", e)
                conn.rollback()

            # ── Budget alerts ─────────────────────────────────────────────────
            budget_alerts = []
            try:
                cur.execute(
                    """SELECT category_code, category_name_th, budget_amount, actual_amount,
                              pct_used, status
                       FROM public.v_budget_status
                       WHERE month = %s AND branch_code = %s
                         AND status IN ('warning','over')
                       ORDER BY pct_used DESC NULLS LAST""",
                    (month_start.strftime("%Y-%m"), branch),
                )
                budget_alerts = [
                    {
                        "category_code": r[0],
                        "name_th": r[1],
                        "amount_limit": float(r[2] or 0),
                        "spent": float(r[3] or 0),
                        "usage_pct": float(r[4] or 0),
                        "status": r[5],
                        "alert_at_pct": 80,
                    }
                    for r in cur.fetchall()
                ]
            except Exception as e:
                logger.error("dashboard_overview: v_budget_status query failed: %s", e)
                conn.rollback()

            # ── Platform breakdown (income by source) ─────────────────────────
            platform_breakdown = []
            try:
                pe = _next_month(month_start)
                cur.execute(
                    """SELECT source,
                              COALESCE(SUM(amount), 0)::numeric AS total,
                              COUNT(*)::int AS txn_count
                       FROM public.v_daybook
                       WHERE direction = 'income'
                         AND branch_code = %s
                         AND entry_date >= %s AND entry_date < %s
                         AND source IN ('pos_sale','rider_income_grab','rider_income_lineman',
                                        'pos_cashflow','manual','ar_payment')
                       GROUP BY source
                       ORDER BY total DESC""",
                    (branch, month_start, pe),
                )
                _SOURCE_LABEL = {
                    "pos_sale":             "หน้าร้าน (FoodStory)",
                    "rider_income_grab":    "Grab",
                    "rider_income_lineman": "Lineman",
                    "pos_cashflow":         "FoodStory Cashflow",
                    "manual":               "Manual Entry",
                    "ar_payment":           "รับชำระ AR",
                }
                platform_breakdown = [
                    {
                        "source": r[0],
                        "label": _SOURCE_LABEL.get(r[0], r[0]),
                        "total": float(r[1] or 0),
                        "txn_count": int(r[2] or 0),
                    }
                    for r in cur.fetchall()
                ]
            except Exception as e:
                logger.error("dashboard_overview: platform_breakdown query failed: %s", e)

            # ── Food Cost % (วัตถุดิบ / COGS categories) ─────────────────────
            food_cost_amt = 0.0
            food_cost_pct = 0.0
            try:
                pe_fc = _next_month(month_start)
                cur.execute(
                    """SELECT COALESCE(SUM(vb.amount), 0)::numeric
                       FROM public.vendor_bills vb
                       WHERE vb.review_status = 'confirmed'
                         AND COALESCE(vb.branch_code, %s) = %s
                         AND vb.bill_date >= %s AND vb.bill_date < %s
                         AND vb.category_code IN (
                             'food_cost','raw_meat','raw_veggies',
                             'raw_seasoning','raw_oil_gas','raw_beverage'
                         )""",
                    (branch, branch, month_start, pe_fc),
                )
                food_cost_amt = float(cur.fetchone()[0] or 0)
                sales_net_cur = current.get("sales_net", 0)
                if sales_net_cur > 0:
                    food_cost_pct = round(food_cost_amt / sales_net_cur * 100, 1)
            except Exception as e:
                logger.error("dashboard_overview: food_cost_pct failed: %s", e)
                conn.rollback()

        return {
            "month": month_start.strftime("%Y-%m"),
            "branch_code": branch,
            "current": current,
            "prev_month": prev,
            "ytd_2026": {
                "sales_net": ytd_sales,
                "expense_total": ytd_expense,
                "gross_profit": ytd_sales - ytd_expense,
            },
            "trend": trend,
            "top_categories": top_categories,
            "budget_status": budget_alerts,
            "platform_breakdown": platform_breakdown,
            "food_cost": {
                "amount": food_cost_amt,
                "pct": food_cost_pct,
            },
        }
    finally:
        conn.close()


# ============================================================
# SECTION C — BUDGETS
# ============================================================

@router.get("/budgets/categories")
def budgets_categories():
    """Alias of /receipts/categories."""
    return list_categories()


@router.get("/budgets/status")
def budgets_status(
    month: Optional[str] = Query(None),
    branch: str = Query(DEFAULT_BRANCH),
):
    """Per-category budget vs actual for the given month."""
    period_month = _month_start(month)

    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT ec.code              AS category_code,
                          ec.name_th,
                          ec.sort_order,
                          b.id                  AS budget_id,
                          b.amount_limit,
                          b.alert_at_pct,
                          COALESCE(spent.total, 0)::numeric AS spent
                   FROM public.expense_categories ec
                   LEFT JOIN public.budgets b
                          ON b.category_code = ec.code
                         AND b.branch_code   = %s
                         AND b.period_month  = %s
                   LEFT JOIN (
                       SELECT category_code, SUM(amount) AS total
                       FROM public.vendor_bills
                       WHERE review_status = 'confirmed'
                         AND bill_date IS NOT NULL
                         AND bill_date >= %s
                         AND bill_date < %s
                         AND COALESCE(branch_code, %s) = %s
                       GROUP BY category_code
                   ) spent ON spent.category_code = ec.code
                   WHERE ec.is_active = true
                   ORDER BY ec.sort_order, ec.code""",
                (branch, period_month, period_month, _next_month(period_month),
                 branch, branch),
            )
            rows = []
            for r in cur.fetchall():
                amount_limit = float(r[4]) if r[4] is not None else None
                spent = float(r[6] or 0)
                if amount_limit is None:
                    status = "no_budget"
                    usage_pct: Optional[float] = None
                    over_under: Optional[float] = None
                else:
                    usage_pct = round((spent / amount_limit * 100) if amount_limit else 0, 1)
                    over_under = round(spent - amount_limit, 2)
                    if usage_pct >= 100:
                        status = "over"
                    elif usage_pct >= (r[5] or 80):
                        status = "warn"
                    else:
                        status = "ok"
                rows.append({
                    "category_code": r[0],
                    "name_th": r[1],
                    "sort_order": r[2],
                    "budget_id": str(r[3]) if r[3] else None,
                    "amount_limit": amount_limit,
                    "alert_at_pct": r[5] if r[5] is not None else 80,
                    "spent": spent,
                    "usage_pct": usage_pct,
                    "over_under": over_under,
                    "status": status,
                })
        return {
            "month": period_month.strftime("%Y-%m"),
            "branch_code": branch,
            "rows": rows,
        }
    finally:
        conn.close()


class BudgetUpsert(BaseModel):
    branch_code: str = DEFAULT_BRANCH
    category_code: str
    period_month: date           # first day of month
    amount_limit: float
    alert_at_pct: int = 80


@router.put("/budgets")
def upsert_budget(body: BudgetUpsert):
    """Upsert one budget row (insert if absent, update if present)."""
    if body.period_month.day != 1:
        raise HTTPException(400, "period_month must be the first day of the month")
    if body.amount_limit < 0:
        raise HTTPException(400, "amount_limit must be >= 0")
    if not (1 <= body.alert_at_pct <= 100):
        raise HTTPException(400, "alert_at_pct must be 1..100")

    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO public.budgets
                     (branch_code, category_code, period_month,
                      amount_limit, alert_at_pct)
                   VALUES (%s, %s, %s, %s, %s)
                   ON CONFLICT (branch_code, category_code, period_month)
                   DO UPDATE SET
                     amount_limit = EXCLUDED.amount_limit,
                     alert_at_pct = EXCLUDED.alert_at_pct
                   RETURNING id, branch_code, category_code, period_month,
                             amount_limit, alert_at_pct""",
                (body.branch_code, body.category_code, body.period_month,
                 body.amount_limit, body.alert_at_pct),
            )
            conn.commit()
            row = _rows_to_dicts(cur)[0]
        return row
    finally:
        conn.close()


@router.delete("/budgets/{budget_id}")
def delete_budget(budget_id: str):
    """Remove a budget row by id."""
    try:
        uid = UUID(budget_id)
    except ValueError:
        raise HTTPException(400, "Invalid budget id")
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM public.budgets WHERE id = %s",
                (str(uid),),
            )
            conn.commit()
            deleted = cur.rowcount > 0
        if not deleted:
            raise HTTPException(404, "Budget not found")
        return {"deleted": True, "id": str(uid)}
    finally:
        conn.close()


# ============================================================
# Health-style endpoint (helps Coolify confirm DB connectivity)
# ============================================================
@router.get("/phase2/health")
def phase2_health():
    """Quick DB-touch endpoint — useful for connectivity testing."""
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
            ok = cur.fetchone()[0] == 1
            cur.execute("SELECT count(*) FROM public.expense_categories")
            cats = cur.fetchone()[0]
        return {"db": "ok" if ok else "fail", "categories": cats}
    except Exception as e:
        return {"db": "fail", "error": str(e)}
    finally:
        conn.close()


# ============================================================
# SECTION D — P&L (Profit & Loss)
# ============================================================

@router.get("/pnl/daily")
def pnl_daily(
    from_date: date = Query(..., alias="from"),
    to_date: date = Query(..., alias="to"),
    branch: str = Query(DEFAULT_BRANCH),
):
    """Daily P&L for an arbitrary date range (max 366 days)."""
    if (to_date - from_date).days > 366:
        raise HTTPException(400, "Range too large (max 366 days)")
    if to_date < from_date:
        raise HTTPException(400, "to_date must be >= from_date")

    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            # daily sales
            cur.execute(
                """SELECT sales_date, SUM(net_total)::numeric AS sales_net,
                          SUM(bill_count)::int AS bills
                   FROM public.pos_sales_daily
                   WHERE branch_code = %s AND sales_date BETWEEN %s AND %s
                   GROUP BY sales_date
                   ORDER BY sales_date""",
                (branch, from_date, to_date),
            )
            sales_map = {r[0]: (float(r[1] or 0), int(r[2] or 0)) for r in cur.fetchall()}

            # daily expense
            cur.execute(
                """SELECT bill_date, SUM(amount)::numeric AS exp
                   FROM public.vendor_bills
                   WHERE review_status = 'confirmed'
                     AND bill_date IS NOT NULL
                     AND bill_date BETWEEN %s AND %s
                   GROUP BY bill_date
                   ORDER BY bill_date""",
                (from_date, to_date),
            )
            exp_map = {r[0]: float(r[1] or 0) for r in cur.fetchall()}

            # build day-by-day rows (include zero-sales days)
            rows = []
            d = from_date
            tot_sales = tot_bills = tot_exp = 0
            while d <= to_date:
                s_net, s_bills = sales_map.get(d, (0.0, 0))
                exp = exp_map.get(d, 0.0)
                profit = s_net - exp
                margin = round(profit / s_net * 100, 1) if s_net else None
                rows.append({
                    "sales_date": d.isoformat(),
                    "sales_net": s_net,
                    "sales_bill_count": s_bills,
                    "expense_total": exp,
                    "gross_profit": profit,
                    "gross_margin_pct": margin,
                })
                tot_sales += s_net; tot_bills += s_bills; tot_exp += exp
                d = date.fromordinal(d.toordinal() + 1)

            tot_profit = tot_sales - tot_exp
            tot_margin = round(tot_profit / tot_sales * 100, 1) if tot_sales else None
        return {
            "from": from_date.isoformat(),
            "to": to_date.isoformat(),
            "branch_code": branch,
            "rows": rows,
            "totals": {
                "sales_net": tot_sales,
                "sales_bill_count": tot_bills,
                "expense_total": tot_exp,
                "gross_profit": tot_profit,
                "gross_margin_pct": tot_margin,
            },
        }
    finally:
        conn.close()


@router.get("/pnl/monthly")
def pnl_monthly(
    year: int = Query(2026, ge=2020, le=2099),
    branch: str = Query(DEFAULT_BRANCH),
):
    """Monthly P&L for a full year (up to 12 rows)."""
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT date_trunc('month', sales_date)::date AS m,
                          SUM(net_total)::numeric, SUM(bill_count)::int
                   FROM public.pos_sales_daily
                   WHERE branch_code = %s
                     AND EXTRACT(YEAR FROM sales_date) = %s
                   GROUP BY 1
                   ORDER BY 1""",
                (branch, year),
            )
            sales_map = {r[0]: (float(r[1] or 0), int(r[2] or 0)) for r in cur.fetchall()}

            cur.execute(
                """SELECT date_trunc('month', bill_date)::date AS m,
                          SUM(amount)::numeric, count(*)::int
                   FROM public.vendor_bills
                   WHERE review_status = 'confirmed' AND bill_date IS NOT NULL
                     AND EXTRACT(YEAR FROM bill_date) = %s
                   GROUP BY 1
                   ORDER BY 1""",
                (year,),
            )
            exp_map = {r[0]: (float(r[1] or 0), int(r[2] or 0)) for r in cur.fetchall()}

            months = sorted(set(sales_map) | set(exp_map))
            rows = []
            tot_s = tot_e = tot_sb = tot_eb = 0
            for m in months:
                s_net, s_b = sales_map.get(m, (0.0, 0))
                e_tot, e_b = exp_map.get(m, (0.0, 0))
                profit = s_net - e_tot
                margin = round(profit / s_net * 100, 1) if s_net else None
                rows.append({
                    "month": m.strftime("%Y-%m"),
                    "sales_net": s_net,
                    "expense_total": e_tot,
                    "gross_profit": profit,
                    "gross_margin_pct": margin,
                    "bill_count_sales": s_b,
                    "bill_count_expense": e_b,
                })
                tot_s += s_net; tot_e += e_tot; tot_sb += s_b; tot_eb += e_b

            tot_p = tot_s - tot_e
            tot_m = round(tot_p / tot_s * 100, 1) if tot_s else None
        return {
            "year": year,
            "branch_code": branch,
            "rows": rows,
            "totals": {
                "sales_net": tot_s, "expense_total": tot_e,
                "gross_profit": tot_p, "gross_margin_pct": tot_m,
                "bill_count_sales": tot_sb, "bill_count_expense": tot_eb,
            },
        }
    finally:
        conn.close()


@router.get("/pnl/by-category")
def pnl_by_category(
    month: Optional[str] = Query(None),
    branch: str = Query(DEFAULT_BRANCH),
):
    """Expense breakdown by category for one month, with % of sales."""
    period_month = _month_start(month)
    pe = _next_month(period_month)
    prev = _prev_month(period_month)
    prev_end = _next_month(prev)

    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            # this month's sales
            cur.execute(
                """SELECT COALESCE(SUM(net_total),0)::numeric
                   FROM public.pos_sales_daily
                   WHERE branch_code = %s
                     AND sales_date >= %s AND sales_date < %s""",
                (branch, period_month, pe),
            )
            sales_net = float(cur.fetchone()[0] or 0)

            # this month's expense by category
            cur.execute(
                """SELECT vb.category_code,
                          COALESCE(ec.name_th, vb.category_code) AS name_th,
                          SUM(vb.amount)::numeric AS exp
                   FROM public.vendor_bills vb
                   LEFT JOIN public.expense_categories ec ON ec.code = vb.category_code
                   WHERE vb.review_status = 'confirmed'
                     AND vb.bill_date IS NOT NULL
                     AND vb.bill_date >= %s AND vb.bill_date < %s
                   GROUP BY vb.category_code, ec.name_th
                   ORDER BY exp DESC""",
                (period_month, pe),
            )
            curr_rows = [(r[0], r[1], float(r[2] or 0)) for r in cur.fetchall()]

            # prev month for comparison
            cur.execute(
                """SELECT category_code, SUM(amount)::numeric
                   FROM public.vendor_bills
                   WHERE review_status = 'confirmed' AND bill_date IS NOT NULL
                     AND bill_date >= %s AND bill_date < %s
                   GROUP BY category_code""",
                (prev, prev_end),
            )
            prev_map = {r[0]: float(r[1] or 0) for r in cur.fetchall()}

            total_exp = sum(r[2] for r in curr_rows if r[0])
            uncategorized = sum(r[2] for r in curr_rows if not r[0])

            categories = []
            for code, name_th, exp in curr_rows:
                if not code:
                    continue
                prev_amt = prev_map.get(code, 0)
                vs_prev = round((exp - prev_amt) / prev_amt * 100, 1) if prev_amt else None
                categories.append({
                    "category_code": code,
                    "name_th": name_th,
                    "expense": exp,
                    "pct_of_sales": round(exp / sales_net * 100, 2) if sales_net else None,
                    "pct_of_expense": round(exp / total_exp * 100, 2) if total_exp else None,
                    "vs_prev_month": vs_prev,
                })
        return {
            "month": period_month.strftime("%Y-%m"),
            "sales_net": sales_net,
            "categories": categories,
            "uncategorized_expense": uncategorized,
        }
    finally:
        conn.close()


# ============================================================
# SECTION E — INVENTORY
# ============================================================

@router.get("/inventory/current")
def inventory_current(branch: str = Query(DEFAULT_BRANCH)):
    """Latest snapshot + status-grouped item list."""
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT id, snapshot_at, item_count, total_value
                   FROM public.pos_inventory_snapshots
                   WHERE branch_code = %s
                   ORDER BY snapshot_at DESC
                   LIMIT 1""",
                (branch,),
            )
            row = cur.fetchone()
            if not row:
                return {
                    "snapshot_at": None,
                    "branch_code": branch,
                    "total_items": 0,
                    "total_value": 0,
                    "by_status": {"negative":0,"out":0,"critical":0,"low":0,"ok":0},
                    "items": [],
                }
            snap_id, snap_at, item_count, total_value = row

            cur.execute(
                """SELECT item_name, material_code, tag,
                          qty_in_stock, qty_max, qty_diff,
                          unit, unit_price, stock_value
                   FROM public.pos_inventory_items
                   WHERE snapshot_id = %s
                   ORDER BY
                     CASE
                       WHEN qty_in_stock < 0 THEN 1
                       WHEN qty_in_stock = 0 THEN 2
                       WHEN qty_in_stock < qty_max*0.2 THEN 3
                       WHEN qty_in_stock < qty_max*0.5 THEN 4
                       ELSE 5
                     END,
                     item_name""",
                (snap_id,),
            )
            items = []
            counts = {"negative":0,"out":0,"critical":0,"low":0,"ok":0}
            for r in cur.fetchall():
                qty = float(r[3]) if r[3] is not None else 0
                qmax = float(r[4]) if r[4] is not None else 0
                if qty < 0: status = "negative"
                elif qty == 0: status = "out"
                elif qmax > 0 and qty < qmax * 0.2: status = "critical"
                elif qmax > 0 and qty < qmax * 0.5: status = "low"
                else: status = "ok"
                counts[status] += 1
                items.append({
                    "item_name": r[0],
                    "material_code": r[1],
                    "tag": r[2],
                    "qty_in_stock": qty,
                    "qty_max": qmax,
                    "qty_diff": float(r[5]) if r[5] is not None else None,
                    "unit": r[6],
                    "unit_price": float(r[7]) if r[7] is not None else None,
                    "stock_value": float(r[8]) if r[8] is not None else 0,
                    "status": status,
                })
        return {
            "snapshot_at": snap_at.isoformat() if snap_at else None,
            "branch_code": branch,
            "total_items": item_count,
            "total_value": float(total_value or 0),
            "by_status": counts,
            "items": items,
        }
    finally:
        conn.close()


@router.get("/inventory/snapshots")
def inventory_snapshots(limit: int = Query(10, ge=1, le=50)):
    """List recent inventory snapshots."""
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT id, branch_code, snapshot_at, item_count, total_value
                   FROM public.pos_inventory_snapshots
                   ORDER BY snapshot_at DESC
                   LIMIT %s""",
                (limit,),
            )
            return _rows_to_dicts(cur)
    finally:
        conn.close()


# ============================================================
# SECTION F — EXPORT
# All export endpoints have been moved to export_routes.py (Phase 9).
# Do NOT add export routes here — they will conflict.
# ============================================================


# ── GET /dashboard/category-trends ───────────────────────────────────────────
# Phase 36: Expense Category 6-Month Trend

@router.get("/dashboard/category-trends")
def category_trends(
    months: int = Query(6, ge=2, le=12),
    branch: str = Query(DEFAULT_BRANCH),
):
    """
    Per-category expense totals across the last N months.
    Sources: vendor_bills (confirmed, expense) + manual_entries (expense) +
             bank_statement_entries (debit > 0, categorised).
    Returns month labels + per-category monthly arrays + trend indicators.
    """
    from datetime import date as _date
    from collections import defaultdict

    today = _date.today()
    # end = first day of next month (exclusive)
    end = (_date(today.year, today.month, 1).replace(day=28) +
           __import__('datetime').timedelta(days=4)).replace(day=1)

    # build month list: last `months` calendar months before end
    month_starts = []
    cur_m = end
    for _ in range(months):
        prev = cur_m - __import__('datetime').timedelta(days=1)
        cur_m = _date(prev.year, prev.month, 1)
        month_starts.insert(0, cur_m)

    start = month_starts[0]
    month_keys = [m.strftime("%Y-%m") for m in month_starts]

    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            # vendor_bills
            cur.execute("""
                SELECT
                    vb.category_code,
                    COALESCE(ec.name_th, vb.category_code) AS name_th,
                    DATE_TRUNC('month', vb.bill_date)::date AS m,
                    SUM(vb.amount)::numeric AS total
                FROM public.vendor_bills vb
                LEFT JOIN public.expense_categories ec ON ec.code = vb.category_code
                WHERE vb.review_status = 'confirmed'
                  AND vb.direction = 'expense'
                  AND vb.bill_date >= %s AND vb.bill_date < %s
                  AND vb.category_code IS NOT NULL
                GROUP BY 1, 2, 3
            """, (start, end))
            bill_rows = cur.fetchall()

            # manual_entries
            cur.execute("""
                SELECT
                    me.category_code,
                    COALESCE(ec.name_th, me.category_code) AS name_th,
                    DATE_TRUNC('month', me.entry_date)::date AS m,
                    SUM(me.amount)::numeric AS total
                FROM public.manual_entries me
                LEFT JOIN public.expense_categories ec ON ec.code = me.category_code
                WHERE me.direction = 'expense'
                  AND me.entry_date >= %s AND me.entry_date < %s
                  AND me.category_code IS NOT NULL
                GROUP BY 1, 2, 3
            """, (start, end))
            manual_rows = cur.fetchall()

            # bank_statement_entries
            cur.execute("""
                SELECT
                    bse.category_code,
                    COALESCE(ec.name_th, bse.category_code) AS name_th,
                    DATE_TRUNC('month', bse.txn_date)::date AS m,
                    SUM(bse.debit)::numeric AS total
                FROM public.bank_statement_entries bse
                LEFT JOIN public.expense_categories ec ON ec.code = bse.category_code
                WHERE bse.branch_code = %s
                  AND bse.debit > 0
                  AND bse.category_code IS NOT NULL
                  AND bse.txn_date >= %s AND bse.txn_date < %s
                GROUP BY 1, 2, 3
            """, (branch, start, end))
            bank_rows = cur.fetchall()

    finally:
        conn.close()

    # ── Merge all sources ─────────────────────────────────────────────────
    monthly: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    names: dict[str, str] = {}

    for code, name, m, total in list(bill_rows) + list(manual_rows) + list(bank_rows):
        mk = m.strftime("%Y-%m")
        monthly[code][mk] += float(total or 0)
        if code not in names and name:
            names[code] = name

    # ── Build per-category series ─────────────────────────────────────────
    categories = []
    for code, month_map in monthly.items():
        series = [round(month_map.get(mk, 0.0), 2) for mk in month_keys]
        total_all = sum(series)
        if total_all == 0:
            continue
        non_zero = [v for v in series if v > 0]
        avg = total_all / len(non_zero) if non_zero else 0

        # Trend: compare last 2 months
        last = series[-1] if series else 0
        prev = series[-2] if len(series) >= 2 else 0
        if prev > 0:
            change_pct = (last - prev) / prev * 100
        else:
            change_pct = 0
        if change_pct > 10:
            trend = "rising"
        elif change_pct < -10:
            trend = "falling"
        else:
            trend = "stable"

        categories.append({
            "category_code": code,
            "name_th":       names.get(code, code),
            "series":        series,        # aligned to month_keys
            "total":         round(total_all, 2),
            "avg_monthly":   round(avg, 2),
            "last_month":    round(last, 2),
            "prev_month":    round(prev, 2),
            "change_pct":    round(change_pct, 1),
            "trend":         trend,
        })

    # Sort by total spend descending
    categories.sort(key=lambda x: x["total"], reverse=True)

    return {
        "months":     month_keys,
        "categories": categories,
        "branch":     branch,
        "note":       f"ข้อมูลค่าใช้จ่ายย้อนหลัง {months} เดือน (vendor_bills + manual_entries + bank_statement)"
    }
