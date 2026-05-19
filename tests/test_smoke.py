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
    $env:BACKEND_URL = "https://b4zhad8qkoxjushdq8465056.178.128.31.76.sslip.io"
    pytest tests/test_smoke.py -v

If BACKEND_URL is unset, defaults to the production sslip.io URL above.
"""

from __future__ import annotations

import os

import pytest
import requests

BACKEND_URL = os.environ.get(
    "BACKEND_URL",
    "https://b4zhad8qkoxjushdq8465056.178.128.31.76.sslip.io",
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
    # JWTAuthMiddleware in main.py exempts any path starting with /line/
    # (so LINE Messaging API can call /line/webhook without a Bearer token).
    # Side effect: /line/scheduler/status is publicly readable too.
    # If you want to lock it down, narrow the middleware prefix to
    # /line/webhook explicitly and move this back to AUTHED_ROUTES.
    ("GET", "/line/scheduler/status"),
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

    # Inventory — Phase 32 regression zone
    ("GET", "/inventory/forecast"),
    ("GET", "/inventory/reorder"),
    ("GET", "/inventory/ai-order-advice"),  # Phase 32 victim — must not vanish
    ("GET", "/inventory/current"),
    ("GET", "/inventory/snapshots"),

    # Recipes + Ingredients (Phase 31)
    ("GET", "/recipes"),
    ("GET", "/ingredients"),

    # AR/AP + Bills + Bank statement
    ("GET", "/ar-ap/list"),
    ("GET", "/ar-ap/summary"),
    ("GET", "/bills/payment/summary"),
    ("GET", "/bank-statement/history"),
    ("GET", "/bank-statement/review"),

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
]


@pytest.fixture(scope="session")
def session():
    s = requests.Session()
    s.headers.update({"User-Agent": "vexonhq-smoke/1.0"})
    return s


@pytest.mark.parametrize("method,path", PUBLIC_ROUTES, ids=lambda x: str(x))
def test_public_route_responds(session, method, path):
    """Public routes must return 200."""
    r = session.request(
        method, f"{BACKEND_URL}{path}",
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
    r = session.request(
        method, f"{BACKEND_URL}{path}",
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
    r = session.get(f"{BACKEND_URL}/health/deep", timeout=30)
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
    r = session.get(f"{BACKEND_URL}/openapi.json", timeout=DEFAULT_TIMEOUT)
    assert r.status_code == 200
    spec = r.json()
    count = len(spec.get("paths", {}))
    assert count >= 150, (
        f"Only {count} routes registered (floor: 150). Something major "
        f"may have been deleted. Compare openapi.json to git history."
    )
