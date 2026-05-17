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


# ── GET /pos/dow-stats — Phase 37 ────────────────────────────────────────────

DOW_LABELS_TH = ["อาทิตย์", "จันทร์", "อังคาร", "พุธ", "พฤหัส", "ศุกร์", "เสาร์"]
DOW_LABELS_SHORT = ["อา.", "จ.", "อ.", "พ.", "พฤ.", "ศ.", "ส."]

@router.get("/pos/dow-stats")
def pos_dow_stats(
    months: int = Query(6, ge=1, le=12),
    branch: str = Query(DEFAULT_BRANCH),
):
    """
    Day-of-week revenue & bill-count analytics from pos_sales_daily.
    Returns per-DOW averages + per-month per-DOW breakdown for heatmap.
    PostgreSQL DOW: 0=Sunday … 6=Saturday
    """
    today = date.today()
    end = (today.replace(day=28) + timedelta(days=4)).replace(day=1)
    start = end
    for _ in range(months):
        start = (start - timedelta(days=1)).replace(day=1)

    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            # ── Per-DOW aggregate (avg over all days in range) ──
            cur.execute("""
                SELECT
                    EXTRACT(DOW FROM sales_date)::int   AS dow,
                    COUNT(DISTINCT sales_date)::int      AS day_count,
                    SUM(net_total)::numeric              AS total_revenue,
                    AVG(net_total)::numeric              AS avg_revenue,
                    SUM(bill_count)::numeric             AS total_bills,
                    AVG(bill_count)::numeric             AS avg_bills,
                    AVG(net_total / NULLIF(bill_count,0))::numeric AS avg_per_bill
                FROM public.pos_sales_daily
                WHERE branch_code = %s
                  AND sales_date >= %s AND sales_date < %s
                  AND net_total > 0
                GROUP BY 1
                ORDER BY 1
            """, (branch, start, end))
            dow_agg = {r[0]: r for r in cur.fetchall()}

            # ── Per-month per-DOW (for heatmap) ──
            cur.execute("""
                SELECT
                    DATE_TRUNC('month', sales_date)::date AS m,
                    EXTRACT(DOW FROM sales_date)::int      AS dow,
                    SUM(net_total)::numeric                AS total_revenue,
                    COUNT(DISTINCT sales_date)::int        AS day_count
                FROM public.pos_sales_daily
                WHERE branch_code = %s
                  AND sales_date >= %s AND sales_date < %s
                  AND net_total > 0
                GROUP BY 1, 2
                ORDER BY 1, 2
            """, (branch, start, end))
            heatmap_raw = cur.fetchall()

    finally:
        conn.close()

    # ── Build DOW stats list ──────────────────────────────────────────────
    stats = []
    for dow in range(7):
        r = dow_agg.get(dow)
        if r:
            stats.append({
                "dow":            dow,
                "label":          DOW_LABELS_TH[dow],
                "label_short":    DOW_LABELS_SHORT[dow],
                "day_count":      int(r[1]),
                "total_revenue":  round(float(r[2] or 0), 2),
                "avg_revenue":    round(float(r[3] or 0), 2),
                "total_bills":    round(float(r[4] or 0), 2),
                "avg_bills":      round(float(r[5] or 0), 1),
                "avg_per_bill":   round(float(r[6] or 0), 2),
            })
        else:
            stats.append({
                "dow": dow, "label": DOW_LABELS_TH[dow],
                "label_short": DOW_LABELS_SHORT[dow],
                "day_count": 0, "total_revenue": 0, "avg_revenue": 0,
                "total_bills": 0, "avg_bills": 0, "avg_per_bill": 0,
            })

    revenues = [s["avg_revenue"] for s in stats if s["avg_revenue"] > 0]
    best  = max(stats, key=lambda x: x["avg_revenue"]) if revenues else None
    worst = min((s for s in stats if s["avg_revenue"] > 0), key=lambda x: x["avg_revenue"]) if revenues else None

    # ── Build heatmap: {month_key: {dow: avg_revenue}} ───────────────────
    heatmap: dict[str, dict[int, float]] = {}
    month_keys_seen: list[str] = []
    for m, dow, rev, cnt in heatmap_raw:
        mk = m.strftime("%Y-%m")
        if mk not in heatmap:
            heatmap[mk] = {}
            month_keys_seen.append(mk)
        heatmap[mk][dow] = round(float(rev or 0) / max(int(cnt), 1), 2)  # avg per day-occurrence

    # Build sorted month list
    month_keys_seen.sort()

    heatmap_rows = [
        {
            "month": mk,
            "by_dow": [heatmap[mk].get(d, 0) for d in range(7)],
        }
        for mk in month_keys_seen
    ]

    return {
        "months":        months,
        "branch":        branch,
        "date_from":     str(start),
        "date_to":       str(end),
        "dow_labels":    DOW_LABELS_TH,
        "dow_labels_short": DOW_LABELS_SHORT,
        "stats":         stats,
        "best_day":      best,
        "worst_day":     worst,
        "heatmap":       heatmap_rows,
    }


