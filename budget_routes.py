"""
budget_routes.py — Phase 8: Budget Management
==============================================
Endpoints:
  GET  /budget/targets          — list all budget targets for a month
  PUT  /budget/targets          — upsert (set/update) a budget target
  DELETE /budget/targets/{id}   — delete a budget target
  GET  /budget/status           — actual vs budget for a month (v_budget_status)
  POST /budget/check-alerts     — check all categories, push LINE if any is over budget
"""

import logging
import os
from datetime import date
from typing import Optional

import psycopg2
import psycopg2.extras
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

log = logging.getLogger("vexonhq-budget")
router = APIRouter(prefix="/budget", tags=["budget"])


def _get_db():
    return psycopg2.connect(os.environ["DATABASE_URL"])


# ─────────────────────────────────────────────
# Models
# ─────────────────────────────────────────────

class BudgetUpsert(BaseModel):
    month: str          # 'YYYY-MM'
    category_code: str
    amount: float
    branch_code: str = "thawi_watthana"
    notes: Optional[str] = None


# ─────────────────────────────────────────────
# LINE push helper (reuse pattern from line_bot_routes)
# ─────────────────────────────────────────────

import json, urllib.request, urllib.error

LINE_PUSH_URL = "https://api.line.me/v2/bot/message/push"

def _push_line(text: str):
    token = os.environ.get("LINE_CHANNEL_TOKEN", "")
    user_id = os.environ.get("LINE_USER_ID", "")
    if not token or not user_id:
        log.warning("LINE not configured — skipping push")
        return
    payload = json.dumps({"to": user_id, "messages": [{"type": "text", "text": text}]}).encode("utf-8")
    req = urllib.request.Request(LINE_PUSH_URL, data=payload,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10):
            pass
    except Exception as e:
        log.error("LINE push failed: %s", e)


# ─────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────

@router.get("/targets")
def list_targets(month: str, branch_code: str = "thawi_watthana"):
    """List all budget targets for a month (YYYY-MM)."""
    conn = _get_db()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("""
            SELECT b.id, b.month, b.category_code,
                   COALESCE(ec.name_th, b.category_code) AS category_name_th,
                   b.amount, b.branch_code, b.notes, b.updated_at
            FROM public.budget_targets b
            LEFT JOIN public.expense_categories ec ON ec.code = b.category_code
            WHERE b.month = %s AND b.branch_code = %s
            ORDER BY ec.sort_order NULLS LAST, b.category_code
        """, (month, branch_code))
        rows = cur.fetchall()
        return {"success": True, "month": month, "targets": [dict(r) for r in rows]}
    finally:
        conn.close()


@router.put("/targets")
def upsert_target(body: BudgetUpsert):
    """Create or update a budget target. Returns the upserted row."""
    conn = _get_db()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("""
            INSERT INTO public.budget_targets (month, category_code, amount, branch_code, notes, updated_at)
            VALUES (%s, %s, %s, %s, %s, now())
            ON CONFLICT (month, category_code, branch_code)
            DO UPDATE SET amount = EXCLUDED.amount,
                          notes  = EXCLUDED.notes,
                          updated_at = now()
            RETURNING *
        """, (body.month, body.category_code, body.amount, body.branch_code, body.notes))
        conn.commit()
        row = cur.fetchone()
        return {"success": True, "target": dict(row)}
    finally:
        conn.close()


@router.delete("/targets/{target_id}")
def delete_target(target_id: str):
    """Delete a budget target by ID."""
    conn = _get_db()
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM public.budget_targets WHERE id = %s RETURNING id", (target_id,))
        conn.commit()
        if cur.rowcount == 0:
            raise HTTPException(404, "Budget target not found")
        return {"success": True}
    finally:
        conn.close()


@router.get("/status")
def budget_status(month: str, branch_code: str = "thawi_watthana"):
    """
    Actual vs budget for all categories in a month.
    Returns status: ok / warning (≥90%) / over (≥100%)
    """
    conn = _get_db()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("""
            SELECT category_code, category_name_th, category_name_en,
                   budget_amount, actual_amount, variance, pct_used, status
            FROM public.v_budget_status
            WHERE month = %s AND branch_code = %s
            ORDER BY pct_used DESC NULLS LAST
        """, (month, branch_code))
        rows = [dict(r) for r in cur.fetchall()]

        over = [r for r in rows if r["status"] == "over"]
        warning = [r for r in rows if r["status"] == "warning"]

        return {
            "success": True,
            "month": month,
            "summary": {
                "total_budget": sum(float(r["budget_amount"] or 0) for r in rows),
                "total_actual": sum(float(r["actual_amount"] or 0) for r in rows),
                "over_count": len(over),
                "warning_count": len(warning),
            },
            "categories": rows,
        }
    finally:
        conn.close()


