"""
GET /invoice/duplicates — suspected-duplicate banner grouping.

Regression (2026-10-03): one Makro purchase arrived as two pending bills —
the full tax invoice ("CP AXTRA PUBLIC COMPANY LIMITED (HEAD)") and the store
receipt ("CP AXTRA ศาลายามาร์เก็ต (3203)"), same amount and date. The old key
(exact vendor name + amount + date) kept them apart, so the banner stayed
silent. The key is now amount + bill_date; vendor names are only displayed.

Offline: get_supabase is replaced with an in-memory fake.
Run: pytest tests/test_invoice_duplicates.py -v
"""
from __future__ import annotations

import os
import types

os.environ.setdefault("DATABASE_URL", "postgresql://u:p@localhost:5432/d")
os.environ.setdefault("OPENAI_API_KEY", "x")

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import main


def _bill(i, vendor, amount, bill_date, created):
    return {
        "id": f"b{i}",
        "vendor_name": vendor,
        "invoice_no": f"INV{i}",
        "bill_date": bill_date,
        "amount": amount,
        "created_at": created,
        "review_status": "pending",
        "payment_status": "unpaid",
    }


class _FakeQuery:
    def __init__(self, rows):
        self._rows = rows

    def select(self, *a, **k):
        return self

    def in_(self, column, values):
        self._rows = [r for r in self._rows if r.get(column) in values]
        return self

    def execute(self):
        return types.SimpleNamespace(data=self._rows)


def _run(monkeypatch, rows):
    fake = types.SimpleNamespace(table=lambda name: _FakeQuery(rows))
    monkeypatch.setattr(main, "get_supabase", lambda: fake)
    return main.invoice_suspected_duplicates()


def test_same_amount_and_date_with_different_vendor_names_is_grouped(monkeypatch):
    rows = [
        _bill(1, "CP AXTRA PUBLIC COMPANY LIMITED (HEAD)", "2082.0", "2026-10-03", "2026-10-03T13:12:45Z"),
        _bill(2, "CP AXTRA ศาลายามาร์เก็ต (3203)", "2082.0", "2026-10-03", "2026-10-03T13:21:46Z"),
    ]
    out = _run(monkeypatch, rows)
    assert out["total_groups"] == 1
    g = out["groups"][0]
    assert g["count"] == 2
    assert g["amount"] == 2082.0
    assert g["bill_date"] == "2026-10-03"
    assert [b["id"] for b in g["bills"]] == ["b1", "b2"]  # oldest first
    assert g["vendor_name"] == "CP AXTRA PUBLIC COMPANY LIMITED (HEAD) / CP AXTRA ศาลายามาร์เก็ต (3203)"


def test_same_vendor_same_amount_same_date_still_grouped_with_one_name(monkeypatch):
    rows = [
        _bill(1, "Singha", 500, "2026-10-01", "2026-10-01T01:00:00Z"),
        _bill(2, " singha ", 500, "2026-10-01", "2026-10-01T02:00:00Z"),
    ]
    out = _run(monkeypatch, rows)
    assert out["total_groups"] == 1
    assert out["groups"][0]["vendor_name"] == "Singha"


def test_different_date_or_amount_is_not_grouped(monkeypatch):
    rows = [
        _bill(1, "Makro", 2082, "2026-10-03", "2026-10-03T01:00:00Z"),
        _bill(2, "Makro", 2082, "2026-10-04", "2026-10-04T01:00:00Z"),
        _bill(3, "Makro", 2083, "2026-10-03", "2026-10-03T02:00:00Z"),
    ]
    assert _run(monkeypatch, rows)["total_groups"] == 0


def test_undated_bills_still_need_the_same_vendor(monkeypatch):
    rows = [
        _bill(1, "Wealimex", 900, None, "2026-10-01T01:00:00Z"),
        _bill(2, "wealimex", 900, None, "2026-10-01T02:00:00Z"),
        _bill(3, "Other Shop", 900, None, "2026-10-01T03:00:00Z"),
    ]
    out = _run(monkeypatch, rows)
    assert out["total_groups"] == 1
    assert [b["id"] for b in out["groups"][0]["bills"]] == ["b1", "b2"]


def test_missing_or_non_positive_amount_is_ignored(monkeypatch):
    rows = [
        _bill(1, "A", None, "2026-10-03", "2026-10-03T01:00:00Z"),
        _bill(2, "B", None, "2026-10-03", "2026-10-03T02:00:00Z"),
        _bill(3, "A", 0, "2026-10-03", "2026-10-03T03:00:00Z"),
        _bill(4, "B", "0", "2026-10-03", "2026-10-03T04:00:00Z"),
    ]
    assert _run(monkeypatch, rows)["total_groups"] == 0


