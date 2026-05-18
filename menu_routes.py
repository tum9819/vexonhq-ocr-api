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


# ─────────────────────────────────────────────────────────
# GET /pos/overview
# Phase 44 — POS Command Center (all KPIs in one call)
# ─────────────────────────────────────────────────────────

@router.get("/pos/overview")
def get_pos_overview(
    branch: str = Query(DEFAULT_BRANCH),
):
    """
    Single-call POS command center:
    - This month vs last month revenue
    - Top/worst DOW
    - Peak hour
    - Top table + top staff
    - Order type split (this month)
    """
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            today      = date.today()
            this_start = today.replace(day=1)
            last_end   = this_start
            last_start = (last_end - timedelta(days=1)).replace(day=1)
            six_start  = (this_start - timedelta(days=1)).replace(day=1)
            for _ in range(5):
                six_start = (six_start - timedelta(days=1)).replace(day=1)

            # ── 1. This month vs last month revenue ──────────────────────
            cur.execute("""
                SELECT
                    SUM(CASE WHEN sales_date >= %s THEN bill_net ELSE 0 END) AS this_rev,
                    COUNT(CASE WHEN sales_date >= %s THEN 1 END)             AS this_bills,
                    SUM(CASE WHEN sales_date < %s AND sales_date >= %s THEN bill_net ELSE 0 END) AS last_rev,
                    COUNT(CASE WHEN sales_date < %s AND sales_date >= %s THEN 1 END)             AS last_bills
                FROM pos_bills
                WHERE branch_code = %s
                  AND sales_date  >= %s
                  AND bill_net    >  0
            """, (
                this_start,
                this_start,
                this_start, last_start,
                this_start, last_start,
                branch, last_start,
            ))
            rev_row = _rows_to_dicts(cur)[0]

            # ── 2. Best DOW (6-month) ────────────────────────────────────
            cur.execute("""
                SELECT
                    EXTRACT(DOW FROM sales_date)::int AS dow,
                    AVG(daily_total)                  AS avg_rev
                FROM (
                    SELECT sales_date, SUM(bill_net) AS daily_total
                    FROM pos_bills
                    WHERE branch_code = %s AND sales_date >= %s AND bill_net > 0
                    GROUP BY sales_date
                ) sub
                GROUP BY dow
                ORDER BY avg_rev DESC
                LIMIT 1
            """, (branch, six_start))
            dow_rows = _rows_to_dicts(cur)

            # ── 3. Peak hour (6-month) ───────────────────────────────────
            cur.execute("""
                SELECT
                    EXTRACT(HOUR FROM sales_time)::int AS hr,
                    SUM(bill_net)                      AS total_rev,
                    COUNT(*)                           AS bill_count
                FROM pos_bills
                WHERE branch_code = %s AND sales_date >= %s
                  AND bill_net > 0 AND sales_time IS NOT NULL
                GROUP BY hr
                ORDER BY total_rev DESC
                LIMIT 1
            """, (branch, six_start))
            hour_rows = _rows_to_dicts(cur)

            # ── 4. Top table (6-month) ───────────────────────────────────
            cur.execute("""
                SELECT TRIM(table_label) AS tbl, SUM(bill_net) AS total_rev, COUNT(*) AS bills
                FROM pos_bills
                WHERE branch_code = %s AND sales_date >= %s AND bill_net > 0
                  AND table_label IS NOT NULL AND TRIM(table_label) <> ''
                GROUP BY tbl ORDER BY total_rev DESC LIMIT 1
            """, (branch, six_start))
            table_rows = _rows_to_dicts(cur)

            # ── 5. Top staff (6-month) ───────────────────────────────────
            cur.execute("""
                SELECT COALESCE(NULLIF(TRIM(opened_by),''),'ไม่ระบุ') AS staff,
                       SUM(bill_net) AS total_rev, COUNT(*) AS bills
                FROM pos_bills
                WHERE branch_code = %s AND sales_date >= %s AND bill_net > 0
                GROUP BY staff ORDER BY total_rev DESC LIMIT 1
            """, (branch, six_start))
            staff_rows = _rows_to_dicts(cur)

            # ── 6. Order type split (this month) ────────────────────────
            cur.execute("""
                SELECT COALESCE(NULLIF(TRIM(order_type),''),'ไม่ระบุ') AS otype,
                       SUM(bill_net) AS rev, COUNT(*) AS bills
                FROM pos_bills
                WHERE branch_code = %s AND sales_date >= %s AND bill_net > 0
                GROUP BY otype ORDER BY rev DESC
            """, (branch, this_start))
            otype_rows = _rows_to_dicts(cur)

            # ── 7. Daily revenue last 14 days (sparkline) ───────────────
            cur.execute("""
                SELECT sales_date, SUM(bill_net) AS daily_rev
                FROM pos_bills
                WHERE branch_code = %s
                  AND sales_date >= %s AND sales_date < %s
                  AND bill_net > 0
                GROUP BY sales_date ORDER BY sales_date
            """, (branch, today - timedelta(days=13), today + timedelta(days=1)))
            sparkline_raw = _rows_to_dicts(cur)

        # ── Compute MoM delta ────────────────────────────────────────────
        this_rev  = float(rev_row.get("this_rev")  or 0)
        last_rev  = float(rev_row.get("last_rev")  or 0)
        this_bills = int(rev_row.get("this_bills") or 0)
        mom_delta  = round((this_rev - last_rev) / last_rev * 100, 1) if last_rev > 0 else None

        # DOW labels
        best_dow = None
        if dow_rows:
            d = dow_rows[0]
            best_dow = {
                "dow":    int(d["dow"]),
                "label":  DOW_LABELS_TH[int(d["dow"])],
                "label_short": DOW_LABELS_SHORT[int(d["dow"])],
                "avg_rev": round(float(d["avg_rev"] or 0), 2),
            }

        peak_hour = None
        if hour_rows:
            h = hour_rows[0]
            hr = int(h["hr"])
            peak_hour = {
                "hour": hr,
                "label": f"{hr:02d}:00",
                "total_rev": round(float(h["total_rev"] or 0), 2),
                "bill_count": int(h["bill_count"] or 0),
            }

        top_table = None
        if table_rows:
            t = table_rows[0]
            top_table = {
                "table":     t["tbl"],
                "total_rev": round(float(t["total_rev"] or 0), 2),
                "bills":     int(t["bills"] or 0),
            }

        top_staff = None
        if staff_rows:
            s = staff_rows[0]
            top_staff = {
                "staff":     s["staff"],
                "total_rev": round(float(s["total_rev"] or 0), 2),
                "bills":     int(s["bills"] or 0),
            }

        # Order type split with pct
        otype_total = sum(float(r.get("rev") or 0) for r in otype_rows)
        order_types = [
            {
                "type": r["otype"],
                "rev":  round(float(r.get("rev") or 0), 2),
                "bills": int(r.get("bills") or 0),
                "pct":  round(float(r.get("rev") or 0) / otype_total * 100, 1) if otype_total > 0 else 0,
            }
            for r in otype_rows
        ]

        # Sparkline: fill missing days with 0
        spark_map = {str(r["sales_date"]): round(float(r["daily_rev"] or 0), 2) for r in sparkline_raw}
        sparkline = [
            {"date": str(today - timedelta(days=13-i)), "rev": spark_map.get(str(today - timedelta(days=13-i)), 0)}
            for i in range(14)
        ]

        return {
            "branch":      branch,
            "as_of":       str(today),
            "this_month":  str(this_start),
            "last_month":  str(last_start),
            "this_rev":    round(this_rev, 2),
            "last_rev":    round(last_rev, 2),
            "this_bills":  this_bills,
            "mom_delta":   mom_delta,
            "best_dow":    best_dow,
            "peak_hour":   peak_hour,
            "top_table":   top_table,
            "top_staff":   top_staff,
            "order_types": order_types,
            "sparkline":   sparkline,
        }
    finally:
        conn.close()


# ============================================================
# Phase 45 — Delivery Platform Analytics
# ============================================================

@router.get("/delivery/summary")
def delivery_summary(months: int = 6, branch: str = "thawi_watthana"):
    """
    Delivery platform comparison: Grab vs Lineman.
    Returns per-platform KPIs + monthly trend + totals.
    """
    conn = get_db_conn()
    try:
        since = date.today().replace(day=1) - relativedelta(months=months - 1)

        # ── Per-platform KPIs ──
        platform_sql = """
            SELECT
                platform,
                COUNT(*)                                  AS months_active,
                SUM(gross_sales)                          AS gross_total,
                SUM(ABS(gp_amount))                       AS commission_total,
                SUM(COALESCE(promo_store, 0))             AS promo_total,
                SUM(net_payout)                           AS net_total,
                SUM(order_count)                          AS order_total,
                ROUND(AVG(gross_sales / NULLIF(order_count, 0))::numeric, 2) AS avg_basket,
                BOOL_OR(gp_is_estimated)                  AS gp_estimated
            FROM public.rider_deliveries
            WHERE delivery_date >= %s
              AND (%s = '' OR branch_code = %s)
            GROUP BY platform
            ORDER BY gross_total DESC
        """
        platform_rows = _rows_to_dicts(
            conn, platform_sql, (since, branch, branch)
        )

        platforms = []
        for r in platform_rows:
            gross = float(r["gross_total"] or 0)
            comm  = float(r["commission_total"] or 0)
            net   = float(r["net_total"] or 0)
            orders = int(r["order_total"] or 0)
            platforms.append({
                "platform":          r["platform"],
                "gross_total":       gross,
                "commission_total":  comm,
                "commission_pct":    round(comm / gross * 100, 1) if gross else 0,
                "promo_total":       float(r["promo_total"] or 0),
                "net_total":         net,
                "order_total":       orders,
                "avg_basket":        float(r["avg_basket"] or 0),
                "avg_net_per_order": round(net / orders, 2) if orders else 0,
                "gp_estimated":      bool(r["gp_estimated"]),
            })

        # ── Monthly trend by platform ──
        trend_sql = """
            SELECT
                TO_CHAR(DATE_TRUNC('month', delivery_date), 'YYYY-MM') AS month,
                platform,
                SUM(gross_sales)          AS gross,
                SUM(ABS(gp_amount))       AS commission,
                SUM(net_payout)           AS net,
                SUM(order_count)          AS orders
            FROM public.rider_deliveries
            WHERE delivery_date >= %s
              AND (%s = '' OR branch_code = %s)
            GROUP BY 1, 2
            ORDER BY 1, 2
        """
        trend_rows = _rows_to_dicts(conn, trend_sql, (since, branch, branch))

        # Build month × platform matrix
        months_set: dict = {}
        platforms_seen: set = set()
        for r in trend_rows:
            m = r["month"]
            p = r["platform"]
            platforms_seen.add(p)
            if m not in months_set:
                months_set[m] = {}
            months_set[m][p] = {
                "gross":      float(r["gross"] or 0),
                "commission": float(r["commission"] or 0),
                "net":        float(r["net"] or 0),
                "orders":     int(r["orders"] or 0),
            }

        trend = []
        for m in sorted(months_set.keys()):
            entry = {"month": m}
            for p in sorted(platforms_seen):
                d = months_set[m].get(p, {})
                entry[f"{p}_gross"]  = d.get("gross", 0)
                entry[f"{p}_net"]    = d.get("net", 0)
                entry[f"{p}_orders"] = d.get("orders", 0)
            trend.append(entry)

        # ── Grand totals ──
        total_gross = sum(p["gross_total"] for p in platforms)
        total_net   = sum(p["net_total"]   for p in platforms)
        total_comm  = sum(p["commission_total"] for p in platforms)
        total_orders = sum(p["order_total"] for p in platforms)

        return {
            "platforms":            platforms,
            "platforms_list":       sorted(platforms_seen),
            "trend":                trend,
            "total_gross":          total_gross,
            "total_net":            total_net,
            "total_commission":     total_comm,
            "total_commission_pct": round(total_comm / total_gross * 100, 1) if total_gross else 0,
            "total_orders":         total_orders,
            "period_months":        months,
        }
    finally:
        conn.close()


# ============================================================
# Phase 46 — Revenue Source Breakdown
# ============================================================

_REVENUE_SOURCE_META = {
    "pos_sale":             {"label": "POS หน้าร้าน",     "color": "#6366F1", "group": "pos"},
    "rider_income_grab":    {"label": "Grab Food",         "color": "#00B14F", "group": "delivery"},
    "rider_income_lineman": {"label": "LINE MAN",          "color": "#FFC800", "group": "delivery"},
    "manual":               {"label": "รายรับอื่นๆ (Manual)", "color": "#8B5CF6", "group": "other"},
    "ar_payment":           {"label": "รับชำระ AR",        "color": "#06B6D4", "group": "other"},
    "pos_cashflow":         {"label": "POS Cashflow",      "color": "#10B981", "group": "other"},
    "bank_statement":       {"label": "Bank Transfer",     "color": "#F59E0B", "group": "other"},
}

@router.get("/revenue/breakdown")
def revenue_breakdown(months: int = 6, branch: str = "thawi_watthana"):
    """
    All income sources from v_daybook — breakdown + monthly trend.
    Sources: pos_sale, rider_income_grab, rider_income_lineman,
             manual, ar_payment, pos_cashflow, bank_statement.
    """
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            since = date.today().replace(day=1) - relativedelta(months=months - 1)

            # ── Per-source totals ──
            cur.execute(
                """SELECT
                       source,
                       COUNT(*)    AS tx_count,
                       SUM(amount) AS total
                   FROM public.v_daybook
                   WHERE direction = 'income'
                     AND entry_date >= %s
                     AND (%s = '' OR branch_code = %s)
                   GROUP BY source
                   ORDER BY total DESC""",
                (since, branch, branch),
            )
            source_rows = _rows_to_dicts(cur)

            grand_total = sum(float(r["total"] or 0) for r in source_rows)

            sources = []
            for r in source_rows:
                src = r["source"]
                meta = _REVENUE_SOURCE_META.get(src, {"label": src, "color": "#64748b", "group": "other"})
                total = float(r["total"] or 0)
                sources.append({
                    "source":    src,
                    "label":     meta["label"],
                    "color":     meta["color"],
                    "group":     meta["group"],
                    "total":     total,
                    "pct":       round(total / grand_total * 100, 1) if grand_total else 0,
                    "tx_count":  int(r["tx_count"] or 0),
                })

            # ── Monthly trend per source ──
            cur.execute(
                """SELECT
                       TO_CHAR(DATE_TRUNC('month', entry_date), 'YYYY-MM') AS month,
                       source,
                       SUM(amount) AS total
                   FROM public.v_daybook
                   WHERE direction = 'income'
                     AND entry_date >= %s
                     AND (%s = '' OR branch_code = %s)
                   GROUP BY 1, 2
                   ORDER BY 1, 2""",
                (since, branch, branch),
            )
            trend_rows = _rows_to_dicts(cur)

            sources_seen: set = set()
            months_map: dict = {}
            for r in trend_rows:
                m = r["month"]
                s = r["source"]
                sources_seen.add(s)
                if m not in months_map:
                    months_map[m] = {}
                months_map[m][s] = float(r["total"] or 0)

            sources_order = [s["source"] for s in sources if s["source"] in sources_seen]

            trend = []
            for m in sorted(months_map.keys()):
                entry: dict = {"month": m}
                month_total = 0.0
                for s in sources_order:
                    v = months_map[m].get(s, 0)
                    entry[s] = round(v, 2)
                    month_total += v
                entry["total"] = round(month_total, 2)
                trend.append(entry)

            group_totals: dict = {}
            for s in sources:
                g = s["group"]
                group_totals[g] = group_totals.get(g, 0) + s["total"]

            return {
                "sources":        sources,
                "sources_order":  sources_order,
                "trend":          trend,
                "grand_total":    grand_total,
                "group_totals":   group_totals,
                "period_months":  months,
            }
    finally:
        conn.close()


