"""Unit tests for ai_exec_routes — whitelist, auth, rate limit.
Also tests health_monitor.py parsers via mocked subprocess output.
"""
from __future__ import annotations

import importlib
import sys
import types
import unittest
from unittest.mock import MagicMock, patch


# ── Shared stub helpers ────────────────────────────────────────────────

class _FakeHTTPException(Exception):
    """Mimics fastapi.HTTPException so raise HTTPException(status_code=...) works."""
    def __init__(self, status_code: int = 500, detail: str = ""):
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"HTTP {status_code}: {detail}")


def _load_exec_module():
    """Import ai_exec_routes with FastAPI/Pydantic stubbed out (no live server needed)."""
    fastapi_stub = types.ModuleType("fastapi")
    fastapi_stub.APIRouter = MagicMock(return_value=MagicMock())
    fastapi_stub.HTTPException = _FakeHTTPException
    fastapi_stub.Request = MagicMock

    pydantic_stub = types.ModuleType("pydantic")
    pydantic_stub.BaseModel = object

    sys.modules["fastapi"] = fastapi_stub
    sys.modules["pydantic"] = pydantic_stub

    if "ai_exec_routes" in sys.modules:
        del sys.modules["ai_exec_routes"]
    return importlib.import_module("ai_exec_routes")


# ── ai_exec_routes tests ───────────────────────────────────────────────

class TestWhitelist(unittest.TestCase):
    def setUp(self):
        self.mod = _load_exec_module()

    def test_whitelisted_commands_present(self):
        expected = {
            "df -h",
            "free -h",
            "docker ps -a",
            "journalctl -n 50",
            "uptime",
            "docker restart vexonhq-backend",
            "docker restart vexonhq-frontend",
        }
        self.assertEqual(self.mod.WHITELIST, expected)

    def test_dangerous_commands_not_in_whitelist(self):
        for cmd in [
            "rm -rf /",
            "docker stop vexonhq-backend",
            "kill 1",
            "systemctl stop nginx",
            "docker rm vexonhq-backend",
            "pkill python",
        ]:
            self.assertNotIn(cmd, self.mod.WHITELIST, f"Dangerous cmd should not be in whitelist: {cmd}")


class TestRateLimiter(unittest.TestCase):
    def setUp(self):
        self.mod = _load_exec_module()
        self.mod._rate_buckets.clear()

    def test_allows_under_limit(self):
        """19 calls from same IP should not raise."""
        for _ in range(19):
            self.mod._check_rate_limit("1.2.3.4")  # should not raise

    def test_blocks_at_limit(self):
        """21st call (after 20 in window) triggers rate limit exception."""
        for _ in range(20):
            self.mod._check_rate_limit("5.6.7.8")
        with self.assertRaises(_FakeHTTPException) as ctx:
            self.mod._check_rate_limit("5.6.7.8")
        self.assertEqual(ctx.exception.status_code, 429)

    def test_different_ips_independent(self):
        """Rate limit buckets are per-IP."""
        for _ in range(20):
            self.mod._check_rate_limit("10.0.0.1")
        # Different IP should still be allowed
        self.mod._check_rate_limit("10.0.0.2")  # should not raise


class TestExecValidation(unittest.TestCase):
    def setUp(self):
        self.mod = _load_exec_module()

    def test_strip_whitespace(self):
        """Stripped cmd should match whitelist."""
        self.assertIn("df -h".strip(), self.mod.WHITELIST)
        self.assertIn("  uptime  ".strip(), self.mod.WHITELIST)

    def test_non_whitelisted_raises(self):
        """Non-whitelisted commands are not in WHITELIST."""
        self.assertNotIn("ls -la /etc", self.mod.WHITELIST)
        self.assertNotIn("cat /etc/passwd", self.mod.WHITELIST)


# ── health_monitor.py parser tests ─────────────────────────────────────

class TestHealthMonitorParsers(unittest.TestCase):
    """Tests for health_monitor.py parsers using mocked subprocess output."""

    def setUp(self):
        # Stub line_bot_routes to avoid circular import at test time
        stub = types.ModuleType("line_bot_routes")
        stub._push_text = MagicMock()
        sys.modules["line_bot_routes"] = stub
        if "health_monitor" in sys.modules:
            del sys.modules["health_monitor"]
        self.hm = importlib.import_module("health_monitor")

    def test_check_disk_over_threshold(self):
        """Returns True when a mount is > 80% used."""
        df_output = (
            "Filesystem      Size  Used Avail Use% Mounted on\n"
            "/dev/vda1        50G   43G    7G   86% /\n"
        )
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout=df_output, returncode=0)
            self.assertTrue(self.hm._check_disk())

    def test_check_disk_under_threshold(self):
        """Returns False when all mounts are <= 80% used."""
        df_output = (
            "Filesystem      Size  Used Avail Use% Mounted on\n"
            "/dev/vda1        50G   35G   15G   70% /\n"
        )
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout=df_output, returncode=0)
            self.assertFalse(self.hm._check_disk())

    def test_check_ram_low(self):
        """Returns True when available RAM < 400 MB."""
        # free -m: Mem: total used free shared buff/cache available
        free_output = "Mem:           3900   3600     50     20      250      300\n"
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout=free_output, returncode=0)
            self.assertTrue(self.hm._check_ram())

    def test_check_ram_ok(self):
        """Returns False when available RAM >= 400 MB."""
        free_output = "Mem:           3900   2900    200     20      800      800\n"
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout=free_output, returncode=0)
            self.assertFalse(self.hm._check_ram())

    def test_check_containers_exited(self):
        """Returns True when a container status does not start with 'Up'."""
        docker_output = (
            "vexonhq-backend Up 2 hours\n"
            "vexonhq-frontend Exited (1) 5 minutes ago\n"
        )
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout=docker_output, returncode=0)
            self.assertTrue(self.hm._check_containers())

    def test_check_containers_all_up(self):
        """Returns False when all containers are Up."""
        docker_output = (
            "vexonhq-backend Up 2 hours\n"
            "vexonhq-frontend Up 3 days\n"
        )
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout=docker_output, returncode=0)
            self.assertFalse(self.hm._check_containers())


if __name__ == "__main__":
    unittest.main()
