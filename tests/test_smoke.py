"""
Smoke tests for VEXONHQ OCR API — catches Phase 32-style endpoint regression.

Background
----------
Session 16 commit 742b618 accidentally deleted the entire
/inventory/ai-order-advice endpoint (165 lines). It went unnoticed until a
user tried to use the feature. These tests would have caught it within
seconds, by hitting each critical route and asserting it's still registered.

Strategy
--------
For each critical route, send an HTTP request. We don't care about the
response *body* — we just check the endpoint EXISTS:

  - Public routes:        200
  - Authed routes:        401 (auth challenge proves route is registered)
  - POST-only routes hit
    with GET:             405 (method exists, route exists)
  - Path-param routes:    401 (auth middleware runs before route matching)

A 404 here = REGRESSION — the endpoint was deleted or renamed.

Usage
-----
    pip install pytest requests
    $env:BACKEND_URL = "https://api.marastation.com"
    pytest tests/test_smoke.py -v

If BACKEND_URL is unset, defaults to the production marastation.com URL above.
(Session 32 migration — previous default was sslip.io fallback URL).
"""

from __future__ import annotations

import os
import time

import pytest
import requests

BACKEND_URL = os.environ.get(
    "BACKEND_URL",
    "https://api.marastation.com",
).rstrip("/")

DEFAULT_TIMEOUT = 20  # seconds; live /health/deep can take ~1s + network

# ──────────────────────────────────────────────────────────
# Public endpoints — should return 200
# ──────────────────────────────────────────────────────────
PUBLIC_ROUTES = [
    ("GET", "/"),
    ("GET", "/health"),
    ("GET", "/health/deep"),
    ("GET", "/openapi.json"),
    # Session 33 Item A_new — public menu data for marastation-web
    # (no JWT; returns recipes with selling_price > 0)
    ("GET", "/menu/public"),
]

# ──────────────────────────────────────────────────────────
# Auth-required endpoints — should return 401 (proves route exists)
#
# Selection criteria: "user-visible critical paths" — if any of these
# silently disappears, real users will notice within hours.
#
# Total ~45 routes, sampled across every major flow.
# ──────────────────────────────────────────────────────────
AUTHED_ROUTES = [
    # OCR pipeline
    ("GET", "/invoice/queue"),
    ("GET", "/invoice/duplicates"),

    # P&L (Session 23 just fixed canonical /pnl/* routes — keep monitored)
    ("GET", "/pnl/daily"),
    ("GET", "/pnl/monthly"),
    ("GET", "/pnl/yearly"),
    ("GET", "/pnl/by-category"),
    ("GET", "/pnl/narrative"),

    # Dashboard + Daybook
    ("GET", "/dashboard/overview"),
    ("GET", "/dashboard/category-trends"),
    ("GET", "/daybook/list"),
    ("GET", "/daybook/summary"),

    # POS analytics (menu_routes.py — largest, most volatile file)
    ("GET", "/pos/heatmap"),
    ("GET", "/pos/menu-engineering"),
    ("GET", "/pos/payments"),
    ("GET", "/pos/bill-analysis"),
    ("GET", "/pos/dow-stats"),
    ("GET", "/pos/hourly-stats"),
    ("GET", "/pos/food-cost"),
    ("GET", "/pos/voids"),
    ("GET", "/pos/discounts"),
    ("GET", "/pos/overview"),
    ("GET", "/pos/prep-forecast"),

    # Inventory — Phase 32 regression zone
    ("GET", "/inventory/forecast"),
    ("GET", "/inventory/reorder"),
    ("GET", "/inventory/ai-order-advice"),  # Phase 32 victim — must not vanish
    ("GET", "/inventory/ai-order-advice/backtest"),  # F8 backtest (Session 53)
    ("GET", "/inventory/current"),
    ("GET", "/inventory/snapshots"),

    # Recipes + Ingredients (Phase 31)
    ("GET", "/recipes"),
    ("GET", "/ingredients"),
    # Selling Price Calculator (RestoSheet gap #15) — channel config + per-recipe forward calc
    ("GET", "/recipes/pricing/channels"),
    ("GET", "/recipes/00000000-0000-0000-0000-000000000000/pricing"),

    # AR/AP + Bills + Bank statement
    ("GET", "/ar-ap/list"),
    ("GET", "/ar-ap/summary"),
    ("GET", "/bills/payment/summary"),
    ("GET", "/bank-statement/history"),
    ("GET", "/bank-statement/review"),

    # Loan ledger (เงินยืม) — Phase 1 (GET without auth → 401 = route exists)
    ("GET", "/loans"),

    # Cashflow + Budget
    ("GET", "/cashflow/forecast"),
    ("GET", "/cashflow/summary"),
    ("GET", "/budgets"),
    ("GET", "/budget/status"),

    # Quick entry + Categories
    ("GET", "/quick-entries/list"),
    ("GET", "/categories/tree"),

    # Stock
    ("GET", "/stock/all"),
    ("GET", "/stock/low"),
    ("GET", "/stock/summary"),

    # Supplier
    ("GET", "/supplier/top"),
    ("GET", "/supplier/summary"),

    # Export
    ("GET", "/export/summary"),
    ("GET", "/export/daybook"),

    # Tax
    ("GET", "/tax/wht-summary"),

    # Auth
    ("GET", "/auth/me"),
    # Session 40 RBAC — page-config endpoints
    ("GET", "/auth/page-config"),
    ("POST", "/auth/page-config"),   # POST without auth = 401 (route exists)

    # LINE bot diagnostics (now authed after Session 24 task O narrowed
    # the middleware prefix — only /line/webhook stays public for LINE
    # Messaging API callbacks)
    ("GET", "/line/scheduler/status"),

    # Discord Interactions endpoint (Session 29 P1.4 v2)
    # — POST-only; GET should return 405 (route exists, wrong method).
    # — A 404 here means discord_routes.py wasn't registered.
    ("GET", "/alerts/discord-interaction"),
    # Manual test trigger — GET without ?secret → 401 (auth fail = route exists)
    ("GET", "/alerts/discord-restart-test"),

    # DO snapshot rotation (Session 31 P2.4) — GET without ?secret → 401
    ("GET", "/snapshots/status"),
    ("GET", "/snapshots/auto-rotate"),

    # Option A + LINE Alert — AI exec endpoint (POST-only → GET returns 405 = route registered)
    ("GET", "/ai/exec"),

    # AI monitoring (JWT-gated → 401 proves route exists). Telemetry (S51) + drift (S54).
    ("GET", "/ai/stats"),
    ("GET", "/ai/drift"),
]


