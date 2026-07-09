"""
Tests for Monthly Close Risk Marking V1 (monthly_close_routes.py).

Spec: docs/superpowers/specs/2026-07-09-monthly-close-risk-marking-v1-design.md

Design lets most behavior be tested as PURE functions (no DB):
  - R1..R5 rule builders          -> synthetic SQL rows
  - plan_risk_sync                -> idempotency / resolve / reopen / LINE cooldown
  - send_danger_line              -> failed push must NOT report success
  - validate_month                -> format validation

Endpoint-level tests use TestClient with a fake verify_token (auth guard) and a
dummy connection with the internal SQL functions monkeypatched (wiring +
failed-LINE-does-not-mark-sent + /alerts/summary integration).

Run: pytest tests/test_monthly_close.py -v
"""
from __future__ import annotations

import os
import sys
from datetime import date, datetime, timedelta, timezone

os.environ.setdefault("DATABASE_URL", "postgresql://u:p@localhost:5432/d")
os.environ.setdefault("JWT_SECRET", "testsecret")
os.environ.setdefault("OPENAI_API_KEY", "x")

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import main
import auth_routes
import menu_routes
import monthly_close_routes as mc


UTC = timezone.utc


def _now() -> datetime:
    return datetime(2026, 7, 9, 12, 0, tzinfo=UTC)


# ═══════════════════════════════════════════════════════════════════════════
# R1 — bank statement needs review (danger)
# ═══════════════════════════════════════════════════════════════════════════

def test_r1_builds_danger_with_count_and_sum():
    rows = [("id1", "SHOPEE PAY", 100.0), ("id2", "TRANSFER", 50.5)]
    r = mc.build_bank_needs_review_risk(rows)
    assert r is not None
    assert r["risk_key"] == "bank_needs_review"
    assert r["severity"] == "danger"
    assert r["evidence"]["count"] == 2
    assert r["evidence"]["sum_abs_amount"] == 150.5
    assert r["amount"] == 150.5
    assert len(r["evidence"]["examples"]) == 2
    assert r["link"] == "/alerts"


def test_r1_none_when_empty():
    assert mc.build_bank_needs_review_risk([]) is None


def test_r1_caps_examples_at_10():
    rows = [(f"id{i}", "d", 1.0) for i in range(25)]
    r = mc.build_bank_needs_review_risk(rows)
    assert r["evidence"]["count"] == 25          # count reflects all rows
    assert len(r["evidence"]["examples"]) == 10   # but examples capped at 10


# ═══════════════════════════════════════════════════════════════════════════
# R2 — bank rows still classified as rider income (danger)
# ═══════════════════════════════════════════════════════════════════════════

def test_r2_danger_for_rider_income():
    rows = [
        ("i1", "GRAB", "rider_income_grab", 500.0, 0.0),
        ("i2", "LINEMAN", "rider_income_lineman", 300.0, 0.0),
    ]
    r = mc.build_bank_rider_income_risk(rows)
    assert r is not None
    assert r["risk_key"] == "bank_rider_income"
    assert r["severity"] == "danger"
    assert r["evidence"]["count"] == 2
    assert r["evidence"]["sum_credit"] == 800.0
    assert r["amount"] == 800.0


def test_r2_none_when_empty():
    assert mc.build_bank_rider_income_risk([]) is None


# ═══════════════════════════════════════════════════════════════════════════
# R3 — POS shows delivery channel but platform export missing (danger)
# ═══════════════════════════════════════════════════════════════════════════

def test_r3_danger_when_pos_exists_and_no_import():
    r = mc.build_missing_platform_export_risk(
        "grab", "Grab", "missing_platform_export_grab",
        pos_count=10, pos_sum=1234.0, import_count=0,
    )
    assert r is not None
    assert r["risk_key"] == "missing_platform_export_grab"
    assert r["severity"] == "danger"
    assert r["evidence"]["pos_count"] == 10
    assert r["evidence"]["matching_import_count"] == 0
    assert r["amount"] == 1234.0


def test_r3_none_when_import_present():
    # POS evidence exists but a platform import also exists -> not a risk
    r = mc.build_missing_platform_export_risk(
        "grab", "Grab", "missing_platform_export_grab",
        pos_count=10, pos_sum=1234.0, import_count=2,
    )
    assert r is None


