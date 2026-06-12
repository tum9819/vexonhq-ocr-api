"""
M1 — FoodStory Stock-in Importer: pure-function unit tests.
No DB, no network, no filesystem — pure logic only.

Test groups:
  A. canonical_key computation
  B. identity_key computation
  C. occurrence_index assignment (multiset, order-independent)
  D. parse_row — date, nan-code, blank costs, bad-date rejection
  E. parse_stock_in_file — whole-file atomicity
  F. reconcile_diff — new / skip / needs_review / missing_from_reexport
  G. STOCK_IN_SIGNATURE / signature detection
"""

from __future__ import annotations

import pandas as pd
import pytest
from collections import Counter
from datetime import date

from stock_in_import import (
    STOCK_IN_SIGNATURE,
    assign_occurrence_indices,
    compute_canonical_key,
    compute_identity_key,
    parse_row,
    parse_stock_in_file,
    reconcile_diff,
)


# ── helpers ─────────────────────────────────────────────────────────────────

def _row(**kwargs) -> dict:
    """Minimal valid parsed-row dict (as returned by parse_row)."""
    base = dict(
        branch_code="ทวีวัฒนา",
        received_date=date(2026, 5, 1),
        item_name="เบียร์สิงห์",
        material_code="A14",
        tag="เครื่องดื่ม",
        refill_type="สั่งซื้อ",
        invoice_no="",
        gr_ref="",
        po_ref="",
        po_date=None,
        unit="",
        qty=12.0,
        unit_cost=840.0,
        net_cost=10080.0,
        source_row_number=1,
        original_row_json={},
    )
    base.update(kwargs)
    return base


def _raw(**kwargs) -> dict:
    """Raw dict as it comes from a pandas row before parse_row."""
    base = {
        "วันที่": "01/05/2026",
        "ชื่อ": "เบียร์สิงห์",
        "รหัสวัตถุดิบ": "A14",
        "ป้ายกำกับ": "เครื่องดื่ม",
        "ประเภทการเติมวัตถุดิบ": "สั่งซื้อ",
        "เติมสินค้า": 12,
        "ค่าใช้จ่ายต่อหน่วย": 840.0,
        "ค่าใช้จ่ายสุทธิ": 10080.0,
        "สาขา": "ทวีวัฒนา",
    }
    base.update(kwargs)
    return base


def _make_df(rows: list[dict]) -> pd.DataFrame:
    """Build a minimal valid FoodStory stock-in DataFrame."""
    return pd.DataFrame(rows)


def _with_keys(row: dict) -> dict:
    """Return row with canonical_key, identity_key, occurrence_index pre-computed."""
    r = dict(row)
    r["canonical_key"] = compute_canonical_key(r)
    r["identity_key"] = compute_identity_key(r)
    r["occurrence_index"] = 0
    return r


# ── A. canonical_key ─────────────────────────────────────────────────────────

def test_canonical_key_is_deterministic():
    row = _row()
    assert compute_canonical_key(row) == compute_canonical_key(row)


def test_canonical_key_nan_material_code_equals_empty():
    row_nan = _row(material_code="nan")
    row_empty = _row(material_code="")
    assert compute_canonical_key(row_nan) == compute_canonical_key(row_empty)


def test_canonical_key_none_material_code_equals_empty():
    row_none = _row(material_code=None)
    row_empty = _row(material_code="")
    assert compute_canonical_key(row_none) == compute_canonical_key(row_empty)


def test_canonical_key_different_qty_gives_different_key():
    row12 = _row(qty=12.0, net_cost=10080.0)
    row24 = _row(qty=24.0, net_cost=20160.0)
    assert compute_canonical_key(row12) != compute_canonical_key(row24)


def test_canonical_key_different_items_give_different_keys():
    row_singha = _row(item_name="เบียร์สิงห์", material_code="A14")
    row_chang = _row(item_name="เบียร์ช้าง", material_code="A15")
    assert compute_canonical_key(row_singha) != compute_canonical_key(row_chang)


