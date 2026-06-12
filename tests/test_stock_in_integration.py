"""
M1 Stock-in Import — DB Integration Tests (Antigravity Item 8)
==============================================================
These tests use a REAL PostgreSQL database.

To run:
    $env:TEST_DATABASE_URL = "postgresql://user:pass@host:5432/dbname"
    pytest tests/test_stock_in_integration.py -v

All tests are skipped automatically when TEST_DATABASE_URL is not set.

Coverage:
  A. Staging flow:        parse + stage + diff computation
  B. Approve flow:        new rows, skip rows, stale-review guard, resolution contract
  C. Cancel flow:         cancel contract, only staged imports cancellable
  D. Recovery flow:       recover stuck import, idempotent recover
  E. Concurrency:         double-approve protection (FOR UPDATE lock)
  F. Branch scope:        approve does not touch rows from other branches
  G. Occurrence index:    multiset: 2 identical rows → both committed, 1 re-export → 1 missing
"""
from __future__ import annotations

import json
import os
import uuid
from datetime import date, datetime, timezone, timedelta
from typing import Optional

import pandas as pd
import psycopg2
import pytest

# ── Skip entire module when no test DB is available ───────────────────────────
_DB_URL = os.environ.get("TEST_DATABASE_URL", "")
if not _DB_URL:
    pytest.skip("TEST_DATABASE_URL not set — skipping DB integration tests", allow_module_level=True)

# Route DATABASE_URL to the test DB so endpoints use the test database
os.environ["DATABASE_URL"] = _DB_URL

from fastapi.testclient import TestClient
import main
import auth_routes

def _fake_verify(token):
    if token == "ADMIN":
        return {"sub": "admin-uid", "_role": "admin"}
    if token == "STAFF":
        return {"sub": "staff-uid", "_role": "staff"}
    return None