# ============================================================
# Phase 47 — Alert Center
# ============================================================

@router.get("/alerts/summary")
def alerts_summary(branch: str = "thawi_watthana"):
    """
    Unified alert feed: anomaly bills, budget overruns, AP overdue/due-soon, low stock.
    Returns grouped alerts with severity (danger/warning/info).
    """
    conn = get_db_conn()
    try:
        today = date.today()
        this_month = today.strftime("%Y-%m")
        alerts: list[dict] = []

        # ── 1. Bill Anomalies (pending review) ──────────────────
        try:
            anom_sql = """
                SELECT a.id, a.severity, a.anomaly_type, a.message,
                       a.bill_amount, a.mean_amount, a.created_at,
                       vb.vendor_name, vb.bill_date, vb.category_code
                FROM public.bill_anomalies a
                JOIN public.vendor_bills vb ON vb.id = a.bill_id
                WHERE a.user_action IS NULL
                ORDER BY
                    CASE a.severity WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END,
                    a.created_at DESC
                LIMIT 20
            """
            anom_rows = _rows_to_dicts(conn, anom_sql, ())
            for r in anom_rows:
                sev = "danger" if r["severity"] == "high" else "warning"
                alerts.append({
                    "id":       f"anom_{r['id']}",
                    "type":     "anomaly",
                    "severity": sev,
                    "title":    f"บิลผิดปกติ — {r['vendor_name'] or 'ไม่ระบุ'}",
                    "message":  r["message"] or f"{r['anomaly_type']} ฿{r['bill_amount']:,.0f}",
                    "amount":   float(r["bill_amount"] or 0),
                    "date":     str(r["bill_date"] or ""),
                    "link":     "/ai-review",
                    "link_label": "ตรวจสอบ",
                })
        except Exception:
            pass

        # ── 2. Budget Overruns this month ────────────────────────
        try:
            budget_sql = """
                SELECT bt.category_code, bt.amount AS target,
                       COALESCE(act.actual, 0) AS actual
                FROM public.budget_targets bt
                LEFT JOIN (
                    SELECT
                        COALESCE(vb.category_code, 'other') AS category_code,
                        SUM(vb.amount) AS actual
                    FROM public.vendor_bills vb
                    WHERE TO_CHAR(vb.bill_date, 'YYYY-MM') = %s
                      AND vb.review_status = 'confirmed'
                      AND (%s = '' OR vb.branch_code = %s)
                    GROUP BY 1
                ) act USING (category_code)
                WHERE bt.month = %s
                  AND (%s = '' OR bt.branch_code = %s)
                  AND bt.amount > 0
                  AND COALESCE(act.actual, 0) >= bt.amount * 0.8
                ORDER BY (COALESCE(act.actual, 0) / bt.amount) DESC
            """
            budget_rows = _rows_to_dicts(
                conn, budget_sql,
                (this_month, branch, branch, this_month, branch, branch)
            )
            for r in budget_rows:
                target = float(r["target"] or 0)
                actual = float(r["actual"] or 0)
                pct = (actual / target * 100) if target else 0
                sev = "danger" if pct >= 100 else "warning"
                label = "เกินงบ" if pct >= 100 else f"ใกล้ถึงงบ ({pct:.0f}%)"
                alerts.append({
                    "id":       f"budget_{r['category_code']}",
                    "type":     "budget",
                    "severity": sev,
                    "title":    f"{label} — {r['category_code']}",
                    "message":  f"ใช้จริง ฿{actual:,.0f} / งบ ฿{target:,.0f} ({pct:.1f}%)",
                    "amount":   actual,
                    "date":     this_month,
                    "link":     "/budget",
                    "link_label": "ดูงบประมาณ",
                })
        except Exception:
            pass

        # ── 3. AP Overdue + Due Soon ─────────────────────────────
        try:
            ap_sql = """
                SELECT id, vendor_name, amount, due_date, payment_status,
                       (due_date - %s::date) AS days_until_due
                FROM public.vendor_bills
                WHERE payment_status = 'unpaid'
                  AND review_status = 'confirmed'
                  AND due_date IS NOT NULL
                  AND due_date <= (%s::date + INTERVAL '7 days')
                  AND (%s = '' OR branch_code = %s)
                ORDER BY due_date ASC
                LIMIT 20
            """
            ap_rows = _rows_to_dicts(conn, ap_sql, (today, today, branch, branch))
            for r in ap_rows:
                days = int(r["days_until_due"] or 0)
                if days < 0:
                    sev, label = "danger", f"เกินกำหนด {abs(days)} วัน"
                elif days == 0:
                    sev, label = "danger", "ครบกำหนดวันนี้"
                elif days <= 3:
                    sev, label = "warning", f"ครบกำหนดใน {days} วัน"
                else:
                    sev, label = "info", f"ครบกำหนดใน {days} วัน"
                alerts.append({
                    "id":       f"ap_{r['id']}",
                    "type":     "ap_due",
                    "severity": sev,
                    "title":    f"บิลค้างจ่าย — {r['vendor_name'] or 'ไม่ระบุ'}",
                    "message":  f"{label} | ฿{float(r['amount'] or 0):,.0f} | due {r['due_date']}",
                    "amount":   float(r["amount"] or 0),
                    "date":     str(r["due_date"] or ""),
                    "link":     "/bills/payment",
                    "link_label": "จ่ายบิล",
                })
        except Exception:
            pass

        # ── 4. Low Stock ─────────────────────────────────────────
        try:
            stock_sql = """
                SELECT i.item_name, i.qty_in_stock, i.unit,
                       s.snapshot_at
                FROM public.pos_inventory_items i
                JOIN public.pos_inventory_snapshots s ON s.id = i.snapshot_id
                WHERE s.id = (
                    SELECT id FROM public.pos_inventory_snapshots
                    ORDER BY snapshot_at DESC LIMIT 1
                )
                  AND (%s = '' OR s.branch_code = %s)
                  AND i.qty_in_stock <= 5
                ORDER BY i.qty_in_stock ASC
                LIMIT 15
            """
            stock_rows = _rows_to_dicts(conn, stock_sql, (branch, branch))
            for r in stock_rows:
                qty = float(r["qty_in_stock"] or 0)
                sev = "danger" if qty <= 0 else "warning"
                label = "หมดสต็อก" if qty <= 0 else f"เหลือ {qty} {r['unit'] or 'หน่วย'}"
                alerts.append({
                    "id":       f"stock_{r['item_name']}",
                    "type":     "low_stock",
                    "severity": sev,
                    "title":    f"Stock ต่ำ — {r['item_name']}",
                    "message":  label,
                    "amount":   qty,
                    "date":     str(r["snapshot_at"] or ""),
                    "link":     "/inventory",
                    "link_label": "ดู Inventory",
                })
        except Exception:
            pass

        # ── Sort: danger first, then warning, then info ──────────
        sev_order = {"danger": 0, "warning": 1, "info": 2}
        alerts.sort(key=lambda a: sev_order.get(a["severity"], 9))

        counts = {
            "danger":  sum(1 for a in alerts if a["severity"] == "danger"),
            "warning": sum(1 for a in alerts if a["severity"] == "warning"),
            "info":    sum(1 for a in alerts if a["severity"] == "info"),
            "total":   len(alerts),
        }

        return {
            "alerts":  alerts,
            "counts":  counts,
            "as_of":   str(today),
        }
    finally:
        conn.close()


# ============================================================
# Phase 48 — Monthly Business Scorecard
# ============================================================

def _score_status(value: float, good_thresh: float, warn_thresh: float, higher_is_better: bool = True) -> str:
    """Return 'good' | 'warning' | 'danger' based on thresholds."""
    if higher_is_better:
        if value >= good_thresh: return "good"
        if value >= warn_thresh: return "warning"
        return "danger"
    else:
        if value <= good_thresh: return "good"
        if value <= warn_thresh: return "warning"
        return "danger"

@router.get("/scorecard")
def scorecard(month: str = "", branch: str = "thawi_watthana"):
    """
    Monthly business KPI scorecard.
    month = YYYY-MM (default = current month).
    Returns 8 KPIs each with value, label, status (good/warning/danger), vs_last_month.
    """
    if not month:
        month = date.today().strftime("%Y-%m")

    # Compute previous month
    y, m = int(month[:4]), int(month[5:7])
    if m == 1:
        prev_month = f"{y-1}-12"
    else:
        prev_month = f"{y}-{m-1:02d}"

    conn = get_db_conn()
    try:
        def q(sql: str, params: tuple):
            rows = _rows_to_dicts(conn, sql, params)
            return rows[0] if rows else {}

        B = branch

        # ── 1. Revenue this month vs last ─────────────────────
        rev_sql = """
            SELECT
                SUM(CASE WHEN TO_CHAR(entry_date,'YYYY-MM')=%s THEN amount ELSE 0 END) AS this_rev,
                SUM(CASE WHEN TO_CHAR(entry_date,'YYYY-MM')=%s THEN amount ELSE 0 END) AS prev_rev
            FROM public.v_daybook
            WHERE direction='income'
              AND TO_CHAR(entry_date,'YYYY-MM') IN (%s,%s)
              AND (%s='' OR branch_code=%s)
        """
        rev = q(rev_sql, (month, prev_month, month, prev_month, B, B))
        this_rev  = float(rev.get("this_rev") or 0)
        prev_rev  = float(rev.get("prev_rev") or 0)
        rev_delta = round((this_rev - prev_rev) / prev_rev * 100, 1) if prev_rev else 0

        # ── 2. Total Expenses ─────────────────────────────────
        exp_sql = """
            SELECT
                SUM(CASE WHEN TO_CHAR(entry_date,'YYYY-MM')=%s THEN amount ELSE 0 END) AS this_exp,
                SUM(CASE WHEN TO_CHAR(entry_date,'YYYY-MM')=%s THEN amount ELSE 0 END) AS prev_exp
            FROM public.v_daybook
            WHERE direction='expense'
              AND TO_CHAR(entry_date,'YYYY-MM') IN (%s,%s)
              AND (%s='' OR branch_code=%s)
        """
        exp = q(exp_sql, (month, prev_month, month, prev_month, B, B))
        this_exp  = float(exp.get("this_exp") or 0)
        prev_exp  = float(exp.get("prev_exp") or 0)

        # ── 3. Net Profit ─────────────────────────────────────
        net_profit = this_rev - this_exp
        prev_net   = prev_rev - prev_exp
        net_margin = round(net_profit / this_rev * 100, 1) if this_rev else 0
        net_delta  = round((net_profit - prev_net) / abs(prev_net) * 100, 1) if prev_net else 0

        # ── 4. Food Cost % ────────────────────────────────────
        fc_sql = """
            SELECT COALESCE(SUM(amount),0) AS food_cost
            FROM public.v_daybook
            WHERE direction='expense'
              AND category_code IN ('food_cost','raw_meat','raw_veggies','raw_seasoning','raw_oil_gas','raw_beverage')
              AND TO_CHAR(entry_date,'YYYY-MM')=%s
              AND (%s='' OR branch_code=%s)
        """
        fc = q(fc_sql, (month, B, B))
        food_cost    = float(fc.get("food_cost") or 0)
        food_cost_pct = round(food_cost / this_rev * 100, 1) if this_rev else 0

        # ── 5. Budget Compliance ──────────────────────────────
        budget_sql = """
            SELECT
                COUNT(*) AS total_cats,
                SUM(CASE WHEN COALESCE(act.actual,0) <= bt.amount THEN 1 ELSE 0 END) AS ok_cats
            FROM public.budget_targets bt
            LEFT JOIN (
                SELECT COALESCE(category_code,'other') AS category_code, SUM(amount) AS actual
                FROM public.v_daybook
                WHERE direction='expense' AND TO_CHAR(entry_date,'YYYY-MM')=%s
                  AND (%s='' OR branch_code=%s)
                GROUP BY 1
            ) act USING (category_code)
            WHERE bt.month=%s AND (%s='' OR bt.branch_code=%s)
        """
        budget = q(budget_sql, (month, B, B, month, B, B))
        total_cats = int(budget.get("total_cats") or 0)
        ok_cats    = int(budget.get("ok_cats") or 0)
        budget_pct = round(ok_cats / total_cats * 100) if total_cats else 100

        # ── 6. Delivery Revenue % ─────────────────────────────
        del_sql = """
            SELECT COALESCE(SUM(amount),0) AS delivery_rev
            FROM public.v_daybook
            WHERE direction='income'
              AND source IN ('rider_income_grab','rider_income_lineman')
              AND TO_CHAR(entry_date,'YYYY-MM')=%s
              AND (%s='' OR branch_code=%s)
        """
        del_rev = float((q(del_sql, (month, B, B))).get("delivery_rev") or 0)
        delivery_pct = round(del_rev / this_rev * 100, 1) if this_rev else 0

        # ── 7. AP Overdue count ───────────────────────────────
        ap_sql = """
            SELECT COUNT(*) AS overdue_count, COALESCE(SUM(amount),0) AS overdue_total
            FROM public.vendor_bills
            WHERE payment_status='unpaid' AND review_status='confirmed'
              AND due_date < CURRENT_DATE
              AND due_date IS NOT NULL
              AND (%s='' OR branch_code=%s)
        """
        ap = q(ap_sql, (B, B))
        ap_overdue_count = int(ap.get("overdue_count") or 0)
        ap_overdue_total = float(ap.get("overdue_total") or 0)

        # ── 8. Top Expense Category ───────────────────────────
        top_exp_sql = """
            SELECT category_code, SUM(amount) AS total
            FROM public.v_daybook
            WHERE direction='expense' AND TO_CHAR(entry_date,'YYYY-MM')=%s
              AND (%s='' OR branch_code=%s)
              AND category_code IS NOT NULL
            GROUP BY category_code ORDER BY total DESC LIMIT 1
        """
        top_exp = q(top_exp_sql, (month, B, B))

        # ── Build scorecard ───────────────────────────────────
        kpis = [
            {
                "key":      "revenue",
                "label":    "รายรับรวม",
                "value":    this_rev,
                "display":  f"฿{this_rev:,.0f}",
                "vs_prev":  rev_delta,
                "sub":      f"{'▲' if rev_delta>=0 else '▼'} {abs(rev_delta):.1f}% จากเดือนก่อน",
                "status":   _score_status(rev_delta, 0, -10, higher_is_better=True),
                "unit":     "บาท",
                "link":     "/revenue",
            },
            {
                "key":      "net_profit",
                "label":    "กำไรสุทธิ",
                "value":    net_profit,
                "display":  f"฿{net_profit:,.0f}",
                "vs_prev":  net_delta,
                "sub":      f"Margin {net_margin:.1f}%",
                "status":   _score_status(net_margin, 15, 5, higher_is_better=True),
                "unit":     "บาท",
                "link":     "/pnl",
            },
            {
                "key":      "food_cost",
                "label":    "Food Cost %",
                "value":    food_cost_pct,
                "display":  f"{food_cost_pct:.1f}%",
                "vs_prev":  None,
                "sub":      f"฿{food_cost:,.0f} จากรายรับ",
                "status":   _score_status(food_cost_pct, 30, 40, higher_is_better=False),
                "unit":     "%",
                "link":     "/dashboard",
            },
            {
                "key":      "budget",
                "label":    "Budget ตามแผน",
                "value":    budget_pct,
                "display":  f"{ok_cats}/{total_cats} หมวด" if total_cats else "ไม่มีงบ",
                "vs_prev":  None,
                "sub":      f"อยู่ในงบ {budget_pct}%",
                "status":   _score_status(budget_pct, 80, 50, higher_is_better=True) if total_cats else "info",
                "unit":     "%",
                "link":     "/budget",
            },
            {
                "key":      "expenses",
                "label":    "รายจ่ายรวม",
                "value":    this_exp,
                "display":  f"฿{this_exp:,.0f}",
                "vs_prev":  round((this_exp-prev_exp)/prev_exp*100,1) if prev_exp else 0,
                "sub":      f"{round(this_exp/this_rev*100,1):.1f}% ของรายรับ" if this_rev else "-",
                "status":   _score_status(this_exp/this_rev if this_rev else 1, 0.7, 0.85, higher_is_better=False),
                "unit":     "บาท",
                "link":     "/expense-trends",
            },
            {
                "key":      "delivery",
                "label":    "สัดส่วน Delivery",
                "value":    delivery_pct,
                "display":  f"{delivery_pct:.1f}%",
                "vs_prev":  None,
                "sub":      f"฿{del_rev:,.0f} (Grab + LINE MAN)",
                "status":   "good" if delivery_pct > 0 else "info",
                "unit":     "%",
                "link":     "/delivery",
            },
            {
                "key":      "ap_overdue",
                "label":    "บิลค้างจ่าย",
                "value":    ap_overdue_count,
                "display":  f"{ap_overdue_count} บิล",
                "vs_prev":  None,
                "sub":      f"฿{ap_overdue_total:,.0f} รวม",
                "status":   "danger" if ap_overdue_count > 3 else "warning" if ap_overdue_count > 0 else "good",
                "unit":     "บิล",
                "link":     "/bills/payment",
            },
            {
                "key":      "top_expense",
                "label":    "หมวดรายจ่ายสูงสุด",
                "value":    float(top_exp.get("total") or 0),
                "display":  top_exp.get("category_code") or "-",
                "vs_prev":  None,
                "sub":      f"฿{float(top_exp.get('total') or 0):,.0f}" if top_exp else "-",
                "status":   "info",
                "unit":     "",
                "link":     "/expense-trends",
            },
        ]

        # Overall score = count of good/total (excluding info)
        scored = [k for k in kpis if k["status"] in ("good","warning","danger")]
        good_count = sum(1 for k in scored if k["status"] == "good")
        overall_pct = round(good_count / len(scored) * 100) if scored else 0

        return {
            "month":        month,
            "prev_month":   prev_month,
            "kpis":         kpis,
            "overall_score": overall_pct,
            "good_count":   good_count,
            "total_scored": len(scored),
        }
    finally:
        conn.close()

