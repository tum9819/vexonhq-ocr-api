"""
VEXONHQ Phase 17 — Menu Performance
=====================================
Top/bottom sellers from pos_sales_items + pos_bills.

Endpoints:
  GET /menu/performance   — top/bottom items for a month (or date range)
  GET /menu/categories    — category breakdown for a month
  GET /menu/trends        — item trend across N months

In main.py add:
    from menu_routes import router as menu_router
    app.include_router(menu_router)
"""

from __future__ import annotations

import logging
import os
from datetime import date, datetime, timedelta
from typing import Any, Optional
from uuid import UUID

import psycopg2
from fastapi import APIRouter, HTTPException, Query

try:
    from main import get_db_conn  # type: ignore
except ImportError:
    def get_db_conn():
        return psycopg2.connect(os.environ["DATABASE_URL"])

log = logging.getLogger("menu_routes")
router = APIRouter(tags=["menu"])

DEFAULT_BRANCH = "thawi_watthana"


# ─────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────

def _rows_to_dicts(cur) -> list[dict]:
    if cur.description is None:
        return []
    cols = [d[0] for d in cur.description]
    rows = []
    for r in cur.fetchall():
        row: dict[str, Any] = {}
        for k, v in zip(cols, r):
            if isinstance(v, UUID):
                row[k] = str(v)
            elif isinstance(v, (datetime, date)):
                row[k] = v.isoformat()
            elif hasattr(v, "__float__") and not isinstance(v, (int, float, bool)):
                row[k] = float(v)
            else:
                row[k] = v
        rows.append(row)
    return rows


def _month_bounds(month: Optional[str]):
    if not month:
        today = date.today()
        start = today.replace(day=1)
    else:
        try:
            start = datetime.strptime(month + "-01", "%Y-%m-%d").date()
        except ValueError:
            raise HTTPException(400, f"Invalid month: {month!r} — use YYYY-MM")
    end = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
    return start, end


# ─────────────────────────────────────────────────────────
# GET /menu/performance
# ─────────────────────────────────────────────────────────

