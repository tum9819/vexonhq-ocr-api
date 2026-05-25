"""
VEXONHQ Workflow Tests — authenticated end-to-end quality checks.
=================================================================

ต่างจาก test_smoke.py ที่แค่ check ว่า route ยัง exist (401 = alive),
test นี้ login จริง และ verify ว่า:
  - Data ถูกต้อง (GP หลัง fix, ingredient prices)
  - RBAC ทำงาน (role ใน JWT, page-config admin)
  - Cron jobs กำลัง track อยู่
  - Pages สำคัญ return ข้อมูลจริง (ไม่ใช่ empty)

Setup (ทำใน PowerShell ก่อน run):
    $env:VEXONHQ_TEST_PASS = "รหัสผ่าน_ของ_tum"    # ห้ามใส่ใน chat

Optional overrides:
    $env:VEXONHQ_TEST_USER = "vexonhq"              # default "vexonhq"
    $env:BACKEND_URL = "https://api.marastation.com" # default ↑

Run:
    python -m pytest tests/test_workflow.py -v
    python -m pytest tests/test_workflow.py -v -k "gp"        # GP tests only
    python -m pytest tests/test_workflow.py -v -k "auth"       # Auth tests only
    python -m pytest tests/test_workflow.py -v -k "cron"       # Cron tests only
"""

from __future__ import annotations

import base64
import json
import os
import time

import pytest
import requests

# ─────────────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────────────
BACKEND_URL = os.environ.get("BACKEND_URL", "https://api.marastation.com").rstrip("/")
TEST_USER   = os.environ.get("VEXONHQ_TEST_USER", "vexonhq")
TEST_PASS   = os.environ.get("VEXONHQ_TEST_PASS", "")

_NO_CREDS_MSG = (
    "VEXONHQ_TEST_PASS not set — run: $env:VEXONHQ_TEST_PASS = '<password>'"
)

DEFAULT_TIMEOUT = 30  # seconds


# ─────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def http():
    s = requests.Session()
    s.headers["User-Agent"] = "vexonhq-workflow-test/1.0"
    return s


@pytest.fixture(scope="session")
def admin_token(http):
    """Login and return access_token. Skip all if password not set.

    Retries up to 3x with 5s backoff to handle Coolify redeploy windows
    (502/503 during the ~30s deploy). Without retry, one failed login causes
    all auth-dependent tests to ERROR rather than skip or fail cleanly.
    """
    if not TEST_PASS:
        pytest.skip(_NO_CREDS_MSG)

    import requests as _req

    last_err: str = ""
    for attempt in range(3):
        try:
            r = http.post(
                f"{BACKEND_URL}/auth/login",
                json={"username": TEST_USER, "password": TEST_PASS},
                timeout=DEFAULT_TIMEOUT,
            )
            if r.status_code in (502, 503, 504):
                last_err = f"Backend unavailable ({r.status_code}) on attempt {attempt + 1}/3"
                time.sleep(5)
                continue
            assert r.status_code == 200, (
                f"WF-1 Login failed ({r.status_code}): {r.text[:300]}"
            )
            data = r.json()
            assert "access_token" in data, f"No access_token in login response: {data}"
            return data["access_token"]
        except _req.exceptions.ConnectionError as exc:
            last_err = f"ConnectionError on attempt {attempt + 1}/3: {exc}"
            time.sleep(5)

    pytest.fail(
        f"Could not login after 3 attempts (backend may still be deploying). "
        f"Last error: {last_err}"
    )


@pytest.fixture(scope="session")
def auth_headers(admin_token):
    return {"Authorization": f"Bearer {admin_token}"}


def _get(http, path, headers=None, **kw):
    """GET helper with default timeout."""
    return http.get(f"{BACKEND_URL}{path}", headers=headers, timeout=DEFAULT_TIMEOUT, **kw)


def _post(http, path, headers=None, **kw):
    """POST helper with default timeout."""
    return http.post(f"{BACKEND_URL}{path}", headers=headers, timeout=DEFAULT_TIMEOUT, **kw)


# ─────────────────────────────────────────────────────────────────
# WF-1: Authentication flows
# ─────────────────────────────────────────────────────────────────

