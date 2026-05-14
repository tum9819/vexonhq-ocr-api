"""
VEXONHQ Phase 3 F3 — Quick Entry Routes
========================================
Backend for manual_entries (cash transactions without receipts).
Companion to 08_phase3_f3_manual_entries.sql.

Endpoints (5):
    GET    /quick-entries/list        — paginated list with date/direction/q filters
    GET    /quick-entries/chips       — top labels from v_quick_chips
    GET    /quick-entries/summary     — today + this-month totals
    POST   /quick-entries             — create a manual entry
    DELETE /quick-entries/{id}        — hard delete (small one-off records, hard delete OK)
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta
from typing import Any, Optional
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

try:
    from main import get_db_conn  # type: ignore
except ImportError:
    import os
    import psycopg2
    def get_db_conn():
        return psycopg2.connect(os.environ["DATABASE_URL"])


logger = logging.getLogger("phase3_quick_entry_routes")
router = APIRouter(tags=["phase3-quick-entry"])

DEFAULT_BRANCH = "thawi_watthana"

VALID_DIRECTIONS = {"income", "expense", "transfer"}
VALID_PAYMENT_METHODS = {"cash", "transfer", "credit_card", "qr", "promptpay", "cheque", "other"}


# ============================================================
# Helpers
# ============================================================

def _serialize_row(row: dict) -> dict:
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
    if cur.description is None:
        return []
    cols = [d[0] for d in cur.description]
    return [_serialize_row(dict(zip(cols, r))) for r in cur.fetchall()]


def _parse_uuid(value: Any, field_name: str = "id") -> UUID:
    try:
        return UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(400, f"Invalid UUID for {field_name}: {value!r}")


def parse_quick_text(text: str) -> dict:
    """
    Parse 'label amount' freeform string.
    Examples:
      "กาแฟ 50"            → {label: "กาแฟ", amount: 50.0}
      "ค่าน้ำ 480.50"       → {label: "ค่าน้ำ", amount: 480.50}
      "ตลาดสด เนื้อหมู 350" → {label: "ตลาดสด เนื้อหมู", amount: 350}
    Returns None if no number found.
    """
    if not text or not text.strip():
        return {"label": None, "amount": None}
    # Find LAST numeric token (in case label contains digits)
    m = list(re.finditer(r"(\d+(?:\.\d+)?)", text))
    if not m:
        return {"label": text.strip(), "amount": None}
    last = m[-1]
    amt = float(last.group(1))
    label = (text[:last.start()] + text[last.end():]).strip()
    if not label:
        label = "—"
    return {"label": label, "amount": amt}


# ============================================================
# Pydantic models
# ============================================================

class QuickEntryCreate(BaseModel):
    direction: str
    amount: float = Field(gt=0)
    label: str
    entry_date: Optional[date] = None
    description: Optional[str] = None
    category_code: Optional[str] = None
    payment_method: str = "cash"
    reference_no: Optional[str] = None
    branch_code: str = DEFAULT_BRANCH
    created_by: Optional[str] = None


class QuickParseRequest(BaseModel):
    text: str


# ============================================================
# Endpoints
# ============================================================

@router.get("/quick-entries/list")
def list_entries(
    direction: Optional[str] = Query(None),
    date_from: Optional[date] = Query(None),
    date_to: Optional[date] = Query(None),
    q: Optional[str] = Query(None, description="search label or description"),
    category_code: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    """Paginated list of manual entries with filters."""
    if direction and direction not in VALID_DIRECTIONS:
        raise HTTPException(400, f"direction must be one of {sorted(VALID_DIRECTIONS)}")

    where: list[str] = []
    params: list[Any] = []
    if direction:
        where.append("direction = %s"); params.append(direction)
    if date_from:
        where.append("entry_date >= %s"); params.append(date_from)
    if date_to:
        where.append("entry_date <= %s"); params.append(date_to)
    if category_code:
        where.append("category_code = %s"); params.append(category_code)
    if q:
        where.append("(label ILIKE %s OR description ILIKE %s)")
        params.extend([f"%{q}%", f"%{q}%"])

    sql_where = (" WHERE " + " AND ".join(where)) if where else ""

    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT id, branch_code, entry_date, direction, amount, label, "
                f"       description, category_code, payment_method, reference_no, "
                f"       created_at "
                f"FROM public.manual_entries{sql_where} "
                f"ORDER BY entry_date DESC, created_at DESC "
                f"LIMIT %s OFFSET %s",
                params + [limit, offset],
            )
            rows = _rows_to_dicts(cur)

            cur.execute(
                f"SELECT count(*) FROM public.manual_entries{sql_where}",
                params,
            )
            total = cur.fetchone()[0]
        return {"rows": rows, "total": total, "limit": limit, "offset": offset}
    finally:
        conn.close()


@router.get("/quick-entries/chips")
def list_chips(
    direction: Optional[str] = Query(None),
    limit: int = Query(20, ge=1, le=50),
):
    """Top frequent labels — used to render chip palette in UI.
    Auto-derived from v_quick_chips view (last 6 months usage)."""
    if direction and direction not in VALID_DIRECTIONS:
        raise HTTPException(400, f"direction must be one of {sorted(VALID_DIRECTIONS)}")

    where = " WHERE direction = %s" if direction else ""
    params: list[Any] = []
    if direction:
        params.append(direction)

    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT label, direction, use_count, suggested_amount, "
                f"       suggested_category_code, last_used_date, total_amount "
                f"FROM public.v_quick_chips{where} "
                f"LIMIT %s",
                params + [limit],
            )
            rows = _rows_to_dicts(cur)
        return {"rows": rows}
    finally:
        conn.close()


@router.get("/quick-entries/summary")
def quick_summary():
    """Totals for dashboard cards.
    Returns: today, week, month totals by direction."""
    today = date.today()
    week_start = today - timedelta(days=today.weekday())
    month_start = today.replace(day=1)

    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            def fetch_for(period_start: date):
                cur.execute(
                    "SELECT direction, count(*)::int AS count, sum(amount)::numeric AS total "
                    "FROM public.manual_entries "
                    "WHERE entry_date >= %s AND entry_date <= %s "
                    "GROUP BY direction",
                    (period_start, today),
                )
                out = {
                    "income":   {"count": 0, "total": 0.0},
                    "expense":  {"count": 0, "total": 0.0},
                    "transfer": {"count": 0, "total": 0.0},
                }
                for direction, count, total in cur.fetchall():
                    if direction in out:
                        out[direction] = {"count": int(count), "total": float(total or 0)}
                return out

            today_data = fetch_for(today)
            week_data = fetch_for(week_start)
            month_data = fetch_for(month_start)

        return {
            "today":  today_data,
            "week":   week_data,
            "month":  month_data,
            "today_date": today.isoformat(),
        }
    finally:
        conn.close()


@router.post("/quick-entries")
def create_entry(body: QuickEntryCreate):
    """Create a manual entry."""
    if body.direction not in VALID_DIRECTIONS:
        raise HTTPException(400, f"direction must be one of {sorted(VALID_DIRECTIONS)}")
    if body.amount <= 0:
        raise HTTPException(400, "amount must be > 0")
    if not body.label or not body.label.strip():
        raise HTTPException(400, "label is required")
    if body.payment_method and body.payment_method not in VALID_PAYMENT_METHODS:
        # allow custom payment methods but warn
        logger.info("Unknown payment_method: %s", body.payment_method)

    entry_date = body.entry_date or date.today()
    label = body.label.strip()[:120]   # bounded length

    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO public.manual_entries
                       (branch_code, entry_date, direction, amount, label,
                        description, category_code, payment_method, reference_no, created_by)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                   RETURNING id, branch_code, entry_date, direction, amount, label,
                             description, category_code, payment_method, reference_no, created_at""",
                (body.branch_code, entry_date, body.direction, body.amount, label,
                 body.description, body.category_code, body.payment_method or "cash",
                 body.reference_no, body.created_by),
            )
            row = cur.fetchone()
            cols = [d[0] for d in cur.description]
            conn.commit()
            return _serialize_row(dict(zip(cols, row)))
    finally:
        conn.close()


@router.delete("/quick-entries/{entry_id}")
def delete_entry(entry_id: str):
    """Hard delete a manual entry (no audit trail — these are tiny cash records)."""
    eid = _parse_uuid(entry_id, "entry_id")
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM public.manual_entries WHERE id = %s RETURNING id",
                (str(eid),),
            )
            row = cur.fetchone()
            if not row:
                raise HTTPException(404, "Entry not found")
            conn.commit()
            return {"id": str(eid), "deleted": True}
    finally:
        conn.close()


@router.post("/quick-entries/parse")
def parse_text(body: QuickParseRequest):
    """Parse freeform 'label amount' string. Pure regex — no DB."""
    return parse_quick_text(body.text)


@router.get("/quick-entries/health")
def quick_entry_health():
    """Smoke: DB reachable + table exists + view queryable."""
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM public.manual_entries")
            entries_count = cur.fetchone()[0]
            cur.execute("SELECT count(*) FROM public.v_quick_chips")
            chips_count = cur.fetchone()[0]
        return {
            "db": "ok",
            "manual_entries_count": int(entries_count),
            "active_chips": int(chips_count),
        }
    finally:
        conn.close()
