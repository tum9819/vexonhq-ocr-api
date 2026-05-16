"""
VEXONHQ Phase 15 — Bill Payment Tracker
=========================================
Track payment status of confirmed vendor bills per month.

Endpoints:
  GET   /bills/payment              — list confirmed bills for a month + payment status
  PATCH /bills/payment/{id}         — update payment_status + paid_date
  GET   /bills/payment/summary      — unpaid count + total (for dashboard badge)
  POST  /bills/payment/line-alert   — push LINE for bills unpaid > 7 days (cron Monday 09:00)

In main.py add:
    from bill_payment_routes import router as bill_payment_router
    app.include_router(bill_payment_router)
"""

from __future__ import annotations

import logging
import os
from datetime import date, datetime, timedelta
from typing import Any, Optional
from uuid import UUID

import psycopg2
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

try:
    from main import get_db_conn  # type: ignore
except ImportError:
    def get_db_conn():
        return psycopg2.connect(os.environ["DATABASE_URL"])

log = logging.getLogger("bill_payment_routes")
router = APIRouter(tags=["bill-payment"])

DEFAULT_BRANCH = "thawi_watthana"

VALID_STATUSES = {"unpaid", "paid", "credit_card", "partial"}
STATUS_LABEL = {
    "unpaid":      "ค้างจ่าย",
    "paid":        "จ่ายแล้ว",
    "credit_card": "บัตรเครดิต",
    "partial":     "จ่ายบางส่วน",
}


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
    """Return (start, end) date for a YYYY-MM string."""
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
# Pydantic bodies
# ─────────────────────────────────────────────────────────

class BillPaymentPatch(BaseModel):
    payment_status: str
    paid_date: Optional[date] = None


# ─────────────────────────────────────────────────────────
# GET /bills/payment
# ─────────────────────────────────────────────────────────

@router.get("/bills/payment")
def list_bills_payment(
    month: Optional[str] = Query(None, description="YYYY-MM, defaults to current month"),
    status: Optional[str] = Query(None, description="unpaid | paid | credit_card | partial"),
    vendor: Optional[str] = Query(None, description="ค้นหาชื่อ vendor (partial match)"),
    branch: str = Query(DEFAULT_BRANCH),
):
    """
    List confirmed vendor bills for a month with payment status.
    Used by /bills/payment page.
    """
    start, end = _month_bounds(month)

    status_filter = ""
    vendor_filter = ""
    params: list[Any] = [branch, start, end]
    if status and status in VALID_STATUSES:
        status_filter = "AND vb.payment_status = %s"
        params.append(status)
    if vendor and vendor.strip():
        vendor_filter = "AND vb.vendor_name ILIKE %s"
        params.append(f"%{vendor.strip()}%")

    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT
                    vb.id,
                    vb.vendor_name,
                    vb.invoice_no,
                    vb.bill_date,
                    vb.due_date,
                    vb.amount,
                    vb.category_code,
                    COALESCE(ec.name_th, vb.category_code) AS category_name,
                    vb.payment_status,
                    vb.paid_date,
                    vb.review_status,
                    vb.notes
                FROM public.vendor_bills vb
                LEFT JOIN public.expense_categories ec ON ec.code = vb.category_code
                WHERE vb.review_status = 'confirmed'
                  AND COALESCE(vb.branch_code, %s) = %s
                  AND vb.bill_date >= %s
                  AND vb.bill_date < %s
                  {status_filter}
                  {vendor_filter}
                ORDER BY vb.bill_date DESC, vb.amount DESC
                """,
                [branch] + params,
            )
            bills = _rows_to_dicts(cur)

        # ── Summary totals ────────────────────────────────────
        summary: dict[str, float] = {s: 0.0 for s in VALID_STATUSES}
        for b in bills:
            s = b.get("payment_status") or "unpaid"
            summary[s] = summary.get(s, 0.0) + float(b.get("amount") or 0)

        return {
            "month": start.strftime("%Y-%m"),
            "bills": bills,
            "summary": summary,
            "total_bills": len(bills),
        }
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────
# PATCH /bills/payment/{id}
# ─────────────────────────────────────────────────────────

@router.patch("/bills/payment/{bill_id}")
def update_bill_payment(bill_id: str, body: BillPaymentPatch):
    """Update payment_status (and optionally paid_date) for a confirmed bill."""
    if body.payment_status not in VALID_STATUSES:
        raise HTTPException(400, f"payment_status must be one of {sorted(VALID_STATUSES)}")

    try:
        uid = UUID(bill_id)
    except (ValueError, AttributeError):
        raise HTTPException(400, f"Invalid bill_id: {bill_id!r}")

    # Auto-set paid_date to today when marking paid/credit_card
    paid_date = body.paid_date
    if paid_date is None and body.payment_status in ("paid", "credit_card"):
        paid_date = date.today()
    # Clear paid_date when marking unpaid
    if body.payment_status == "unpaid":
        paid_date = None

    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE public.vendor_bills
                   SET payment_status = %s,
                       paid_date = %s,
                       updated_at = now()
                   WHERE id = %s
                     AND review_status = 'confirmed'
                   RETURNING id, vendor_name, invoice_no, bill_date,
                             amount, payment_status, paid_date""",
                (body.payment_status, paid_date, str(uid)),
            )
            row = cur.fetchone()
            if not row:
                raise HTTPException(404, "Bill not found or not confirmed")
            conn.commit()
            cols = [d[0] for d in cur.description]
            result = dict(zip(cols, row))
            # Serialize
            for k, v in result.items():
                if isinstance(v, UUID):
                    result[k] = str(v)
                elif isinstance(v, (date, datetime)):
                    result[k] = v.isoformat()
            return result
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────
# GET /bills/payment/summary  (for dashboard badge)
# ─────────────────────────────────────────────────────────