def test_r3_none_when_no_pos_delivery_evidence():
    # No POS delivery evidence -> not a risk even with zero imports
    r = mc.build_missing_platform_export_risk(
        "lineman", "LINE MAN", "missing_platform_export_lineman",
        pos_count=0, pos_sum=0.0, import_count=0,
    )
    assert r is None


# ═══════════════════════════════════════════════════════════════════════════
# R4 — ambiguous settlement keywords (warning, never LINE)
# ═══════════════════════════════════════════════════════════════════════════

def test_r4_warning_and_not_line():
    rows = [("i1", "LINE PAY 12345", "bank_statement", 120.0)]
    r = mc.build_ambiguous_settlement_risk(rows)
    assert r is not None
    assert r["risk_key"] == "ambiguous_settlement"
    assert r["severity"] == "warning"
    assert r["amount"] == 120.0
    # A warning must never become a LINE target.
    plan = mc.plan_risk_sync([], [r], _now())
    assert plan["line_targets"] == []


def test_r4_none_when_empty():
    assert mc.build_ambiguous_settlement_risk([]) is None


def test_r4_sql_handles_null_source_type():
    sql_source = open(mc.__file__, encoding="utf-8").read()
    assert "COALESCE(source_type, '') NOT IN" in sql_source
    assert "AND source_type NOT IN ('grab_payout'" not in sql_source


# ═══════════════════════════════════════════════════════════════════════════
# R5 — duplicate statement rows (warning)
# ═══════════════════════════════════════════════════════════════════════════

def test_r5_warning_for_duplicates():
    groups = [
        (date(2026, 7, 1), "DUP ROW", 100.0, 0.0, 5000.0, "thawi_watthana", 3),
        (date(2026, 7, 5), "DUP TWO", 0.0, 250.0, 6000.0, "thawi_watthana", 2),
    ]
    r = mc.build_duplicate_statement_risk(groups)
    assert r is not None
    assert r["risk_key"] == "duplicate_statement"
    assert r["severity"] == "warning"
    assert r["evidence"]["duplicate_group_count"] == 2
    assert r["evidence"]["duplicate_row_count"] == 5            # 3 + 2
    # extra copies: (3-1)*100 + (2-1)*250 = 200 + 250 = 450
    assert r["amount"] == 450.0
    assert r["evidence"]["total_duplicate_amount"] == 450.0


def test_r5_none_when_empty():
    assert mc.build_duplicate_statement_risk([]) is None


# ═══════════════════════════════════════════════════════════════════════════
# Month validation
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("bad", ["2026-13", "202607", "2026-7", "", "bad", "2026-00", "2026-7a"])
def test_validate_month_rejects_invalid(bad):
    with pytest.raises(HTTPException) as e:
        mc.validate_month(bad)
    assert e.value.status_code == 400


def test_validate_month_accepts_valid():
    assert mc.validate_month("2026-07") == (date(2026, 7, 1), date(2026, 7, 31))
    assert mc.validate_month("2026-02") == (date(2026, 2, 1), date(2026, 2, 28))  # 2026 not leap
    assert mc.validate_month("2026-12") == (date(2026, 12, 1), date(2026, 12, 31))


# ═══════════════════════════════════════════════════════════════════════════
# plan_risk_sync — idempotency / resolve / reopen / LINE cooldown
# ═══════════════════════════════════════════════════════════════════════════

def _danger():
    return mc.build_bank_needs_review_risk([("i", "d", 100.0)])


def test_idempotency_no_duplicate_and_line_once():
    now = _now()
    detected = [_danger()]

    # First run: nothing stored -> upsert once, LINE first time.
    plan1 = mc.plan_risk_sync([], detected, now)
    assert len(plan1["upserts"]) == 1
    assert plan1["resolve_keys"] == []
    assert len(plan1["line_targets"]) == 1

    # Second run 1h later: risk now open with last_line_sent_at set.
    existing = [{"risk_key": "bank_needs_review", "status": "open", "last_line_sent_at": now}]
    plan2 = mc.plan_risk_sync(existing, detected, now + timedelta(hours=1))
    assert len(plan2["upserts"]) == 1          # ON CONFLICT upsert, no duplicate row
    assert plan2["resolve_keys"] == []
    assert plan2["line_targets"] == []          # within 24h -> no repeat LINE


