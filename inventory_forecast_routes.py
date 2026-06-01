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

# ===========================================================================
# Session 18 (2026-05-18) — Phase 32 Feature 3 RESTORED from commit b19b23f
# (was accidentally removed by Session 16 commit 742b618 — see memory
#  vexonhq_session18_phase32_recovery.md for root cause)
# ===========================================================================


# ─────────────────────────────────────────────────────────
# GET /inventory/ai-order-advice  (Phase 32)
# วิเคราะห์รูปแบบขายตามวันในสัปดาห์ (Day-of-Week)
# → แนะนำวันที่ควรสั่งของเข้าและปริมาณ
# ─────────────────────────────────────────────────────────

_DOW_TH = ["อาทิตย์", "จันทร์", "อังคาร", "พุธ", "พฤหัสบดี", "ศุกร์", "เสาร์"]
_DOW_EN = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]


@router.get("/inventory/ai-order-advice")
def ai_order_advice(
    branch: str = Query(DEFAULT_BRANCH),
    lookback_weeks: int = Query(12, ge=4, le=52, description="จำนวนสัปดาห์ย้อนหลัง"),
):
    """
    Phase 32 — AI Day-of-Week Order Advice

    วิเคราะห์ยอดขาย POS ย้อนหลัง N สัปดาห์ แล้วแนะนำ:
    1. วันไหนขายดีที่สุด/น้อยที่สุด
    2. ช่วงไหนควรสั่งของเข้า (ก่อนวันขายดี 1-2 วัน)
    3. ปริมาณสั่งแนะนำเทียบกับ baseline (avg weekday)
    """
    from datetime import timedelta as _td

    today = date.today()
    cutoff = today - _td(weeks=lookback_weeks)

    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            # ── 1. POS Sales by day-of-week ──────────────────────────────
            cur.execute(
                """SELECT
                       EXTRACT(DOW FROM entry_date)::int AS dow,
                       COUNT(DISTINCT entry_date)::int   AS day_count,
                       COALESCE(SUM(amount), 0)::numeric AS total_sales,
                       COALESCE(AVG(amount), 0)::numeric AS avg_daily_sales,
                       COALESCE(COUNT(*), 0)::int         AS txn_count
                   FROM public.v_daybook
                   WHERE direction = 'income'
                     AND source IN ('pos_sale','rider_income_grab','rider_income_lineman')
                     AND branch_code = %s
                     AND entry_date >= %s
                   GROUP BY EXTRACT(DOW FROM entry_date)
                   ORDER BY dow""",
                (branch, cutoff),
            )
            dow_rows = _rows_to_dicts(cur)

            # ── 2. Total days in analysis ─────────────────────────────────
            total_days = (today - cutoff).days or 1

            # ── 3. Weekly purchase spend (vendor_bills) ───────────────────
            cur.execute(
                """SELECT
                       EXTRACT(DOW FROM bill_date)::int AS dow,
                       COALESCE(AVG(amount), 0)::numeric AS avg_purchase
                   FROM public.vendor_bills
                   WHERE review_status = 'confirmed'
                     AND COALESCE(branch_code, %s) = %s
                     AND bill_date >= %s
                   GROUP BY EXTRACT(DOW FROM bill_date)
                   ORDER BY dow""",
                (branch, branch, cutoff),
            )
            purchase_rows = _rows_to_dicts(cur)
    finally:
        conn.close()

    # ── Build DOW stats ──────────────────────────────────────────────
    dow_stats: list[dict] = []
    all_avg = [float(r.get("avg_daily_sales") or 0) for r in dow_rows]
    grand_avg = sum(all_avg) / len(all_avg) if all_avg else 1.0

    purchase_by_dow: dict[int, float] = {}
    for pr in purchase_rows:
        purchase_by_dow[int(pr.get("dow") or 0)] = float(pr.get("avg_purchase") or 0)

    for r in dow_rows:
        dow = int(r.get("dow") or 0)
        avg_sales = float(r.get("avg_daily_sales") or 0)
        index = round(avg_sales / grand_avg * 100, 1) if grand_avg > 0 else 100.0

        dow_stats.append({
            "dow":            dow,
            "day_th":         _DOW_TH[dow],
            "day_en":         _DOW_EN[dow],
            "avg_sales":      round(avg_sales, 2),
            "sales_index":    index,           # 100 = avg, >100 above avg
            "day_count":      int(r.get("day_count") or 0),
            "total_sales":    round(float(r.get("total_sales") or 0), 2),
            "avg_purchase_on_day": round(purchase_by_dow.get(dow, 0), 2),
        })

    # Sort by avg_sales desc to rank days
    dow_ranked = sorted(dow_stats, key=lambda x: x["avg_sales"], reverse=True)

    # ── Generate advice ──────────────────────────────────────────────
    advice = []
    if dow_ranked:
        best_days   = [d for d in dow_ranked if d["sales_index"] >= 110]
        worst_days  = [d for d in dow_ranked if d["sales_index"] <= 80]
        top2        = dow_ranked[:2]

        # Best days label
        best_labels = " / ".join(f"วัน{d['day_th']}" for d in best_days[:3]) if best_days else "ยังไม่ชัดเจน"
        worst_labels = " / ".join(f"วัน{d['day_th']}" for d in worst_days[:2]) if worst_days else "ยังไม่ชัดเจน"

        # Recommend ordering 1-2 days before the best days
        order_days_suggestion = []
        for d in best_days[:2]:
            order_dow = (d["dow"] - 2) % 7  # 2 days before
            order_days_suggestion.append(_DOW_TH[order_dow])
        order_suggest = " / ".join(f"วัน{x}" for x in order_days_suggestion) if order_days_suggestion else "วันจันทร์"

        advice = [
            {
                "type":    "best_sales_days",
                "icon":    "🔥",
                "title":   "วันขายดีที่สุด",
                "detail":  best_labels,
                "note":    f"ยอดขายสูงกว่าค่าเฉลี่ย ควรมีของเตรียมพร้อม",
            },
            {
                "type":    "order_schedule",
                "icon":    "🛒",
                "title":   "แนะนำสั่งของเข้า",
                "detail":  order_suggest,
                "note":    f"สั่งของ 1-2 วันก่อนวันขายดี เพื่อให้มี stock พร้อม",
            },
            {
                "type":    "slow_days",
                "icon":    "📦",
                "title":   "วันขายช้า / รับสินค้าได้",
                "detail":  worst_labels,
                "note":    "เหมาะนับ stock และเช็คของหมดอายุ",
            },
        ]

        # Stock multiplier recommendation
        if top2:
            top_index = top2[0]["sales_index"]
            multiplier = round(top_index / 100, 2)
            advice.append({
                "type":    "stock_level",
                "icon":    "📊",
                "title":   "ปริมาณสต็อกแนะนำ (วันขายดี)",
                "detail":  f"เตรียม {multiplier}x เทียบกับวันปกติ",
                "note":    f"วัน{top2[0]['day_th']} ขายเฉลี่ย ฿{top2[0]['avg_sales']:,.0f} (index {top_index:.0f})",
            })

    return {
        "as_of":             today.isoformat(),
        "lookback_weeks":    lookback_weeks,
        "branch_code":       branch,
        "grand_avg_daily":   round(grand_avg, 2),
        "dow_stats":         dow_stats,       # sorted by DOW (Sun=0..Sat=6)
        "dow_ranked":        dow_ranked,      # sorted best→worst
        "advice":            advice,
    }


