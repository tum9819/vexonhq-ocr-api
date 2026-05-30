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

import asyncio
import base64
import io
import logging
import os
from datetime import date, datetime, timedelta
from typing import Any, Optional
from uuid import UUID

import psycopg2
from fastapi import APIRouter, File, HTTPException, Query, UploadFile
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


# ─────────────────────────────────────────────────────────
# POST /bills/payment/slip-match  (Phase 32)
# Upload a payment slip → GPT Vision reads amount →
# find matching unpaid vendor_bills
# ─────────────────────────────────────────────────────────

def _call_gpt_vision_for_slip(image_bytes: bytes, mime: str) -> dict:
    """Send slip image to GPT Vision and return extracted fields."""
    import urllib.request, urllib.error, json as _json

    openai_key = os.environ.get("OPENAI_API_KEY", "")
    if not openai_key:
        raise RuntimeError("OPENAI_API_KEY not set")

    b64 = base64.b64encode(image_bytes).decode()
    data_url = f"data:{mime};base64,{b64}"

    prompt = (
        "คุณคือผู้ช่วยอ่านสลิปโอนเงิน/ใบเสร็จการชำระเงิน\n"
        "กรุณาอ่านข้อมูลจากภาพสลิปนี้แล้วตอบ JSON เท่านั้น ไม่มีคำอธิบายเพิ่มเติม\n"
        "Format: {\"amount\": <จำนวนเงิน ตัวเลขทศนิยม>, "
        "\"bank\": \"<ชื่อธนาคาร>\", "
        "\"ref\": \"<เลขอ้างอิง/Ref No>\", "
        "\"txn_date\": \"<YYYY-MM-DD หรือ null>\", "
        "\"note\": \"<หมายเหตุถ้ามี หรือ null>\"}\n"
        "ถ้าไม่แน่ใจค่าไหนให้ใส่ null"
    )

    body = {
        "model": os.environ.get("OPENAI_VISION_MODEL", "gpt-4o"),
        "max_tokens": 300,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": data_url, "detail": "high"}},
                ],
            }
        ],
    }

    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=_json.dumps(body).encode(),
        headers={
            "Authorization": f"Bearer {openai_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = _json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"OpenAI API error: {e.code} {e.read()[:200]}")

    raw_text = result["choices"][0]["message"]["content"].strip()
    # Strip markdown code fences if present
    if raw_text.startswith("```"):
        raw_text = raw_text.split("```")[1]
        if raw_text.startswith("json"):
            raw_text = raw_text[4:]
    return _json.loads(raw_text)


@router.post("/bills/payment/slip-match")
async def slip_match(file: UploadFile = File(...)):
    """
    Phase 32 — อัพโหลดสลิปโอนเงิน → GPT Vision อ่านยอด →
    จับคู่กับ vendor_bills ที่ค้างชำระยอดใกล้เคียง

    Returns:
        extracted: {amount, bank, ref, txn_date}
        candidates: [{bill_id, vendor_name, invoice_no, bill_date, amount, diff}]
    """
    # Read file bytes
    image_bytes = await file.read()
    if len(image_bytes) > 10 * 1024 * 1024:
        raise HTTPException(400, "ไฟล์ใหญ่เกิน 10 MB")

    # Detect MIME
    fname = (file.filename or "").lower()
    if fname.endswith(".pdf"):
        raise HTTPException(400, "กรุณาอัพโหลดรูปภาพสลิป (JPG/PNG) ไม่ใช่ PDF")
    mime = "image/jpeg" if fname.endswith((".jpg", ".jpeg")) else "image/png"

    # Call GPT Vision (blocking — run off the event loop so it doesn't freeze the server)
    try:
        extracted = await asyncio.to_thread(_call_gpt_vision_for_slip, image_bytes, mime)
    except Exception as e:
        log.error("slip_match: vision failed: %s", e)
        raise HTTPException(500, f"ไม่สามารถอ่านสลิปได้: {e}")

    slip_amount = extracted.get("amount")
    if slip_amount is None:
        raise HTTPException(422, "ไม่พบยอดเงินในสลิป — กรุณาลองใหม่ด้วยรูปที่ชัดขึ้น")

    try:
        slip_amount = float(slip_amount)
    except (TypeError, ValueError):
        raise HTTPException(422, f"ยอดเงินไม่ถูกต้อง: {slip_amount!r}")

    # Find unpaid vendor_bills within ±10 baht of slip amount
    tolerance = 10.0
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT id, vendor_name, invoice_no, bill_date, due_date,
                          amount, category_code, payment_status
                   FROM public.vendor_bills
                   WHERE review_status = 'confirmed'
                     AND payment_status = 'unpaid'
                     AND ABS(amount - %s) <= %s
                   ORDER BY ABS(amount - %s) ASC, bill_date DESC
                   LIMIT 10""",
                (slip_amount, tolerance, slip_amount),
            )
            rows = _rows_to_dicts(cur)
    finally:
        conn.close()

    candidates = []
    for r in rows:
        bill_amount = float(r.get("amount") or 0)
        candidates.append({
            **r,
            "diff": round(slip_amount - bill_amount, 2),
        })

    return {
        "extracted": extracted,
        "slip_amount": slip_amount,
        "candidates": candidates,
        "matched_count": len(candidates),
    }
