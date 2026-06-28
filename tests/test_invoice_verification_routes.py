"""Route-level regression tests for invoice verification API guards."""
from __future__ import annotations

import os
import types

os.environ.setdefault("DATABASE_URL", "postgresql://u:p@localhost:5432/d")
os.environ.setdefault("JWT_SECRET", "testsecret")
os.environ.setdefault("OPENAI_API_KEY", "x")
os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "x")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "x")
os.environ.setdefault("SUPABASE_ANON_KEY", "x")

from fastapi.testclient import TestClient

import auth_routes  # noqa: E402
import main  # noqa: E402


INVOICE_ID = "00000000-0000-0000-0000-000000000000"


def _fake_verify(token):
    if token == "ADMIN":
        return {"sub": "admin@example.com", "_role": "admin"}
    if token == "STAFF":
        return {"sub": "staff@example.com", "_role": "staff"}
    return None


class _Query:
    def __init__(self, table_name: str, fixture: dict):
        self.table_name = table_name
        self.fixture = fixture
        self.updated = None
        self.inserted = None

    def select(self, *args, **kwargs):
        return self

    def eq(self, *args, **kwargs):
        return self

    def order(self, *args, **kwargs):
        return self

    def limit(self, *args, **kwargs):
        return self

    def update(self, payload):
        self.updated = payload
        self.fixture.setdefault("_updates", []).append((self.table_name, payload))
        return self

    def insert(self, payload):
        self.inserted = payload
        rows = payload if isinstance(payload, list) else [payload]
        for row in rows:
            self.fixture.setdefault("_inserts", []).append((self.table_name, row))
        return self

    def execute(self):
        data = self.fixture.get(self.table_name, [])
        if self.updated is not None:
            return types.SimpleNamespace(data=[{"id": INVOICE_ID, **self.updated}])
        if self.inserted is not None:
            row = self.inserted[0] if isinstance(self.inserted, list) else self.inserted
            return types.SimpleNamespace(data=[{"id": "inserted", **row}])
        return types.SimpleNamespace(data=data)


class _Supabase:
    def __init__(self, fixture: dict):
        self.fixture = fixture

    def table(self, table_name: str):
        return _Query(table_name, self.fixture)


def _client(monkeypatch, fixture: dict) -> TestClient:
    monkeypatch.setattr(main, "verify_token", _fake_verify)
    monkeypatch.setattr(auth_routes, "verify_token", _fake_verify)
    monkeypatch.setattr(main, "get_supabase", lambda: _Supabase(fixture))
    monkeypatch.setattr(main, "_revalidate_bill", lambda invoice_id: [])
    monkeypatch.setattr(main, "_match_invoice_against_statement", lambda *args, **kwargs: None)
    monkeypatch.setattr(main, "_auto_sync_ingredient_prices", lambda: None)
    return TestClient(main.app, raise_server_exceptions=False)


