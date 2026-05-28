# Discord Slash Command `/resources` — Design Spec

**Date:** 2026-05-28
**Session:** 45 (continuation — feature spec, no code yet)
**Owner:** TUM
**Author:** Claude (brainstorming flow)
**Status:** Draft — awaiting TUM review before implementation plan
**Related:** Tier 2 of the "agent on VPS" conversation; supersedes the earlier "marastation-ai/chat" infra-management idea (chat app remains a separate product for business data).

---

## 1. Context

Today the VEXONHQ Ops Discord channel has two AI-driven features:

- **`auto_diagnose.py`** — when `/health/deep` returns 503, a FastAPI BackgroundTask asks Claude Haiku to explain the failure and posts a diagnosis embed to Discord (~5s after Uptime Robot's own DOWN alert).
- **`discord_interactions.py` + `discord_routes.py:/discord-interaction`** — every diagnosis post carries two inline buttons:
  - **🔄 Restart** → POSTs `Coolify v4 /applications/{uuid}/restart`
  - **🩹 Show patch** → Haiku reads container logs + emits a unified-diff suggestion

These are reactive — they appear only *after* a 503 incident. TUM has no way to check VPS health proactively from his phone unless he opens DigitalOcean or `curl /health/deep` manually.

This spec adds the first proactive ops command.

## 2. Goal

Let TUM type **`/resources`** in the VEXONHQ Ops Discord channel **at any time** and get a one-message snapshot of the VPS state: CPU, RAM, disk, swap, scheduler health, and current deployed commit. Read-only. No mutating behavior.

## 3. Non-goals (explicit YAGNI cuts)

The brainstorming pass surfaced several adjacent ideas and rejected them for this iteration:

- ❌ **LINE input.** TUM chose to keep ops surface on Discord (where the diagnosis + buttons already live) — moving ops chatter into LINE risks polluting the same channel TUM uses for restaurant business intents (stock query, quick expense, daily digest).
- ❌ **`/restart`, `/redeploy`, `/rollback` slash commands.** Restart already exists as a button. The other mutating commands need careful design (confirmation step, backup tag picker, audit log) and TUM declined to scope them in. Future work — see §10.
- ❌ **Conversational agent ("agent, restart frontend").** Out of scope. Brainstorming clearly separated "ops autopilot" from "business chat app".
- ❌ **User whitelist / RBAC on the slash command.** VEXONHQ Ops Discord server has one member (TUM). If membership grows, see §10.
- ❌ **Rate limiting on `/resources`.** Single trusted user; Discord platform already enforces 5 commands/sec/user; per-server throttling is overhead without benefit.

## 4. Approach (chosen)

**Approach B (slash command), recommended during brainstorm, accepted by TUM.**

Discord slash commands are application-scoped. We register `/resources` once with the Discord HTTP API. After registration, Discord routes invocations to our existing `/discord-interaction` webhook endpoint as `INTERACTION_APPLICATION_COMMAND` (type 2) — the same endpoint that already handles button clicks (type 3).

Approach A (button-only) was rejected because the button surfaces only on diagnosis posts; TUM wants anytime checks.
Approach C (button + slash) was rejected as redundant — slash alone covers both contexts.

## 5. Architecture

```
TUM types /resources in Discord
        │
        ▼
Discord servers → POST https://api.marastation.com/discord-interaction
        │           (Ed25519 signature header)
        ▼
discord_routes.py : discord_interaction()
   ├─ verify_signature()              [existing]
   ├─ if itype == INTERACTION_PING    [existing — PONG]
   ├─ if itype == INTERACTION_MESSAGE_COMPONENT  [existing — buttons]
   └─ if itype == INTERACTION_APPLICATION_COMMAND  ◄── NEW
         └─ if data["name"] == "resources":
               snap = build_resources_snapshot()
               return RESPONSE_CHANNEL_MESSAGE (type 4) with formatted text
        │
        ▼
Discord channel renders the response message
```

### Why synchronous (not BackgroundTask + DEFERRED)

- `psutil.cpu_percent(interval=0.1)` ≈ 100ms
- `psutil.virtual_memory() / swap_memory()` ≈ 10ms total
- `shutil.disk_usage("/")` ≈ 10ms
- `_line_scheduler.get_jobs()` ≈ 1ms
- `os.environ.get("SOURCE_COMMIT", "unknown")` ≈ 0ms
- **Total budget ≈ 150–200ms.** Discord deadline is 3000ms. Plenty of headroom.

No background task = simpler code, no `RESPONSE_DEFERRED_CHANNEL_MESSAGE` token-edit dance.

## 6. Components

### 6.1 `discord_interactions.py` — new function `build_resources_snapshot()`

Located alongside existing helpers (`coolify_restart`, `coolify_fetch_logs`). ~40 lines.

```python
def build_resources_snapshot() -> dict[str, Any]:
    """
    Collect a one-shot VPS resource snapshot for the /resources slash
    command. Never raises — every metric is independently try/excepted
    so a single failed probe degrades to '—' instead of crashing the
    whole response.

    Returns a dict with keys:
        cpu_pct, ram_pct, ram_used_gb, ram_total_gb,
        disk_pct, disk_used_gb, disk_total_gb,
        swap_pct, swap_used_mb, swap_total_gb,
        scheduler_running, scheduler_jobs,
        git_sha, warnings: list[str]
    All metric values are floats or None (None when the probe failed).
    """
```

Warning rules (also documented inline):
- `cpu_pct > 80` → `"⚠️ CPU high — wait before next deploy"`
- `ram_pct > 80` → `"⚠️ RAM high — risk of OOM kill"`
- `disk_pct > 80` → `"⚠️ Disk filling — run docker prune"`
- `swap_pct > 50` → `"⚠️ Swap heavy use — investigate process"`
- scheduler not running → `"⚠️ APScheduler not running — digests will not fire"`

### 6.2 `discord_interactions.py` — new function `format_resources_message(snap)`

~30 lines. Pure formatter — takes the dict, returns the Discord-markdown string:

```
📊 VPS Resources — vexonhq-core
─────────────────────────────────
🖥️  CPU            28.0%
💾 RAM            29.5%   (1.18 / 4.00 GB)
💿 Disk           61.4%   (24.6 / 40.0 GB)
📦 Swap            0.1%   (4 MB / 4.0 GB)
⏰ Scheduler      7 jobs running
🚀 Last deploy    8ad1f51
─────────────────────────────────
⚠️ Warnings: none
```

When a metric is `None`: display `—` in its slot.
When `warnings` list is non-empty: render each on its own line below the divider.

### 6.3 `discord_routes.py` — new branch in `discord_interaction()`

~15 lines. Imports + branch added after the existing `INTERACTION_MESSAGE_COMPONENT` block:

```python
if itype == INTERACTION_APPLICATION_COMMAND:
    data = payload.get("data") or {}
    cmd_name = (data.get("name") or "").lower()
    if cmd_name == "resources":
        snap = di.build_resources_snapshot()
        return JSONResponse({
            "type": RESPONSE_CHANNEL_MESSAGE,
            "data": {"content": di.format_resources_message(snap)},
        })
    # Unknown command — graceful error so TUM can spot registration drift
    return JSONResponse({
        "type": RESPONSE_CHANNEL_MESSAGE,
        "data": {"content": f"⚠️ Unsupported command: `{cmd_name[:32]}`"},
    })
```

Constant `INTERACTION_APPLICATION_COMMAND = 2` added to module imports.

### 6.4 `scripts/register_slash_commands.py` — new file

One-shot registration script. ~60 lines including a `--dry-run` flag and clear stdout for verification.

```
usage: python scripts/register_slash_commands.py [--dry-run]

Registers all VEXONHQ slash commands with Discord (idempotent upsert).
Reads DISCORD_APP_ID + DISCORD_BOT_TOKEN from env. Prints the resulting
command list and Discord-assigned IDs.

Currently registers:
  /resources — Show VPS resource snapshot (CPU/RAM/disk/swap/deploy)
```

POSTs to `https://discord.com/api/v10/applications/{APP_ID}/commands` with `Authorization: Bot {TOKEN}`. Discord upserts by name — safe to re-run.

The list of commands lives in the script as a Python list literal so adding `/restart`, `/redeploy`, etc. later means appending one dict and re-running.

## 7. Data sources

| Metric | Source | Failure mode |
|---|---|---|
| CPU% | `psutil.cpu_percent(interval=0.1)` | `None` → `—` |
| RAM% | `psutil.virtual_memory().percent` | `None` → `—` |
| RAM used/total | same call | `None` |
| Disk% | `shutil.disk_usage("/").used / .total * 100` | `None` → `—` |
| Swap% | `psutil.swap_memory().percent` | `None` → `—` |
| Scheduler running | `line_bot_routes._scheduler.running` | `False` + warning |
| Scheduler jobs | `_scheduler.get_jobs()` count | `0` + warning |
| Git SHA | `os.environ.get("SOURCE_COMMIT", "unknown")[:7]` | `"unknown"` |

**`SOURCE_COMMIT`** is the standard Coolify v4 Nixpacks build-arg env var. The implementation step will SSH to the running container once to `printenv | grep -i commit` and confirm the exact var name; if Coolify uses a different name on our deployment (e.g. `COOLIFY_GIT_COMMIT_SHA`), the script reads the actual name discovered there. If no commit-SHA env var is present at all, fall back to `"unknown"` — non-blocking.

## 8. Error handling

The guiding rule: **`/resources` must always reply within Discord's 3-second window with *something*, even if every probe fails.**

Practical implications:
- Every metric collection is wrapped in its own `try / except Exception` block. A failure logs at `WARNING` level (so it shows up in Coolify stdout) but does not abort the snapshot.
- The handler in `discord_routes.py` is itself wrapped in a top-level try/except that, on any unexpected error, returns a Discord-message response with the error class name + first 100 chars of the error string. This keeps Discord from showing the user "The application did not respond" — which is worse UX than a visible error.
- The Discord 401 path (bad/missing Ed25519 signature) is unchanged. Outside attackers cannot reach this code path; the existing fail-closed behavior in `verify_signature` is sufficient.

## 9. Testing

Per CLAUDE.md working rule 1 ("Verify before commit") and rule 5 (run pytest before handing diff to TUM).

### 9.1 Unit — `tests/test_resources_snapshot.py` (new file)

| Test | What it asserts |
|---|---|
| `test_snapshot_happy_path` | Returns dict with all expected keys; numeric metrics within 0–100. |
| `test_snapshot_resilient_to_psutil_failure` | `psutil.cpu_percent` raises → snapshot still returns; `cpu_pct is None`; other metrics still collected. |
| `test_snapshot_warnings_above_threshold` | Monkey-patched RAM at 85% → `"RAM"` substring appears in `warnings`. |
| `test_format_resources_message_handles_none` | `cpu_pct=None` → formatted line contains `—` not `None`. |
| `test_format_includes_git_sha_short` | `SOURCE_COMMIT=8ad1f51abc123` → output contains `8ad1f51`, not the full hash. |

### 9.2 Integration — additions to `tests/test_discord_interactions.py`

| Test | What it asserts |
|---|---|
| `test_application_command_resources_returns_snapshot` | POST with `type=2 data.name=resources` → 200; body `type=4`; content contains `"VPS Resources"` and `"RAM"`. |
| `test_application_command_unknown_name` | POST with `data.name=foo` → 200 with `"Unsupported command"` content. |
| `test_application_command_bad_signature_401` | Existing signature check still blocks the new command path. |

### 9.3 Script — `tests/test_register_slash_commands.py` (new file)

| Test | What it asserts |
|---|---|
| `test_dry_run_does_not_call_discord` | `python scripts/register_slash_commands.py --dry-run` exits 0; stdout lists `resources`; no `https://discord.com` HTTP call traced. |

### 9.4 Live verification (manual, post-deploy)

1. Push → Coolify auto-deploy → `/health/deep` 200.
2. Run `python scripts/register_slash_commands.py` from local PowerShell.
3. In Discord, type `/` in #vexonhq-ops — autocomplete should list `/resources`.
4. Invoke `/resources` — expect a snapshot reply within 2 seconds.
5. *(Optional)* Force a warning: SSH to VPS, run `stress-ng --vm 1 --vm-bytes 90% --timeout 10s` (install with `apt install stress-ng` if missing) to spike RAM, immediately call `/resources` — confirm the RAM warning fires. Skip this step if SSH/stress-ng access is inconvenient; the unit test `test_snapshot_warnings_above_threshold` already covers the threshold logic.

### 9.5 `verify.ps1 -Smoke` — no change required

The smoke suite hits 55 unauthenticated and JWT-authed routes. `/discord-interaction` is signature-gated and not in the smoke list. Unit + integration tests above are the gate.

## 10. Future extensions (explicitly not now)

If TUM later wants to expand the ops surface, the recommended order is:

1. **`/logs [n]`** — read-only tail of Coolify stdout, last `n` lines (default 50). Builds on existing `coolify_fetch_logs()`. ~30 lines.
2. **User whitelist** — when the Discord server gets >1 member, add `_is_authorized_ops_user(payload.get("member", {}).get("user", {}).get("id"))` (guild context — the bot is added to a server, not DMed; payload structure differs between guild and DM invocations) as a single gate at the top of the application_command branch. Single config list in env.
3. **`/redeploy`** — mutating; needs confirmation step (button "Confirm redeploy?" appearing after the slash command). Requires real audit log entry. Larger spec — re-brainstorm separately.
4. **`/rollback <tag>`** — mutating; needs backup-tag picker (autocomplete from `git tag -l 'backup-pre-*'`). Largest spec — separate session.

The current spec is intentionally narrow so that these future commands can plug in as new entries in the `cmd_name` switch without re-architecting.

## 11. Files touched (summary)

| File | Change | Approx LOC |
|---|---|---|
| `discord_interactions.py` | +`build_resources_snapshot()`, +`format_resources_message()`, +`INTERACTION_APPLICATION_COMMAND` const | +70 |
| `discord_routes.py` | +branch in `discord_interaction()` for `APPLICATION_COMMAND` | +15 |
| `scripts/register_slash_commands.py` | new file | +60 |
| `tests/test_resources_snapshot.py` | new file | +80 |
| `tests/test_discord_interactions.py` | +3 tests | +50 |
| `tests/test_register_slash_commands.py` | new file | +20 |
| `docs/superpowers/specs/2026-05-28-discord-slash-resources-design.md` | this file | — |

**Total ≈ 295 LOC including tests.** No requirements.txt change.

## 12. Acceptance criteria

The implementation is done when **all** of these hold:

- [ ] `pytest tests/test_resources_snapshot.py tests/test_discord_interactions.py tests/test_register_slash_commands.py -v` → all green.
- [ ] `python -c "import ast; ast.parse(open('discord_routes.py').read())"` and same for `discord_interactions.py`, `scripts/register_slash_commands.py` → all OK.
- [ ] After TUM pushes + Coolify redeploys: `python scripts/register_slash_commands.py` prints `✅ Registered /resources`.
- [ ] In Discord VEXONHQ Ops channel, typing `/` shows `/resources` in autocomplete.
- [ ] Invoking `/resources` replies within 3 seconds with a snapshot message containing CPU, RAM, Disk, Swap, Scheduler, Last deploy, Warnings.
- [ ] DAILY_LOG entry written; AGENTS.md gets a new pitfall row only if Coolify behaviour surprises us during deploy.
- [ ] Existing 🔄 Restart and 🩹 Show patch buttons still function (regression check on next 503).

## 13. Rollback

If the slash command misbehaves in production:

1. **Quick disable (no redeploy):** Re-run `register_slash_commands.py --delete resources` (script supports the inverse). Discord drops the command from autocomplete within ~60 seconds; existing buttons are unaffected.
2. **Full rollback:** `git revert <commit>` and push. Coolify redeploys without the new code. Buttons unaffected.
3. **Tagged before push:** Backup tag `backup-pre-discord-resources-2026-05-28` per CLAUDE.md rule 5; rollback is `git reset --hard <tag>`.
