# Discord `/resources` Slash Command — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a read-only Discord slash command `/resources` that returns a one-shot VPS resource snapshot (CPU/RAM/disk/swap/scheduler/last deploy) to TUM in the VEXONHQ Ops channel.

**Architecture:** Extend the existing `/alerts/discord-interaction` endpoint to handle `INTERACTION_APPLICATION_COMMAND` (type 2) in addition to `INTERACTION_PING` and `INTERACTION_MESSAGE_COMPONENT`. Two new pure functions (`build_resources_snapshot`, `format_resources_message`) in `discord_interactions.py`. One new branch in `discord_routes.py`. One new registration script. All tests TDD.

**Tech Stack:** Python 3.11, FastAPI, psutil (already in requirements), shutil (stdlib), urllib (stdlib for Discord API). Ed25519 signature verification reuses the existing fixture in `tests/test_discord_interactions.py`.

**Spec:** `docs/superpowers/specs/2026-05-28-discord-slash-resources-design.md` (commits `7c73209` + `b3e5d17`).

---

## File structure

| File | Responsibility | Status |
|---|---|---|
| `discord_interactions.py` | Add `INTERACTION_APPLICATION_COMMAND` const, `_get_scheduler()`, `build_resources_snapshot()`, `format_resources_message()` | Modify |
| `discord_routes.py` | Add `INTERACTION_APPLICATION_COMMAND = 2` const + new branch in `discord_interaction()` for slash commands | Modify |
| `scripts/register_slash_commands.py` | Idempotent one-shot script: upsert `/resources` with Discord HTTP API. Supports `--dry-run`. | Create |
| `tests/test_resources_snapshot.py` | Unit tests for `build_resources_snapshot` and `format_resources_message` (5 tests) | Create |
| `tests/test_discord_interactions.py` | Integration tests for the new application_command branch (3 tests) | Modify (append) |
| `tests/test_register_slash_commands.py` | Smoke test for `--dry-run` mode of the registration script (1 test) | Create |

Total new code ≈ 200 LOC + 200 LOC of tests + 60 LOC of script.

---

## Task 1: Snapshot builder + `_get_scheduler` helper

**Files:**
- Modify: `discord_interactions.py` (add `_get_scheduler`, `build_resources_snapshot`, top-level `psutil` import)
- Create: `tests/test_resources_snapshot.py`

- [ ] **Step 1: Add the psutil import + scheduler accessor**

Edit `discord_interactions.py`. Locate the existing import block (around line 28-45) and add `psutil` and `shutil` to the imports if not already present. Then add the accessor right after the imports, before the existing module constants:

```python
import shutil

import psutil

# ──────────────────────────────────────────────────────────
# Scheduler accessor — wrapped so tests can monkeypatch without
# triggering line_bot_routes module init (DB, APScheduler.start).
# ──────────────────────────────────────────────────────────
def _get_scheduler():
    """Return the line_bot APScheduler instance, or None if unavailable."""
    try:
        import line_bot_routes  # noqa: PLC0415 — lazy to avoid import-time cost
        return getattr(line_bot_routes, "_scheduler", None)
    except Exception:
        return None
```

- [ ] **Step 2: Create the test file with 3 failing tests**

Create `tests/test_resources_snapshot.py`:

