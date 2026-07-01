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


def test_is_real_item_filters_makro_vat_summary_label():
    assert main._is_real_item({
        "product_name": "จำนวนชิ้น",
        "quantity": 5.556,
        "unit_price": 684.25,
        "amount": 684.25,
    }) is False


def test_extract_makro_totals_prefers_vat_row_subtotal_semantics():
    text = """
    รวม | | 1,076.77 | 27.48 | 1,104.25
    ราคาสินค้ารวมภาษีมูลค่าเพิ่ม/ TOTAL 1,125.25
    หักส่วนลด/ DISCOUNT 21.00
    จำนวนเงินรวมสุทธิ/ AMOUNT 1,104.25
    ราคาสินค้าที่ต้องชำระ / NET AMOUNT 1,104.25
    """

    assert main._extract_makro_totals_from_text(text) == {
        "subtotal": 1076.77,
        "discount_amount": 21.00,
        "amount": 1104.25,
        "vat": 27.48,
    }


def test_merge_ocr_json_preserves_items_and_adds_summary_fields():
    existing_ocr = {
        "items": [{"product_name": "มันฝรั่งนอก 1 กก.", "amount": 60.25}],
        "subtotal": None,
        "vat": None,
        "amount": None,
        "discount": {
            "line_items_discount_pct": None,
            "whole_bill_discount_amount": None,
            "whole_bill_discount_pct": None,
            "note": None,
        },
    }
    summary_page = {
        "items": [{"product_name": "จำนวนชิ้น", "amount": 684.25}],
        "subtotal": 1076.77,
        "vat": 27.48,
        "amount": 1104.25,
        "discount": {
            "line_items_discount_pct": None,
            "whole_bill_discount_amount": 21.00,
            "whole_bill_discount_pct": None,
            "note": None,
        },
    }

    out = main._merge_ocr_json(existing_ocr, summary_page)

    assert out["items"] == existing_ocr["items"]
    assert out["subtotal"] == 1076.77
    assert out["vat"] == 27.48
    assert out["amount"] == 1104.25
    assert out["discount"]["whole_bill_discount_amount"] == 21.00


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
