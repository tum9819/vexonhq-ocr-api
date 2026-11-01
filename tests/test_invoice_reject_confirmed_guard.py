"""
POST /invoice/{id}/reject — a confirmed bill needs allow_confirmed=true.

Antigravity review 2026-11-01: the duplicate banner links to the confirmed
bill it says to keep, and the detail page's reject button was enabled for it.
Rejecting a confirmed bill silently drops it from AP, P&L and exports.

Offline: get_supabase is replaced with an in-memory fake.
Run: pytest tests/test_invoice_reject_confirmed_guard.py -v
"""
from __future__ import annotations

import os
import types

os.environ.setdefault("DATABASE_URL", "postgresql://u:p@localhost:5432/d")
os.environ.setdefault("OPENAI_API_KEY", "x")

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest
from fastapi import HTTPException

import main

BILL_ID = "11111111-1111-1111-1111-111111111111"


class _FakeQuery:
    def __init__(self, store):
        self._store = store
        self._update = None

    def select(self, *a, **k):
        return self

    def update(self, values):
        self._update = values
        return self

    def eq(self, column, value):
        self._match = value
        return self

    def execute(self):
        row = self._store.get(self._match)
        if row is None:
            return types.SimpleNamespace(data=[])
        if self._update is not None:
            row.update(self._update)
            self._store["updates"] += 1
        return types.SimpleNamespace(data=[dict(row)])


def _run(monkeypatch, status, body):
    store = {BILL_ID: {"id": BILL_ID, "review_status": status}, "updates": 0}
    fake = types.SimpleNamespace(table=lambda name: _FakeQuery(store))
    monkeypatch.setattr(main, "get_supabase", lambda: fake)
    monkeypatch.setattr(main, "_require_admin_request", lambda request: None)
    monkeypatch.setattr(main, "_current_username", lambda request: "tester")
    return store, main.invoice_reject(BILL_ID, request=None, body=body)


def test_pending_bill_rejects_as_before(monkeypatch):
    store, out = _run(monkeypatch, "pending", {"reject_reason": "ซ้ำ"})
    assert out["invoice"]["review_status"] == "rejected"
    assert store["updates"] == 1


def test_confirmed_bill_without_opt_in_is_refused_and_untouched(monkeypatch):
    with pytest.raises(HTTPException) as e:
        _run(monkeypatch, "confirmed", {"reject_reason": "ซ้ำ"})
    assert e.value.status_code == 409


@pytest.mark.parametrize("flag", ["true", 1, "yes"])
def test_only_literal_true_counts_as_opt_in(monkeypatch, flag):
    with pytest.raises(HTTPException) as e:
        _run(monkeypatch, "confirmed", {"reject_reason": "ซ้ำ", "allow_confirmed": flag})
    assert e.value.status_code == 409


def test_confirmed_bill_with_opt_in_is_rejected(monkeypatch):
    store, out = _run(monkeypatch, "confirmed", {"reject_reason": "ซ้ำ", "allow_confirmed": True})
    assert out["invoice"]["review_status"] == "rejected"
    assert store["updates"] == 1


def test_missing_bill_is_404(monkeypatch):
    monkeypatch.setattr(main, "get_supabase", lambda: types.SimpleNamespace(
        table=lambda name: _FakeQuery({"updates": 0})))
    monkeypatch.setattr(main, "_require_admin_request", lambda request: None)
    monkeypatch.setattr(main, "_current_username", lambda request: "tester")
    with pytest.raises(HTTPException) as e:
        main.invoice_reject(BILL_ID, request=None, body={"reject_reason": "x"})
    assert e.value.status_code == 404
