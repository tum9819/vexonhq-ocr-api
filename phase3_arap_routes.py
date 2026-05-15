"""
VEXONHQ Phase 3 F1 — AR/AP Routes
=================================
Backend endpoints for Accounts Receivable + Accounts Payable.
Companion to 07_phase3_arap_schema.sql (3 tables + 2 views + 2 triggers).

Drop into vexonhq-ocr-api repo next to main.py + pos_import.py + phase2_routes.py.

In main.py add:
    from phase3_arap_routes import router as phase3_arap_router
    app.include_router(phase3_arap_router)

Endpoints (9 total):

    Counterparties
      GET    /counterparties              — list (filter by type/active/search)
      POST   /counterparties              — create new vendor/customer
      PATCH  /counterparties/{id}         — edit
      DELETE /counterparties/{id}         — soft delete (is_active=false)

    AR/AP entries
      GET    /ar-ap/list                  — list outstanding entries
      GET    /ar-ap/entries/{id}          — single entry + payments
      POST   /ar-ap/entries               — create entry manually
      PATCH  /ar-ap/entries/{id}          — edit notes/due_date
      DELETE /ar-ap/entries/{id}          — cancel (status='cancelled')

    Payments
      POST   /ar-ap/payments              — record partial/full payment
      DELETE /ar-ap/payments/{id}         — reverse payment (trigger recomputes status)

    Dashboard
      GET    /ar-ap/summary               — counts + totals per direction (for cards)

Dependencies: psycopg2-binary (already in requirements.txt)
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Any, Optional
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

# Reuse main.get_db_conn (same pattern as pos_import.py + phase2_routes.py)
try:
    from main import get_db_conn  # type: ignore
except ImportError:
    import os
    import psycopg2
    def get_db_conn():
        return psycopg2.connect(os.environ["DATABASE_URL"])


logger = logging.getLogger("phase3_arap_routes")
router = APIRouter(tags=["phase3-arap"])

DEFAULT_BRANCH = "thawi_watthana"

VALID_DIRECTIONS = {"receivable", "payable"}
VALID_STATUSES = {"pending", "partial", "paid", "cancelled"}
VALID_CP_TYPES = {"supplier", "customer", "platform", "recurring", "employee", "service"}


# ============================================================
# Helpers (mirror phase2_routes._serialize_row / _rows_to_dicts)
# ============================================================

def _serialize_row(row: dict) -> dict:
    """Convert UUID/date/datetime/Decimal to JSON-safe types."""
    out: dict[str, Any] = {}
    for k, v in row.items():
        if isinstance(v, UUID):
            out[k] = str(v)
        elif isinstance(v, (datetime, date)):
            out[k] = v.isoformat()
        elif hasattr(v, "__float__") and not isinstance(v, (int, float, bool)):
            out[k] = float(v)
        else:
            out[k] = v
    return out


def _rows_to_dicts(cur) -> list[dict]:
    """Convert cursor results to list[dict] using column names."""
    if cur.description is None:
        return []
    cols = [d[0] for d in cur.description]
    return [_serialize_row(dict(zip(cols, r))) for r in cur.fetchall()]


def _parse_uuid(value: Any, field_name: str = "id") -> UUID:
    """Parse string into UUID, raise 400 if malformed."""
    try:
        return UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(400, f"Invalid UUID for {field_name}: {value!r}")


def _slugify(text: str) -> str:
    """Best-effort slug for auto-generated counterparty codes.
    Strips spaces, lowercases, replaces non-alphanumeric with underscore."""
    import re
    s = (text or "").strip().lower()
    s = re.sub(r"[^a-z0-9ก-๙]+", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return s[:60] if s else "unnamed"


# ============================================================
# Pydantic request bodies
# ============================================================

class CounterpartyCreate(BaseModel):
    code: Optional[str] = None        # auto-slug from name if absent
    name: str
    type: str
    tax_id: Optional[str] = None
    contact_phone: Optional[str] = None
    contact_email: Optional[str] = None
    default_credit_days: int = 0
    notes: Optional[str] = None


class CounterpartyPatch(BaseModel):
    name: Optional[str] = None
    type: Optional[str] = None
    tax_id: Optional[str] = None
    contact_phone: Optional[str] = None
    contact_email: Optional[str] = None
    default_credit_days: Optional[int] = None
    notes: Optional[str] = None
    is_active: Optional[bool] = None


class EntryCreate(BaseModel):
    direction: str                                       # 'receivable' | 'payable'
    counterparty_id: Optional[str] = None
    counterparty_name_snapshot: Optional[str] = None     # used when counterparty_id is null
    doc_no: Optional[str] = None
    doc_date: date
    due_date: Optional[date] = None
    amount_total: float = Field(gt=0)
    category_code: Optional[str] = None
    notes: Optional[str] = None
    branch_code: str = DEFAULT_BRANCH
    created_by: Optional[str] = None


class EntryPatch(BaseModel):
    doc_no: Optional[str] = None
    due_date: Optional[date] = None
    category_code: Optional[str] = None
    notes: Optional[str] = None


class PaymentCreate(BaseModel):
    entry_id: str
    payment_date: date
    amount: float = Field(gt=0)
    method: Optional[str] = None
    reference_no: Optional[str] = None
    notes: Optional[str] = None
    created_by: Optional[str] = None


class CancelBody(BaseModel):
    reason: Optional[str] = None


# ============================================================
# SECTION A — Counterparties
# ============================================================

@router.get("/counterparties")
def list_counterparties(
    type: Optional[str] = Query(None, description="Filter by type"),
    active: Optional[bool] = Query(None, description="Filter by is_active"),
    q: Optional[str] = Query(None, description="Search by name (ILIKE)"),
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
):
    """List counterparties for AR/AP dropdowns."""
    if type and type not in VALID_CP_TYPES and type != "":
        # Allow custom types too, just warn
        logger.info("Unknown counterparty type filter: %s", type)

    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            where = []
            params: list[Any] = []
            if type:
                where.append("type = %s")
                params.append(type)
            if active is not None:
                where.append("is_active = %s")
                params.append(active)
            if q:
                where.append("(name ILIKE %s OR code ILIKE %s)")
                params.extend([f"%{q}%", f"%{q}%"])

            sql = "SELECT id, code, name, type, tax_id, default_credit_days, " \
                  "       contact_phone, contact_email, notes, is_active, created_at " \
                  "FROM public.counterparties"
            if where:
                sql += " WHERE " + " AND ".join(where)
            sql += " ORDER BY type, name LIMIT %s OFFSET %s"
            params.extend([limit, offset])

            cur.execute(sql, params)
            rows = _rows_to_dicts(cur)

            # total (separate count for pagination)
            count_sql = "SELECT count(*) FROM public.counterparties"
            count_params: list[Any] = []
            if where:
                count_sql += " WHERE " + " AND ".join(where)
                count_params = params[:-2]
            cur.execute(count_sql, count_params)
            total = cur.fetchone()[0]

        return {"rows": rows, "total": total, "limit": limit, "offset": offset}
    finally:
        conn.close()


@router.post("/counterparties")
def create_counterparty(body: CounterpartyCreate):
    """Create a new vendor/customer. Auto-generates code if not provided."""
    if not body.name or not body.name.strip():
        raise HTTPException(400, "name is required")
    if not body.type or not body.type.strip():
        raise HTTPException(400, "type is required")
    if body.default_credit_days < 0:
        raise HTTPException(400, "default_credit_days must be >= 0")

    code = (body.code or _slugify(body.name)).strip().lower()

    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            try:
                cur.execute(
                    """INSERT INTO public.counterparties
                         (code, name, type, tax_id, contact_phone, contact_email,
                          default_credit_days, notes, is_active)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, true)
                       RETURNING id, code, name, type, default_credit_days, is_active""",
                    (code, body.name.strip(), body.type, body.tax_id,
                     body.contact_phone, body.contact_email,
                     body.default_credit_days, body.notes),
                )
            except Exception as e:
                conn.rollback()
                # unique violation → counterparty code already exists
                if "duplicate key" in str(e).lower() or "unique" in str(e).lower():
                    raise HTTPException(409, f"Counterparty code already exists: {code}")
                raise HTTPException(500, f"Insert failed: {e}")
            row = cur.fetchone()
            conn.commit()
            cols = [d[0] for d in cur.description]
            return _serialize_row(dict(zip(cols, row)))
    finally:
        conn.close()


@router.patch("/counterparties/{cp_id}")
def patch_counterparty(cp_id: str, body: CounterpartyPatch):
    """Edit counterparty fields. Only non-null fields are updated."""
    cp_uuid = _parse_uuid(cp_id, "cp_id")

    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    if not updates:
        raise HTTPException(400, "No fields to update")

    set_clauses = ", ".join(f"{k} = %s" for k in updates.keys())
    params = list(updates.values()) + [str(cp_uuid)]

    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"UPDATE public.counterparties SET {set_clauses}, updated_at = now() "
                f"WHERE id = %s "
                f"RETURNING id, code, name, type, is_active",
                params,
            )
            row = cur.fetchone()
            if not row:
                raise HTTPException(404, "Counterparty not found")
            conn.commit()
            cols = [d[0] for d in cur.description]
            return _serialize_row(dict(zip(cols, row)))
    finally:
        conn.close()


@router.delete("/counterparties/{cp_id}")
def soft_delete_counterparty(cp_id: str):
    """Soft delete (is_active = false). Preserves history."""
    cp_uuid = _parse_uuid(cp_id, "cp_id")
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE public.counterparties SET is_active = false, updated_at = now() "
                "WHERE id = %s RETURNING id",
                (str(cp_uuid),),
            )
            row = cur.fetchone()
            if not row:
                raise HTTPException(404, "Counterparty not found")
            conn.commit()
            return {"id": str(cp_uuid), "is_active": False}
    finally:
        conn.close()


# ============================================================
# SECTION B — AR/AP entries
# ============================================================

@router.get("/ar-ap/list")
def list_entries(
    direction: Optional[str] = Query(None, description="receivable | payable"),
    status: Optional[str] = Query(None, description="pending | partial | paid"),
    overdue_only: bool = Query(False),
    counterparty_id: Optional[str] = Query(None),
    date_from: Optional[date] = Query(None, description="filter by doc_date >= "),
    date_to: Optional[date] = Query(None, description="filter by doc_date <= "),
    q: Optional[str] = Query(None, description="search doc_no or counterparty name"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    """List outstanding AR/AP entries with overdue flag computed in view."""
    if direction and direction not in VALID_DIRECTIONS:
        raise HTTPException(400, f"direction must be one of {sorted(VALID_DIRECTIONS)}")
    if status and status not in VALID_STATUSES:
        raise HTTPException(400, f"status must be one of {sorted(VALID_STATUSES)}")

    where: list[str] = []
    params: list[Any] = []

    if direction:
        where.append("direction = %s")
        params.append(direction)
    if status:
        where.append("status = %s")
        params.append(status)
    if overdue_only:
        where.append("is_overdue = true")
    if counterparty_id:
        where.append("counterparty_id = %s")
        params.append(str(_parse_uuid(counterparty_id, "counterparty_id")))
    if date_from:
        where.append("doc_date >= %s")
        params.append(date_from)
    if date_to:
        where.append("doc_date <= %s")
        params.append(date_to)
    if q:
        where.append("(doc_no ILIKE %s OR counterparty_name ILIKE %s)")
        params.extend([f"%{q}%", f"%{q}%"])

    sql_where = (" WHERE " + " AND ".join(where)) if where else ""

    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT * FROM public.v_ar_ap_outstanding{sql_where} "
                f"ORDER BY is_overdue DESC, due_date NULLS LAST, doc_date DESC "
                f"LIMIT %s OFFSET %s",
                params + [limit, offset],
            )
            rows = _rows_to_dicts(cur)

            cur.execute(
                f"SELECT count(*) FROM public.v_ar_ap_outstanding{sql_where}",
                params,
            )
            total = cur.fetchone()[0]
        return {"rows": rows, "total": total, "limit": limit, "offset": offset}
    finally:
        conn.close()


@router.get("/ar-ap/entries/{entry_id}")
def get_entry_with_payments(entry_id: str):
    """Get one entry + all its payments."""
    eid = _parse_uuid(entry_id, "entry_id")
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM public.v_ar_ap_outstanding WHERE id = %s",
                (str(eid),),
            )
            erows = _rows_to_dicts(cur)
            if not erows:
                # Maybe cancelled — try base table
                cur.execute(
                    "SELECT * FROM public.ar_ap_entries WHERE id = %s",
                    (str(eid),),
                )
                erows = _rows_to_dicts(cur)
                if not erows:
                    raise HTTPException(404, "Entry not found")
            entry = erows[0]

            cur.execute(
                "SELECT * FROM public.ar_ap_payments WHERE entry_id = %s "
                "ORDER BY payment_date DESC, created_at DESC",
                (str(eid),),
            )
            payments = _rows_to_dicts(cur)

        return {"entry": entry, "payments": payments}
    finally:
        conn.close()


@router.post("/ar-ap/entries")
def create_entry(body: EntryCreate):
    """Manually create an AR or AP entry."""
    if body.direction not in VALID_DIRECTIONS:
        raise HTTPException(400, f"direction must be one of {sorted(VALID_DIRECTIONS)}")
    if body.amount_total <= 0:
        raise HTTPException(400, "amount_total must be > 0")

    cp_uuid: Optional[str] = None
    if body.counterparty_id:
        cp_uuid = str(_parse_uuid(body.counterparty_id, "counterparty_id"))

    if not cp_uuid and not body.counterparty_name_snapshot:
        raise HTTPException(400, "Provide counterparty_id or counterparty_name_snapshot")

    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO public.ar_ap_entries (
                       direction, branch_code, counterparty_id, counterparty_name_snapshot,
                       doc_no, doc_date, due_date,
                       amount_total, category_code, notes, created_by, status
                   )
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'pending')
                   RETURNING id, direction, doc_date, due_date,
                             amount_total, amount_paid, status""",
                (
                    body.direction, body.branch_code, cp_uuid,
                    body.counterparty_name_snapshot,
                    body.doc_no, body.doc_date, body.due_date,
                    body.amount_total, body.category_code, body.notes,
                    body.created_by,
                ),
            )
            row = cur.fetchone()
            conn.commit()
            cols = [d[0] for d in cur.description]
            return _serialize_row(dict(zip(cols, row)))
    finally:
        conn.close()


