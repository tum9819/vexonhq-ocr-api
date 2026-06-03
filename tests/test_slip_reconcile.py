"""OPS-10: offline unit tests for slip-reconcile pure mappings
(slip_routes._source_for_category, _normalize_lender). A mis-mapped source is a
P&L category error; lender normalization keeps loan repay/borrow on one key.

Run: pytest tests/test_slip_reconcile.py -v
"""
import os

os.environ.setdefault("DATABASE_URL", "postgresql://u:p@localhost:5432/d")
os.environ.setdefault("JWT_SECRET", "testsecret")
os.environ.setdefault("OPENAI_API_KEY", "x")
os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "x")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "x")
os.environ.setdefault("SUPABASE_ANON_KEY", "x")

import slip_routes as sr  # noqa: E402


def test_source_for_category_known_maps():
    assert sr._source_for_category("musician_fee") == "payroll_expense"
    assert sr._source_for_category("staff_salary") == "payroll_expense"
    assert sr._source_for_category("rent") == "rent_expense"
    assert sr._source_for_category("utility") == "utility_expense"
    assert sr._source_for_category("food_raw") == "vendor_purchase"
    assert sr._source_for_category("bank_fee") == "bank_fee"
    assert sr._source_for_category("loan_repayment") == "loan_repayment"


def test_source_for_category_unknown_falls_back():
    assert sr._source_for_category("nonexistent_code") == "other_expense"
    assert sr._source_for_category(None) == "other_expense"
    assert sr._source_for_category("") == "other_expense"


def test_normalize_lender_strips_title_and_padding():
    assert sr._normalize_lender("น.ส. นุศรา ปรางม++") == "นุศรา"
    assert sr._normalize_lender("นาย สมชาย") == "สมชาย"
    assert sr._normalize_lender("นางสาว มาลี") == "มาลี"
    assert sr._normalize_lender("นุศรา") == "นุศรา"


def test_normalize_lender_blank_is_none():
    assert sr._normalize_lender("") is None
    assert sr._normalize_lender(None) is None
    assert sr._normalize_lender("  ++  ") is None
