"""
VEXONHQ Phase 19 — AI Inventory Forecast
==========================================
Predict reorder points from historical vendor_bills purchase patterns.
No ML model required — uses statistical frequency + average spend analysis.

Endpoints:
  GET /inventory/forecast          — reorder predictions per vendor/category
  GET /inventory/purchase-history  — monthly purchase history per vendor

In main.py add:
    from inventory_forecast_routes import router as inventory_forecast_router
    app.include_router(inventory_forecast_router)
"""

from __future__ import annotations

import logging
import os
from datetime import date, datetime, timedelta
from typing import Any, Optional
from uuid import UUID

import psycopg2
from fastapi import APIRouter, Query

try:
    from main import get_db_conn  # type: ignore
except ImportError:
    def get_db_conn():
        return psycopg2.connect(os.environ["DATABASE_URL"])

log = logging.getLogger("inventory_forecast_routes")
router = APIRouter(tags=["inventory-forecast"])

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


def _urgency(days_since: int, avg_interval: float) -> str:
    """Return urgency level based on how overdue the reorder is."""
    if avg_interval <= 0:
        return "unknown"
    ratio = days_since / avg_interval
    if ratio >= 1.1:
        return "overdue"       # สั่งเกินกำหนดแล้ว
    elif ratio >= 0.85:
        return "urgent"        # ควรสั่งเร็วๆ นี้
    elif ratio >= 0.65:
        return "soon"          # ใกล้ถึงเวลา
    else:
        return "ok"            # ยังไม่ถึงเวลา


def _urgency_label(level: str) -> str:
    return {
        "overdue": "⭕ เกินกำหนด",
        "urgent":  "🔴 ควรสั่งเร็วๆ นี้",
        "soon":    "🟡 ใกล้ถึงเวลา",
        "ok":      "🟢 ยังไม่ถึงเวลา",
        "unknown": "⚪ ไม่มีข้อมูล",
    }.get(level, level)


# ─────────────────────────────────────────────────────────
# GET /inventory/forecast
# ─────────────────────────────────────────────────────────

@router.get("/inventory/forecast")
def inventory_forecast(
    branch: str = Query(DEFAULT_BRANCH),
    lookback_months: int = Query(6, ge=2, le=24, description="เดือนที่ใช้วิเคราะห์ pattern"),
    min_orders: int = Query(2, ge=1, description="จำนวน order ขั้นต่ำเพื่อแสดงใน forecast"),
):
    """
    Predict reorder timing for each vendor based on historical purchase frequency.

    Algorithm:
    1. Group vendor_bills by vendor_name for last N months
    2. Calculate: avg purchase interval (days), avg amount, last purchase date
    3. Predict: next_order_date = last_purchase + avg_interval
    4. Flag urgency: overdue / urgent / soon / ok
    """
    today = date.today()
    cutoff = today - timedelta(days=lookback_months * 30)

    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT
                       vendor_name,
                       COALESCE(ec.name_th, vb.category_code, 'ไม่ระบุ') AS category_name,
                       vb.category_code,
                       COUNT(*)::int                         AS order_count,
                       SUM(vb.amount)::numeric               AS total_spend,
                       AVG(vb.amount)::numeric               AS avg_amount,
                       MIN(vb.bill_date)                     AS first_order,
                       MAX(vb.bill_date)                     AS last_order,
                       -- purchase dates as array for interval calculation
                       ARRAY_AGG(vb.bill_date ORDER BY vb.bill_date) AS order_dates
                   FROM public.vendor_bills vb
                   LEFT JOIN public.expense_categories ec ON ec.code = vb.category_code
                   WHERE vb.review_status = 'confirmed'
                     AND vb.bill_date >= %s
                     AND COALESCE(vb.branch_code, %s) = %s
                     AND vb.amount > 0
                   GROUP BY vb.vendor_name, ec.name_th, vb.category_code
                   HAVING COUNT(*) >= %s
                   ORDER BY MAX(vb.bill_date) ASC""",  # most overdue first
                (cutoff, branch, branch, min_orders),
            )
            rows = _rows_to_dicts(cur)
    finally:
        conn.close()

    forecasts = []
    for r in rows:
        order_dates = r.get("order_dates") or []
        if not order_dates:
            continue

        # Parse dates
        dates: list[date] = []
        for d in order_dates:
            if isinstance(d, date):
                dates.append(d)
            elif isinstance(d, str):
                try:
                    dates.append(date.fromisoformat(d))
                except ValueError:
                    pass

        dates.sort()
        order_count = len(dates)
        last_order = dates[-1]
        days_since = (today - last_order).days

        # Calculate average interval between orders
        if len(dates) >= 2:
            intervals = [(dates[i+1] - dates[i]).days for i in range(len(dates) - 1)]
            avg_interval = sum(intervals) / len(intervals)
            min_interval = min(intervals)
            max_interval = max(intervals)
        else:
            # Only 1 order — estimate from lookback
            avg_interval = lookback_months * 15.0  # assume bi-monthly
            min_interval = max_interval = int(avg_interval)

        # Predict next order
        next_order_est = last_order + timedelta(days=int(avg_interval))
        days_until = (next_order_est - today).days

        urgency = _urgency(days_since, avg_interval)

        forecasts.append({
            "vendor_name":       r["vendor_name"],
            "category_name":     r["category_name"],
            "category_code":     r["category_code"],
            "order_count":       order_count,
            "avg_amount":        round(float(r.get("avg_amount") or 0), 2),
            "total_spend":       round(float(r.get("total_spend") or 0), 2),
            "last_order_date":   last_order.isoformat(),
            "days_since_order":  days_since,
            "avg_interval_days": round(avg_interval, 0),
            "min_interval_days": min_interval,
            "max_interval_days": max_interval,
            "next_order_est":    next_order_est.isoformat(),
            "days_until_order":  days_until,
            "urgency":           urgency,
            "urgency_label":     _urgency_label(urgency),
        })

    # Sort: overdue first → urgent → soon → ok
    urgency_order = {"overdue": 0, "urgent": 1, "soon": 2, "ok": 3, "unknown": 4}
    forecasts.sort(key=lambda x: (urgency_order.get(x["urgency"], 4), x["days_until_order"]))

    overdue_count = sum(1 for f in forecasts if f["urgency"] == "overdue")
    urgent_count  = sum(1 for f in forecasts if f["urgency"] == "urgent")

    return {
        "as_of":          today.isoformat(),
        "lookback_months": lookback_months,
        "total_vendors":  len(forecasts),
        "overdue_count":  overdue_count,
        "urgent_count":   urgent_count,
        "forecasts":      forecasts,
    }


# ─────────────────────────────────────────────────────────
# GET /inventory/purchase-history
# ─────────────────────────────────────────────────────────

@router.get("/inventory/purchase-history")
def purchase_history(
    vendor_name: str = Query(..., description="Vendor name to look up"),
    branch: str = Query(DEFAULT_BRANCH),
):
    """Monthly purchase history for a specific vendor."""
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT
                       bill_date,
                       invoice_no,
                       amount,
                       payment_status,
                       notes
                   FROM public.vendor_bills
                   WHERE review_status = 'confirmed'
                     AND vendor_name ILIKE %s
                     AND COALESCE(branch_code, %s) = %s
                   ORDER BY bill_date DESC
                   LIMIT 50""",
                (f"%{vendor_name}%", branch, branch),
            )
            bills = _rows_to_dicts(cur)
    finally:
        conn.close()

    return {
        "vendor_name": vendor_name,
        "bills":       bills,
        "total_bills": len(bills),
        "total_spend": sum(float(b.get("amount") or 0) for b in bills),
    }