# ─────────────────────────────────────────────────────────────────────────────
# Phase 49 — Menu Engineering Matrix
# GET /pos/menu-engineering?months=3&branch=thawi_watthana&min_orders=3
# Classifies menu items into Star / Plowhorse / Puzzle / Dog quadrants
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/pos/menu-engineering")
def pos_menu_engineering(
    months: int = Query(3, ge=1, le=12),
    branch: str = Query("thawi_watthana"),
    min_orders: int = Query(3, ge=1),
):
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            end   = date.today().replace(day=1)
            start = end - relativedelta(months=months)

            # ── Check item-level data exists ──────────────────────────────
            cur.execute(
                """SELECT COUNT(*) AS cnt
                   FROM public.pos_sales_items si
                   JOIN public.pos_bills pb ON pb.id = si.bill_id
                   WHERE pb.branch_code = %s
                     AND pb.sales_date >= %s AND pb.sales_date < %s""",
                (branch, start, end),
            )
            if cur.fetchone()[0] == 0:
                return {
                    "has_data": False,
                    "message": "ไม่มีข้อมูล item-level — ต้อง upload FoodStory Type 7 (bill detail) ก่อน",
                    "items": [], "summary": {}, "period": {"months": months, "start": str(start), "end": str(end)},
                }

            # ── Pull all items with popularity & revenue stats ─────────────
            cur.execute(
                """SELECT
                       si.item_name,
                       COALESCE(si.category, 'ไม่ระบุหมวด') AS category,
                       SUM(si.qty)::numeric                  AS total_qty,
                       SUM(si.net_amount)::numeric           AS total_revenue,
                       COUNT(DISTINCT si.bill_id)            AS order_count,
                       AVG(si.unit_price)::numeric           AS avg_price
                   FROM public.pos_sales_items si
                   JOIN public.pos_bills pb ON pb.id = si.bill_id
                   WHERE pb.branch_code = %s
                     AND pb.sales_date >= %s
                     AND pb.sales_date < %s
                     AND si.item_name IS NOT NULL
                     AND si.item_name <> ''
                   GROUP BY si.item_name, si.category
                   HAVING COUNT(DISTINCT si.bill_id) >= %s
                   ORDER BY total_revenue DESC""",
                (branch, start, end, min_orders),
            )
            rows = _rows_to_dicts(cur)

        if not rows:
            return {
                "has_data": False,
                "message": f"ไม่มี item ที่มี order >= {min_orders} ในช่วง {months} เดือน",
                "items": [], "summary": {}, "period": {"months": months, "start": str(start), "end": str(end)},
            }

        # ── Calculate popularity index & revenue index ────────────────────
        total_items    = len(rows)
        avg_qty        = sum(float(r["total_qty"])     for r in rows) / total_items
        avg_revenue    = sum(float(r["total_revenue"]) for r in rows) / total_items

        def _classify(qty, revenue):
            high_pop = float(qty)     >= avg_qty
            high_rev = float(revenue) >= avg_revenue
            if high_pop and high_rev:     return "star"
            if high_pop and not high_rev: return "plowhorse"
            if not high_pop and high_rev: return "puzzle"
            return "dog"

        _QUAD_META = {
            "star":       {"label": "⭐ Star",       "label_th": "เมนูเด่น",      "color": "#22c55e", "action": "รักษาคุณภาพ + โปรโมต"},
            "plowhorse":  {"label": "🐄 Plowhorse",  "label_th": "ขายดีแต่กำไรน้อย", "color": "#3b82f6", "action": "พิจารณาขึ้นราคาหรือลดต้นทุน"},
            "puzzle":     {"label": "❓ Puzzle",      "label_th": "กำไรดีแต่ขายน้อย", "color": "#f59e0b", "action": "โปรโมตให้มากขึ้น"},
            "dog":        {"label": "🐕 Dog",         "label_th": "ขายน้อยกำไรน้อย", "color": "#ef4444", "action": "พิจารณาตัดเมนูออก"},
        }

        items = []
        counts = {"star": 0, "plowhorse": 0, "puzzle": 0, "dog": 0}
        revenue_by_quad = {"star": 0.0, "plowhorse": 0.0, "puzzle": 0.0, "dog": 0.0}

        grand_total_revenue = sum(float(r["total_revenue"]) for r in rows)

        for r in rows:
            quad = _classify(r["total_qty"], r["total_revenue"])
            rev  = float(r["total_revenue"])
            counts[quad] += 1
            revenue_by_quad[quad] += rev
            pct_total = round(rev / grand_total_revenue * 100, 1) if grand_total_revenue else 0
            pop_index = round(float(r["total_qty"]) / avg_qty, 2)
            rev_index = round(rev / avg_revenue, 2)
            items.append({
                "item_name":    r["item_name"],
                "category":     r["category"],
                "quadrant":     quad,
                "total_qty":    float(r["total_qty"]),
                "total_revenue": rev,
                "order_count":  int(r["order_count"]),
                "avg_price":    round(float(r["avg_price"]), 2),
                "pct_total":    pct_total,
                "popularity_index": pop_index,
                "revenue_index":    rev_index,
                **_QUAD_META[quad],
            })

        summary = {
            "total_items": total_items,
            "avg_qty":     round(avg_qty, 1),
            "avg_revenue": round(avg_revenue, 1),
            "grand_total_revenue": round(grand_total_revenue, 2),
            "quadrants": {
                q: {
                    "count":   counts[q],
                    "revenue": round(revenue_by_quad[q], 2),
                    "pct":     round(revenue_by_quad[q] / grand_total_revenue * 100, 1) if grand_total_revenue else 0,
                    **_QUAD_META[q],
                }
                for q in ["star", "plowhorse", "puzzle", "dog"]
            },
        }

        return {
            "has_data": True,
            "items":    items,
            "summary":  summary,
            "period":   {"months": months, "start": str(start), "end": str(end)},
        }

    finally:
        conn.close()

# ─────────────────────────────────────────────────────────────────────────────
# Phase 50 — Payment Method & Discount Analytics
# GET /pos/payments?months=3&branch=thawi_watthana
# ─────────────────────────────────────────────────────────────────────────────

_PAYMENT_LABEL = {
    "qr":           "QR Code",
    "qrcode":       "QR Code",
    "qr_code":      "QR Code",
    "cash":         "เงินสด",
    "credit":       "บัตรเครดิต",
    "credit_card":  "บัตรเครดิต",
    "creditcard":   "บัตรเครดิต",
    "transfer":     "โอนเงิน",
    "bank":         "โอนเงิน",
    "grab":         "Grab Pay",
    "lineman":      "LINE MAN",
    "line":         "LINE MAN",
    "voucher":      "Voucher",
    "coupon":       "Coupon",
    "member":       "Member Card",
}
_PAYMENT_COLOR = {
    "QR Code":      "#00b96b",
    "เงินสด":       "#3b82f6",
    "บัตรเครดิต":  "#f59e0b",
    "โอนเงิน":      "#8b5cf6",
    "Grab Pay":     "#00b14f",
    "LINE MAN":     "#ffc800",
    "Voucher":      "#ec4899",
    "Coupon":       "#f97316",
    "Member Card":  "#06b6d4",
    "อื่นๆ":        "#94a3b8",
}

def _normalize_payment(raw: str) -> str:
    if not raw:
        return "อื่นๆ"
    key = raw.strip().lower().replace(" ", "_").replace("-", "_")
    for k, v in _PAYMENT_LABEL.items():
        if k in key:
            return v
    return raw.strip() or "อื่นๆ"