def test_get_invoice_verification_returns_latest_rows(monkeypatch):
    fixture = {
        "vendor_bills": [{
            "id": INVOICE_ID,
            "verification_status": "mismatch",
            "reconciliation_status": "mismatch",
        }],
        "invoice_ai_verifications": [{
            "id": "ver-1",
            "vendor_bill_id": INVOICE_ID,
            "status": "mismatch",
            "confidence": "0.95",
        }],
        "invoice_reconciliation_results": [{
            "id": "rec-1",
            "vendor_bill_id": INVOICE_ID,
            "status": "mismatch",
            "difference": "13.00",
        }],
    }
    client = _client(monkeypatch, fixture)

    resp = client.get(
        f"/invoice/{INVOICE_ID}/verification",
        headers={"Authorization": "Bearer ADMIN"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["verification"]["status"] == "mismatch"
    assert body["reconciliation"]["status"] == "mismatch"
    assert body["approval_blockers"][0]["code"] == "AI_VERIFIER_MISMATCH"


def test_get_invoice_verification_without_new_rows_returns_frontend_safe_shape(monkeypatch):
    fixture = {
        "vendor_bills": [{
            "id": INVOICE_ID,
            "review_status": "pending",
            "verification_status": None,
            "reconciliation_status": None,
        }],
        "invoice_ai_verifications": [],
        "invoice_reconciliation_results": [],
    }
    client = _client(monkeypatch, fixture)

    resp = client.get(
        f"/invoice/{INVOICE_ID}/verification",
        headers={"Authorization": "Bearer ADMIN"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["verification"] == {}
    assert body["reconciliation"] == {}
    assert body["approval_blockers"] == []


def test_old_invoice_without_verification_rows_confirms_with_legacy_flow(monkeypatch):
    fixture = {"vendor_bills": [{"id": INVOICE_ID, "review_status": "pending"}]}
    client = _client(monkeypatch, fixture)
    monkeypatch.setattr(main, "_load_latest_invoice_review_state", lambda invoice_id: ({}, {}))

    resp = client.post(
        f"/invoice/{INVOICE_ID}/confirm",
        headers={"Authorization": "Bearer ADMIN"},
        json={},
    )

    assert resp.status_code == 200
    assert any(
        table == "vendor_bills" and payload.get("review_status") == "confirmed"
        for table, payload in fixture["_updates"]
    )


def test_confirm_blocks_verification_mismatch_without_force(monkeypatch):
    fixture = {}
    client = _client(monkeypatch, fixture)
    monkeypatch.setattr(
        main,
        "_load_latest_invoice_review_state",
        lambda invoice_id: (
            {"status": "verified"},
            {"status": "mismatch", "blocking": True},
        ),
    )

    resp = client.post(
        f"/invoice/{INVOICE_ID}/confirm",
        headers={"Authorization": "Bearer ADMIN"},
        json={},
    )

    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "CONFIRM_BLOCKED"


def test_confirm_blocks_low_confidence_without_force(monkeypatch):
    fixture = {}
    client = _client(monkeypatch, fixture)
    monkeypatch.setattr(
        main,
        "_load_latest_invoice_review_state",
        lambda invoice_id: (
            {"status": "verified", "confidence": "0.40"},
            {"status": "matched", "blocking": False},
        ),
    )

    resp = client.post(
        f"/invoice/{INVOICE_ID}/confirm",
        headers={"Authorization": "Bearer ADMIN"},
        json={},
    )

    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert detail["code"] == "CONFIRM_BLOCKED"
    assert detail["warnings"][0]["code"] == "LOW_CONFIDENCE"


def test_force_confirm_requires_reason_and_writes_audit_warning(monkeypatch):
    fixture = {"vendor_bills": [{"id": INVOICE_ID}]}
    client = _client(monkeypatch, fixture)
    monkeypatch.setattr(
        main,
        "_load_latest_invoice_review_state",
        lambda invoice_id: (
            {"status": "verified"},
            {"status": "mismatch", "blocking": True},
        ),
    )

    missing_reason = client.post(
        f"/invoice/{INVOICE_ID}/confirm",
        headers={"Authorization": "Bearer ADMIN"},
        json={"force": True},
    )
    assert missing_reason.status_code == 422
    assert missing_reason.json()["detail"]["code"] == "FORCE_REASON_REQUIRED"

    ok = client.post(
        f"/invoice/{INVOICE_ID}/confirm",
        headers={"Authorization": "Bearer ADMIN"},
        json={"force": True, "force_reason": "checked original receipt"},
    )

    assert ok.status_code == 200
    inserted = fixture["_inserts"]
    assert any(
        table == "invoice_validation_warnings"
        and row["code"] == "FORCE_CONFIRMED_VERIFICATION_BLOCK"
        for table, row in inserted
    )


def test_force_confirm_with_reason_is_rejected_for_non_admin(monkeypatch):
    fixture = {"vendor_bills": [{"id": INVOICE_ID}]}
    client = _client(monkeypatch, fixture)

    resp = client.post(
        f"/invoice/{INVOICE_ID}/confirm",
        headers={"Authorization": "Bearer STAFF"},
        json={"force": True, "force_reason": "checked original receipt"},
    )

    assert resp.status_code == 403


def test_patch_reconciliation_accepts_item_payload_dicts(monkeypatch):
    fixture = {
        "vendor_bills": [{
            "id": INVOICE_ID,
            "review_status": "pending",
            "amount": "96.30",
            "vat": "6.30",
        }],
        "invoice_items": [{
            "id": "item-1",
            "vendor_bill_id": INVOICE_ID,
            "line_no": 1,
            "quantity": "1",
            "unit_price": "100.00",
            "amount": "90.00",
        }],
        "invoice_ai_verifications": [],
        "invoice_reconciliation_results": [],
    }
    client = _client(monkeypatch, fixture)

    class _Cursor:
        rowcount = 1

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def execute(self, *args, **kwargs):
            self.rowcount = 1

    class _Conn:
        def cursor(self):
            return _Cursor()

        def commit(self):
            pass

        def rollback(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr(main, "get_db_conn", lambda: _Conn())

    resp = client.patch(
        f"/invoice/{INVOICE_ID}/reconciliation",
        headers={"Authorization": "Bearer ADMIN"},
        json={
            "items": [{
                "line_no": 1,
                "gross_amount": 100.0,
                "line_discount_amount": 10.0,
                "net_amount": 90.0,
            }],
            "tolerance": 0.05,
        },
    )

    assert resp.status_code == 200
    assert any(
        table == "invoice_reconciliation_results"
        for table, _row in fixture["_inserts"]
    )