# ============================================================
# Phase 66 — Inventory Reorder Suggestion (Session 16, 2026-05-17)
# ============================================================
# Returns items that should be re-stocked: any item where
# qty_in_stock < qty_max (i.e. qty_diff > 0). Includes urgency
# band and excludes Pro/(pro) promo SKUs.

def _compute_reorder_list(branch_code: str = DEFAULT_BRANCH,
                          tag: Optional[str] = None) -> dict:
    """
    Plain-Python helper (no FastAPI dependency) — called by the HTTP route
    AND by LINE bot's _handle_reorder_list. Same logic, no Query() injection.
    """
    try:
        from stock_routes import _get_latest_snapshot_id
    except Exception as e:
        log.error("import _get_latest_snapshot_id failed: %s", e)
        return {"snapshot_at": None, "items": [], "summary": {}}

    snapshot_id, snapshot_at = _get_latest_snapshot_id(branch_code)
    if not snapshot_id:
        return {"snapshot_at": None, "items": [], "summary": {
            "total_items": 0, "critical": 0, "high": 0, "medium": 0, "low": 0,
        }}

    sql = """
        SELECT
            item_name,
            tag,
            COALESCE(qty_in_stock, 0) AS qty_current,
            COALESCE(qty_max, 0)       AS qty_max,
            COALESCE(unit_price, 0)    AS unit_price,
            unit
        FROM public.pos_inventory_items
        WHERE snapshot_id = %s
          AND COALESCE(qty_max, 0) > COALESCE(qty_in_stock, 0)
          AND LOWER(item_name) NOT LIKE %s
          AND LOWER(item_name) NOT LIKE %s
    """
    params: list = [snapshot_id, "pro(%", "(pro%"]

    if tag:
        sql += " AND tag ILIKE %s"
        params.append(f"%{tag}%")

    sql += " ORDER BY qty_in_stock ASC, item_name"

    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
    finally:
        conn.close()

    items: list[dict] = []
    summary = {"total_items": 0, "critical": 0, "high": 0, "medium": 0, "low": 0,
               "est_total_cost": 0.0}

    for name, t, q_cur, q_max, price, unit in rows:
        q_cur = float(q_cur or 0)
        q_max = float(q_max or 0)
        to_order = max(q_max - q_cur, 0)
        price = float(price or 0)

        if q_cur <= 0:
            urgency = "critical"
        elif q_max > 0 and q_cur < q_max * 0.25:
            urgency = "high"
        elif q_max > 0 and q_cur < q_max * 0.5:
            urgency = "medium"
        else:
            urgency = "low"

        items.append({
            "item_name":   name,
            "tag":         t or "ไม่ระบุ",
            "qty_current": q_cur,
            "qty_max":     q_max,
            "qty_to_order": to_order,
            "unit":        unit or "",
            "unit_price":  price,
            "est_cost":    round(to_order * price, 2),
            "urgency":     urgency,
        })
        summary["total_items"] += 1
        summary[urgency] += 1
        summary["est_total_cost"] += to_order * price

    summary["est_total_cost"] = round(summary["est_total_cost"], 2)

    return {
        "snapshot_at": snapshot_at,
        "branch_code": branch_code,
        "items":       items,
        "summary":     summary,
    }


@router.get("/inventory/reorder")
def inventory_reorder(
    branch_code: str = Query(default=DEFAULT_BRANCH),
    tag: Optional[str] = Query(default=None, description="filter by inventory tag"),
):
    """HTTP wrapper around _compute_reorder_list — see helper for full docs."""
    return _compute_reorder_list(branch_code=branch_code, tag=tag)
