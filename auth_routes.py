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

import jwt
from fastapi import APIRouter, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel

log = logging.getLogger("auth_routes")

router = APIRouter(prefix="/auth", tags=["auth"])

# ─────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────

JWT_SECRET = os.environ.get(
    "JWT_SECRET",
    "vexonhq-change-this-secret-key-in-production-please"
)
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_HOURS = 8

# Default credentials (override via env vars)
VEXON_USER = os.environ.get("VEXON_USER", "vexonhq")
VEXON_HASH = os.environ.get(
    "VEXON_HASH",
    "pbkdf2:sha256:260000:de343f6dd1a13b5506a6b34f15f99f4d:svo3yRsZ22V3yJ1X/y4UZ5FJJEiZZe/Ss0Uc+uhptdk="
)

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
    now = datetime.now(timezone.utc)
    payload = {
        "sub": username,
        "iat": now,
        "exp": now + timedelta(hours=JWT_EXPIRE_HOURS),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def verify_token(token: str) -> Optional[dict]:
    """Return payload dict or None if invalid/expired."""
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
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

    # Constant-time username check
    username_ok = secrets.compare_digest(username, VEXON_USER.lower())
    password_ok = _verify_password(password, VEXON_HASH) if username_ok else False

    if not (username_ok and password_ok):
        log.warning("Failed login attempt for user '%s' from %s", username, client_ip)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="ชื่อผู้ใช้หรือรหัสผ่านไม่ถูกต้อง",
        )

    token = create_token(VEXON_USER)
    log.info("Successful login for user '%s' from %s", username, client_ip)

    return {
        "access_token": token,
        "token_type": "bearer",
        "expires_in": JWT_EXPIRE_HOURS * 3600,
        "username": VEXON_USER,
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
        "username": payload.get("sub"),
        "expires_at": payload.get("exp"),
    }


@router.post("/logout")
def logout():
    """
    Logout hint — actual token invalidation is client-side.
    Client should delete the stored JWT token.
    """
    return {"detail": "Logged out successfully. Please remove your token."}