def test_canonical_key_different_dates_give_different_keys():
    row_may = _row(received_date=date(2026, 5, 1))
    row_jun = _row(received_date=date(2026, 6, 1))
    assert compute_canonical_key(row_may) != compute_canonical_key(row_jun)


def test_canonical_key_invoice_no_is_discriminator():
    row_inv1 = _row(invoice_no="INV-001")
    row_inv2 = _row(invoice_no="INV-002")
    assert compute_canonical_key(row_inv1) != compute_canonical_key(row_inv2)


# ── B. identity_key ──────────────────────────────────────────────────────────

def test_identity_key_ignores_measures_qty():
    row12 = _row(qty=12.0, unit_cost=840.0, net_cost=10080.0)
    row24 = _row(qty=24.0, unit_cost=840.0, net_cost=20160.0)
    assert compute_identity_key(row12) == compute_identity_key(row24)


def test_identity_key_ignores_measures_unit_cost():
    row_a = _row(qty=12.0, unit_cost=840.0, net_cost=10080.0)
    row_b = _row(qty=12.0, unit_cost=900.0, net_cost=10800.0)
    assert compute_identity_key(row_a) == compute_identity_key(row_b)


def test_identity_key_differs_for_different_items():
    row_singha = _row(item_name="เบียร์สิงห์", material_code="A14")
    row_chang = _row(item_name="เบียร์ช้าง", material_code="A15")
    assert compute_identity_key(row_singha) != compute_identity_key(row_chang)


def test_canonical_and_identity_differ_for_same_row():
    row = _row()
    assert compute_canonical_key(row) != compute_identity_key(row)


def test_identity_key_same_canonical_implies_same_identity():
    row1 = _row()
    row2 = _row()
    # Rows with same canonical_key must have same identity_key (subset of canonical fields)
    assert compute_canonical_key(row1) == compute_canonical_key(row2)
    assert compute_identity_key(row1) == compute_identity_key(row2)


# ── C. occurrence_index ──────────────────────────────────────────────────────

def test_occurrence_index_single_row():
    rows = [_row()]
    result = assign_occurrence_indices(rows)
    assert result[0]["occurrence_index"] == 0


def test_occurrence_index_two_distinct_rows():
    rows = [_row(item_name="เบียร์สิงห์"), _row(item_name="เบียร์ช้าง")]
    result = assign_occurrence_indices(rows)
    # Different canonical_keys → each gets occurrence_index 0
    assert result[0]["occurrence_index"] == 0
    assert result[1]["occurrence_index"] == 0


def test_occurrence_index_two_identical_rows():
    row_a = _row()
    row_b = _row()
    result = assign_occurrence_indices([row_a, row_b])
    indices = [r["occurrence_index"] for r in result]
    assert sorted(indices) == [0, 1]


def test_occurrence_index_multiset_reorder_independent():
    """Re-export that reorders identical rows must yield same (canonical, index) multiset."""
    rows_a = [
        _row(item_name="A", qty=10.0, net_cost=1000.0),
        _row(item_name="A", qty=10.0, net_cost=1000.0),  # duplicate
        _row(item_name="B", qty=5.0, net_cost=500.0),
    ]
    rows_b = [
        _row(item_name="B", qty=5.0, net_cost=500.0),
        _row(item_name="A", qty=10.0, net_cost=1000.0),
        _row(item_name="A", qty=10.0, net_cost=1000.0),
    ]
    result_a = assign_occurrence_indices(list(rows_a))
    result_b = assign_occurrence_indices(list(rows_b))

    multiset_a = Counter((r["canonical_key"], r["occurrence_index"]) for r in result_a)
    multiset_b = Counter((r["canonical_key"], r["occurrence_index"]) for r in result_b)
    assert multiset_a == multiset_b