@router.get("/pos/payments")
def pos_payments(
    months: int = Query(3, ge=1, le=12),
    branch: str = Query("thawi_watthana"),
):
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            end   = date.today().replace(day=1)
            start = end - relativedelta(months=months)

            # ── Payment method breakdown ──────────────────────────────────
            cur.execute(
                """SELECT
                       payment_type_raw,
                       COUNT(*)                  AS bill_count,
                       SUM(net_total)::numeric   AS total_revenue,
                       AVG(net_total)::numeric   AS avg_bill
                   FROM public.pos_bills
                   WHERE branch_code = %s
                     AND sales_date >= %s
                     AND sales_date < %s
                     AND payment_type_raw IS NOT NULL
                   GROUP BY payment_type_raw
                   ORDER BY total_revenue DESC""",
                (branch, start, end),
            )
            pay_rows = _rows_to_dicts(cur)

            # aggregate by normalized label
            pay_agg: dict = {}
            for r in pay_rows:
                label = _normalize_payment(r["payment_type_raw"])
                if label not in pay_agg:
                    pay_agg[label] = {"bill_count": 0, "total_revenue": 0.0}
                pay_agg[label]["bill_count"]    += int(r["bill_count"])
                pay_agg[label]["total_revenue"] += float(r["total_revenue"] or 0)

            grand_pay_rev = sum(v["total_revenue"] for v in pay_agg.values()) or 1
            payment_methods = sorted(
                [
                    {
                        "label":         lbl,
                        "bill_count":    v["bill_count"],
                        "total_revenue": round(v["total_revenue"], 2),
                        "avg_bill":      round(v["total_revenue"] / v["bill_count"], 2) if v["bill_count"] else 0,
                        "pct":           round(v["total_revenue"] / grand_pay_rev * 100, 1),
                        "color":         _PAYMENT_COLOR.get(lbl, "#94a3b8"),
                    }
                    for lbl, v in pay_agg.items()
                ],
                key=lambda x: -x["total_revenue"],
            )

            # ── Monthly payment trend ─────────────────────────────────────
            cur.execute(
                """SELECT
                       TO_CHAR(sales_date, 'YYYY-MM') AS month,
                       payment_type_raw,
                       SUM(net_total)::numeric         AS total_revenue
                   FROM public.pos_bills
                   WHERE branch_code = %s
                     AND sales_date >= %s
                     AND sales_date < %s
                     AND payment_type_raw IS NOT NULL
                   GROUP BY 1, 2
                   ORDER BY 1""",
                (branch, start, end),
            )
            trend_rows = _rows_to_dicts(cur)

            months_set: dict = {}
            for r in trend_rows:
                mo = r["month"]
                lbl = _normalize_payment(r["payment_type_raw"])
                if mo not in months_set:
                    months_set[mo] = {}
                months_set[mo][lbl] = months_set[mo].get(lbl, 0) + float(r["total_revenue"] or 0)

            pay_trend = [
                {"month": mo, **{k: round(v, 2) for k, v in vals.items()}}
                for mo, vals in sorted(months_set.items())
            ]

            # ── Discount summary ──────────────────────────────────────────
            cur.execute(
                """SELECT
                       COUNT(*)                                              AS total_bills,
                       SUM(gross)::numeric                                   AS total_gross,
                       SUM(item_discount + bill_discount)::numeric           AS total_discount,
                       SUM(net_total)::numeric                               AS total_net,
                       COUNT(*) FILTER (
                           WHERE (item_discount + bill_discount) > 0
                       )                                                      AS bills_with_discount,
                       AVG(item_discount + bill_discount) FILTER (
                           WHERE (item_discount + bill_discount) > 0
                       )::numeric                                             AS avg_discount_when_given
                   FROM public.pos_bills
                   WHERE branch_code = %s
                     AND sales_date >= %s
                     AND sales_date < %s""",
                (branch, start, end),
            )
            ds = _rows_to_dicts(cur)[0]
            total_gross    = float(ds["total_gross"]    or 0)
            total_discount = float(ds["total_discount"] or 0)
            discount_summary = {
                "total_bills":          int(ds["total_bills"] or 0),
                "total_gross":          round(total_gross, 2),
                "total_discount":       round(total_discount, 2),
                "total_net":            round(float(ds["total_net"] or 0), 2),
                "bills_with_discount":  int(ds["bills_with_discount"] or 0),
                "avg_discount_when_given": round(float(ds["avg_discount_when_given"] or 0), 2),
                "discount_rate_pct":    round(total_discount / total_gross * 100, 2) if total_gross else 0,
                "pct_bills_discounted": round(
                    int(ds["bills_with_discount"] or 0) / int(ds["total_bills"] or 1) * 100, 1
                ),
            }

            # ── Monthly discount trend ────────────────────────────────────
            cur.execute(
                """SELECT
                       TO_CHAR(sales_date, 'YYYY-MM')            AS month,
                       SUM(gross)::numeric                        AS gross,
                       SUM(item_discount + bill_discount)::numeric AS discount,
                       SUM(net_total)::numeric                    AS net,
                       COUNT(*) FILTER (
                           WHERE (item_discount + bill_discount) > 0
                       )                                          AS bills_discounted,
                       COUNT(*)                                   AS total_bills
                   FROM public.pos_bills
                   WHERE branch_code = %s
                     AND sales_date >= %s
                     AND sales_date < %s
                   GROUP BY 1
                   ORDER BY 1""",
                (branch, start, end),
            )
            disc_trend = []
            for r in _rows_to_dicts(cur):
                g = float(r["gross"] or 0)
                d = float(r["discount"] or 0)
                disc_trend.append({
                    "month":            r["month"],
                    "gross":            round(g, 2),
                    "discount":         round(d, 2),
                    "net":              round(float(r["net"] or 0), 2),
                    "discount_rate":    round(d / g * 100, 2) if g else 0,
                    "bills_discounted": int(r["bills_discounted"] or 0),
                    "total_bills":      int(r["total_bills"] or 0),
                })

        return {
            "payment_methods":   payment_methods,
            "pay_trend":         pay_trend,
            "discount_summary":  discount_summary,
            "disc_trend":        disc_trend,
            "period":            {"months": months, "start": str(start), "end": str(end)},
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Phase 51 — Sales Heatmap  GET /pos/heatmap
# DOW (0=Sun … 6=Sat) × Hour (0–23) matrix — revenue + bill_count per cell
# ---------------------------------------------------------------------------
@router.get("/pos/heatmap")
def pos_heatmap(
    months: int = Query(3, ge=1, le=24),
    branch: str = Query(""),
):
    conn = get_db_conn()
    try:
        start = (date.today().replace(day=1) - relativedelta(months=months - 1))
        end   = date.today()

        branch_filter = "AND b.branch_code = %(branch)s" if branch else ""

        heatmap_sql = f"""
            SELECT
                EXTRACT(DOW  FROM b.sales_date)::int  AS dow,
                EXTRACT(HOUR FROM b.sales_time)::int  AS hr,
                SUM(b.net_total)::numeric              AS revenue,
                COUNT(*)::int                          AS bill_count
            FROM pos_bills b
            WHERE b.sales_date BETWEEN %(start)s AND %(end)s
              AND b.net_total IS NOT NULL
              {branch_filter}
            GROUP BY dow, hr
            ORDER BY dow, hr
        """

        params = {"start": start, "end": end}
        if branch:
            params["branch"] = branch

        with conn.cursor() as cur:
            cur.execute(heatmap_sql, params)
            rows = _rows_to_dicts(cur)

        # Build full 7×24 matrix initialised to zero
        matrix = {
            dow: {hr: {"revenue": 0.0, "bill_count": 0} for hr in range(24)}
            for dow in range(7)
        }
        for r in rows:
            d = int(r["dow"]); h = int(r["hr"])
            matrix[d][h] = {
                "revenue":    round(float(r["revenue"] or 0), 2),
                "bill_count": int(r["bill_count"] or 0),
            }

        # Flatten to list of cells for JSON
        cells = []
        max_rev = 0.0
        for dow in range(7):
            for hr in range(24):
                rev = matrix[dow][hr]["revenue"]
                bc  = matrix[dow][hr]["bill_count"]
                if rev > max_rev:
                    max_rev = rev
                cells.append({"dow": dow, "hr": hr, "revenue": rev, "bill_count": bc})

        # Peak cell
        peak = max(cells, key=lambda c: c["revenue"]) if cells else None

        # DOW aggregates (for sparkline row totals)
        dow_totals = []
        for dow in range(7):
            total_rev = sum(matrix[dow][hr]["revenue"] for hr in range(24))
            total_bc  = sum(matrix[dow][hr]["bill_count"] for hr in range(24))
            dow_totals.append({"dow": dow, "revenue": round(total_rev, 2), "bill_count": total_bc})

        # Hour aggregates
        hr_totals = []
        for hr in range(24):
            total_rev = sum(matrix[dow][hr]["revenue"] for dow in range(7))
            total_bc  = sum(matrix[dow][hr]["bill_count"] for dow in range(7))
            hr_totals.append({"hr": hr, "revenue": round(total_rev, 2), "bill_count": total_bc})

        return {
            "cells":      cells,
            "dow_totals": dow_totals,
            "hr_totals":  hr_totals,
            "max_revenue": round(max_rev, 2),
            "peak":       peak,
            "period":     {"months": months, "start": str(start), "end": str(end)},
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Phase 52 — Item Sales Trend  GET /pos/items
# Per-menu-item: revenue, qty, avg_price, MoM delta, 6-month sparkline
# ---------------------------------------------------------------------------
@router.get("/pos/items")
def pos_item_trend(
    months:   int   = Query(6,  ge=1, le=24),
    branch:   str   = Query(""),
    category: str   = Query(""),
    limit:    int   = Query(50, ge=1, le=200),
):
    conn = get_db_conn()
    try:
        end        = date.today()
        start      = (end.replace(day=1) - relativedelta(months=months - 1))
        # previous period (same length) for MoM comparison
        prev_end   = start - timedelta(days=1)
        prev_start = (prev_end.replace(day=1) - relativedelta(months=months - 1))

        branch_filter   = "AND b.branch_code = %(branch)s"   if branch   else ""
        category_filter = "AND si.category   = %(category)s" if category else ""

        params: dict = {"start": start, "end": end, "prev_start": prev_start, "prev_end": prev_end, "limit": limit}
        if branch:   params["branch"]   = branch
        if category: params["category"] = category

        # Current-period aggregates per item
        cur_sql = f"""
            SELECT
                si.item_name,
                si.category,
                SUM(si.qty)::numeric           AS total_qty,
                SUM(si.net_amount)::numeric    AS total_revenue,
                AVG(si.unit_price)::numeric    AS avg_price,
                COUNT(DISTINCT si.bill_id)::int AS bill_count
            FROM pos_sales_items si
            JOIN pos_bills b ON b.id = si.bill_id
            WHERE b.sales_date BETWEEN %(start)s AND %(end)s
              {branch_filter}
              {category_filter}
            GROUP BY si.item_name, si.category
            ORDER BY total_revenue DESC
            LIMIT %(limit)s
        """

        # Previous-period totals (same items only)
        prev_sql = f"""
            SELECT
                si.item_name,
                SUM(si.net_amount)::numeric AS prev_revenue,
                SUM(si.qty)::numeric        AS prev_qty
            FROM pos_sales_items si
            JOIN pos_bills b ON b.id = si.bill_id
            WHERE b.sales_date BETWEEN %(prev_start)s AND %(prev_end)s
              {branch_filter}
              {category_filter}
            GROUP BY si.item_name
        """

        # Monthly sparkline: last 6 months per item (top 50 only — joined after)
        sparkline_sql = f"""
            SELECT
                si.item_name,
                TO_CHAR(DATE_TRUNC('month', b.sales_date), 'YYYY-MM') AS month,
                SUM(si.net_amount)::numeric AS revenue
            FROM pos_sales_items si
            JOIN pos_bills b ON b.id = si.bill_id
            WHERE b.sales_date BETWEEN %(start)s AND %(end)s
              {branch_filter}
              {category_filter}
            GROUP BY si.item_name, month
        """

        # Categories list
        cat_sql = f"""
            SELECT DISTINCT si.category
            FROM pos_sales_items si
            JOIN pos_bills b ON b.id = si.bill_id
            WHERE b.sales_date BETWEEN %(start)s AND %(end)s
              {branch_filter}
            ORDER BY si.category
        """

        with conn.cursor() as cur:
            cur.execute(cur_sql, params)
            cur_rows = _rows_to_dicts(cur)

            cur.execute(prev_sql, params)
            prev_rows = _rows_to_dicts(cur)

            cur.execute(sparkline_sql, params)
            spark_rows = _rows_to_dicts(cur)

            cur.execute(cat_sql, params)
            cat_rows = _rows_to_dicts(cur)

        # Build prev lookup
        prev_map: dict = {r["item_name"]: r for r in prev_rows}

        # Build sparkline lookup: item → {month: revenue}
        spark_map: dict = {}
        for r in spark_rows:
            spark_map.setdefault(r["item_name"], {})[r["month"]] = float(r["revenue"] or 0)

        # Build month list (last N months in order)
        month_labels = []
        cur_m = start.replace(day=1)
        while cur_m <= end.replace(day=1):
            month_labels.append(cur_m.strftime("%Y-%m"))
            cur_m = (cur_m + relativedelta(months=1)).replace(day=1)

        # Assemble items
        items = []
        total_rev = sum(float(r["total_revenue"] or 0) for r in cur_rows)
        for r in cur_rows:
            name      = r["item_name"]
            cur_rev   = float(r["total_revenue"] or 0)
            prev_rev  = float(prev_map.get(name, {}).get("prev_revenue") or 0)
            cur_qty   = float(r["total_qty"] or 0)
            prev_qty  = float(prev_map.get(name, {}).get("prev_qty") or 0)

            if prev_rev > 0:
                mom_delta = round((cur_rev - prev_rev) / prev_rev * 100, 1)
            else:
                mom_delta = None  # new item

            if mom_delta is None:
                trend = "new"
            elif mom_delta >= 5:
                trend = "up"
            elif mom_delta <= -5:
                trend = "down"
            else:
                trend = "stable"

            sparkline = [
                {"month": m, "revenue": round(spark_map.get(name, {}).get(m, 0), 2)}
                for m in month_labels
            ]

            items.append({
                "item_name":    name,
                "category":     r["category"],
                "total_qty":    round(cur_qty, 1),
                "total_revenue": round(cur_rev, 2),
                "avg_price":    round(float(r["avg_price"] or 0), 2),
                "bill_count":   int(r["bill_count"] or 0),
                "revenue_pct":  round(cur_rev / total_rev * 100, 2) if total_rev else 0,
                "prev_revenue": round(prev_rev, 2),
                "mom_delta":    mom_delta,
                "trend":        trend,
                "sparkline":    sparkline,
            })

        categories = [r["category"] for r in cat_rows if r["category"]]

        return {
            "items":        items,
            "categories":   categories,
            "month_labels": month_labels,
            "total_revenue": round(total_rev, 2),
            "period":       {"months": months, "start": str(start), "end": str(end)},
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Phase 53 — Bill Size Analysis  GET /pos/bill-analysis
# Avg check, histogram buckets, DOW avg, order-type avg, monthly trend
# ---------------------------------------------------------------------------
@router.get("/pos/bill-analysis")
def pos_bill_analysis(
    months: int = Query(3, ge=1, le=24),
    branch: str = Query(""),
):
    conn = get_db_conn()
    try:
        end   = date.today()
        start = (end.replace(day=1) - relativedelta(months=months - 1))

        branch_filter = "AND branch_code = %(branch)s" if branch else ""
        params: dict  = {"start": start, "end": end}
        if branch:
            params["branch"] = branch

        # Overall KPIs
        kpi_sql = f"""
            SELECT
                COUNT(*)::int                        AS total_bills,
                AVG(net_total)::numeric              AS avg_bill,
                MIN(net_total)::numeric              AS min_bill,
                MAX(net_total)::numeric              AS max_bill,
                PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY net_total)::numeric AS median_bill,
                SUM(net_total)::numeric              AS total_revenue
            FROM pos_bills
            WHERE sales_date BETWEEN %(start)s AND %(end)s
              AND net_total > 0
              {branch_filter}
        """

        # Histogram — 10 buckets (0-99, 100-199, ..., 900+)
        hist_sql = f"""
            SELECT
                FLOOR(net_total / 100) * 100 AS bucket_start,
                COUNT(*)::int                AS bill_count
            FROM pos_bills
            WHERE sales_date BETWEEN %(start)s AND %(end)s
              AND net_total >= 0 AND net_total < 10000
              {branch_filter}
            GROUP BY bucket_start
            ORDER BY bucket_start
        """

        # DOW avg
        dow_sql = f"""
            SELECT
                EXTRACT(DOW FROM sales_date)::int AS dow,
                AVG(net_total)::numeric           AS avg_bill,
                COUNT(*)::int                     AS bill_count
            FROM pos_bills
            WHERE sales_date BETWEEN %(start)s AND %(end)s
              AND net_total > 0
              {branch_filter}
            GROUP BY dow
            ORDER BY dow
        """

        # Order type avg
        otype_sql = f"""
            SELECT
                COALESCE(NULLIF(TRIM(order_type), ''), 'ไม่ระบุ') AS order_type,
                AVG(net_total)::numeric  AS avg_bill,
                COUNT(*)::int            AS bill_count,
                SUM(net_total)::numeric  AS total_revenue
            FROM pos_bills
            WHERE sales_date BETWEEN %(start)s AND %(end)s
              AND net_total > 0
              {branch_filter}
            GROUP BY order_type
            ORDER BY total_revenue DESC
        """

        # Monthly trend — avg + bill_count
        trend_sql = f"""
            SELECT
                TO_CHAR(DATE_TRUNC('month', sales_date), 'YYYY-MM') AS month,
                AVG(net_total)::numeric  AS avg_bill,
                COUNT(*)::int            AS bill_count,
                SUM(net_total)::numeric  AS total_revenue
            FROM pos_bills
            WHERE sales_date BETWEEN %(start)s AND %(end)s
              AND net_total > 0
              {branch_filter}
            GROUP BY month
            ORDER BY month
        """

        with conn.cursor() as cur:
            cur.execute(kpi_sql,   params); kpi_rows   = _rows_to_dicts(cur)
            cur.execute(hist_sql,  params); hist_rows  = _rows_to_dicts(cur)
            cur.execute(dow_sql,   params); dow_rows   = _rows_to_dicts(cur)
            cur.execute(otype_sql, params); otype_rows = _rows_to_dicts(cur)
            cur.execute(trend_sql, params); trend_rows = _rows_to_dicts(cur)

        kpi = kpi_rows[0] if kpi_rows else {}

        # Fill histogram with zero buckets for readability (0–1900)
        hist_map = {int(r["bucket_start"]): int(r["bill_count"]) for r in hist_rows}
        max_bucket = max(hist_map.keys(), default=0)
        histogram = []
        b = 0
        while b <= max(max_bucket, 900):
            histogram.append({
                "label":      f"{b}–{b+99}",
                "bucket":     b,
                "bill_count": hist_map.get(b, 0),
            })
            b += 100

        dow_list = [
            {
                "dow":        int(r["dow"]),
                "avg_bill":   round(float(r["avg_bill"] or 0), 2),
                "bill_count": int(r["bill_count"] or 0),
            }
            for r in dow_rows
        ]

        otype_list = [
            {
                "order_type":    r["order_type"],
                "avg_bill":      round(float(r["avg_bill"] or 0), 2),
                "bill_count":    int(r["bill_count"] or 0),
                "total_revenue": round(float(r["total_revenue"] or 0), 2),
            }
            for r in otype_rows
        ]

        trend_list = [
            {
                "month":         r["month"],
                "avg_bill":      round(float(r["avg_bill"] or 0), 2),
                "bill_count":    int(r["bill_count"] or 0),
                "total_revenue": round(float(r["total_revenue"] or 0), 2),
            }
            for r in trend_rows
        ]

        return {
            "kpi": {
                "total_bills":   int(kpi.get("total_bills") or 0),
                "avg_bill":      round(float(kpi.get("avg_bill") or 0), 2),
                "median_bill":   round(float(kpi.get("median_bill") or 0), 2),
                "min_bill":      round(float(kpi.get("min_bill") or 0), 2),
                "max_bill":      round(float(kpi.get("max_bill") or 0), 2),
                "total_revenue": round(float(kpi.get("total_revenue") or 0), 2),
            },
            "histogram":  histogram,
            "dow_avg":    dow_list,
            "order_types": otype_list,
            "trend":      trend_list,
            "period":     {"months": months, "start": str(start), "end": str(end)},
        }
    finally:
        conn.close()


# ──────────────────────────────────────────────────────────────────────────────
# Phase 54 — Category Sales Mix & Trend  GET /pos/categories
# ──────────────────────────────────────────────────────────────────────────────
@router.get("/pos/categories")
def pos_categories(
    months: int = Query(3),
    branch: str = Query(""),
):
    from datetime import date
    from dateutil.relativedelta import relativedelta

    conn = get_db_conn()
    try:
        end   = date.today()
        start = end - relativedelta(months=months)

        branch_sql = "AND b.branch_code = %(branch)s" if branch else ""
        params = {"start": start, "end": end, "branch": branch or ""}

        with conn.cursor() as cur:
            # ── 1. Category totals ──────────────────────────────────────────
            cur.execute(f"""
                SELECT
                    COALESCE(NULLIF(TRIM(si.category), ''), 'ไม่ระบุ') AS category,
                    SUM(si.net_amount)   AS total_revenue,
                    SUM(si.qty)          AS total_qty,
                    COUNT(DISTINCT si.bill_id) AS bill_count,
                    COUNT(*)             AS item_lines
                FROM pos_sales_items si
                JOIN pos_bills b ON b.id = si.bill_id
                WHERE b.sales_date BETWEEN %(start)s AND %(end)s
                  {branch_sql}
                GROUP BY 1
                ORDER BY total_revenue DESC
            """, params)
            cat_rows = _rows_to_dicts(cur)

            total_rev = sum(float(r["total_revenue"] or 0) for r in cat_rows) or 1

            # ── 2. Monthly trend per category (last N months) ───────────────
            cur.execute(f"""
                SELECT
                    TO_CHAR(b.sales_date, 'YYYY-MM') AS month,
                    COALESCE(NULLIF(TRIM(si.category), ''), 'ไม่ระบุ') AS category,
                    SUM(si.net_amount)  AS revenue,
                    SUM(si.qty)         AS qty
                FROM pos_sales_items si
                JOIN pos_bills b ON b.id = si.bill_id
                WHERE b.sales_date BETWEEN %(start)s AND %(end)s
                  {branch_sql}
                GROUP BY 1, 2
                ORDER BY 1, revenue DESC
            """, params)
            trend_rows = _rows_to_dicts(cur)

            # ── 3. Top 5 items per category ─────────────────────────────────
            cur.execute(f"""
                SELECT
                    COALESCE(NULLIF(TRIM(si.category), ''), 'ไม่ระบุ') AS category,
                    si.item_name,
                    SUM(si.net_amount) AS revenue,
                    SUM(si.qty)        AS qty
                FROM pos_sales_items si
                JOIN pos_bills b ON b.id = si.bill_id
                WHERE b.sales_date BETWEEN %(start)s AND %(end)s
                  {branch_sql}
                GROUP BY 1, 2
                ORDER BY 1, revenue DESC
            """, params)
            item_rows = _rows_to_dicts(cur)

        # Build categories list
        categories = []
        for r in cat_rows:
            rev = float(r["total_revenue"] or 0)
            categories.append({
                "category":     r["category"],
                "total_revenue": round(rev, 2),
                "total_qty":    int(r["total_qty"] or 0),
                "bill_count":   int(r["bill_count"] or 0),
                "item_lines":   int(r["item_lines"] or 0),
                "revenue_pct":  round(rev / total_rev * 100, 1),
            })

        # Build monthly trend: {month: {cat: revenue}}
        months_set = sorted({r["month"] for r in trend_rows})
        cat_set    = [c["category"] for c in categories]  # ordered by revenue

        trend_by_month = []
        for m in months_set:
            row = {"month": m}
            for cat in cat_set:
                match = next((r for r in trend_rows if r["month"] == m and r["category"] == cat), None)
                row[cat] = round(float(match["revenue"]) if match else 0, 2)
            trend_by_month.append(row)

        # Build top items per category (top 5)
        top_items: dict = {}
        for r in item_rows:
            cat = r["category"]
            if cat not in top_items:
                top_items[cat] = []
            if len(top_items[cat]) < 5:
                top_items[cat].append({
                    "item_name": r["item_name"],
                    "revenue":   round(float(r["revenue"] or 0), 2),
                    "qty":       int(r["qty"] or 0),
                })

        return {
            "categories":    categories,
            "trend":         trend_by_month,
            "top_items":     top_items,
            "category_names": cat_set,
            "period": {"months": months, "start": str(start), "end": str(end)},
        }
    finally:
        conn.close()


# ──────────────────────────────────────────────────────────────────────────────
# Phase 55 — Daily Revenue Calendar  GET /pos/calendar
# ──────────────────────────────────────────────────────────────────────────────
@router.get("/pos/calendar")
def pos_calendar(
    year:   int = Query(None),
    month:  int = Query(None),
    branch: str = Query(""),
):
    import calendar
    from datetime import date

    today = date.today()
    if not year:  year  = today.year
    if not month: month = today.month

    # clamp month
    month = max(1, min(12, month))

    _, days_in_month = calendar.monthrange(year, month)
    start = date(year, month, 1)
    end   = date(year, month, days_in_month)

    branch_sql = "AND branch_code = %(branch)s" if branch else ""
    params = {"start": start, "end": end, "branch": branch or ""}

    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            # ── Daily totals ────────────────────────────────────────────────
            cur.execute(f"""
                SELECT
                    sales_date,
                    SUM(net_total)   AS revenue,
                    COUNT(*)         AS bill_count,
                    AVG(net_total)   AS avg_bill,
                    MAX(net_total)   AS max_bill
                FROM pos_bills
                WHERE sales_date BETWEEN %(start)s AND %(end)s
                  {branch_sql}
                GROUP BY sales_date
                ORDER BY sales_date
            """, params)
            day_rows = _rows_to_dicts(cur)

            # ── Month KPIs ──────────────────────────────────────────────────
            cur.execute(f"""
                SELECT
                    SUM(net_total)   AS total_revenue,
                    COUNT(*)         AS total_bills,
                    AVG(net_total)   AS avg_bill,
                    MAX(net_total)   AS max_bill,
                    MIN(net_total)   AS min_bill,
                    COUNT(DISTINCT sales_date) AS active_days
                FROM pos_bills
                WHERE sales_date BETWEEN %(start)s AND %(end)s
                  {branch_sql}
            """, params)
            kpi = _rows_to_dicts(cur)[0] if cur.rowcount else {}

            # ── Top items for the month ─────────────────────────────────────
            cur.execute(f"""
                SELECT
                    si.item_name,
                    SUM(si.qty)        AS qty,
                    SUM(si.net_amount) AS revenue
                FROM pos_sales_items si
                JOIN pos_bills b ON b.id = si.bill_id
                WHERE b.sales_date BETWEEN %(start)s AND %(end)s
                  {'AND b.branch_code = %(branch)s' if branch else ''}
                GROUP BY si.item_name
                ORDER BY revenue DESC
                LIMIT 5
            """, params)
            top_items = _rows_to_dicts(cur)

        # Build day map
        day_map = {str(r["sales_date"]): r for r in day_rows}

        days_out = []
        for d in range(1, days_in_month + 1):
            ds = str(date(year, month, d))
            r  = day_map.get(ds)
            days_out.append({
                "date":       ds,
                "day":        d,
                "weekday":    date(year, month, d).weekday(),  # 0=Mon
                "revenue":    round(float(r["revenue"]) if r else 0, 2),
                "bill_count": int(r["bill_count"]) if r else 0,
                "avg_bill":   round(float(r["avg_bill"]) if r else 0, 2),
                "max_bill":   round(float(r["max_bill"]) if r else 0, 2),
                "is_today":   ds == str(today),
            })

        total_rev = float(kpi.get("total_revenue") or 0)
        active    = int(kpi.get("active_days") or 1) or 1

        return {
            "year":   year,
            "month":  month,
            "days":   days_out,
            "kpi": {
                "total_revenue": round(total_rev, 2),
                "total_bills":   int(kpi.get("total_bills") or 0),
                "avg_bill":      round(float(kpi.get("avg_bill") or 0), 2),
                "max_bill":      round(float(kpi.get("max_bill") or 0), 2),
                "min_bill":      round(float(kpi.get("min_bill") or 0), 2),
                "active_days":   active,
                "daily_avg_rev": round(total_rev / active, 2),
            },
            "top_items": [
                {
                    "item_name": r["item_name"],
                    "qty":       int(r["qty"] or 0),
                    "revenue":   round(float(r["revenue"] or 0), 2),
                }
                for r in top_items
            ],
            "days_in_month": days_in_month,
        }
    finally:
        conn.close()


# ──────────────────────────────────────────────────────────────────────────────
# Phase 56 — Item Combo Analysis  GET /pos/combos
# ──────────────────────────────────────────────────────────────────────────────
@router.get("/pos/combos")
def pos_combos(
    months: int = Query(3),
    branch: str = Query(""),
    item:   str = Query(""),    # filter: show combos for this specific item
    limit:  int = Query(30),
):
    from datetime import date
    from dateutil.relativedelta import relativedelta

    conn = get_db_conn()
    try:
        end   = date.today()
        start = end - relativedelta(months=months)

        branch_sql_b = "AND b.branch_code = %(branch)s" if branch else ""
        params = {"start": start, "end": end, "branch": branch or "",
                  "limit": limit, "item": f"%{item}%"}

        with conn.cursor() as cur:
            # ── Total bills in period (for support calculation) ─────────────
            cur.execute(f"""
                SELECT COUNT(DISTINCT id) AS total_bills
                FROM pos_bills
                WHERE sales_date BETWEEN %(start)s AND %(end)s
                  {branch_sql_b}
            """, params)
            total_row = _rows_to_dicts(cur)
            total_bills = int(total_row[0]["total_bills"]) if total_row else 1

            # ── Individual item counts (for lift calculation) ───────────────
            cur.execute(f"""
                SELECT si.item_name, COUNT(DISTINCT si.bill_id) AS item_bills
                FROM pos_sales_items si
                JOIN pos_bills b ON b.id = si.bill_id
                WHERE b.sales_date BETWEEN %(start)s AND %(end)s
                  {branch_sql_b}
                GROUP BY si.item_name
            """, params)
            item_counts = {r["item_name"]: int(r["item_bills"]) for r in _rows_to_dicts(cur)}

            # ── Pair co-occurrence (self-join) ──────────────────────────────
            item_filter = "AND (a.item_name ILIKE %(item)s OR b.item_name ILIKE %(item)s)" if item else ""
            cur.execute(f"""
                SELECT
                    a.item_name   AS item_a,
                    b.item_name   AS item_b,
                    COUNT(DISTINCT a.bill_id) AS co_count
                FROM pos_sales_items a
                JOIN pos_sales_items b
                  ON  a.bill_id = b.bill_id
                  AND a.item_name < b.item_name
                JOIN pos_bills bl ON bl.id = a.bill_id
                WHERE bl.sales_date BETWEEN %(start)s AND %(end)s
                  {branch_sql_b.replace('b.branch_code', 'bl.branch_code')}
                  {item_filter}
                GROUP BY 1, 2
                HAVING COUNT(DISTINCT a.bill_id) >= 2
                ORDER BY co_count DESC
                LIMIT %(limit)s
            """, params)
            pair_rows = _rows_to_dicts(cur)

            # ── Top solo items (for context) ────────────────────────────────
            cur.execute(f"""
                SELECT si.item_name, COUNT(DISTINCT si.bill_id) AS bill_count,
                       SUM(si.net_amount) AS revenue
                FROM pos_sales_items si
                JOIN pos_bills b ON b.id = si.bill_id
                WHERE b.sales_date BETWEEN %(start)s AND %(end)s
                  {branch_sql_b}
                GROUP BY si.item_name
                ORDER BY bill_count DESC
                LIMIT 20
            """, params)
            top_items = _rows_to_dicts(cur)

        # Compute support & lift for each pair
        pairs_out = []
        for r in pair_rows:
            a, b_name = r["item_a"], r["item_b"]
            co = int(r["co_count"])
            cnt_a = item_counts.get(a, 1) or 1
            cnt_b = item_counts.get(b_name, 1) or 1
            support   = round(co / total_bills * 100, 1)
            # lift = P(A∩B) / (P(A) * P(B))
            lift = round((co / total_bills) / ((cnt_a / total_bills) * (cnt_b / total_bills)), 2)
            confidence_a = round(co / cnt_a * 100, 1)  # P(B|A)
            confidence_b = round(co / cnt_b * 100, 1)  # P(A|B)
            pairs_out.append({
                "item_a":        a,
                "item_b":        b_name,
                "co_count":      co,
                "support_pct":   support,
                "lift":          lift,
                "conf_a_to_b":   confidence_a,
                "conf_b_to_a":   confidence_b,
                "bills_a":       cnt_a,
                "bills_b":       cnt_b,
            })

        top_items_out = [
            {
                "item_name":  r["item_name"],
                "bill_count": int(r["bill_count"] or 0),
                "revenue":    round(float(r["revenue"] or 0), 2),
            }
            for r in top_items
        ]

        return {
            "pairs":       pairs_out,
            "top_items":   top_items_out,
            "total_bills": total_bills,
            "period":      {"months": months, "start": str(start), "end": str(end)},
        }
    finally:
        conn.close()


# ──────────────────────────────────────────────────────────────────────────────
# Phase 57 — Period Comparison  GET /pos/compare
# ──────────────────────────────────────────────────────────────────────────────
@router.get("/pos/compare")
def pos_compare(
    period_a: str = Query(...),   # YYYY-MM
    period_b: str = Query(...),   # YYYY-MM
    branch:   str = Query(""),
):
    """Compare two calendar months side-by-side."""
    import calendar
    from datetime import date

    def parse_period(p: str):
        """Return (start, end) for a YYYY-MM string."""
        try:
            y, m = int(p[:4]), int(p[5:7])
        except Exception:
            raise ValueError(f"Invalid period: {p}")
        _, last = calendar.monthrange(y, m)
        return date(y, m, 1), date(y, m, last)

    start_a, end_a = parse_period(period_a)
    start_b, end_b = parse_period(period_b)

    branch_sql = "AND b.branch_code = %(branch)s" if branch else ""

    def fetch_period(conn, start, end, branch_sql, branch):
        params = {"start": start, "end": end, "branch": branch or ""}
        with conn.cursor() as cur:
            # KPIs
            cur.execute(f"""
                SELECT
                    COUNT(*)            AS total_bills,
                    SUM(net_total)      AS total_revenue,
                    AVG(net_total)      AS avg_bill,
                    MAX(net_total)      AS max_bill,
                    PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY net_total) AS median_bill,
                    COUNT(DISTINCT sales_date) AS active_days
                FROM pos_bills b
                WHERE b.sales_date BETWEEN %(start)s AND %(end)s
                  {branch_sql}
            """, params)
            kpi = _rows_to_dicts(cur)[0]

            # Top 10 items
            cur.execute(f"""
                SELECT si.item_name, SUM(si.qty) AS qty, SUM(si.net_amount) AS revenue
                FROM pos_sales_items si
                JOIN pos_bills b ON b.id = si.bill_id
                WHERE b.sales_date BETWEEN %(start)s AND %(end)s
                  {branch_sql}
                GROUP BY si.item_name
                ORDER BY revenue DESC
                LIMIT 10
            """, params)
            top_items = _rows_to_dicts(cur)

            # Category mix
            cur.execute(f"""
                SELECT COALESCE(NULLIF(TRIM(si.category),''),'ไม่ระบุ') AS category,
                       SUM(si.net_amount) AS revenue
                FROM pos_sales_items si
                JOIN pos_bills b ON b.id = si.bill_id
                WHERE b.sales_date BETWEEN %(start)s AND %(end)s
                  {branch_sql}
                GROUP BY 1
                ORDER BY revenue DESC
                LIMIT 8
            """, params)
            cats = _rows_to_dicts(cur)

            # DOW avg revenue
            cur.execute(f"""
                SELECT EXTRACT(DOW FROM b.sales_date)::int AS dow,
                       AVG(b.net_total) AS avg_bill,
                       COUNT(*) AS bill_count
                FROM pos_bills b
                WHERE b.sales_date BETWEEN %(start)s AND %(end)s
                  {branch_sql}
                GROUP BY 1
                ORDER BY 1
            """, params)
            dow = _rows_to_dicts(cur)

            # Order type
            cur.execute(f"""
                SELECT COALESCE(NULLIF(TRIM(order_type),''),'ไม่ระบุ') AS order_type,
                       COUNT(*) AS bill_count, SUM(net_total) AS revenue
                FROM pos_bills b
                WHERE b.sales_date BETWEEN %(start)s AND %(end)s
                  {branch_sql}
                GROUP BY 1
                ORDER BY revenue DESC
            """, params)
            otypes = _rows_to_dicts(cur)

        total_rev = float(kpi.get("total_revenue") or 0)
        active = int(kpi.get("active_days") or 1) or 1
        return {
            "kpi": {
                "total_bills":   int(kpi.get("total_bills") or 0),
                "total_revenue": round(total_rev, 2),
                "avg_bill":      round(float(kpi.get("avg_bill") or 0), 2),
                "max_bill":      round(float(kpi.get("max_bill") or 0), 2),
                "median_bill":   round(float(kpi.get("median_bill") or 0), 2),
                "active_days":   active,
                "daily_avg_rev": round(total_rev / active, 2),
            },
            "top_items": [
                {"item_name": r["item_name"], "qty": int(r["qty"] or 0),
                 "revenue": round(float(r["revenue"] or 0), 2)}
                for r in top_items
            ],
            "categories": [
                {"category": r["category"], "revenue": round(float(r["revenue"] or 0), 2)}
                for r in cats
            ],
            "dow": [
                {"dow": r["dow"], "avg_bill": round(float(r["avg_bill"] or 0), 2),
                 "bill_count": int(r["bill_count"] or 0)}
                for r in dow
            ],
            "order_types": [
                {"order_type": r["order_type"], "bill_count": int(r["bill_count"] or 0),
                 "revenue": round(float(r["revenue"] or 0), 2)}
                for r in otypes
            ],
        }

    conn = get_db_conn()
    try:
        data_a = fetch_period(conn, start_a, end_a, branch_sql, branch)
        data_b = fetch_period(conn, start_b, end_b, branch_sql, branch)

        def delta(a, b):
            if a == 0: return None
            return round((b - a) / a * 100, 1)

        kpi_a, kpi_b = data_a["kpi"], data_b["kpi"]
        deltas = {
            k: delta(kpi_a[k], kpi_b[k])
            for k in ["total_revenue","total_bills","avg_bill","daily_avg_rev","median_bill"]
        }

        return {
            "period_a": {"label": period_a, "start": str(start_a), "end": str(end_a), **data_a},
            "period_b": {"label": period_b, "start": str(start_b), "end": str(end_b), **data_b},
            "deltas": deltas,
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Phase 58 — Daily Flash Report
# GET /pos/flash?date=YYYY-MM-DD&branch=
# ---------------------------------------------------------------------------
@router.get("/pos/flash")
def pos_flash(date: str = Query(None), branch: str = Query("")):
    from datetime import date as date_type, timedelta
    import calendar as cal_mod

    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            # Resolve target date
            if date:
                try:
                    target = date_type.fromisoformat(date)
                except Exception:
                    target = date_type.today()
            else:
                target = date_type.today()

            last_week  = target - timedelta(days=7)
            last_month = target.replace(day=1) - timedelta(days=1)
            last_month = last_month.replace(day=min(target.day,
                         cal_mod.monthrange(last_month.year, last_month.month)[1]))

            branch_filter = "AND branch_code = %(branch)s" if branch else ""
            params = {"branch": branch}

            def fetch_day_kpi(d):
                cur.execute(f"""
                    SELECT
                        COALESCE(SUM(net_total), 0)        AS revenue,
                        COUNT(*)                            AS bills,
                        COALESCE(AVG(net_total), 0)        AS avg_bill,
                        COALESCE(MAX(net_total), 0)        AS max_bill
                    FROM pos_bills
                    WHERE sales_date = %(d)s
                    {branch_filter}
                """, {**params, "d": d})
                row = cur.fetchone()
                if row:
                    return {
                        "revenue":  float(row[0]),
                        "bills":    int(row[1]),
                        "avg_bill": round(float(row[2]), 2),
                        "max_bill": float(row[3]),
                    }
                return {"revenue": 0, "bills": 0, "avg_bill": 0, "max_bill": 0}

            kpi_today      = fetch_day_kpi(target)
            kpi_last_week  = fetch_day_kpi(last_week)
            kpi_last_month = fetch_day_kpi(last_month)

            def delta(a, b):
                if b == 0:
                    return None
                return round((a - b) / b * 100, 1)

            deltas = {
                "vs_last_week": {
                    "revenue":  delta(kpi_today["revenue"],  kpi_last_week["revenue"]),
                    "bills":    delta(kpi_today["bills"],    kpi_last_week["bills"]),
                    "avg_bill": delta(kpi_today["avg_bill"], kpi_last_week["avg_bill"]),
                },
                "vs_last_month": {
                    "revenue":  delta(kpi_today["revenue"],  kpi_last_month["revenue"]),
                    "bills":    delta(kpi_today["bills"],    kpi_last_month["bills"]),
                    "avg_bill": delta(kpi_today["avg_bill"], kpi_last_month["avg_bill"]),
                },
            }

            # Hourly breakdown today
            cur.execute(f"""
                SELECT
                    EXTRACT(HOUR FROM sales_time::time)::int AS hr,
                    COALESCE(SUM(net_total), 0)              AS revenue,
                    COUNT(*)                                  AS bills
                FROM pos_bills
                WHERE sales_date = %(d)s
                {branch_filter}
                GROUP BY hr
                ORDER BY hr
            """, {**params, "d": target})
            hourly_rows = _rows_to_dicts(cur)
            hourly = [{"hour": r["hr"], "revenue": float(r["revenue"]), "bills": int(r["bills"])} for r in hourly_rows]

            # Top 10 items today
            cur.execute(f"""
                SELECT
                    si.item_name,
                    si.category,
                    SUM(si.qty)        AS qty,
                    SUM(si.net_amount) AS revenue
                FROM pos_sales_items si
                JOIN pos_bills b ON b.id = si.bill_id
                WHERE b.sales_date = %(d)s
                {branch_filter.replace('branch_code', 'b.branch_code')}
                GROUP BY si.item_name, si.category
                ORDER BY revenue DESC
                LIMIT 10
            """, {**params, "d": target})
            top_items = [
                {"item": r["item_name"], "category": r["category"],
                 "qty": int(r["qty"]), "revenue": float(r["revenue"])}
                for r in _rows_to_dicts(cur)
            ]

            # Order type split today
            cur.execute(f"""
                SELECT order_type, COUNT(*) AS bills, SUM(net_total) AS revenue
                FROM pos_bills
                WHERE sales_date = %(d)s
                {branch_filter}
                GROUP BY order_type
                ORDER BY revenue DESC
            """, {**params, "d": target})
            order_types = [
                {"type": r["order_type"] or "ไม่ระบุ",
                 "bills": int(r["bills"]),
                 "revenue": float(r["revenue"])}
                for r in _rows_to_dicts(cur)
            ]

            # MTD summary (month-to-date)
            month_start = target.replace(day=1)
            cur.execute(f"""
                SELECT
                    COALESCE(SUM(net_total), 0) AS mtd_revenue,
                    COUNT(*)                     AS mtd_bills,
                    COUNT(DISTINCT sales_date)   AS active_days
                FROM pos_bills
                WHERE sales_date BETWEEN %(ms)s AND %(t)s
                {branch_filter}
            """, {**params, "ms": month_start, "t": target})
            mtd_row = cur.fetchone()
            mtd = {
                "revenue":     float(mtd_row[0]) if mtd_row else 0,
                "bills":       int(mtd_row[1]) if mtd_row else 0,
                "active_days": int(mtd_row[2]) if mtd_row else 0,
            }

        return {
            "date":       str(target),
            "kpi":        kpi_today,
            "last_week":  kpi_last_week,
            "last_month": kpi_last_month,
            "deltas":     deltas,
            "hourly":     hourly,
            "top_items":  top_items,
            "order_types": order_types,
            "mtd":        mtd,
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Phase 59 — Discount Analytics
# GET /pos/discounts?months=3&branch=
# ---------------------------------------------------------------------------
@router.get("/pos/discounts")
def pos_discounts(months: int = Query(3), branch: str = Query("")):
    from datetime import date as date_type
    from dateutil.relativedelta import relativedelta

    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            end_date   = date_type.today()
            start_date = (end_date.replace(day=1) - relativedelta(months=months - 1))
            branch_filter = "AND branch_code = %(branch)s" if branch else ""
            params = {"start": start_date, "end": end_date, "branch": branch}

            # Overall summary
            cur.execute(f"""
                SELECT
                    COUNT(*)                                                        AS total_bills,
                    COALESCE(SUM(net_total), 0)                                    AS total_net,
                    COALESCE(SUM(item_discount + bill_discount), 0)                AS total_discount,
                    COALESCE(SUM(item_discount), 0)                                AS total_item_disc,
                    COALESCE(SUM(bill_discount), 0)                                AS total_bill_disc,
                    COUNT(*) FILTER (WHERE (item_discount + bill_discount) > 0)    AS discounted_bills,
                    COALESCE(AVG(item_discount + bill_discount)
                        FILTER (WHERE (item_discount + bill_discount) > 0), 0)     AS avg_discount_per_bill
                FROM pos_bills
                WHERE sales_date BETWEEN %(start)s AND %(end)s
                {branch_filter}
            """, params)
            row = cur.fetchone()
            total_net      = float(row[1] or 0)
            total_discount = float(row[2] or 0)
            gross_revenue  = total_net + total_discount
            summary = {
                "total_bills":         int(row[0] or 0),
                "total_net":           round(total_net, 2),
                "total_discount":      round(total_discount, 2),
                "total_item_disc":     round(float(row[3] or 0), 2),
                "total_bill_disc":     round(float(row[4] or 0), 2),
                "discounted_bills":    int(row[5] or 0),
                "avg_discount_per_bill": round(float(row[6] or 0), 2),
                "discount_rate_pct":   round(total_discount / gross_revenue * 100, 2) if gross_revenue else 0,
                "pct_bills_discounted": round(int(row[5] or 0) / max(int(row[0] or 1), 1) * 100, 1),
            }

            # By staff (top 15 staff with most discount given)
            cur.execute(f"""
                SELECT
                    COALESCE(staff_name, 'ไม่ระบุ')              AS staff,
                    COUNT(*)                                       AS bills,
                    COUNT(*) FILTER (WHERE (item_discount + bill_discount) > 0) AS disc_bills,
                    COALESCE(SUM(item_discount + bill_discount), 0) AS total_disc,
                    COALESCE(SUM(net_total), 0)                    AS net_rev
                FROM pos_bills
                WHERE sales_date BETWEEN %(start)s AND %(end)s
                {branch_filter}
                GROUP BY staff
                HAVING SUM(item_discount + bill_discount) > 0
                ORDER BY total_disc DESC
                LIMIT 15
            """, params)
            by_staff = [
                {
                    "staff":      r["staff"],
                    "bills":      int(r["bills"]),
                    "disc_bills": int(r["disc_bills"]),
                    "total_disc": round(float(r["total_disc"]), 2),
                    "net_rev":    round(float(r["net_rev"]), 2),
                    "disc_pct":   round(float(r["total_disc"]) /
                                  (float(r["net_rev"]) + float(r["total_disc"])) * 100, 1)
                                  if (float(r["net_rev"]) + float(r["total_disc"])) > 0 else 0,
                }
                for r in _rows_to_dicts(cur)
            ]

            # By hour of day
            cur.execute(f"""
                SELECT
                    EXTRACT(HOUR FROM sales_time::time)::int       AS hr,
                    COUNT(*) FILTER (WHERE (item_discount + bill_discount) > 0) AS disc_bills,
                    COALESCE(SUM(item_discount + bill_discount), 0) AS total_disc
                FROM pos_bills
                WHERE sales_date BETWEEN %(start)s AND %(end)s
                {branch_filter}
                GROUP BY hr
                ORDER BY hr
            """, params)
            by_hour = [
                {"hour": r["hr"], "disc_bills": int(r["disc_bills"]),
                 "total_disc": round(float(r["total_disc"]), 2)}
                for r in _rows_to_dicts(cur)
            ]

            # Monthly trend
            cur.execute(f"""
                SELECT
                    TO_CHAR(sales_date, 'YYYY-MM')                 AS month,
                    COALESCE(SUM(net_total), 0)                    AS net_rev,
                    COALESCE(SUM(item_discount + bill_discount), 0) AS total_disc,
                    COUNT(*) FILTER (WHERE (item_discount + bill_discount) > 0) AS disc_bills,
                    COUNT(*)                                        AS total_bills
                FROM pos_bills
                WHERE sales_date BETWEEN %(start)s AND %(end)s
                {branch_filter}
                GROUP BY month
                ORDER BY month
            """, params)
            monthly = [
                {
                    "month":       r["month"],
                    "net_rev":     round(float(r["net_rev"]), 2),
                    "total_disc":  round(float(r["total_disc"]), 2),
                    "disc_bills":  int(r["disc_bills"]),
                    "total_bills": int(r["total_bills"]),
                    "disc_rate":   round(float(r["total_disc"]) /
                                   (float(r["net_rev"]) + float(r["total_disc"])) * 100, 2)
                                   if (float(r["net_rev"]) + float(r["total_disc"])) > 0 else 0,
                }
                for r in _rows_to_dicts(cur)
            ]

            # By order type
            cur.execute(f"""
                SELECT
                    COALESCE(order_type, 'ไม่ระบุ')               AS otype,
                    COUNT(*)                                        AS bills,
                    COALESCE(SUM(item_discount + bill_discount), 0) AS total_disc,
                    COALESCE(SUM(net_total), 0)                    AS net_rev
                FROM pos_bills
                WHERE sales_date BETWEEN %(start)s AND %(end)s
                {branch_filter}
                GROUP BY otype
                ORDER BY total_disc DESC
            """, params)
            by_order_type = [
                {"type": r["otype"], "bills": int(r["bills"]),
                 "total_disc": round(float(r["total_disc"]), 2),
                 "net_rev":    round(float(r["net_rev"]), 2)}
                for r in _rows_to_dicts(cur)
            ]

        return {
            "summary":       summary,
            "by_staff":      by_staff,
            "by_hour":       by_hour,
            "monthly":       monthly,
            "by_order_type": by_order_type,
            "period":        {"start": str(start_date), "end": str(end_date), "months": months},
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Phase 60 — 7-Day Revenue Forecast
# GET /pos/predict?weeks=8&branch=
# ---------------------------------------------------------------------------
@router.get("/pos/predict")
def pos_predict(weeks: int = Query(8), branch: str = Query("")):
    """
    Forecast next 7 days using DOW averages from last N weeks.
    Also shows last-30-day actuals for context.
    """
    from datetime import date as date_type, timedelta
    import math

    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            today      = date_type.today()
            train_start = today - timedelta(weeks=weeks)
            branch_filter = "AND branch_code = %(branch)s" if branch else ""
            params = {"branch": branch, "train_start": train_start, "today": today}

            # DOW averages from training window (0=Mon ... 6=Sun in Python)
            cur.execute(f"""
                SELECT
                    EXTRACT(DOW FROM sales_date)::int  AS dow,   -- 0=Sun..6=Sat (postgres)
                    COUNT(DISTINCT sales_date)          AS day_count,
                    COALESCE(SUM(net_total), 0)         AS total_rev,
                    COALESCE(AVG(net_total), 0)         AS avg_daily_rev,
                    COALESCE(STDDEV(net_total), 0)      AS std_dev,
                    COUNT(*)                            AS total_bills,
                    COALESCE(AVG(COUNT(*)) OVER(), 0)   AS overall_avg_bills
                FROM (
                    SELECT sales_date,
                           SUM(net_total)  AS net_total,
                           COUNT(*)        AS bills
                    FROM pos_bills
                    WHERE sales_date BETWEEN %(train_start)s AND %(today)s
                    {branch_filter}
                    GROUP BY sales_date
                ) daily
                GROUP BY dow
                ORDER BY dow
            """, params)
            dow_rows = _rows_to_dicts(cur)

            # Build DOW lookup {0..6 postgres -> avg_rev, std, bills}
            DOW_NAMES_TH = {0: 'อาทิตย์', 1: 'จันทร์', 2: 'อังคาร', 3: 'พุธ',
                            4: 'พฤหัสบดี', 5: 'ศุกร์', 6: 'เสาร์'}
            dow_map = {}
            for r in dow_rows:
                d = int(r["dow"])
                avg = float(r["avg_daily_rev"] or 0)
                std = float(r["std_dev"] or 0)
                dow_map[d] = {
                    "avg": round(avg, 2),
                    "std": round(std, 2),
                    "day_count": int(r["day_count"]),
                    "avg_bills": round(float(r["total_bills"]) / max(int(r["day_count"]), 1), 1),
                    "confidence": max(0, round(100 - (std / avg * 100) if avg > 0 else 0, 0)),
                }

            # Overall trend slope (linear regression on last 8 weeks daily revenue)
            cur.execute(f"""
                SELECT
                    sales_date,
                    SUM(net_total) AS daily_rev
                FROM pos_bills
                WHERE sales_date BETWEEN %(train_start)s AND %(today)s
                {branch_filter}
                GROUP BY sales_date
                ORDER BY sales_date
            """, params)
            daily_rows = _rows_to_dicts(cur)

            # Simple linear regression for trend
            n = len(daily_rows)
            slope = 0.0
            if n >= 7:
                xs = list(range(n))
                ys = [float(r["daily_rev"]) for r in daily_rows]
                mean_x = sum(xs) / n
                mean_y = sum(ys) / n
                num = sum((xs[i] - mean_x) * (ys[i] - mean_y) for i in range(n))
                den = sum((xs[i] - mean_x) ** 2 for i in range(n))
                slope = num / den if den else 0

            # Build 7-day forecast
            forecast = []
            for i in range(1, 8):
                fdate = today + timedelta(days=i)
                pg_dow = fdate.weekday() % 7  # Python: 0=Mon; Postgres DOW: 0=Sun
                # Convert: Python weekday 0=Mon,6=Sun → Postgres DOW 0=Sun,1=Mon..6=Sat
                pg_dow_conv = (fdate.weekday() + 1) % 7  # 0=Sun..6=Sat
                info = dow_map.get(pg_dow_conv, {"avg": 0, "std": 0, "day_count": 0, "avg_bills": 0, "confidence": 0})
                trend_adj = slope * (n + i)  # project trend forward
                base = info["avg"]
                predicted = max(0, base + trend_adj * 0.3)  # dampen trend
                low  = max(0, predicted - info["std"])
                high = predicted + info["std"]
                forecast.append({
                    "date":       str(fdate),
                    "dow_name":   DOW_NAMES_TH[pg_dow_conv],
                    "weekday":    fdate.strftime('%a'),
                    "predicted":  round(predicted, 2),
                    "low":        round(low, 2),
                    "high":       round(high, 2),
                    "avg_bills":  info["avg_bills"],
                    "confidence": info["confidence"],
                    "day_count":  info["day_count"],
                })

            # Last 30 actuals for chart context
            actuals_start = today - timedelta(days=29)
            cur.execute(f"""
                SELECT
                    sales_date,
                    SUM(net_total) AS revenue,
                    COUNT(*)       AS bills
                FROM pos_bills
                WHERE sales_date BETWEEN %(as)s AND %(today)s
                {branch_filter}
                GROUP BY sales_date
                ORDER BY sales_date
            """, {**params, "as": actuals_start})
            actuals = [
                {"date": str(r["sales_date"]), "revenue": round(float(r["revenue"]), 2),
                 "bills": int(r["bills"])}
                for r in _rows_to_dicts(cur)
            ]

            # DOW summary for display
            dow_summary = []
            for pg_dow in range(7):
                info = dow_map.get(pg_dow, {"avg": 0, "std": 0, "day_count": 0, "avg_bills": 0, "confidence": 0})
                dow_summary.append({
                    "dow":      pg_dow,
                    "name":     DOW_NAMES_TH[pg_dow],
                    **info,
                })

            return {
                "forecast":    forecast,
                "actuals":     actuals,
                "dow_summary": dow_summary,
                "meta": {
                    "train_weeks": weeks,
                    "train_days":  n,
                    "slope_per_day": round(slope, 2),
                    "today": str(today),
                },
            }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Phase 61 — Menu Price History
# GET /pos/prices?months=6&branch=&item=
# ---------------------------------------------------------------------------
@router.get("/pos/prices")
def pos_prices(months: int = Query(6), branch: str = Query(""), item: str = Query("")):
    """
    Track unit_price changes per menu item over time from pos_sales_items.
    Returns: summary per item (current, min, max, change%) + monthly avg price trend.
    """
    from datetime import date as date_type
    from dateutil.relativedelta import relativedelta

    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            end_date   = date_type.today()
            start_date = (end_date.replace(day=1) - relativedelta(months=months - 1))
            branch_filter = "AND b.branch_code = %(branch)s" if branch else ""
            item_filter   = "AND si.item_name ILIKE %(item)s" if item else ""
            params = {"start": start_date, "end": end_date, "branch": branch, "item": f"%{item}%"}

            # Per-item summary: first price, latest price, min, max, # distinct prices
            cur.execute(f"""
                WITH monthly AS (
                    SELECT
                        si.item_name,
                        TO_CHAR(b.sales_date, 'YYYY-MM')              AS month,
                        ROUND(AVG(si.unit_price)::numeric, 2)          AS avg_price,
                        MIN(si.unit_price)                             AS min_price,
                        MAX(si.unit_price)                             AS max_price,
                        COUNT(DISTINCT si.unit_price)                  AS distinct_prices,
                        SUM(si.qty)                                    AS total_qty
                    FROM pos_sales_items si
                    JOIN pos_bills b ON b.id = si.bill_id
                    WHERE b.sales_date BETWEEN %(start)s AND %(end)s
                      AND si.unit_price > 0
                      {branch_filter}
                      {item_filter}
                    GROUP BY si.item_name, month
                ),
                first_last AS (
                    SELECT
                        item_name,
                        FIRST_VALUE(avg_price) OVER (
                            PARTITION BY item_name ORDER BY month ASC
                        ) AS first_price,
                        LAST_VALUE(avg_price) OVER (
                            PARTITION BY item_name ORDER BY month ASC
                            ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING
                        ) AS last_price,
                        MIN(min_price) OVER (PARTITION BY item_name) AS overall_min,
                        MAX(max_price) OVER (PARTITION BY item_name) AS overall_max,
                        SUM(distinct_prices) OVER (PARTITION BY item_name) AS price_events,
                        SUM(total_qty) OVER (PARTITION BY item_name) AS total_qty
                    FROM monthly
                )
                SELECT DISTINCT
                    item_name,
                    ROUND(first_price::numeric, 2)   AS first_price,
                    ROUND(last_price::numeric, 2)    AS current_price,
                    ROUND(overall_min::numeric, 2)   AS min_price,
                    ROUND(overall_max::numeric, 2)   AS max_price,
                    price_events,
                    total_qty
                FROM first_last
                ORDER BY ABS(last_price - first_price) DESC, total_qty DESC
                LIMIT 100
            """, params)
            items_raw = _rows_to_dicts(cur)

            items_summary = []
            for r in items_raw:
                first = float(r["first_price"] or 0)
                current = float(r["current_price"] or 0)
                change_pct = round((current - first) / first * 100, 1) if first > 0 else 0
                direction = "up" if change_pct > 1 else "down" if change_pct < -1 else "stable"
                items_summary.append({
                    "item":          r["item_name"],
                    "first_price":   first,
                    "current_price": current,
                    "min_price":     float(r["min_price"] or 0),
                    "max_price":     float(r["max_price"] or 0),
                    "change_pct":    change_pct,
                    "direction":     direction,
                    "price_events":  int(r["price_events"] or 0),
                    "total_qty":     int(r["total_qty"] or 0),
                })

            # Monthly price trend per item (for top 10 changed items)
            changed_items = [r["item"] for r in items_summary if r["direction"] != "stable"][:10]
            all_items_for_trend = changed_items if changed_items else [r["item"] for r in items_summary[:10]]

            trend_data = {}
            if all_items_for_trend:
                cur.execute(f"""
                    SELECT
                        si.item_name,
                        TO_CHAR(b.sales_date, 'YYYY-MM') AS month,
                        ROUND(AVG(si.unit_price)::numeric, 2) AS avg_price
                    FROM pos_sales_items si
                    JOIN pos_bills b ON b.id = si.bill_id
                    WHERE b.sales_date BETWEEN %(start)s AND %(end)s
                      AND si.unit_price > 0
                      AND si.item_name = ANY(%(items)s)
                      {branch_filter}
                    GROUP BY si.item_name, month
                    ORDER BY si.item_name, month
                """, {**params, "items": all_items_for_trend})
                for r in _rows_to_dicts(cur):
                    name = r["item_name"]
                    if name not in trend_data:
                        trend_data[name] = []
                    trend_data[name].append({"month": r["month"], "price": float(r["avg_price"])})

            # Overall stats
            changed_up   = sum(1 for r in items_summary if r["direction"] == "up")
            changed_down = sum(1 for r in items_summary if r["direction"] == "down")
            stable_count = sum(1 for r in items_summary if r["direction"] == "stable")

            return {
                "items":        items_summary,
                "trends":       trend_data,
                "stats": {
                    "total_items":   len(items_summary),
                    "changed_up":    changed_up,
                    "changed_down":  changed_down,
                    "stable":        stable_count,
                },
                "period": {"start": str(start_date), "end": str(end_date), "months": months},
            }
    finally:
        conn.close()


# ── Phase 62: Monthly Revenue Goal Tracker (/pos/goals) ──────────────────────
@router.get("/pos/goals")
def pos_goals(
    month: str = Query(None),
    target: float = Query(282000),
    branch: str = Query(""),
):
    import calendar as cal
    from datetime import date
    today = date.today()
    if month:
        try:
            y, m = int(month[:4]), int(month[5:7])
        except Exception:
            y, m = today.year, today.month
    else:
        y, m = today.year, today.month

    first_day = date(y, m, 1)
    days_in_month = cal.monthrange(y, m)[1]
    last_day = date(y, m, days_in_month)

    if y == today.year and m == today.month:
        days_elapsed = today.day
    else:
        days_elapsed = days_in_month
    days_remaining = max(0, days_in_month - days_elapsed)

    branch_filter = "AND branch = %(branch)s" if branch else ""
    params: dict = {"start": str(first_day), "end": str(last_day), "branch": branch}

    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(f"""
                SELECT sales_date::text AS dt,
                       COALESCE(SUM(net_price), 0) AS rev
                FROM pos_bills
                WHERE sales_date BETWEEN %(start)s AND %(end)s
                  AND LOWER(COALESCE(status, '')) NOT IN ('void','cancelled')
                  {branch_filter}
                GROUP BY sales_date
                ORDER BY sales_date
            """, params)
            daily_rows = _rows_to_dicts(cur)
    finally:
        conn.close()

    daily_map: dict = {r["dt"]: float(r["rev"]) for r in daily_rows}
    actual = sum(daily_map.values())
    daily_actual_avg = actual / days_elapsed if days_elapsed > 0 else 0.0
    projected_eom = daily_actual_avg * days_in_month
    gap = max(0.0, target - actual)
    daily_required = gap / days_remaining if days_remaining > 0 else 0.0
    pct = round(actual / target * 100, 1) if target > 0 else 0.0
    projected_pct = round(projected_eom / target * 100, 1) if target > 0 else 0.0

    if pct >= 100 or projected_pct >= 95:
        status = "on_track"
    elif projected_pct >= 75:
        status = "at_risk"
    else:
        status = "behind"

    target_daily = target / days_in_month if days_in_month > 0 else 0.0
    is_past = (y < today.year) or (y == today.year and m < today.month)

    # Build daily array with cumulative & target pace line
    all_days = []
    cumulative = 0.0
    for i in range(1, days_in_month + 1):
        d_str = date(y, m, i).isoformat()
        rev = daily_map.get(d_str, 0.0)
        in_past = is_past or (y == today.year and m == today.month and i <= today.day)
        if in_past:
            cumulative += rev
        all_days.append({
            "date": d_str,
            "day": i,
            "revenue": round(rev, 0) if in_past else None,
            "cumulative": round(cumulative, 0) if in_past else None,
            "target_pace": round(target_daily * i, 0),
        })

    # Weekly breakdown (5 weeks max)
    weekly = []
    for wk in range(5):
        ws = 1 + wk * 7
        we = min(ws + 6, days_in_month)
        if ws > days_in_month:
            break
        w_rev = sum(
            daily_map.get(date(y, m, d).isoformat(), 0.0)
            for d in range(ws, we + 1)
        )
        w_tgt = target_daily * (we - ws + 1)
        weekly.append({
            "week": wk + 1,
            "label": f"W{wk+1} ({ws}–{we})",
            "revenue": round(w_rev, 0),
            "target": round(w_tgt, 0),
            "pct": round(w_rev / w_tgt * 100, 1) if w_tgt > 0 else 0.0,
        })

    return {
        "month": f"{y:04d}-{m:02d}",
        "target": target,
        "actual": round(actual, 0),
        "pct": pct,
        "gap": round(gap, 0),
        "days_elapsed": days_elapsed,
        "days_total": days_in_month,
        "days_remaining": days_remaining,
        "daily_actual_avg": round(daily_actual_avg, 0),
        "daily_required": round(daily_required, 0),
        "projected_eom": round(projected_eom, 0),
        "projected_pct": projected_pct,
        "status": status,
        "daily": all_days,
        "weekly": weekly,
    }


# ── Phase 63: Void & Cancelled Orders Analytics (/pos/voids) ─────────────────
@router.get("/pos/voids")
def pos_voids(
    months: int = Query(3),
    branch: str = Query(""),
):
    from datetime import date, timedelta
    today = date.today()
    start = (today.replace(day=1) - timedelta(days=1)).replace(day=1)
    # go back `months` months from start of current month
    for _ in range(months - 1):
        start = (start - timedelta(days=1)).replace(day=1)
    start_str = str(start)

    branch_filter = "AND branch = %(branch)s" if branch else ""
    params: dict = {"start": start_str, "branch": branch}

    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            # Total bills & revenue (all statuses)
            cur.execute(f"""
                SELECT COUNT(*) AS total_bills,
                       COALESCE(SUM(net_price), 0) AS total_rev
                FROM pos_bills
                WHERE sales_date >= %(start)s {branch_filter}
            """, params)
            totals = _rows_to_dicts(cur)[0]

            # Void summary
            cur.execute(f"""
                SELECT COUNT(*) AS void_count,
                       COALESCE(SUM(net_price), 0) AS void_amount,
                       COALESCE(AVG(net_price), 0) AS avg_void
                FROM pos_bills
                WHERE sales_date >= %(start)s
                  AND LOWER(COALESCE(status,'')) IN ('void','cancelled')
                  {branch_filter}
            """, params)
            void_sum = _rows_to_dicts(cur)[0]

            # By staff
            cur.execute(f"""
                SELECT COALESCE(staff, '(ไม่ระบุ)') AS staff,
                       COUNT(*) AS voids,
                       COALESCE(SUM(net_price), 0) AS amount
                FROM pos_bills
                WHERE sales_date >= %(start)s
                  AND LOWER(COALESCE(status,'')) IN ('void','cancelled')
                  {branch_filter}
                GROUP BY staff
                ORDER BY voids DESC
                LIMIT 15
            """, params)
            by_staff = _rows_to_dicts(cur)

            # By hour
            cur.execute(f"""
                SELECT EXTRACT(HOUR FROM sales_time::time)::int AS hour,
                       COUNT(*) AS voids,
                       COALESCE(SUM(net_price), 0) AS amount
                FROM pos_bills
                WHERE sales_date >= %(start)s
                  AND LOWER(COALESCE(status,'')) IN ('void','cancelled')
                  AND sales_time IS NOT NULL
                  {branch_filter}
                GROUP BY hour
                ORDER BY hour
            """, params)
            by_hour = _rows_to_dicts(cur)

            # Monthly trend
            cur.execute(f"""
                SELECT TO_CHAR(sales_date,'YYYY-MM') AS month,
                       COUNT(*) AS voids,
                       COALESCE(SUM(net_price), 0) AS amount
                FROM pos_bills
                WHERE sales_date >= %(start)s
                  AND LOWER(COALESCE(status,'')) IN ('void','cancelled')
                  {branch_filter}
                GROUP BY month
                ORDER BY month
            """, params)
            monthly = _rows_to_dicts(cur)

            # Top voided items (via sales_items join)
            cur.execute(f"""
                SELECT si.item_name,
                       COUNT(*) AS void_count,
                       COALESCE(SUM(si.qty), 0) AS void_qty,
                       COALESCE(SUM(si.qty * si.unit_price), 0) AS void_value
                FROM pos_sales_items si
                JOIN pos_bills b ON b.id = si.bill_id
                WHERE b.sales_date >= %(start)s
                  AND LOWER(COALESCE(b.status,'')) IN ('void','cancelled')
                  {branch_filter}
                GROUP BY si.item_name
                ORDER BY void_count DESC
                LIMIT 15
            """, params)
            top_items = _rows_to_dicts(cur)

    finally:
        conn.close()

    total_bills = int(totals["total_bills"] or 0)
    total_rev   = float(totals["total_rev"] or 0)
    void_count  = int(void_sum["void_count"] or 0)
    void_amount = float(void_sum["void_amount"] or 0)
    avg_void    = float(void_sum["avg_void"] or 0)

    void_rate_pct = round(void_count / total_bills * 100, 2) if total_bills > 0 else 0.0
    rev_loss_pct  = round(void_amount / (total_rev + void_amount) * 100, 2) if (total_rev + void_amount) > 0 else 0.0

    return {
        "months": months,
        "summary": {
            "void_count":    void_count,
            "void_amount":   round(void_amount, 0),
            "avg_void":      round(avg_void, 0),
            "void_rate_pct": void_rate_pct,
            "rev_loss_pct":  rev_loss_pct,
            "total_bills":   total_bills,
        },
        "by_staff":  [{"staff": r["staff"], "voids": int(r["voids"]), "amount": round(float(r["amount"]), 0)} for r in by_staff],
        "by_hour":   [{"hour": r["hour"],   "voids": int(r["voids"]), "amount": round(float(r["amount"]), 0)} for r in by_hour],
        "monthly":   [{"month": r["month"], "voids": int(r["voids"]), "amount": round(float(r["amount"]), 0)} for r in monthly],
        "top_items": [{"item": r["item_name"], "count": int(r["void_count"]), "qty": float(r["void_qty"]), "value": round(float(r["void_value"]), 0)} for r in top_items],
    }


# ── Phase 64: Food Cost & Gross Profit Estimator (/pos/food-cost) ─────────────
@router.get("/pos/food-cost")
def pos_food_cost(
    months: int = Query(1),
    branch: str = Query(""),
):
    from datetime import date, timedelta
    today = date.today()
    start = today.replace(day=1)
    for _ in range(months - 1):
        start = (start - timedelta(days=1)).replace(day=1)
    start_str = str(start)

    branch_filter = "AND b.branch = %(branch)s" if branch else ""
    params: dict = {"start": start_str, "branch": branch}

    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            # Check if any ingredient has price set
            cur.execute("SELECT COUNT(*) AS cnt FROM ingredients WHERE price_per_unit > 0")
            priced_count = int((_rows_to_dicts(cur)[0])["cnt"] or 0)

            # Recipe cost per menu item (SUM of ingredient qty × price)
            # NOTE: recipes table uses `name` column, aliased to menu_name for backward compat (Session 18 fix)
            cur.execute("""
                SELECT r.name AS menu_name,
                       COALESCE(SUM(ri.qty_used * COALESCE(i.price_per_unit, 0)), 0) AS cost_per_unit
                FROM recipes r
                JOIN recipe_ingredients ri ON ri.recipe_id = r.id
                JOIN ingredients i ON i.id = ri.ingredient_id
                GROUP BY r.name
            """)
            recipe_costs = {row["menu_name"]: float(row["cost_per_unit"]) for row in _rows_to_dicts(cur)}

            # Sales by item
            cur.execute(f"""
                SELECT si.item_name,
                       SUM(si.qty)::float                              AS qty_sold,
                       COALESCE(SUM(si.qty * si.unit_price), 0)::float AS revenue,
                       AVG(si.unit_price)::float                       AS avg_price
                FROM pos_sales_items si
                JOIN pos_bills b ON b.id = si.bill_id
                WHERE b.sales_date >= %(start)s
                  AND si.unit_price > 0
                  AND LOWER(COALESCE(b.status,'')) NOT IN ('void','cancelled')
                  {branch_filter}
                GROUP BY si.item_name
                ORDER BY revenue DESC
                LIMIT 60
            """, params)
            sales_rows = _rows_to_dicts(cur)

            # Total revenue for the period (for summary)
            cur.execute(f"""
                SELECT COALESCE(SUM(net_price), 0) AS total_rev
                FROM pos_bills b
                WHERE b.sales_date >= %(start)s
                  AND LOWER(COALESCE(b.status,'')) NOT IN ('void','cancelled')
                  {branch_filter}
            """, params)
            total_rev = float((_rows_to_dicts(cur)[0])["total_rev"] or 0)

    finally:
        conn.close()

    # Enrich each item with recipe cost
    items = []
    total_est_cost = 0.0
    total_item_rev = 0.0
    matched = 0
    for r in sales_rows:
        name      = r["item_name"]
        qty       = float(r["qty_sold"] or 0)
        revenue   = float(r["revenue"] or 0)
        cost_unit = recipe_costs.get(name, 0.0)
        est_cost  = round(qty * cost_unit, 2)
        gross     = round(revenue - est_cost, 2)
        fc_pct    = round(est_cost / revenue * 100, 1) if revenue > 0 else 0.0
        has_recipe = name in recipe_costs
        if has_recipe and cost_unit > 0:
            matched += 1
        total_est_cost += est_cost
        total_item_rev += revenue
        items.append({
            "item":       name,
            "qty_sold":   round(qty, 1),
            "revenue":    round(revenue, 0),
            "avg_price":  round(float(r["avg_price"] or 0), 0),
            "cost_unit":  round(cost_unit, 2),
            "est_cost":   round(est_cost, 0),
            "gross":      round(gross, 0),
            "fc_pct":     fc_pct,
            "has_recipe": has_recipe,
        })

    overall_fc   = round(total_est_cost / total_item_rev * 100, 1) if total_item_rev > 0 else 0.0
    overall_gp   = round(total_item_rev - total_est_cost, 0)
    coverage_pct = round(matched / len(items) * 100, 1) if items else 0.0

    return {
        "months":       months,
        "start":        start_str,
        "priced_ingredients": priced_count,
        "summary": {
            "total_revenue":  round(total_rev, 0),
            "item_revenue":   round(total_item_rev, 0),
            "total_est_cost": round(total_est_cost, 0),
            "gross_profit":   overall_gp,
            "food_cost_pct":  overall_fc,
            "items_with_recipe": matched,
            "total_items":    len(items),
            "coverage_pct":   coverage_pct,
        },
        "items": items,
    }


# ── Phase 65: Shift Performance Analytics (/pos/shifts) ──────────────────────
_SHIFT_MAP = {
    0:"กลางคืน",1:"กลางคืน",2:"กลางคืน",3:"กลางคืน",4:"กลางคืน",5:"กลางคืน",
    6:"เช้า",7:"เช้า",8:"เช้า",9:"เช้า",10:"เช้า",
    11:"กลางวัน",12:"กลางวัน",13:"กลางวัน",14:"กลางวัน",
    15:"บ่าย",16:"บ่าย",
    17:"เย็น",18:"เย็น",19:"เย็น",20:"เย็น",21:"เย็น",
    22:"กลางคืน",23:"กลางคืน",
}
_SHIFT_ORDER = ["เช้า","กลางวัน","บ่าย","เย็น","กลางคืน"]
_SHIFT_HOURS = {"เช้า":"06–10","กลางวัน":"11–14","บ่าย":"15–16","เย็น":"17–21","กลางคืน":"22–05"}
_DOW_TH = {0:"อา",1:"จ",2:"อ",3:"พ",4:"พฤ",5:"ศ",6:"ส"}

@router.get("/pos/shifts")
def pos_shifts(
    months: int = Query(3),
    branch: str = Query(""),
):
    from datetime import date, timedelta
    today = date.today()
    start = today.replace(day=1)
    for _ in range(months - 1):
        start = (start - timedelta(days=1)).replace(day=1)
    start_str = str(start)

    branch_filter = "AND branch = %(branch)s" if branch else ""
    params: dict = {"start": start_str, "branch": branch}

    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            # Hour-level aggregation
            cur.execute(f"""
                SELECT EXTRACT(HOUR FROM sales_time::time)::int AS hour,
                       EXTRACT(DOW  FROM sales_date)::int        AS dow,
                       TO_CHAR(sales_date,'YYYY-MM')             AS month,
                       COUNT(*)                                   AS bills,
                       COALESCE(SUM(net_price),0)                 AS revenue
                FROM pos_bills
                WHERE sales_date >= %(start)s
                  AND sales_time IS NOT NULL
                  AND LOWER(COALESCE(status,'')) NOT IN ('void','cancelled')
                  {branch_filter}
                GROUP BY hour, dow, month
                ORDER BY hour
            """, params)
            rows = _rows_to_dicts(cur)
    finally:
        conn.close()

    # Aggregate into shift buckets
    shift_rev   = {s: 0.0 for s in _SHIFT_ORDER}
    shift_bills = {s: 0   for s in _SHIFT_ORDER}
    # DOW × Shift matrix  [dow][shift] = revenue
    dow_shift: dict = {d: {s: 0.0 for s in _SHIFT_ORDER} for d in range(7)}
    # Monthly × Shift    [month][shift] = revenue
    month_shift: dict = {}

    for r in rows:
        h = int(r["hour"] or 0)
        d = int(r["dow"]  or 0)
        mo = r["month"]
        rev  = float(r["revenue"] or 0)
        bills = int(r["bills"] or 0)
        sh = _SHIFT_MAP.get(h, "กลางคืน")
        shift_rev[sh]   += rev
        shift_bills[sh] += bills
        dow_shift[d][sh] += rev
        if mo not in month_shift:
            month_shift[mo] = {s: 0.0 for s in _SHIFT_ORDER}
        month_shift[mo][sh] += rev

    total_rev = sum(shift_rev.values()) or 1.0

    summary = []
    for sh in _SHIFT_ORDER:
        rev   = shift_rev[sh]
        bills = shift_bills[sh]
        summary.append({
            "shift":    sh,
            "hours":    _SHIFT_HOURS[sh],
            "revenue":  round(rev, 0),
            "bills":    bills,
            "avg_bill": round(rev / bills, 0) if bills > 0 else 0,
            "pct":      round(rev / total_rev * 100, 1),
        })

    # DOW × Shift table
    dow_table = []
    for d in range(7):
        row_rev = sum(dow_shift[d].values())
        dow_table.append({
            "dow": _DOW_TH[d],
            "shifts": {sh: round(dow_shift[d][sh], 0) for sh in _SHIFT_ORDER},
            "total": round(row_rev, 0),
        })

    # Monthly trend list
    monthly = []
    for mo in sorted(month_shift.keys()):
        entry = {"month": mo}
        for sh in _SHIFT_ORDER:
            entry[sh] = round(month_shift[mo].get(sh, 0), 0)
        monthly.append(entry)

    best = max(summary, key=lambda x: x["revenue"]) if summary else {}

    return {
        "months":  months,
        "summary": summary,
        "best_shift": best.get("shift", ""),
        "dow_table":  dow_table,
        "monthly":    monthly,
    }
