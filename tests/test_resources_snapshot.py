"""Unit tests for build_resources_snapshot (Session 45 Discord /resources)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import discord_interactions as di  # noqa: E402


class TestBuildResourcesSnapshot:
    def test_happy_path_returns_all_keys(self, monkeypatch):
        """Real psutil call — every snapshot key present, numerics within bounds."""
        monkeypatch.setattr(di, "_get_scheduler", lambda: None)
        snap = di.build_resources_snapshot()
        for key in (
            "cpu_pct", "ram_pct", "ram_used_gb", "ram_total_gb",
            "disk_pct", "disk_used_gb", "disk_total_gb",
            "swap_pct", "swap_used_mb", "swap_total_gb",
            "scheduler_running", "scheduler_jobs",
            "git_sha", "warnings",
        ):
            assert key in snap, f"missing key: {key}"
        assert snap["ram_pct"] is None or 0 <= snap["ram_pct"] <= 100
        assert isinstance(snap["warnings"], list)

    def test_resilient_to_psutil_cpu_failure(self, monkeypatch):
        """psutil.cpu_percent raises -> cpu_pct is None, other metrics still collected."""
        def boom(*a, **k):
            raise OSError("denied")
        monkeypatch.setattr(di.psutil, "cpu_percent", boom)
        monkeypatch.setattr(di, "_get_scheduler", lambda: None)
        snap = di.build_resources_snapshot()
        assert snap["cpu_pct"] is None
        # RAM probe is independent — should still produce a value
        assert snap["ram_pct"] is not None

    def test_warnings_fired_above_threshold(self, monkeypatch):
        """RAM at 85% -> 'RAM high' warning present."""
        class FakeMem:
            percent = 85.0
            used = int(3.4e9)
            total = int(4e9)
        monkeypatch.setattr(di.psutil, "virtual_memory", lambda: FakeMem())
        monkeypatch.setattr(di, "_get_scheduler", lambda: None)
        snap = di.build_resources_snapshot()
        assert any("RAM" in w for w in snap["warnings"])

    def test_scheduler_not_running_emits_warning(self, monkeypatch):
        """_get_scheduler returns None -> 'APScheduler not running' warning."""
        monkeypatch.setattr(di, "_get_scheduler", lambda: None)
        snap = di.build_resources_snapshot()
        assert snap["scheduler_running"] is False
        assert any("APScheduler" in w for w in snap["warnings"])

    def test_git_sha_from_env(self, monkeypatch):
        """SOURCE_COMMIT env var -> first 7 chars in git_sha; missing -> 'unknown'."""
        monkeypatch.setenv("SOURCE_COMMIT", "8ad1f51abcdef")
        monkeypatch.delenv("COOLIFY_GIT_COMMIT_SHA", raising=False)
        monkeypatch.delenv("GIT_SHA", raising=False)
        monkeypatch.setattr(di, "_get_scheduler", lambda: None)
        snap = di.build_resources_snapshot()
        assert snap["git_sha"] == "8ad1f51"

        monkeypatch.delenv("SOURCE_COMMIT")
        snap2 = di.build_resources_snapshot()
        assert snap2["git_sha"] == "unknown"