# ─────────────────────────────────────────────────────────
# GET /pos/hourly-stats
# Phase 40 — Hourly Sales Analysis
# ─────────────────────────────────────────────────────────

HOUR_LABELS = [f"{h:02d}:00" for h in range(24)]

@router.get("/pos/hourly-stats")
def get_hourly_stats(
    months: int = Query(6, ge=1, le=24),
    branch: str = Query(DEFAULT_BRANCH),
):
    """
    Aggregate pos_bills by hour-of-day.
    Returns: per-hour stats, peak_hour, slow_hour.
    """
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            # Date window
            end   = date.today()
            start = (end.replace(day=1) - timedelta(days=1)).replace(day=1)
            for _ in range(months - 1):
                start = (start.replace(day=1) - timedelta(days=1)).replace(day=1)

            # ── Per-hour aggregates ──────────────────────────────────────
            cur.execute("""
                SELECT
                    EXTRACT(HOUR FROM sales_time)::int  AS hr,
                    COUNT(*)                            AS total_bills,
                    SUM(bill_net)                       AS total_revenue,
                    COUNT(DISTINCT sales_date)          AS day_count
                FROM pos_bills
                WHERE branch_code = %s
                  AND sales_date  >= %s
                  AND sales_date  <  %s
                  AND sales_time  IS NOT NULL
                  AND bill_net    >  0
                GROUP BY hr
                ORDER BY hr
            """, (branch, start, end))
            rows = _rows_to_dicts(cur)

        # Build full 24-hour array (fill zeros for missing hours)
        hour_map: dict[int, dict] = {r["hr"]: r for r in rows}
        stats: list[dict] = []
        for h in range(24):
            r = hour_map.get(h, {})
            total_rev  = float(r.get("total_revenue") or 0)
            total_bills = int(r.get("total_bills") or 0)
            day_count   = int(r.get("day_count") or 0)
            # avg per day that had ANY sales in this hour
            avg_rev   = round(total_rev  / day_count,   2) if day_count  > 0 else 0
            avg_bills = round(total_bills / day_count,  2) if day_count  > 0 else 0
            avg_per_bill = round(total_rev / total_bills, 2) if total_bills > 0 else 0
            stats.append({
                "hour":          h,
                "label":         HOUR_LABELS[h],
                "total_bills":   total_bills,
                "total_revenue": round(total_rev, 2),
                "day_count":     day_count,
                "avg_revenue":   avg_rev,
                "avg_bills":     avg_bills,
                "avg_per_bill":  avg_per_bill,
            })

        active = [s for s in stats if s["avg_revenue"] > 0]
        peak = max(active, key=lambda s: s["avg_revenue"]) if active else None
        slow = min(active, key=lambda s: s["avg_revenue"]) if active else None

        return {
            "months":     months,
            "branch":     branch,
            "date_from":  str(start),
            "date_to":    str(end),
            "stats":      stats,
            "peak_hour":  peak,
            "slow_hour":  slow,
        }
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────
# GET /pos/channel-stats
# Phase 41 — Channel & Order Type Analysis
# ─────────────────────────────────────────────────────────