```python
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
        """psutil.cpu_percent raises → cpu_pct is None, other metrics still collected."""
        def boom(*a, **k):
            raise OSError("denied")
        monkeypatch.setattr(di.psutil, "cpu_percent", boom)
        monkeypatch.setattr(di, "_get_scheduler", lambda: None)
        snap = di.build_resources_snapshot()
        assert snap["cpu_pct"] is None
        # RAM probe is independent — should still produce a value
        assert snap["ram_pct"] is not None

    def test_warnings_fired_above_threshold(self, monkeypatch):
        """RAM at 85% → 'RAM high' warning present."""
        class FakeMem:
            percent = 85.0
            used = int(3.4e9)
            total = int(4e9)
        monkeypatch.setattr(di.psutil, "virtual_memory", lambda: FakeMem())
        monkeypatch.setattr(di, "_get_scheduler", lambda: None)
        snap = di.build_resources_snapshot()
        assert any("RAM" in w for w in snap["warnings"])

    def test_scheduler_not_running_emits_warning(self, monkeypatch):
        """_get_scheduler returns None → 'APScheduler not running' warning."""
        monkeypatch.setattr(di, "_get_scheduler", lambda: None)
        snap = di.build_resources_snapshot()
        assert snap["scheduler_running"] is False
        assert any("APScheduler" in w for w in snap["warnings"])

    def test_git_sha_from_env(self, monkeypatch):
        """SOURCE_COMMIT env var → first 7 chars in git_sha; missing → 'unknown'."""
        monkeypatch.setenv("SOURCE_COMMIT", "8ad1f51abcdef")
        monkeypatch.delenv("COOLIFY_GIT_COMMIT_SHA", raising=False)
        monkeypatch.delenv("GIT_SHA", raising=False)
        monkeypatch.setattr(di, "_get_scheduler", lambda: None)
        snap = di.build_resources_snapshot()
        assert snap["git_sha"] == "8ad1f51"

        monkeypatch.delenv("SOURCE_COMMIT")
        snap2 = di.build_resources_snapshot()
        assert snap2["git_sha"] == "unknown"
```

- [ ] **Step 3: Run tests — verify all 5 FAIL**

```powershell
cd C:\Users\rapee\vexonhq-ocr-api
pytest tests/test_resources_snapshot.py -v
```

Expected: 5 failures with `AttributeError: module 'discord_interactions' has no attribute 'build_resources_snapshot'`.

- [ ] **Step 4: Implement `build_resources_snapshot` in `discord_interactions.py`**

Add right after `_get_scheduler` (Step 1 location):

```python
# ──────────────────────────────────────────────────────────
# /resources snapshot — read-only VPS health collection.
# Used by the Discord slash command added Session 45.
# ──────────────────────────────────────────────────────────
def build_resources_snapshot() -> dict[str, Any]:
    """
    Collect a one-shot VPS resource snapshot for the /resources slash
    command. Never raises — every metric is independently try/excepted
    so a single failed probe degrades to None instead of crashing the
    whole response.

    Threshold warnings (appended to snap["warnings"] as strings):
      cpu_pct  > 80   → "CPU high"
      ram_pct  > 80   → "RAM high"
      disk_pct > 80   → "Disk filling"
      swap_pct > 50   → "Swap heavy use"
      scheduler not running → "APScheduler not running"
    """
    GB = 1024 ** 3
    MB = 1024 ** 2

    snap: dict[str, Any] = {
        "cpu_pct": None,
        "ram_pct": None,
        "ram_used_gb": None,
        "ram_total_gb": None,
        "disk_pct": None,
        "disk_used_gb": None,
        "disk_total_gb": None,
        "swap_pct": None,
        "swap_used_mb": None,
        "swap_total_gb": None,
        "scheduler_running": False,
        "scheduler_jobs": 0,
        "git_sha": "unknown",
        "warnings": [],
    }

    try:
        snap["cpu_pct"] = round(psutil.cpu_percent(interval=0.1), 1)
    except Exception:
        log.warning("snapshot: cpu_percent failed", exc_info=True)

    try:
        m = psutil.virtual_memory()
        snap["ram_pct"] = round(m.percent, 1)
        snap["ram_used_gb"] = round(m.used / GB, 2)
        snap["ram_total_gb"] = round(m.total / GB, 2)
    except Exception:
        log.warning("snapshot: virtual_memory failed", exc_info=True)

    try:
        d = shutil.disk_usage("/")
        snap["disk_pct"] = round(d.used / d.total * 100, 1)
        snap["disk_used_gb"] = round(d.used / GB, 1)
        snap["disk_total_gb"] = round(d.total / GB, 1)
    except Exception:
        log.warning("snapshot: disk_usage failed", exc_info=True)

    try:
        s = psutil.swap_memory()
        snap["swap_pct"] = round(s.percent, 1)
        snap["swap_used_mb"] = round(s.used / MB, 0)
        snap["swap_total_gb"] = round(s.total / GB, 1)
    except Exception:
        log.warning("snapshot: swap_memory failed", exc_info=True)

    try:
        sched = _get_scheduler()
        if sched is not None:
            snap["scheduler_running"] = bool(getattr(sched, "running", False))
            snap["scheduler_jobs"] = len(sched.get_jobs())
    except Exception:
        log.warning("snapshot: scheduler probe failed", exc_info=True)

    for var in ("SOURCE_COMMIT", "COOLIFY_GIT_COMMIT_SHA", "GIT_SHA"):
        v = os.environ.get(var, "")
        if v:
            snap["git_sha"] = v[:7]
            break

    if snap["cpu_pct"] is not None and snap["cpu_pct"] > 80:
        snap["warnings"].append("⚠️ CPU high — wait before next deploy")
    if snap["ram_pct"] is not None and snap["ram_pct"] > 80:
        snap["warnings"].append("⚠️ RAM high — risk of OOM kill")
    if snap["disk_pct"] is not None and snap["disk_pct"] > 80:
        snap["warnings"].append("⚠️ Disk filling — run docker prune")
    if snap["swap_pct"] is not None and snap["swap_pct"] > 50:
        snap["warnings"].append("⚠️ Swap heavy use — investigate process")
    if not snap["scheduler_running"]:
        snap["warnings"].append("⚠️ APScheduler not running — digests will not fire")

    return snap
```

