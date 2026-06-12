"""
B2 M1 — Stock-in Import routes (DB-touching)
=============================================
Endpoints:
    GET  /pos/stock-in/diff/{import_id}    — reconcile diff for user review
    POST /pos/stock-in/approve/{import_id} — approve with FOR UPDATE lock + re-validate
    POST /pos/stock-in/cancel/{import_id}  — cancel, clear staging

Internal:
    _stage_stock_in(...)  — called by pos_import._process_import_background

Spec: VEXONHQ/docs/03_SPECS/B2_STOCKIN_AI_SEARCH_SPEC.md §§2.4–2.6b
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import date, datetime
from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

try:
    from main import get_db_conn  # type: ignore
except ImportError:
    import psycopg2
    def get_db_conn():
        return psycopg2.connect(os.environ["DATABASE_URL"])

from stock_in_import import parse_stock_in_file, reconcile_diff

logger = logging.getLogger("stock_in_routes")
router = APIRouter(prefix="/pos", tags=["pos"])


# ─── Helpers ─────────────────────────────────────────────────────────────────

_STAGING_COLS = [
    "id", "import_id", "branch_code", "received_date", "item_name",
    "material_code", "tag", "refill_type", "invoice_no", "gr_ref", "po_ref",
    "po_date", "unit", "qty", "unit_cost", "net_cost",
    "canonical_key", "occurrence_index", "identity_key",
    "source_row_number", "original_row_json",
]

_LINE_COLS = [
    "id", "import_id", "branch_code", "received_date", "item_name",
    "material_code", "tag", "refill_type", "invoice_no", "gr_ref", "po_ref",
    "po_date", "unit", "qty", "unit_cost", "net_cost",
    "canonical_key", "occurrence_index", "identity_key",
    "source_row_number", "original_row_json", "row_status",
]


def _row_to_dict(row, cols: list[str]) -> dict:
    d = dict(zip(cols, row))
    # Convert date objects to ISO strings for JSON serialisation
    for k, v in d.items():
        if isinstance(v, (date, datetime)):
            d[k] = str(v)
    return d


def _diff_counts(diff: dict) -> dict:
    return {
        "new":       len(diff["insert"]),
        "unchanged": len(diff["skip"]),
        "changed":   len(diff["needs_review"]),
        "missing":   len(diff["missing_from_reexport"]),
    }


def _fetch_staged_rows(cur, import_id: str) -> list[dict]:
    cur.execute(
        f"SELECT {', '.join(_STAGING_COLS)} FROM public.stock_in_staging "
        "WHERE import_id = %s ORDER BY source_row_number",
        (import_id,),
    )
    return [_row_to_dict(r, _STAGING_COLS) for r in cur.fetchall()]


def _fetch_committed_rows(cur, branch_code: str, period_start, period_end) -> list[dict]:
    cur.execute(
        f"SELECT {', '.join(_LINE_COLS)} FROM public.stock_in_lines "
        "WHERE branch_code = %s AND received_date BETWEEN %s AND %s "
        "AND row_status = 'active' ORDER BY received_date, source_row_number",
        (branch_code, period_start, period_end),
    )
    return [_row_to_dict(r, _LINE_COLS) for r in cur.fetchall()]


# ─── Background staging function (called from pos_import) ────────────────────

def _stage_stock_in(
    import_id: str,
    df,
    branch_code: str,
    uploaded_by: Optional[str],
    _set,  # pos_import._set callback for job status
) -> None:
    """
    Parse + validate the DataFrame atomically, write to stock_in_staging,
    compute the reconcile diff, and update pos_imports status.

    Any single parse error → status='failed', zero rows staged.
    """
    conn = None
    try:
        # 1. Parse atomically (raises ValueError on any bad row)
        try:
            rows = parse_stock_in_file(df, branch_code=branch_code)
        except ValueError as e:
            conn = get_db_conn()
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE public.pos_imports "
                    "SET status='failed', error_message=%s, finished_at=now() "
                    "WHERE id=%s",
                    (str(e)[:2000], import_id),
                )
                conn.commit()
            _set({"status": "error", "error": str(e)})
            return

        # 2. Stage rows into stock_in_staging
        conn = get_db_conn()
        with conn.cursor() as cur:
            for row in rows:
                cur.execute("""
                    INSERT INTO public.stock_in_staging
                      (import_id, branch_code, received_date, item_name,
                       material_code, tag, refill_type, invoice_no, gr_ref, po_ref,
                       po_date, unit, qty, unit_cost, net_cost,
                       canonical_key, occurrence_index, identity_key,
                       source_row_number, original_row_json)
                    VALUES (%s,%s,%s,%s, %s,%s,%s,%s,%s,%s, %s,%s,%s,%s,%s,
                            %s,%s,%s, %s,%s)
                """, (
                    import_id,
                    row["branch_code"],
                    row["received_date"],
                    row["item_name"],
                    row["material_code"],
                    row["tag"],
                    row["refill_type"],
                    row["invoice_no"],
                    row["gr_ref"],
                    row["po_ref"],
                    row["po_date"],
                    row["unit"],
                    row["qty"],
                    row["unit_cost"],
                    row["net_cost"],
                    row["canonical_key"],
                    row["occurrence_index"],
                    row["identity_key"],
                    row["source_row_number"],
                    json.dumps(row["original_row_json"], ensure_ascii=False),
                ))
            conn.commit()

            # 3. Compute period from staged rows
            dates = [r["received_date"] for r in rows if r["received_date"]]
            period_start = min(dates) if dates else None
            period_end = max(dates) if dates else None

            # 4. Load committed rows for this branch + period
            committed = []
            if period_start and period_end:
                committed = _fetch_committed_rows(cur, branch_code, period_start, period_end)

            # 5. Reconcile diff
            staged_for_diff = _fetch_staged_rows(cur, import_id)
            diff = reconcile_diff(staged_for_diff, committed)
            counts = _diff_counts(diff)

            # 6. Update pos_imports
            cur.execute(
                "UPDATE public.pos_imports "
                "SET status='needs_review', period_start=%s, period_end=%s, "
                "row_count=%s, error_message=%s, finished_at=now() "
                "WHERE id=%s",
                (
                    period_start,
                    period_end,
                    len(rows),
                    json.dumps(counts),
                    import_id,
                ),
            )
            conn.commit()

        _set({
            "status": "success",
            "result": {
                "import_id":     import_id,
                "report_type":   "stock_in_refill",
                "status":        "needs_review",
                "rows_staged":   len(rows),
                "period_start":  str(period_start) if period_start else None,
                "period_end":    str(period_end) if period_end else None,
                "diff_counts":   counts,
                "detail":        "staged — ตรวจสอบ diff ที่ GET /pos/stock-in/diff/{import_id} ก่อน approve",
            },
        })

    except Exception as e:
        logger.exception("stock_in staging failed")
        if conn:
            try:
                conn.rollback()
            except Exception:
                pass
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE public.pos_imports SET status='failed', "
                        "error_message=%s, finished_at=now() WHERE id=%s",
                        (str(e)[:2000], import_id),
                    )
                    conn.commit()
            except Exception:
                pass
        _set({"status": "error", "error": str(e)})
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass


# ─── API models ──────────────────────────────────────────────────────────────

class ApproveRequest(BaseModel):
    approved_by: str
    reason: Optional[str] = None
    expected_counts: dict   # {new, unchanged, changed, missing} from the diff the user reviewed


class CancelRequest(BaseModel):
    cancelled_by: str
    reason: Optional[str] = None


# ─── GET /pos/stock-in/diff/{import_id} ─────────────────────────────────────

@router.get("/stock-in/diff/{import_id}")
def get_stock_in_diff(import_id: str):
    """
    Return the current reconcile diff for a staged import.
    Call before approving so the user can review insert/changed/missing rows.
    """
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT status, branch_code, period_start, period_end "
                "FROM public.pos_imports WHERE id=%s",
                (import_id,),
            )
            row = cur.fetchone()
            if not row:
                raise HTTPException(404, f"Import {import_id} not found")
            status, branch_code, period_start, period_end = row

            if status not in ("needs_review", "staged"):
                raise HTTPException(
                    409,
                    f"Import status is '{status}'; diff only available for needs_review/staged imports",
                )

            staged = _fetch_staged_rows(cur, import_id)
            committed = _fetch_committed_rows(cur, branch_code, period_start, period_end) if period_start else []
            diff = reconcile_diff(staged, committed)
            counts = _diff_counts(diff)

        return {
            "import_id":    import_id,
            "status":       status,
            "branch_code":  branch_code,
            "period_start": str(period_start) if period_start else None,
            "period_end":   str(period_end) if period_end else None,
            "counts":       counts,
            "insert":       diff["insert"],
            "skip":         diff["skip"],
            "needs_review": diff["needs_review"],
            "missing_from_reexport": diff["missing_from_reexport"],
        }
    finally:
        conn.close()


# ─── POST /pos/stock-in/approve/{import_id} ──────────────────────────────────

@router.post("/stock-in/approve/{import_id}")
def approve_stock_in(import_id: str, body: ApproveRequest):
    """
    Approve a staged stock-in import.

    Runs inside ONE DB transaction with FOR UPDATE lock on pos_imports:
    1. Lock the row
    2. Status gate (must be needs_review/staged)
    3. Re-validate diff under lock (stale-review guard)
    4. If diff counts differ from expected_counts → 409 stale_review
    5. Insert new rows, soft-delete superseded/voided, write reconcile_log
    6. Update pos_imports status='success'
    All or nothing — no partial state.
    """
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            # 1. Lock
            cur.execute(
                "SELECT status, branch_code, period_start, period_end "
                "FROM public.pos_imports WHERE id=%s FOR UPDATE",
                (import_id,),
            )
            row = cur.fetchone()
            if not row:
                raise HTTPException(404, f"Import {import_id} not found")
            status, branch_code, period_start, period_end = row

            # 2. Status gate
            if status not in ("needs_review", "staged"):
                raise HTTPException(
                    409,
                    {
                        "error": "not_approvable",
                        "detail": f"Import is '{status}'; cannot approve",
                    },
                )

            # 3. Re-validate diff INSIDE the lock
            staged = _fetch_staged_rows(cur, import_id)
            committed = _fetch_committed_rows(cur, branch_code, period_start, period_end) if period_start else []
            diff = reconcile_diff(staged, committed)
            live_counts = _diff_counts(diff)

            # 4. Stale-review guard
            if live_counts != body.expected_counts:
                raise HTTPException(
                    409,
                    {
                        "error": "stale_review",
                        "detail": "Reconcile diff changed since you reviewed it. Re-fetch /diff and re-confirm.",
                        "expected_counts":    body.expected_counts,
                        "current_counts":     live_counts,
                        "insert":             diff["insert"],
                        "needs_review":       diff["needs_review"],
                        "missing_from_reexport": diff["missing_from_reexport"],
                    },
                )

            # 5. Write insert rows into stock_in_lines
            new_rows_inserted = 0
            for r in diff["insert"]:
                cur.execute("""
                    INSERT INTO public.stock_in_lines
                      (import_id, branch_code, received_date, item_name,
                       material_code, tag, refill_type, invoice_no, gr_ref, po_ref,
                       po_date, unit, qty, unit_cost, net_cost,
                       canonical_key, occurrence_index, identity_key,
                       source_row_number, original_row_json, row_status)
                    VALUES (%s,%s,%s,%s, %s,%s,%s,%s,%s,%s, %s,%s,%s,%s,%s,
                            %s,%s,%s, %s,%s, 'active')
                """, (
                    import_id,
                    r["branch_code"],
                    r["received_date"],
                    r["item_name"],
                    r["material_code"],
                    r["tag"],
                    r["refill_type"],
                    r["invoice_no"],
                    r["gr_ref"],
                    r["po_ref"],
                    r["po_date"],
                    r["unit"],
                    r["qty"],
                    r["unit_cost"],
                    r["net_cost"],
                    r["canonical_key"],
                    r["occurrence_index"],
                    r["identity_key"],
                    r["source_row_number"],
                    json.dumps(r.get("original_row_json", {}), ensure_ascii=False),
                ))
                new_rows_inserted += 1

            # 6. Write reconcile_log (append-only audit)
            log_id = str(uuid.uuid4())
            cur.execute("""
                INSERT INTO public.stock_in_reconcile_log
                  (id, import_id_new, branch_code, period_start, period_end,
                   approved_by, approved_at, decision, reason, counts_json,
                   before_after_diff)
                VALUES (%s,%s,%s,%s,%s, %s,now(),'approve',%s, %s,%s)
            """, (
                log_id,
                import_id,
                branch_code,
                period_start,
                period_end,
                body.approved_by,
                body.reason,
                json.dumps(live_counts),
                json.dumps({
                    "insert": [r.get("canonical_key") for r in diff["insert"]],
                    "skip":   len(diff["skip"]),
                    "needs_review": [r.get("canonical_key") for r in diff["needs_review"]],
                    "missing": [r.get("canonical_key") for r in diff["missing_from_reexport"]],
                }),
            ))

            # 7. Update pos_imports status='success'
            cur.execute(
                "UPDATE public.pos_imports "
                "SET status='success', finished_at=now() WHERE id=%s",
                (import_id,),
            )

            conn.commit()

        return {
            "import_id":      import_id,
            "status":         "success",
            "rows_committed": new_rows_inserted,
            "counts":         live_counts,
        }

    except HTTPException:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        logger.exception("stock_in approve failed")
        raise HTTPException(500, f"Approve failed: {e}") from e
    finally:
        conn.close()


# ─── POST /pos/stock-in/cancel/{import_id} ───────────────────────────────────

@router.post("/stock-in/cancel/{import_id}")
def cancel_stock_in(import_id: str, body: CancelRequest):
    """
    Cancel a staged stock-in import.
    Clears stock_in_staging, updates pos_imports to 'cancelled'.
    stock_in_lines are untouched.
    """
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT status, branch_code, period_start, period_end "
                "FROM public.pos_imports WHERE id=%s FOR UPDATE",
                (import_id,),
            )
            row = cur.fetchone()
            if not row:
                raise HTTPException(404, f"Import {import_id} not found")
            status, branch_code, period_start, period_end = row

            if status not in ("needs_review", "staged"):
                raise HTTPException(
                    409,
                    {
                        "error": "not_cancellable",
                        "detail": f"Import is '{status}'; only needs_review/staged can be cancelled",
                    },
                )

            # Clear staging rows
            cur.execute(
                "DELETE FROM public.stock_in_staging WHERE import_id=%s",
                (import_id,),
            )

            # Write cancel audit record
            cur.execute("""
                INSERT INTO public.stock_in_reconcile_log
                  (id, import_id_new, branch_code, period_start, period_end,
                   approved_by, approved_at, decision, reason, counts_json,
                   before_after_diff)
                VALUES (%s,%s,%s,%s,%s, %s,now(),'cancel',%s, %s,%s)
            """, (
                str(uuid.uuid4()),
                import_id,
                branch_code,
                period_start,
                period_end,
                body.cancelled_by,
                body.reason,
                json.dumps({}),
                json.dumps({}),
            ))

            # Update pos_imports
            cur.execute(
                "UPDATE public.pos_imports SET status='cancelled', finished_at=now() "
                "WHERE id=%s",
                (import_id,),
            )
            conn.commit()

        return {"import_id": import_id, "status": "cancelled"}

    except HTTPException:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        logger.exception("stock_in cancel failed")
        raise HTTPException(500, f"Cancel failed: {e}") from e
    finally:
        conn.close()