def test_negative_invalid_and_non_finite_amounts_are_ignored(monkeypatch):
    # "Infinity"/"1e309" parse to inf and would make the JSON response 500.
    rows = []
    for i, bad in enumerate(["-5", "abc", "Infinity", "1e309", "NaN", "-Infinity"]):
        rows.append(_bill(2 * i, "A", bad, "2026-10-03", f"2026-10-03T0{i}:00:00Z"))
        rows.append(_bill(2 * i + 1, "B", bad, "2026-10-03", f"2026-10-03T0{i}:30:00Z"))
    out = _run(monkeypatch, rows)
    assert out["total_groups"] == 0
    import json
    json.dumps(out, allow_nan=False)  # the real response class serialises like this


def test_empty_string_date_falls_back_to_vendor_rule(monkeypatch):
    rows = [
        _bill(1, "Shop A", 120, "", "2026-10-01T01:00:00Z"),
        _bill(2, "Shop B", 120, "", "2026-10-01T02:00:00Z"),
        _bill(3, "shop a", 120, "", "2026-10-01T03:00:00Z"),
    ]
    out = _run(monkeypatch, rows)
    assert out["total_groups"] == 1
    assert [b["id"] for b in out["groups"][0]["bills"]] == ["b1", "b3"]


def test_whitespace_only_vendor_is_treated_as_missing(monkeypatch):
    rows = [
        _bill(1, "   ", 80, None, "2026-10-01T01:00:00Z"),
        _bill(2, "   ", 80, None, "2026-10-01T02:00:00Z"),
    ]
    assert _run(monkeypatch, rows)["total_groups"] == 0


def _with_status(bill, status):
    return {**bill, "review_status": status}


def test_pending_rescan_of_a_confirmed_bill_is_flagged(monkeypatch):
    # Regression (2026-10-31): a re-scan of confirmed Singha SS 690600113 was
    # OCR'd as "SS 696000113" with a Thai vendor name, slipped past the dedup
    # key and became a new pending bill. Pending-only grouping never saw it.
    rows = [
        _with_status(_bill(1, "SINGHA BEER CO., LTD.", "20639.98", "2026-06-02", "2026-06-09T04:26:43Z"), "confirmed"),
        _bill(2, "บริษัท เบียร์สิงห์ จำกัด", "20639.98", "2026-06-02", "2026-10-31T10:16:45Z"),
    ]
    out = _run(monkeypatch, rows)
    assert out["total_groups"] == 1
    g = out["groups"][0]
    assert [b["id"] for b in g["bills"]] == ["b1", "b2"]
    assert [b["review_status"] for b in g["bills"]] == ["confirmed", "pending"]


def test_groups_without_a_pending_bill_are_not_shown(monkeypatch):
    # Two confirmed (or confirmed + rejected) bills with the same amount/date
    # were already decided by the operator — the banner must not nag forever.
    rows = [
        _with_status(_bill(1, "A", 100, "2026-10-01", "2026-10-01T01:00:00Z"), "confirmed"),
        _with_status(_bill(2, "B", 100, "2026-10-01", "2026-10-01T02:00:00Z"), "confirmed"),
        _with_status(_bill(3, "C", 200, "2026-10-01", "2026-10-01T03:00:00Z"), "confirmed"),
        _with_status(_bill(4, "C", 200, "2026-10-01", "2026-10-01T04:00:00Z"), "rejected"),
    ]
    assert _run(monkeypatch, rows)["total_groups"] == 0


def test_rejected_bills_never_join_a_group(monkeypatch):
    rows = [
        _with_status(_bill(1, "A", 100, "2026-10-01", "2026-10-01T01:00:00Z"), "rejected"),
        _bill(2, "A", 100, "2026-10-01", "2026-10-01T02:00:00Z"),
    ]
    assert _run(monkeypatch, rows)["total_groups"] == 0


def test_confirmed_bill_is_listed_first_even_if_uploaded_later(monkeypatch):
    # The banner marks bills[0] as "keep" and the rest as "probably reject" —
    # it must never suggest rejecting a bill the operator already confirmed.
    rows = [
        _bill(1, "A", 100, "2026-10-01", "2026-10-01T01:00:00Z"),
        _with_status(_bill(2, "A", 100, "2026-10-01", "2026-10-01T02:00:00Z"), "confirmed"),
    ]
    out = _run(monkeypatch, rows)
    assert [b["id"] for b in out["groups"][0]["bills"]] == ["b2", "b1"]


def test_undated_pending_matches_undated_confirmed_by_vendor(monkeypatch):
    rows = [
        _with_status(_bill(1, "Wealimex", 900, None, "2026-10-01T01:00:00Z"), "confirmed"),
        _bill(2, "wealimex", 900, None, "2026-10-02T01:00:00Z"),
    ]
    assert _run(monkeypatch, rows)["total_groups"] == 1


def test_dated_bill_without_vendor_name_can_still_be_grouped(monkeypatch):
    rows = [
        _bill(1, None, 350, "2026-10-02", "2026-10-02T01:00:00Z"),
        _bill(2, "7-Eleven", 350, "2026-10-02", "2026-10-02T02:00:00Z"),
    ]
    out = _run(monkeypatch, rows)
    assert out["total_groups"] == 1
    assert out["groups"][0]["vendor_name"] == "7-Eleven"
