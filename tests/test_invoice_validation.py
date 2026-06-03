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