def test_existing_risk_becomes_resolved_when_absent():
    now = _now()
    existing = [{"risk_key": "bank_needs_review", "status": "open", "last_line_sent_at": now}]
    plan = mc.plan_risk_sync(existing, [], now)
    assert plan["resolve_keys"] == ["bank_needs_review"]
    assert plan["upserts"] == []
    assert plan["line_targets"] == []


def test_reopen_preserves_cooldown_no_line_spam():
    now = _now()
    detected = [_danger()]
    # Risk had been resolved but retains last_line_sent_at from 1h ago.
    existing = [{
        "risk_key": "bank_needs_review",
        "status": "resolved",
        "last_line_sent_at": now - timedelta(hours=1),
    }]
    plan = mc.plan_risk_sync(existing, detected, now)
    assert len(plan["upserts"]) == 1        # reopened via upsert
    assert plan["resolve_keys"] == []        # was 'resolved', not 'open'
    assert plan["line_targets"] == []        # cooldown still active -> no LINE


def test_line_sends_danger_first_time():
    now = _now()
    plan = mc.plan_risk_sync([], [_danger()], now)
    assert len(plan["line_targets"]) == 1


def test_line_no_resend_within_24h():
    now = _now()
    existing = [{"risk_key": "bank_needs_review", "status": "open",
                 "last_line_sent_at": now - timedelta(hours=23)}]
    plan = mc.plan_risk_sync(existing, [_danger()], now)
    assert plan["line_targets"] == []


def test_line_resends_after_24h():
    now = _now()
    existing = [{"risk_key": "bank_needs_review", "status": "open",
                 "last_line_sent_at": now - timedelta(hours=25)}]
    plan = mc.plan_risk_sync(existing, [_danger()], now)
    assert len(plan["line_targets"]) == 1


# ═══════════════════════════════════════════════════════════════════════════
# send_danger_line — success / failure / empty
# ═══════════════════════════════════════════════════════════════════════════

def test_send_danger_line_success_builds_message():
    captured = {}
    ok = mc.send_danger_line([_danger()], "2026-07", "thawi_watthana",
                             push_fn=lambda msg: captured.setdefault("msg", msg))
    assert ok is True
    assert "Monthly Close Critical Risk" in captured["msg"]
    assert "เดือน: 2026-07" in captured["msg"]
    assert "สาขา: thawi_watthana" in captured["msg"]
    assert "เปิดดู: /alerts" in captured["msg"]


def test_send_danger_line_failure_returns_false():
    def boom(msg):
        raise RuntimeError("LINE down")
    ok = mc.send_danger_line([_danger()], "2026-07", "thawi_watthana", push_fn=boom)
    assert ok is False


def test_send_danger_line_empty_does_not_push():
    calls = []
    ok = mc.send_danger_line([], "2026-07", "thawi_watthana", push_fn=lambda m: calls.append(m))
    assert ok is False
    assert calls == []


# ═══════════════════════════════════════════════════════════════════════════
# Endpoint wiring — dummy conn + fake auth
# ═══════════════════════════════════════════════════════════════════════════

class _DummyConn:
    def cursor(self):
        raise AssertionError("cursor must not be used when internals are monkeypatched")

    def commit(self):
        pass

    def close(self):
        pass


def _fake_verify(token):
    if token == "ADMIN":
        return {"sub": "admin-uid", "_role": "admin"}
    if token == "STAFF":
        return {"sub": "staff-uid", "_role": "staff"}
    return None


def _client(monkeypatch):
    monkeypatch.setattr(main, "verify_token", _fake_verify)
    monkeypatch.setattr(auth_routes, "verify_token", _fake_verify)
    return TestClient(main.app, raise_server_exceptions=False)


# ── Auth guard: 401 (no token) / 403 (non-admin) ──

@pytest.mark.parametrize("method,path", [
    ("POST", "/monthly-close/check?month=2026-07"),
    ("GET", "/monthly-close/risks?month=2026-07"),
])
def test_auth_no_token_is_401(monkeypatch, method, path):
    client = _client(monkeypatch)
    resp = client.request(method, path)
    assert resp.status_code == 401


@pytest.mark.parametrize("method,path", [
    ("POST", "/monthly-close/check?month=2026-07"),
    ("GET", "/monthly-close/risks?month=2026-07"),
])
def test_auth_staff_is_403(monkeypatch, method, path):
    client = _client(monkeypatch)
    resp = client.request(method, path, headers={"Authorization": "Bearer STAFF"})
    assert resp.status_code == 403


# ── Failed LINE push must NOT mark last_line_sent_at ──

