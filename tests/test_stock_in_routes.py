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
    _validate_resolutions(diff, [])  # must not raise


def test_validate_resolutions_needs_review_unresolved_raises_409():
    diff = {
        "insert": [],
        "skip": [],
        "needs_review": [_make_row("staged-1")],
        "missing_from_reexport": [],
    }
    with pytest.raises(HTTPException) as exc:
        _validate_resolutions(diff, [])
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
        _validate_resolutions(diff, [])
    assert exc.value.status_code == 409
    assert any(r["row_id"] == "committed-1" for r in exc.value.detail["unresolved"])


def test_validate_resolutions_all_resolved_passes():
    diff = {
        "insert": [],
        "skip": [],
        "needs_review": [_make_row("staged-1")],
        "missing_from_reexport": [_make_row("committed-1")],
    }
    resolutions = [
        Resolution(row_id="staged-1", action="supersede"),
        Resolution(row_id="committed-1", action="void"),
    ]
    _validate_resolutions(diff, resolutions)  # must not raise


def test_validate_resolutions_partial_missing_raises():
    diff = {
        "insert": [],
        "skip": [],
        "needs_review": [_make_row("staged-1"), _make_row("staged-2")],
        "missing_from_reexport": [],
    }
    resolutions = [
        Resolution(row_id="staged-1", action="retain"),
        # staged-2 unresolved
    ]
    with pytest.raises(HTTPException) as exc:
        _validate_resolutions(diff, resolutions)
    assert exc.value.status_code == 409
    unresolved_ids = [r["row_id"] for r in exc.value.detail["unresolved"]]
    assert "staged-2" in unresolved_ids
    assert "staged-1" not in unresolved_ids


def test_validate_resolutions_rejects_duplicates():
    diff = {
        "insert": [],
        "skip": [],
        "needs_review": [_make_row("staged-1")],
        "missing_from_reexport": [],
    }
    resolutions = [
        Resolution(row_id="staged-1", action="retain"),
        Resolution(row_id="staged-1", action="supersede"),
    ]
    with pytest.raises(HTTPException) as exc:
        _validate_resolutions(diff, resolutions)
    assert exc.value.status_code == 409
    assert exc.value.detail["error"] == "invalid_resolution"
    assert "Duplicate" in exc.value.detail["detail"]


def test_validate_resolutions_rejects_unknown_ids():
    diff = {
        "insert": [],
        "skip": [],
        "needs_review": [_make_row("staged-1")],
        "missing_from_reexport": [],
    }
    resolutions = [
        Resolution(row_id="staged-1", action="retain"),
        Resolution(row_id="unknown-uuid", action="retain"),
    ]
    with pytest.raises(HTTPException) as exc:
        _validate_resolutions(diff, resolutions)
    assert exc.value.status_code == 409
    assert exc.value.detail["error"] == "invalid_resolution"
    assert "not found in needs_review" in exc.value.detail["detail"]


def test_validate_resolutions_rejects_wrong_actions_for_needs_review():
    diff = {
        "insert": [],
        "skip": [],
        "needs_review": [_make_row("staged-1")],
        "missing_from_reexport": [],
    }
    resolutions = [
        # void is invalid for needs_review
        Resolution(row_id="staged-1", action="void"),
    ]
    with pytest.raises(HTTPException) as exc:
        _validate_resolutions(diff, resolutions)
    assert exc.value.status_code == 409
    assert exc.value.detail["error"] == "invalid_resolution"
    assert "Action 'void' is invalid for needs_review" in exc.value.detail["detail"]


def test_validate_resolutions_rejects_wrong_actions_for_missing():
    diff = {
        "insert": [],
        "skip": [],
        "needs_review": [],
        "missing_from_reexport": [_make_row("committed-1")],
    }
    resolutions = [
        # supersede is invalid for missing
        Resolution(row_id="committed-1", action="supersede"),
    ]
    with pytest.raises(HTTPException) as exc:
        _validate_resolutions(diff, resolutions)
    assert exc.value.status_code == 409
    assert exc.value.detail["error"] == "invalid_resolution"
    assert "Action 'supersede' is invalid for missing_from_reexport" in exc.value.detail["detail"]


