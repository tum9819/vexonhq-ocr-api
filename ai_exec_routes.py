"""
ai_exec_routes.py — Restricted command executor for Claude AI chat
==================================================================
POST /ai/exec — Execute a whitelisted shell command and return stdout.

Auth:  X-AI-Exec-Key header (separate secret — NOT the JWT Bearer token)
Rate:  20 requests per minute per IP (in-memory, resets on restart)
Log:   every call → [AI-EXEC] timestamp cmd=... exit=N ip=...

Route is listed in PUBLIC_PATHS in main.py so JWTAuthMiddleware passes it
through. Auth is enforced here via X-AI-Exec-Key comparison.

Whitelist — Tier 1 read-only (auto-execute from Claude):
  "df -h"                          disk usage
  "free -h"                        RAM usage (human-readable for Claude)
  "docker ps -a"                   all containers including stopped/exited
  "journalctl -n 50"               last 50 system log lines
  "uptime"                         server uptime + load average

Whitelist — Tier 2 action (only reached after TUM confirms in /api/confirm):
  "docker restart vexonhq-backend"
  "docker restart vexonhq-frontend"

NEVER in whitelist: rm, kill, pkill, systemctl stop/start, docker stop,
docker rm, pipes, redirects.
"""

import logging
import os
import subprocess
import time
from collections import defaultdict
from datetime import datetime

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

log = logging.getLogger("ai-exec")
router = APIRouter(prefix="/ai", tags=["ai"])

WHITELIST: set[str] = {
    "df -h",
    "free -h",
    "docker ps -a",
    "journalctl -n 50",
    "uptime",
    "docker restart vexonhq-backend",
    "docker restart vexonhq-frontend",
}

# ── In-memory rate limiter (per IP, 20 req / 60 s) ────────────────────
_rate_buckets: dict[str, list[float]] = defaultdict(list)
RATE_LIMIT = 20
RATE_WINDOW = 60.0  # seconds


def _check_rate_limit(ip: str) -> None:
    """Raise HTTPException 429 if the IP has exceeded 20 calls/min."""
    now = time.monotonic()
    _rate_buckets[ip] = [t for t in _rate_buckets[ip] if now - t < RATE_WINDOW]
    if len(_rate_buckets[ip]) >= RATE_LIMIT:
        raise HTTPException(status_code=429, detail="Rate limit exceeded (20/min)")
    _rate_buckets[ip].append(now)


# ── Request / Response models ──────────────────────────────────────────

class ExecRequest(BaseModel):
    cmd: str


class ExecResponse(BaseModel):
    stdout: str
    stderr: str
    exit_code: int


# ── Endpoint ───────────────────────────────────────────────────────────

@router.post("/exec", response_model=ExecResponse)
def exec_command(body: ExecRequest, request: Request) -> ExecResponse:
    # 1. API key auth
    api_key = request.headers.get("X-AI-Exec-Key", "")
    expected = os.environ.get("AI_EXEC_SECRET", "")
    if not expected or api_key != expected:
        raise HTTPException(status_code=401, detail="Invalid or missing X-AI-Exec-Key")

    # 2. Rate limit (per client IP)
    client_ip = request.client.host if request.client else "unknown"
    _check_rate_limit(client_ip)

    # 3. Whitelist check
    cmd = body.cmd.strip()
    if cmd not in WHITELIST:
        log.warning("[AI-EXEC] REJECTED cmd=%r ip=%s", cmd, client_ip)
        raise HTTPException(status_code=403, detail=f"Command not in whitelist: {cmd!r}")

    # 4. Execute
    ts = datetime.utcnow().isoformat(timespec="seconds")
    try:
        result = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        log.info("[AI-EXEC] %s cmd=%r exit=%d ip=%s", ts, cmd, result.returncode, client_ip)
        return ExecResponse(
            stdout=result.stdout,
            stderr=result.stderr,
            exit_code=result.returncode,
        )
    except subprocess.TimeoutExpired:
        log.error("[AI-EXEC] %s TIMEOUT cmd=%r ip=%s", ts, cmd, client_ip)
        raise HTTPException(status_code=504, detail="Command timed out after 10s")
