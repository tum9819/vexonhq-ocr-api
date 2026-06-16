"""OPS-10: offline unit tests for invoice validation math
(main._validate_invoice + _confidence_warnings). Pure — guards money-path
regressions (VAT consistency, missing total, high-value, confidence robustness).

Run: pytest tests/test_invoice_validation.py -v
"""
import os

os.environ.setdefault("DATABASE_URL", "postgresql://u:p@localhost:5432/d")
os.environ.setdefault("JWT_SECRET", "testsecret")
os.environ.setdefault("OPENAI_API_KEY", "x")
os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "x")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "x")
os.environ.setdefault("SUPABASE_ANON_KEY", "x")

import main  # noqa: E402


def _codes(warnings):
    return {w.get("code") for w in warnings}


def test_vat_consistent_no_mismatch():
    w = main._validate_invoice({"vendor_name": "V", "invoice_no": "INV-1",
                                "subtotal": 1000, "vat": 70, "amount": 1070})
    assert "VAT_MISMATCH" not in _codes(w)


def test_vat_mismatch_beyond_tolerance():
    w = main._validate_invoice({"vendor_name": "V", "invoice_no": "INV-1",
                                "subtotal": 1000, "vat": 70, "amount": 1100})
    assert "VAT_MISMATCH" in _codes(w)


def test_vat_within_005_tolerance():
    # 1000 + 70 = 1070; amount 1070.04 (0.04 gap) <= 0.05 -> no mismatch
    w = main._validate_invoice({"vendor_name": "V", "invoice_no": "INV-1",
                                "subtotal": 1000, "vat": 70, "amount": 1070.04})
    assert "VAT_MISMATCH" not in _codes(w)


def test_missing_total_is_error():
    w = main._validate_invoice({"vendor_name": "V", "invoice_no": "INV-1", "amount": None})
    assert any(x["code"] == "MISSING_TOTAL" and x["severity"] == "error" for x in w)


def test_high_value_info():
    w = main._validate_invoice({"vendor_name": "V", "invoice_no": "INV-1", "amount": 50000})
    hv = [x for x in w if x["code"] == "HIGH_VALUE"]
    assert hv and hv[0]["severity"] == "info"


def test_missing_vendor_and_invoice_are_warn():
    w = main._validate_invoice({"amount": 500})
    assert "MISSING_VENDOR" in _codes(w)
    assert "MISSING_INVOICE_NO" in _codes(w)


def test_nonnumeric_amounts_do_not_crash():
    w = main._validate_invoice({"vendor_name": "V", "invoice_no": "INV-1",
                                "subtotal": "abc", "vat": "x", "amount": 100})
    assert isinstance(w, list)


def test_confidence_warnings_tolerate_garbage():
    assert isinstance(main._confidence_warnings({"field_confidence": "not-a-dict"}), list)
    assert isinstance(main._confidence_warnings({}), list)
    assert isinstance(main._confidence_warnings({"field_confidence": {"amount": "bad"}}), list)


# ── ITEMS_SUBTOTAL_MISMATCH ──────────────────────────────────────────────────

def test_items_match_subtotal_no_warning():
    w = main._validate_invoice({
        "vendor_name": "V", "invoice_no": "INV-1",
        "subtotal": 268.96, "vat": 18.83, "amount": 287.79,
        "items": [{"amount": 134.48}, {"amount": 134.48}],
    })
    assert "ITEMS_SUBTOTAL_MISMATCH" not in _codes(w)


def test_items_mismatch_subtotal_triggers_warn():
    # screenshot case: items total 336.24 vs subtotal 268.96
    w = main._validate_invoice({
        "vendor_name": "V", "invoice_no": "INV-1",
        "subtotal": 268.96, "vat": 18.83, "amount": 287.79,
        "items": [{"amount": 168.12}, {"amount": 168.12}],
    })
    assert "ITEMS_SUBTOTAL_MISMATCH" in _codes(w)
    match = next(x for x in w if x["code"] == "ITEMS_SUBTOTAL_MISMATCH")
    assert match["severity"] == "warn"
    assert match["field"] == "subtotal"
    assert "336.24" in match["message"] and "268.96" in match["message"]


def test_items_bad_amount_string_skipped_not_crash():
    # one malformed item amount should be skipped; valid ones still checked
    w = main._validate_invoice({
        "vendor_name": "V", "invoice_no": "INV-1",
        "subtotal": 268.96, "vat": 18.83, "amount": 287.79,
        "items": [{"amount": 168.12}, {"amount": "bad"}, {"amount": 168.12}],
    })
    assert "ITEMS_SUBTOTAL_MISMATCH" in _codes(w)


def test_items_within_1_baht_tolerance():
    # 268.96 + 0.99 gap — should NOT fire
    w = main._validate_invoice({
        "vendor_name": "V", "invoice_no": "INV-1",
        "subtotal": 268.96, "vat": 18.83, "amount": 287.79,
        "items": [{"amount": 269.95}],
    })
    assert "ITEMS_SUBTOTAL_MISMATCH" not in _codes(w)


def test_items_mismatch_no_subtotal_no_crash():
    # subtotal=None → skip check entirely
    w = main._validate_invoice({
        "vendor_name": "V", "invoice_no": "INV-1",
        "subtotal": None, "amount": 287.79,
        "items": [{"amount": 168.12}, {"amount": 168.12}],
    })
    assert "ITEMS_SUBTOTAL_MISMATCH" not in _codes(w)


def test_items_null_amounts_ignored():
    # items with null amount should not contribute to sum
    w = main._validate_invoice({
        "vendor_name": "V", "invoice_no": "INV-1",
        "subtotal": 268.96, "vat": 18.83, "amount": 287.79,
        "items": [{"amount": 268.96}, {"amount": None}],
    })
    assert "ITEMS_SUBTOTAL_MISMATCH" not in _codes(w)


def test_no_items_no_warning():
    # no items → skip check (can't blame OCR for missing data)
    w = main._validate_invoice({
        "vendor_name": "V", "invoice_no": "INV-1",
        "subtotal": 268.96, "vat": 18.83, "amount": 287.79,
        "items": [],
    })
    assert "ITEMS_SUBTOTAL_MISMATCH" not in _codes(w)