class TestWF1Auth:
    """Login + JWT + wrong-password rejection."""

    def test_login_response_fields(self, http, admin_token):
        """Login returns all required fields."""
        # admin_token fixture already verified login succeeded
        # Re-login to check response shape
        r = http.post(
            f"{BACKEND_URL}/auth/login",
            json={"username": TEST_USER, "password": TEST_PASS},
            timeout=DEFAULT_TIMEOUT,
        )
        data = r.json()
        for field in ("access_token", "token_type", "expires_in", "username"):
            assert field in data, f"Login response missing '{field}': {data}"
        assert data["token_type"] == "bearer"
        assert data["expires_in"] > 0
        # Username returned should match what we sent (possibly different case)
        assert data["username"].lower() == TEST_USER.lower()

    def test_jwt_has_role_and_sub(self, admin_token):
        """JWT payload must contain 'sub' and 'role' fields."""
        parts = admin_token.split(".")
        assert len(parts) == 3, f"Not a valid JWT (expected 3 parts, got {len(parts)})"
        # base64url decode with padding
        padded = parts[1] + "=" * (4 - len(parts[1]) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded))
        assert "sub" in payload,  f"JWT missing 'sub': {payload}"
        assert "role" in payload, f"JWT missing 'role' — RBAC not in token: {payload}"
        assert payload["role"] == "admin", (
            f"Expected role=admin (user '{TEST_USER}' should be in VEXON_ADMINS env), "
            f"got role='{payload['role']}'"
        )

    def test_auth_me_returns_correct_role(self, http, auth_headers):
        """/auth/me returns user identifier and role=admin.

        Accepts either 'username' or 'sub' as the user identifier field
        (Session 40 changed /auth/me to return 'sub' instead of 'username').
        """
        r = _get(http, "/auth/me", headers=auth_headers)
        assert r.status_code == 200, f"/auth/me failed: {r.status_code} {r.text[:200]}"
        data = r.json()
        assert data.get("role") == "admin", f"/auth/me role mismatch: {data}"
        user_id = data.get("username") or data.get("sub")
        assert user_id, f"/auth/me missing user identifier (username or sub): {data}"

    def test_wrong_password_is_rejected(self, http):
        """Wrong password must return 401 — not crash or accept."""
        r = http.post(
            f"{BACKEND_URL}/auth/login",
            json={"username": TEST_USER, "password": "WRONG_XYZ_999"},
            timeout=DEFAULT_TIMEOUT,
        )
        assert r.status_code == 401, (
            f"Expected 401 for wrong password, got {r.status_code}. "
            f"Auth may be misconfigured."
        )

    def test_missing_bearer_returns_401(self, http):
        """/auth/me without token must return 401."""
        r = _get(http, "/auth/me")  # no Authorization header
        assert r.status_code == 401, f"Expected 401, got {r.status_code}"

    def test_expired_token_returns_401(self, http):
        """A clearly fake token must return 401."""
        r = _get(http, "/auth/me", headers={"Authorization": "Bearer fake.token.here"})
        assert r.status_code == 401, f"Expected 401 for fake token, got {r.status_code}"


# ─────────────────────────────────────────────────────────────────
# WF-2: RBAC — role-based page access
# ─────────────────────────────────────────────────────────────────

class TestWF2RBAC:
    """Admin role can access page-config; can toggle page visibility."""

    def test_page_config_admin_sees_all(self, http, auth_headers):
        """Admin /auth/page-config returns role=admin + empty pages (= sees all)."""
        r = _get(http, "/auth/page-config", headers=auth_headers)
        assert r.status_code == 200, f"page-config failed: {r.status_code} {r.text[:200]}"
        data = r.json()
        assert data.get("role") == "admin", f"Expected role=admin: {data}"
        # Admin gets empty pages dict (frontend shows everything)
        assert isinstance(data.get("pages"), dict), f"Expected 'pages' dict: {data}"

    def test_page_config_toggle_write_read(self, http, auth_headers):
        """Admin can toggle a page on/off and read back the change."""
        test_page = "/cashflow"  # non-critical page safe to toggle

        # Toggle OFF
        r = _post(http, "/auth/page-config", headers=auth_headers,
                  json={"page_href": test_page, "user_visible": False})
        assert r.status_code == 200, f"POST page-config failed: {r.status_code} {r.text[:200]}"
        assert r.json().get("ok") is True

        # Toggle back ON (restore)
        r2 = _post(http, "/auth/page-config", headers=auth_headers,
                   json={"page_href": test_page, "user_visible": True})
        assert r2.status_code == 200
        assert r2.json().get("user_visible") is True

    def test_page_config_non_admin_forbidden(self, http):
        """POST /auth/page-config without admin token returns 401."""
        r = _post(http, "/auth/page-config",
                  json={"page_href": "/cashflow", "user_visible": True})
        assert r.status_code == 401, (
            f"Expected 401 for unauthenticated POST /auth/page-config, "
            f"got {r.status_code}"
        )


