"""
Offline route tests for stock-in endpoints (M1 Antigravity REVISE — Items 1-4).

Group A: admin gate  — staff → 403, no token → 401, admin passes gate
Group B: UUID param  — non-UUID import_id → 400
Group C: body schema — no approved_by/cancelled_by field; Resolution action validated
Group D: _validate_resolutions pure contract — unresolved blocking rows → 409

No real DB required. get_db_conn is patched where routes would reach the DB.
"""
from __future__ import annotations

import os
import types

os.environ.setdefault("DATABASE_URL", "postgresql://u:p@localhost:5432/d")
os.environ.setdefault("JWT_SECRET", "testsecret")
os.environ.setdefault("OPENAI_API_KEY", "x")

import pytest
from fastapi.testclient import TestClient
from fastapi import HTTPException

import main
import auth_routes
import stock_in_routes
from stock_in_routes import ApproveRequest, CancelRequest, Resolution, _validate_resolutions


# ── helpers ──────────────────────────────────────────────────────────────────

_GOOD_UUID = "00000000-0000-0000-0000-000000000000"
_BAD_UUID  = "not-a-uuid"

_APPROVE_BODY = {
    "expected_counts": {"new": 0, "unchanged": 0, "changed": 0, "missing": 0},
    "resolutions": [],
}
_CANCEL_BODY = {}


def _fake_verify(token):
    if token == "ADMIN":
        return {"sub": "admin-uid", "_role": "admin"}
    if token == "STAFF":
        return {"sub": "staff-uid", "_role": "staff"}
    return None


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setattr(main, "verify_token", _fake_verify)
    monkeypatch.setattr(auth_routes, "verify_token", _fake_verify)

    class _DummyQuery:
        def update(self, *a, **kw): return self
        def select(self, *a, **kw): return self
        def eq(self, *a, **kw): return self
        def limit(self, *a, **kw): return self
        def order(self, *a, **kw): return self
        def execute(self): return types.SimpleNamespace(data=[{"id": "ok"}])

    class _DummySupabase:
        def table(self, *a, **kw): return _DummyQuery()

    monkeypatch.setattr(main, "get_supabase", lambda: _DummySupabase())
    monkeypatch.setattr(main, "_revalidate_bill", lambda *a, **kw: [])
    monkeypatch.setattr(main, "_match_invoice_against_statement", lambda *a, **kw: {"status": "matched"})
    monkeypatch.setattr(main, "_auto_sync_ingredient_prices", lambda: {"status": "ok"})
    return TestClient(main.app, raise_server_exceptions=False)


# ── Group A: admin gate ───────────────────────────────────────────────────────

@pytest.mark.parametrize("method,path,body", [
    ("GET",  f"/pos/stock-in/diff/{_GOOD_UUID}",    None),
    ("POST", f"/pos/stock-in/approve/{_GOOD_UUID}", _APPROVE_BODY),
    ("POST", f"/pos/stock-in/cancel/{_GOOD_UUID}",  _CANCEL_BODY),
    ("POST", f"/pos/stock-in/recover/{_GOOD_UUID}", {}),
])
def test_staff_token_is_forbidden(client, method, path, body):
    resp = client.request(method, path, headers={"Authorization": "Bearer STAFF"}, json=body)
    assert resp.status_code == 403, f"{method} {path} → {resp.status_code} (want 403 for staff)"


@pytest.mark.parametrize("method,path,body", [
    ("GET",  f"/pos/stock-in/diff/{_GOOD_UUID}",    None),
    ("POST", f"/pos/stock-in/approve/{_GOOD_UUID}", _APPROVE_BODY),
    ("POST", f"/pos/stock-in/cancel/{_GOOD_UUID}",  _CANCEL_BODY),
    ("POST", f"/pos/stock-in/recover/{_GOOD_UUID}", {}),
])
def test_no_token_is_unauthorized(client, method, path, body):
    resp = client.request(method, path, json=body)
    assert resp.status_code == 401, f"{method} {path} → {resp.status_code} (want 401 for no token)"


@pytest.mark.parametrize("method,path,body", [
    ("GET",  f"/pos/stock-in/diff/{_GOOD_UUID}",    None),
    ("POST", f"/pos/stock-in/approve/{_GOOD_UUID}", _APPROVE_BODY),
    ("POST", f"/pos/stock-in/cancel/{_GOOD_UUID}",  _CANCEL_BODY),
    ("POST", f"/pos/stock-in/recover/{_GOOD_UUID}", {}),
])
def test_admin_passes_gate(client, method, path, body):
    resp = client.request(method, path, headers={"Authorization": "Bearer ADMIN"}, json=body)
    assert resp.status_code not in (401, 403), \
        f"{method} {path} → {resp.status_code} (admin should pass the gate)"


# ── Group B: UUID path validation ─────────────────────────────────────────────