def test_occurrence_index_keys_set_on_output():
    rows = [_row()]
    result = assign_occurrence_indices(rows)
    assert "canonical_key" in result[0]
    assert "identity_key" in result[0]
    assert "occurrence_index" in result[0]


# ── D. parse_row ─────────────────────────────────────────────────────────────

def test_parse_row_date_dd_mm_yyyy():
    parsed = parse_row(_raw(**{"วันที่": "05/05/2026"}), row_number=1, branch_code="ทวีวัฒนา")
    assert parsed["received_date"] == date(2026, 5, 5)


def test_parse_row_material_code_nan_to_none():
    parsed = parse_row(_raw(**{"รหัสวัตถุดิบ": "nan"}), row_number=1, branch_code="ทวีวัฒนา")
    assert parsed["material_code"] is None


def test_parse_row_material_code_empty_to_none():
    parsed = parse_row(_raw(**{"รหัสวัตถุดิบ": ""}), row_number=1, branch_code="ทวีวัฒนา")
    assert parsed["material_code"] is None


def test_parse_row_blank_unit_cost_to_zero():
    parsed = parse_row(_raw(**{"ค่าใช้จ่ายต่อหน่วย": None}), row_number=1, branch_code="ทวีวัฒนา")
    assert parsed["unit_cost"] == 0.0


def test_parse_row_blank_net_cost_to_zero():
    parsed = parse_row(_raw(**{"ค่าใช้จ่ายสุทธิ": None}), row_number=1, branch_code="ทวีวัฒนา")
    assert parsed["net_cost"] == 0.0


def test_parse_row_qty_float():
    parsed = parse_row(_raw(**{"เติมสินค้า": 24}), row_number=1, branch_code="ทวีวัฒนา")
    assert parsed["qty"] == 24.0
    assert isinstance(parsed["qty"], float)


def test_parse_row_bad_date_raises_value_error():
    with pytest.raises(ValueError, match="[Rr]ow 5"):
        parse_row(_raw(**{"วันที่": "not_a_date"}), row_number=5, branch_code="ทวีวัฒนา")


def test_parse_row_source_row_number_recorded():
    parsed = parse_row(_raw(), row_number=7, branch_code="ทวีวัฒนา")
    assert parsed["source_row_number"] == 7


def test_parse_row_original_row_json_present():
    parsed = parse_row(_raw(), row_number=1, branch_code="ทวีวัฒนา")
    assert "original_row_json" in parsed
    assert isinstance(parsed["original_row_json"], dict)


# ── E. parse_stock_in_file — atomicity ───────────────────────────────────────

def test_parse_stock_in_file_valid_file_returns_rows():
    rows = [_raw(), _raw(**{"ชื่อ": "เบียร์ช้าง", "รหัสวัตถุดิบ": "A15"})]
    df = _make_df(rows)
    result = parse_stock_in_file(df, branch_code="ทวีวัฒนา")
    assert len(result) == 2


def test_parse_stock_in_file_rows_have_occurrence_indices():
    rows = [_raw(), _raw()]  # two identical rows → occurrence_index 0, 1
    df = _make_df(rows)
    result = parse_stock_in_file(df, branch_code="ทวีวัฒนา")
    indices = sorted(r["occurrence_index"] for r in result)
    assert indices == [0, 1]


def test_parse_stock_in_file_whole_file_rejected_on_one_bad_row():
    good = _raw()
    bad = _raw(**{"วันที่": "not_a_date"})
    df = _make_df([good, good, bad, good])
    with pytest.raises(ValueError):
        parse_stock_in_file(df, branch_code="ทวีวัฒนา")


def test_parse_stock_in_file_bad_row_error_contains_row_number():
    bad = _raw(**{"วันที่": "bad_date"})
    df = _make_df([_raw(), bad])
    with pytest.raises(ValueError) as exc_info:
        parse_stock_in_file(df, branch_code="ทวีวัฒนา")
    # Row 2 (0-indexed row 1 → human row 2)
    assert "2" in str(exc_info.value)