# ─────────────────────────────────────────────────────────────────
# WF-3: GP data quality — verify Session 40 ingredient price fixes
# ─────────────────────────────────────────────────────────────────

class TestWF3GPQuality:
    """
    After Session 40 data fixes:
      - 9 ingredient price_per_unit corrected from selling price → purchase cost
      - C031 composite ingredient created + linked (GP 26.6%)
      - Expected: 0 recipes with GP = 0% (except modifier items with 0 links)
    """

    @pytest.fixture(scope="class")
    def recipes(self, http, auth_headers):
        r = _get(http, "/recipes", headers=auth_headers)
        assert r.status_code == 200, f"/recipes failed: {r.status_code}"
        data = r.json()
        assert "recipes" in data, f"Unexpected /recipes format: {list(data.keys())}"
        return {rec["name"]: rec for rec in data["recipes"]}

    @pytest.fixture(scope="class")
    def ingredients(self, http, auth_headers):
        r = _get(http, "/ingredients", headers=auth_headers)
        assert r.status_code == 200, f"/ingredients failed: {r.status_code}"
        data = r.json()
        assert "ingredients" in data, f"Unexpected /ingredients format: {list(data.keys())}"
        return {ing["name"]: ing for ing in data["ingredients"]}

    # ── Ingredient price corrections ──

    def test_mirindat_daeng_price_corrected(self, ingredients):
        """มิรินด้า-แดง: price_per_unit must be < ฿15 (was ฿15 = selling price)."""
        ing = self._find(ingredients, "มิรินด้า-แดง")
        if ing is None:
            pytest.skip("มิรินด้า-แดง not found in ingredients")
        price = float(ing["price_per_unit"] or 0)
        assert price < 15, (
            f"มิรินด้า-แดง price_per_unit = {price} บาท — still at selling price? "
            f"Expected ~9.16 (฿219.84/24)"
        )
        assert price > 5, f"มิรินด้า-แดง price {price} seems unrealistically low"

    def test_mirindat_som_price_corrected(self, ingredients):
        """มิรินด้า-ส้ม: price_per_unit must be < ฿15."""
        ing = self._find(ingredients, "มิรินด้า-ส้ม")
        if ing is None:
            pytest.skip("มิรินด้า-ส้ม not found in ingredients")
        price = float(ing["price_per_unit"] or 0)
        assert price < 15, (
            f"มิรินด้า-ส้ม price_per_unit = {price} — still at selling price?"
        )

    def test_moo_manao_price_corrected(self, ingredients):
        """หมูมะนาว: price_per_unit must be < ฿99 (was ฿99 = selling price)."""
        ing = self._find(ingredients, "หมูมะนาว")
        if ing is None:
            pytest.skip("หมูมะนาว not found in ingredients")
        price = float(ing["price_per_unit"] or 0)
        assert price < 99, (
            f"หมูมะนาว price_per_unit = {price} — still at selling price ฿99?"
        )
        assert price > 20, f"หมูมะนาว price {price} seems unrealistically low"

    def test_thod_group_price_corrected(self, ingredients):
        """ทอดกลุ่ม (ใส้ทอด/เอ็นข้อไก่/ปีกไก่/นักเก็ต/ไส้กรอกอีสาน): < ฿89."""
        thod_items = ["ใส้ทอด", "เอ็นข้อไก่", "ปีกไก่", "นักเก็ต", "ไส้กรอกอีสาน"]
        found_any = False
        for name in thod_items:
            ing = self._find(ingredients, name)
            if ing is None:
                continue
            found_any = True
            price = float(ing["price_per_unit"] or 0)
            assert price < 89, (
                f"{name} price_per_unit = {price} — still at selling price ฿89?"
                f" Expected ~60 (TUM's actual purchase cost)"
            )
        if not found_any:
            pytest.skip("None of ทอดกลุ่ม ingredients found")

    # ── Recipe GP checks ──

    def test_no_recipes_with_zero_gp_and_links(self, http, auth_headers):
        """
        No recipe with ≥1 ingredient link should have GP = 0%.
        GP = 0% with links means price_per_unit was set to selling price.
        Modifier items (0 links) are expected to have GP = 100% or None.
        """
        r = _get(http, "/recipes", headers=auth_headers)
        data = r.json()
        zero_gp_with_links = [
            rec for rec in data["recipes"]
            if rec.get("ingredient_count", 0) > 0
            and rec.get("gp_pct") is not None
            and abs(float(rec.get("gp_pct", 1))) < 0.1  # GP == 0%
        ]
        assert len(zero_gp_with_links) == 0, (
            f"Found {len(zero_gp_with_links)} recipes with GP=0% and >0 ingredient links "
            f"(ingredient price probably still = selling price):\n"
            + "\n".join(
                f"  {r['name']} (sell={r.get('selling_price')}, cost={r.get('cost_per_dish')}, "
                f"links={r.get('ingredient_count')})"
                for r in zero_gp_with_links[:10]
            )
        )

    def test_c031_yamwunsen_has_gp(self, recipes):
        """C031 ยำวุ้นเส้นโบราณ must have GP > 0% (composite ingredient linked Session 40).
        Search uses 'โบราณ' to avoid matching C008 ยำวุ้นเส้นหมูสับ (same fragment 'ยำวุ้นเส้น').
        """
        rec = self._find(recipes, "โบราณ")
        if rec is None:
            pytest.skip("ยำวุ้นเส้นโบราณ recipe not found")
        gp = rec.get("gp_pct")
        assert gp is not None and gp > 0, (
            f"C031 ยำวุ้นเส้นโบราณ GP = {gp}% (expected ~26.6% after composite ingredient fix)"
        )
        # sell=109, cost=80 → GP ~26.6%. Upper bound 50% to catch if price was entered wrong.
        assert gp < 50, (
            f"C031 GP {gp}% seems too high (sell=109, composite cost should be ~80). "
            f"Check price_per_unit of 'วัตถุดิบรวม C031 ยำวุ้นเส้นโบราณ' ingredient."
        )

    def test_yam_moo_group_gp_reasonable(self, recipes):
        """ยำ ฿99 group (หมูมะนาว, ยำหมูกรอบ) should have GP ~30-50%."""
        for name_fragment in ["หมูมะนาว", "ยำหมูกรอบ"]:
            rec = self._find(recipes, name_fragment)
            if rec is None:
                continue
            gp = rec.get("gp_pct")
            if gp is None:
                continue  # no links → skip
            assert gp > 0, f"{rec['name']} GP = {gp}% (expected >0 after price fix)"
            assert gp < 80, f"{rec['name']} GP = {gp}% seems unusually high"

    def test_mirindat_recipes_gp_reasonable(self, recipes):
        """มิรินด้า recipes (D029/D030) should have GP > 30% after price fix."""
        for name_fragment in ["มิรินด้า-แดง", "มิรินด้า-ส้ม"]:
            rec = self._find(recipes, name_fragment)
            if rec is None:
                continue
            gp = rec.get("gp_pct")
            if gp is None:
                continue
            assert gp > 30, (
                f"{rec['name']} GP = {gp}% (expected ~38.9% after price fix from ฿15→฿9.16)"
            )

    def test_recipe_count_reasonable(self, http, auth_headers):
        """Total recipe count should be > 50 (system has real menu data)."""
        r = _get(http, "/recipes", headers=auth_headers)
        data = r.json()
        count = data.get("count", 0)
        assert count > 50, (
            f"Only {count} recipes — expected 100+ for a real restaurant menu. "
            f"Check if recipes table is populated."
        )

    @staticmethod
    def _find(d: dict, fragment: str):
        """Find a dict entry whose key contains `fragment` (case-insensitive)."""
        fragment_lower = fragment.lower()
        for key, val in d.items():
            if fragment_lower in key.lower():
                return val
        return None