# ─────────────────────────────────────────────────────────
# F8 — Backtest the DOW order-advice against held-out actuals
# ─────────────────────────────────────────────────────────

def backtest_dow(train_daily: list[dict], test_daily: list[dict]) -> dict:
    """Pure scorer (no DB): how well does the day-of-week sales pattern learned on
    `train_daily` predict `test_daily`?

    Each input row: {"date": str, "dow": int 0-6, "sales": float}.
    - Train a per-DOW mean + grand mean; predict each test day = grand_mean ×
      (dow_mean / grand_mean) = dow_mean(train). MAPE vs actual.
    - best_day_hit: how many of train's top-2 DOW (by mean) are in test's top-2
      actual DOW (by mean). 0-2.
    Returns a report; handles empty/degenerate input without raising."""
    def _dow_means(rows: list[dict]) -> dict[int, float]:
        agg: dict[int, list[float]] = {}
        for r in rows:
            try:
                d = int(r["dow"]); s = float(r["sales"])
            except (KeyError, TypeError, ValueError):
                continue
            agg.setdefault(d, []).append(s)
        return {d: (sum(v) / len(v)) for d, v in agg.items() if v}

    train_means = _dow_means(train_daily)
    test_means = _dow_means(test_daily)

    if not train_means or not test_daily:
        return {
            "train_days": len(train_daily), "test_days": len(test_daily),
            "mape_pct": None, "accuracy_pct": None, "best_day_hit": None,
            "verdict_th": "ข้อมูลไม่พอสำหรับ backtest (ต้องมีทั้งช่วง train และ test)",
        }

    grand_train = sum(train_means.values()) / len(train_means)

    # MAPE on test days that have a usable actual (>0) and a train prediction.
    abs_pcts: list[float] = []
    for r in test_daily:
        try:
            d = int(r["dow"]); actual = float(r["sales"])
        except (KeyError, TypeError, ValueError):
            continue
        if actual <= 0:
            continue
        pred = train_means.get(d, grand_train)
        abs_pcts.append(abs(pred - actual) / actual)
    mape = round(sum(abs_pcts) / len(abs_pcts) * 100, 1) if abs_pcts else None
    accuracy = round(max(0.0, 100.0 - mape), 1) if mape is not None else None

    # Best-day hit: train top-2 DOW vs test top-2 DOW (by mean).
    train_top2 = {d for d, _ in sorted(train_means.items(), key=lambda kv: kv[1], reverse=True)[:2]}
    test_top2 = {d for d, _ in sorted(test_means.items(), key=lambda kv: kv[1], reverse=True)[:2]}
    best_day_hit = len(train_top2 & test_top2)

    if mape is None:
        verdict = "ทดสอบไม่ได้ (ยอดขายช่วง test เป็นศูนย์)"
    elif mape <= 20 and best_day_hit >= 1:
        verdict = f"แม่นยำดี — คลาดเฉลี่ย {mape:.0f}% และทายวันขายดีถูก {best_day_hit}/2 วัน เชื่อถือได้"
    elif mape <= 35:
        verdict = f"พอใช้ — คลาดเฉลี่ย {mape:.0f}% ใช้เป็นแนวทางได้ แต่อย่ายึดตายตัว"
    else:
        verdict = f"ยังไม่น่าเชื่อถือ — คลาดเฉลี่ย {mape:.0f}% รูปแบบ DOW ยังไม่นิ่ง อย่าสั่งของตามนี้ล้วน ๆ"

    return {
        "train_days": len(train_daily),
        "test_days": len(test_daily),
        "mape_pct": mape,
        "accuracy_pct": accuracy,
        "best_day_hit": best_day_hit,
        "verdict_th": verdict,
    }


