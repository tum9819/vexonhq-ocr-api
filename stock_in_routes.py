"""
B2 M1 — Stock-in Import routes (DB-touching)
=============================================
Endpoints:
    GET  /pos/stock-in/diff/{import_id}     — reconcile diff for user review
    POST /pos/stock-in/approve/{import_id}  — atomic approve (FOR UPDATE lock)
    POST /pos/stock-in/cancel/{import_id}   — cancel + clear staging
    POST /pos/stock-in/recover/{import_id}  — recover a stuck 'parsing' import

Internal:
    _stage_stock_in(...)  — called by pos_import._process_import_background

All financial-mutation endpoints are admin-only (JWT _role==admin).
approved_by / cancelled_by come exclusively from the JWT token, never the body.

Spec: VEXONHQ/docs/03_SPECS/B2_STOCKIN_AI_SEARCH_SPEC.md §§2.4–2.6b
Antigravity REVISE: items 1–6, 9
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import date, datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, field_validator, Field

def get_db_conn():
    import main
    return main.get_db_conn()

try:
    from auth_routes import _require_admin_role  # type: ignore[import]
except ImportError:
    def _require_admin_role(request: Request) -> dict:  # type: ignore[misc]
        raise HTTPException(500, "auth not available")

from stock_in_import import parse_stock_in_file, reconcile_diff, normalize_branch_code

logger = logging.getLogger("stock_in_routes")
router = APIRouter(prefix="/pos", tags=["pos"])


# ─── Admin gate helpers ───────────────────────────────────────────────────────

def _admin_identity(payload: dict) -> str:
    """Extract caller's string identity from the admin JWT payload."""
    return str(payload.get("sub", "") or "")


def _validate_uuid(import_id: str) -> None:
    """Raise 400 for non-UUID import_id (consistent with main._validate_uuid_param)."""
    try:
        uuid.UUID(str(import_id))
    except (ValueError, AttributeError):
        raise HTTPException(400, f"invalid import_id (expected UUID): {import_id!r}")


# ─── Column lists ─────────────────────────────────────────────────────────────

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


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _row_to_dict(row, cols: list[str]) -> dict:
    d = dict(zip(cols, row))
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
    branch_code = normalize_branch_code(branch_code)
    cur.execute(
        f"SELECT {', '.join(_LINE_COLS)} FROM public.stock_in_lines "
        "WHERE branch_code = %s AND received_date BETWEEN %s AND %s "
        "AND row_status = 'active' ORDER BY received_date, source_row_number",
        (branch_code, period_start, period_end),
    )
    return [_row_to_dict(r, _LINE_COLS) for r in cur.fetchall()]


def _insert_stock_in_line(cur, import_id: str, r: dict, row_status: str = "active") -> str:
    """INSERT one row into stock_in_lines; return the new row's UUID string."""
    new_id = str(uuid.uuid4())
    cur.execute("""
        INSERT INTO public.stock_in_lines
          (id, import_id, branch_code, received_date, item_name,
           material_code, tag, refill_type, invoice_no, gr_ref, po_ref,
           po_date, unit, qty, unit_cost, net_cost,
           canonical_key, occurrence_index, identity_key,
           source_row_number, original_row_json, row_status)
        VALUES (%s,%s,%s,%s,%s, %s,%s,%s,%s,%s,%s, %s,%s,%s,%s,%s,
                %s,%s,%s, %s,%s, %s)
    """, (
        new_id,
        import_id,
        r["branch_code"],
        r["received_date"],
        r["item_name"],
        r.get("material_code"),
        r.get("tag"),
        r.get("refill_type"),
        r.get("invoice_no", ""),
        r.get("gr_ref", ""),
        r.get("po_ref", ""),
        r.get("po_date"),
        r.get("unit", ""),
        r["qty"],
        r.get("unit_cost", 0),
        r.get("net_cost", 0),
        r["canonical_key"],
        r["occurrence_index"],
        r["identity_key"],
        r["source_row_number"],
        json.dumps(r.get("original_row_json", {}), ensure_ascii=False),
        row_status,
    ))
    return new_id


# ─── Resolution contract ──────────────────────────────────────────────────────

