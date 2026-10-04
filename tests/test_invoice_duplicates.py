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

    def in_(self, *a, **k):
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


def test_dated_bill_without_vendor_name_can_still_be_grouped(monkeypatch):
    rows = [
        _bill(1, None, 350, "2026-10-02", "2026-10-02T01:00:00Z"),
        _bill(2, "7-Eleven", 350, "2026-10-02", "2026-10-02T02:00:00Z"),
    ]
    out = _run(monkeypatch, rows)
    assert out["total_groups"] == 1
    assert out["groups"][0]["vendor_name"] == "7-Eleven"
