"""
VEXONHQ — Authentication System
=================================
JWT-based login with PBKDF2 password hashing.
No extra dependencies — uses PyJWT (already installed via supabase) and stdlib hashlib.

Endpoints:
  POST /auth/login   — validate credentials, return JWT
  GET  /auth/me      — return current user info (requires token)
  POST /auth/logout  — client-side logout instruction

Environment variables:
  JWT_SECRET   — secret key for signing JWTs (CHANGE IN PRODUCTION)
  VEXON_USER   — username (default: vexonhq)
  VEXON_HASH   — PBKDF2 password hash (see below for format)

Hash format:  pbkdf2:sha256:<iterations>:<salt_hex>:<hash_b64>
Generate new hash:
  python3 -c "
  import hashlib, secrets, base64
  salt = secrets.token_hex(16)
  key = hashlib.pbkdf2_hmac('sha256', b'YOUR_PASSWORD', salt.encode(), 260000)
  print(f'pbkdf2:sha256:260000:{salt}:{base64.b64encode(key).decode()}')
  "
"""

from __future__ import annotations

import base64
import hashlib
import logging
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

import psycopg2
import jwt
from fastapi import APIRouter, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel

try:
    from main import get_db_conn  # type: ignore[import]
except ImportError:
    def get_db_conn():  # type: ignore[misc]
        return psycopg2.connect(os.environ["DATABASE_URL"])

log = logging.getLogger("auth_routes")

router = APIRouter(prefix="/auth", tags=["auth"])

# ─────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────

JWT_SECRET = os.environ.get(
    "JWT_SECRET",
    "vexonhq-change-this-secret-key-in-production-please"
)
# Supabase project URL — used to build the JWKS endpoint for ES256 token verification.
# Set in Coolify: SUPABASE_URL=https://<project-id>.supabase.co
# (Never use NEXT_PUBLIC_ prefix — this is server-side only.)
SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
# Supabase JWT Secret — kept for legacy HS256 Supabase projects; not used for ES256.
SUPABASE_JWT_SECRET = os.environ.get("SUPABASE_JWT_SECRET", "")
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_HOURS = 8

# Default credentials (override via env vars)
VEXON_USER = os.environ.get("VEXON_USER", "vexonhq")
VEXON_HASH = os.environ.get(
    "VEXON_HASH",
    "pbkdf2:sha256:260000:3aca8935884bee634378925756665515:Z5xUmbAylUBocnq4FchR1f2nfYGeK1WfIPfe62qSvPs="
)
# Default password: mara2026  (override via VEXON_HASH env var in Coolify)

# ── Role config ───────────────────────────────────────────────────────────────
# Comma-separated usernames that get role="admin" in their JWT.
# Default includes the legacy single-user "vexonhq" and "tum".
# Override in Coolify: VEXON_ADMINS=tum,vexonhq,manager
_ADMIN_USERNAMES: frozenset[str] = frozenset(
    x.strip().lower()
    for x in os.environ.get("VEXON_ADMINS", "tum,vexonhq").split(",")
    if x.strip()
)


def _get_role(username: str) -> str:
    """Return 'admin' or 'user' for a given username (case-insensitive)."""
    return "admin" if username.strip().lower() in _ADMIN_USERNAMES else "user"


