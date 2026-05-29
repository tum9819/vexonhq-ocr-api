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
    # Both commands the script registers must appear in the dry-run list
    assert "resources" in out
    assert "help" in out
    # Real guard against a live call is the "--dry-run early-return"
    # logic in the script; sanity-check that path emitted its marker.
    assert "dry-run" in out


def test_missing_env_returns_nonzero_in_live_mode():
    """Without --dry-run and missing creds, the script exits non-zero.

    Preserves PATH + Windows runtime variables (SystemRoot, SystemDrive)
    so the subprocess can still launch python.exe; only the Discord
    credentials are stripped.
    """
    import os
    keep = ("PATH", "SystemRoot", "SystemDrive", "PYTHONPATH")
    env = {k: os.environ[k] for k in keep if k in os.environ}
    result = subprocess.run(
        [sys.executable, str(SCRIPT)],
        capture_output=True, text=True, timeout=10, env=env,
    )
    assert result.returncode != 0
    assert "DISCORD_APP_ID" in result.stderr or "DISCORD_APP_ID" in result.stdout
