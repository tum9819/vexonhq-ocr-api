"""
Admin-gate test (audit AUD-TAX-02) — financial-mutation endpoints must be admin-only.

Offline + deterministic: imports the real FastAPI app, monkeypatches verify_token to
mint controllable admin/staff payloads, and asserts on the ACTUAL wired routes that:
  - a STAFF token  -> 403 on every gated endpoint
  - no token       -> 401 (JWT middleware)
  - an ADMIN token -> passes the gate (not 401/403; may 4xx/5xx downstream, that's fine)

No DB / network / real keys needed. Run: pytest tests/test_admin_gate.py -v
"""
from __future__ import annotations

import os
import re

os.environ.setdefault("DATABASE_URL", "postgresql://u:p@localhost:5432/d")
os.environ.setdefault("JWT_SECRET", "testsecret")
os.environ.setdefault("OPENAI_API_KEY", "x")

import pytest
from fastapi.testclient import TestClient

import main
import auth_routes

# Endpoint function names that MUST be admin-gated (one per money-mutation route).
# `slip_match` intentionally appears in two modules (slip_routes + bill_payment_routes) —
# both are gated, so the name maps to 2 routes. Expected total wired routes = 18.
GATED_NAMES = {
    "classify_entry", "add_rule",
    "create_entry", "delete_entry",
    "create_statement_rule", "delete_statement_rule",
    "create_vendor_alias", "delete_vendor_alias",
    "manual_reconcile", "patch_slip", "delete_slip", "slips_rematch_all",
    "slip_match", "slip_manual_match", "slip_reject", "slip_override_category",
    "update_bill_payment",
    # AR/AP financial-record mutations (phase3_arap_routes)
    "create_counterparty", "patch_counterparty", "soft_delete_counterparty",
    "patch_entry", "cancel_entry", "create_payment", "delete_payment",
}
# create_entry maps to TWO gated routes (quick-entry + ar-ap), so 25 names -> 26 routes.
EXPECTED_ROUTE_COUNT = 26


def _fake_verify(token):
    if token == "ADMIN":
        return {"sub": "admin-uid", "_role": "admin"}
    if token == "STAFF":
        return {"sub": "staff-uid", "_role": "staff"}
    return None


def _gated_routes():
    out = []
    for r in main.app.routes:
        ep = getattr(r, "endpoint", None)
        name = getattr(ep, "__name__", None)
        if name in GATED_NAMES:
            method = next(m for m in r.methods if m not in ("HEAD", "OPTIONS"))
            path = re.sub(r"{[^}]+}", "x", r.path)
            out.append((method, path, name))
    return out


@pytest.fixture()
def client(monkeypatch):
    # main's middleware uses main.verify_token; _require_admin_role uses auth_routes.verify_token.
    monkeypatch.setattr(main, "verify_token", _fake_verify)
    monkeypatch.setattr(auth_routes, "verify_token", _fake_verify)
    return TestClient(main.app, raise_server_exceptions=False)


def test_all_eighteen_routes_present():
    routes = _gated_routes()
    assert len(routes) == EXPECTED_ROUTE_COUNT, f"expected {EXPECTED_ROUTE_COUNT} gated routes, found {len(routes)}: {routes}"


def test_staff_token_is_forbidden(client):
    failures = []
    for method, path, name in _gated_routes():
        resp = client.request(method, path, headers={"Authorization": "Bearer STAFF"}, json={})
        if resp.status_code != 403:
            failures.append(f"{method} {path} ({name}) -> {resp.status_code} (want 403)")
    assert not failures, "staff was NOT blocked on:\n" + "\n".join(failures)


def test_no_token_is_unauthorized(client):
    failures = []
    for method, path, name in _gated_routes():
        resp = client.request(method, path, json={})
        if resp.status_code != 401:
            failures.append(f"{method} {path} ({name}) -> {resp.status_code} (want 401)")
    assert not failures, "missing-token was NOT 401 on:\n" + "\n".join(failures)


def test_admin_token_passes_the_gate(client):
    failures = []
    for method, path, name in _gated_routes():
        resp = client.request(method, path, headers={"Authorization": "Bearer ADMIN"}, json={})
        if resp.status_code in (401, 403):
            failures.append(f"{method} {path} ({name}) -> {resp.status_code} (admin should pass the gate)")
    assert not failures, "admin was wrongly blocked on:\n" + "\n".join(failures)