@router.get("/menu/performance")
def menu_performance(
    month: Optional[str] = Query(None, description="YYYY-MM, defaults to current month"),
    branch: str = Query(DEFAULT_BRANCH),
    limit: int = Query(20, ge=1, le=100, description="Top N items to return"),
):
    """
    Top and bottom performing menu items for a month.
    Requires pos_sales_items data (from FoodStory Type 7 bill detail import).
    Returns top sellers + bottom sellers + category breakdown.
    """
    start, end = _month_bounds(month)

    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            # ── Check if sales_items data exists ──────────────
            cur.execute(
                """SELECT COUNT(*) FROM public.pos_sales_items si
                   JOIN public.pos_bills pb ON pb.id = si.bill_id
                   WHERE pb.branch_code = %s
                     AND pb.sales_date >= %s
                     AND pb.sales_date < %s""",
                (branch, start, end),
            )
            row_count = cur.fetchone()[0]

            if row_count == 0:
                return {
                    "month": start.strftime("%Y-%m"),
                    "has_item_data": False,
                    "message": "ไม่มีข้อมูล item-level — ต้อง upload FoodStory Type 7 (bill detail) ก่อน",
                    "top_items": [],
                    "bottom_items": [],
                    "categories": [],
                    "total_items_sold": 0,
                    "total_revenue": 0.0,
                }

            # ── Top items by revenue ───────────────────────────
            cur.execute(
                """SELECT
                       si.item_name,
                       COALESCE(si.category, 'ไม่ระบุหมวด')  AS category,
                       COALESCE(si.product_group, '')         AS product_group,
                       SUM(si.qty)::numeric                   AS total_qty,
                       SUM(si.net_amount)::numeric            AS total_revenue,
                       AVG(si.unit_price)::numeric            AS avg_price,
                       COUNT(DISTINCT si.bill_id)             AS order_count,
                       SUM(si.discount)::numeric              AS total_discount
                   FROM public.pos_sales_items si
                   JOIN public.pos_bills pb ON pb.id = si.bill_id
                   WHERE pb.branch_code = %s
                     AND pb.sales_date >= %s
                     AND pb.sales_date < %s
                     AND si.item_name IS NOT NULL
                     AND si.item_name <> ''
                   GROUP BY si.item_name, si.category, si.product_group
                   ORDER BY total_revenue DESC
                   LIMIT %s""",
                (branch, start, end, limit),
            )
            top_items = _rows_to_dicts(cur)

            # ── Bottom items (min 3 orders to filter noise) ───
            cur.execute(
                """SELECT
                       si.item_name,
                       COALESCE(si.category, 'ไม่ระบุหมวด')  AS category,
                       COALESCE(si.product_group, '')         AS product_group,
                       SUM(si.qty)::numeric                   AS total_qty,
                       SUM(si.net_amount)::numeric            AS total_revenue,
                       AVG(si.unit_price)::numeric            AS avg_price,
                       COUNT(DISTINCT si.bill_id)             AS order_count,
                       SUM(si.discount)::numeric              AS total_discount
                   FROM public.pos_sales_items si
                   JOIN public.pos_bills pb ON pb.id = si.bill_id
                   WHERE pb.branch_code = %s
                     AND pb.sales_date >= %s
                     AND pb.sales_date < %s
                     AND si.item_name IS NOT NULL
                     AND si.item_name <> ''
                   GROUP BY si.item_name, si.category, si.product_group
                   HAVING COUNT(DISTINCT si.bill_id) >= 3
                   ORDER BY total_revenue ASC
                   LIMIT %s""",
                (branch, start, end, limit),
            )
            bottom_items = _rows_to_dicts(cur)

            # ── Category breakdown ─────────────────────────────
            cur.execute(
                """SELECT
                       COALESCE(si.category, 'ไม่ระบุหมวด') AS category,
                       COUNT(DISTINCT si.item_name)           AS item_count,
                       SUM(si.qty)::numeric                   AS total_qty,
                       SUM(si.net_amount)::numeric            AS total_revenue
                   FROM public.pos_sales_items si
                   JOIN public.pos_bills pb ON pb.id = si.bill_id
                   WHERE pb.branch_code = %s
                     AND pb.sales_date >= %s
                     AND pb.sales_date < %s
                   GROUP BY 1
                   ORDER BY total_revenue DESC""",
                (branch, start, end),
            )
            categories = _rows_to_dicts(cur)

            # ── Totals ─────────────────────────────────────────
            cur.execute(
                """SELECT
                       COUNT(DISTINCT si.item_name)  AS unique_items,
                       SUM(si.qty)::numeric           AS total_qty,
                       SUM(si.net_amount)::numeric    AS total_revenue,
                       COUNT(DISTINCT si.bill_id)     AS total_bills
                   FROM public.pos_sales_items si
                   JOIN public.pos_bills pb ON pb.id = si.bill_id
                   WHERE pb.branch_code = %s
                     AND pb.sales_date >= %s
                     AND pb.sales_date < %s""",
                (branch, start, end),
            )
            r = cur.fetchone()
            unique_items  = int(r[0] or 0)
            total_qty     = float(r[1] or 0)
            total_revenue = float(r[2] or 0)
            total_bills   = int(r[3] or 0)

        # Enrich with share %
        for item in top_items:
            item["revenue_share_pct"] = round(
                float(item.get("total_revenue") or 0) / total_revenue * 100, 1
            ) if total_revenue else 0.0
        for cat in categories:
            cat["revenue_share_pct"] = round(
                float(cat.get("total_revenue") or 0) / total_revenue * 100, 1
            ) if total_revenue else 0.0

        return {
            "month": start.strftime("%Y-%m"),
            "has_item_data": True,
            "unique_items": unique_items,
            "total_qty": total_qty,
            "total_revenue": total_revenue,
            "total_bills": total_bills,
            "avg_items_per_bill": round(total_qty / total_bills, 1) if total_bills else 0,
            "top_items": top_items,
            "bottom_items": bottom_items,
            "categories": categories,
        }
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────
# GET /menu/trends  — item revenue across last N months
# ─────────────────────────────────────────────────────────

@router.get("/menu/trends")
def menu_trends(
    item_names: str = Query(..., description="Comma-separated item names"),
    months: int = Query(6, ge=2, le=12),
    branch: str = Query(DEFAULT_BRANCH),
):
    """
    Month-by-month revenue trend for selected menu items.
    item_names: comma-separated, e.g. 'หมูปิ้ง,ข้าวเหนียว'
    """
    names = [n.strip() for n in item_names.split(",") if n.strip()]
    if not names:
        raise HTTPException(400, "item_names required")

    today = date.today()
    end = (today.replace(day=28) + timedelta(days=4)).replace(day=1)
    start = end
    for _ in range(months):
        start = (start - timedelta(days=1)).replace(day=1)

    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT
                       date_trunc('month', pb.sales_date)::date AS m,
                       si.item_name,
                       SUM(si.qty)::numeric     AS total_qty,
                       SUM(si.net_amount)::numeric AS total_revenue
                   FROM public.pos_sales_items si
                   JOIN public.pos_bills pb ON pb.id = si.bill_id
                   WHERE pb.branch_code = %s
                     AND pb.sales_date >= %s
                     AND pb.sales_date < %s
                     AND si.item_name = ANY(%s)
                   GROUP BY 1, 2
                   ORDER BY 1, 2""",
                (branch, start, end, names),
            )
            rows = _rows_to_dicts(cur)
        return {"months": months, "branch": branch, "items": names, "data": rows}
    finally:
        conn.close()