def _load_users() -> dict[str, str]:
    """
    Discover every configured user account from env vars.

    Two patterns are accepted, additively:

    1. **Legacy single-user** — `VEXON_USER` + `VEXON_HASH`. The
       admin account that has existed since day one. Always present
       so the system never locks itself out.
    2. **Multi-user** — `VEXON_USER_<KEY>` + `VEXON_HASH_<KEY>` pairs.
       The `<KEY>` suffix is just a label that ties username to hash;
       it has no role in auth. Example env pair:
           VEXON_USER_TUM = Tum
           VEXON_HASH_TUM = pbkdf2:sha256:260000:...

    Returns a dict keyed by `username.lower()` so the login handler
    can do an O(1) case-insensitive lookup. Hash is the PBKDF2 string
    in the same format `_verify_password` expects.
    """
    users: dict[str, str] = {}
    if VEXON_USER and VEXON_HASH:
        users[VEXON_USER.strip().lower()] = VEXON_HASH

    prefix = "VEXON_USER_"
    for env_key, env_value in os.environ.items():
        if not env_key.startswith(prefix):
            continue
        suffix = env_key[len(prefix):]
        if not suffix:
            continue
        username = (env_value or "").strip()
        if not username:
            continue
        user_hash = os.environ.get(f"VEXON_HASH_{suffix}")
        if not user_hash:
            log.warning(
                "auth: env %s set to %r but matching VEXON_HASH_%s is missing — skipping account",
                env_key, username, suffix,
            )
            continue
        users[username.lower()] = user_hash
    return users

# ─────────────────────────────────────────────────────────
# Supabase JWKS client (ES256 verification)
# ─────────────────────────────────────────────────────────

_supabase_jwks_client: Optional[object] = None


def _get_supabase_jwks_client() -> Optional[object]:
    """
    Return a cached PyJWKClient pointed at the Supabase JWKS endpoint,
    or None when SUPABASE_URL is not configured.

    Requires: cryptography>=3.4 (for EC key support in PyJWT).
    """
    global _supabase_jwks_client
    if _supabase_jwks_client is not None:
        return _supabase_jwks_client
    if not SUPABASE_URL:
        return None
    try:
        from jwt import PyJWKClient  # PyJWT >= 2.4
        jwks_url = SUPABASE_URL.rstrip("/") + "/.well-known/jwks.json"
        _supabase_jwks_client = PyJWKClient(jwks_url, cache_keys=True)
        log.info("Supabase JWKS client initialised: %s", jwks_url)
    except Exception as exc:
        log.error("Failed to initialise Supabase JWKS client: %s", exc)
    return _supabase_jwks_client


# Simple in-memory rate limiter: {ip: [timestamp, ...]}
_login_attempts: dict[str, list[float]] = {}
MAX_ATTEMPTS = 10
WINDOW_SECONDS = 60

# ─────────────────────────────────────────────────────────
# Password helpers
# ─────────────────────────────────────────────────────────

def _verify_password(plain: str, stored_hash: str) -> bool:
    """Verify PBKDF2-SHA256 hashed password."""
    try:
        parts = stored_hash.split(":")
        if len(parts) != 5 or parts[0] != "pbkdf2" or parts[1] != "sha256":
            return False
        _, _, iterations_str, salt_hex, hash_b64 = parts
        iterations = int(iterations_str)
        key = hashlib.pbkdf2_hmac("sha256", plain.encode(), salt_hex.encode(), iterations)
        expected = base64.b64decode(hash_b64)
        return secrets.compare_digest(key, expected)
    except Exception:
        return False


def _hash_password(plain: str) -> str:
    """Hash a plain password. Use to generate new VEXON_HASH values."""
    salt = secrets.token_hex(16)
    key = hashlib.pbkdf2_hmac("sha256", plain.encode(), salt.encode(), 260000)
    return f"pbkdf2:sha256:260000:{salt}:{base64.b64encode(key).decode()}"


# ─────────────────────────────────────────────────────────
# JWT helpers
# ─────────────────────────────────────────────────────────