@router.get("/inventory/ai-order-advice/backtest")
def ai_order_advice_backtest(
    branch: str = Query(DEFAULT_BRANCH),
    train_weeks: int = Query(8, ge=2, le=52),
    test_weeks: int = Query(4, ge=1, le=26),
):
    """F8 — measure how trustworthy /inventory/ai-order-advice is: train the
    day-of-week sales pattern on the older `train_weeks`, then score it against
    the held-out newer `test_weeks` (MAPE + best-day hit). Read-only/advisory."""
    today = date.today()
    test_start = today - timedelta(weeks=test_weeks)
    train_start = test_start - timedelta(weeks=train_weeks)

    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT entry_date,
                          EXTRACT(DOW FROM entry_date)::int AS dow,
                          COALESCE(SUM(amount), 0)::float   AS sales
                   FROM public.v_daybook
                   WHERE direction = 'income'
                     AND source IN ('pos_sale','rider_income_grab','rider_income_lineman')
                     AND branch_code = %s
                     AND entry_date >= %s AND entry_date < %s
                   GROUP BY entry_date
                   ORDER BY entry_date""",
                (branch, train_start, today),
            )
            rows = _rows_to_dicts(cur)
    finally:
        conn.close()

    train_daily = [
        {"date": r["entry_date"].isoformat(), "dow": int(r["dow"]), "sales": float(r["sales"])}
        for r in rows if r["entry_date"] < test_start
    ]
    test_daily = [
        {"date": r["entry_date"].isoformat(), "dow": int(r["dow"]), "sales": float(r["sales"])}
        for r in rows if r["entry_date"] >= test_start
    ]

    report = backtest_dow(train_daily, test_daily)
    report.update({
        "as_of": today.isoformat(),
        "branch_code": branch,
        "train_weeks": train_weeks,
        "test_weeks": test_weeks,
    })
    return report