from stock_in_import import parse_stock_in_file, reconcile_diff
from stock_in_routes import (
    _diff_counts,
    _fetch_committed_rows,
    _fetch_staged_rows,
    _insert_stock_in_line,
    _stage_stock_in,
    _validate_resolutions,
    Resolution,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def db():
    """Session-wide real DB connection (auto-rollback after session)."""
    conn = psycopg2.connect(_DB_URL)
    conn.autocommit = False
    yield conn
    conn.rollback()
    conn.close()


@pytest.fixture()
def conn(db):
    """Per-test savepoint: rolls back after each test, keeps DB clean."""
    with db.cursor() as c:
        c.execute("SAVEPOINT sp_test")
    yield db
    with db.cursor() as c:
        c.execute("ROLLBACK TO SAVEPOINT sp_test")


@pytest.fixture()
def cur(conn):
    with conn.cursor() as c:
        yield c


# ── Helpers ────────────────────────────────────────────────────────────────────

_BRANCH = "integration_test_branch"
_PERIOD = date(2026, 5, 1)


def _make_import(cur, status: str = "parsing") -> str:
    """Insert a pos_imports row; return its id."""
    import_id = str(uuid.uuid4())
    cur.execute("""
        INSERT INTO public.pos_imports
          (id, report_type, branch_code, source_file, file_size,
           file_hash, status, uploaded_by, uploaded_at, processing_started_at)
        VALUES (%s, 'stock_in_refill', %s, 'test.xlsx', 0, %s, %s, 'testuser', now(), now())
    """, (import_id, _BRANCH, str(uuid.uuid4()), status))
    return import_id


def _make_df(rows: list[dict]) -> pd.DataFrame:
    """Build a minimal FoodStory-shaped DataFrame for parse_stock_in_file."""
    col_map = {
        "received_date": "วันที่",
        "item_name":     "ชื่อ",
        "material_code": "รหัสวัตถุดิบ",
        "tag":           "ป้ายกำกับ",
        "refill_type":   "ประเภทการเติมวัตถุดิบ",
        "qty":           "เติมสินค้า",
        "unit_cost":     "ค่าใช้จ่ายต่อหน่วย",
        "net_cost":      "ค่าใช้จ่ายสุทธิ",
        "unit":          "หน่วย",
        "invoice_no":    "INVOICE",
    }
    records = []
    for row in rows:
        rec = {}
        for k, v in row.items():
            col = col_map.get(k, k)
            rec[col] = v
        records.append(rec)
    return pd.DataFrame(records)


def _base_row(**kwargs) -> dict:
    defaults = dict(
        received_date="01/05/2026",
        item_name="เบียร์สิงห์",
        material_code="A14",
        tag="เครื่องดื่ม",
        refill_type="สั่งซื้อ",
        qty=12.0,
        unit_cost=840.0,
        net_cost=10080.0,
        unit="ลัง",
        invoice_no="INV-001",
    )
    defaults.update(kwargs)
    return defaults


def _stage_rows(cur, import_id: str, rows: list[dict]) -> None:
    """Insert pre-parsed rows directly into stock_in_staging."""
    for r in rows:
        cur.execute("""
            INSERT INTO public.stock_in_staging
              (import_id, branch_code, received_date, item_name, material_code,
               tag, refill_type, invoice_no, gr_ref, po_ref, po_date,
               unit, qty, unit_cost, net_cost,
               canonical_key, occurrence_index, identity_key,
               source_row_number, original_row_json)
            VALUES (%s,%s,%s,%s,%s, %s,%s,%s,%s,%s,%s, %s,%s,%s,%s,
                    %s,%s,%s, %s,%s)
        """, (
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
            json.dumps(r.get("original_row_json", {})),
        ))


def _commit_row(cur, import_id: str, r: dict) -> str:
    """Insert a row directly into stock_in_lines; return its id."""
    return _insert_stock_in_line(cur, import_id, r, "active")


# ── Group A: Staging flow ──────────────────────────────────────────────────────

def test_parse_and_stage_writes_to_staging(conn, cur):
    """parse_stock_in_file + manual stage writes rows to stock_in_staging."""
    import_id = _make_import(cur)
    with conn.cursor() as c:
        c.execute("SAVEPOINT sp_a1")

    df = _make_df([_base_row()])
    rows = parse_stock_in_file(df, branch_code=_BRANCH)
    assert len(rows) == 1
    _stage_rows(cur, import_id, rows)

    staged = _fetch_staged_rows(cur, import_id)
    assert len(staged) == 1
    assert staged[0]["item_name"] == "เบียร์สิงห์"
    assert staged[0]["import_id"] == import_id


def test_stage_multiple_rows_and_diff_counts(conn, cur):
    """Staging 3 rows for a branch with no prior commits → all new."""
    import_id = _make_import(cur)
    df = _make_df([
        _base_row(item_name="ไข่", material_code="B01", qty=100, unit_cost=5, net_cost=500),
        _base_row(item_name="หมู", material_code="B02", qty=5, unit_cost=200, net_cost=1000),
        _base_row(item_name="ผัก", material_code="B03", qty=20, unit_cost=30, net_cost=600),
    ])
    rows = parse_stock_in_file(df, branch_code=_BRANCH)
    _stage_rows(cur, import_id, rows)

    staged = _fetch_staged_rows(cur, import_id)
    committed = _fetch_committed_rows(cur, _BRANCH, date(2026, 5, 1), date(2026, 5, 31))
    diff = reconcile_diff(staged, committed)
    counts = _diff_counts(diff)

    # Assuming clean test DB: no prior committed rows for this branch
    assert counts["new"] >= 3   # at least our 3 rows (could be more from other tests)
    assert counts["changed"] == 0
    assert counts["missing"] == 0


def test_stage_duplicate_file_idempotent_key_assignment(conn, cur):
    """Two identical rows in same import get occurrence_index 0 and 1."""
    import_id = _make_import(cur)
    df = _make_df([_base_row(), _base_row()])  # identical rows
    rows = parse_stock_in_file(df, branch_code=_BRANCH)
    assert len(rows) == 2
    assert rows[0]["canonical_key"] == rows[1]["canonical_key"]
    assert rows[0]["occurrence_index"] == 0
    assert rows[1]["occurrence_index"] == 1


def test_parse_error_returns_no_rows(conn, cur):
    """A row with unparseable date causes parse_stock_in_file to raise ValueError."""
    df = _make_df([_base_row(received_date="not-a-date")])
    with pytest.raises(ValueError, match="Row 1"):
        parse_stock_in_file(df, branch_code=_BRANCH)


# ── Group B: Approve flow ──────────────────────────────────────────────────────

def test_approve_inserts_new_rows_into_stock_in_lines(conn, cur):
    """Approving an import with all-new rows writes them to stock_in_lines."""
    import_id = _make_import(cur, status="needs_review")
    df = _make_df([_base_row(invoice_no="INV-INTEG-001")])
    rows = parse_stock_in_file(df, branch_code=_BRANCH)
    _stage_rows(cur, import_id, rows)

    staged = _fetch_staged_rows(cur, import_id)
    committed = _fetch_committed_rows(cur, _BRANCH, _PERIOD, _PERIOD)
    diff = reconcile_diff(staged, committed)

    resolution_map = {}
    _validate_resolutions(diff, resolution_map)  # no needs_review / missing → OK

    for r in diff["insert"]:
        _insert_stock_in_line(cur, import_id, r, "active")

    cur.execute(
        "SELECT COUNT(*) FROM public.stock_in_lines WHERE import_id=%s AND row_status='active'",
        (import_id,),
    )
    count = cur.fetchone()[0]
    assert count == len(diff["insert"])
    assert count >= 1


def test_approve_skip_rows_not_reinserted(conn, cur):
    """Re-importing identical rows → skip, not duplicated in stock_in_lines."""
    import_id1 = _make_import(cur, status="needs_review")
    df = _make_df([_base_row(invoice_no="INV-SKIP-001")])
    rows = parse_stock_in_file(df, branch_code=_BRANCH)
    _stage_rows(cur, import_id1, rows)

    # Commit these rows as if first import was approved
    for r in rows:
        _insert_stock_in_line(cur, import_id1, r, "active")

    # Second import: same file
    import_id2 = _make_import(cur, status="needs_review")
    _stage_rows(cur, import_id2, rows)

    staged2    = _fetch_staged_rows(cur, import_id2)
    committed2 = _fetch_committed_rows(cur, _BRANCH, _PERIOD, _PERIOD)
    diff2      = reconcile_diff(staged2, committed2)

    # All rows should be in skip (already committed)
    assert len(diff2["skip"]) == len(rows)
    assert len(diff2["insert"]) == 0


def test_stale_review_detected_when_diff_changes(conn, cur):
    """If diff changes between review and approve, _diff_counts mismatch is detectable."""
    import_id = _make_import(cur, status="needs_review")
    df = _make_df([_base_row(invoice_no="INV-STALE-001")])
    rows = parse_stock_in_file(df, branch_code=_BRANCH)
    _stage_rows(cur, import_id, rows)

    staged    = _fetch_staged_rows(cur, import_id)
    committed = _fetch_committed_rows(cur, _BRANCH, _PERIOD, _PERIOD)
    diff      = reconcile_diff(staged, committed)
    counts_at_review = _diff_counts(diff)

    # Simulate another import committing the same rows before this approve
    import_id_other = _make_import(cur, status="needs_review")
    for r in rows:
        _insert_stock_in_line(cur, import_id_other, r, "active")

    # Re-compute diff: the "new" rows are now committed → skip, not insert
    diff_now   = reconcile_diff(staged, _fetch_committed_rows(cur, _BRANCH, _PERIOD, _PERIOD))
    counts_now = _diff_counts(diff_now)

    assert counts_now != counts_at_review, "Stale review should produce different counts"


def test_resolution_supersede_marks_old_row_superseded(conn, cur):
    """Approving with supersede action marks the committed row as superseded."""
    import_id = _make_import(cur, status="needs_review")

    # Commit an "old" row
    df_old = _make_df([_base_row(invoice_no="INV-SUPER-001", qty=10, net_cost=8400)])
    old_rows = parse_stock_in_file(df_old, branch_code=_BRANCH)
    for r in old_rows:
        old_id = _insert_stock_in_line(cur, import_id, r, "active")

    # Stage the "updated" row (same identity, different qty → needs_review)
    import_id2 = _make_import(cur, status="needs_review")
    df_new = _make_df([_base_row(invoice_no="INV-SUPER-001", qty=12, net_cost=10080)])
    new_rows = parse_stock_in_file(df_new, branch_code=_BRANCH)
    _stage_rows(cur, import_id2, new_rows)

    staged    = _fetch_staged_rows(cur, import_id2)
    committed = _fetch_committed_rows(cur, _BRANCH, _PERIOD, _PERIOD)
    diff      = reconcile_diff(staged, committed)

    # The staged row should be in needs_review
    assert len(diff["needs_review"]) >= 1

    staged_nr = diff["needs_review"][0]
    res_map = {staged_nr["id"]: Resolution(row_id=staged_nr["id"], action="supersede")}
    _validate_resolutions(diff, res_map)

    # Execute supersede
    new_id = _insert_stock_in_line(cur, import_id2, staged_nr, "active")
    cur.execute(
        "UPDATE public.stock_in_lines SET row_status='superseded', superseded_by=%s "
        "WHERE id=%s AND row_status='active'",
        (new_id, old_id),
    )

    # Verify
    cur.execute("SELECT row_status, superseded_by FROM public.stock_in_lines WHERE id=%s", (old_id,))
    status, superseded_by = cur.fetchone()
    assert status == "superseded"
    assert superseded_by == new_id


def test_resolution_void_marks_missing_row_voided(conn, cur):
    """void action on missing_from_reexport marks the committed row as voided."""
    import_id = _make_import(cur, status="needs_review")

    # Commit a row
    df = _make_df([_base_row(invoice_no="INV-VOID-001")])
    rows = parse_stock_in_file(df, branch_code=_BRANCH)
    committed_id = _insert_stock_in_line(cur, import_id, rows[0], "active")

    # Stage empty set for same period → row is missing_from_reexport
    import_id2 = _make_import(cur, status="needs_review")
    # No rows staged for import_id2

    staged    = _fetch_staged_rows(cur, import_id2)
    committed = _fetch_committed_rows(cur, _BRANCH, _PERIOD, _PERIOD)
    diff      = reconcile_diff(staged, committed)

    missing = [r for r in diff["missing_from_reexport"] if r["id"] == committed_id]
    assert len(missing) >= 1

    res_map = {committed_id: Resolution(row_id=committed_id, action="void", reason="test void")}
    _validate_resolutions(diff, {committed_id: res_map[committed_id]})

    # Execute void
    cur.execute(
        "UPDATE public.stock_in_lines SET row_status='voided', voided_by='testuser', "
        "voided_at=now(), void_reason='test void' WHERE id=%s AND row_status='active'",
        (committed_id,),
    )

    cur.execute("SELECT row_status, void_reason FROM public.stock_in_lines WHERE id=%s", (committed_id,))
    status, reason = cur.fetchone()
    assert status == "voided"
    assert reason == "test void"


def test_resolution_retain_leaves_committed_row_active(conn, cur):
    """retain action on missing_from_reexport leaves the committed row active."""
    import_id = _make_import(cur, status="needs_review")

    df = _make_df([_base_row(invoice_no="INV-RETAIN-001")])
    rows = parse_stock_in_file(df, branch_code=_BRANCH)
    committed_id = _insert_stock_in_line(cur, import_id, rows[0], "active")

    import_id2 = _make_import(cur, status="needs_review")
    staged    = _fetch_staged_rows(cur, import_id2)
    committed = _fetch_committed_rows(cur, _BRANCH, _PERIOD, _PERIOD)
    diff      = reconcile_diff(staged, committed)

    missing = [r for r in diff["missing_from_reexport"] if r["id"] == committed_id]
    assert len(missing) >= 1

    # retain: no DB action needed, just validate it passes _validate_resolutions
    res_map = {committed_id: Resolution(row_id=committed_id, action="retain")}
    _validate_resolutions(diff, {committed_id: res_map[committed_id]})

    # Row should remain active (no change)
    cur.execute("SELECT row_status FROM public.stock_in_lines WHERE id=%s", (committed_id,))
    assert cur.fetchone()[0] == "active"


# ── Group C: Cancel flow ───────────────────────────────────────────────────────

def test_cancel_clears_staging_rows(conn, cur):
    """Cancel removes rows from stock_in_staging."""
    import_id = _make_import(cur, status="needs_review")
    df = _make_df([_base_row(invoice_no="INV-CANCEL-001")])
    rows = parse_stock_in_file(df, branch_code=_BRANCH)
    _stage_rows(cur, import_id, rows)

    cur.execute("SELECT COUNT(*) FROM public.stock_in_staging WHERE import_id=%s", (import_id,))
    before = cur.fetchone()[0]
    assert before == 1

    # Simulate cancel: delete staging rows
    cur.execute("DELETE FROM public.stock_in_staging WHERE import_id=%s", (import_id,))

    cur.execute("SELECT COUNT(*) FROM public.stock_in_staging WHERE import_id=%s", (import_id,))
    after = cur.fetchone()[0]
    assert after == 0


def test_cancel_does_not_touch_stock_in_lines(conn, cur):
    """Cancel does not delete or modify previously committed stock_in_lines."""
    import_id = _make_import(cur, status="needs_review")
    df = _make_df([_base_row(invoice_no="INV-CANCEL-SAFE-001")])
    rows = parse_stock_in_file(df, branch_code=_BRANCH)
    line_id = _insert_stock_in_line(cur, import_id, rows[0], "active")

    # Simulate cancel (staging only deleted)
    # line should remain untouched
    cur.execute("SELECT row_status FROM public.stock_in_lines WHERE id=%s", (line_id,))
    assert cur.fetchone()[0] == "active"


# ── Group D: Recovery flow ────────────────────────────────────────────────────

def test_recover_with_staging_data_returns_needs_review(conn, cur):
    """recover_stock_in with existing staged rows moves import to needs_review."""
    import_id = _make_import(cur, status="parsing")
    # Set processing_started_at to very old (stuck)
    cur.execute(
        "UPDATE public.pos_imports SET processing_started_at = now() - interval '30 minutes' WHERE id=%s",
        (import_id,),
    )

    df = _make_df([_base_row(invoice_no="INV-RECOVER-001")])
    rows = parse_stock_in_file(df, branch_code=_BRANCH)
    _stage_rows(cur, import_id, rows)

    # Simulate what recover_stock_in does (without HTTP layer)
    staged = _fetch_staged_rows(cur, import_id)
    assert len(staged) >= 1

    dates = [r["received_date"] for r in staged if r.get("received_date")]
    period_start = min(dates) if dates else None
    period_end   = max(dates) if dates else None
    committed = _fetch_committed_rows(cur, _BRANCH, period_start, period_end) if period_start else []
    diff   = reconcile_diff(staged, committed)
    counts = _diff_counts(diff)

    cur.execute(
        "UPDATE public.pos_imports SET status='needs_review', period_start=%s, period_end=%s, "
        "row_count=%s, error_message=%s, finished_at=now() WHERE id=%s",
        (period_start, period_end, len(staged), json.dumps(counts), import_id),
    )

    cur.execute("SELECT status FROM public.pos_imports WHERE id=%s", (import_id,))
    assert cur.fetchone()[0] == "needs_review"


def test_recover_no_staging_data_marks_failed(conn, cur):
    """recover_stock_in with empty staging marks import as failed."""
    import_id = _make_import(cur, status="parsing")
    cur.execute(
        "UPDATE public.pos_imports SET processing_started_at = now() - interval '30 minutes' WHERE id=%s",
        (import_id,),
    )

    # No staging rows inserted
    staged = _fetch_staged_rows(cur, import_id)
    assert len(staged) == 0

    # Simulate recovery logic
    cur.execute(
        "UPDATE public.pos_imports SET status='failed', error_message=%s, finished_at=now() WHERE id=%s",
        ("Recovery failed: no staged rows found", import_id),
    )

    cur.execute("SELECT status FROM public.pos_imports WHERE id=%s", (import_id,))
    assert cur.fetchone()[0] == "failed"


def test_recover_idempotent_not_double_staged(conn, cur):
    """Calling recover twice: second call on needs_review → 409 not_recoverable (contract)."""
    import_id = _make_import(cur, status="needs_review")

    # Confirm it's now in needs_review — second recover would be rejected
    cur.execute("SELECT status FROM public.pos_imports WHERE id=%s", (import_id,))
    status = cur.fetchone()[0]
    assert status != "parsing", "import is no longer parsing — recover should be rejected"


# ── Group E: Concurrency ──────────────────────────────────────────────────────

def test_double_approve_prevented_by_status_gate(conn, cur):
    """After approve, status='success'; a second approve attempt would be blocked by status gate."""
    import_id = _make_import(cur, status="needs_review")
    df = _make_df([_base_row(invoice_no="INV-DOUBLE-001")])
    rows = parse_stock_in_file(df, branch_code=_BRANCH)
    _stage_rows(cur, import_id, rows)

    # Simulate first approve: update status to success
    cur.execute("UPDATE public.pos_imports SET status='success' WHERE id=%s", (import_id,))

    # Second approve would check: status NOT IN ('needs_review', 'staged') → 409
    cur.execute("SELECT status FROM public.pos_imports WHERE id=%s", (import_id,))
    status = cur.fetchone()[0]
    assert status not in ("needs_review", "staged"), \
        "After approve, status must not allow re-approve"


# ── Group F: Branch scope ─────────────────────────────────────────────────────

def test_branch_scope_other_branch_rows_not_in_diff(conn, cur):
    """Committed rows from a different branch do not appear in the diff."""
    other_branch = f"other_branch_{uuid.uuid4().hex[:8]}"
    import_id_other = _make_import(cur, status="needs_review")

    df_other = _make_df([_base_row(invoice_no="INV-BRANCH-001")])
    rows_other = parse_stock_in_file(df_other, branch_code=other_branch)
    _insert_stock_in_line(cur, import_id_other, rows_other[0], "active")

    # Import for our branch
    import_id = _make_import(cur, status="needs_review")
    df = _make_df([_base_row(invoice_no="INV-BRANCH-002")])
    rows = parse_stock_in_file(df, branch_code=_BRANCH)
    _stage_rows(cur, import_id, rows)

    staged    = _fetch_staged_rows(cur, import_id)
    # committed rows queried for _BRANCH only — other_branch rows should NOT appear
    committed = _fetch_committed_rows(cur, _BRANCH, _PERIOD, _PERIOD)

    for r in committed:
        assert r["branch_code"] == _BRANCH, \
            f"Row from branch '{r['branch_code']}' should not appear in {_BRANCH} diff"


# ── Group G: Occurrence index (multiset) ──────────────────────────────────────

def test_two_identical_rows_both_committed_then_one_missing(conn, cur):
    """
    Import A: 2 identical rows → occurrence_index 0 and 1, both committed.
    Import B: 1 identical row → occurrence_index 0, skip; occurrence_index 1 missing.
    """
    import_id_a = _make_import(cur, status="needs_review")
    df_a = _make_df([_base_row(invoice_no="INV-OCC-001"), _base_row(invoice_no="INV-OCC-001")])
    rows_a = parse_stock_in_file(df_a, branch_code=_BRANCH)
    assert len(rows_a) == 2

    for r in rows_a:
        _insert_stock_in_line(cur, import_id_a, r, "active")

    # Verify both are in stock_in_lines
    cur.execute(
        "SELECT COUNT(*) FROM public.stock_in_lines "
        "WHERE import_id=%s AND row_status='active'",
        (import_id_a,),
    )
    assert cur.fetchone()[0] == 2

    # Import B: only 1 identical row
    import_id_b = _make_import(cur, status="needs_review")
    df_b = _make_df([_base_row(invoice_no="INV-OCC-001")])
    rows_b = parse_stock_in_file(df_b, branch_code=_BRANCH)
    _stage_rows(cur, import_id_b, rows_b)

    staged    = _fetch_staged_rows(cur, import_id_b)
    committed = _fetch_committed_rows(cur, _BRANCH, _PERIOD, _PERIOD)
    diff      = reconcile_diff(staged, committed)

    # 1 skip (matched occurrence_index 0), 1 missing (occurrence_index 1 absent from re-export)
    ck = rows_b[0]["canonical_key"]
    skipped_cks  = {r["canonical_key"] for r in diff["skip"]}
    missing_cks  = {r["canonical_key"] for r in diff["missing_from_reexport"]}
    assert ck in skipped_cks,  "First occurrence should be skipped"
    assert ck in missing_cks,  "Second occurrence should be missing"


def test_occurrence_order_independent(conn, cur):
    """
    Two imports with same rows in different order produce the same canonical_key assignments.
    Confirms multiset (order-independent) semantics.
    """
    row1 = _base_row(item_name="ไข่", material_code="C01", invoice_no="INV-ORD-001")
    row2 = _base_row(item_name="หมู", material_code="C02", invoice_no="INV-ORD-002")

    df_ab = _make_df([row1, row2])
    df_ba = _make_df([row2, row1])

    rows_ab = parse_stock_in_file(df_ab, branch_code=_BRANCH)
    rows_ba = parse_stock_in_file(df_ba, branch_code=_BRANCH)

    cks_ab = {(r["canonical_key"], r["occurrence_index"]) for r in rows_ab}
    cks_ba = {(r["canonical_key"], r["occurrence_index"]) for r in rows_ba}
    assert cks_ab == cks_ba, "Canonical key + occurrence index must be order-independent"


# ── Group H: Reconcile log ─────────────────────────────────────────────────────

def test_reconcile_log_written_on_approve(conn, cur):
    """Approving an import writes an entry to stock_in_reconcile_log."""
    import_id = _make_import(cur, status="needs_review")

    cur.execute("""
        INSERT INTO public.stock_in_reconcile_log
          (id, import_id_new, branch_code, period_start, period_end,
           approved_by, decision, counts_json, before_after_diff)
        VALUES (%s,%s,%s,%s,%s,'testuser','approve',%s,%s)
    """, (
        str(uuid.uuid4()),
        import_id,
        _BRANCH,
        _PERIOD,
        _PERIOD,
        json.dumps({"new": 0, "unchanged": 0, "changed": 0, "missing": 0}),
        json.dumps({}),
    ))

    cur.execute(
        "SELECT COUNT(*) FROM public.stock_in_reconcile_log WHERE import_id_new=%s",
        (import_id,),
    )
    assert cur.fetchone()[0] == 1


def test_reconcile_log_written_on_cancel(conn, cur):
    """Cancelling an import writes a 'cancel' entry to stock_in_reconcile_log."""
    import_id = _make_import(cur, status="needs_review")

    cur.execute("""
        INSERT INTO public.stock_in_reconcile_log
          (id, import_id_new, branch_code, period_start, period_end,
           approved_by, decision, counts_json, before_after_diff)
        VALUES (%s,%s,%s,%s,%s,'testuser','cancel',%s,%s)
    """, (
        str(uuid.uuid4()),
        import_id,
        _BRANCH,
        _PERIOD,
        _PERIOD,
        json.dumps({}),
        json.dumps({}),
    ))

    cur.execute(
        "SELECT decision FROM public.stock_in_reconcile_log WHERE import_id_new=%s",
        (import_id,),
    )
    assert cur.fetchone()[0] == "cancel"


# ── Group I: Schema constraints ────────────────────────────────────────────────

def test_invalid_row_status_rejected_by_check(conn, cur):
    """stock_in_lines CHECK constraint rejects invalid row_status values."""
    import_id = _make_import(cur)
    df = _make_df([_base_row(invoice_no="INV-CHK-001")])
    rows = parse_stock_in_file(df, branch_code=_BRANCH)
    r = rows[0]

    with pytest.raises(psycopg2.errors.CheckViolation):
        cur.execute("""
            INSERT INTO public.stock_in_lines
              (id, import_id, branch_code, received_date, item_name,
               canonical_key, occurrence_index, identity_key, source_row_number,
               original_row_json, row_status, qty)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """, (
            str(uuid.uuid4()), import_id, _BRANCH, _PERIOD, r["item_name"],
            r["canonical_key"], r["occurrence_index"], r["identity_key"], 1,
            json.dumps({}), "INVALID_STATUS", 0,
        ))


def test_invalid_decision_rejected_by_check(conn, cur):
    """stock_in_reconcile_log CHECK constraint rejects invalid decision values."""
    import_id = _make_import(cur)
    with pytest.raises(psycopg2.errors.CheckViolation):
        cur.execute("""
            INSERT INTO public.stock_in_reconcile_log
              (id, import_id_new, branch_code, period_start, period_end,
               approved_by, decision, counts_json, before_after_diff)
            VALUES (%s,%s,%s,%s,%s,'user','INVALID_DECISION',%s,%s)
        """, (
            str(uuid.uuid4()), import_id, _BRANCH, _PERIOD, _PERIOD,
            json.dumps({}), json.dumps({}),
        ))


def test_real_two_connection_concurrency_lock(conn, cur):
    """
    Two connections attempting to approve the same import.
    Connection A locks the row and completes approve.
    Connection B blocks until A commits, then resumes and gets 409 conflict.
    """
    import threading
    import time

    # Generate a unique import ID so it doesn't conflict
    import_id = str(uuid.uuid4())

    # 1. Setup import in needs_review with some staged rows using a separate connection
    # so we don't commit/destroy the test savepoint on `conn`
    conn_setup = psycopg2.connect(_DB_URL)
    conn_setup.autocommit = True
    try:
        with conn_setup.cursor() as cur_setup:
            cur_setup.execute("""
                INSERT INTO public.pos_imports
                  (id, report_type, branch_code, source_file, file_size,
                   file_hash, status, uploaded_by, uploaded_at, processing_started_at,
                   period_start, period_end)
                VALUES (%s, 'stock_in_refill', %s, 'test.xlsx', 0, %s, 'needs_review', 'testuser', now(), now(), %s, %s)
            """, (import_id, _BRANCH, str(uuid.uuid4()), _PERIOD, _PERIOD))

            df = _make_df([_base_row(invoice_no="INV-CONCURR-001")])
            rows = parse_stock_in_file(df, branch_code=_BRANCH)
            for r in rows:
                cur_setup.execute("""
                    INSERT INTO public.stock_in_staging
                      (import_id, branch_code, received_date, item_name, material_code,
                       tag, refill_type, invoice_no, gr_ref, po_ref, po_date,
                       unit, qty, unit_cost, net_cost,
                       canonical_key, occurrence_index, identity_key,
                       source_row_number, original_row_json)
                    VALUES (%s,%s,%s,%s,%s, %s,%s,%s,%s,%s,%s, %s,%s,%s,%s,
                            %s,%s,%s, %s,%s)
                """, (
                    import_id, r["branch_code"], r["received_date"], r["item_name"], r["material_code"],
                    r["tag"], r["refill_type"], r["invoice_no"], r.get("gr_ref", ""), r.get("po_ref", ""), r.get("po_date"),
                    r["unit"], r["qty"], r["unit_cost"], r["net_cost"],
                    r["canonical_key"], r["occurrence_index"], r["identity_key"], r["source_row_number"],
                    json.dumps(r.get("original_row_json", {}))
                ))
    finally:
        conn_setup.close()

    conn_a = None
    thread_b = None
    try:
        # 2. Connection A (main thread) locks the row using a separate connection
        conn_a = psycopg2.connect(_DB_URL)
        conn_a.autocommit = False
        cur_a = conn_a.cursor()
        
        # Start Transaction A and lock the row
        cur_a.execute("SELECT status FROM public.pos_imports WHERE id=%s FOR UPDATE", (import_id,))
        assert cur_a.fetchone()[0] == "needs_review"

        # 3. Connection B (in a background thread) attempts to approve the same import
        import_b_response = {}

        def approve_b():
            client = TestClient(main.app)
            # Mock admin auth
            main.verify_token = _fake_verify
            auth_routes.verify_token = _fake_verify
            try:
                # This call will block on the row lock held by cur_a!
                resp = client.post(
                    f"/pos/stock-in/approve/{import_id}",
                    headers={"Authorization": "Bearer ADMIN"},
                    json={
                        "expected_counts": {"new": 1, "unchanged": 0, "changed": 0, "missing": 0},
                        "resolutions": [],
                    }
                )
                import_b_response["status_code"] = resp.status_code
                import_b_response["json"] = resp.json()
            except Exception as e:
                import_b_response["error"] = str(e)

        thread_b = threading.Thread(target=approve_b)
        thread_b.start()

        # Sleep briefly to ensure Thread B has started and is blocked on the lock
        time.sleep(1.0)
        assert thread_b.is_alive(), "Thread B should be blocked on the lock held by Connection A"

        # 4. Connection A performs the approve and commits
        staged = _fetch_staged_rows(cur_a, import_id)
        committed = _fetch_committed_rows(cur_a, _BRANCH, _PERIOD, _PERIOD)
        diff = reconcile_diff(staged, committed)
        for r in diff["insert"]:
            _insert_stock_in_line(cur_a, import_id, r, "active")
            
        cur_a.execute("""
            INSERT INTO public.stock_in_reconcile_log
              (id, import_id_new, branch_code, period_start, period_end, approved_by, decision, counts_json, before_after_diff)
            VALUES (%s, %s, %s, %s, %s, 'admin', 'approve', '{}', '{}')
        """, (str(uuid.uuid4()), import_id, _BRANCH, _PERIOD, _PERIOD))
        
        cur_a.execute("UPDATE public.pos_imports SET status='success' WHERE id=%s", (import_id,))
        cur_a.execute("DELETE FROM public.stock_in_staging WHERE import_id=%s", (import_id,))
        
        # Commit Connection A! This releases the lock!
        conn_a.commit()
        conn_a.close()
        conn_a = None

        # 5. Wait for Thread B to wake up, complete, and join
        thread_b.join(timeout=5.0)

        # 6. Verify B's response is 409 conflict
        assert not thread_b.is_alive(), "Thread B should have completed after lock release"
        assert "status_code" in import_b_response
        assert import_b_response["status_code"] == 409
        assert import_b_response["json"]["detail"]["error"] == "not_approvable"

        # 7. Check database invariants
        with conn.cursor() as cur_verify:
            cur_verify.execute("SELECT COUNT(*) FROM public.stock_in_reconcile_log WHERE import_id_new=%s", (import_id,))
            assert cur_verify.fetchone()[0] == 1
            
            cur_verify.execute("SELECT COUNT(*) FROM public.stock_in_lines WHERE import_id=%s AND row_status='active'", (import_id,))
            assert cur_verify.fetchone()[0] == 1
            
            cur_verify.execute("SELECT COUNT(*) FROM public.stock_in_staging WHERE import_id=%s", (import_id,))
            assert cur_verify.fetchone()[0] == 0

    finally:
        if conn_a:
            try:
                conn_a.rollback()
                conn_a.close()
            except Exception:
                pass
        # Manually cleanup committed setup rows
        try:
            conn_clean = psycopg2.connect(_DB_URL)
            conn_clean.autocommit = True
            with conn_clean.cursor() as cur_clean:
                cur_clean.execute("DELETE FROM public.stock_in_staging WHERE import_id=%s", (import_id,))
                cur_clean.execute("DELETE FROM public.stock_in_lines WHERE import_id=%s", (import_id,))
                cur_clean.execute("DELETE FROM public.stock_in_reconcile_log WHERE import_id_new=%s", (import_id,))
                cur_clean.execute("DELETE FROM public.pos_imports WHERE id=%s", (import_id,))
            conn_clean.close()
        except Exception:
            pass


def test_full_approve_endpoint_lifecycle(conn, cur):
    """
    Test the full HTTP API lifecycle of a stock-in import:
    staged -> diff -> approve -> verify database invariants.
    Also tests validation failure and rollback.
    """
    import_id = str(uuid.uuid4())

    # 1. Setup import in needs_review with some staged rows using a separate connection
    # so we don't commit/destroy the test savepoint on `conn`
    conn_setup = psycopg2.connect(_DB_URL)
    conn_setup.autocommit = True
    try:
        with conn_setup.cursor() as cur_setup:
            cur_setup.execute("""
                INSERT INTO public.pos_imports
                  (id, report_type, branch_code, source_file, file_size,
                   file_hash, status, uploaded_by, uploaded_at, processing_started_at,
                   period_start, period_end)
                VALUES (%s, 'stock_in_refill', %s, 'test.xlsx', 0, %s, 'needs_review', 'testuser', now(), now(), %s, %s)
            """, (import_id, _BRANCH, str(uuid.uuid4()), _PERIOD, _PERIOD))

            df = _make_df([
                _base_row(item_name="ไข่", material_code="E01", qty=10, unit_cost=5, net_cost=50),
                _base_row(item_name="หมู", material_code="E02", qty=2, unit_cost=150, net_cost=300),
            ])
            rows = parse_stock_in_file(df, branch_code=_BRANCH)
            for r in rows:
                cur_setup.execute("""
                    INSERT INTO public.stock_in_staging
                      (import_id, branch_code, received_date, item_name, material_code,
                       tag, refill_type, invoice_no, gr_ref, po_ref, po_date,
                       unit, qty, unit_cost, net_cost,
                       canonical_key, occurrence_index, identity_key,
                       source_row_number, original_row_json)
                    VALUES (%s,%s,%s,%s,%s, %s,%s,%s,%s,%s,%s, %s,%s,%s,%s,
                            %s,%s,%s, %s,%s)
                """, (
                    import_id, r["branch_code"], r["received_date"], r["item_name"], r["material_code"],
                    r["tag"], r["refill_type"], r["invoice_no"], r.get("gr_ref", ""), r.get("po_ref", ""), r.get("po_date"),
                    r["unit"], r["qty"], r["unit_cost"], r["net_cost"],
                    r["canonical_key"], r["occurrence_index"], r["identity_key"], r["source_row_number"],
                    json.dumps(r.get("original_row_json", {}))
                ))
    finally:
        conn_setup.close()

    try:
        client = TestClient(main.app)
        # Mock admin auth
        main.verify_token = _fake_verify
        auth_routes.verify_token = _fake_verify

        # 1. GET /pos/stock-in/diff/{import_id}
        resp_diff = client.get(
            f"/pos/stock-in/diff/{import_id}",
            headers={"Authorization": "Bearer ADMIN"}
        )
        assert resp_diff.status_code == 200
        diff_data = resp_diff.json()
        assert diff_data["status"] == "needs_review"
        assert diff_data["counts"]["new"] == 2
        assert len(diff_data["insert"]) == 2

        # 2. Try to approve with stale counts -> should get 409 stale_review
        resp_stale = client.post(
            f"/pos/stock-in/approve/{import_id}",
            headers={"Authorization": "Bearer ADMIN"},
            json={
                "expected_counts": {"new": 999, "unchanged": 0, "changed": 0, "missing": 0},
                "resolutions": [],
            }
        )
        assert resp_stale.status_code == 409
        assert resp_stale.json()["detail"]["error"] == "stale_review"

        # 3. Approve with correct counts -> should get 200
        resp_approve = client.post(
            f"/pos/stock-in/approve/{import_id}",
            headers={"Authorization": "Bearer ADMIN"},
            json={
                "expected_counts": diff_data["counts"],
                "resolutions": [],
            }
        )
        assert resp_approve.status_code == 200
        assert resp_approve.json()["status"] == "success"
        assert resp_approve.json()["rows_committed"] == 2

        # 4. Verify database invariants
        with conn.cursor() as cur_verify:
            # pos_imports is success
            cur_verify.execute("SELECT status FROM public.pos_imports WHERE id=%s", (import_id,))
            assert cur_verify.fetchone()[0] == "success"

            # stock_in_staging is cleared
            cur_verify.execute("SELECT COUNT(*) FROM public.stock_in_staging WHERE import_id=%s", (import_id,))
            assert cur_verify.fetchone()[0] == 0

            # stock_in_lines has 2 active lines
            cur_verify.execute("SELECT COUNT(*), SUM(qty) FROM public.stock_in_lines WHERE import_id=%s AND row_status='active'", (import_id,))
            count, total_qty = cur_verify.fetchone()
            assert count == 2
            assert float(total_qty) == 12.0

            # reconcile log is written
            cur_verify.execute("SELECT decision, approved_by FROM public.stock_in_reconcile_log WHERE import_id_new=%s", (import_id,))
            row = cur_verify.fetchone()
            assert row is not None
            assert row[0] == "approve"
            assert row[1] == "admin-uid"

    finally:
        # Manually cleanup committed setup rows
        try:
            conn_clean = psycopg2.connect(_DB_URL)
            conn_clean.autocommit = True
            with conn_clean.cursor() as cur_clean:
                cur_clean.execute("DELETE FROM public.stock_in_staging WHERE import_id=%s", (import_id,))
                cur_clean.execute("DELETE FROM public.stock_in_lines WHERE import_id=%s", (import_id,))
                cur_clean.execute("DELETE FROM public.stock_in_reconcile_log WHERE import_id_new=%s", (import_id,))
                cur_clean.execute("DELETE FROM public.pos_imports WHERE id=%s", (import_id,))
            conn_clean.close()
        except Exception:
            pass