def create_token(username: str) -> str:
    role = _get_role(username)
    now = datetime.now(timezone.utc)
    payload = {
        "sub": username,
        "role": role,
        "iat": now,
        "exp": now + timedelta(hours=JWT_EXPIRE_HOURS),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def verify_token(token: str) -> Optional[dict]:
    """
    Verify a JWT and return the normalized payload, or None.

    Tries two validation paths in order:

    Path 1 — Supabase Auth tokens:
        ES256 (current Supabase default): verifies using EC public key fetched
            from Supabase's JWKS endpoint (SUPABASE_URL env var required).
        HS256 (legacy Supabase): verifies with SUPABASE_JWT_SECRET env var.
        Role comes from app_metadata.role (Supabase convention).

    Path 2 — Self-issued tokens from /auth/login (legacy VEXONHQ login):
        Decodes with JWT_SECRET, no audience check.
        Role comes directly from payload["role"] (our convention).
        Always tried as fallback when Path 1 fails.

    Returns None on any definitive failure (expired, malformed). Never raises.
    """
    # Path 1: Supabase Auth tokens (ES256 via JWKS, or HS256 via secret)
    if SUPABASE_URL or SUPABASE_JWT_SECRET:
        try:
            hdr = jwt.get_unverified_header(token)
            alg = hdr.get("alg", "")

            if alg == "ES256":
                # Modern Supabase: asymmetric ES256 — verify with EC public key from JWKS
                jwks_client = _get_supabase_jwks_client()
                if jwks_client is None:
                    log.warning("verify_token: ES256 token but SUPABASE_URL not set — skipping Supabase path")
                else:
                    signing_key = jwks_client.get_signing_key_from_jwt(token)  # type: ignore[union-attr]
                    payload = jwt.decode(
                        token,
                        signing_key.key,
                        algorithms=["ES256"],
                        audience="authenticated",
                    )
                    app_meta = payload.get("app_metadata") or {}
                    payload["_role"] = app_meta.get("role", "staff")
                    return payload

            elif alg == "HS256" and SUPABASE_JWT_SECRET:
                # Legacy HS256 Supabase project
                payload = jwt.decode(
                    token,
                    SUPABASE_JWT_SECRET,
                    algorithms=["HS256"],
                    audience="authenticated",
                )
                app_meta = payload.get("app_metadata") or {}
                payload["_role"] = app_meta.get("role", "staff")
                return payload

        except jwt.ExpiredSignatureError:
            # Expired Supabase token — don't fall through to legacy path
            return None
        except Exception as exc:
            # Network error, wrong audience, invalid signature, etc. — try legacy
            log.warning(
                "verify_token: Supabase path failed (%s: %s) — trying legacy path",
                type(exc).__name__, exc,
            )

    # Path 2: Self-issued tokens from /auth/login
    try:
        payload = jwt.decode(
            token,
            JWT_SECRET,
            algorithms=["HS256"],
            options={"verify_aud": False},
        )
        # Normalize role to _role so all callers use payload['_role'] consistently
        payload["_role"] = payload.get("role", "staff")
        return payload
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None


# ─────────────────────────────────────────────────────────
# Rate limiter helper
# ─────────────────────────────────────────────────────────

def _check_rate_limit(ip: str) -> bool:
    """Return True if request is allowed. Sliding window."""
    import time
    now = time.time()
    attempts = _login_attempts.get(ip, [])
    # Remove old attempts outside window
    attempts = [t for t in attempts if now - t < WINDOW_SECONDS]
    if len(attempts) >= MAX_ATTEMPTS:
        _login_attempts[ip] = attempts
        return False
    attempts.append(now)
    _login_attempts[ip] = attempts
    return True


# ─────────────────────────────────────────────────────────
# Pydantic models
# ─────────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    username: str
    password: str


class PageConfigUpdate(BaseModel):
    page_href: str
    user_visible: bool


# ─────────────────────────────────────────────────────────
# Routes
# ─────────────────────────────────────────────────────────

@router.post("/login")
def login(body: LoginRequest, request: Request):
    """
    Validate credentials and return a JWT access token.
    Rate limited to 10 attempts per minute per IP.
    """
    # Use X-Forwarded-For when behind Coolify/nginx reverse proxy
    forwarded_for = request.headers.get("X-Forwarded-For")
    client_ip = forwarded_for.split(",")[0].strip() if forwarded_for else (
        request.client.host if request.client else "unknown"
    )

    if not _check_rate_limit(client_ip):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many login attempts. Please wait 1 minute.",
        )

    username = body.username.strip().lower()
    password = body.password

    # Look up the user's stored hash from the configured account list.
    # _load_users() reads env vars on every call so password rotation
    # in Coolify is picked up without a worker restart.
    users = _load_users()
    stored_hash = users.get(username)
    # Always run _verify_password (even when the user is unknown) so the
    # response time doesn't leak which usernames exist — feed it a
    # placeholder hash in the missing-user case.
    placeholder_hash = "pbkdf2:sha256:260000:0:" + base64.b64encode(b"\x00" * 32).decode()
    password_ok = _verify_password(password, stored_hash or placeholder_hash)
    auth_ok = bool(stored_hash) and password_ok

    if not auth_ok:
        log.warning("Failed login attempt for user '%s' from %s", username, client_ip)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="ชื่อผู้ใช้หรือรหัสผ่านไม่ถูกต้อง",
        )

    # Use the original-cased username from env (display name) when we
    # can find it; fall back to the lowercase form otherwise.
    display_name = username
    for env_key, env_value in os.environ.items():
        if (env_key == "VEXON_USER" or env_key.startswith("VEXON_USER_")) and \
           env_value and env_value.strip().lower() == username:
            display_name = env_value.strip()
            break

    token = create_token(display_name)
    log.info("Successful login for user '%s' from %s", display_name, client_ip)

    return {
        "access_token": token,
        "token_type": "bearer",
        "expires_in": JWT_EXPIRE_HOURS * 3600,
        "username": display_name,
    }


