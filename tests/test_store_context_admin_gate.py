"""
store_context mutation gate — must follow the JWT role, not a username list.

Regression: the gate compared request.state.username against a hardcoded set of
legacy login names ({"vexonhq","Tum",...}). Since the Supabase SSO cutover
(2026-05-25) the middleware stores the token `sub` there, which is a UUID, so
every real user — admins included — got 403 on create/patch/delete/reload and
the AI knowledge layer could not be edited from /admin/store-context.

Offline + deterministic: real FastAPI app, verify_token monkeypatched, DB access
stubbed to fail loudly so an admin request that passes the gate shows up as a
non-401/403 status. Run: pytest tests/test_store_context_admin_gate.py -v
"""
from __future__ import annotations

import os

os.environ.setdefault("DATABASE_URL", "postgresql://u:p@localhost:5432/d")
os.environ.setdefault("JWT_SECRET", "testsecret")
os.environ.setdefault("OPENAI_API_KEY", "x")

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest
from fastapi.testclient import TestClient

import main
import store_context_routes

ADMIN_UUID = "878f5109-0000-4000-8000-000000000001"
STAFF_UUID = "11111111-0000-4000-8000-000000000002"

MUTATIONS = [
    ("POST", "/store-context", {"key": "k", "content": "c"}),
    ("PATCH", "/store-context/k", {"content": "c"}),
    ("DELETE", "/store-context/k", None),
    ("POST", "/store-context/reload", None),
]


def _fake_verify(token):
    if token == "ADMIN":
        return {"sub": ADMIN_UUID, "_role": "admin"}
    if token == "STAFF":
        return {"sub": STAFF_UUID, "_role": "staff"}
    # A legacy-style username that used to be on the allowlist, without admin role.
    if token == "LEGACY_NAME_STAFF":
        return {"sub": "vexonhq", "_role": "staff"}
    return None


def _no_db():
    raise RuntimeError("DB disabled in this test")


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setattr(main, "verify_token", _fake_verify)
    monkeypatch.setattr(store_context_routes, "get_db_conn", _no_db)
    return TestClient(main.app, raise_server_exceptions=False)


def _call(client, method, path, body, token=None):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return client.request(method, path, headers=headers, json=body)


def test_sso_admin_can_reload_cache(client):
    resp = _call(client, "POST", "/store-context/reload", None, "ADMIN")
    assert resp.status_code == 200, resp.text


@pytest.mark.parametrize("method,path,body", MUTATIONS)
def test_sso_admin_passes_the_gate(client, method, path, body):
    resp = _call(client, method, path, body, "ADMIN")
    assert resp.status_code not in (401, 403), f"{method} {path} -> {resp.status_code}: {resp.text}"


@pytest.mark.parametrize("method,path,body", MUTATIONS)
def test_staff_is_forbidden(client, method, path, body):
    resp = _call(client, method, path, body, "STAFF")
    assert resp.status_code == 403, f"{method} {path} -> {resp.status_code}"


@pytest.mark.parametrize("method,path,body", MUTATIONS)
def test_legacy_username_alone_is_not_enough(client, method, path, body):
    resp = _call(client, method, path, body, "LEGACY_NAME_STAFF")
    assert resp.status_code == 403, f"{method} {path} -> {resp.status_code}"


@pytest.mark.parametrize("method,path,body", MUTATIONS)
def test_no_token_is_unauthorized(client, method, path, body):
    resp = _call(client, method, path, body)
    assert resp.status_code == 401, f"{method} {path} -> {resp.status_code}"
