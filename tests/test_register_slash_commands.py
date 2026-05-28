"""Smoke test for the slash-command registration script's --dry-run mode."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "register_slash_commands.py"


def test_script_file_exists():
    assert SCRIPT.exists(), f"missing: {SCRIPT}"


def test_dry_run_lists_resources_without_api_call():
    """--dry-run prints the command list and exits 0; no HTTPS calls."""
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--dry-run"],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, (
        f"dry-run failed: rc={result.returncode}\n"
        f"stdout={result.stdout}\nstderr={result.stderr}"
    )
    out = result.stdout.lower()
    assert "resources" in out
    # No live API call should have happened
    assert "https://discord.com" not in result.stdout


def test_missing_env_returns_nonzero_in_live_mode(monkeypatch):
    """Without --dry-run and missing creds, the script exits non-zero."""
    env = {"PATH": __import__("os").environ.get("PATH", "")}
    result = subprocess.run(
        [sys.executable, str(SCRIPT)],
        capture_output=True, text=True, timeout=10, env=env,
    )
    assert result.returncode != 0
    assert "DISCORD_APP_ID" in result.stderr or "DISCORD_APP_ID" in result.stdout
