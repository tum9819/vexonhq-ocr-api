"""Unit tests for the audit-package voucher assembly (CODEX-7 / audit-package feature).

Pure-function tests — no DB. Covers: seq ordering, WHT via tax_routes.WHT_RULES,
evidence linking by statement ref_id, and null-evidence fallback.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from export_routes import _assemble_audit_vouchers  # noqa: E402
from tax_routes import WHT_RULES  # noqa: E402


def _row(date, amount, cat, ref, label="x", cp="y", name_th=None):
    return {
        "entry_date": date, "amount": amount, "category_code": cat,
        "category_name_th": name_th or (cat or "ไม่ระบุ"),
        "counterparty": cp, "label": label, "ref_id": ref,
    }


def test_seq_follows_input_order_and_amounts_round():
    rows = [_row("2026-06-01", 100.005, "rent", "a"), _row("2026-06-02", 50, "food_raw", "b")]
    v = _assemble_audit_vouchers(rows, {}, {}, WHT_RULES)
    assert [x["seq"] for x in v] == [1, 2]
    assert v[0]["amount"] == 100.0 or v[0]["amount"] == 100.01  # round to 2dp


def test_wht_musician_3pct_and_rent_5pct_others_none():
    rows = [
        _row("2026-06-05", 2100, "musician_fee", "m1"),
        _row("2026-06-06", 8000, "rent", "r1"),
        _row("2026-06-07", 500, "food_raw", "f1"),
    ]
    v = _assemble_audit_vouchers(rows, {}, {}, WHT_RULES)
    assert v[0]["wht"] == {"rate": 3.0, "amount": 63.0}
    assert v[1]["wht"] == {"rate": 5.0, "amount": 400.0}
    assert v[2]["wht"] is None


def test_evidence_linked_by_ref_id_and_missing_is_none():
    rows = [_row("2026-06-01", 100, "rent", "s1"), _row("2026-06-02", 200, "rent", "s2")]
    slips = {"s1": {"image_url": "http://img/slip1", "ref_no": "R1",
                    "transfer_date": "2026-06-01", "transfer_time": "10:00"}}
    invs = {"s1": {"image_url": "http://img/inv1", "invoice_no": "IV-1", "vendor_name": "V"}}
    v = _assemble_audit_vouchers(rows, slips, invs, WHT_RULES)
    assert v[0]["slip"]["ref_no"] == "R1" and v[0]["invoice"]["invoice_no"] == "IV-1"
    assert v[1]["slip"] is None and v[1]["invoice"] is None


def test_null_ref_id_never_matches_evidence():
    rows = [_row("2026-06-01", 100, None, None)]
    v = _assemble_audit_vouchers(rows, {"None": {"image_url": "boom"}}, {}, WHT_RULES)
    assert v[0]["slip"] is None
    assert v[0]["category_name_th"] == "ไม่ระบุ"
    assert v[0]["wht"] is None


def test_counterparty_falls_back_to_label_when_null():
    """v_daybook_pnl.counterparty is NULL for bank-sourced rows (payroll/rent/
    vendor_purchase); the payee name lives in `label` instead — the printed
    voucher must not show a blank "จ่ายให้" for a real transaction."""
    r = _row("2026-06-01", 600, "musician_fee", "x", label="K PLUS โอนไป SCB X0060 นาย ศาตราวุธ", cp=None)
    v = _assemble_audit_vouchers([r], {}, {}, WHT_RULES)
    assert v[0]["counterparty"] == "K PLUS โอนไป SCB X0060 นาย ศาตราวุธ"


def test_counterparty_uses_real_value_when_present():
    r = _row("2026-06-01", 600, "rent", "x", label="some label", cp="Real Landlord Co.")
    v = _assemble_audit_vouchers([r], {}, {}, WHT_RULES)
    assert v[0]["counterparty"] == "Real Landlord Co."