def test_check_failed_line_does_not_mark_sent(monkeypatch):
    client = _client(monkeypatch)

    monkeypatch.setattr(mc, "get_db_conn", lambda: _DummyConn())
    monkeypatch.setattr(mc, "run_all_checks", lambda conn, a, b, c: [_danger()])
    monkeypatch.setattr(mc, "_fetch_existing", lambda conn, m, b: [])
    monkeypatch.setattr(mc, "_apply_upserts", lambda *a, **k: None)
    monkeypatch.setattr(mc, "_apply_resolves", lambda *a, **k: None)
    monkeypatch.setattr(mc, "_fetch_risks", lambda conn, m, b, status: [])

    marked = {"called": False}
    monkeypatch.setattr(mc, "_mark_line_sent",
                        lambda *a, **k: marked.__setitem__("called", True))
    # LINE push raises -> send_danger_line returns False
    import line_bot_routes
    def boom(msg):
        raise RuntimeError("LINE 500")
    monkeypatch.setattr(line_bot_routes, "_push_text", boom)

    resp = client.post("/monthly-close/check?month=2026-07",
                       headers={"Authorization": "Bearer ADMIN"})
    assert resp.status_code == 200
    assert resp.json()["line_sent"] is False
    assert marked["called"] is False


def test_check_successful_line_marks_sent(monkeypatch):
    client = _client(monkeypatch)

    monkeypatch.setattr(mc, "get_db_conn", lambda: _DummyConn())
    monkeypatch.setattr(mc, "run_all_checks", lambda conn, a, b, c: [_danger()])
    monkeypatch.setattr(mc, "_fetch_existing", lambda conn, m, b: [])
    monkeypatch.setattr(mc, "_apply_upserts", lambda *a, **k: None)
    monkeypatch.setattr(mc, "_apply_resolves", lambda *a, **k: None)
    monkeypatch.setattr(mc, "_fetch_risks", lambda conn, m, b, status: [])

    marked = {"keys": None}
    monkeypatch.setattr(mc, "_mark_line_sent",
                        lambda conn, m, b, keys, now: marked.__setitem__("keys", list(keys)))
    import line_bot_routes
    monkeypatch.setattr(line_bot_routes, "_push_text", lambda msg: {"ok": True})

    resp = client.post("/monthly-close/check?month=2026-07",
                       headers={"Authorization": "Bearer ADMIN"})
    assert resp.status_code == 200
    assert resp.json()["line_sent"] is True
    assert marked["keys"] == ["bank_needs_review"]


def test_check_invalid_month_is_400(monkeypatch):
    client = _client(monkeypatch)
    monkeypatch.setattr(mc, "get_db_conn", lambda: _DummyConn())
    resp = client.post("/monthly-close/check?month=2026-13",
                       headers={"Authorization": "Bearer ADMIN"})
    assert resp.status_code == 400


# ═══════════════════════════════════════════════════════════════════════════
# /alerts/summary integration — open monthly close risks appear
# ═══════════════════════════════════════════════════════════════════════════

def test_alerts_summary_includes_monthly_close(monkeypatch):
    client = _client(monkeypatch)
    monkeypatch.setattr(menu_routes, "get_db_conn", lambda: _DummyConn())

    def fake_rows(conn, sql=None, params=None):
        if sql and "monthly_close_risks" in sql:
            return [{
                "risk_key": "bank_needs_review",
                "branch_code": "thawi_watthana",
                "month": "2026-07",
                "severity": "danger",
                "title": "Statement รอจัดหมวด",
                "message": "Statement รอจัดหมวด 8 รายการ / ฿12,345",
                "amount": 12345.0,
                "link": "/alerts",
            }]
        return []

    monkeypatch.setattr(menu_routes, "_rows_to_dicts", fake_rows)

    resp = client.get("/alerts/summary", headers={"Authorization": "Bearer STAFF"})
    assert resp.status_code == 200
    data = resp.json()
    mc_alerts = [a for a in data["alerts"] if a["type"] == "monthly_close"]
    assert len(mc_alerts) == 1
    assert mc_alerts[0]["id"] == "mclose_thawi_watthana_2026-07_bank_needs_review"
    assert mc_alerts[0]["date"] == "2026-07"
    assert mc_alerts[0]["severity"] == "danger"
    assert mc_alerts[0]["link"] == "/alerts"
    assert data["counts"]["danger"] >= 1