# ─────────────────────────────────────────────────────────────────
# WF-4: Core data presence — real restaurant data must be there
# ─────────────────────────────────────────────────────────────────

class TestWF4DataPresence:
    """
    System has months of real POS + invoice + bank data.
    Every major page should return real rows, not empty.
    """

    def test_dashboard_overview_has_data(self, http, auth_headers):
        r = _get(http, "/dashboard/overview", headers=auth_headers)
        assert r.status_code == 200, f"dashboard/overview: {r.status_code} {r.text[:200]}"
        assert r.json(), "Empty dashboard overview response"

    def test_pnl_monthly_has_rows(self, http, auth_headers):
        # /pnl/monthly requires ?year=YYYY
        r = _get(http, "/pnl/monthly?year=2026", headers=auth_headers)
        assert r.status_code == 200, f"pnl/monthly: {r.status_code} {r.text[:200]}"
        data = r.json()
        rows = data if isinstance(data, list) else data.get("rows", data.get("months", [data]))
        assert len(rows) > 0, "P&L monthly returned no rows for 2026"

    def test_pnl_by_category_has_data(self, http, auth_headers):
        # /pnl/by-category requires ?month=YYYY-MM
        r = _get(http, "/pnl/by-category?month=2026-04", headers=auth_headers)
        assert r.status_code == 200, f"pnl/by-category: {r.status_code} {r.text[:200]}"
        assert r.json(), "Empty pnl/by-category response for 2026-04"

    def test_pos_overview_has_bills(self, http, auth_headers):
        r = _get(http, "/pos/overview", headers=auth_headers)
        assert r.status_code == 200, f"pos/overview: {r.status_code} {r.text[:200]}"
        data = r.json()
        # Should have bills (restaurant has real POS data)
        assert data, "Empty POS overview"

    def test_pos_food_cost_returns_data(self, http, auth_headers):
        """GP data endpoint must respond with actual content."""
        r = _get(http, "/pos/food-cost", headers=auth_headers)
        assert r.status_code == 200, f"pos/food-cost: {r.status_code} {r.text[:200]}"
        assert r.json(), "Empty pos/food-cost response"

    def test_inventory_current_has_items(self, http, auth_headers):
        r = _get(http, "/inventory/current", headers=auth_headers)
        assert r.status_code == 200, f"inventory/current: {r.status_code} {r.text[:200]}"
        data = r.json()
        assert data, "Empty inventory response"

    def test_ingredients_count_reasonable(self, http, auth_headers):
        """Ingredients list must have real data (>20 entries)."""
        r = _get(http, "/ingredients", headers=auth_headers)
        assert r.status_code == 200, f"ingredients: {r.status_code}"
        data = r.json()
        count = data.get("count", len(data.get("ingredients", [])))
        assert count > 20, f"Only {count} ingredients — expected 50+ for a real restaurant"

    def test_prep_forecast_responds(self, http, auth_headers):
        """/pos/prep-forecast is a key daily-use page — must return data."""
        r = _get(http, "/pos/prep-forecast", headers=auth_headers)
        assert r.status_code == 200, f"prep-forecast: {r.status_code} {r.text[:200]}"

    def test_stock_summary_has_items(self, http, auth_headers):
        r = _get(http, "/stock/summary", headers=auth_headers)
        assert r.status_code == 200, f"stock/summary: {r.status_code} {r.text[:200]}"
        data = r.json()
        assert data, "Empty stock/summary response"


