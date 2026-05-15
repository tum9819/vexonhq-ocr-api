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


@router.post("/check-alerts")
def check_budget_alerts(month: Optional[str] = None, branch_code: str = "thawi_watthana"):
    """
    Check all budget categories for the month.
    Push LINE alert for any category that is OVER budget.
    Called automatically after each new expense is confirmed.
    """
    if not month:
        month = date.today().strftime("%Y-%m")

    conn = _get_db()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("""
            SELECT category_name_th, budget_amount, actual_amount, pct_used, variance
            FROM public.v_budget_status
            WHERE month = %s AND branch_code = %s AND status = 'over'
            ORDER BY pct_used DESC
        """, (month, branch_code))
        over_rows = [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()

    alerts_sent = []
    for row in over_rows:
        name = row["category_name_th"] or row.get("category_code", "?")
        budget = float(row["budget_amount"] or 0)
        actual = float(row["actual_amount"] or 0)
        pct = float(row["pct_used"] or 0)
        excess = actual - budget

        text = (
            f"⚠️ เกินงบประมาณ!\n"
            f"📂 {name}\n"
            f"💰 ใช้ไป: ฿{actual:,.0f} / งบ ฿{budget:,.0f}\n"
            f"📈 {pct:.1f}% (+฿{excess:,.0f})"
        )
        _push_line(text)
        alerts_sent.append({"category": name, "pct_used": pct, "excess": excess})

    return {
        "success": True,
        "month": month,
        "alerts_sent": len(alerts_sent),
        "details": alerts_sent,
    }
