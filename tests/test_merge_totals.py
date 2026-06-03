"""OPS-10: offline unit tests for multi-page merge helpers
(main._norm_text, _is_real_item, _compute_backfill). Guards the double-count /
wrong-page-clobber class of money bugs.

Run: pytest tests/test_merge_totals.py -v
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


def test_norm_text_collapses_internal_whitespace():
    # the v3.5 double-bill bug: "บริษัท  ซีพี" vs "บริษัท ซีพี" must normalize equal
    assert main._norm_text("บริษัท  ซีพี") == main._norm_text("บริษัท ซีพี")
    assert main._norm_text("  x  ") == "x"


def test_norm_text_empty_is_none():
    assert main._norm_text("") is None
    assert main._norm_text(None) is None
    assert main._norm_text("   ") is None


def test_is_real_item_filters_noise():
    assert main._is_real_item({"product_name": "ข้าวผัดกระเพรา"}) is True
    assert main._is_real_item({"product_name": "1"}) is False     # pure-digit = tax code
    assert main._is_real_item({"product_name": ""}) is False
    assert main._is_real_item({"product_name": "-"}) is False
    assert main._is_real_item({}) is False


def test_compute_backfill_never_overwrites_present_value():
    # the anti-double-count rule: an existing total must NOT be clobbered by a page
    existing = {"amount": 1000, "vat": 70}
    parsed = {"amount": 2000, "vat": 140}
    out = main._compute_backfill(existing, parsed)
    assert "amount" not in out and "vat" not in out


def test_compute_backfill_fills_only_missing():
    existing = {"amount": None, "vat": ""}
    parsed = {"amount": 2000, "vat": 140, "bill_date": "2026-01-01"}
    out = main._compute_backfill(existing, parsed)
    assert out["amount"] == 2000
    assert out["vat"] == 140
    assert out["bill_date"] == "2026-01-01"


def test_compute_backfill_skips_when_new_page_also_missing():
    assert main._compute_backfill({"amount": None}, {"amount": None}) == {}