# ─────────────────────────────────────────────────────────────────
# WF-5: Cron job health
# ─────────────────────────────────────────────────────────────────

class TestWF5CronHealth:
    """Scheduled jobs must be tracked in job_heartbeat."""

    @pytest.fixture(scope="class")
    def cron_data(self, http):
        r = _get(http, "/cron/health")
        assert r.status_code in (200, 503), (
            f"/cron/health returned {r.status_code} — DB may be unreachable: {r.text[:200]}"
        )
        data = r.json()
        assert "jobs" in data, f"Unexpected /cron/health format: {data}"
        return {j["job_id"]: j for j in data["jobs"]}

    def test_expected_jobs_all_registered(self, cron_data):
        """All 4 scheduled jobs must appear in job_heartbeat."""
        expected = [
            "daily_stock_digest",
            "weekly_summary",
            "weekly_do_snapshot",
            "daily_budget_alert",
        ]
        for job_id in expected:
            assert job_id in cron_data, (
                f"Job '{job_id}' not found in /cron/health. "
                f"Registered jobs: {list(cron_data.keys())}"
            )

    def test_daily_stock_digest_has_run(self, cron_data):
        """daily_stock_digest fires every day at 07:00 BKK — must have run."""
        job = cron_data.get("daily_stock_digest", {})
        run_count = job.get("run_count", 0)
        assert run_count >= 1, (
            f"daily_stock_digest run_count={run_count} — job has never fired. "
            f"Check APScheduler + line_bot_routes.py"
        )
        assert job.get("last_success_at") is not None, (
            f"daily_stock_digest has never succeeded (last_success_at=null). "
            f"Last error: {job.get('last_error_message')}"
        )

    def test_weekly_snapshot_job_is_tracked(self, cron_data):
        """weekly_do_snapshot must be tracked (run_count >= 1 = has attempted)."""
        job = cron_data.get("weekly_do_snapshot", {})
        run_count = job.get("run_count", 0)
        assert run_count >= 1, (
            f"weekly_do_snapshot never ran (run_count=0). "
            f"APScheduler may not be registering this job."
        )
        # Note: after DO token fix (Session 40), next success = Sunday 2026-06-01
        # error_count = 1 is expected until then — don't assert error_count == 0 yet

    def test_no_jobs_are_stale(self, cron_data):
        """
        No job should be 'stale' (last_run > 2× expected_interval).
        Stale = job is silently failing to run on schedule.
        """
        stale_jobs = [j for j in cron_data.values() if j.get("stale") is True]
        if stale_jobs:
            details = "\n".join(
                f"  {j['job_id']}: last_run={j.get('last_run_at')}, "
                f"expected_interval={j.get('expected_interval_hours')}h, "
                f"minutes_since={j.get('minutes_since_last_run')}"
                for j in stale_jobs
            )
            pytest.fail(
                f"{len(stale_jobs)} stale job(s) detected:\n{details}\n"
                f"Stale = job hasn't run in >2× its expected interval. "
                f"Check APScheduler logs in Coolify."
            )