- [ ] **Step 5: Run tests — verify all 5 PASS**

```powershell
pytest tests/test_resources_snapshot.py -v
```

Expected: `5 passed`. If any fails, fix `build_resources_snapshot` until green — do not edit the test to match a broken implementation.

- [ ] **Step 6: Run ast.parse + commit**

```powershell
python -c "import ast; ast.parse(open('discord_interactions.py', encoding='utf-8').read()); print('OK')"
git add discord_interactions.py tests/test_resources_snapshot.py
git commit -m "feat(ops): add build_resources_snapshot for Discord /resources

Reads psutil CPU/RAM/swap + shutil disk + APScheduler jobs + Coolify
git SHA env var. Every probe try/excepted so a single failure produces
None for that metric, not a 500.

Threshold warnings: CPU/RAM/disk >80%, swap >50%, scheduler down.
Tests: 5 unit tests cover happy path, psutil failure, threshold warning,
scheduler off, git SHA env lookup."
```

---

## Task 2: Message formatter

**Files:**
- Modify: `discord_interactions.py` (add `format_resources_message`)
- Modify: `tests/test_resources_snapshot.py` (append `TestFormatResourcesMessage` class)

- [ ] **Step 1: Append 4 failing tests to the test file**

Append to `tests/test_resources_snapshot.py`:

```python
class TestFormatResourcesMessage:
    def test_none_metric_renders_as_dash(self):
        """cpu_pct=None → line contains '—' (em-dash), not 'None'."""
        snap = {
            "cpu_pct": None, "ram_pct": 30.0, "ram_used_gb": 1.2, "ram_total_gb": 4.0,
            "disk_pct": 50.0, "disk_used_gb": 20.0, "disk_total_gb": 40.0,
            "swap_pct": 0.0, "swap_used_mb": 0, "swap_total_gb": 4.0,
            "scheduler_running": True, "scheduler_jobs": 7,
            "git_sha": "abc1234", "warnings": [],
        }
        out = di.format_resources_message(snap)
        assert "—" in out
        assert "None" not in out

    def test_warnings_block_renders(self):
        """Multiple warnings → each appears on its own line."""
        snap = {
            "cpu_pct": 95.0, "ram_pct": 85.0, "ram_used_gb": 3.4, "ram_total_gb": 4.0,
            "disk_pct": 50.0, "disk_used_gb": 20.0, "disk_total_gb": 40.0,
            "swap_pct": 0.0, "swap_used_mb": 0, "swap_total_gb": 4.0,
            "scheduler_running": True, "scheduler_jobs": 7,
            "git_sha": "abc1234",
            "warnings": ["⚠️ CPU high", "⚠️ RAM high"],
        }
        out = di.format_resources_message(snap)
        assert "CPU high" in out
        assert "RAM high" in out
        assert "Warnings: none" not in out

    def test_no_warnings_shows_none_label(self):
        """Empty warnings list → 'Warnings: none' line present."""
        snap = {
            "cpu_pct": 30.0, "ram_pct": 30.0, "ram_used_gb": 1.2, "ram_total_gb": 4.0,
            "disk_pct": 50.0, "disk_used_gb": 20.0, "disk_total_gb": 40.0,
            "swap_pct": 0.0, "swap_used_mb": 0, "swap_total_gb": 4.0,
            "scheduler_running": True, "scheduler_jobs": 7,
            "git_sha": "abc1234", "warnings": [],
        }
        out = di.format_resources_message(snap)
        assert "Warnings: none" in out

    def test_format_includes_git_sha_short(self):
        """Formatter renders the (already-shortened) git_sha verbatim."""
        snap = {
            "cpu_pct": 30.0, "ram_pct": 30.0, "ram_used_gb": 1.2, "ram_total_gb": 4.0,
            "disk_pct": 50.0, "disk_used_gb": 20.0, "disk_total_gb": 40.0,
            "swap_pct": 0.0, "swap_used_mb": 0, "swap_total_gb": 4.0,
            "scheduler_running": True, "scheduler_jobs": 7,
            "git_sha": "8ad1f51", "warnings": [],
        }
        out = di.format_resources_message(snap)
        assert "8ad1f51" in out
        # The builder is responsible for truncating to 7 chars; the
        # formatter must not pad / expand. Sanity-check both bounds:
        assert "8ad1f51abcdef" not in out
```