@router.get("/bills/payment/summary")
def bills_payment_summary(
    month: Optional[str] = Query(None),
    branch: str = Query(DEFAULT_BRANCH),
):
    """
    Returns unpaid bill count + total for the current month.
    Used by Dashboard to show ⭕ badge.
    """
    start, end = _month_bounds(month)
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT
                       COUNT(*) AS unpaid_count,
                       COALESCE(SUM(amount), 0)::numeric AS unpaid_total
                   FROM public.vendor_bills
                   WHERE review_status = 'confirmed'
                     AND payment_status = 'unpaid'
                     AND COALESCE(branch_code, %s) = %s
                     AND bill_date >= %s
                     AND bill_date < %s""",
                (branch, branch, start, end),
            )
            row = cur.fetchone()
            return {
                "month": start.strftime("%Y-%m"),
                "unpaid_count": int(row[0] or 0),
                "unpaid_total": float(row[1] or 0),
            }
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────
# POST /bills/payment/line-alert  (Phase 15B)
# Coolify cron: 0 2 * * 1  (Monday 09:00 Bangkok = 02:00 UTC)
# ─────────────────────────────────────────────────────────

@router.post("/bills/payment/line-alert")
def bills_payment_line_alert():
    """
    Phase 15B — ส่ง LINE แจ้งเตือนบิลค้างจ่าย > 7 วัน
    Coolify cron: 0 2 * * 1 (จันทร์ 09:00 Bangkok)
    """
    from line_bot_routes import _push_text  # noqa: PLC0415

    today = date.today()
    cutoff = today - timedelta(days=7)

    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT vendor_name, invoice_no, bill_date, amount
                   FROM public.vendor_bills
                   WHERE review_status = 'confirmed'
                     AND payment_status = 'unpaid'
                     AND bill_date <= %s
                   ORDER BY bill_date ASC
                   LIMIT 20""",
                (cutoff,),
            )
            rows = _rows_to_dicts(cur)
    finally:
        conn.close()

    if not rows:
        return {"sent": False, "message": "ไม่มีบิลค้างจ่ายเกิน 7 วัน", "count": 0}

    lines = [f"⭕ แจ้งเตือนบิลค้างจ่าย > 7 วัน (ทุกวันจันทร์)\n"]
    for r in rows:
        vendor = r.get("vendor_name") or "ไม่ระบุ"
        inv = f" #{r['invoice_no']}" if r.get("invoice_no") else ""
        bill_date = r.get("bill_date", "")
        amount = float(r.get("amount") or 0)
        days_late = (today - date.fromisoformat(bill_date)).days if bill_date else "?"
        lines.append(f"• {vendor}{inv}\n  วันที่บิล: {bill_date} ({days_late} วันที่แล้ว)\n  ฿{amount:,.2f}\n")

    lines.append(f"รวม {len(rows)} รายการ — กรุณาอัปเดตสถานะที่ /bills/payment")
    message = "\n".join(lines)

    _push_text(message)
    return {"sent": True, "count": len(rows)}