# ─────────────────────────────────────────────────────────────────
# WF-6: Public menu endpoint (for marastation-web)
# ─────────────────────────────────────────────────────────────────

class TestWF6PublicMenu:
    """GET /menu/public is used by the public website — no auth needed."""

    def test_public_menu_returns_items(self, http):
        r = _get(http, "/menu/public")
        assert r.status_code == 200, f"/menu/public: {r.status_code} {r.text[:200]}"
        data = r.json()
        items = data if isinstance(data, list) else data.get("items", data.get("menu", []))
        assert len(items) > 0, (
            "GET /menu/public returned empty list — "
            "marastation-web will show blank menu"
        )

    def test_public_menu_items_have_price(self, http):
        """Each public menu item must have a selling_price > 0."""
        r = _get(http, "/menu/public")
        data = r.json()
        items = data if isinstance(data, list) else data.get("items", data.get("menu", [data]))
        if not items:
            pytest.skip("No menu items returned")
        for item in items[:10]:  # check first 10
            price = item.get("selling_price") or item.get("price") or 0
            assert float(price) > 0, (
                f"Menu item '{item.get('name', '?')}' has price={price}. "
                f"Public menu should only show items with selling_price > 0."
            )

    def test_public_menu_no_auth_needed(self, http):
        """Public menu must not return 401 — no JWT required."""
        r = http.get(
            f"{BACKEND_URL}/menu/public",
            headers={"Authorization": "Bearer fake"},  # bad token
            timeout=DEFAULT_TIMEOUT,
        )
        # Should still return 200 (public route ignores auth header)
        assert r.status_code == 200, (
            f"Public menu returned {r.status_code} with bad token — "
            f"route may be behind JWT middleware. Check PUBLIC_PATHS in main.py"
        )


# ─────────────────────────────────────────────────────────────────
# WF-7: Infrastructure health
# ─────────────────────────────────────────────────────────────────

class TestWF7Health:
    """DB, Supabase, API gateway are reachable and healthy."""

    def test_health_basic(self, http):
        r = _get(http, "/health")
        assert r.status_code == 200, f"/health: {r.status_code}"

    def test_health_deep_db_and_supabase(self, http):
        r = http.get(f"{BACKEND_URL}/health/deep", timeout=30)
        assert r.status_code == 200, f"/health/deep: {r.status_code} {r.text[:300]}"
        body = r.json()
        checks = body.get("checks", {})
        assert checks.get("postgres", {}).get("ok") is True, (
            f"Postgres health check FAILED: {checks.get('postgres')}. "
            f"Database may be down or connection pool exhausted."
        )
        assert checks.get("supabase", {}).get("ok") is True, (
            f"Supabase health check FAILED: {checks.get('supabase')}. "
            f"Supabase project may be paused (free tier auto-pause)."
        )

    def test_openapi_route_count(self, http):
        """Total registered routes must not have dropped significantly."""
        r = _get(http, "/openapi.json")
        count = len(r.json().get("paths", {}))
        assert count >= 150, (
            f"Only {count} routes in OpenAPI spec (floor: 150). "
            f"A recent deploy may have silently deleted routes."
        )