- [ ] **Step 2: Run tests — verify the 4 new tests FAIL**

```powershell
pytest tests/test_resources_snapshot.py::TestFormatResourcesMessage -v
```

Expected: 4 failures with `AttributeError: module 'discord_interactions' has no attribute 'format_resources_message'`.

- [ ] **Step 3: Implement `format_resources_message`**

Append to `discord_interactions.py` right after `build_resources_snapshot`:

```python
def format_resources_message(snap: dict[str, Any]) -> str:
    """Render a snapshot dict as a Discord-flavored markdown message.

    None values become em-dash. The 'Warnings' block lists each warning
    on its own line when non-empty, or shows 'Warnings: none' when empty.
    """
    def _pct(v):
        return f"{v:.1f}%" if v is not None else "—"

    def _gb(used, total):
        if used is None or total is None:
            return ""
        return f"({used:.2f} / {total:.2f} GB)"

    def _disk_gb(used, total):
        if used is None or total is None:
            return ""
        return f"({used:.1f} / {total:.1f} GB)"

    def _swap_label(used_mb, total_gb):
        if used_mb is None or total_gb is None:
            return ""
        return f"({used_mb:.0f} MB / {total_gb:.1f} GB)"

    if snap.get("scheduler_running"):
        sched_line = f"⏰ Scheduler      {snap.get('scheduler_jobs', 0)} jobs running"
    else:
        sched_line = "⏰ Scheduler      ⚠️ not running"

    lines = [
        "📊 **VPS Resources** — vexonhq-core",
        "─────────────────────────────────",
        f"🖥️  CPU            {_pct(snap.get('cpu_pct'))}",
        f"💾 RAM            {_pct(snap.get('ram_pct'))}   {_gb(snap.get('ram_used_gb'), snap.get('ram_total_gb'))}".rstrip(),
        f"💿 Disk           {_pct(snap.get('disk_pct'))}   {_disk_gb(snap.get('disk_used_gb'), snap.get('disk_total_gb'))}".rstrip(),
        f"📦 Swap           {_pct(snap.get('swap_pct'))}   {_swap_label(snap.get('swap_used_mb'), snap.get('swap_total_gb'))}".rstrip(),
        sched_line,
        f"🚀 Last deploy    {snap.get('git_sha', 'unknown')}",
        "─────────────────────────────────",
    ]
    warnings = snap.get("warnings") or []
    if warnings:
        lines.append("⚠️ Warnings:")
        for w in warnings:
            lines.append(f"  {w}")
    else:
        lines.append("⚠️ Warnings: none")

    return "\n".join(lines)
```

- [ ] **Step 4: Run tests — verify all 4 PASS**

```powershell
pytest tests/test_resources_snapshot.py -v
```

Expected: `9 passed` (5 from Task 1 still pass + 4 new).

- [ ] **Step 5: ast.parse + commit**