def run_budget_alert_check(month: Optional[str] = None, branch_code: str = "thawi_watthana") -> dict:
    """
    Core logic: query budget status and push LINE for over + warning categories.
    Called by the HTTP endpoint AND by the APScheduler cron job (20:00 daily).
    Returns summary dict.
    """
    if not month:
        month = date.today().strftime("%Y-%m")

    conn = _get_db()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("""
            SELECT category_name_th, category_code, budget_amount, actual_amount, pct_used, variance, status
            FROM public.v_budget_status
            WHERE month = %s AND branch_code = %s AND status IN ('over', 'warning')
            ORDER BY pct_used DESC
        """, (month, branch_code))
        alert_rows = [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()

    if not alert_rows:
        log.info("Budget check %s: all categories within budget", month)
        return {"success": True, "month": month, "alerts_sent": 0, "details": []}

    # Build a single LINE message grouping over + warning
    over_items   = [r for r in alert_rows if r["status"] == "over"]
    warn_items   = [r for r in alert_rows if r["status"] == "warning"]

    lines = [f"📊 สรุปงบประมาณ {month}"]
    if over_items:
        lines.append("\n🔴 เกินงบประมาณ:")
        for row in over_items:
            name   = row.get("category_name_th") or row.get("category_code", "?")
            budget = float(row["budget_amount"] or 0)
            actual = float(row["actual_amount"] or 0)
            pct    = float(row["pct_used"] or 0)
            excess = actual - budget
            lines.append(f"  • {name}: ฿{actual:,.0f} / ฿{budget:,.0f} ({pct:.0f}%, +฿{excess:,.0f})")

    if warn_items:
        lines.append("\n🟡 ใกล้เต็มงบ (≥80%):")
        for row in warn_items:
            name   = row.get("category_name_th") or row.get("category_code", "?")
            budget = float(row["budget_amount"] or 0)
            actual = float(row["actual_amount"] or 0)
            pct    = float(row["pct_used"] or 0)
            remain = budget - actual
            lines.append(f"  • {name}: ฿{actual:,.0f} / ฿{budget:,.0f} ({pct:.0f}%, เหลือ ฿{remain:,.0f})")

    _push_line("\n".join(lines))

    details = [
        {
            "category": r.get("category_name_th") or r.get("category_code", "?"),
            "status":   r["status"],
            "pct_used": float(r["pct_used"] or 0),
        }
        for r in alert_rows
    ]
    log.info("Budget alert %s: sent %d items (over=%d warn=%d)", month, len(alert_rows), len(over_items), len(warn_items))
    return {"success": True, "month": month, "alerts_sent": len(alert_rows), "details": details}


@router.post("/check-alerts")
def check_budget_alerts(month: Optional[str] = None, branch_code: str = "thawi_watthana"):
    """
    HTTP endpoint: manually trigger budget alert check.
    Also called automatically by APScheduler at 20:00 daily via run_budget_alert_check().
    """
    return run_budget_alert_check(month=month, branch_code=branch_code)


# ── GET /budget/suggest ──────────────────────────────────────────────────────

@router.get("/suggest")
def suggest_budget(
    month: str,
    lookback: int = 3,
    branch_code: str = "thawi_watthana",
    buffer_pct: float = 10.0,
):
    """
    Suggest budget amounts for `month` based on average spending over the past
    `lookback` months + a buffer.

    Query: vendor_bills (confirmed, expense) + manual_entries (expense) +
           bank_statement_entries (expense) — same sources as v_daybook.
    Returns per-category: avg_actual, suggested_amount, months_with_data.
    """
    try:
        y, m = int(month[:4]), int(month[5:7])
    except (ValueError, IndexError):
        raise HTTPException(400, "month must be YYYY-MM")

    # Start of lookback window = month - lookback months
    start_m, start_y = m - lookback, y
    while start_m <= 0:
        start_m += 12
        start_y -= 1
    start_date = date(start_y, start_m, 1)
    end_date   = date(y, m, 1)  # exclusive — up to but not including target month

    conn = _get_db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            # ── vendor_bills actuals ──
            cur.execute("""
                SELECT
                    vb.category_code,
                    COALESCE(ec.name_th, vb.category_code) AS category_name_th,
                    DATE_TRUNC('month', vb.bill_date)::date AS month_dt,
                    SUM(vb.amount)::numeric AS month_total
                FROM public.vendor_bills vb
                LEFT JOIN public.expense_categories ec ON ec.code = vb.category_code
                WHERE vb.branch_code = %s
                  AND vb.confirmed = TRUE
                  AND vb.direction = 'expense'
                  AND vb.bill_date >= %s
                  AND vb.bill_date < %s
                  AND vb.category_code IS NOT NULL
                GROUP BY vb.category_code, ec.name_th, DATE_TRUNC('month', vb.bill_date)::date
            """, (branch_code, start_date, end_date))
            bill_rows = [dict(r) for r in cur.fetchall()]

            # ── manual_entries actuals ──
            cur.execute("""
                SELECT
                    me.category_code,
                    COALESCE(ec.name_th, me.category_code) AS category_name_th,
                    DATE_TRUNC('month', me.entry_date)::date AS month_dt,
                    SUM(me.amount)::numeric AS month_total
                FROM public.manual_entries me
                LEFT JOIN public.expense_categories ec ON ec.code = me.category_code
                WHERE me.branch_code = %s
                  AND me.direction = 'expense'
                  AND me.entry_date >= %s
                  AND me.entry_date < %s
                  AND me.category_code IS NOT NULL
                GROUP BY me.category_code, ec.name_th, DATE_TRUNC('month', me.entry_date)::date
            """, (branch_code, start_date, end_date))
            manual_rows = [dict(r) for r in cur.fetchall()]

            # ── bank_statement_entries actuals ──
            cur.execute("""
                SELECT
                    bse.category_code,
                    COALESCE(ec.name_th, bse.category_code) AS category_name_th,
                    DATE_TRUNC('month', bse.txn_date)::date AS month_dt,
                    SUM(bse.debit)::numeric AS month_total
                FROM public.bank_statement_entries bse
                LEFT JOIN public.expense_categories ec ON ec.code = bse.category_code
                WHERE bse.branch_code = %s
                  AND bse.category_code IS NOT NULL
                  AND bse.debit > 0
                  AND bse.txn_date >= %s
                  AND bse.txn_date < %s
                GROUP BY bse.category_code, ec.name_th, DATE_TRUNC('month', bse.txn_date)::date
            """, (branch_code, start_date, end_date))
            bank_rows = [dict(r) for r in cur.fetchall()]

            # ── current budget targets (to show in comparison) ──
            cur.execute("""
                SELECT category_code, amount
                FROM public.budget_targets
                WHERE month = %s AND branch_code = %s
            """, (month, branch_code))
            existing_budget = {r["category_code"]: float(r["amount"] or 0) for r in cur.fetchall()}

    finally:
        conn.close()

    # ── Merge all rows ─────────────────────────────────────────────────────
    # group by category_code × month_dt
    from collections import defaultdict
    monthly: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    names: dict[str, str] = {}

    for row in bill_rows + manual_rows + bank_rows:
        cat = row["category_code"]
        mon = str(row["month_dt"])[:7]   # YYYY-MM
        monthly[cat][mon] += float(row["month_total"] or 0)
        if cat not in names and row.get("category_name_th"):
            names[cat] = row["category_name_th"]

    # ── Build suggestions ─────────────────────────────────────────────────
    suggestions = []
    for cat, month_totals in monthly.items():
        totals = [v for v in month_totals.values() if v > 0]
        if not totals:
            continue
        avg = sum(totals) / len(totals)
        suggested = round(avg * (1 + buffer_pct / 100) / 100) * 100  # round to nearest 100
        suggestions.append({
            "category_code":     cat,
            "category_name_th":  names.get(cat, cat),
            "months_with_data":  len(totals),
            "lookback_months":   lookback,
            "avg_monthly":       round(avg, 2),
            "buffer_pct":        buffer_pct,
            "suggested_amount":  suggested,
            "current_budget":    existing_budget.get(cat, 0),
            "diff":              round(suggested - existing_budget.get(cat, 0), 2),
        })

    suggestions.sort(key=lambda x: x["avg_monthly"], reverse=True)
    total_suggested = sum(s["suggested_amount"] for s in suggestions)

    return {
        "month":           month,
        "lookback_months": lookback,
        "buffer_pct":      buffer_pct,
        "branch_code":     branch_code,
        "data_from":       str(start_date),
        "data_to":         str(end_date),
        "total_suggested": total_suggested,
        "suggestions":     suggestions,
        "note":            f"คำนวณจากค่าใช้จ่ายจริงย้อนหลัง {lookback} เดือน บวก {buffer_pct:.0f}% เพื่อเป็น buffer",
    }