# ── F. reconcile_diff ────────────────────────────────────────────────────────

def test_reconcile_diff_empty_staged_and_committed():
    diff = reconcile_diff(staged=[], committed=[])
    assert diff["insert"] == []
    assert diff["skip"] == []
    assert diff["needs_review"] == []
    assert diff["missing_from_reexport"] == []


def test_reconcile_diff_new_row_is_insert():
    staged = [_with_keys(_row())]
    diff = reconcile_diff(staged=staged, committed=[])
    assert len(diff["insert"]) == 1
    assert diff["skip"] == []


def test_reconcile_diff_unchanged_row_is_skip():
    row = _with_keys(_row())
    diff = reconcile_diff(staged=[row], committed=[row])
    assert len(diff["skip"]) == 1
    assert diff["insert"] == []


def test_reconcile_diff_edited_measures_is_needs_review():
    committed = _with_keys(_row(qty=12.0, unit_cost=840.0, net_cost=10080.0))
    # Same identity (same item/date/branch/refs) but different qty → different canonical_key
    staged = _with_keys(_row(qty=24.0, unit_cost=840.0, net_cost=20160.0))
    # Force identity_key to match committed but canonical_key to differ
    staged["identity_key"] = committed["identity_key"]
    staged["canonical_key"] = "different_canonical_key"
    diff = reconcile_diff(staged=[staged], committed=[committed])
    assert len(diff["needs_review"]) == 1
    assert diff["insert"] == []


def test_reconcile_diff_missing_from_reexport_is_flagged():
    committed = _with_keys(_row())
    diff = reconcile_diff(staged=[], committed=[committed])
    assert len(diff["missing_from_reexport"]) == 1
    assert diff["insert"] == []


def test_reconcile_diff_missing_not_auto_deleted():
    """missing_from_reexport rows are flagged but NOT in insert (no auto-write)."""
    committed = _with_keys(_row())
    diff = reconcile_diff(staged=[], committed=[committed])
    assert len(diff["missing_from_reexport"]) == 1
    # The committed row is in missing, NOT in skip or insert
    assert len(diff["skip"]) == 0
    assert len(diff["insert"]) == 0


def test_reconcile_diff_two_identical_rows_both_present():
    row = _row()
    s0 = _with_keys(dict(row, source_row_number=1))
    s1 = _with_keys(dict(row, source_row_number=2))
    s0["occurrence_index"] = 0
    s1["occurrence_index"] = 1
    c0 = dict(s0)
    c1 = dict(s1)
    diff = reconcile_diff(staged=[s0, s1], committed=[c0, c1])
    assert len(diff["skip"]) == 2
    assert diff["insert"] == []
    assert diff["missing_from_reexport"] == []


def test_reconcile_diff_two_identical_committed_one_staged_missing():
    """Committed has 2 identical rows; re-export only has 1 → 1 missing."""
    row = _row()
    s0 = _with_keys(dict(row, source_row_number=1))
    s0["occurrence_index"] = 0
    c0 = dict(s0)
    c1 = _with_keys(dict(row, source_row_number=2))
    c1["occurrence_index"] = 1
    diff = reconcile_diff(staged=[s0], committed=[c0, c1])
    assert len(diff["skip"]) == 1
    assert len(diff["missing_from_reexport"]) == 1
    assert diff["insert"] == []


def test_reconcile_diff_changed_row_not_missing_from_reexport():
    """A committed row whose identity appears in staged (changed) must NOT be missing."""
    committed = _with_keys(_row(qty=12.0, net_cost=10080.0))
    staged = _with_keys(_row(qty=24.0, net_cost=20160.0))  # different qty
    # Force identity match (same logical line, different measures)
    staged["identity_key"] = committed["identity_key"]
    staged["canonical_key"] = "new_canonical_key"
    diff = reconcile_diff(staged=[staged], committed=[committed])
    # staged row → needs_review; committed row → NOT in missing
    assert len(diff["needs_review"]) == 1
    assert len(diff["missing_from_reexport"]) == 0


