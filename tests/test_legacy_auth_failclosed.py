"""Legacy self-issued JWT path must fail closed without an operator secret.

Background (2026-09-12 audit): production accepted an HS256 token signed with
the JWT_SECRET *default literal* that lived in auth_routes.py, and the code
also shipped a default VEXON_USER/VEXON_HASH admin login. Anyone holding the
source could mint an admin token. These tests pin the fail-closed contract:

  * JWT_SECRET unset  -> Path 2 disabled, /auth/login -> 503, no default users
  * JWT_SECRET == old default literal -> treated exactly like unset
  * JWT_SECRET set to a real value -> Path 2 works as before (existing tests
    rely on this with JWT_SECRET=testsecret)

No network, no DB: /auth/login is exercised through a throwaway FastAPI app
and short-circuits before any DB or password work. No importlib.reload — the
module-level flags are monkeypatched instead, because tests/test_ai_exec.py
leaves ``sys.modules["fastapi"]`` stubbed for the rest of the session and a
reload of auth_routes after it would fail on ``from fastapi import ...``.
"""
import os

import jwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

os.environ.setdefault("DATABASE_URL", "postgresql://u:p@localhost:5432/d")
os.environ.setdefault("JWT_SECRET", "testsecret")
os.environ.setdefault("OPENAI_API_KEY", "x")
os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "x")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "x")
os.environ.setdefault("SUPABASE_ANON_KEY", "x")

# main MUST be imported before auth_routes. auth_routes does `from main import
# get_db_conn`; importing auth_routes first makes that a circular import in
# which stock_in_routes' `from auth_routes import _require_admin_role` fails
# and silently binds its always-500 fallback gate for the rest of the session
# (breaks test_admin_gate when this file is collected first).
import main  # noqa: E402,F401
import auth_routes  # noqa: E402

OLD_DEFAULT_SECRET = "vexonhq-change-this-secret-key-in-production-please"
REAL_SECRET = "unit-test-secret-not-the-default"


def _forged_token(secret: str, role: str = "admin") -> str:
    return jwt.encode({"sub": "forged", "role": role, "exp": 4102444800}, secret, algorithm="HS256")


def _login_client() -> TestClient:
    app = FastAPI()
    app.include_router(auth_routes.router)
    return TestClient(app)


def _configure(monkeypatch, secret: str) -> None:
    """Mirror what module import does for a given JWT_SECRET value."""
    monkeypatch.setattr(auth_routes, "JWT_SECRET", secret)
    monkeypatch.setattr(auth_routes, "LEGACY_AUTH_ENABLED", auth_routes._legacy_auth_enabled(secret))
    # Path 1 is irrelevant here; force verify_token straight to Path 2.
    monkeypatch.setattr(auth_routes, "SUPABASE_URL", "")
    monkeypatch.setattr(auth_routes, "SUPABASE_JWT_SECRET", "")


def test_enabled_flag_derivation():
    assert auth_routes._legacy_auth_enabled("") is False
    assert auth_routes._legacy_auth_enabled(OLD_DEFAULT_SECRET) is False
    assert auth_routes._legacy_auth_enabled(REAL_SECRET) is True


@pytest.mark.parametrize("secret", ["", OLD_DEFAULT_SECRET])
def test_missing_or_default_secret_disables_legacy_path(monkeypatch, secret):
    _configure(monkeypatch, secret)

    assert auth_routes.LEGACY_AUTH_ENABLED is False
    assert auth_routes.verify_token(_forged_token(OLD_DEFAULT_SECRET)) is None

    resp = _login_client().post("/auth/login", json={"username": "vexonhq", "password": "mara2026"})
    assert resp.status_code == 503


def test_real_secret_keeps_legacy_path_working(monkeypatch):
    _configure(monkeypatch, REAL_SECRET)
    monkeypatch.setattr(auth_routes, "VEXON_USER", "")
    monkeypatch.setattr(auth_routes, "VEXON_HASH", "")

    assert auth_routes.LEGACY_AUTH_ENABLED is True
    payload = auth_routes.verify_token(auth_routes.create_token("tum"))
    assert payload is not None
    assert payload["sub"] == "tum"
    assert payload["_role"] == "admin"
    assert auth_routes.verify_token(_forged_token(OLD_DEFAULT_SECRET)) is None

    # Enabled but no accounts configured -> ordinary 401, not 503.
    resp = _login_client().post("/auth/login", json={"username": "vexonhq", "password": "mara2026"})
    assert resp.status_code == 401


def test_no_builtin_default_account(monkeypatch):
    # The test environment never sets these, so the module-level values prove
    # there is no in-code default account any more.
    assert "VEXON_USER" not in os.environ and "VEXON_HASH" not in os.environ
    assert auth_routes.VEXON_USER == ""
    assert auth_routes.VEXON_HASH == ""
    assert auth_routes._load_users() == {}

    monkeypatch.setattr(auth_routes, "VEXON_USER", "Ops")
    monkeypatch.setattr(auth_routes, "VEXON_HASH", "pbkdf2:sha256:1:00:AA==")
    assert auth_routes._load_users() == {"ops": "pbkdf2:sha256:1:00:AA=="}