@router.get("/me")
def get_me(request: Request):
    """Return current authenticated user info."""
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Not authenticated")

    token = auth_header[7:]
    payload = verify_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Token expired or invalid")

    return {
        "sub": payload.get("sub"),
        "email": payload.get("email"),
        "role": payload.get("_role", "staff"),
        "expires_at": payload.get("exp"),
    }


@router.post("/logout")
def logout():
    """
    Logout hint — actual token invalidation is client-side.
    Client should delete the stored JWT token.
    """
    return {"detail": "Logged out successfully. Please remove your token."}


def _require_admin_role(request: Request) -> dict:
    """Decode JWT from request and raise 403 if not admin. Returns payload."""
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Not authenticated")
    payload = verify_token(auth_header[7:])
    if not payload:
        raise HTTPException(status_code=401, detail="Token expired or invalid")
    if payload.get("_role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return payload


@router.get("/page-config")
def get_page_config(request: Request):
    """
    Return page visibility config for the authenticated user.

    Admin: returns role='admin' with empty pages dict (frontend shows everything).
    User:  returns role='user' with dict of {page_href: bool} from user_page_config.
    """
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Not authenticated")
    payload = verify_token(auth_header[7:])
    if not payload:
        raise HTTPException(status_code=401, detail="Token expired or invalid")

    role = payload.get("_role", "staff")

    if role == "admin":
        # Admin sees everything — no need to send full page list
        return {"role": "admin", "pages": {}}

    # For users, load visibility from DB
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT page_href, user_visible FROM public.user_page_config ORDER BY sort_order"
            )
            pages = {row[0]: bool(row[1]) for row in cur.fetchall()}
        return {"role": "user", "pages": pages}
    finally:
        conn.close()


@router.post("/page-config")
def update_page_config(body: PageConfigUpdate, request: Request):
    """
    Update visibility for a single page (admin only).

    Body: { "page_href": "/cashflow", "user_visible": true }
    """
    _require_admin_role(request)

    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO public.user_page_config (page_href, page_label, user_visible, updated_at)
                VALUES (%s, %s, %s, now())
                ON CONFLICT (page_href) DO UPDATE
                  SET user_visible = EXCLUDED.user_visible,
                      updated_at   = now()
                """,
                (body.page_href, body.page_href, body.user_visible),
            )
        conn.commit()
        return {"ok": True, "page_href": body.page_href, "user_visible": body.user_visible}
    finally:
        conn.close()