@router.get("/pos/channel-stats")
def get_channel_stats(
    months: int = Query(6, ge=1, le=24),
    branch: str = Query(DEFAULT_BRANCH),
):
    """
    Breakdown of POS revenue by order_type and payment channel.
    Also returns a monthly trend for each order_type over the window.
    """
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            # Date window
            end   = date.today()
            start = (end.replace(day=1) - timedelta(days=1)).replace(day=1)
            for _ in range(months - 1):
                start = (start.replace(day=1) - timedelta(days=1)).replace(day=1)

            # ── 1. Order type breakdown ─────────────────────────────────
            cur.execute("""
                SELECT
                    COALESCE(NULLIF(TRIM(order_type), ''), 'ไม่ระบุ') AS order_type,
                    COUNT(*)        AS bill_count,
                    SUM(bill_net)   AS total_revenue,
                    AVG(bill_net)   AS avg_per_bill
                FROM pos_bills
                WHERE branch_code = %s
                  AND sales_date  >= %s
                  AND sales_date  <  %s
                  AND bill_net    >  0
                GROUP BY order_type
                ORDER BY total_revenue DESC
            """, (branch, start, end))
            order_type_rows = _rows_to_dicts(cur)

            # ── 2. Payment method breakdown ─────────────────────────────
            cur.execute("""
                SELECT
                    COALESCE(NULLIF(TRIM(payment_method), ''), 'ไม่ระบุ') AS payment_method,
                    COUNT(*)        AS bill_count,
                    SUM(bill_net)   AS total_revenue,
                    AVG(bill_net)   AS avg_per_bill
                FROM pos_bills
                WHERE branch_code = %s
                  AND sales_date  >= %s
                  AND sales_date  <  %s
                  AND bill_net    >  0
                GROUP BY payment_method
                ORDER BY total_revenue DESC
            """, (branch, start, end))
            payment_rows = _rows_to_dicts(cur)

            # ── 3. Monthly trend by order_type ──────────────────────────
            cur.execute("""
                SELECT
                    DATE_TRUNC('month', sales_date)::date              AS month,
                    COALESCE(NULLIF(TRIM(order_type), ''), 'ไม่ระบุ') AS order_type,
                    SUM(bill_net)                                       AS total_revenue,
                    COUNT(*)                                            AS bill_count
                FROM pos_bills
                WHERE branch_code = %s
                  AND sales_date  >= %s
                  AND sales_date  <  %s
                  AND bill_net    >  0
                GROUP BY month, order_type
                ORDER BY month, order_type
            """, (branch, start, end))
            trend_raw = _rows_to_dicts(cur)

        # Build month-keyed trend structure
        month_keys: list[str] = []
        order_types: list[str] = []
        trend_map: dict[str, dict[str, float]] = {}

        for r in trend_raw:
            mk = r["month"][:7]  # "YYYY-MM"
            ot = r["order_type"]
            if mk not in trend_map:
                trend_map[mk] = {}
                month_keys.append(mk)
            if ot not in order_types:
                order_types.append(ot)
            trend_map[mk][ot] = round(float(r["total_revenue"] or 0), 2)

        month_keys = sorted(set(month_keys))

        trend_rows = [
            {
                "month": mk,
                "by_type": {ot: trend_map.get(mk, {}).get(ot, 0) for ot in order_types},
            }
            for mk in month_keys
        ]

        # Grand total for percentage calculation
        grand_total = sum(float(r["total_revenue"] or 0) for r in order_type_rows)

        # Enrich order_type rows with pct
        for r in order_type_rows:
            rev = float(r.get("total_revenue") or 0)
            r["total_revenue"]  = round(rev, 2)
            r["avg_per_bill"]   = round(float(r.get("avg_per_bill") or 0), 2)
            r["pct"]            = round(rev / grand_total * 100, 1) if grand_total > 0 else 0

        for r in payment_rows:
            rev = float(r.get("total_revenue") or 0)
            r["total_revenue"] = round(rev, 2)
            r["avg_per_bill"]  = round(float(r.get("avg_per_bill") or 0), 2)
            r["pct"]           = round(rev / grand_total * 100, 1) if grand_total > 0 else 0

        return {
            "months":       months,
            "branch":       branch,
            "date_from":    str(start),
            "date_to":      str(end),
            "grand_total":  round(grand_total, 2),
            "order_types":  order_type_rows,
            "payments":     payment_rows,
            "trend_months": month_keys,
            "order_types_list": order_types,
            "trend":        trend_rows,
        }
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────
# GET /pos/staff-stats
# Phase 42 — Staff Performance Analytics
# ─────────────────────────────────────────────────────────

SHIFT_MORNING = (6, 14)   # 06:00 – 14:59
SHIFT_EVENING = (15, 23)  # 15:00 – 23:59