@pytest.mark.parametrize("method,path,body", [
    ("GET",  f"/pos/stock-in/diff/{_BAD_UUID}",    None),
    ("POST", f"/pos/stock-in/approve/{_BAD_UUID}", _APPROVE_BODY),
    ("POST", f"/pos/stock-in/cancel/{_BAD_UUID}",  _CANCEL_BODY),
    ("POST", f"/pos/stock-in/recover/{_BAD_UUID}", {}),
])
def test_invalid_uuid_is_rejected(client, method, path, body):
    resp = client.request(method, path, headers={"Authorization": "Bearer ADMIN"}, json=body)
    assert resp.status_code == 400, \
        f"{method} {path} → {resp.status_code} (want 400 for non-UUID import_id)"


# ── Group C: request body schema ──────────────────────────────────────────────

def test_approve_request_has_no_approved_by_field():
    """approved_by must come from JWT, not request body."""
    fields = ApproveRequest.model_fields if hasattr(ApproveRequest, "model_fields") \
             else ApproveRequest.__fields__
    assert "approved_by" not in fields, \
        "ApproveRequest must NOT have an approved_by field — identity comes from JWT"


def test_cancel_request_has_no_cancelled_by_field():
    """cancelled_by must come from JWT, not request body."""
    fields = CancelRequest.model_fields if hasattr(CancelRequest, "model_fields") \
             else CancelRequest.__fields__
    assert "cancelled_by" not in fields, \
        "CancelRequest must NOT have a cancelled_by field — identity comes from JWT"


def test_resolution_requires_row_id_and_action():
    r = Resolution(row_id="aaa", action="retain")
    assert r.row_id == "aaa"
    assert r.action == "retain"


@pytest.mark.parametrize("action", ["retain", "supersede", "void"])
def test_resolution_valid_actions(action):
    r = Resolution(row_id="x", action=action)
    assert r.action == action


def test_resolution_invalid_action_raises():
    with pytest.raises(Exception):  # pydantic ValidationError
        Resolution(row_id="x", action="delete")


def test_resolution_invalid_action_via_api(client):
    body = {
        "expected_counts": {"new": 0, "unchanged": 0, "changed": 0, "missing": 0},
        "resolutions": [{"row_id": "x", "action": "destroy"}],
    }
    resp = client.post(
        f"/pos/stock-in/approve/{_GOOD_UUID}",
        headers={"Authorization": "Bearer ADMIN"},
        json=body,
    )
    assert resp.status_code == 422, \
        f"invalid resolution action should be 422, got {resp.status_code}"


# ── Group D: _validate_resolutions pure contract ──────────────────────────────

def _make_row(row_id: str, **kwargs) -> dict:
    base = {
        "id": row_id,
        "item_name": "TestItem",
        "canonical_key": "ck1",
        "identity_key": "ik1",
        "occurrence_index": 0,
    }
    base.update(kwargs)
    return base


def test_validate_resolutions_passes_when_no_blocking_rows():
    diff = {"insert": [], "skip": [], "needs_review": [], "missing_from_reexport": []}
    _validate_resolutions(diff, {})  # must not raise


def test_validate_resolutions_needs_review_unresolved_raises_409():
    diff = {
        "insert": [],
        "skip": [],
        "needs_review": [_make_row("staged-1")],
        "missing_from_reexport": [],
    }
    with pytest.raises(HTTPException) as exc:
        _validate_resolutions(diff, {})
    assert exc.value.status_code == 409
    assert exc.value.detail["error"] == "unresolved_rows"
    assert any(r["row_id"] == "staged-1" for r in exc.value.detail["unresolved"])


def test_validate_resolutions_missing_unresolved_raises_409():
    diff = {
        "insert": [],
        "skip": [],
        "needs_review": [],
        "missing_from_reexport": [_make_row("committed-1")],
    }
    with pytest.raises(HTTPException) as exc:
        _validate_resolutions(diff, {})
    assert exc.value.status_code == 409
    assert any(r["row_id"] == "committed-1" for r in exc.value.detail["unresolved"])


def test_validate_resolutions_all_resolved_passes():
    diff = {
        "insert": [],
        "skip": [],
        "needs_review": [_make_row("staged-1")],
        "missing_from_reexport": [_make_row("committed-1")],
    }
    resolutions = {
        "staged-1": Resolution(row_id="staged-1", action="supersede"),
        "committed-1": Resolution(row_id="committed-1", action="void"),
    }
    _validate_resolutions(diff, resolutions)  # must not raise


def test_validate_resolutions_partial_missing_raises():
    diff = {
        "insert": [],
        "skip": [],
        "needs_review": [_make_row("staged-1"), _make_row("staged-2")],
        "missing_from_reexport": [],
    }
    resolutions = {
        "staged-1": Resolution(row_id="staged-1", action="retain"),
        # staged-2 unresolved
    }
    with pytest.raises(HTTPException) as exc:
        _validate_resolutions(diff, resolutions)
    assert exc.value.status_code == 409
    unresolved_ids = [r["row_id"] for r in exc.value.detail["unresolved"]]
    assert "staged-2" in unresolved_ids
    assert "staged-1" not in unresolved_ids