def _validate_resolutions(diff: dict, resolutions_list: list[Resolution]) -> dict[str, Resolution]:
    """
    Validate the resolutions list and return a mapping of {row_id: Resolution}.
    Raises HTTPException(409) if any validation fails.
    """
    # 1. Check duplicate row_id from the list before making the dict/map
    seen_row_ids = set()
    for res in resolutions_list:
        if res.row_id in seen_row_ids:
            raise HTTPException(409, {
                "error": "invalid_resolution",
                "detail": f"Duplicate resolution row_id: {res.row_id}"
            })
        seen_row_ids.add(res.row_id)

    # 2. Build maps of live diff rows
    needs_review_ids = {row["id"] for row in diff.get("needs_review", [])}
    missing_ids = {row["id"] for row in diff.get("missing_from_reexport", [])}
    allowed_ids = needs_review_ids.union(missing_ids)

    # 3. Check for unknown row_id and incorrect action types
    resolution_map = {}
    for res in resolutions_list:
        if res.row_id not in allowed_ids:
            raise HTTPException(409, {
                "error": "invalid_resolution",
                "detail": f"Resolution row_id {res.row_id} not found in needs_review or missing_from_reexport"
            })
        if res.row_id in needs_review_ids:
            if res.action not in ("retain", "supersede"):
                raise HTTPException(409, {
                    "error": "invalid_resolution",
                    "detail": f"Action '{res.action}' is invalid for needs_review row {res.row_id} (must be 'retain' or 'supersede')"
                })
        elif res.row_id in missing_ids:
            if res.action not in ("retain", "void"):
                raise HTTPException(409, {
                    "error": "invalid_resolution",
                    "detail": f"Action '{res.action}' is invalid for missing_from_reexport row {res.row_id} (must be 'retain' or 'void')"
                })
        resolution_map[res.row_id] = res

    # 4. Check that every blocking row has a resolution
    unresolved = []
    for row in diff.get("needs_review", []):
        if row["id"] not in resolution_map:
            unresolved.append({
                "type": "needs_review",
                "row_id": row["id"],
                "item_name": row.get("item_name"),
            })
    for row in diff.get("missing_from_reexport", []):
        if row["id"] not in resolution_map:
            unresolved.append({
                "type": "missing_from_reexport",
                "row_id": row["id"],
                "item_name": row.get("item_name"),
            })

    if unresolved:
        raise HTTPException(409, {
            "error": "unresolved_rows",
            "detail": (
                "All needs_review and missing_from_reexport rows must have a resolution "
                "before approving. Provide resolutions[] covering each listed row_id."
            ),
            "unresolved": unresolved,
        })

    return resolution_map


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
    branch_code = normalize_branch_code(branch_code)
    conn = None
    try:
        # 1. Parse atomically (raises ValueError on any bad row)
        try:
            rows = parse_stock_in_file(df, branch_code=branch_code)
        except ValueError as e:
            conn_fail = None
            try:
                conn_fail = get_db_conn()
                with conn_fail.cursor() as cur:
                    cur.execute(
                        "UPDATE public.pos_imports "
                        "SET status='failed', error_message=%s, finished_at=now() "
                        "WHERE id=%s",
                        (str(e)[:2000], import_id),
                    )
                    conn_fail.commit()
            finally:
                if conn_fail:
                    try:
                        conn_fail.close()
                    except Exception:
                        pass
            _set({"status": "error", "error": str(e)})
            return

        # 2. Stage rows into stock_in_staging inside a single transaction
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

            # 3. Compute period from staged rows
            dates = [r["received_date"] for r in rows if r["received_date"]]
            period_start = min(dates) if dates else None
            period_end   = max(dates) if dates else None

            # 4. Load committed rows for this branch + period
            committed: list[dict] = []
            if period_start and period_end:
                committed = _fetch_committed_rows(cur, branch_code, period_start, period_end)

            # 5. Reconcile diff
            staged_for_diff = _fetch_staged_rows(cur, import_id)
            diff = reconcile_diff(staged_for_diff, committed)
            counts = _diff_counts(diff)

            # 6. Update pos_imports status
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
            # Commit exactly once at the end of the transaction
            conn.commit()

        _set({
            "status": "success",
            "result": {
                "import_id":    import_id,
                "report_type":  "stock_in_refill",
                "status":       "needs_review",
                "rows_staged":  len(rows),
                "period_start": str(period_start) if period_start else None,
                "period_end":   str(period_end) if period_end else None,
                "diff_counts":  counts,
                "detail":       "staged — ตรวจสอบ diff ที่ GET /pos/stock-in/diff/{import_id} ก่อน approve",
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
                conn.close()
            except Exception:
                pass
            conn = None

        # Open separate connection to update status to failed
        conn_fail = None
        try:
            conn_fail = get_db_conn()
            with conn_fail.cursor() as cur:
                cur.execute(
                    "UPDATE public.pos_imports SET status='failed', "
                    "error_message=%s, finished_at=now() WHERE id=%s",
                    (str(e)[:2000], import_id),
                )
                conn_fail.commit()
        except Exception:
            pass
        finally:
            if conn_fail:
                try:
                    conn_fail.close()
                except Exception:
                    pass
        _set({"status": "error", "error": str(e)})
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass


# ─── API models ───────────────────────────────────────────────────────────────

class Resolution(BaseModel):
    """
    Resolution for one needs_review or missing_from_reexport row.

    row_id:
      - needs_review row:          the staged row's id (from stock_in_staging)
      - missing_from_reexport row: the committed row's id (from stock_in_lines)
    action:
      - "retain"    — keep the committed row as-is; discard the staged counterpart (if any)
      - "supersede" — insert staged as new active, mark committed as superseded (needs_review only)
      - "void"      — mark committed row as voided (missing_from_reexport only)
    """
    row_id: str
    action: str
    reason: Optional[str] = None

    @field_validator("action")
    @classmethod
    def _valid_action(cls, v: str) -> str:
        if v not in ("retain", "supersede", "void"):
            raise ValueError("action must be 'retain', 'supersede', or 'void'")
        return v


class ApproveRequest(BaseModel):
    """
    Body for POST /pos/stock-in/approve/{import_id}.

    approved_by is NOT in this model — it is read exclusively from the JWT token.

    expected_counts must exactly match the live diff at approve-time; a mismatch
    returns 409 stale_review so the client knows to re-fetch and re-confirm.

    resolutions must cover every needs_review and missing_from_reexport row.
    Pure-new (insert) and unchanged (skip) rows need no resolution.
    """
    reason: Optional[str] = None
    expected_counts: dict   # {new, unchanged, changed, missing}
    resolutions: list[Resolution] = Field(default_factory=list)


class CancelRequest(BaseModel):
    """
    Body for POST /pos/stock-in/cancel/{import_id}.

    cancelled_by is NOT in this model — it is read exclusively from the JWT token.
    """
    reason: Optional[str] = None


# ─── GET /pos/stock-in/diff/{import_id} ──────────────────────────────────────

@router.get("/stock-in/diff/{import_id}")
def get_stock_in_diff(import_id: str, _admin: dict = Depends(_require_admin_role)):
    """
    Return the current reconcile diff for a staged import.
    Call before approving so the user can review insert/changed/missing rows.
    Admin-only — exposes raw stock-in line data.
    """
    _validate_uuid(import_id)

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
            branch_code = normalize_branch_code(branch_code)

            if status not in ("needs_review", "staged"):
                raise HTTPException(
                    409,
                    f"Import status is '{status}'; diff only available for needs_review/staged imports",
                )

            staged    = _fetch_staged_rows(cur, import_id)
            committed = _fetch_committed_rows(cur, branch_code, period_start, period_end) if period_start else []
            diff      = reconcile_diff(staged, committed)
            counts    = _diff_counts(diff)

        return {
            "import_id":             import_id,
            "status":                status,
            "branch_code":           branch_code,
            "period_start":          str(period_start) if period_start else None,
            "period_end":            str(period_end)   if period_end   else None,
            "counts":                counts,
            "insert":                diff["insert"],
            "skip":                  diff["skip"],
            "needs_review":          diff["needs_review"],
            "missing_from_reexport": diff["missing_from_reexport"],
        }
    finally:
        conn.close()


# ─── POST /pos/stock-in/approve/{import_id} ───────────────────────────────────

@router.post("/stock-in/approve/{import_id}")
def approve_stock_in(
    import_id: str,
    body: ApproveRequest,
    _admin: dict = Depends(_require_admin_role),
):
    """
    Approve a staged stock-in import.  Full atomic 12-step transaction:

     1. Validate import_id format
     2. Admin gate (JWT _role==admin)
     3. FOR UPDATE lock on pos_imports
     4. Status gate (must be needs_review/staged)
     5. Re-load staged + committed rows under the lock
     6. Re-compute diff (stale-review guard)
     7. Compare live counts vs expected_counts → 409 stale_review on mismatch
     8. Validate resolutions cover all needs_review + missing rows → 409 unresolved_rows
     9. Insert pure-new rows (diff["insert"]) into stock_in_lines
    10. Execute needs_review resolutions (supersede → insert new + mark old superseded;
                                          retain    → discard staged, keep committed)
    11. Execute missing_from_reexport resolutions (void → mark committed voided;
                                                   retain → no-op)
    12. Write stock_in_reconcile_log (append-only audit)
    13. Update pos_imports status='success'
    14. DELETE stock_in_staging rows for this import
    15. COMMIT

    All-or-nothing: any failure rolls back the entire transaction.
    """
    _validate_uuid(import_id)
    approved_by = _admin_identity(_admin)

    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            # ── 3. Lock ────────────────────────────────────────────────────────
            cur.execute(
                "SELECT status, branch_code, period_start, period_end "
                "FROM public.pos_imports WHERE id=%s FOR UPDATE",
                (import_id,),
            )
            row = cur.fetchone()
            if not row:
                raise HTTPException(404, f"Import {import_id} not found")
            status, branch_code, period_start, period_end = row
            branch_code = normalize_branch_code(branch_code)

            # ── 4. Status gate ────────────────────────────────────────────────
            if status not in ("needs_review", "staged"):
                raise HTTPException(409, {
                    "error":  "not_approvable",
                    "detail": f"Import is '{status}'; only needs_review/staged can be approved",
                })

            # ── 4a. Branch authority check ────────────────────────────────────
            cur.execute(
                "SELECT COUNT(*) FROM public.stock_in_staging "
                "WHERE import_id = %s AND branch_code != %s",
                (import_id, branch_code),
            )
            mismatch_count = cur.fetchone()[0]
            if mismatch_count > 0:
                raise HTTPException(409, {
                    "error": "branch_mismatch",
                    "detail": f"Found {mismatch_count} staged rows with branch code not matching authoritative import branch '{branch_code}'"
                })

            # ── 5. Re-load under lock ─────────────────────────────────────────
            staged    = _fetch_staged_rows(cur, import_id)
            committed = _fetch_committed_rows(cur, branch_code, period_start, period_end) if period_start else []
            diff      = reconcile_diff(staged, committed)
            live_counts = _diff_counts(diff)

            # ── 6-7. Stale review guard ───────────────────────────────────────
            if live_counts != body.expected_counts:
                raise HTTPException(409, {
                    "error":           "stale_review",
                    "detail":          "Reconcile diff changed since you reviewed it. Re-fetch /diff and re-confirm.",
                    "expected_counts": body.expected_counts,
                    "current_counts":  live_counts,
                })

            # ── 8. Resolution validation ──────────────────────────────────────
            resolution_map = _validate_resolutions(diff, body.resolutions)

            # ── 9. Insert pure-new rows ───────────────────────────────────────
            new_rows_inserted = 0
            for r in diff["insert"]:
                _insert_stock_in_line(cur, import_id, r, "active")
                new_rows_inserted += 1

            # ── 10. needs_review resolutions ──────────────────────────────────
            for staged_row in diff["needs_review"]:
                res = resolution_map[staged_row["id"]]
                if res.action == "supersede":
                    new_id = _insert_stock_in_line(cur, import_id, staged_row, "active")
                    # Mark the matching committed row as superseded using counterpart_id
                    old_id = staged_row["counterpart_id"]
                    cur.execute(
                        "UPDATE public.stock_in_lines "
                        "SET row_status='superseded', superseded_by=%s "
                        "WHERE id=%s AND row_status='active'",
                        (new_id, old_id),
                    )
                    if cur.rowcount != 1:
                        raise ValueError(f"Supersede failed: committed row {old_id} is not active or not found")
                # "retain": keep committed row, discard staged — no DB action needed

            # ── 11. missing_from_reexport resolutions ─────────────────────────
            for committed_row in diff["missing_from_reexport"]:
                res = resolution_map.get(committed_row["id"])
                if res and res.action == "void":
                    cur.execute(
                        "UPDATE public.stock_in_lines "
                        "SET row_status='voided', voided_by=%s, voided_at=now(), void_reason=%s "
                        "WHERE id=%s AND row_status='active'",
                        (approved_by, res.reason, committed_row["id"]),
                    )
                    if cur.rowcount != 1:
                        raise ValueError(f"Void failed: committed row {committed_row['id']} is not active or not found")
                # "retain": keep committed row active — no DB action needed

            # ── 12. Reconcile log ─────────────────────────────────────────────
            cur.execute("""
                INSERT INTO public.stock_in_reconcile_log
                  (id, import_id_new, branch_code, period_start, period_end,
                   approved_by, approved_at, decision, reason, counts_json,
                   before_after_diff)
                VALUES (%s,%s,%s,%s,%s, %s,now(),'approve',%s, %s,%s)
            """, (
                str(uuid.uuid4()),
                import_id,
                branch_code,
                period_start,
                period_end,
                approved_by,
                body.reason,
                json.dumps(live_counts),
                json.dumps({
                    "insert": [r.get("canonical_key") for r in diff["insert"]],
                    "skip":   len(diff["skip"]),
                    "needs_review": [
                        {"canonical_key": r.get("canonical_key"),
                         "action": resolution_map[r["id"]].action}
                        for r in diff["needs_review"]
                    ],
                    "missing": [
                        {"canonical_key": r.get("canonical_key"),
                         "action": resolution_map[r["id"]].action}
                        for r in diff["missing_from_reexport"]
                    ],
                }),
            ))

            # ── 13. Update pos_imports ────────────────────────────────────────
            cur.execute(
                "UPDATE public.pos_imports "
                "SET status='success', finished_at=now() WHERE id=%s",
                (import_id,),
            )

            # ── 14. Clear staging rows ────────────────────────────────────────
            cur.execute(
                "DELETE FROM public.stock_in_staging WHERE import_id=%s",
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


# ─── POST /pos/stock-in/cancel/{import_id} ────────────────────────────────────

@router.post("/stock-in/cancel/{import_id}")
def cancel_stock_in(
    import_id: str,
    body: CancelRequest,
    _admin: dict = Depends(_require_admin_role),
):
    """
    Cancel a staged stock-in import.

    Only needs_review/staged imports can be cancelled.  Atomically:
    - Clears stock_in_staging rows
    - Writes a 'cancel' entry in stock_in_reconcile_log (audit)
    - Updates pos_imports status='cancelled'

    stock_in_lines are untouched.
    cancelled_by comes from the JWT token (not the request body).
    """
    _validate_uuid(import_id)
    cancelled_by = _admin_identity(_admin)

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
            branch_code = normalize_branch_code(branch_code)

            if status not in ("needs_review", "staged"):
                raise HTTPException(409, {
                    "error":  "not_cancellable",
                    "detail": f"Import is '{status}'; only needs_review/staged can be cancelled",
                })

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
                cancelled_by,
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


# ─── POST /pos/stock-in/recover/{import_id} ───────────────────────────────────

# An import stuck in 'parsing' for this long is assumed to have crashed.
_STUCK_THRESHOLD_MINUTES = 10


@router.post("/stock-in/recover/{import_id}")
def recover_stock_in(import_id: str, _admin: dict = Depends(_require_admin_role)):
    """
    Recover a stuck stock-in import.

    An import is 'stuck' if its status is 'parsing' AND processing_started_at
    is older than _STUCK_THRESHOLD_MINUTES (background process likely crashed).

    Recovery strategy:
    - If stock_in_staging still has rows for this import:
        re-compute the reconcile diff from the existing staged data and move the
        import back to 'needs_review'.  No original file bytes needed.
    - If staging is empty:
        mark the import as 'failed' (data was never staged before the crash).

    Behaviour matrix:
    - import not found                     → 404
    - status != 'parsing'                  → 409 not_recoverable
    - status='parsing', started < threshold → 409 not_stuck_yet (too soon)
    - status='parsing', started=NULL or old → recover using staging data
    - already needs_review / success        → 409 not_recoverable

    Admin-only.  Idempotent: calling again on an import already recovered (now
    'needs_review') returns 409 gracefully.
    """
    _validate_uuid(import_id)
    admin_user = _admin_identity(_admin)

    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            # ── Lock + validate state ─────────────────────────────────────────
            cur.execute(
                "SELECT status, branch_code, uploaded_by, processing_started_at "
                "FROM public.pos_imports WHERE id=%s FOR UPDATE",
                (import_id,),
            )
            row = cur.fetchone()
            if not row:
                raise HTTPException(404, f"Import {import_id} not found")
            status, branch_code, uploaded_by, processing_started_at = row
            branch_code = normalize_branch_code(branch_code)

            if status != "parsing":
                raise HTTPException(409, {
                    "error":  "not_recoverable",
                    "detail": f"Import is '{status}'; only 'parsing' imports can be recovered",
                    "status": status,
                })

            # ── Threshold check ───────────────────────────────────────────────
            if processing_started_at is not None:
                now_utc = datetime.now(timezone.utc)
                if hasattr(processing_started_at, "tzinfo") and processing_started_at.tzinfo:
                    started = processing_started_at
                else:
                    started = processing_started_at.replace(tzinfo=timezone.utc)
                age_min = (now_utc - started).total_seconds() / 60
                if age_min < _STUCK_THRESHOLD_MINUTES:
                    raise HTTPException(409, {
                        "error":       "not_stuck_yet",
                        "detail":      (
                            f"Import has been processing for {age_min:.1f} min; "
                            f"recover is available after {_STUCK_THRESHOLD_MINUTES} min"
                        ),
                        "age_minutes": age_min,
                    })

            # ── Attempt recovery from existing staging data ───────────────────
            staged = _fetch_staged_rows(cur, import_id)

            if not staged:
                # Crash happened before any rows were staged — cannot recover data.
                cur.execute(
                    "UPDATE public.pos_imports "
                    "SET status='failed', error_message=%s, finished_at=now() "
                    "WHERE id=%s",
                    ("Recovery failed: no staged rows found (process crashed before staging)", import_id),
                )
                conn.commit()
                return {
                    "import_id":    import_id,
                    "recovered_by": admin_user,
                    "outcome":      "failed",
                    "detail":       "No staged rows found; import marked failed.",
                }

            # Verify branch authority check
            cur.execute(
                "SELECT COUNT(*) FROM public.stock_in_staging "
                "WHERE import_id = %s AND branch_code != %s",
                (import_id, branch_code),
            )
            mismatch_count = cur.fetchone()[0]
            if mismatch_count > 0:
                raise HTTPException(409, {
                    "error": "branch_mismatch",
                    "detail": f"Found {mismatch_count} staged rows with branch code not matching authoritative import branch '{branch_code}'"
                })

            # Re-derive period + committed rows from staged data
            dates = [
                r["received_date"] for r in staged
                if r.get("received_date")
            ]
            period_start = min(dates) if dates else None
            period_end   = max(dates) if dates else None

            committed: list[dict] = []
            if period_start and period_end:
                committed = _fetch_committed_rows(cur, branch_code, period_start, period_end)

            diff   = reconcile_diff(staged, committed)
            counts = _diff_counts(diff)

            cur.execute(
                "UPDATE public.pos_imports "
                "SET status='needs_review', period_start=%s, period_end=%s, "
                "row_count=%s, error_message=%s, finished_at=now() "
                "WHERE id=%s",
                (period_start, period_end, len(staged), json.dumps(counts), import_id),
            )
            conn.commit()

        return {
            "import_id":    import_id,
            "recovered_by": admin_user,
            "outcome":      "needs_review",
            "rows_staged":  len(staged),
            "diff_counts":  counts,
        }

    except HTTPException:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        logger.exception("stock_in recover failed")
        raise HTTPException(500, f"Recover failed: {e}") from e
    finally:
        conn.close()