@router.get("/pos/staff-stats")
def get_staff_stats(
    months: int = Query(6, ge=1, le=24),
    branch: str = Query(DEFAULT_BRANCH),
):
    """
    Staff performance ranked by revenue (opened_by).
    Also returns shift breakdown (morning / evening) per staff.
    """
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            # Date window
            end   = date.today()
            start = (end.replace(day=1) - timedelta(days=1)).replace(day=1)
            for _ in range(months - 1):
                start = (start.replace(day=1) - timedelta(days=1)).replace(day=1)

            # ── 1. Overall per-staff stats ───────────────────────────────
            cur.execute("""
                SELECT
                    COALESCE(NULLIF(TRIM(opened_by), ''), 'ไม่ระบุ') AS staff,
                    COUNT(*)        AS bill_count,
                    SUM(bill_net)   AS total_revenue,
                    AVG(bill_net)   AS avg_per_bill,
                    SUM(bill_discount) AS total_discount,
                    COUNT(DISTINCT sales_date) AS active_days
                FROM pos_bills
                WHERE branch_code = %s
                  AND sales_date  >= %s
                  AND sales_date  <  %s
                  AND bill_net    >  0
                GROUP BY staff
                ORDER BY total_revenue DESC
            """, (branch, start, end))
            staff_rows = _rows_to_dicts(cur)

            # ── 2. Per-staff, per-shift breakdown ────────────────────────
            cur.execute("""
                SELECT
                    COALESCE(NULLIF(TRIM(opened_by), ''), 'ไม่ระบุ') AS staff,
                    CASE
                        WHEN EXTRACT(HOUR FROM sales_time) BETWEEN %s AND %s THEN 'morning'
                        WHEN EXTRACT(HOUR FROM sales_time) BETWEEN %s AND %s THEN 'evening'
                        ELSE 'other'
                    END AS shift,
                    COUNT(*)      AS bill_count,
                    SUM(bill_net) AS total_revenue
                FROM pos_bills
                WHERE branch_code = %s
                  AND sales_date  >= %s
                  AND sales_date  <  %s
                  AND bill_net    >  0
                  AND sales_time  IS NOT NULL
                GROUP BY staff, shift
                ORDER BY staff, shift
            """, (
                SHIFT_MORNING[0], SHIFT_MORNING[1],
                SHIFT_EVENING[0], SHIFT_EVENING[1],
                branch, start, end,
            ))
            shift_raw = _rows_to_dicts(cur)

            # ── 3. Overall shift summary ─────────────────────────────────
            cur.execute("""
                SELECT
                    CASE
                        WHEN EXTRACT(HOUR FROM sales_time) BETWEEN %s AND %s THEN 'morning'
                        WHEN EXTRACT(HOUR FROM sales_time) BETWEEN %s AND %s THEN 'evening'
                        ELSE 'other'
                    END AS shift,
                    COUNT(*)      AS bill_count,
                    SUM(bill_net) AS total_revenue,
                    AVG(bill_net) AS avg_per_bill
                FROM pos_bills
                WHERE branch_code = %s
                  AND sales_date  >= %s
                  AND sales_date  <  %s
                  AND bill_net    >  0
                  AND sales_time  IS NOT NULL
                GROUP BY shift
                ORDER BY total_revenue DESC
            """, (
                SHIFT_MORNING[0], SHIFT_MORNING[1],
                SHIFT_EVENING[0], SHIFT_EVENING[1],
                branch, start, end,
            ))
            shift_summary = _rows_to_dicts(cur)

        # Build shift map: {staff: {morning: rev, evening: rev}}
        shift_map: dict[str, dict[str, float]] = {}
        for r in shift_raw:
            s = r["staff"]
            sh = r["shift"]
            if s not in shift_map:
                shift_map[s] = {}
            shift_map[s][sh] = round(float(r["total_revenue"] or 0), 2)

        grand_total = sum(float(r.get("total_revenue") or 0) for r in staff_rows)

        # Enrich staff rows
        for r in staff_rows:
            s = r["staff"]
            rev = float(r.get("total_revenue") or 0)
            r["total_revenue"]  = round(rev, 2)
            r["avg_per_bill"]   = round(float(r.get("avg_per_bill") or 0), 2)
            r["total_discount"] = round(float(r.get("total_discount") or 0), 2)
            r["pct"]            = round(rev / grand_total * 100, 1) if grand_total > 0 else 0
            r["morning_rev"]    = shift_map.get(s, {}).get("morning", 0)
            r["evening_rev"]    = shift_map.get(s, {}).get("evening", 0)

        for r in shift_summary:
            r["total_revenue"] = round(float(r.get("total_revenue") or 0), 2)
            r["avg_per_bill"]  = round(float(r.get("avg_per_bill") or 0), 2)

        return {
            "months":        months,
            "branch":        branch,
            "date_from":     str(start),
            "date_to":       str(end),
            "grand_total":   round(grand_total, 2),
            "staff":         staff_rows,
            "shift_summary": shift_summary,
        }
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────
# GET /pos/table-stats
# Phase 43 — Table Performance Analytics
# ─────────────────────────────────────────────────────────

