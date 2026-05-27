"""
health_monitor.py — Proactive VPS health monitoring + LINE alerts
=================================================================
Called by APScheduler (registered in line_bot_routes.py) every 15 minutes.

Checks:
  1. Disk > 80%         — parse `df -h` output
  2. RAM < 400 MB free  — parse `free -m` output (integer MB, avoids unit parsing)
  3. Container not Up   — parse `docker ps -a` output
  4. API /health down   — HTTP GET localhost:8000/health

Alert behaviour (edge-trigger, no spam):
  - Fires LINE alert when a check transitions False → True (new issue)
  - Fires LINE resolved when a check transitions True → False (issue cleared)
  - Does NOT fire again while issue persists
  - State is in-memory — resets on process restart (acceptable: max 1 dupe alert)
"""

import logging
import os
import subprocess
import urllib.request
from datetime import datetime

log = logging.getLogger("health-monitor")

AI_CHAT_URL = "https://ai.marastation.com"

# ── Alert state (edge-trigger) ─────────────────────────────────────────
_alert_state: dict[str, bool] = {
    "disk":      False,
    "ram":       False,
    "container": False,
    "api":       False,
}

_ALERT_META: dict[str, tuple[str, str]] = {
    "disk":      ("⚠️  Disk usage สูงเกิน 80%",          "เช็ค disk หน่อย"),
    "ram":       ("⚠️  RAM เหลือน้อยกว่า 400 MB",         "ดู memory หน่อย"),
    "container": ("⚠️  Docker container หยุดทำงาน",       "ดู container ทั้งหมด"),
    "api":       ("⚠️  API /health ไม่ตอบสนอง",           "เช็ค health ระบบ"),
}

_RESOLVED_META: dict[str, str] = {
    "disk":      "Disk กลับมาปกติ",
    "ram":       "RAM กลับมาปกติ",
    "container": "Container กลับมาทำงาน",
    "api":       "API /health กลับมาปกติ",
}


# ── System parsers ──────────────────────────────────────────────────────

def _check_disk() -> bool:
    """Return True if any mount point is > 80% used."""
    try:
        result = subprocess.run(
            "df -h", shell=True, capture_output=True, text=True, timeout=5
        )
        for line in result.stdout.splitlines()[1:]:  # skip header row
            parts = line.split()
            if len(parts) >= 5:
                pct_str = parts[4].rstrip("%")
                try:
                    if int(pct_str) > 80:
                        return True
                except ValueError:
                    pass
    except Exception:
        log.exception("disk check failed")
    return False


def _check_ram() -> bool:
    """Return True if available RAM < 400 MB.

    Uses 'free -m' (megabytes) so parsing is exact integer arithmetic.
    The 'available' column (index 6) includes reclaimable cache — more
    accurate than 'free' (index 3) for real headroom.
    """
    try:
        result = subprocess.run(
            "free -m", shell=True, capture_output=True, text=True, timeout=5
        )
        for line in result.stdout.splitlines():
            if line.startswith("Mem:"):
                parts = line.split()
                # free -m: Mem: total used free shared buff/cache available
                if len(parts) >= 7:
                    available_mb = int(parts[6])
                    return available_mb < 400
    except Exception:
        log.exception("RAM check failed")
    return False


def _check_containers() -> bool:
    """Return True if any Docker container is not in 'Up' state.

    Uses --format to get name + status without table header noise.
    Containers with status starting with 'Up' are healthy; anything
    else (Exited, unhealthy, Restarting, etc.) is flagged as an issue.
    """
    try:
        result = subprocess.run(
            "docker ps -a --format '{{.Names}} {{.Status}}'",
            shell=True, capture_output=True, text=True, timeout=5,
        )
        for line in result.stdout.splitlines():
            line = line.strip().strip("'")
            if not line:
                continue
            # Split into name + status on first space
            parts = line.split(" ", 1)
            if len(parts) == 2:
                status = parts[1].lower()
                if not status.startswith("up"):
                    return True
    except Exception:
        log.exception("container check failed")
    return False


def _check_api() -> bool:
    """Return True if localhost:8000/health is unreachable or returns >= 500."""
    try:
        req = urllib.request.Request("http://localhost:8000/health", method="GET")
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status >= 500
    except Exception:
        return True  # connection refused or timeout = effectively down


# ── Message formatters ──────────────────────────────────────────────────

def _alert_message(key: str) -> str:
    label, query = _ALERT_META[key]
    now = datetime.now().strftime("%H:%M")
    return (
        f"🚨 [ALERT] vexonhq-core\n"
        f"─────────────────\n"
        f"{label}\n"
        f"🕐 {now} น.\n"
        f"\n"
        f"👉 {AI_CHAT_URL}\n"
        f"พิมพ์: \"{query}\" เพื่อดูรายละเอียด"
    )


def _resolved_message(key: str) -> str:
    label = _RESOLVED_META[key]
    now = datetime.now().strftime("%H:%M")
    return (
        f"✅ [RESOLVED] vexonhq-core\n"
        f"─────────────────\n"
        f"🟢 {label}\n"
        f"🕐 {now} น."
    )


# ── LINE push (lazy import avoids circular dependency at module load) ───

def _push_line(text: str) -> None:
    """Push LINE message. Best-effort — logs failure but does not raise."""
    try:
        from line_bot_routes import _push_text  # type: ignore
        _push_text(text)
    except Exception:
        log.exception("health monitor: LINE push failed")


# ── Main job (called by APScheduler every 15 min) ──────────────────────

def health_check_job() -> None:
    """Run all health checks and send LINE alerts on state transitions."""
    checks: dict[str, bool] = {
        "disk":      _check_disk(),
        "ram":       _check_ram(),
        "container": _check_containers(),
        "api":       _check_api(),
    }

    for key, is_issue in checks.items():
        was_issue = _alert_state[key]
        if is_issue and not was_issue:
            log.warning("health monitor: NEW ISSUE — %s", key)
            _push_line(_alert_message(key))
            _alert_state[key] = True
        elif not is_issue and was_issue:
            log.info("health monitor: RESOLVED — %s", key)
            _push_line(_resolved_message(key))
            _alert_state[key] = False