```powershell
python -c "import ast; ast.parse(open('discord_interactions.py', encoding='utf-8').read()); print('OK')"
git add discord_interactions.py tests/test_resources_snapshot.py
git commit -m "feat(ops): add format_resources_message for /resources reply

Renders snapshot dict to Discord markdown. None metrics show as em-dash
not literal 'None'. Warnings section is per-line when present, 'none'
when empty.

Tests: 3 formatter tests cover dash rendering, warnings rendering,
and the empty-warnings 'none' label."
```

---

## Task 3: Wire `INTERACTION_APPLICATION_COMMAND` into the route

**Files:**
- Modify: `discord_routes.py` (add constant + new branch in `discord_interaction()`)
- Modify: `tests/test_discord_interactions.py` (append 3 tests)

- [ ] **Step 1: Append 3 failing integration tests**

Append a new class to `tests/test_discord_interactions.py` (after the last existing class):

```python
# ──────────────────────────────────────────────────────────
# Slash command branch (INTERACTION_APPLICATION_COMMAND = 2)
# ──────────────────────────────────────────────────────────
class TestApplicationCommandBranch:
    def test_resources_returns_snapshot_message(
        self, app_with_router, keypair, monkeypatch
    ):
        """POST /alerts/discord-interaction with type=2, name=resources →
        200 + RESPONSE_CHANNEL_MESSAGE containing snapshot text."""
        sk, _ = keypair
        # Force a known snapshot so the test is deterministic
        fake_snap = {
            "cpu_pct": 25.0, "ram_pct": 30.0, "ram_used_gb": 1.2,
            "ram_total_gb": 4.0, "disk_pct": 50.0, "disk_used_gb": 20.0,
            "disk_total_gb": 40.0, "swap_pct": 0.0, "swap_used_mb": 0,
            "swap_total_gb": 4.0, "scheduler_running": True,
            "scheduler_jobs": 7, "git_sha": "abc1234", "warnings": [],
        }
        monkeypatch.setattr(di, "build_resources_snapshot", lambda: fake_snap)

        body_obj = {"type": 2, "data": {"name": "resources"},
                    "application_id": "test-app-id-123", "token": "tok"}
        body = json.dumps(body_obj).encode("utf-8")
        headers = _sign_body(sk, body)

        client = TestClient(app_with_router)
        r = client.post(
            "/alerts/discord-interaction", content=body, headers=headers
        )
        assert r.status_code == 200, r.text
        j = r.json()
        assert j["type"] == 4  # RESPONSE_CHANNEL_MESSAGE
        assert "VPS Resources" in j["data"]["content"]
        assert "abc1234" in j["data"]["content"]

    def test_unknown_command_name_returns_warning(
        self, app_with_router, keypair
    ):
        """Unknown slash-command name → 'Unsupported command' reply."""
        sk, _ = keypair
        body_obj = {"type": 2, "data": {"name": "nope"},
                    "application_id": "test-app-id-123", "token": "tok"}
        body = json.dumps(body_obj).encode("utf-8")
        headers = _sign_body(sk, body)

        client = TestClient(app_with_router)
        r = client.post(
            "/alerts/discord-interaction", content=body, headers=headers
        )
        assert r.status_code == 200
        j = r.json()
        assert j["type"] == 4
        assert "Unsupported command" in j["data"]["content"]
        assert "nope" in j["data"]["content"]

    def test_application_command_still_requires_valid_signature(
        self, app_with_router
    ):
        """Unsigned slash-command POST → 401, snapshot never built."""
        body_obj = {"type": 2, "data": {"name": "resources"}}
        body = json.dumps(body_obj).encode("utf-8")

        client = TestClient(app_with_router)
        r = client.post(
            "/alerts/discord-interaction",
            content=body,
            headers={"Content-Type": "application/json"},
        )
        assert r.status_code == 401
```

- [ ] **Step 2: Run tests — verify all 3 FAIL**

```powershell
pytest tests/test_discord_interactions.py::TestApplicationCommandBranch -v
```