@pytest.fixture(scope="session")
def session():
    s = requests.Session()
    s.headers.update({"User-Agent": "vexonhq-smoke/1.0"})
    return s


def _request_with_retry(session, method, url, **kwargs):
    """
    HTTP request with retry on transient gateway/connection errors.

    Coolify auto-deploy creates a ~20-30s window where the proxy can't
    reach the upstream container, surfacing as 502/503/504. Real outages
    last longer and will still fail (3 attempts x 3s wait = ~9s max).
    """
    last_response = None
    for attempt in range(3):
        try:
            r = session.request(method, url, **kwargs)
            if r.status_code in (502, 503, 504) and attempt < 2:
                last_response = r
                time.sleep(3)
                continue
            return r
        except requests.exceptions.RequestException:
            if attempt < 2:
                time.sleep(3)
                continue
            raise
    return last_response  # 3 attempts all returned 5xx


@pytest.mark.parametrize("method,path", PUBLIC_ROUTES, ids=lambda x: str(x))
def test_public_route_responds(session, method, path):
    """Public routes must return 200."""
    r = _request_with_retry(
        session, method, f"{BACKEND_URL}{path}",
        timeout=DEFAULT_TIMEOUT, allow_redirects=True,
    )
    assert r.status_code == 200, (
        f"{method} {path} -> {r.status_code} (expected 200). "
        f"Body: {r.text[:200]}"
    )


@pytest.mark.parametrize("method,path", AUTHED_ROUTES, ids=lambda x: str(x))
def test_authed_route_exists(session, method, path):
    """
    Authed routes return 401 when no Bearer token. A 404 = REGRESSION.

    Also accept 405 (wrong method but route exists) and 422 (validation
    rejected our params but route exists) — both prove registration.
    """
    r = _request_with_retry(
        session, method, f"{BACKEND_URL}{path}",
        timeout=DEFAULT_TIMEOUT, allow_redirects=False,
    )
    assert r.status_code != 404, (
        f"{method} {path} returned 404 — endpoint may be deleted "
        f"(Phase 32-style regression). Run: git log -S '<function_name>'"
    )
    assert r.status_code in (401, 405, 422), (
        f"{method} {path} -> {r.status_code} (expected 401/405/422). "
        f"Body: {r.text[:200]}"
    )


def test_health_deep_actually_works(session):
    """/health/deep proves DB+Supabase are reachable (P0.1 contract)."""
    r = _request_with_retry(session, "GET", f"{BACKEND_URL}/health/deep", timeout=30)
    assert r.status_code == 200, f"Got {r.status_code}: {r.text[:200]}"
    body = r.json()
    assert body.get("status") in ("healthy", "degraded"), (
        f"Backend status: {body.get('status')} -> DB or Supabase down. "
        f"Full check: {body}"
    )
    checks = body.get("checks", {})
    assert checks.get("postgres", {}).get("ok") is True, (
        f"Postgres check failed: {checks.get('postgres')}"
    )
    assert checks.get("supabase", {}).get("ok") is True, (
        f"Supabase check failed: {checks.get('supabase')}"
    )


def test_openapi_route_count_floor(session):
    """
    Total registered paths should be roughly stable. If count drops a lot,
    investigate — something major has been deleted.

    Snapshot: 171 paths on 2026-05-19. Floor set to 150 to allow gradual
    cleanup, but a sudden drop below 150 is suspicious.
    Adjust this floor over time as the app evolves, but NEVER decrease
    it without investigating — lowering the floor masks regressions.
    """
    r = _request_with_retry(session, "GET", f"{BACKEND_URL}/openapi.json", timeout=DEFAULT_TIMEOUT)
    assert r.status_code == 200
    spec = r.json()
    count = len(spec.get("paths", {}))
    assert count >= 150, (
        f"Only {count} routes registered (floor: 150). Something major "
        f"may have been deleted. Compare openapi.json to git history."
    )
