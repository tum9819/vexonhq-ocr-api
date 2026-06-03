"""Offline unit tests for OCR-1 (confirm-gate premise) + OCR-2 (cross-vendor
merge guard, multi-page-safe). Pure logic — no DB / network / real key.

Run: pytest tests/test_ocr_confirm_merge.py -v
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


# ── OCR-1: the confirm gate blocks on error-severity (MISSING_TOTAL) ──

def test_missing_total_is_error_severity():
    w = main._validate_invoice({"vendor_name": "X", "invoice_no": "INV-1", "amount": None})
    assert any(x.get("code") == "MISSING_TOTAL" and x.get("severity") == "error" for x in w)


def test_complete_bill_has_no_error_severity():
    w = main._validate_invoice({
        "vendor_name": "X", "invoice_no": "INV-1",
        "merchant_tax_id": "1234567890123",
        "subtotal": 1000, "vat": 70, "amount": 1070,
    })
    assert [x for x in w if x.get("severity") == "error"] == []


# ── OCR-2: weak vs strong invoice-number classification ──

def test_weak_invoice_numbers_detected():
    for weak in ("1", "001", "12", "123", "12345", "  7  ", "65432"):
        assert main._is_weak_invoice_no(weak) is True, weak


def test_strong_invoice_numbers_allowed():
    for strong in ("INV-2026-001", "A12345", "2026/0001", "987654"):
        assert main._is_weak_invoice_no(strong) is False, strong


# ── OCR-2: _should_merge_on_invoice_no(cand, vendor_name, parsed, invoice_no) ──

def test_same_vendor_always_merges():
    cand = {"vendor_name": "Makro", "amount": 999, "bill_date": "2026-01-01"}
    # different amount + date but same vendor -> same bill
    assert main._should_merge_on_invoice_no(cand, "Makro", {"amount": 1, "bill_date": "2026-09-09"}, "1") is True


def test_amount_match_merges_even_weak_number():
    cand = {"vendor_name": "A", "amount": 1000.0}
    assert main._should_merge_on_invoice_no(cand, "B", {"amount": 1005.0}, "1") is True  # 0.5% apart


def test_weak_number_diff_vendor_no_corroboration_splits():
    """Audit's core fix: invoice_no='1' across two different vendors must NOT fuse."""
    cand = {"vendor_name": "Vendor A", "amount": 1000.0, "bill_date": "2026-01-01"}
    assert main._should_merge_on_invoice_no(cand, "Vendor B", {"amount": 5000.0, "bill_date": "2026-02-02"}, "1") is False


def test_weak_number_same_date_alone_does_not_merge():
    """Same calendar day is too weak to fuse two different vendors on a weak no."""
    cand = {"vendor_name": "Vendor A", "amount": 1000.0, "bill_date": "2026-01-01"}
    assert main._should_merge_on_invoice_no(cand, "Vendor B", {"amount": 5000.0, "bill_date": "2026-01-01"}, "1") is False


def test_strong_number_multipage_missing_fields_merges():
    """Regression guard (caught in adversarial review): a genuine multi-page
    invoice — strong invoice_no, vendor OCR-drifted, new page missing amount+date
    because they live on another page — MUST still merge, not split."""
    cand = {"vendor_name": "Makro Co", "amount": 1500.0, "bill_date": "2026-01-01"}
    assert main._should_merge_on_invoice_no(cand, "Makr0 Co", {"amount": None, "bill_date": None}, "INV-2026-04521") is True


def test_strong_number_active_conflict_splits():
    """Strong invoice_no but vendors differ AND amounts both present and far
    apart -> clearly a different bill -> do not merge."""
    cand = {"vendor_name": "Vendor A", "amount": 1000.0, "bill_date": "2026-01-01"}
    assert main._should_merge_on_invoice_no(cand, "Vendor B", {"amount": 9000.0, "bill_date": "2026-01-01"}, "987654") is False


def test_strong_number_same_vendor_missing_fields_merges():
    cand = {"vendor_name": "Vendor A", "amount": None}
    assert main._should_merge_on_invoice_no(cand, "Vendor A", {"amount": None}, "INV-1") is True


def test_large_bill_coincidental_near_amount_does_not_merge_weak():
    """Amount band is capped at 500 THB so two ~1M bills 10k apart don't fuse on
    a weak number."""
    cand = {"vendor_name": "Vendor A", "amount": 1_000_000.0}
    assert main._should_merge_on_invoice_no(cand, "Vendor B", {"amount": 1_010_000.0}, "1") is False


def test_comma_amount_string_parsed():
    cand = {"vendor_name": "A", "amount": "1,070.00"}
    assert main._should_merge_on_invoice_no(cand, "B", {"amount": "1,070"}, "1") is True


def test_zero_amount_diff_vendor_weak_does_not_merge():
    """amount=0 must count as 'unknown' (not a match) so two different vendors
    both carrying 0 on a weak invoice_no do not fuse."""
    cand = {"vendor_name": "A", "amount": 0.0}
    assert main._should_merge_on_invoice_no(cand, "B", {"amount": 0.0}, "1") is False


def test_strong_number_vendor_and_date_drift_amount_missing_merges():
    """Regression guard (v2 review): strong invoice_no, vendor OCR-drifted, amount
    missing on the new page, and bill_date OCR-drifted by a digit — this is still
    one multi-page bill and MUST merge (a drifted date is not a split signal)."""
    cand = {"vendor_name": "Makro Co Ltd", "amount": 1500.0, "bill_date": "2026-01-01"}
    parsed = {"amount": None, "bill_date": "2026-07-01"}
    assert main._should_merge_on_invoice_no(cand, "Makr0 Co Ltd", parsed, "INV-2026-04521") is True