Expected: the first 2 fail (the application_command branch doesn't exist; default code path returns "Unsupported interaction type"). The signature test should pass because the 401 path is shared with existing tests — verify it passes; if it fails, debug separately.

- [ ] **Step 3: Add the constant + new branch to `discord_routes.py`**

Edit `discord_routes.py`. Add the new constant in the existing constants block (around line 55):

```python
INTERACTION_APPLICATION_COMMAND = 2
```

Then locate the existing `if itype == INTERACTION_MESSAGE_COMPONENT:` block in `discord_interaction()` (around line 256) and add this new branch immediately **after** the closing of that block, **before** the final "unhandled interaction type" return:

```python
    if itype == INTERACTION_APPLICATION_COMMAND:
        data = payload.get("data") or {}
        cmd_name = (data.get("name") or "").lower()

        if cmd_name == "resources":
            try:
                snap = di.build_resources_snapshot()
                content = di.format_resources_message(snap)
            except Exception:
                log.exception(
                    "discord_interaction: /resources snapshot failed"
                )
                content = (
                    "❌ /resources failed — check Coolify logs for traceback"
                )
            return JSONResponse(
                {
                    "type": RESPONSE_CHANNEL_MESSAGE,
                    "data": {"content": content},
                }
            )

        # Unknown command — visible so TUM can spot registration drift
        return JSONResponse(
            {
                "type": RESPONSE_CHANNEL_MESSAGE,
                "data": {
                    "content": (
                        f"⚠️ Unsupported command: `{cmd_name[:32]}` — "
                        f"re-run scripts/register_slash_commands.py?"
                    ),
                },
            }
        )
```

- [ ] **Step 4: Run tests — verify all 3 PASS + nothing else broke**

```powershell
pytest tests/test_discord_interactions.py -v
```

Expected: all existing tests still green + the 3 new ones pass. Total count should grow by exactly 3.

- [ ] **Step 5: ast.parse + commit**

```powershell
python -c "import ast; ast.parse(open('discord_routes.py', encoding='utf-8').read()); print('OK')"
git add discord_routes.py tests/test_discord_interactions.py
git commit -m "feat(ops): handle Discord application_command (slash) in /alerts/discord-interaction

Adds INTERACTION_APPLICATION_COMMAND = 2 const + dispatch branch.
Currently routes /resources to build_resources_snapshot + formatter.
Unknown command names get a visible 'Unsupported' reply pointing at
the registration script — failure mode is loud, not silent.

Existing button-click (type=3) and PING (type=1) paths untouched.
Tests: 3 integration tests cover resources success, unknown name,
and signature still required for the new branch."
```

---

## Task 4: Registration script

**Files:**
- Create: `scripts/register_slash_commands.py`
- Create: `tests/test_register_slash_commands.py`

- [ ] **Step 1: Create the failing test**

Create `tests/test_register_slash_commands.py`:

```python
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
```

- [ ] **Step 2: Run tests — verify they FAIL**

```powershell
pytest tests/test_register_slash_commands.py -v
```

Expected: all 3 fail with `AssertionError: missing: ...scripts/register_slash_commands.py`.

- [ ] **Step 3: Create the `scripts/` directory + the script**

Create `scripts/register_slash_commands.py`:

```python
"""Register VEXONHQ Discord slash commands.

Idempotent: Discord upserts application commands by name. Re-run any
time you add a new command or change a description.

Required env (live mode only):
  DISCORD_APP_ID      from Discord Developer Portal → General Information
  DISCORD_BOT_TOKEN   from Bot tab → Reset Token (NOT the OAuth2 client secret)

Usage:
  python scripts/register_slash_commands.py            # live POST
  python scripts/register_slash_commands.py --dry-run  # print plan only
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

COMMANDS = [
    {
        "name": "resources",
        "description": (
            "Show VPS resource snapshot "
            "(CPU/RAM/disk/swap/scheduler/last deploy)"
        ),
        "type": 1,  # CHAT_INPUT
    },
]


def _post_command(app_id: str, token: str, cmd: dict) -> tuple[int, str]:
    url = (
        f"https://discord.com/api/v10/applications/{app_id}/commands"
    )
    body = json.dumps(cmd).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bot {token}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            text = resp.read().decode("utf-8")
            data = json.loads(text)
            return 0, str(data.get("id", "?"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")[:400]
        return e.code, detail


def main(argv: list[str]) -> int:
    dry_run = "--dry-run" in argv

    if dry_run:
        print("Would register the following commands:")
        for c in COMMANDS:
            print(f"  /{c['name']} — {c['description']}")
        print(f"\nTotal: {len(COMMANDS)} command(s) (dry-run, no API call)")
        return 0

    app_id = os.environ.get("DISCORD_APP_ID", "").strip()
    token = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
    missing = [k for k, v in (
        ("DISCORD_APP_ID", app_id), ("DISCORD_BOT_TOKEN", token),
    ) if not v]
    if missing:
        print(
            "ERROR: required env vars missing: "
            + ", ".join(missing),
            file=sys.stderr,
        )
        return 2

    overall_rc = 0
    for c in COMMANDS:
        code, detail = _post_command(app_id, token, c)
        if code == 0:
            print(f"✅ Registered /{c['name']} (id={detail})")
        else:
            print(
                f"❌ /{c['name']}: HTTP {code} — {detail}",
                file=sys.stderr,
            )
            overall_rc = 1
    return overall_rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
```

- [ ] **Step 4: Run tests — verify all 3 PASS**

```powershell
pytest tests/test_register_slash_commands.py -v
```

Expected: `3 passed`.

- [ ] **Step 5: ast.parse + commit**

```powershell
python -c "import ast; ast.parse(open('scripts/register_slash_commands.py', encoding='utf-8').read()); print('OK')"
git add scripts/register_slash_commands.py tests/test_register_slash_commands.py
git commit -m "feat(ops): add register_slash_commands.py for Discord upsert

One-shot script to POST /api/v10/applications/{id}/commands. Discord
upserts by name → safe to re-run. --dry-run prints the command list
without any HTTPS call (used in CI smoke). Missing env vars in live
mode exit code 2 with a clear stderr message.

Tests: 3 smoke tests cover existence, dry-run output, and
missing-env-returns-nonzero behaviour."
```

---

## Task 5: Final verification + handoff prep

**Files:**
- Modify: `docs/04_LOGS/DAILY_LOG_2026_05.md` (in `VEXONHQ` repo) — append Session 45 follow-up entry
- Modify: `docs/superpowers/plans/2026-05-28-discord-slash-resources.md` (this file) — mark plan complete

- [ ] **Step 1: Full ast.parse sweep**

```powershell
cd C:\Users\rapee\vexonhq-ocr-api
foreach ($f in @('discord_interactions.py','discord_routes.py','scripts/register_slash_commands.py')) {
  python -c "import ast; ast.parse(open('$f', encoding='utf-8').read()); print('$f OK')"
}
```

Expected: 3 lines ending in `OK`.

- [ ] **Step 2: Full pytest of the new + existing Discord test files**

```powershell
pytest tests/test_resources_snapshot.py tests/test_discord_interactions.py tests/test_register_slash_commands.py -v
```

Expected: all green. Total test count must equal (pre-existing tests in `test_discord_interactions.py`) + 9 new (5 snapshot + 4 formatter) + 3 application_command + 3 register_script.

- [ ] **Step 3: Backup tag origin/main**

```powershell
cd C:\Users\rapee\vexonhq-ocr-api
git fetch origin
git tag backup-pre-discord-resources-2026-05-28 origin/main
git push origin backup-pre-discord-resources-2026-05-28
```

Expected: `* [new tag] backup-pre-discord-resources-2026-05-28 -> backup-pre-discord-resources-2026-05-28`.

- [ ] **Step 4: Append DAILY_LOG follow-up entry**

Add to the end of `C:\Users\rapee\VEXONHQ\docs\04_LOGS\DAILY_LOG_2026_05.md`:

```markdown

---

## 2026-05-28 — Session 45 follow-up (Discord /resources slash command)

**Scope:** First proactive ops command. Read-only snapshot. Brainstormed + spec'd + implemented in the same session.

**Files added/changed (vexonhq-ocr-api):**
- `discord_interactions.py` — +`build_resources_snapshot`, +`format_resources_message`, +`_get_scheduler`, +`psutil` / `shutil` imports
- `discord_routes.py` — +`INTERACTION_APPLICATION_COMMAND = 2`, +slash-command dispatch branch
- `scripts/register_slash_commands.py` — new, idempotent upsert with `--dry-run`
- `tests/test_resources_snapshot.py` — 9 unit tests (5 snapshot + 4 formatter)
- `tests/test_discord_interactions.py` — +3 integration tests for the new branch
- `tests/test_register_slash_commands.py` — 3 script smoke tests
- `docs/superpowers/specs/2026-05-28-discord-slash-resources-design.md` — design spec (commits 7c73209 + b3e5d17)
- `docs/superpowers/plans/2026-05-28-discord-slash-resources.md` — this plan

**Out of scope** (per spec §3 and §10): LINE input, mutating commands (`/restart`, `/redeploy`, `/rollback`), user whitelist, conversational agent.

**Manual step required after push:**
1. Coolify auto-deploys → wait `/health/deep` 200.
2. Locally run `python scripts/register_slash_commands.py` once (needs `DISCORD_APP_ID` + `DISCORD_BOT_TOKEN` in env).
3. Confirm `/resources` autocomplete appears in VEXONHQ Ops Discord.

**Backup tag:** `backup-pre-discord-resources-2026-05-28`.
```

- [ ] **Step 5: Commit the DAILY_LOG entry**

```powershell
cd C:\Users\rapee\VEXONHQ
git add docs/04_LOGS/DAILY_LOG_2026_05.md
git commit -m "docs: DAILY_LOG Session 45 follow-up (Discord /resources)

Implemented per spec docs/superpowers/specs/2026-05-28-discord-slash-resources-design.md.
Backend-only change (vexonhq-ocr-api). Frontend untouched."
```

- [ ] **Step 6: Hand off to TUM — paste block**

Print this block for TUM to push (separate from the implementation commits, which TUM will also push):

```
## ✅ Discord /resources slash command — พร้อม push

### Backend
cd C:\Users\rapee\vexonhq-ocr-api
git log --oneline backup-pre-discord-resources-2026-05-28..main   # review commits
git push origin main

### Frontend (DAILY_LOG)
cd C:\Users\rapee\VEXONHQ
git push origin main

### หลัง deploy
1. รอ /health/deep 200 (~30s)
2. cd C:\Users\rapee\vexonhq-ocr-api
3. $env:DISCORD_APP_ID = "<from Coolify env>"
4. $env:DISCORD_BOT_TOKEN = "<from Coolify env>"
5. python scripts/register_slash_commands.py
6. เปิด Discord VEXONHQ Ops → พิมพ์ "/" → autocomplete ต้องเห็น /resources
7. กด /resources → ภายใน 2 วินาที ต้องเห็น snapshot block
```

---

## Acceptance criteria (from spec §12)

- [ ] All pytest tests green: snapshot (5) + formatter (4) + application_command (3) + register_script (3).
- [ ] ast.parse OK on all touched Python files.
- [ ] Backup tag `backup-pre-discord-resources-2026-05-28` pushed to origin.
- [ ] After TUM push: Coolify deploy passes, `/health/deep` returns 200.
- [ ] `scripts/register_slash_commands.py` prints `✅ Registered /resources` after live run.
- [ ] In Discord, `/resources` appears in autocomplete and replies within 3 seconds.
- [ ] Existing 🔄 Restart and 🩹 Show patch buttons untouched (regression-tested by the existing `tests/test_discord_interactions.py` cases continuing to pass).
- [ ] DAILY_LOG follow-up entry committed in VEXONHQ repo.

---

## Rollback

If something is wrong post-deploy:

| Scope | Action |
|---|---|
| /resources misbehaves (e.g. raises 500) | Edit COMMANDS list in script → re-run `register_slash_commands.py --delete resources` (script enhancement, not in this plan). Or simpler: revert the commit chain back to `backup-pre-discord-resources-2026-05-28` and force-push (with TUM consent). |
| Slash command never appeared in autocomplete | Re-run `python scripts/register_slash_commands.py` (idempotent). If still missing, confirm `DISCORD_APP_ID` matches the Application whose Bot is in the server. |
| Existing buttons stopped working | Run the existing `tests/test_discord_interactions.py::TestDiscordInteractionRoute` suite; any failure here indicates a regression in the new code — git revert the offending commit. |
