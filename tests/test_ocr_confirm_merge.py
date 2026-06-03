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


# ── Money-path OCR dedup C-v2 verification tests ──

def test_normalize_vendor_name():
    # Sara-am, tone marks, punctuation, spacing normalization
    assert main._normalize_vendor_name("บริษัท ซีพี ออลล์ จำกัด") == "ซีพี ออลล"
    assert main._normalize_vendor_name("บจก. ซีพี ออลล์ (มหาชน)") == "ซีพี ออลล"
    assert main._normalize_vendor_name("แม็คโคร") == "แมคโคร"  # mai-taikhoo removed
    assert main._normalize_vendor_name("แมคโคร") == "แมคโคร"
    assert main._normalize_vendor_name("สำราญ") == "สำราญ"
    assert main._normalize_vendor_name("สําราญ") == "สำราญ"  # nikhahit + sara-aa normalized to sara-am


def test_distinct_vendors_with_substring_colliding_names_do_not_merge():
    # Since brand aliases are dropped, substring brands don't collapse to same name
    assert main._normalize_vendor_name("CP Group") == "cp group"
    assert main._normalize_vendor_name("Other CP") == "other cp"
    
    cand = {"vendor_name": "CP Group", "amount": 1000}
    # Weak invoice number, different normalized vendors -> should not merge
    assert main._should_merge_on_invoice_no(cand, "Other CP", {"amount": 5000}, "1") is False


from unittest.mock import MagicMock, patch

@patch("main.get_supabase")
def test_stored_vendor_name_stays_real(mock_get_supabase):
    mock_sb = MagicMock()
    mock_get_supabase.return_value = mock_sb
    
    mock_table = MagicMock()
    mock_sb.table.return_value = mock_table
    
    mock_table.select.return_value = mock_table
    mock_table.eq.return_value = mock_table
    mock_table.in_.return_value = mock_table
    mock_table.limit.return_value = mock_table
    mock_table.execute.return_value = MagicMock(data=[])
    
    parsed = {
        "vendor_name": "บริษัท ซีพี ออลล์ จำกัด",
        "invoice_no": "INV-12345",
        "amount": 1000,
    }
    
    main._save_invoice(
        parsed=parsed,
        ocr_text="some ocr",
        file_url="http://example.com/file.jpg",
        file_name="file.jpg",
        mime_type="image/jpeg",
    )
    
    # Check that insert receives original extracted name, not normalized "ซีพี ออลล์"
    # mock_table.insert was called twice: once for vendor_bills, once for attachments.
    # The first call is the vendor_bills insert.
    insert_call_args = mock_table.insert.call_args_list[0][0][0]
    assert insert_call_args["vendor_name"] == "บริษัท ซีพี ออลล์ จำกัด"
    assert insert_call_args["invoice_no"] == "INV-12345"


def test_genuine_multipage_merges_without_losing_pages():
    # Drifted vendor name with strong invoice_no and missing details should merge
    cand = {"vendor_name": "บริษัท ซีพี ออลล์ จำกัด", "amount": 1000, "bill_date": "2026-01-01"}
    parsed = {"amount": None, "bill_date": None}
    assert main._should_merge_on_invoice_no(cand, "ซีพี ออลล์", parsed, "INV-123456") is True


@patch("main.get_supabase")
def test_reupload_of_same_invoice_no_after_confirm_does_not_duplicate(mock_get_supabase):
    mock_sb = MagicMock()
    mock_get_supabase.return_value = mock_sb
    
    mock_table = MagicMock()
    mock_sb.table.return_value = mock_table
    
    mock_table.select.return_value = mock_table
    mock_table.eq.return_value = mock_table
    mock_table.in_.return_value = mock_table
    mock_table.limit.return_value = mock_table
    
    existing_bill = {
        "id": "existing-uuid-123",
        "vendor_name": "บริษัท ซีพี ออลล์ จำกัด",
        "invoice_no": "INV-123",
        "amount": 1000,
        "review_status": "confirmed",
        "batch_id": "batch-123",
    }
    
    mock_table.execute.side_effect = [
        MagicMock(data=[existing_bill]),  # exact lookup
        MagicMock(data=[{"page_no": 1, "file_name": "scan-p1.png"}]),  # attachments fetch
        MagicMock(data=[{
            "product_name": "item1",
            "quantity": 1,
            "unit_price": 1000,
            "amount": 1000,
            "source_page": 1,
        }]),  # items fetch
    ]
    
    parsed = {
        "vendor_name": "บริษัท ซีพี ออลล์ จำกัด",
        "invoice_no": "INV-123",
        "amount": 1000,
        "items": [{"product_name": "item1", "quantity": 1, "unit_price": 1000, "amount": 1000}],
    }
    
    invoice_id, batch_id, page_no, merged = main._save_invoice(
        parsed=parsed,
        ocr_text="some ocr",
        file_url="http://example.com/file.jpg",
        file_name="scan-p1.png",
        mime_type="image/png",
    )
    
    # Verify that it matched and skipped inserts
    assert invoice_id == "existing-uuid-123"
    assert page_no == 1
    assert merged is True
    assert mock_table.insert.call_count == 0


def test_diacritic_sara_am_variant_merges():
    # E.g. "แม็คโคร" vs "แมคโคร"
    cand = {"vendor_name": "แม็คโคร", "amount": 1000}
    parsed = {"amount": 1000, "vendor_name": "แมคโคร"}
    assert main._should_merge_on_invoice_no(cand, "แมคโคร", parsed, "INV-001") is True
    
    # E.g. sara-am variant (nikhahit + sara-aa vs sara-am)
    cand2 = {"vendor_name": "สําราญ", "amount": 1000}
    parsed2 = {"amount": 1000, "vendor_name": "สำราญ"}
    assert main._should_merge_on_invoice_no(cand2, "สำราญ", parsed2, "INV-002") is True
