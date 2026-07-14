"""Offline unit tests for the OCR completeness (items-tie) layer, 2026-07-15.

Covers:
  - _items_tie_state: the +/-10% band shared with monthly-by-sku true-cost
  - _validate_invoice bill_level gate: ITEMS_TOTAL_INCOMPLETE fires only on
    merged-bill validation, with error severity (blocks confirm unless forced)
  - _storage_path_from_url: attachment URL -> storage object key

No network, no DB, no OpenAI.

Run: pytest tests/test_items_tie.py -v
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


def _items(*amounts):
    return [{"amount": a} for a in amounts]


# ── _items_tie_state ──────────────────────────────────────────────────────────

def test_tie_ok_exact_match():
    s = main._items_tie_state(100.0, _items(60, 40))
    assert s["ok"] is True
    assert s["ratio"] == 1.0
    assert s["n_items"] == 2


def test_tie_ok_vat_gap_within_band():
    # amount includes 7% VAT over the line sum — inside the 0.90..1.10 band
    s = main._items_tie_state(107.0, _items(100))
    assert s["ok"] is True


def test_tie_fails_out_of_band():
    # The SS 680904726 class: amount 2693.98 vs lines 16.82 (ratio 160x)
    s = main._items_tie_state(2693.98, _items(16.82))
    assert s["ok"] is False
    assert s["ratio"] and s["ratio"] > 100


def test_tie_fails_zero_lines():
    s = main._items_tie_state(20639.98, [])
    assert s["ok"] is False
    assert s["n_items"] == 0


def test_tie_fails_zero_sum_lines():
    # The "Service" class: 4 lines, every amount 0
    s = main._items_tie_state(1700.0, _items(0, 0, 0, 0))
    assert s["ok"] is False


def test_tie_ok_when_no_amount():
    # Missing total is MISSING_TOTAL's job — the tie check must not pile on
    assert main._items_tie_state(None, _items(100))["ok"] is True
    assert main._items_tie_state(0, [])["ok"] is True


def test_tie_ok_when_discount_explains_gap():
    # lines 1000, whole-bill discount 300 -> effective 700 vs amount 700
    s = main._items_tie_state(
        700.0, _items(1000), {"whole_bill_discount_amount": 300}
    )
    assert s["ok"] is True


def test_tie_pct_discount_explains_gap():
    s = main._items_tie_state(
        800.0, _items(1000), {"whole_bill_discount_pct": 20}
    )
    assert s["ok"] is True


def test_tie_tolerates_garbage_amounts():
    s = main._items_tie_state("1,070", [{"amount": "1,000"}, {"amount": None}, "junk"])
    assert s["n_items"] == 1
    assert s["ok"] is True  # 1070/1000 = 1.07 inside band


# ── _validate_invoice bill_level gate ─────────────────────────────────────────

def _codes(warnings):
    return {w["code"] for w in warnings}


def test_gate_fires_only_at_bill_level():
    parsed = {"vendor_name": "X", "invoice_no": "INV-1", "merchant_tax_id": "t",
              "amount": 20639.98, "items": []}
    page_level = main._validate_invoice(parsed)
    bill_level = main._validate_invoice(parsed, bill_level=True)
    assert "ITEMS_TOTAL_INCOMPLETE" not in _codes(page_level)
    assert "ITEMS_TOTAL_INCOMPLETE" in _codes(bill_level)


def test_gate_is_error_severity():
    parsed = {"vendor_name": "X", "invoice_no": "INV-1", "merchant_tax_id": "t",
              "amount": 1000.0, "items": _items(100)}
    w = [x for x in main._validate_invoice(parsed, bill_level=True)
         if x["code"] == "ITEMS_TOTAL_INCOMPLETE"]
    assert len(w) == 1
    assert w[0]["severity"] == "error"


def test_gate_silent_when_bill_ties():
    parsed = {"vendor_name": "X", "invoice_no": "INV-1", "merchant_tax_id": "t",
              "amount": 107.0, "items": _items(50, 50)}
    assert "ITEMS_TOTAL_INCOMPLETE" not in _codes(
        main._validate_invoice(parsed, bill_level=True)
    )


def test_gate_silent_when_no_amount():
    # MISSING_TOTAL (already error severity) covers this case
    parsed = {"vendor_name": "X", "invoice_no": "INV-1", "merchant_tax_id": "t",
              "amount": None, "items": []}
    codes = _codes(main._validate_invoice(parsed, bill_level=True))
    assert "MISSING_TOTAL" in codes
    assert "ITEMS_TOTAL_INCOMPLETE" not in codes


# ── _storage_path_from_url ────────────────────────────────────────────────────

def test_storage_path_public_url():
    url = (f"https://example.supabase.co/storage/v1/object/public/"
           f"{main.SUPABASE_STORAGE_BUCKET}/invoices/2026-07/abc.png")
    assert main._storage_path_from_url(url) == "invoices/2026-07/abc.png"


def test_storage_path_signed_url_strips_query():
    url = (f"https://example.supabase.co/storage/v1/object/sign/"
           f"{main.SUPABASE_STORAGE_BUCKET}/invoices/2026-07/abc.png?token=zzz")
    assert main._storage_path_from_url(url) == "invoices/2026-07/abc.png"


def test_storage_path_rejects_other_bucket_and_none():
    assert main._storage_path_from_url(
        "https://example.supabase.co/storage/v1/object/public/other/x.png"
    ) is None
    assert main._storage_path_from_url(None) is None
    assert main._storage_path_from_url("not a url") is None