# ── Group E: recover endpoint contract ───────────────────────────────────────

def test_recover_non_parsing_import_is_409(client, monkeypatch):
    """Recover on a 'success' import returns 409 not_recoverable."""
    import psycopg2

    class _Cur:
        def execute(self, sql, params=None):
            self._sql = sql
        def fetchone(self):
            # pos_imports row: status='success'
            return ("success", "branch1", "user1", None)
        def fetchall(self): return []
        def __enter__(self): return self
        def __exit__(self, *a): pass

    class _Conn:
        def cursor(self): return _Cur()
        def commit(self): pass
        def rollback(self): pass
        def close(self): pass
        def __enter__(self): return self
        def __exit__(self, *a): pass

    monkeypatch.setattr(stock_in_routes, "get_db_conn", lambda: _Conn())
    resp = client.post(
        f"/pos/stock-in/recover/{_GOOD_UUID}",
        headers={"Authorization": "Bearer ADMIN"},
        json={},
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "not_recoverable"


def test_recover_recent_parsing_import_is_409(client, monkeypatch):
    """Recover on a 'parsing' import that started < threshold minutes ago returns 409."""
    from datetime import datetime, timezone

    class _Cur:
        def execute(self, sql, params=None): pass
        def fetchone(self):
            # processing_started_at = right now (not stuck)
            return ("parsing", "branch1", "user1", datetime.now(timezone.utc))
        def fetchall(self): return []
        def __enter__(self): return self
        def __exit__(self, *a): pass

    class _Conn:
        def cursor(self): return _Cur()
        def commit(self): pass
        def rollback(self): pass
        def close(self): pass

    monkeypatch.setattr(stock_in_routes, "get_db_conn", lambda: _Conn())
    resp = client.post(
        f"/pos/stock-in/recover/{_GOOD_UUID}",
        headers={"Authorization": "Bearer ADMIN"},
        json={},
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "not_stuck_yet"


# ── Group F: Shared DB Connection Resolver ───────────────────────────────────

def test_get_db_conn_lazy_resolver(monkeypatch):
    sentinel_connection = object()
    monkeypatch.setattr(main, "get_db_conn", lambda: sentinel_connection)

    conn = stock_in_routes.get_db_conn()
    assert conn is sentinel_connection


def test_routes_use_shared_db_conn_resolver(monkeypatch, client):
    """
    Verify M1 routes use main.get_db_conn instead of direct psycopg2.connect fallback.
    We monkeypatch main.get_db_conn to return a mock connection and monkeypatch
    psycopg2.connect to raise an error to ensure no fallback direct psycopg2.connect is bypass-called.
    """
    import psycopg2
    import stock_in_routes

    # Monkeypatch psycopg2.connect to raise a loud error if called directly
    def _faulty_connect(*args, **kwargs):
        raise RuntimeError("psycopg2.connect called directly!")
    monkeypatch.setattr(psycopg2, "connect", _faulty_connect)

    class MockCursor:
        def __init__(self):
            self.calls = []
        def execute(self, sql, params=None):
            self.calls.append((sql, params))
        def fetchone(self):
            # Return dummy pos_imports row: status='needs_review'
            return ("needs_review", "branch1", None, None)
        def fetchall(self):
            # return empty list for staged/committed rows
            return []
        def __enter__(self):
            return self
        def __exit__(self, exc_type, exc_val, exc_tb):
            pass

    class MockConnection:
        def __init__(self):
            self.cursor_instance = MockCursor()
            self.closed = False
            self.commit_called = False
            self.rollback_called = False
        def cursor(self):
            return self.cursor_instance
        def commit(self):
            self.commit_called = True
        def rollback(self):
            self.rollback_called = True
        def close(self):
            self.closed = True

    mock_conn = MockConnection()
    monkeypatch.setattr(main, "get_db_conn", lambda: mock_conn)

    # Call diff route via client
    resp = client.get(
        f"/pos/stock-in/diff/{_GOOD_UUID}",
        headers={"Authorization": "Bearer ADMIN"}
    )
    # The route should succeed because mock connection handles it, returning 200
    assert resp.status_code == 200
    # Confirm it fetched data and closed connection
    assert mock_conn.closed is True


# ── Group E: GET /pos/stock-in/verification/{import_id} ──────────────────────

@pytest.mark.parametrize("method,path,body", [
    ("GET", f"/pos/stock-in/verification/{_GOOD_UUID}", None),
])
def test_verification_staff_token_is_forbidden(client, method, path, body):
    resp = client.request(method, path, headers={"Authorization": "Bearer STAFF"}, json=body)
    assert resp.status_code == 403

def test_verification_no_token_is_unauthorized(client):
    resp = client.get(f"/pos/stock-in/verification/{_GOOD_UUID}")
    assert resp.status_code == 401

def test_verification_invalid_uuid_is_rejected(client):
    resp = client.get("/pos/stock-in/verification/not-a-uuid", headers={"Authorization": "Bearer ADMIN"})
    assert resp.status_code == 400

class _MockVerificationCur:
    def __init__(self, fetch_sequence):
        self.fetch_sequence = fetch_sequence
        self.call_idx = 0
    def __enter__(self): return self
    def __exit__(self, *a): pass
    def execute(self, q, p=None): pass
    def fetchone(self):
        res = self.fetch_sequence[self.call_idx]
        self.call_idx += 1
        return res
    def fetchall(self):
        res = self.fetch_sequence[self.call_idx]
        self.call_idx += 1
        return res

class _MockVerificationConn:
    def __init__(self, fetch_sequence):
        self.cur = _MockVerificationCur(fetch_sequence)
        self.closed = False
    def cursor(self): return self.cur
    def close(self): self.closed = True

def test_verification_unknown_import_is_404(client, monkeypatch):
    conn = _MockVerificationConn([None])
    monkeypatch.setattr(main, "get_db_conn", lambda: conn)
    resp = client.get(f"/pos/stock-in/verification/{_GOOD_UUID}", headers={"Authorization": "Bearer ADMIN"})
    assert resp.status_code == 404
    assert conn.closed is True

def test_verification_non_success_status_is_409(client, monkeypatch):
    conn = _MockVerificationConn([("staged", "report", "bkk", "2026-06-01", "2026-06-01", 10)])
    monkeypatch.setattr(main, "get_db_conn", lambda: conn)
    resp = client.get(f"/pos/stock-in/verification/{_GOOD_UUID}", headers={"Authorization": "Bearer ADMIN"})
    assert resp.status_code == 409
    assert conn.closed is True

from decimal import Decimal
def test_verification_success_normal(client, monkeypatch):
    # 1: import status
    # 2: staging count
    # 3: committed row
    # 4: audit rows (fetchall)
    # 5: duplicate keys
    seq = [
        ("success", "report", "bkk", "2026-06-01", "2026-06-01", 10),
        (0,), # staging count
        (10, Decimal('50.00'), Decimal('100.12'), 10, Decimal('50.00'), Decimal('100.12'), "2026-06-01", "2026-06-01", 0), # committed
        [("approve", "admin_user", {"new": 10, "unchanged": 0, "changed": 0, "missing": 0})], # audit
        (0,), # duplicates
    ]
    conn = _MockVerificationConn(seq)
    monkeypatch.setattr(main, "get_db_conn", lambda: conn)
    resp = client.get(f"/pos/stock-in/verification/{_GOOD_UUID}", headers={"Authorization": "Bearer ADMIN"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["verification_status"] == "verified"
    assert data["integrity"]["warnings"] == []
    assert data["committed"]["snapshot_rows"] == 10
    assert data["committed"]["snapshot_qty"] == 50.0
    assert data["committed"]["snapshot_net_cost"] == 100.12
    assert conn.closed is True


def test_verification_staging_count_warning(client, monkeypatch):
    seq = [
        ("success", "report", "bkk", "2026-06-01", "2026-06-01", 10),
        (5,), # staging count non-zero
        (10, 50.0, 100.0, 10, 50.0, 100.0, "2026-06-01", "2026-06-01", 0),
        [("approve", "admin_user", {"new": 10, "unchanged": 0, "changed": 0, "missing": 0})],
        (0,),
    ]
    monkeypatch.setattr(main, "get_db_conn", lambda: _MockVerificationConn(seq))
    resp = client.get(f"/pos/stock-in/verification/{_GOOD_UUID}", headers={"Authorization": "Bearer ADMIN"})
    assert resp.status_code == 200
    assert resp.json()["verification_status"] == "warning"
    assert "Found 5 staged rows" in resp.json()["integrity"]["warnings"][0]

def test_verification_missing_audit_warning(client, monkeypatch):
    seq = [
        ("success", "report", "bkk", "2026-06-01", "2026-06-01", 10),
        (0,),
        (10, 50.0, 100.0, 10, 50.0, 100.0, "2026-06-01", "2026-06-01", 0),
        [], # empty audit
        (0,),
    ]
    monkeypatch.setattr(main, "get_db_conn", lambda: _MockVerificationConn(seq))
    resp = client.get(f"/pos/stock-in/verification/{_GOOD_UUID}", headers={"Authorization": "Bearer ADMIN"})
    assert resp.status_code == 200
    assert resp.json()["verification_status"] == "warning"
    assert "Missing audit record" in resp.json()["integrity"]["warnings"][0]
    assert resp.json()["audit"] is None

def test_verification_multiple_audit_warning(client, monkeypatch):
    seq = [
        ("success", "report", "bkk", "2026-06-01", "2026-06-01", 10),
        (0,),
        (10, 50.0, 100.0, 10, 50.0, 100.0, "2026-06-01", "2026-06-01", 0),
        [
            ("approve", "admin1", {"new": 10, "unchanged": 0, "changed": 0, "missing": 0}),
            ("approve", "admin2", {"new": 10, "unchanged": 0, "changed": 0, "missing": 0})
        ],
        (0,),
    ]
    monkeypatch.setattr(main, "get_db_conn", lambda: _MockVerificationConn(seq))
    resp = client.get(f"/pos/stock-in/verification/{_GOOD_UUID}", headers={"Authorization": "Bearer ADMIN"})
    assert resp.status_code == 200
    assert resp.json()["verification_status"] == "warning"
    assert "Multiple audit records" in resp.json()["integrity"]["warnings"][0]
    assert resp.json()["audit"]["approved_by"] == "admin1"

def test_verification_wrong_audit_decision(client, monkeypatch):
    seq = [
        ("success", "report", "bkk", "2026-06-01", "2026-06-01", 10),
        (0,),
        (10, 50.0, 100.0, 10, 50.0, 100.0, "2026-06-01", "2026-06-01", 0),
        [("cancel", "admin", {"new": 10, "unchanged": 0, "changed": 0, "missing": 0})],
        (0,),
    ]
    monkeypatch.setattr(main, "get_db_conn", lambda: _MockVerificationConn(seq))
    resp = client.get(f"/pos/stock-in/verification/{_GOOD_UUID}", headers={"Authorization": "Bearer ADMIN"})
    assert resp.status_code == 200
    assert "not 'approve'" in resp.json()["integrity"]["warnings"][0]

def test_verification_malformed_counts_json(client, monkeypatch):
    seq = [
        ("success", "report", "bkk", "2026-06-01", "2026-06-01", 10),
        (0,),
        (10, 50.0, 100.0, 10, 50.0, 100.0, "2026-06-01", "2026-06-01", 0),
        [("approve", "admin", {"missing_keys": True})],
        (0,),
    ]
    monkeypatch.setattr(main, "get_db_conn", lambda: _MockVerificationConn(seq))
    resp = client.get(f"/pos/stock-in/verification/{_GOOD_UUID}", headers={"Authorization": "Bearer ADMIN"})
    assert resp.status_code == 200
    assert any("Malformed" in w for w in resp.json()["integrity"]["warnings"])

def test_verification_snapshot_vs_active_totals(client, monkeypatch):
    seq = [
        ("success", "report", "bkk", "2026-06-01", "2026-06-01", 10),
        (0,),
        (10, 50.0, 100.0, 8, 40.0, 80.0, "2026-06-01", "2026-06-01", 0),
        [("approve", "admin", {"new": 10, "unchanged": 0, "changed": 0, "missing": 0})],
        (0,),
    ]
    monkeypatch.setattr(main, "get_db_conn", lambda: _MockVerificationConn(seq))
    resp = client.get(f"/pos/stock-in/verification/{_GOOD_UUID}", headers={"Authorization": "Bearer ADMIN"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["committed"]["snapshot_rows"] == 10
    assert data["committed"]["current_active_rows"] == 8

def test_verification_branch_mismatch_and_duplicates(client, monkeypatch):
    seq = [
        ("success", "report", "bkk", "2026-06-01", "2026-06-01", 10),
        (0,),
        (10, 50.0, 100.0, 10, 50.0, 100.0, "2026-06-01", "2026-06-01", 2), # branch mismatch = 2
        [("approve", "admin", {"new": 10, "unchanged": 0, "changed": 0, "missing": 0})],
        (3,), # duplicates = 3
    ]
    monkeypatch.setattr(main, "get_db_conn", lambda: _MockVerificationConn(seq))
    resp = client.get(f"/pos/stock-in/verification/{_GOOD_UUID}", headers={"Authorization": "Bearer ADMIN"})
    data = resp.json()
    assert data["verification_status"] == "warning"
    assert data["integrity"]["branch_mismatch_rows"] == 2
    assert data["integrity"]["duplicate_active_keys"] == 3
    warnings = data["integrity"]["warnings"]
    assert any("branch_code mismatching" in w for w in warnings)
    assert any("duplicate active keys" in w for w in warnings)

def test_verification_coalesce_empty_aggregate(client, monkeypatch):
    seq = [
        ("success", "report", "bkk", "2026-06-01", "2026-06-01", 0),
        (0,),
        (0, 0, 0, 0, 0, 0, None, None, 0), # Empty aggregate
        [("approve", "admin", {"new": 0, "unchanged": 0, "changed": 0, "missing": 0})],
        (0,),
    ]
    monkeypatch.setattr(main, "get_db_conn", lambda: _MockVerificationConn(seq))
    resp = client.get(f"/pos/stock-in/verification/{_GOOD_UUID}", headers={"Authorization": "Bearer ADMIN"})
    data = resp.json()
    assert data["committed"]["snapshot_rows"] == 0
    assert data["committed"]["snapshot_qty"] == 0.0

def test_verification_db_exception_is_sanitized(client, monkeypatch):
    class _ErrorConn:
        def __init__(self):
            self.closed = False
        def cursor(self): raise Exception("Secret DB Error")
        def close(self): self.closed = True
    conn = _ErrorConn()
    monkeypatch.setattr(main, "get_db_conn", lambda: conn)
    resp = client.get(f"/pos/stock-in/verification/{_GOOD_UUID}", headers={"Authorization": "Bearer ADMIN"})
    assert resp.status_code == 500
    assert "Secret DB Error" not in resp.text
    assert resp.json()["detail"] == "Internal Server Error"
    assert conn.closed is True