# ── G. signature ─────────────────────────────────────────────────────────────

def test_stock_in_signature_has_required_columns():
    required = [
        "วันที่", "ชื่อ", "รหัสวัตถุดิบ", "ป้ายกำกับ",
        "ประเภทการเติมวัตถุดิบ", "เติมสินค้า", "ค่าใช้จ่ายต่อหน่วย",
    ]
    for col in required:
        assert col in STOCK_IN_SIGNATURE, f"Missing required column in STOCK_IN_SIGNATURE: {col}"


def test_signature_detection_stock_in_refill():
    from pos_import import detect_report_type
    headers = [
        "วันที่", "ชื่อ", "รหัสวัตถุดิบ", "ป้ายกำกับ",
        "ประเภทการเติมวัตถุดิบ", "เติมสินค้า", "ค่าใช้จ่ายต่อหน่วย",
        "ค่าใช้จ่ายสุทธิ", "สาขา",
    ]
    assert detect_report_type(headers) == "stock_in_refill"


def test_signature_does_not_match_inventory():
    from pos_import import detect_report_type
    inventory_headers = [
        "ชื่อ", "รหัสวัตถุดิบ", "ป้ายกำกับ",
        "จำนวนของในสต็อก", "จำนวนสูงสุดของสต็อก",
    ]
    assert detect_report_type(inventory_headers) == "inventory"
    assert detect_report_type(inventory_headers) != "stock_in_refill"


# ── H. Branch Normalization & Deterministic Pairing ─────────────────────────

def test_normalize_branch_code():
    from stock_in_import import normalize_branch_code
    assert normalize_branch_code("ทวีวัฒนา") == "thawi_watthana"
    assert normalize_branch_code("thawi_watthana") == "thawi_watthana"
    assert normalize_branch_code("thawi-watthana") == "thawi_watthana"
    assert normalize_branch_code("THAWIE") == "thawie"


def test_parse_row_branch_checking():
    # Matching branch
    row_ok = parse_row(_raw(สาขา="ทวีวัฒนา"), row_number=1, branch_code="thawi_watthana")
    assert row_ok["branch_code"] == "thawi_watthana"

    # Mismatching branch
    with pytest.raises(ValueError, match="Row 2: branch 'บางแค' does not match import branch 'thawi_watthana'"):
        parse_row(_raw(สาขา="บางแค"), row_number=2, branch_code="thawi_watthana")


def test_reconcile_diff_multiset_one_to_one_pairing():
    c0 = _with_keys(_row(qty=10.0, net_cost=1000.0, source_row_number=1, id="c0-uuid"))
    c1 = _with_keys(_row(qty=20.0, net_cost=2000.0, source_row_number=2, id="c1-uuid"))
    c0["occurrence_index"] = 0
    c1["occurrence_index"] = 1

    s0 = _with_keys(_row(qty=15.0, net_cost=1500.0, source_row_number=10))
    s1 = _with_keys(_row(qty=25.0, net_cost=2500.0, source_row_number=11))
    s0["occurrence_index"] = 0
    s1["occurrence_index"] = 1

    s0["identity_key"] = c0["identity_key"]
    s1["identity_key"] = c1["identity_key"]
    s0["canonical_key"] = "s0-canonical"
    s1["canonical_key"] = "s1-canonical"

    diff = reconcile_diff(staged=[s0, s1], committed=[c0, c1])
    assert len(diff["needs_review"]) == 2
    assert len(diff["skip"]) == 0
    assert len(diff["insert"]) == 0
    assert len(diff["missing_from_reexport"]) == 0

    paired_counterparts = {r["counterpart_id"] for r in diff["needs_review"]}
    assert paired_counterparts == {"c0-uuid", "c1-uuid"}


