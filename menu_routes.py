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
