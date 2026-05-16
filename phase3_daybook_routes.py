"""
VEXONHQ Phase 3 F2 — Daybook Routes
====================================
Unified chronological feed of POS sales + vendor_bills + manual_entries + AR/AP payments.
Companion to 09_phase3_f2_daybook_view.sql.

Endpoints (3):
    GET  /daybook/list      — paginated entries with filters (date/source/direction/q)
    GET  /daybook/summary   — totals per direction + source for a date range
    GET  /daybook/health    — smoke test (DB + view)
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Any, Optional
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query

try:
    from main import get_db_conn  # type: ignore
except ImportError:
    import os
    import psycopg2
    def get_db_conn():
        return psycopg2.connect(os.environ["DATABASE_URL"])


logger = logging.getLogger("phase3_daybook_routes")
router = APIRouter(tags=["phase3-daybook"])

# All source values v_daybook can return (updated for migration-16 schema)
VALID_SOURCES = {
    "pos_sale", "vendor_bill", "manual", "ar_payment", "ap_payment",
    "rider_income_grab", "rider_income_lineman", "pos_cashflow",
    # bank_statement_entries.source_type values
    "salary", "transfer", "withdrawal", "deposit",
}
VALID_DIRECTIONS = {"income", "expense"}


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


def _default_range() -> tuple[date, date]:
    """Default: this month from day 1 to today."""
    today = date.today()
    return today.replace(day=1), today


# ============================================================
# Endpoints
# ============================================================

@router.get("/daybook/list")
def list_daybook(
    date_from: Optional[date] = Query(None),
    date_to: Optional[date] = Query(None),
    source: Optional[str] = Query(None, description="comma-separated: pos_sale,vendor_bill,manual,ar_payment,ap_payment"),
    direction: Optional[str] = Query(None, description="income | expense"),
    q: Optional[str] = Query(None, description="search label/counterparty/doc_no/notes"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    """Paginated daybook entries, newest first."""
    df, dt = (date_from, date_to)
    if not df and not dt:
        df, dt = _default_range()

    where: list[str] = []
    params: list[Any] = []
    if df:
        where.append("entry_date >= %s"); params.append(df)
    if dt:
        where.append("entry_date <= %s"); params.append(dt)
    if direction:
        if direction not in VALID_DIRECTIONS:
            raise HTTPException(400, f"direction must be one of {sorted(VALID_DIRECTIONS)}")
        where.append("direction = %s"); params.append(direction)
    if source:
        # Comma-separated allowed
        sources = [s.strip() for s in source.split(",") if s.strip()]
        bad = [s for s in sources if s not in VALID_SOURCES]
        if bad:
            raise HTTPException(400, f"Unknown source(s): {bad}. Valid: {sorted(VALID_SOURCES)}")
        where.append("source = ANY(%s)"); params.append(sources)
    if q:
        # v_daybook migration-16: only label, counterparty, category_code, ref_id available
        where.append("(label ILIKE %s OR counterparty ILIKE %s OR category_code ILIKE %s OR ref_id::text ILIKE %s)")
        params.extend([f"%{q}%", f"%{q}%", f"%{q}%", f"%{q}%"])

    sql_where = (" WHERE " + " AND ".join(where)) if where else ""

    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            # NOTE: v_daybook was rewritten in migration-16 — new column list:
            # entry_date, direction, amount, source, category_code, label,
            # counterparty, branch_code, ref_id  (no doc_no/payment_method/notes/created_at)
            cur.execute(
                f"SELECT source, entry_date, direction, amount, label, counterparty, "
                f"       branch_code, ref_id, category_code "
                f"FROM public.v_daybook{sql_where} "
                f"ORDER BY entry_date DESC, ref_id DESC "
                f"LIMIT %s OFFSET %s",
                params + [limit, offset],
            )
            rows = _rows_to_dicts(cur)

            cur.execute(
                f"SELECT count(*) FROM public.v_daybook{sql_where}",
                params,
            )
            total = cur.fetchone()[0]
        return {
            "rows": rows,
            "total": total,
            "limit": limit,
            "offset": offset,
            "date_from": df.isoformat() if df else None,
            "date_to": dt.isoformat() if dt else None,
        }
    finally:
        conn.close()


@router.get("/daybook/summary")
def daybook_summary(
    date_from: Optional[date] = Query(None),
    date_to: Optional[date] = Query(None),
    source: Optional[str] = Query(None, description="comma-separated sources to include"),
):
    """Totals per direction and per source for the date range.
    If `source` is passed, totals reflect only those sources.
    Default range: this month."""
    df, dt = (date_from, date_to)
    if not df and not dt:
        df, dt = _default_range()

    # Parse source filter
    sources_filter: Optional[list[str]] = None
    if source:
        sources_filter = [s.strip() for s in source.split(",") if s.strip()]
        bad = [s for s in sources_filter if s not in VALID_SOURCES]
        if bad:
            raise HTTPException(400, f"Unknown source(s): {bad}. Valid: {sorted(VALID_SOURCES)}")

    base_where = "entry_date >= %s AND entry_date <= %s"
    base_params: list[Any] = [df, dt]
    if sources_filter:
        base_where += " AND source = ANY(%s)"
        base_params.append(sources_filter)

    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            # Direction totals
            cur.execute(
                f"""SELECT direction,
                           count(*)::int              AS count,
                           sum(amount)::numeric(14,2) AS total
                    FROM public.v_daybook
                    WHERE {base_where}
                    GROUP BY direction""",
                base_params,
            )
            direction_rows = cur.fetchall()
            by_direction = {
                "income":  {"count": 0, "total": 0.0},
                "expense": {"count": 0, "total": 0.0},
            }
            for d, count, total in direction_rows:
                by_direction[d] = {"count": int(count), "total": float(total or 0)}

            net = by_direction["income"]["total"] - by_direction["expense"]["total"]

            # Source breakdown — always show ALL sources here so users see
            # which sources are hidden by filter (helpful UX context)
            cur.execute(
                """SELECT source,
                          direction,
                          count(*)::int                    AS count,
                          sum(amount)::numeric(14,2)       AS total
                   FROM public.v_daybook
                   WHERE entry_date >= %s AND entry_date <= %s
                   GROUP BY source, direction
                   ORDER BY source""",
                (df, dt),
            )
            by_source: dict[str, dict[str, Any]] = {}
            for s, d, count, total in cur.fetchall():
                if s not in by_source:
                    by_source[s] = {"income": {"count": 0, "total": 0.0},
                                    "expense": {"count": 0, "total": 0.0}}
                by_source[s][d] = {"count": int(count), "total": float(total or 0)}

        return {
            "date_from": df.isoformat(),
            "date_to":   dt.isoformat(),
            "by_direction": by_direction,
            "net":          float(net),
            "by_source":    by_source,
            "applied_sources": sources_filter,
        }
    finally:
        conn.close()


@router.get("/daybook/health")
def daybook_health():
    """Smoke: DB reachable + view queryable + breakdown counts."""
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM public.v_daybook")
            total = cur.fetchone()[0]
            cur.execute(
                """SELECT source, count(*)::int
                   FROM public.v_daybook
                   GROUP BY source ORDER BY source"""
            )
            sources = {row[0]: int(row[1]) for row in cur.fetchall()}
        return {
            "db": "ok",
            "total_entries": int(total),
            "by_source": sources,
        }
    finally:
        conn.close()