def test_reconcile_diff_multiset_occurrence_index_gaps():
    c1 = _with_keys(_row(qty=10.0, net_cost=1000.0, source_row_number=2, id="c1-uuid"))
    c1["occurrence_index"] = 1

    s0 = _with_keys(_row(qty=10.0, net_cost=1000.0, source_row_number=10))
    s1 = _with_keys(_row(qty=10.0, net_cost=1000.0, source_row_number=11))
    s0["occurrence_index"] = 0
    s1["occurrence_index"] = 1

    s0["canonical_key"] = c1["canonical_key"]
    s1["canonical_key"] = c1["canonical_key"]

    diff = reconcile_diff(staged=[s0, s1], committed=[c1])
    assert len(diff["skip"]) == 1
    assert diff["skip"][0]["source_row_number"] == 11

    assert len(diff["insert"]) == 1
    assert diff["insert"][0]["source_row_number"] == 10

    assert len(diff["missing_from_reexport"]) == 0
    assert len(diff["needs_review"]) == 0


def test_reconcile_diff_ordering_is_stable():
    # Set up some rows with different identity keys and canonical keys
    # Identity Group 1
    c1 = _with_keys(_row(item_name="ไข่", qty=10.0, net_cost=100.0, source_row_number=1, id="c1-id"))
    c1["occurrence_index"] = 0
    s1 = _with_keys(_row(item_name="ไข่", qty=12.0, net_cost=120.0, source_row_number=10)) # Needs review
    s1["occurrence_index"] = 0
    s1["identity_key"] = c1["identity_key"]
    s1["canonical_key"] = "s1-canonical"
    
    # Identity Group 2
    c2 = _with_keys(_row(item_name="หมู", qty=20.0, net_cost=200.0, source_row_number=2, id="c2-id"))
    c2["occurrence_index"] = 0
    s2 = _with_keys(_row(item_name="หมู", qty=20.0, net_cost=200.0, source_row_number=11)) # Skip (match)
    s2["occurrence_index"] = 0
    s2["identity_key"] = c2["identity_key"]
    s2["canonical_key"] = c2["canonical_key"]

    # Identity Group 3 (Insert)
    s3 = _with_keys(_row(item_name="ผัก", qty=30.0, net_cost=300.0, source_row_number=12))
    s3["occurrence_index"] = 0

    # Identity Group 4 (Missing)
    c4 = _with_keys(_row(item_name="ปลา", qty=40.0, net_cost=400.0, source_row_number=4, id="c4-id"))
    c4["occurrence_index"] = 0

    # Run reconcile_diff with different permutations of inputs
    import itertools
    staged_permutations = list(itertools.permutations([s1, s2, s3]))
    committed_permutations = list(itertools.permutations([c1, c2, c4]))

    # Call with first permutation to get the baseline
    baseline_diff = reconcile_diff(list(staged_permutations[0]), list(committed_permutations[0]))

    for st in staged_permutations:
        for co in committed_permutations:
            diff = reconcile_diff(list(st), list(co))
            
            # Verify ordering in lists is completely identical to baseline
            assert [r["source_row_number"] for r in diff["skip"]] == [r["source_row_number"] for r in baseline_diff["skip"]]
            assert [r["source_row_number"] for r in diff["insert"]] == [r["source_row_number"] for r in baseline_diff["insert"]]
            assert [r["source_row_number"] for r in diff["needs_review"]] == [r["source_row_number"] for r in baseline_diff["needs_review"]]
            assert [r["id"] for r in diff["missing_from_reexport"]] == [r["id"] for r in baseline_diff["missing_from_reexport"]]
            
            # Verify counterpart_id mapping is exactly identical
            for r_baseline, r_curr in zip(baseline_diff["needs_review"], diff["needs_review"]):
                assert r_baseline["counterpart_id"] == r_curr["counterpart_id"]