DOW_LABELS_SHORT2 = ['อา','จ','อ','พ','พฤ','ศ','ส']

@router.get("/pos/table-stats")
def get_table_stats(
    months: int = Query(6, ge=1, le=24),
    branch: str = Query(DEFAULT_BRANCH),
):
    """
    Revenue performance ranked by table_label.
    Also returns DOW breakdown per table (top 10 tables only).
    """
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            # Date window
            end   = date.today()
            start = (end.replace(day=1) - timedelta(days=1)).replace(day=1)
            for _ in range(months - 1):
                start = (start.replace(day=1) - timedelta(days=1)).replace(day=1)

            # ── 1. Per-table overall stats ────────────────────────────────
            cur.execute("""
                SELECT
                    TRIM(table_label)               AS tbl,
                    COUNT(*)                        AS bill_count,
                    SUM(bill_net)                   AS total_revenue,
                    AVG(bill_net)                   AS avg_per_bill,
                    COUNT(DISTINCT sales_date)      AS active_days
                FROM pos_bills
                WHERE branch_code  = %s
                  AND sales_date   >= %s
                  AND sales_date   <  %s
                  AND bill_net     >  0
                  AND table_label  IS NOT NULL
                  AND TRIM(table_label) <> ''
                GROUP BY tbl
                ORDER BY total_revenue DESC
            """, (branch, start, end))
            table_rows = _rows_to_dicts(cur)

            if not table_rows:
                return {
                    "months": months, "branch": branch,
                    "date_from": str(start), "date_to": str(end),
                    "grand_total": 0, "tables": [], "dow_heatmap": [],
                    "dow_labels_short": DOW_LABELS_SHORT2,
                }

            # ── 2. DOW breakdown for top-15 tables ───────────────────────
            top_tables = [r["tbl"] for r in table_rows[:15]]
            cur.execute("""
                SELECT
                    TRIM(table_label)                   AS tbl,
                    EXTRACT(DOW FROM sales_date)::int   AS dow,
                    COUNT(*)                            AS bill_count,
                    SUM(bill_net)                       AS total_revenue
                FROM pos_bills
                WHERE branch_code  = %s
                  AND sales_date   >= %s
                  AND sales_date   <  %s
                  AND bill_net     >  0
                  AND TRIM(table_label) = ANY(%s)
                GROUP BY tbl, dow
                ORDER BY tbl, dow
            """, (branch, start, end, top_tables))
            dow_raw = _rows_to_dicts(cur)

        # Build DOW map: {table: {dow: total_revenue}}
        dow_map: dict[str, dict[int, float]] = {}
        for r in dow_raw:
            t = r["tbl"]
            d = int(r["dow"])
            if t not in dow_map:
                dow_map[t] = {}
            dow_map[t][d] = round(float(r["total_revenue"] or 0), 2)

        grand_total = sum(float(r.get("total_revenue") or 0) for r in table_rows)

        # Enrich table rows
        for r in table_rows:
            rev  = float(r.get("total_revenue") or 0)
            days = int(r.get("active_days") or 1)
            cnt  = int(r.get("bill_count") or 0)
            r["total_revenue"] = round(rev, 2)
            r["avg_per_bill"]  = round(float(r.get("avg_per_bill") or 0), 2)
            r["pct"]           = round(rev / grand_total * 100, 1) if grand_total > 0 else 0
            r["turnover"]      = round(cnt / days, 1) if days > 0 else 0  # avg bills per active day

        # Build heatmap rows (top 15 tables × 7 DOW)
        heatmap = [
            {
                "table": t,
                "by_dow": [dow_map.get(t, {}).get(d, 0) for d in range(7)],
            }
            for t in top_tables
        ]

        return {
            "months":           months,
            "branch":           branch,
            "date_from":        str(start),
            "date_to":          str(end),
            "grand_total":      round(grand_total, 2),
            "tables":           table_rows,
            "dow_heatmap":      heatmap,
            "dow_labels_short": DOW_LABELS_SHORT2,
        }
    finally:
        conn.close()