@router.patch("/ar-ap/entries/{entry_id}")
def patch_entry(entry_id: str, body: EntryPatch):
    """Edit doc_no, due_date, category_code, notes on an existing entry."""
    eid = _parse_uuid(entry_id, "entry_id")
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    if not updates:
        raise HTTPException(400, "No fields to update")

    set_clauses = ", ".join(f"{k} = %s" for k in updates.keys())
    params = list(updates.values()) + [str(eid)]

    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"UPDATE public.ar_ap_entries SET {set_clauses}, updated_at = now() "
                f"WHERE id = %s AND status != 'cancelled' "
                f"RETURNING id, status, doc_no, due_date, category_code, notes",
                params,
            )
            row = cur.fetchone()
            if not row:
                raise HTTPException(404, "Entry not found or already cancelled")
            conn.commit()
            cols = [d[0] for d in cur.description]
            return _serialize_row(dict(zip(cols, row)))
    finally:
        conn.close()


@router.delete("/ar-ap/entries/{entry_id}")
def cancel_entry(entry_id: str, body: CancelBody = CancelBody()):
    """Cancel an entry (status='cancelled'). Does NOT physically delete (preserves audit trail).
    Existing payments are kept but trigger ignores them (status guard)."""
    eid = _parse_uuid(entry_id, "entry_id")
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE public.ar_ap_entries
                   SET status = 'cancelled',
                       cancelled_at = now(),
                       cancelled_reason = %s,
                       updated_at = now()
                   WHERE id = %s AND status != 'cancelled'
                   RETURNING id, status""",
                (body.reason, str(eid)),
            )
            row = cur.fetchone()
            if not row:
                raise HTTPException(404, "Entry not found or already cancelled")
            conn.commit()
            return {"id": str(eid), "status": "cancelled"}
    finally:
        conn.close()


# ============================================================
# SECTION C — Payments
# ============================================================

@router.post("/ar-ap/payments")
def create_payment(body: PaymentCreate):
    """Record a payment. Trigger auto-updates entry.amount_paid + status."""
    eid = _parse_uuid(body.entry_id, "entry_id")
    if body.amount <= 0:
        raise HTTPException(400, "amount must be > 0")

    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            # Pre-check: entry exists and not cancelled
            cur.execute(
                "SELECT amount_total, amount_paid, status FROM public.ar_ap_entries WHERE id = %s",
                (str(eid),),
            )
            erow = cur.fetchone()
            if not erow:
                raise HTTPException(404, "Entry not found")
            total, paid_before, st = erow
            if st == "cancelled":
                raise HTTPException(409, "Cannot pay a cancelled entry")
            if float(paid_before) + body.amount > float(total) + 0.01:
                raise HTTPException(
                    400,
                    f"Payment would exceed total. "
                    f"Outstanding: {float(total) - float(paid_before):.2f}, "
                    f"requested: {body.amount:.2f}",
                )

            # ── Duplicate guard: same (entry_id, payment_date, amount) within 30s ──
            cur.execute(
                """SELECT id FROM public.ar_ap_payments
                   WHERE entry_id = %s
                     AND payment_date = %s
                     AND amount = %s
                     AND created_at > NOW() - INTERVAL '30 seconds'
                   LIMIT 1""",
                (str(eid), body.payment_date, body.amount),
            )
            if cur.fetchone():
                raise HTTPException(
                    409,
                    "รายการซ้ำกัน — พบการชำระเงินจำนวนเดียวกันในวันเดียวกันเมื่อครู่นี้ "
                    "กรุณารอ 30 วินาทีหากต้องการบันทึกซ้ำ",
                )

            cur.execute(
                """INSERT INTO public.ar_ap_payments
                       (entry_id, payment_date, amount, method, reference_no, notes, created_by)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)
                   RETURNING id, entry_id, payment_date, amount""",
                (str(eid), body.payment_date, body.amount, body.method,
                 body.reference_no, body.notes, body.created_by),
            )
            pay_row = cur.fetchone()
            pay_cols = [d[0] for d in cur.description]

            # Read back the updated entry (trigger already fired)
            cur.execute(
                "SELECT status, amount_total, amount_paid FROM public.ar_ap_entries WHERE id = %s",
                (str(eid),),
            )
            new_st, new_total, new_paid = cur.fetchone()
            conn.commit()

            return {
                "payment": _serialize_row(dict(zip(pay_cols, pay_row))),
                "entry": {
                    "id": str(eid),
                    "status": new_st,
                    "amount_total": float(new_total),
                    "amount_paid": float(new_paid),
                    "amount_outstanding": float(new_total) - float(new_paid),
                },
            }
    finally:
        conn.close()


@router.delete("/ar-ap/payments/{payment_id}")
def delete_payment(payment_id: str):
    """Reverse a payment. Trigger recomputes entry.amount_paid + status."""
    pid = _parse_uuid(payment_id, "payment_id")
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT entry_id FROM public.ar_ap_payments WHERE id = %s",
                (str(pid),),
            )
            row = cur.fetchone()
            if not row:
                raise HTTPException(404, "Payment not found")
            entry_id = row[0]

            cur.execute("DELETE FROM public.ar_ap_payments WHERE id = %s", (str(pid),))
            conn.commit()

            # Trigger already recomputed entry; return new state
            cur.execute(
                "SELECT status, amount_total, amount_paid FROM public.ar_ap_entries WHERE id = %s",
                (str(entry_id),),
            )
            r = cur.fetchone()
            if not r:
                return {"payment_id": str(pid), "deleted": True, "entry": None}
            st, total, paid = r
            return {
                "payment_id": str(pid),
                "deleted": True,
                "entry": {
                    "id": str(entry_id),
                    "status": st,
                    "amount_total": float(total),
                    "amount_paid": float(paid),
                    "amount_outstanding": float(total) - float(paid),
                },
            }
    finally:
        conn.close()


# ============================================================
# SECTION D — Dashboard summary
# ============================================================

@router.get("/ar-ap/summary")
def ar_ap_summary():
    """Pre-aggregated totals for AR/AP dashboard cards.
    Returns: {receivable: {open_count, overdue_count, total_outstanding, total_overdue},
              payable:    {...}}"""
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT direction, open_count, overdue_count, "
                "       total_outstanding, total_overdue "
                "FROM public.v_ar_ap_summary"
            )
            rows = _rows_to_dicts(cur)

        # Always return both directions, even if no rows
        out: dict[str, dict[str, Any]] = {
            "receivable": {"open_count": 0, "overdue_count": 0,
                           "total_outstanding": 0.0, "total_overdue": 0.0},
            "payable":    {"open_count": 0, "overdue_count": 0,
                           "total_outstanding": 0.0, "total_overdue": 0.0},
        }
        for r in rows:
            d = r.get("direction")
            if d in out:
                out[d] = {
                    "open_count":        int(r.get("open_count") or 0),
                    "overdue_count":     int(r.get("overdue_count") or 0),
                    "total_outstanding": float(r.get("total_outstanding") or 0),
                    "total_overdue":     float(r.get("total_overdue") or 0),
                }
        return out
    finally:
        conn.close()


# ============================================================
# SECTION E — Phase 14: AP Due Date Reminder (LINE)
# ============================================================

@router.post("/ap/due-reminder")
def ap_due_reminder():
    """
    Phase 14 — ส่ง LINE แจ้งเตือน AP ที่ครบกำหนดภายใน 3 วัน
    เรียกจาก Coolify cron: 0 9 * * * (09:00 Bangkok)
    หรือเรียก manual: POST /ap/due-reminder
    """
    from line_bot_routes import _push_text  # noqa: PLC0415

    today = date.today()
    deadline = today + timedelta(days=3)

    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    e.id,
                    COALESCE(c.name, e.counterparty_name_snapshot, 'ไม่ระบุ') AS vendor_name,
                    e.doc_no,
                    e.due_date,
                    e.amount_total,
                    e.amount_paid,
                    (e.amount_total - e.amount_paid) AS remaining
                FROM public.ar_ap_entries e
                LEFT JOIN public.counterparties c ON c.id = e.counterparty_id
                WHERE e.direction = 'payable'
                  AND e.status IN ('pending', 'partial')
                  AND e.due_date BETWEEN %s AND %s
                ORDER BY e.due_date ASC
                """,
                (today.isoformat(), deadline.isoformat()),
            )
            rows = _rows_to_dicts(cur)
    finally:
        conn.close()

    if not rows:
        return {"sent": False, "message": "ไม่มี AP ครบกำหนดใน 3 วันข้างหน้า", "count": 0}

    # ── Build LINE message ──
    lines = ["⚠️ แจ้งเตือน AP ครบกำหนด (3 วัน)\n"]
    for r in rows:
        due_str = r["due_date"]
        days_left = (date.fromisoformat(due_str) - today).days
        if days_left == 0:
            day_label = "🔴 วันนี้!"
        elif days_left == 1:
            day_label = "🟠 พรุ่งนี้"
        else:
            day_label = f"🟡 อีก {days_left} วัน"

        vendor = r["vendor_name"]
        remaining = float(r["remaining"])
        doc = f" ({r['doc_no']})" if r.get("doc_no") else ""
        lines.append(
            f"{day_label} — {vendor}{doc}\n"
            f"ครบกำหนด: {due_str}\n"
            f"ค้างจ่าย: ฿{remaining:,.2f}\n"
        )

    lines.append(f"รวม {len(rows)} รายการ — กรุณาชำระตามกำหนด")
    message = "\n".join(lines)

    _push_text(message)
    return {"sent": True, "count": len(rows), "entries": rows}


# ============================================================
# Health check
# ============================================================

@router.get("/ar-ap/health")
def ar_ap_health():
    """Quick sanity: DB reachable + counterparties seeded + views queryable."""
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM public.counterparties WHERE is_active = true")
            cp_count = cur.fetchone()[0]
            cur.execute("SELECT count(*) FROM public.ar_ap_entries WHERE status != 'cancelled'")
            entries_count = cur.fetchone()[0]
            cur.execute("SELECT count(*) FROM public.v_ar_ap_summary")
            summary_count = cur.fetchone()[0]
        return {
            "db": "ok",
            "counterparties_active": cp_count,
            "entries_open": entries_count,
            "summary_directions": summary_count,
        }
    finally:
        conn.close()
