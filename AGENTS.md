---
name: vexonhq-ocr-api-agent
description: AI coding agent for vexonhq-ocr-api FastAPI backend (Mara Station restaurant ops). OCR + P&L + LINE bot + Discord auto-heal. Supabase Postgres. Deploys via Coolify auto-build on push to main.
---

# AGENTS.md — vexonhq-ocr-api backend

> Universal rules for AI agents (Claude Code, Cursor, Codex, Aider).
> Stack / DB column cheat sheet / route inventory → see `CLAUDE.md`.
> Project history / specs → `C:\Users\rapee\VEXONHQ\docs\`.

> **🔁 Multi-agent flow + 3-AI self-review → source of truth = global `~/.claude/CLAUDE.md`.**
> ตั้งแต่ **2026-06-08 roles สลับ**: *Claude เขียนโค้ดเอง*, Antigravity เป็น reviewer. ก่อนรายงาน TUM, Claude self-review ด้วย `C:\Users\rapee\review.ps1` (Tier 2 **Codex** → Tier 3 **Gemini REST** ฟรี สลับอัตโนมัติเมื่อ Codex หมด limit). รันเฉพาะงานสำคัญ (auth/เงิน/security/migration/หลายไฟล์); งาน routine ใช้ `-Engine gemini`.
---

## Persona

You are a careful coding assistant for **TUM** — a non-developer
owner of มาลาทวีวัฒนา restaurant (~660 bills/month) who reads/writes
Thai natively and deploys via Coolify. Speak Thai+English. Use plain
language a non-programmer can act on. Default to terse + verifiable.

---

## Division of labor — FIRM RULE (updated 2026-06-08, roles swapped)

Follows TUM's global rule (`~/.claude/CLAUDE.md`):
- **Claude Code writes, edits, and owns the application code** in this repo (backend). Implement directly — no `HANDOFF.md` specs.
- **Antigravity (Gemini IDE) is the reviewer** — it adversarially reviews Claude's diffs after the fact. Take its findings as genuine signal.
- Claude self-reviews BEFORE Antigravity sees a diff: code-review / simplify / verify skills + `review.ps1` (Codex → Gemini failover) on substantive changes.
- Claude owns git commit + push (**only after TUM Confirm**) + the mandatory clean-rebuild verification + doc updates.

The Persona above + the 6-step workflow, pitfalls, and boundaries below apply to Claude as the author. (Supersedes the 2026-06-02 split where Antigravity wrote the code and Claude was QA-only.)

---

## Commands you'll run

```powershell
# Local dev (port 8000)
.\.venv\Scripts\Activate.ps1
$env:DATABASE_URL = "postgresql://..."   # Supabase pooler
uvicorn main:app --reload --port 8000

# Pre-handoff gate — all of these MUST pass
python -c "import ast; ast.parse(open('<file>.py', encoding='utf-8').read())"   # per-file
pytest tests/test_<feature>.py -v                                                # if tests exist
.\verify.ps1            # compileall on every .py (~2 s)
.\verify.ps1 -Smoke     # + live 63-route smoke against deployed backend (as of Session 40)

# Backup tag before any change to main
git fetch origin
git tag backup-pre-<descriptor>-YYYY-MM-DD origin/main
# (TUM pushes the tag; Claude does NOT push)

# Verify column names before writing SQL — never trust LLM memory
# (run in psql or supabase SQL editor)
SELECT column_name, data_type FROM information_schema.columns
WHERE table_schema='public' AND table_name='<table>' ORDER BY ordinal_position;
```

---

## The 6-step workflow (mandatory)

**Backup → แก้ไข → Test หลายรอบ → Confirm → ขออนุมัติ → Claude push → Verify + Report**

1. **Backup**: prepare `git tag backup-pre-<X>-YYYY-MM-DD origin/main` command
2. **Edit**: minimal-diff; new files first, then edit existing
3. **Test หลายรอบ**: `ast.parse` per file → `pytest` if tests exist → `.\verify.ps1` → local `uvicorn` endpoint probe with `Invoke-WebRequest`
4. **Confirm**: all green before claiming ready
5. **ขออนุมัติ TUM**: show the diff + commit message (HEREDOC) + Coolify env-var instructions if needed, and ask for approval
6. **Claude push (หลัง Confirm)**: Claude runs `git push` → Coolify auto-deploys ~20-30s → Claude **verifies the rebuild finished cleanly EVERY TIME** before reporting: `/health` 200 + `/health/deep` healthy **and** the shared-VPS CPU is back to normal (`cpu_pct` from `/health/deep`). The 3 apps share the 4GB VPS, so any push rebuilds and spikes CPU — re-check until it settles, don't report "done" while a build is still running. *(Clean-rebuild check added 2026-06-02 per TUM.)*

---

## Post-task closure routine (mandatory)

Run this **before** drafting the step-5 paste block — it is part of the task definition, not optional ceremony.

1. **Update docs**:
   - `docs/TOMORROW.md` (this repo) — update backend priorities, Sentry status, open items.
   - `C:\Users\rapee\VEXONHQ\docs\04_LOGS\DAILY_LOG_<YYYY_MM>.md` for the **CURRENT month** (e.g. `DAILY_LOG_2026_06.md` for June 2026 — do NOT keep writing to a past month's file) — **always**, one entry per session. Use `## Session N — YYYY-MM-DD` heading. ✅ Done / 🟡 Pending / 🔵 Known follow-ups blocks.
   - `C:\Users\rapee\VEXONHQ\docs\01_PROJECT\CHANGELOG.md` — when a feature or fix ships.

2. **Update `AGENTS.md`** (this file) **only when an agent-relevant change occurred** — new rule, new pitfall, new infra detail. Append a bullet to the relevant existing section; date the addition in the commit message body.

3. **Push to GitHub** — Claude composes the commit (no `Co-Authored-By:` trailer), shows TUM the diff + message, and asks approval. **After TUM confirms, Claude runs `git push` itself**, then verifies + reports. Do NOT push without an explicit Confirm for that push. *(Updated 2026-06-02: was "TUM pushes from his own PowerShell; Claude never pushes".)*

**Why this exists:** The next Claude session loads `AGENTS.md` + `CLAUDE.md` + auto-memory first; skipping doc updates makes the new session start with stale context.

---

## Boundaries

### ✅ Always do
- Run `.\verify.ps1` before handoff (catches Phase-32-style endpoint regression)
- Verify SQL columns against `information_schema.columns` before writing queries
- Add new public endpoints to `PUBLIC_PATHS` set in `main.py:275` if they need to bypass JWT
- Use `methods=["GET", "HEAD"]` for any endpoint Uptime Robot will monitor
- Tag a rollback target on `origin/main` before TUM pushes
- Add `@heartbeat(<job_id>)` decorator to any new APScheduler job (P1.2)

### ⚠️ Ask first
- Adding a new router file (must register in `main.py` + smoke test)
- Adding a new pip dependency (manylinux wheel availability matters for Coolify Nixpacks)
- Changing the JWT contract or auth flow (`auth_routes.py`)
- Database schema changes (write idempotent migration, commit to repo before applying)
- Touching `line_bot_routes.py`, `alerts_webhook_routes.py`, or scheduler files (coordination zone with other-Claude worktrees)
- Anything that requires a new Coolify env var (instruct TUM, don't paste secret in chat)
- Modifying `_ADMIN_USERNAMES` logic or `_get_role()` in `auth_routes.py` — affects all user access

### 🚫 Never do
- `git push` **without TUM's Confirm** — Claude may push only after showing the diff + commit and getting TUM's approval for that push (updated 2026-06-02; previously "TUM pushes always")
- `except: pass` or `except Exception: pass` — log + re-raise or `log.exception()`
- Hallucinated SQL columns: NEVER `net_price`, `b.status`, `b.branch`, `staff`, `r.menu_name`, `ri.quantity` — see `CLAUDE.md` cheat sheet for verified columns
- `git reset --hard`, `git checkout HEAD -- .` reflexively
- Add emojis to code or commit messages
- Share secrets in chat (bot tokens, API tokens, hashed passwords)
- Delete `vexonhq-clean-base` snapshot
- Skip `verify.ps1` — every Phase-32-style regression in history would have been caught by it

---

## Critical pitfalls (debugged the hard way — don't repeat)

**1. Coolify app UUID = URL subdomain prefix.**
```bash
# For vexonhq-ocr-api the URL is:
# https://b4zhad8qkoxjushdq8465056.178.128.31.76.sslip.io
# → UUID is b4zhad8qkoxjushdq8465056 (24 chars before first dot)
# NOT iwa8jm7gvjqi3awnslk924a4 (that's the Docker image tag suffix).
# Verify:
curl -H "Authorization: Bearer $COOLIFY_API_TOKEN" \
  http://178.128.31.76:8000/api/v1/applications/<UUID>
# 200 + JSON = right UUID. 404 "Application not found" = wrong.
```

**2. Discord Bot Token ≠ OAuth2 Client Secret.**
```python
# Bot Token (Bot tab → Reset Token) = ~70 chars, 2 dots, "MTUwNjgz..."
# Client Secret (OAuth2 tab) = 32 chars, no dots, "rUpsufnu..."
# Only Bot Token works with `Authorization: Bot <token>`.
# Verify env-set token:
python -c "import os; t=os.environ['DISCORD_BOT_TOKEN']; print(f'len={len(t)} dots={t.count(chr(46))}')"
# Expected: len=70-72 dots=2.  Got len=32 dots=0 → Client Secret leaked in.
```

**3. Uptime Robot free plan = HEAD only.**
```python
# ❌ BAD — UptimeRobot probe returns 405
@router.get("/cron/health")

# ✅ GOOD
@router.api_route("/cron/health", methods=["GET", "HEAD"])
```

**4. PUBLIC_PATHS gotcha for new monitor/webhook routes.**
JWT middleware (main.py:275) returns 401 for any path NOT in the
`PUBLIC_PATHS` set. Add new monitor / webhook / Discord-interaction
routes to that set. Session 28 `/cron/health` lost 401 to this.

**5. SQL column hallucinations.** Run the
`information_schema.columns` query (see "Commands" above) FIRST.
Never trust LLM memory of column names.

**6. Files removed during refactor.** Session 16 commit `742b618`
silently deleted 165 lines of `/inventory/ai-order-advice`. ALWAYS
run `.\verify.ps1 -Smoke` after a refactor PR. Pattern to recover:
`git log -S "<function_name>"`.

**7. Discord API behind Cloudflare bans default `Python-urllib` UA.**
Any new `urllib.request` call to `discord.com/api/*` MUST set a
`User-Agent` header. The default `Python-urllib/3.x` gets HTTP 403
with body `error code: 1010` (Cloudflare browser-signature ban).
```python
req = urllib.request.Request(
    url, data=body, method="POST",
    headers={
        "Authorization": f"Bot {token}",
        "Content-Type": "application/json",
        "User-Agent": "VEXONHQ-OpsBot (vexonhq.com, 1.0)",  # required
    },
)
```
The 4 Discord call sites already in `discord_interactions.py` (lines
361/417/476/526) all set this. Session 45 `scripts/register_slash_commands.py`
shipped without it → blocked at first invocation → fixed in `daeef7f`.

**8. Discord slash commands collide across bots in shared servers — namespace under one top-level command.**
The VEXONHQ Ops Discord server hosts multiple bots (Sentry, GitHub,
Wordle, etc.). Each bot's slash commands appear in the SAME autocomplete
list. Generic names like `/help`, `/logs`, `/info` will collide — Sentry
already owns `/help`, and TUM accidentally invoked Sentry's `/help`
instead of ours during initial testing (Session 45).

Solution: register ONE top-level command per bot, with subcommands
underneath. Discord's autocomplete then shows `/vex resources` and
`/vex help` (clearly ours, no collision) instead of `/resources` and
`/help` (which would compete with every other bot's commands).

```python
# WRONG — every command is a top-level name in the global pool
COMMANDS = [
    {"name": "resources", "type": 1, "description": "..."},
    {"name": "help",      "type": 1, "description": "..."},  # collides with Sentry
]

# RIGHT — one top-level namespace, two subcommands
COMMANDS = [
    {
        "name": "vex", "type": 1,
        "description": "VEXONHQ Ops Bot",
        "options": [
            {"name": "resources", "type": 1, "description": "..."},
            {"name": "help",      "type": 1, "description": "..."},
        ],
    },
]
```

Companion rule: when *renaming* or *removing* commands, use Discord's
**bulk overwrite** endpoint (`PUT /applications/{id}/commands`)
instead of POST-per-command. PUT replaces the entire command set
atomically — anything not in the body is deleted. POST-per-command
leaves orphaned old commands forever (no auto-cleanup).

Refactored in `ab053aa` after live UX feedback. Dispatch pattern in
`discord_routes.py` reads `data["options"][0]["name"]` for the
subcommand after matching the top-level `data["name"] == "vex"`.

---

**58. `dr_backup` dies SILENTLY if Coolify loses the `SUPABASE_S3_*` env vars — it exits BEFORE writing a heartbeat.** (2026-06-08, H3) `scripts/backup.py` `main()` reads `SUPABASE_S3_ACCESS_KEY_ID` / `SUPABASE_S3_SECRET_ACCESS_KEY` from `os.environ`; on a missing var it `send_discord_alert()` + `sys.exit(1)`. The S3 keys can vanish from the Coolify app on a redeploy (still present in the local `.env`). The 02:00-Bangkok cron then fires nightly but aborts, `job_heartbeat.dr_backup.last_success_at` freezes, `/cron/health` returns 503, and the stale-job watcher only notices hours/days later — NO DR backups in between (this incident: none 2026-06-05→08). **Recovery:** re-add `SUPABASE_S3_ACCESS_KEY_ID` + `SUPABASE_S3_SECRET_ACCESS_KEY` + `SUPABASE_S3_REGION` in Coolify env (values are in the local `.env`) → Restart → run `python scripts/backup.py` in the Coolify Terminal to refresh `last_success_at` immediately. Also set `DISCORD_OPS_WEBHOOK_URL` (was missing) so backup's own ConfigError alert reaches Discord, not just the late stale-job watcher. **Hardening shipped this entry:** the ConfigError path now writes a failure-heartbeat (when `DATABASE_URL` is present) before exit, so the next config failure surfaces in `job_heartbeat` (`error_count` / `last_error_message`) instead of going silent.

---

## Workspaces

```
C:\Users\rapee\Documents\Claude\Projects\MaraStation\   ← draft / staging
C:\Users\rapee\VEXONHQ\                                  ← LIVE frontend
  └─ docs\                                                ← canonical project docs
C:\Users\rapee\vexonhq-ocr-api\                          ← LIVE backend (THIS REPO)
```

Backend repo has no `docs/` of its own — all project docs live in the
frontend repo under `C:\Users\rapee\VEXONHQ\docs\`.

---

## Infrastructure quick-ref

- **VPS**: `vexonhq-core` on DigitalOcean (SGP1, IP `178.128.31.76`)
- **Coolify**: `http://178.128.31.76:8000/`
- **Backend URL**: `https://api.marastation.com` (Session 32 migration; sslip.io fallback still resolves)
- **Backend UUID**: `b4zhad8qkoxjushdq8465056` (Coolify API — derived from original sslip subdomain prefix)
- **Database**: Supabase Cloud Free, project `mara-ai-prod`, Tokyo region
- **Snapshots**: keep 3 max. `$0.06/GB/month`. Total cap ~$5/month.
- **Auto-heal pipeline**: L3 (Restart) + L3.5 (Show patch) shipped Sessions 29 + 31. See `CLAUDE.md` and `docs/01_PROJECT/ROADMAP.md`.
- **Operations RUNBOOK (deploy / monitor / recover / handover)**: `C:\Users\rapee\VEXONHQ\docs\06_SUPPORT\RUNBOOK.md` — single source of truth for running the whole system (key-person-risk remediation, audit F3). Update it whenever infra/secret/process changes.
- **Second-operator onboarding**: `C:\Users\rapee\VEXONHQ\docs\06_SUPPORT\HANDOVER.md` — learning path + graded drills (supervised deploy, incident restart) + "if X then Y" panic card, pairing with the RUNBOOK reference (closes F3).

---

## Iterate this file

The best AGENTS.md grows from edge cases, not upfront planning.
**Append a new bullet to the relevant section** when an agent makes a
mistake the same rule would have prevented. Don't edit existing rules
without TUM's explicit ask. Date the addition in the commit message.

**9. ai-link-ingredients: AI may return Thai text as ingredient_id.** (Session 34, 2026-05-23)
```python
# Claude Haiku occasionally returns "ไม่มี ID ต้นประกอบ" or similar Thai text
# instead of a valid UUID when it cannot find a matching ingredient.
# Inserting that text into recipe_ingredients (UUID column) causes:
#   psycopg2.errors.InvalidTextRepresentation: invalid input syntax for type uuid
# Fix: build valid_ingredient_ids set from all_ingredients before the apply loop;
# skip + log.warning any ingredient_id not in that set.
valid_ingredient_ids = {i["id"] for i in all_ingredients}
if ing_id not in valid_ingredient_ids:
    logger.warning("ai-link: skipping invalid ingredient_id %r", ing_id)
    skipped += 1
    continue
```

**10. detect-only and other async endpoints must NOT call blocking I/O directly.** (Session 36, 2026-05-23)
```python
# ❌ BAD — blocks uvicorn event loop; server appears "ค้าง" for 10-30 s on large XLSXs
@router.post("/detect-only")
async def detect_only(file: UploadFile = File(...)):
    content = await file.read()
    _, rtype = read_and_detect(content, file.filename or "")  # pd.read_excel() × 3 in event loop

# ✅ GOOD — moves blocking work to thread pool; event loop stays responsive
import asyncio
@router.post("/detect-only")
async def detect_only(file: UploadFile = File(...)):
    content = await file.read()
    _, rtype = await asyncio.to_thread(read_and_detect, content, file.filename or "")
```
Rule: any `async def` endpoint that calls sync, CPU/IO-heavy functions (pandas, openpyxl, cv2, etc.)
MUST use `asyncio.to_thread()` or `BackgroundTasks`. The actual import endpoint `/pos/import`
already does this correctly via `background_tasks.add_task()`.

**11. DigitalOcean API token for snapshots requires `image:create` scope explicitly.** (Session 40, 2026-05-25)
```
# DigitalOcean scoped tokens — common mistake:
# A token with "snapshot:read+delete" + "image:read+delete" is NOT enough.
# Creating a snapshot requires the separate "image:create" scope.
# The error at runtime:
#   403 Forbidden: "You are missing the required permission image:create."
# This fails silently (job runs, error_count++ but no alert fires).
#
# When creating a new DO token for vexonhq-ocr-api:
# Required scopes: image:create, image:delete, droplet:read
# Env var: DO_API_TOKEN (set in Coolify → vexonhq-ocr-api → Environment)
```
Verify: after redeploying with new token, check `job_heartbeat` table:
```sql
SELECT job_id, run_count, error_count, last_success_at
FROM job_heartbeat WHERE job_id = 'weekly_do_snapshot';
```
`last_success_at` should populate the Sunday after the token update.

**12. verify_token and create_token must use the same secret.** (Session 41, 2026-05-25)
```python
# create_token() signs with JWT_SECRET (no aud claim)
# verify_token() must also accept JWT_SECRET — not ONLY SUPABASE_JWT_SECRET

# ❌ BAD — broke ALL auth silently (Session 40 commit 6069f32)
def verify_token(token):
    if not SUPABASE_JWT_SECRET:
        return None  # rejects ALL tokens if env var missing
    payload = jwt.decode(token, SUPABASE_JWT_SECRET, audience="authenticated")
    # ↑ fails: wrong key + no aud claim in self-issued tokens

# ✅ GOOD — dual-path: Supabase SSO first, self-issued fallback
def verify_token(token):
    if SUPABASE_JWT_SECRET:
        try:
            payload = jwt.decode(token, SUPABASE_JWT_SECRET, audience="authenticated")
            payload["_role"] = payload.get("app_metadata", {}).get("role", "staff")
            return payload
        except jwt.ExpiredSignatureError:
            return None  # expired = terminal
        except jwt.InvalidTokenError:
            pass         # wrong key/aud = try self-issued path
    try:
        payload = jwt.decode(token, JWT_SECRET, options={"verify_aud": False})
        payload["_role"] = payload.get("role", "staff")
        return payload
    except Exception:
        return None
```
Rule: whenever `create_token` signing key or claims change, `verify_token` MUST be updated
in the same commit. Test with `pytest tests/test_workflow.py -k "auth"` before push.

**13. v_daybook P&L queries MUST exclude equity/transfer sources — duplicated in 7+ files, missing = Session-6 incident.** (Session 43, 2026-05-27)
```python
# Every query that aggregates v_daybook for P&L purposes (income/expense totals,
# category breakdowns, narrative inputs, dashboard cards) MUST filter out these
# sources, otherwise equity injections + bank transfers inflate the numbers.

# ❌ BAD — Session-6 incident pattern, found again in phase10_narrative_routes.py
#         (audit C1, fixed commit 9296ed5). Inflated April 2026 income by 62K baht.
cur.execute("""SELECT SUM(d.amount) FROM v_daybook d
               WHERE d.entry_date BETWEEN %s AND %s""", (first, last))

# ✅ GOOD — use the same exclusion list as pnl_routes.py:96-99
_EXCLUDED_SOURCES_SQL = (
    "AND d.source NOT IN ("
    "'owner_capital', 'owner_advance', 'transfer_error', "
    "'bank_statement', 'vendor_payment', "
    "'grab_payout', 'lineman_payout', "
    "'pos_cash_deposit', 'cash_withdrawal'"
    ")"
)
cur.execute(f"""SELECT SUM(d.amount) FROM v_daybook d
               WHERE d.entry_date BETWEEN %s AND %s
                 {_EXCLUDED_SOURCES_SQL}""", (first, last))
```
Files known to use the exclusion (cross-check before duplicating again): `pnl_routes.py`, `phase2_routes.py` (overview branch), `phase10_narrative_routes.py` (Session 43 fix). Files known to LACK it as of 2026-05-27 audit: `phase2_routes.py` /dashboard/category-trends (C3), `yearly_routes.py` (C4 source-mixing). Long-term fix: extract to DB view `v_daybook_pnl` so SQL stays DRY — until then, splice the constant.

Verify your change: run an A/B query via Supabase MCP for a month that contains equity rows (April 2026 has 52 `owner_advance` + 10 `owner_capital`) — old vs new totals should differ; the new value should match `/pnl/monthly` for the same month.

Update Session 44 (2026-05-28): `v_daybook_pnl` is now live in production and used by `/pnl/*`, `/scorecard`, `/dashboard/overview`, `/daybook/summary`, `/pos/food-cost`, `/yearly`, `/pnl/narrative`. New P&L queries should `FROM public.v_daybook_pnl` directly — no inline exclusion needed.

**14. Don't use positional `r.iloc[N]` for column access in POS Excel parsers.** (Session 44, 2026-05-28)
```python
# ❌ BAD — silent wrong-cell write if FoodStory adds or reorders columns
"total_discount": to_num(r.iloc[4]) or 0,   # "ส่วนลด" merged col

# ✅ GOOD — `r.get(canonical_name)` resolves via the normalize_columns map
"total_discount": to_num(r.get("ส่วนลด")) or 0,
```
Rule: every field in `pos_import.py` that maps to a POS report column MUST go through `r.get("<canonical Thai name>")`. The canonical name must exist in `_CANONICAL_COLS`. Positional `iloc` is fragile and was the root cause of B7-C5 — `total_discount` could end up reading `ยอดรวม` or `ค่าบริการ` instead.

**15. Async def handlers must NOT call blocking I/O directly.** (Session 44, 2026-05-28; first seen Session 36)
```python
# ❌ BAD — blocks uvicorn event loop, freezes /health for the whole parse
@router.post("/import_sync", response_model=ImportResponse)
async def import_pos_excel_sync(...):
    content = await file.read()
    df, rtype = read_and_detect(content, file.filename or "")   # pd.read_excel × 3, 10-30s
    cur.executemany("INSERT ...", rows)                          # blocking psycopg2

# ✅ GOOD — sync def (Starlette runs in threadpool) + file.file.read() instead of await file.read()
@router.post("/import_sync", response_model=ImportResponse)
def import_pos_excel_sync(...):
    content = file.file.read()
    df, rtype = read_and_detect(content, file.filename or "")
    cur.executemany("INSERT ...", rows)

# ✅ ALSO GOOD — keep async but offload to threadpool
@router.post("/detect-only")
async def detect_only(file: UploadFile = File(...)):
    content = await file.read()
    _, rtype = await asyncio.to_thread(read_and_detect, content, file.filename or "")
```
Rule: any handler that calls pandas (`pd.read_excel`), psycopg2 (`cur.execute*` on big inputs), or other blocking C extensions must NOT be `async def` unless every blocking call is wrapped in `asyncio.to_thread()` or `BackgroundTasks`. Default to plain `def` for import paths — simpler, no foot-gun. (Audit B7-C3 / Session 36 incident class.)

**16. P&L is CASH / bank-statement basis (Session 46, 2026-05-30) — `vendor_bill` is NOT a P&L expense.**
Expense = actual money out (bank statement debits + `pos_cashflow` + payroll/rent/utility + manual), counted once. The OCR'd supplier invoice (`vendor_bill`) is kept for AP / line-item detail / slip-match ONLY — it was REMOVED from `v_daybook` (Branch 8) because counting both the invoice and its bank/cash payment double-counted supplier cost. **Do NOT re-add Branch 8.** See `migrations/2026_05_30_vdaybook_cashbasis_exclude_vendor_bill.sql` and memory [[project-pnl-cash-basis]].

**17. `v_daybook` in prod DRIFTS from the repo — always `pg_get_viewdef` the LIVE view before editing it.** (Session 46)
Prod had uncommitted fixes (delivery dedup `GREATEST(net_total - rider_gross)`, bank rider-income exclusion) not in repo migration 17. Editing from the stale repo file would have regressed prod. A re-audit "delivery double-count" finding was a FALSE POSITIVE because it read the repo, not the live view. Capture any live-only definition back into a migration.

**18. KBank statement parser is LINE-BASED; verify every import against the PDF's own `รวมฝาก/รวมถอน` checksum.** (Session 46, B6)
`_extract_transactions` reads `date time type amount balance` from one text line and takes direction from the running-BALANCE delta (the old table-cell-index alignment silently dropped/misclassified wrapped rows — Nov–Apr drifted ~30k). After any statement import, run `python scripts/verify_statement_parse.py <pdf>` — parsed deposit/withdrawal count+sum MUST equal the statement's `รวมฝาก/รวมถอน` line. The dedup constraint `uq_bse_txn` includes `balance` so genuine identical same-day transfers aren't collapsed.

**19. Expense classification is SLIP-MEMO-driven, not amount-guessed (Session 46).**
The K+ slip memo (arrives via LINE → `slips` table) is the source of truth for what a bank transfer was FOR. `musician_fee` is assigned ONLY when a slip memo says "ค่าดนตรี" — the old "amount 600/700/2100/2800 → musician_fee" heuristic was REMOVED (inflated ภ.ง.ด.3 WHT, mis-tagged owner transfers). The nightly job `nightly_slip_reconcile` (02:00 BKK) = `slip_routes.reconcile_slips_to_statements()` re-matches slips and pushes their memo category onto the `bank_statement_entries` row the P&L reads; manual `POST /slip/reconcile`. No slip → `other_expense`. `pos_cashflow_entries.category_code` is FK-constrained to `expense_categories` (bank rows are not). Memory [[project-slip-classification]].

**20. Accountant EXPORTS + analytics must read `v_daybook_pnl`, NOT raw `v_daybook` — Session-47 audit found 6 surfaces still on the raw view.** (Session 47, 2026-05-30)
Even after `v_daybook_pnl` went live (pitfall #13 note), `export_routes.py` (daybook / category-summary / pnd3 / `/export/summary`), `menu_routes.py` `/revenue/breakdown` + `/scorecard` KPI#5/#6/#8, and `tax_routes.py` WHT were STILL `FROM public.v_daybook` with no exclusion. Effect: the accountant's daybook export showed a LOSS in profitable months (owner drawings + savings + ATM booked as expense, vendor payments double-counted), expense ~3.3x overstated, monthly vs annual disagreed ~850k. Gate before shipping ANY money number: `rg "FROM public\.v_daybook\b" *.py` (word-boundary excludes `_pnl`) must return nothing in a P&L/export/analytics path. Fixed all 6 → `v_daybook_pnl`.

**21. A bank EXPENSE row must NEVER carry `source_type='bank_statement'` — it's on the exclusion list, so the expense silently leaves profit.** (Session 47, 2026-05-30)
Seeded `statement_rules` set `source_type='bank_statement'` for categorised expense rules (utility/rent/salary/food) and `_classify` copies it onto the row → ~1.53M of real beer/salary/food expense was excluded and the dashboard showed an IMPOSSIBLE ~66% margin. Expense classification must map to a COUNTED source (`payroll_expense` / `rent_expense` / `utility_expense` / `vendor_purchase` / `bank_fee` / `tax_expense` / `other_expense`). `bank_statement` source is for UNREVIEWED INCOME only. Sanity check: a single-branch mala shop nets ~ -6%..+35%/month (avg ~15%); ~66% margin = expenses leaking out. See `migrations/2026_05_30_audit_cashbasis_expense_reclass.sql` and memory [[reference-pnl-views-and-bankstatement-source]]. Musician WHT confirmed มาตรา 40(8) เงินได้อื่น (all 3 pnd3 generators); payer = ร้านสถานีหม่าล่า.

**22. COGS / food-cost% must sum by category SUBTREE (`parent_code`), never a hard-coded code list.** (Session 47b, 2026-05-30)
A single new or renamed sub-code silently drops from food-cost% otherwise — `food_raw` and the duplicate `beverage_raw` (625k of beer) were both missing from the old hard-coded list, undercounting COGS to ~15%. Pattern: `category_code IN (SELECT code FROM public.expense_categories WHERE code='food_cost' OR parent_code='food_cost')`. COGS is now split into two subtrees — `food_cost` (ต้นทุนอาหาร: raw_meat/raw_veggies/raw_seasoning/raw_oil_gas/food_raw/packaging) and `beverage_cost` (ต้นทุนเครื่องดื่ม: raw_beverage/beverage) — because cash-basis beer buying is lumpy (FOOD ~13-19% stable vs BEVERAGE 7-33% volatile). `/scorecard` + `/dashboard/overview` return both; `/pos/food-cost` is recipe-based (unaffected). `migrations/2026_05_30_food_beverage_cost_split.sql`.

**23. OCR / upload endpoints must offload heavy sync work with `asyncio.to_thread` — never run it on the event loop.** (Session 47c, 2026-05-30)
`do_ocr`, `invoice_upload`, `slip_upload` (slip_routes), `slip_match` (bill_payment_routes) were `async def` but called the sync pipeline (PDF render via pdfium + GPT-4o Vision + Supabase save + Tesseract) DIRECTLY. One multi-page upload (a 1.1MB 6-page Makro PDF) froze the WHOLE server for the duration → `/health/deep` + `/cron/health` time out → UptimeRobot DOWN + Discord bot silent. Identical to the earlier POS-import incident (`/pos/detect-only`). Fix: `await asyncio.to_thread(<sync_fn>, ...)` (added `import asyncio` to all three files; `invoice_upload`'s body extracted into a sync `_process_upload`). Rule: any FastAPI `async def` that does CPU work (image/PDF/OCR) or calls the SYNC OpenAI/Anthropic client must wrap it in `asyncio.to_thread` (or be a plain `def` so FastAPI threadpools it). Grep before shipping an upload route: an `async def` containing `pdfium`/`pytesseract`/`client.chat.completions`/`.create(` with no `to_thread` is a server-freeze bug.

**24. Whitelist AI-extracted ENUM fields against their DB CHECK constraint before INSERT.** (Session 47c, 2026-05-30)
GPT returned a `payment_type` outside `chk_vb_payment_type` (a Thai credit term on a SINGHA invoice) → INSERT 23514 → the WHOLE bill upload failed to save ("db save failed"). The OCR prompt asking for an enum is NOT a guarantee. `main.py` now maps `parsed["payment_type"]` to NULL or credit_card/transfer/cash/cheque/other (synonyms mapped, else NULL) before both the create INSERT and the merge backfill. Verified `payment_type` is the only AI-fed column on `vendor_bills` with a CHECK (status/review_status/payment_status are hardcoded/defaults). Rule: any column fed by AI output that has a CHECK/enum constraint must be normalized to the allowed set (the constraint allows NULL → safest fallback; raw stays in ocr_json).

**25. All AI calls go through `llm.py` — never instantiate an OpenAI client or hand-roll an Anthropic urllib request in a route/module.** (Session 48, 2026-05-31)
`llm.py` owns three primitives: `get_openai()` (singleton OpenAI client), `call_anthropic(task, user, system=, max_tokens=, timeout=)` (raw-HTTP Messages API — one `anthropic-version` header, one place for the key), and `MODELS` (task→model dict). Before this, OpenAI clients were built in 5 places and Anthropic was urllib'd in 6, with the Haiku string split between `claude-haiku-4-5` and `claude-haiku-4-5-20251001` (now unified → pinned `claude-haiku-4-5-20251001`). Rule: a new AI feature does `from llm import get_openai` / `from llm import call_anthropic` + adds a `MODELS` key — it does NOT write `OpenAI(api_key=...)`, a call-site `from openai import OpenAI`, or a fresh `urllib.request` to `api.anthropic.com`. `call_anthropic` raises `LLMError` (`.status`, `.status_for_http()`) — convert at the call site (`raise HTTPException(e.status_for_http(), ...)` in routes; swallow to None in BackgroundTasks). Left raw on purpose (env-driven, low-traffic — route through `llm` only if you touch them): `tools/ocr_menu_to_json.py` (standalone CLI) + `bill_payment_routes.py._call_gpt_vision_for_slip` (raw-HTTP vision).

**26. Supabase `public` schema ships with RLS DISABLED — the anon key is a full read breach until you enable it.** (Session 49, 2026-05-31)
57/59 public tables had RLS off. The project's anon key is public-by-design (shipped in the frontend JS bundle), so anyone could `GET https://<proj>.supabase.co/rest/v1/pos_bills` (and every other financial table) directly via PostgREST — bypassing all FastAPI auth. The backend connects with the **service_role / postgres role (BYPASSRLS)**, so enabling RLS with NO policy denies anon+authenticated while leaving the backend 100% working. Always run `get_advisors(type='security')` after any schema change. Fix pattern (idempotent, reversible) = `migrations/2026_05_31_enable_rls_all_public_tables.sql`. Verify the breach is closed with the anon key: `curl .../rest/v1/<table>?limit=1 -H "apikey: <anon>"` must return `[]`. NEVER flip a `security_definer` view (e.g. `v_daybook_pnl`) to `security_invoker` while RLS is on — P&L reads would return 0 rows.

**27. Supabase storage buckets default to over-permissive policies — financial-doc buckets must not be world-listable / anon-uploadable.** (Session 49, 2026-05-31)
The `uploads` bucket (OCR'd bank statements / slips / invoices) was `public=true` with `SELECT`+`INSERT` policies for role `public`/`anon` → anyone could enumerate every doc and upload arbitrary files. Backend uses service_role (BYPASSRLS) so it doesn't need those policies. Dropping them (`migrations/2026_05_31_lock_uploads_bucket_policies.sql`) kills enumeration+anon-upload while public-URL downloads still work (so the dashboard's `get_public_url` `<img>` rendering is unaffected). NOTE `_upload_to_storage` (main.py) + `_upload_slip_to_storage` (slip_routes.py) discard the returned `storage_path` and store the public URL — so a true private+signed-URL migration needs path persistence + read-time signing + a backfill (don't make the bucket private without that or every dashboard image 404s).

**28. A scheduled job that swallows its own exception makes `@_heartbeat` report false-healthy.** (Session 49, 2026-05-31)
`cron_heartbeat.heartbeat` records `ok=False` ONLY when the wrapped fn RAISES. Four LINE digest crons (`_scheduled_daily_digest`, `_scheduled_ap_due_reminder`, `_scheduled_weekly_summary`, `_scheduled_daily_stock_digest`) did `except Exception: log.error(...)` with no re-raise → they recorded `ok=True` on every run even when the LINE push failed, so `/cron/health` + Uptime Robot never caught a silently-dead digest. Rule: a `@_heartbeat`-decorated job's top-level `except` MUST end with `raise` (mirrors `_scheduled_do_snapshot_rotation`). Also: every `add_job` target should be `@_heartbeat`-wrapped — `vps_health_monitor` was the one job missing it (the watchdog itself was unmonitored).

**29. PUBLIC_PATHS endpoints gated only by a header/signature must FAIL CLOSED, and use `compare_digest`.** (Session 49, 2026-05-31)
`/line/webhook` checked `if x_line_signature and not _verify_signature(...)` — omitting the `X-Line-Signature` header skipped verification entirely (anon could burn paid Claude/OpenAI + spam LINE). Fail closed: `if not _verify_signature(body, x_line_signature or ""): raise 403`, and `_verify_signature` returns False when the secret or header is missing + compares with `hmac.compare_digest`. Same constant-time rule for the 5 `secret != ALERTS_WEBHOOK_SECRET` cron sites (→ `secrets.compare_digest`). And: a PUBLIC_PATHS route that returns financial data or can push to LINE but is fired by the IN-PROCESS scheduler (not external HTTP) doesn't need to be public at all — remove it from PUBLIC_PATHS so JWT gates it (`/ap/due-reminder`, `/stock/alert`).

**30. POS `pos_sales_items` has no UNIQUE(bill_id,line_no) — re-import double-counts line items.** (Session 49, 2026-05-31)
The `file_hash` guard on `pos_imports` only blocks byte-identical re-uploads; a re-exported FoodStory file (new hash, same bills) re-resolves to the existing `pos_bills` row and inserts a SECOND copy of every line (4,311 dup pairs / 96k baht surplus found in prod). Affects menu analytics only (P&L reads `pos_sales_daily`, upserted). Fix = delete-by-bill before insert, in the same transaction (`DELETE FROM pos_sales_items WHERE bill_id = ANY(%s)` then executemany). Do NOT add `UNIQUE(bill_id,line_no)` — `line_no` content diverges across re-exports so it would fail to build and ON CONFLICT would be semantically wrong.

**31. `async def` upload handlers must offload heavy sync work — bank-statement upload was the last one missed (AGENTS #23 sibling).** (Session 49, 2026-05-31)
`phase12_bank_statement_routes.upload_statement` parsed the PDF with pdfplumber + ran the INSERT loop directly on the event loop → a multi-page statement froze uvicorn → `/health/deep` timeout → Uptime Robot DOWN + in-process Discord bot dies. Fixed: extracted `_process_statement_upload(pdf_bytes, branch_code)` (sync) called via `await asyncio.to_thread(...)`, mirroring `/invoice/upload`, `/slip/upload`, `/slip-match`. Grep before shipping any upload route: an `async def` containing `pdfplumber`/`pdfium`/`pytesseract`/`pd.read_excel`/`cur.executemany` with no `to_thread` is a server-freeze bug.

**32. Validate AI JSON output SHAPE (is-it-a-list), not just that it parses.** (Session 49, 2026-05-31)
`recipe_routes.ai_link_ingredients` did `json.loads(...)` (catching only JSONDecodeError) then `for s in suggestions: s.get(...)`. Claude (Haiku) often wraps the array in `{"suggestions":[...]}` or returns a single object — `json.loads` SUCCEEDS, `suggestions` is a dict, the loop iterates str keys, and `s.get(...)` raises AttributeError → 500. After `json.loads`, unwrap a dict (`.get("suggestions")/("ingredients")/("data")`) and assert `isinstance(list)`; skip non-dict elements in the loop. (Extends AGENTS #25 — the OCR/AI-JSON validation rule.)

**33. Money/analytics endpoints that build dates from query params must bound them or they 500 on bad input.** (Session 49, 2026-05-31)
`/pos/calendar` (`year` unbounded), `/pos/goals` + `/pos/compare` (month parsed but `date()`/`calendar.monthrange()` OUTSIDE the try) returned an uncaught 500 on `?year=99999` / `month=2026-13` / `0000-05` (ValueError). Fix: bound the `Query(..., ge=2000, le=2100)`, or move `date()`/`monthrange()` inside the try and validate `1<=m<=12` before constructing — return 422/400, not 500.

**34. `pos_imports.status='error'` was INVALID — `chk_pos_import_status` only allows pending/parsing/success/failed.** (Session 49b, 2026-05-31)
The error-marking UPDATE in BOTH `pos_import.py` paths wrote `status='error'`, which the CHECK constraint rejects (23514) → the UPDATE always failed (compounding the aborted-transaction bug #12: even after rollback, the wrong value still 500s, caught by `except: pass`, row stuck at 'parsing'). Use `'failed'`. Rule: any AI/code-fed value going into a column with a CHECK/enum must match the allowed set — verify with `SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid='public.<t>'::regclass AND contype='c'` (same class as AGENTS #24 payment_type). Verified the only valid statuses are pending/parsing/success/failed.

**35. Private storage bucket → sign stored public URLs at READ time (the `uploads` bucket is now private).** (Session 49b, 2026-05-31)
GAP 2 follow-through: `uploads` (OCR'd statements/slips/invoices) is now `public=false`, so the stored `.../object/public/uploads/<path>` URLs return 400. `main._sign_uploads_url(url)` extracts the path and returns a fresh `create_signed_url` (24h) — the signature authorizes the GET, which is why this works for `<img src>` (image requests can't carry a JWT header). It's applied at every read path that returns a stored URL: invoice list+detail (`phase2_routes`), slip list+detail+upload previews (`slip_routes`), invoice upload preview (`main`). Rules: (a) STORE the canonical public-URL form in the DB (don't store signed URLs — they expire); sign only at read time. (b) Any NEW endpoint that returns an `uploads` URL to the client MUST wrap it in `_sign_uploads_url` or the image 400s. (c) `_sign_uploads_url` is forward-safe (signed URLs work on public buckets too) and falls back to the input on error. (d) `slip_routes` re-exposes it via a lazy-import wrapper because `main._sign_uploads_url` is defined AFTER the router includes (a module-top `from main import` would fail at load). recipes.image_url is all-NULL and not in `uploads`, so the public menu is unaffected.

**36. Loan (เงินยืม) = financing, NOT P&L — exclude both legs; lender lives in `notes`; `match_status='manual'` protects it.** (Session 50, 2026-05-31)
A loan is a balance-sheet/financing event: money borrowed in is a liability (not income), repayment out settles it (not an expense). Both `source_type` values — `loan_in` / `loan_repayment` — must be excluded from EVERY P&L aggregate. They were added to `v_daybook_pnl` AND the inline `source NOT IN` lists in `pnl_routes.py` (5), `cashflow_routes.py` (3), `line_bot_routes.py` (weekly digest, 2), `phase2_routes.py` (2), and the `_EXCLUDED_SOURCES_SQL` macro in `phase10_narrative_routes.py`. (`export_routes.py` + `yearly_routes.py` read `v_daybook_pnl`, so the view fix covers them.) Per-lender ledger = `v_loan_balance` (migration `2026_05_31_v_loan_balance.sql`), which reads `bank_statement_entries` DIRECTLY and groups by the `notes` column — because `v_daybook` hard-codes `counterparty=NULL` for every bank row. Tagging is manual via `POST /classify/{entry_id}` (now takes a `lender` → written to `notes` via `COALESCE`, and already sets `match_status='manual'`). Read via `GET /loans` + `GET /loans/{lender}` (JWT-gated). Two traps: (a) the nightly `slip_routes.reconcile_slips_to_statements` only updates `WHERE match_status <> 'manual'`, so a hand-tagged loan row is NOT clobbered back to `other_expense` — manual tagging is the protection; (b) `bank_statement_entries.direction` and `amount` are GENERATED columns (from debit/credit) — never INSERT a value into them (`cannot insert a non-DEFAULT value`). **Phase 2 SHIPPED (repayment auto-tag):** `statement_rules` keywords `คืนยืม`/`คืนเงินยืม` (direction='expense', priority 100) → category `loan_repayment`; `slip_routes._CAT_TO_SOURCE['loan_repayment']='loan_repayment'`; the reconcile Pass-2 push now also writes the normalized lender to `notes` (`_normalize_lender` = strip Thai title + first token, so "น.ส. นุศรา ปรางม++" → "นุศรา" matches the manual borrow's lender). Borrows (incoming) stay manual — the slip pipeline is expense-only (`_classify_slip_category` hard-codes `direction='expense'`). Operator how-to: `docs/HOWTO_loans.md`. Spec/plan: `docs/superpowers/{specs,plans}/2026-05-31-loan-autotag-phase2*`. **Re-tagging AUTO rows:** the `/bank-statement` review list is `needs_review`-only, so an auto-classified row (e.g. a name rule tags incoming นุศรา → `other_income`/`auto`) never shows there. Added `GET /bank-statement/search?q=<text>` (description ILIKE, all `match_status`, returns current category/source/notes) — the frontend's "ค้นหา/แก้รายการ" section uses it to find + re-tag any row. (Spec/plan `2026-05-31-bank-statement-search-retag*`.)

**37. ALL AI calls now carry telemetry — OpenAI goes through `llm.openai_chat`, Anthropic through `call_anthropic`; both log to `ai_call_log`.** (Session 51, 2026-06-01) The audit's Monitoring gap is closed: `llm.py` writes one best-effort row to `public.ai_call_log` per call (provider/task/model/ok/tokens/latency/status/error) via `_log_ai_call`, which swallows ALL its own errors so telemetry can NEVER break an AI call or a user request (same contract as `cron_heartbeat.record_heartbeat`). This REVERSES the original "no cost-tracking" note in `llm.py` (TUM approved). Rules for any NEW AI feature: OpenAI → `from llm import openai_chat; openai_chat("<task>", model=..., messages=..., **kwargs)` (NOT `get_openai().chat.completions.create` directly — that bypasses logging; the only sanctioned direct call is `product_classifier`'s injected-test path). Anthropic → `call_anthropic` (already logged). Add a `MODELS` key for the task. Read it back via `GET /ai/stats` / `GET /ai/calls` (JWT-gated, in `ai_monitor_routes.py` — do NOT add to PUBLIC_PATHS; the log holds prompt/usage data). Cost in `/ai/stats` is an ESTIMATE from `llm.PRICES` (override env `AI_PRICES_JSON`) × `USD_THB`. `ai_call_log` has RLS on + no policy (pitfall #26). Migration `migrations/2026_06_01_ai_call_log.sql`. F10 (bill_payment raw-urllib slip vision) folded into `openai_chat` at the same time.

**38. OCR accuracy is measurable now — `tests/ocr_golden/` — but the real number needs real images run LOCALLY (none in the repo).** (Session 51, 2026-06-01) The audit's Testing gap: `tests/ocr_golden/scorer.py` scores an (expected, actual) OCR pair field-by-field (text exact-after-normalize; money ±0.01; line items precision/recall/F1; `overall`=mean of scalar-accuracy and item-F1). `tests/test_ocr_golden.py` runs OFFLINE in `verify.ps1`/CI with NO API key against **synthetic** fixtures in `cases/*.json` (fictional vendors/amounts) — it proves the SCORER is correct, NOT production accuracy. The real accuracy number comes from `python -m tests.ocr_golden.scorer --live <image> <expected.json>` on human-confirmed invoices kept OUTSIDE the repo (see `tests/ocr_golden/README.md`). NEVER commit real financial documents to build a golden set — keep them in a private folder and point `--live` at them. Re-run `--live` before/after any OCR prompt or model change to see whether accuracy moved (this is also how to evaluate a possible OpenAI→Anthropic OCR switch).

**39. AI-generated money prose is number-verified, not auto-corrected (audit F7).** (Session 52, 2026-06-01) `/pnl/narrative` runs `_verify_narrative()` after Claude returns: it extracts every ฿ figure from the prose and flags any that matches no known-true value from `_gather_month_data` (±1%, or ±1 for small counts; years 1900-2600 and `N%` percentages are skipped). It is **advisory — it does NOT rewrite the AI text** (silently "fixing" a number on a money report is riskier than flagging it). On a mismatch it `logger.warning`s and adds a `verification:{ok,checked,unmatched}` block to the POST response. Rule: any future AI feature that emits money/number prose to a human should verify against the source values the same way (detect + log + surface), never trust the model's arithmetic, and never auto-edit the figure. The prompt also tells the model to use only the given numbers. Pure helper → unit-tested in `tests/test_narrative_verify.py` (no API key). Companion: `tools/gen_golden_from_confirmed.py` builds a REAL OCR golden set from `review_status='confirmed'` bills but REFUSES an `--out` inside the repo (real financial data must never be committed — extends #38).

**40. OCR returns per-field confidence + image-quality; low values become review warnings (audit F6).** (Session 53, 2026-06-01) `VISION_PROMPT` asks for two extra top-level keys — `field_confidence` (field→0-1) and `image_quality` (`{level,reason}`) — that describe the model's READING confidence and MUST NOT change the extracted values. `_confidence_warnings(parsed)` (pure, in `main.py`) turns a field <0.6 into a `LOW_CONFIDENCE` warning and `image_quality.level=="poor"` into `LOW_IMAGE_QUALITY`, appended to the EXISTING `_validate_invoice` warnings array already shown on the invoice review screen (no new UI). It tolerates a model that omits/mangles these keys (non-dict, string, out-of-range → no warning, never raises) — so the feature degrades silently if the model ignores the new instruction. `field_confidence`/`image_quality` persist in `ocr_json`. Rule: confidence is advisory (flag for the human), never gate/auto-reject on it. Unit-tested in `tests/test_ocr_confidence.py` (no API key).

**41. AI order-advice is backtested, not trusted blind (audit F8).** (Session 53, 2026-06-01) `GET /inventory/ai-order-advice/backtest?train_weeks=&test_weeks=` trains the day-of-week sales pattern on the older weeks and scores it against the held-out newer weeks: **MAPE** + `best_day_hit` (did train's top-2 DOW land in test's top-2?) + a Thai verdict. Pure scorer `backtest_dow(train_daily, test_daily)` (in `inventory_forecast_routes.py`) takes `[{date,dow,sales}]` lists, skips zero-actual days (no div-by-zero), and returns a defined report for empty/degenerate input. It's read-only/advisory — it tells the owner how trustworthy the DOW advice is before acting on it; it does NOT change `/inventory/ai-order-advice`. Added to the smoke route list. Unit-tested in `tests/test_order_backtest.py`. Rule: any AI that recommends a quantity/action should ship a backtest like this so its accuracy is a number, not a vibe.

**42. The AI drift watcher (`drift_monitor.py`) is QUALITY/COST drift, NOT outage detection — and is silent-by-default.** (Session 54, 2026-06-01) Daily 08:30 BKK job (`_scheduled_ai_drift_check` in `line_bot_routes.py`, `@_heartbeat("daily_ai_drift_check")`, re-raises on DB/logic error per #28) reads `ai_call_log`, evaluates per-task drift, and — only when **armed** (`AI_DRIFT_ALERTS_ARMED=1`, default off) and past the **28-day cold-start** — pings the Ops Discord on ONE signal: a persistent-error-rate regression. Everything else (latency/token/cost/model-change/volume) is digest-only (`GET /ai/drift` + one Monday `weekly_summary` line); cost is shown but does NOT page in v1. Critical false-positive guards baked in (do NOT weaken without re-reading the spec): persistent vs transient error split (only `status IN 400/401/403/404/422` counts; 429/5xx/529/NULL are excluded so a provider outage can't page); a two-proportion lower-bound test + ≥10pp lift gate (n=4 can't fire); min-sample gates (sparse tasks silent by construction); WARN→CRIT only after persisting 2 runs; the outage-dedup guard (≥50% transient → suppress, owned by /health/deep); model-change suppression (covers intentional model swaps). Detection is the PURE `evaluate_drift(recent_rows, baseline_rows, oldest_age, prev_state, now)` (unit-tested in `tests/test_drift_monitor.py`, no DB/key); the IO wrapper `run_drift_check(dry_run, post)` does the SQL + reads/writes the dedup table and is best-effort on Discord/state-write (only the read re-raises). **Dedup state lives in `public.ai_drift_state`** (migration `2026_06_02_ai_drift_state.sql`, RLS-on no-policy) — NEVER an in-process dict (forgotten on Coolify redeploy) and NEVER `job_heartbeat.last_error_message` (clobbered every run + surfaced by /cron/health). Clean day = zero Discord output (silence = healthy). Rollout: ships dry-run; flip `AI_DRIFT_ALERTS_ARMED=1` in Coolify only after a clean shadow week verified via `GET /ai/drift`.

**43. Structured-output OCR exists as an EXPERIMENT — strict JSON Schema via `ocr_schema.py` + `llm.openai_chat_structured`; NOT wired to production.** (Session 55, 2026-06-01) `ocr_schema.invoice_json_schema()` is the strict JSON Schema for the OCR parsed dict (scalars + F6 `field_confidence`/`image_quality` + `items[]`); `payment_type` is a real enum incl. null (matches `chk_vb_payment_type`). `llm.openai_chat_structured(task, messages=, schema=, ...)` sends `response_format={"type":"json_schema",...,"strict":True}` so OpenAI structurally guarantees the shape (kills the omit/wrong-type/invalid-enum class behind #24/#32/#34 at the source). `ocr_schema.normalize_structured(parsed)` maps a structured result back to the consumer dict shape (the seam a future promotion plugs into) and tolerates garbage without raising. The 3rd runner `run_openai_structured_ocr` in `tests/ocr_golden/compare.py` means `compare --dir` now scores free-form vs **structured** vs Claude (task `vision_ocr_compare_structured`). **Production `_run_gpt_vision` is UNCHANGED** (still free-form json_object) — strict mode can reject a borderline response + behaves per-model, so promote only after `compare` shows structured ≥ free-form on real invoices. Promotion = swap `_run_gpt_vision` to `openai_chat_structured(..., schema=invoice_json_schema())` + route through `normalize_structured`. STRICT-MODE RULE if you edit the schema: every object needs `additionalProperties:false` AND `required` == all property keys (optional = nullable type like `["number","null"]`), else the API 400s. Unit-tested in `tests/test_ocr_schema.py` (no API key).

**44. Selling Price Calculator — pure `pricing.py` + per-channel prices/config; the new public table needed RLS.** (Session 56, 2026-06-01) Forward "target% → suggested price" calculator closing RestoSheet gap #15. Pure module `pricing.py` (DB-free, unit-tested `tests/test_pricing.py`, no key): `round_price` rounds UP for charm pricing (`9`/`0`/`5`/`none`); `compute_channel` → price = `channel_cost/(target/100)` [mode=cost] or `channel_cost/(1−target/100)` [mode=gp], where `channel_cost = food_cost + packaging`, and net GP after platform commission = `price×(1−comm) − channel_cost`, with `low_margin` when net GP% < `LOW_MARGIN_PCT`(=40); `compute_reverse` does price→cost%/net-GP%. 4 routes in `recipe_routes.py`: `GET /recipes/{id}/pricing?target_pct=&mode=cost|gp&rounding=9|0|5|none`, `PUT /recipes/{id}/prices` (`{dine_in,takeaway,delivery}`→cols), `GET|PUT /recipes/pricing/channels` (config). Reuses the yield-aware `_calc_cost`. Migration `2026_06_02_selling_price_channels.sql`: adds `recipes.price_takeaway`/`price_delivery` (kept `selling_price` = dine_in so existing `gp_pct` is untouched) + `pricing_channels` config table (seed dine_in/takeaway/delivery; delivery commission 32.1). ROUTE-ORDER note: `/recipes/pricing/channels` is a 2-segment static path so it never collides with `/{recipe_id}` (1-seg) — declaration order is irrelevant. **PITFALL I HIT:** I created `pricing_channels` WITHOUT RLS — every other public table is RLS-on/no-policy (Session-49 hardening, pitfall #26), so it was the lone anon-exposed table; fixed by `2026_06_02_pricing_channels_enable_rls.sql` (`ENABLE ROW LEVEL SECURITY`; backend service_role bypasses, frontend touches it only via backend endpoints). RULE: any `CREATE TABLE public.*` migration MUST be followed by `ENABLE ROW LEVEL SECURITY`. **TECH DEBT:** delivery `commission_pct=32.1` now duplicates the hardcoded `pos_import._LINEMAN_GP_RATE=0.321` — a later task should make `pos_import` read the config row so the two can't drift. Spec/plan: `docs/superpowers/{specs,plans}/2026-06-01-selling-price-calculator*`.

**45. Table RLS does NOT cover `public` VIEWS — a SECURITY DEFINER view stays anon-readable and leaks underlying data past the Session-49 baseline.** (2026-06-01, executive-audit CEO-SEC-01) Session 49 enabled RLS on all 63 public TABLES, but the 27 `security_definer` views still had SELECT granted to `anon`+`authenticated`. A security_definer view runs as its owner (postgres = BYPASSRLS), so the public anon key (in the app.marastation.com bundle) could `GET /rest/v1/v_dashboard_overview` (May sales/profit/margin), `v_daybook_pnl`, `v_shop_savings`, `v_ar_ap_*`, `v_daily_sales`, ... — the whole P&L, bypassing table RLS + all FastAPI auth. Same breach class as Session-49 GAP 1, via views. **Fixed live** (`migrations/2026_06_01_revoke_anon_select_secdef_views.sql`, applied via MCP): `REVOKE ALL ON <each security-definer public view> FROM anon, authenticated`. Backend reads them as service_role/owner (BYPASSRLS) → unaffected; VEXONHQ browser client is SSO-only (zero `.from()`), marastation-web uses Prisma `web`-schema → neither app breaks. Verified: anon REST now `42501 permission denied`; owner still reads (v_daybook_pnl 1651 rows); /health/deep healthy; /menu/public 200. Do NOT flip the views to `security_invoker` while RLS is on (P&L empties — pitfall #26). **ROOT CAUSE / RECURRENCE:** `pg_default_acl` shows `postgres`+`supabase_admin` still default-GRANT all privileges to anon+authenticated on FUTURE public objects, so EVERY new public reporting view re-leaks until you either create it `WITH (security_invoker=on)` (then table RLS applies) or REVOKE anon+authenticated like above. RULE (companion to #44's "new public table → ENABLE RLS"): **any new `CREATE VIEW public.*` that is security-definer MUST be followed by `REVOKE ALL ON public.<view> FROM anon, authenticated;`** — and run `get_advisors(type='security')` + an anon-key REST probe before calling a schema change done.

**46. JWT middleware AUTHENTICATES but does not AUTHORIZE — every money-mutation endpoint must add `Depends(_require_admin_role)` itself.** (2026-06-01, executive-audit AUD-TAX-02) `JWTAuthMiddleware` (main.py) only verifies the token is valid + stashes `request.state.username`; it NEVER checks role. So before this fix, ANY logged-in user — incl. the `staff`-role Supabase accounts (may/toon/oil; `verify_token` defaults non-admins to `_role='staff'`) — could hit the financial-mutation endpoints and alter the books (reclassify bank rows, create/delete manual P&L entries, set permanent auto-rules, edit/match/reject slips, record/reverse AR-AP payments). Fix: gate them with the existing helper as a dependency — `def endpoint(..., _admin: dict = Depends(_require_admin_role))` (`from auth_routes import _require_admin_role`; raises 401 no-token / 403 non-admin). 26 endpoints gated across phase12 (classify, add-rule), phase3_quick_entry (quick-entries POST/DELETE), rules (statement-rules + vendor-aliases POST/DELETE), slip_routes (reconcile, patch, delete, rematch-all, match, manual-match, reject, category), bill_payment (payment PATCH, slip-match), phase3_arap (counterparties + ar-ap entries + payments). Deliberately LEFT open for staff (data-entry / read / notify, not book-altering): ingestion uploads (`/upload`, `/slip/upload`, `/pos/import`, invoice upload), GET reads, and the LINE-notify triggers (`/bills/payment/line-alert`, `/ap/due-reminder`). RULE: any NEW endpoint that writes a financial record/classification/rule MUST add `Depends(_require_admin_role)`. Regression-tested offline + deterministically in `tests/test_admin_gate.py` (TestClient + monkeypatched verify_token: staff→403, no-token→401, admin→passes the gate on the real wired routes) — run it after adding/touching any money-mutation route, and bump its EXPECTED_ROUTE_COUNT. TUM's own account is `app_metadata.role='admin'` so the dashboard is unaffected; the `staff` accounts now get 403 on these (intended segregation of duties — make a helper an admin in Supabase if they need write access).

**47. Executive-audit batch 2 — four small durable rules (2026-06-01).** (a) **AUD-TAX-01:** ALL FIVE WHT surfaces must read `tax_routes.WHT_RULES` per-category — `/export/summary` (the pre-download preview, `export_routes.py`) was the lone holdout using flat 3% + the removed amount-heuristic + phantom `freelance`/`pnd3` categories, so it under-reported rent's 5% and disagreed with the actual pnd3 export. Fixed to build the WHT `CASE` from `WHT_RULES` (local import). Any new WHT/ภ.ง.ด.3 number reads `WHT_RULES`, never a hard-coded rate. (b) **CEO-REL-01:** `/cron/health` was blind to a job that NEVER wrote a heartbeat row (dead-on-arrival = invisible). `cron_heartbeat.heartbeat()` now registers every decorated `job_id` in module-level `_REGISTERED_JOBS` at import time; `/cron/health` returns `missing_jobs` (decorated but no row) and `status:"degraded"` — but stays **HTTP 200** for missing-only (so a freshly-deployed job awaiting its first run doesn't false-alarm Uptime Robot); stale jobs still 503. (c) **AUD-TAX-03:** `bank_statement_entries` gained `classified_by`/`classified_at`; `classify_entry` writes `_admin["sub"]` + `now()` so every manual P&L reclassification has a who/when trail (the `_admin` payload from #46's `Depends(_require_admin_role)` is the actor source — no `request: Request` needed). (d) **AUD-PNL-01:** DROPPED `v_dashboard_overview` — it computed expense from `vendor_bills(confirmed)` only (ignoring payroll/cash/bank) → false 73-87% margin; 0 code refs, 0 dependents. Migration `2026_06_01_audit_batch2_dashboard_classify_cashflow.sql`. Reaffirms pitfall #13/#20: a P&L view must derive from `v_daybook_pnl`, never from `vendor_bills` alone (not a cash-basis expense, #16).

**48. Bank-statement upload now self-checks against the รวมฝาก/รวมถอน checksum (audit AUD-DATA-01).** (2026-06-01) `/upload` (`_process_statement_upload`) used to parse → insert → return `success` with NO comparison to the statement's own printed totals — so a parser drift (the Nov-Apr/May ~10-31k silent class, AGENTS #18) slipped in unnoticed. Now `_statement_checksum(pdf_bytes, raw_rows)` reads the PDF's `รวมฝาก/รวมถอน` summary (`_read_pdf_summary_totals`, a copy of `scripts/verify_statement_parse.pdf_checksum` — **keep the two in sync**) and compares parsed deposit/withdrawal count+sum. The response gains `checksum_ok` (true/false/null) + a `checksum` detail object, and on a mismatch the `message` carries a loud `*** เตือน:` drift line (import still happens — don't lose data — but the operator is told to verify; frontend can red-banner on `checksum_ok===false`). It's best-effort: a missing summary line or a read error → `available:false` (can't verify), never blocks/raises. Pure-logic unit-tested in `tests/test_statement_checksum.py` (match / sum-drift / count-drift / no-summary / read-raises; no PDF/DB/key). Rule: any new statement/ledger import that has a printed total MUST verify against it and surface a mismatch, never return a bare success.

**49. Slip upload flags edited-amount fraud — same bank `ref_no`, different amount = tamper (audit F11).** (2026-06-01) A bank transfer's `ref_no` is globally unique per transaction, so `_find_duplicate_slip` already returns the existing slip when a `ref_no` re-appears (accidental double-upload). But it deduped SILENTLY even when the new upload's amount differed from the stored one — i.e. someone editing the amount in a slip screenshot and re-uploading would just be told "duplicate", with no flag. Now the duplicate branch in `slip_upload` calls the pure `_slip_tamper_signal(existing_amount, existing_ref, new_ref, new_amount)`: when the ref matches but the amounts differ by >0.01, it returns `{existing_amount, uploaded_amount, ref_no}`, the response carries `tamper_warning:true` + `tamper:{...}` and a loud Thai `*** เตือน:` message, and the server logs `slip TAMPER signal`. The original stored slip is KEPT either way (we never overwrite with the edited one). Best-effort + advisory — it warns the human, never blocks the upload or auto-rejects (same philosophy as #39/#40/#48). Pure helper → unit-tested in `tests/test_slip_tamper.py` (no DB/key: same-ref-diff-amount fires; same/within-1-satang/different-ref/missing-ref/missing-amount stay silent). Rule: a fraud/integrity check on financial input should detect + surface (flag for the human), never silently swallow the anomaly. Stronger image-tamper (ELA/EXIF) is out of scope — `ref_no` reuse is the high-signal, zero-false-positive check the OCR already gives us for free.

**50. Production OCR accuracy is measurable with NO images/API key — diff `ocr_json` (raw) against the confirmed columns (audit F1, the real number).** (2026-06-01) #38's golden-set `--live` path needs the original images (kept out of the repo); but every `review_status='confirmed'` bill ALSO carries the model's raw read in `ocr_json` AND the human-corrected final columns, so the correction delta IS the accuracy signal — free, on 100% of production, queryable in one SQL. Method: `SELECT ocr_json->>'vendor_name' vs vendor_name`, `ocr_json->>'amount' vs amount` (numeric, ±0.01), `ocr_json->>'bill_date' vs bill_date`, `ocr_json->>'invoice_no' vs invoice_no` over confirmed bills; count per-field match / extracted / present. **Baseline (92 confirmed bills, 2026-06-01): bill_date 100% (87/87), invoice_no 100% (69/69), amount 83.7% (5 outright-wrong, 10 not-extracted), vendor_name 82.6%.** Takeaway for the operator: trust OCR dates/invoice numbers; ALWAYS eyeball the AMOUNT on convenience-store receipts (7-Eleven/CP — the 5 amount errors clustered there, incl. a 685→6,850 missing-zero). Caveat (don't over-claim): this measures OCR-vs-human-correction, so a bill confirmed without noticing an OCR error counts as "correct" — it's a tight LOWER bound on error, not a ceiling. Re-run this query before/after any OCR prompt/model change as the cheapest regression signal; reserve `--live` (#38) for fields not stored verbatim (line items).

**51. Full-system audit (2026-06-02, B+) + A+ remediation round 1 (2026-06-03, B+ → A-) — backend items shipped, plus what's deferred and why.** (2026-06-03)

**AUDIT (2026-06-02):** Independent full-system audit of VEXONHQ (frontend) + vexonhq-ocr-api (this backend) by 6 parallel auditors (OCR, P&L/accounting, AI, frontend/UX, security, reliability/ops) + live read-only prod tests + Supabase advisors. 52 findings: 2 Critical, 9 High, 19 Medium, 18 Low, 4 Info. **Overall grade B+.** Key conclusions: no active data leak found; all prior Session-47/49/50 fixes still hold; P&L/accounting core trustworthy (no hallucinated SQL columns, exports reconcile). Main themes = the 2 Criticals + UI page sprawl (73 routes; recommend ~18 core + admin) + AI monitoring statistically hollow (anomaly had only 1 usable baseline; drift monitor inert at this volume). **Self-correction during the round — audit OPS-2 was WRONG:** the `backup.py` `:5432`→`:6543` pooler rewrite is INTENTIONAL/correct (avoids "max clients reached"; COPY works on 6543) → downgraded to Low. Report file: `VEXONHQ_System_Audit_2026-06-02.html` (in the Antigravity/IDE working dir).

**RULE RELAXATION (A+ plan only):** TUM approved a one-off relaxation scoped strictly to the A+ remediation plan — Claude Code implemented + verified + pushed these items WITHOUT a per-push Confirm. At that date the firm rule was "Antigravity writes ALL code / Claude does NOT write application code" (since superseded 2026-06-08 — roles swapped, see Division of labor above); the dated exception does not generalize.

**A+ REMEDIATION ROUND 1 (2026-06-03, B+ → A-):** 9 items shipped to prod + verified (smoke 70/70, deploy settled each push, independent adversarial re-review = 0 regressions, live-tested). BACKEND items:
- **SEC-1 / AI-1 (Critical) — `/ai/exec` hardened** (commit `6e98a57`): `secrets.compare_digest` (constant-time key check) + removed `shell=True` (whitelisted cmds run as argv lists; docker-restart resolved in Python, no shell pipe) + timeout message 10s → 30s. Kept on `PUBLIC_PATHS` so the `ai.marastation.com` chat keeps working. Verified live: bad key → HTTP 401.
- **OPS-1 / DR (Critical) — backup + DR tooling** (commit `d6d7832`): full pre-fix backup taken + verified (83 tables / 56,798 rows + 276 storage files / 268.7 MB; CSV row counts == manifest). DR tooling committed incl. off-host → Google Drive wrapper (`mara_backup_to_gdrive.ps1`, db daily / full weekly). PENDING on TUM: add a Windows Task Scheduler entry to run it automatically.
- **AI-2 (High) — anomaly detection** (commit `bd36076`): replaced mean/stddev z-score with robust median(p50) + percentile-spread; `MIN_SAMPLE_FOR_BASELINE` 3 → 8. Advisory-only (no financial-number impact).
- **OPS-3 (High) — cron resilience** (commit `bd36076`): `BackgroundScheduler` `job_defaults` `misfire_grace_time=3600` + `coalesce=True` (a missed run fires on recovery instead of being dropped). Verified: 9 cron jobs healthy.
- **OCR-3 (High) — Vision API cost cap** (commit `bd36076`): 25 MB upload cap + 40-page PDF cap (bounds GPT-4o credit-burn).
- **AI-8 (Info) — P&L narrative warning** (commit `bd36076`): append an in-message warning to the LINE digest when a baht figure fails verification (was log-only; companion to #39's advisory `_verify_narrative`).
- **PNL-1 (Medium) — budget view repointed** (Supabase migration via `apply_migration`): `v_budget_status` `FROM v_daybook` → `FROM v_daybook_pnl` so budget "actual spend" matches the cash-basis P&L/exports (extends #13/#20/#47). Verified: view returns valid rows. Reversible.

(Frontend items shipped same round, for cross-ref: FE-5 `/pos/compare` stale `https://api.vexonhq.com` fallback → `'' (?? '')`; FE-1 `/budgets` EmptyState dead link `/settings` → `/categories`. Frontend commit `1f727da`.)

**Rollback tags (one-command revert):** `backup-pre-dr-tooling-2026-06-03`, `backup-pre-aiexec-2026-06-03`, `backup-pre-backendbatch2-2026-06-03` (this repo); `backup-pre-fe-2026-06-03` (VEXONHQ). Plus data backup `backups/mara-backup-20260602_171525`. Remediation report file: `VEXONHQ_Remediation_Report_2026-06-03.html`.

**DEFERRED to reach full A+** (all specced in `HANDOFF_AUDIT_FIXES.md`; deliberately NOT done unattended — each needs TUM's decision or risks breaking a core flow that smoke can't verify). Backend-relevant: **SEC-1b** remove `/ai/exec` from `PUBLIC_PATHS` + add JWT/IP (needs `ai.marastation.com` chat-auth coordination); **OCR-1** confirm-gating (touches the daily bill-confirm flow); **OCR-2** cross-vendor invoice merge guard (changes financial-record matching); **SEC-2** enable RLS on the `web.*` schema (could cut off marastation-web's DB role → break the customer site); **SEC-3** move auth token to an HttpOnly cookie (login risk); **OPS-12** pin `requirements.txt` versions (a wrong pin breaks the Coolify build); **OPS-4** add `postgresql-client`/`pg_dump` to the image; **DB drops** (4 leftover backup tables + 3 duplicate `vendor_bills` indexes). (Frontend-side deferred: FE-2 single route manifest; FE-3 page declutter ~30 routes; FE-4 role-gate `/ai-review` + `/ai-stats`; FE-6 migrate `/budget` + `/pos/compare` to `safeFetch`.)

**52. A+ remediation ROUND 2 (2026-06-03, A- → ~A) — 10 backend/DB items shipped + verified live, plus an audit correction.** (2026-06-03) Continues round 1 (#51) the same day. **Backup taken FIRST** (mandatory before any change to main): Supabase logical backup `mara-backup-20260603_005144` (83 tables / 56,842 rows / 276 storage files), verified.

SHIPPED + DEPLOYED + VERIFIED LIVE:
- **SEC-4 — Discord anti-replay** (`discord_routes.py`, commit `56b345b`): after the Ed25519 signature verify, added an interaction-timestamp freshness check — reject if `|now − ts| > 300s`. Stops a captured-but-valid interaction body from being replayed. Companion test fix below.
- **OPS-6 — `/health/deep` disk visibility** (`main.py`, commit `56b345b`): now reports `disk_pct` + `disk_warn` (true when ≥80%). Verified live `disk_pct=29.2`. (Extends the existing `cpu_pct` clean-rebuild check in the 6-step workflow.)
- **OPS-8 — DO snapshot floor** (`do_snapshot_routes.py`, commit `56b345b`): `DO_SNAPSHOT_MAX_KEEP` default 1 → 2 so a FAILED create never leaves zero snapshots; doc'd the required `image:create` scope (pitfall #11) and the note **"DO snapshot backs up the APP SERVER, not the Supabase DB"** (the DB backup is the Supabase logical dump — don't confuse the two during a restore).
- **AI-5 — forecast no-guess** (`inventory_forecast_routes.py`, commit `56b345b`): when `order_count < 2` → `insufficient_data=true`, urgency `"unknown"`, `next_order_est`/`days_until_order` = `None` (no fabricated date from a single data point); None-safe sort. Advisory-only, no financial-number impact. (Same "AI returns a number, not a vibe / don't guess" philosophy as #41.)
- **PNL-2 — budget per-category spend = cash basis** (`phase2_routes.py`, commit `1d1328e`): `/budgets/status` per-category `spent` now derives from `v_daybook_pnl` (was `vendor_bills`-only). It now matches the dashboard `top_categories`/`food_cost` — includes cash/manual + bank-statement expenses and excludes equity. Extends #13/#20/#47/#51-PNL-1 (`vendor_bill` is NOT a cash-basis P&L expense, #16).
- **OPS-9 — snapshot rotation verifies create BEFORE delete** (`do_snapshot_routes.py`, commit `1d1328e`): `rotate_auto_snapshots` now confirms the DO create action was accepted (`status in in-progress/completed`) before deleting ANY old snapshot — a failed create now SKIPS deletion, so the backup count can never drop. Pairs with OPS-8's floor of 2.
- **dup-index (DB migration `g1_drop_duplicate_vendor_bills_indexes`):** dropped `idx_vb_due_date` / `idx_vb_status` / `idx_vb_vendor` — exact duplicates of the canonical `idx_vendor_bills_*`. Verified gone. (Closes the #51 deferred "3 duplicate vendor_bills indexes".)
- **table-drops (DB migration `g2_drop_3_dead_backup_tables`):** dropped `sales_backup`, `bank_statement_entries_bak_20260530`, `pos_sales_items_dedup_bak_20260531`. **KEPT `sales_import_raw`** — the audit misclassified it as a backup, but it is a LIVE source: `sales_import_raw → v_sales_unified → v_sales_clean → v_sales_forecast_base → v_sales_next7`. RULE: confirm a `*_backup`/`*_bak`/`*_raw` table has zero view/FK dependents (trace the view chain) before dropping — name alone is not proof it's dead.
- **SEC-2 — RLS on `web.*` schema** (DB migration `g2_sec2_enable_rls_web_schema`): enabled RLS on all 20 `web.*` tables (defense-in-depth, completes the #51 deferred SEC-2). **Verified marastation.com still SSRs identically** (homepage 212017B unchanged, `/menu` + `/events` unchanged) — which PROVES marastation-web connects via a BYPASSRLS role, so enabling RLS with no policy doesn't cut off the customer site. Reversible (`DISABLE ROW LEVEL SECURITY`). (Same "service_role/BYPASSRLS bypasses RLS so the backend keeps working" mechanism as #26.)
- **test fix** (`tests/test_discord_interactions.py`): `_sign_body` now defaults to the CURRENT timestamp (SEC-4 made the old static `"0"` ts stale → would always fail the freshness window). pytest: **165 passed, 23 skipped.**

**AUDIT FINDINGS CORRECTED (do NOT re-flag):**
- **OPS-2 downgraded High → Low (reaffirms #51):** `backup.py` rewriting the host port `5432` → `6543` is INTENTIONAL — it routes through the Supabase pooler to avoid "max clients reached". KEEP the rewrite.
- **`sales_import_raw` is NOT a dead backup table** — it is the live head of the sales-forecast view chain (see table-drops above). Not dropped.

COMMITS: backend `56b345b` (batch 1 — SEC-4 / OPS-6 / OPS-8 / AI-5), `1d1328e` (PNL-2 / OPS-9); DB migrations applied via Supabase MCP. **Rollback tags (2026-06-03):** `backup-pre-g1batch1`, `backup-pre-g1batch2` (plus the round-1 tags `backup-pre-aiexec` / `backup-pre-backendbatch2` / `backup-pre-dr-tooling`). All changes gated: `compileall` + pytest 165 + live smoke (pre-push hook); every change verified live.

**STILL OPEN → tracked in `docs/TOMORROW.md` (path to FULL A+).** BLOCKED on TUM input: **SEC-1b** (lock down the `/ai/exec` residual Critical — needs the `ai.marastation.com` chat-app auth mechanism: JWT? fixed IP?); **OPS-12** (pin deps — needs the container's `pip freeze`, no SSH/exec key available to Claude); **PNL-3** (WHT gross-vs-net — tax ambiguity, both unsure, needs a real invoice/accountant — DO NOT guess, same rule as PNL #47/§). INVOLVED (Claude can do on TUM's go): OPS-11 (cron stale-job alert), AI-6 (cashflow AI decision log), OPS-13 (DB connection pool — riskiest, do carefully), PNL-4 (tax-id prefill). GROUP 3 (larger): OCR-1/2 (+unit tests), FE-3 (page declutter), FE-2 (route manifest), SEC-3 (HttpOnly cookie), OPS-4, OPS-10; FE-6 (`safeFetch` on `/budget` + `/pos/compare`) deferred. **Supabase ticket SU-387973** (storage quota — 12.7GB billed vs 268MB physical = orphaned objects at the raw storage node, invisible to the Storage API; grace extension to 04 Jun 2026; local + remote watchdogs armed for a 402).

**53. A+ remediation ROUND 3 (2026-06-03, ~A → A/A+) — remaining High/Med engineering items shipped; only owner-decision items left.** (2026-06-03) Continues rounds 1+2 (#51/#52) the same day. Approach: analyzed all remaining items in parallel, implemented the safe ones, and adversarially verified the money-path changes over **2 rounds** — which caught + fixed a real multi-page-split regression BEFORE it shipped (see OCR-2). Full suite **204 passed** (39 new offline tests). Rollback tags (2026-06-03): `backup-pre-ocr12`, `backup-pre-batchB`, `backup-pre-fe6`, `backup-pre-ops10`.

SHIPPED + DEPLOYED + VERIFIED:
- **OCR-1 (HIGH — re-graded from Low, money correctness)** (`main.py`, commit `722d9fe`): `POST /invoice/{id}/confirm` now REFUSES a bill carrying an error-severity validation warning (e.g. `MISSING_TOTAL`) with **HTTP 422 `CONFIRM_BLOCKED`** unless `ConfirmRequest.force=True`. Fails OPEN on an infra error (never blocks a confirm because the gate itself broke); skips the gate + the warnings-rewrite entirely when forcing. (Stops a known-bad bill silently entering the books — same advisory→gate-only-on-error philosophy as #39/#40/#48/#49.)
- **OCR-2 (HIGH — re-graded from Low, money correctness)** (`main.py`, commit `722d9fe`): the `invoice_no`-only dedup fallback could FUSE two DIFFERENT vendors that share an invoice number ("1"/"001"). New `_should_merge_on_invoice_no()`: same vendor OR a tight non-zero amount match → merge; a WEAK number with neither → split; a STRONG number → trust the match UNLESS both amounts are present and differ. **Adversarial-review nuance (the regression caught before shipping):** a missing/drifted vendor OR date is the MULTI-PAGE OCR case (one invoice, page 2 has no header) — it is NEVER a split signal, so the guard must not split on vendor/date mismatch alone, only on a positive different-vendor/different-amount signal.
- **OPS-11 (active cron stale-job watchdog)** (`cron_heartbeat.py` + `line_bot_routes.py`, commit `0faf40b`): extracted `_compute_job_states()` (behavior-preserving — the `/cron/health` response shape is UNCHANGED) + new `check_and_alert_stale_jobs()` posts the SPECIFIC stale/missing `job_id` to Discord, rate-limited 6h/job; registered as a 30-min APScheduler job wrapped in `@_heartbeat`. Verified live: `/cron/health` shape preserved. (Closes the #52 "OPS-11 cron stale-job alert" item — turns the passive `/cron/health` of #47b into an active push.)
- **OPS-4 (HIGH — pg_dump-able backup)** (`scripts/backup.py`, commit `0faf40b`): `pg_dump` cannot run against the `:6543` TRANSACTION pooler, so the dump + stats now use a DIRECT/session URL (`:5432`), while the COPY fallback KEEPS the `:6543` rewrite (deliberate — that rewrite is correct, OPS-2 / pitfall #51/#52). Enables a real `pg_restore`-able dump once `pg_dump` is on the host PATH. (Note: this is the SCRIPT side of the #51-deferred OPS-4; the image still needs `postgresql-client` for `pg_dump` to exist on PATH.)
- **OPS-10 (money-path unit tests + testability extraction)** (`main.py` + tests, commit `94bf46a`): 18 offline money-path unit tests (`test_invoice_validation`, `test_merge_totals`, `test_slip_reconcile`) + a behavior-preserving `_compute_backfill()` extraction (the never-overwrite-a-present-total merge rule) so merge backfill is unit-testable WITHOUT a DB. `verify.ps1` gained a **[1b] offline-test step**. (Companion to #38/#50 — the money path now has a number, not a vibe.)

(Frontend item shipped same round, for cross-ref: **FE-6** — `/pos/compare` swallowed backend 500/401 and showed the empty "ไม่มีข้อมูล" state (looked like no data, not an outage) → migrated to `safeFetch` (throws on non-2xx) + an explicit error state + a rose error banner. Web commit `c6a3680`. Closes the #51/#52-deferred FE-6.)

**KEY FINDING / audit re-grade (do NOT downgrade back):** the analysis re-graded **OCR-1 + OCR-2 as HIGH (money correctness), not Low** — a bad-bill confirm and a cross-vendor merge both corrupt the books. Also: several audit "easy fixes" were INFEASIBLE as written — **PNL-4** has no clean counterparty join key, and **OPS-13** may just be an env-var port (do NOT do the 42-file pool refactor for a Low finding). RULE: re-assess an audit finding's severity + feasibility against the live code before acting — a label is a hint, not a fact (same spirit as the #51/#52 OPS-2 correction).

COMMITS: backend `722d9fe` (OCR-1 / OCR-2), `0faf40b` (OPS-11 / OPS-4), `94bf46a` (OPS-10); web `c6a3680` (FE-6).

**STILL OPEN → tracked in `docs/TOMORROW.md` (all now need an OWNER DECISION or INFO — the engineering items are done).**
- **DECIDE:** **AI-6** (cashflow AI log — extend `ai_categorization_log` vs a separate table + whether to gate low-confidence to review); **OPS-13** (confirm prod `DATABASE_URL` port `:5432` vs `:6543` — LIKELY env-only; do NOT do the 42-file pool refactor for a Low finding); **PNL-4** (clean counterparty join infeasible — choose best-effort exact-name prefill vs schema+UI vs keep-blank+note); **SEC-3** (HttpOnly cookie — cross-repo: needs a same-origin proxy in BOTH VEXONHQ + marastation-web before flipping the shared SSO cookie; ship CSP first); **FE-3** (declutter POS 25 → ~18 pages — approve a KEEP/CUT map first, deletes are irreversible); **FE-2** (route-manifest single source — do AFTER FE-3).
- **INFO NEEDED:** **SEC-1b** (how does `ai.marastation.com` call `/ai/exec` — JWT? fixed IP? — the residual Critical can't be locked down without it); **OPS-12** (paste the container's `pip freeze` to pin deps, or skip); **PNL-3** (WHT gross vs net — needs an invoice/accountant — DO NOT guess, same rule as #47/#52).
- **Supabase ticket SU-387973** (orphaned storage objects; grace to **04 Jun 2026**); watchdogs armed.

**54. A+ remediation ROUND 4 (2026-06-03 evening, A/A+) — ops fixes after rounds 1-3 same day; a REAL production DB outage root-caused + fixed.** (2026-06-03) Continues rounds 1+2+3 (#51/#52/#53) the same day, driven partly by LIVE Discord `auto_diagnose` alerts. Headline: the previous night's `postgres_failed` alert was traced to a real outage and fixed. Full suite **211 passed / 23 skipped** (48 new offline tests this round). Rollback tags (2026-06-03): `backup-pre-ai6`, `backup-pre-sec1b`, `backup-pre-pnl4`, `backup-pre-ops11fix`, `backup-pre-ops13retry`.

SHIPPED + DEPLOYED + VERIFIED LIVE:
- **OPS-13 (HIGH — the REAL one; #53 had mis-graded it "likely env-only / resolved")** (`main.py`, commit `238313a` + a Coolify env change by TUM): root cause of the prev-night 23:24 Discord `postgres_failed` "max clients reached in session mode" alert (= backend lost DB connectivity) was the app connecting via the Supabase **SESSION-mode** pooler (`:5432`, 15-client cap) which SATURATED. Fix in three parts: (a) verified the code uses NO session-only features (no named/server-side cursors, no `SET`, no `LISTEN`/`NOTIFY`, no prepared statements) so it is **transaction-mode-safe**; (b) added **connect-retry** to `main.get_db_conn` — retry 3× with backoff, ONLY on the saturation/too-many-clients error class; (c) TUM switched the Coolify `DATABASE_URL` to `pooler.supabase.com:6543` (**TRANSACTION mode**). **Verified on :6543:** reads (`/menu/public` 30KB), WRITES (cron heartbeat `run_count` 259→260, 0 new errors), app healthy. **NOTE (do not confuse):** the ~590ms postgres latency is the app↔pooler **TLS handshake** (fresh connection per request — no in-process pool), SEPARATE from pooler mode; cutting it needs an in-process connection pool (deferred — not worth it for the current ~660-bill/mo volume).
- **SEC-1b — optional IP allow-list on `/ai/exec`** (commit `ae5992e`): the residual Critical from #51/#52/#53. `/ai/exec` (already hardened round 1: `X-AI-Exec-Key` constant-time + strict whitelist + rate limit) now also runs `_check_ip_allowed()` — returns **403** unless the caller IP is in `AI_EXEC_ALLOWED_IPS`. **Unset = no restriction (back-compatible)**, so nothing breaks until TUM opts in. The caller is the separate `marastation-ai` app; full lockdown = TUM sets the env var in Coolify.
- **PNL-4 — ภ.ง.ด.3 (PND.3) WHT payee tax-id prefill, EXACT-match only** (`export_routes.py` + `yearly_routes.py`, commit `72343a3`): WHT rows carry only a free-text payee NAME (no FK), so a clean JOIN is infeasible (the #53 finding). New behavior prefills the payee tax-id by **EXACT normalized-name match** against `counterparties`. **Exact-match ONLY — never fuzzy** (a WRONG tax-id on a government filing is worse than a blank); unmatched rows stay blank and the strengthened red manual-check note remains. (Same "don't guess on a tax number" rule as PNL #47/#52/#53.)
- **AI-6 — cashflow AI categorization decisions are audit-logged** (commit `556a119`, migration `2026_06_03_ai6_cashflow_categorization_log`): extended `ai_categorization_log` (its `bill_id` was already nullable) with `cashflow_entry_id` + `source` columns; instrumented `_categorize_cashflow_one` to log BOTH the rule tier AND the LLM tier **in the same transaction** (atomic with the categorization), with a lowered-confidence fallback reason when the LLM returns an invalid code. (Closes the #53 "AI-6 cashflow AI decision log" decide-item; same telemetry contract as #37.)
- **OPS-11 self-alert fix** (`cron_heartbeat.py`/watchdog, commit `7db1562`): the stale-job watchdog shipped in #53 was alerting that **IT ITSELF had "never run"** — it writes its own heartbeat only AFTER finishing, so on its first run it looked missing. Fix: skip `_SELF_JOB_ID` in the missing-job alert (stale-detection still applies to it). (A watchdog must not page on its own first-run heartbeat gap.)
- **duplicate-operationId cleanup** (commit `238313a`): set `include_in_schema=False` on the 4 GET+HEAD ops endpoints (`/health`, `/health/deep`, `/cron/health`, `/menu/public`) — FastAPI emitted the SAME `operationId` for the GET and the HEAD method. **DEFENSE NOTE (do NOT apply Haiku auto-diagnose patches blind):** the `auto_diagnose` "Patch suggestion (Claude Haiku)" was **WRONG** — it told us to DELETE `menu_public_router` as a "duplicate", but it is `include_router`-ed exactly ONCE; applying it would have broken `/menu`. Verify every auto-diagnose patch against the live code before applying (same "a label/suggestion is a hint, not a fact" spirit as the #51/#52/#53 OPS-2 and OCR-1/2 corrections).

NEW TEST FILES this round: `tests/test_ai_exec` (IP allow-list), `tests/test_cron_stale_alert` (self-skip), `tests/test_db_conn_retry` (saturation-class retry). Full suite **211 passed / 23 skipped**.

**SELF-CORRECTIONS this session (surfaced openly, not hidden):** OPS-2 (High → Low — the `backup.py` `:5432`→`:6543` pooler rewrite is INTENTIONAL, reaffirms #51/#52/#53); `sales_import_raw` is NOT a backup (kept — live head of the sales-forecast view chain, reaffirms #52); **OPS-13** itself (resolved → active → fixed — #53's "likely env-only" was an under-grade; it was a real outage).

COMMITS: backend `238313a` (OPS-13 connect-retry + duplicate-operationId cleanup), `ae5992e` (SEC-1b), `72343a3` (PNL-4), `556a119` (AI-6), `7db1562` (OPS-11 self-alert fix); migration `2026_06_03_ai6_cashflow_categorization_log` applied via Supabase MCP; Coolify `DATABASE_URL` → `:6543` set by TUM.

**STILL OPEN → tracked in `docs/TOMORROW.md` (not blocking the A/A+ grade).**
- **TUM DATA / ACTION:** **OPS-12** (paste the container's `pip freeze` to pin deps); **PNL-3** (WHT gross vs net — needs an invoice/accountant, DO NOT guess — same rule as #47/#52/#53); set **`AI_EXEC_ALLOWED_IPS`** in Coolify to complete SEC-1b.
- **ANTIGRAVITY (HANDOFFs written in the VEXONHQ repo):** **FE-3** (POS declutter — KEEP/CUT map approved by TUM); **SEC-3** (HttpOnly cookie via a same-origin proxy, cross-repo); **FE-2** (route manifest, AFTER FE-3).
- **OPTIONAL LATER:** reduce the ~590ms DB latency via an in-process connection pool (deferred — not worth it for current volume; see OPS-13 NOTE above).
- **Supabase ticket SU-387973** (orphaned storage objects; grace to **04 Jun 2026**); watchdogs armed.

**55. PNL-3 (ภ.ง.ด.3 WHT — gross vs net) RESOLVED, verdict = GROSS, NO code change — a cross-agent adversarial-review catch.** (2026-06-03) Closes the PNL-3 item that had been BLOCKED-on-TUM/accountant across #52/#53/#54. NO commit, NO push, NO migration — this entry records a REVERT (net-zero code change) plus the data evidence that settled the ambiguity.

WHAT HAPPENED: Antigravity (headless `agy`) had changed the WHT formula to **GROSS-UP** the stored amount — `gross = net / (1 − rate)`, `WHT = net × rate / (1 − rate)` — across `export_routes.py`, `tax_routes.py`, and `yearly_routes.py`, on the ASSUMPTION that the stored amount was NET-of-withholding. It made that change WITHOUT checking the real data.

CLAUDE'S REVIEW (the catch): queried the LIVE `public.v_daybook_pnl`. `musician_fee` = 120 rows, **115/120 are round-to-100 face values** (฿600 ×88, ฿700 ×19, ฿2,100, ฿2,800); `rent` = ฿8,000. ALL round → the amounts are GROSS. If they were NET-of-3%/5%, the grossed-up values would be NON-round (e.g. 618.56, 721.65, 8,421) — it is impossible for 115/120 to be round by coincidence.

VERDICT: the stored amounts are **GROSS** → the ORIGINAL formula (`tax = amount × rate`) is correct. Claude **REVERTED all 3 files** to the original. PNL-3 is CLOSED — no code change shipped. `HANDOFF.md` already updated with this verdict.

WHY THIS MATTERS (the firm rule working): this is a clean example of the "Antigravity writes / Claude reviews adversarially" division of labor catching a real defect — a WRONG tax formula on a government filing (ภ.ง.ด.3) — BEFORE it shipped. RULE: a tax formula's gross/net assumption MUST be proven against the live data (the round-number test here), never assumed; an unverified gross-up on a filing is worse than the existing correct math.

MINOR DEFERRED FOLLOW-UP (low priority): `tax_routes.py` field `net_before_wht` is a PRE-EXISTING display-only estimate with confusing naming; it is NOT on the official ภ.ง.ด.3 export. Clean up the name later — not urgent, not money-affecting.

**56. Wongnai/FoodStory menu sync — recipes has NO FoodStory id, so match by code-prefix then Thai-name; re-sync needs a FRESH snapshot.** (2026-06-03) TUM handed over the venue's REAL in-store menu (the Wongnai mobile-order QR is a FoodStory POS — the SAME POS VEXONHQ already imports). Directive: treat the in-store prices as the source of truth and import all the food images. Claude recon'd the FoodStory API (`wn-mobile-order-api.foodstory.co`; image CDN `https://images-api.foodstory.co/{image_key}`; **143 items, 100% have images**; behind a PUBLIC `x-api-key`). Built re-runnable tooling, committed as `b7ae41e`: `scripts/wongnai_analyze.py` (READ-ONLY match → `wongnai_snapshot/REPORT.md` + `match.json`) and `scripts/wongnai_apply.py` (backup → update prices → download images → upload to the Supabase bucket `menu-images` (public) → set `image_url` → insert new items; idempotent + logged + verified).

**THE DURABLE PITFALL/RULE:** `public.recipes` has **NO FoodStory `menu_id`** — there is no FK to match on. The sync therefore matches by **code-prefix (e.g. `A001…`) first, then normalized Thai-name**. To RE-SYNC: **re-capture a FRESH `wongnai_snapshot` THEN run `scripts/wongnai_apply.py`** — the in-script 403 image-key refresh uses a **PLACEHOLDER endpoint and is NOT reliable**, so the main flow depends on a fresh snapshot, not on the script self-refreshing keys. The script's **hardcoded `x-api-key` is the PUBLIC Wongnai client key (NOT a secret)** — fine to keep in the repo.

PHASE 1 (analyze, read-only): **125/143 matched** (111 by code, 13 by name, 1 fuzzy `ยำใส้กรอก`↔`ยำใส่กรอก`), 16 price diffs, 18 Wongnai-only, 16 recipes-only.

PHASE 2 (APPLIED + Claude-verified): recipes **backed up FIRST** (`wongnai_snapshot/recipes_backup_20260603_093440.json`, 141 rows) → **16 `selling_price` corrected** to the in-store price (incl. closing 2 long-deferred cost-derived prices: wagyu 24.29→20, edamame 42.68→49; most others were +10 increases the system was behind on) → **141 menu images imported** to Supabase + `image_url` set (recipes had 0 images before; 0 failures) → **16 new real items inserted** (noise `…`฿1 and corkage-fee text excluded) → **157 recipes total**. End-to-end verified: the Supabase menu images return **200 image/webp through the marastation-web Next optimizer** (synergy with today's host-gated WebP work).

INTENTIONALLY LEFT UNTOUCHED — the 16 recipes-only rows are store promos/fees (DD01/DD03 pre-8pm beer, by-the-crate, 10-skewers-free-1, corkage/broken-glass fees), not menu items to sync.

**57. SEC-3 (HttpOnly cookie / same-origin proxy) COMPLETE on the FRONTEND repos — backend `auth_routes.py` was already correct, no ocr-api change needed; GP% costs entered for 25 Wongnai recipes.** (2026-06-03) SEC-3 Phase 2+3 shipped on the frontend repos (VEXONHQ + marastation-web) — same-origin proxy + HttpOnly cookie wired up on both. The ocr-api backend (`auth_routes.py` / Bearer token flow) was already correct and required NO change. Separately, GP% / cost data entered for 25 new Wongnai recipes via Supabase SQL (`recipe_ingredients` table). Both done without any backend code change. **Supabase downgraded back to Free tier** — support confirmed actual usage ~270 MB; no storage-quota blocker. All systems healthy: `api.marastation.com` 200, app 200, `marastation.com` 200.

**58. Multi-page invoice OCR runs the slow GPT-Vision stage CONCURRENTLY (cap 3) but persists/merges pages SEQUENTIALLY — fixes the Cloudflare 524 "upload failed" without splitting one bill into duplicates.** (2026-06-08) SYMPTOM: a 3-page Makro PDF returned **HTTP 524** (Cloudflare edge timeout, hard-capped at ~100s on free/pro — NOT raisable) so the UI showed "Upload failed (524)" even though the bill was actually OCR'd + saved to the review queue. Per-page GPT-4o vision is 7-40s; processed sequentially, 3 pages summed to ~115s > 100s. The false-fail is dangerous: the user re-uploads → risks duplicate bills. ROOT FIX (`main._process_upload`, no frontend change): split the per-page pipeline into two stages — **`_ocr_page`** (Tesseract + `_run_gpt_vision`) is side-effect-free + thread-safe, so multi-page PDFs run it in a `ThreadPoolExecutor(max_workers=min(3, n))` (`pool.map` preserves PAGE ORDER); **`_persist_invoice_page`** (Supabase storage upload + `_save_invoice` merge + revalidate) runs in a plain sequential loop. **WHY persist MUST stay serial:** the multi-page merge keys off the row the *previous* page just wrote (dedup_key / invoice_no lookup in `_save_invoice`), and the shared Supabase client's HTTP/2 connection is NOT thread-safe (same hazard as `_sign_uploads_url`) — concurrent/out-of-order saves would split one invoice into N duplicate bills, the exact bug being prevented. Bonus: OCR-all-then-persist is more atomic — a mid-PDF vision failure now raises BEFORE any page is saved (no half-written bill). Single-image path unchanged (thin `_process_single_image` = `_ocr_page` + `_persist_invoice_page`). Concurrency is safe because `get_db_conn()` returns a FRESH psycopg2 connection per call (telemetry in `openai_chat`) and `_run_tesseract` uses a unique `NamedTemporaryFile` per call. Guarded by `tests/test_ocr_parallel_pages.py` (order-preserved + persist-never-overlaps + cap≤3). **DEFERRED (logged, not done — lean-bar):** a true file-hash idempotency column to hard-block a re-upload of the identical file; today's dedup (`dedup_key` + per-page filename/content idempotency in `_save_invoice`) already catches most retries, and the <100s fix removes the trigger. If multi-page bills ever exceed ~4-5 pages routinely, move to a real async job queue (upload returns job_id, frontend polls) instead of widening the pool.

**59. Invoice upload idempotency keys on a FILE-LEVEL SHA-256 of the original bytes (pre-OCR), NOT on OCR-extracted content — the content comparison failed because GPT vision is non-deterministic.** (2026-06-09) The page-idempotency in `_save_invoice` compared the OCR'd item CONTENT of an incoming page against existing pages; GPT-4o vision returns slightly different items each run for the SAME image, so a re-upload (e.g. a Cloudflare-524 retry, see #58) was judged "a new page" and RE-INSERTED — the live test triplicated a Makro bill's line items (99 = 33×3) across 9 attachments. FIX: `_process_upload` computes `hashlib.sha256(contents).hexdigest()` on the ORIGINAL uploaded bytes ONCE, and `_find_uploaded_file()` looks it up in `public.attachments.file_sha256` BEFORE any OCR. On a hit it returns the existing bill with `already_uploaded:true` and **skips OCR entirely** (saves the GPT-4o cost too). Every attachment that an upload creates stores that file hash (`_persist_invoice_page`/`_save_invoice` thread `file_sha256` through). **WHY file-level + byte hash:** (a) deterministic — independent of OCR drift AND independent of pypdfium2 render determinism (we hash the upload, not the rendered page); (b) a DIFFERENT bill that happens to share a vendor/amount has different bytes → different hash → never false-skipped (the property the old content/amount comparison could not guarantee). **Schema (migration `2026_06_09_attachments_file_sha256.sql`, applied to prod):** `attachments.file_sha256 text NULL` + partial index `idx_attachments_file_sha256 WHERE file_sha256 IS NOT NULL`. **NON-UNIQUE on purpose:** a multi-page upload writes N attachment rows sharing ONE file hash, so a UNIQUE constraint would reject pages 2..N — dedup is enforced in code (the pre-OCR lookup), never by a DB unique. **No backfill:** old attachments stay NULL, so re-uploading a PRE-deploy bill is still not caught (acceptable; only affects bills created before this shipped). The lookup is fail-open (any DB error → treat as new, never block a real upload). Guarded by `tests/test_ocr_file_hash.py`. Rollback: drop the index + column (additive/nullable, safe), revert code. Builds on #58 (the 524 fix that removed the main retry trigger).

**60. Sentry is OPT-IN and MUST NOT break boot — init only when `SENTRY_DSN` is set, and swallow any init error.** (Session 72, 2026-06-09, Reliability Phase, commit `ff66d37`) Backend error tracking was REMOVED in Session 68 (v46.19.1, 2026-06-05) and RE-INTRODUCED here as a strictly opt-in, boot-safe block in `main.py` *before* `app = FastAPI()`. Contract (do NOT weaken): (a) read `SENTRY_DSN` (stripped); if empty, NEVER call `sentry_sdk.init()` — the app must boot + behave identically to no-Sentry (log `Sentry disabled (no SENTRY_DSN set)`); (b) the entire `import sentry_sdk` + `init()` lives in one `try/except Exception` that logs and continues, so a missing package or a malformed DSN can never fail startup (verified: a malformed DSN raises `BadDsn`, is caught, app continues); (c) the `[fastapi]` extra auto-instruments unhandled 500s with tracebacks — do NOT add manual middleware, a webhook, or a debug endpoint to trigger errors; (d) `release` = `APP_VERSION` (the single `"3.7.0"` constant that also feeds `FastAPI(version=...)` — bump both together), `environment` defaults to `production`, tracing off by default (`SENTRY_TRACES_SAMPLE_RATE=0.0`) to spare the 4GB VPS + free-tier quota; (e) NO Telegram — alerting consolidates on the existing Discord Ops channel (route Sentry→Discord via Sentry's native integration, not code). The Dockerfile installs from `requirements.txt` (NOT `requirements-lock.txt`), so the dep line lives there. To enable in prod: set `SENTRY_DSN` in Coolify → Redeploy → boot log must read `Sentry initialized (release=3.7.0, env=production)`; test a sample event from the Coolify Terminal (`python -c "import sentry_sdk,os; sentry_sdk.init(os.environ['SENTRY_DSN'],release='3.7.0'); sentry_sdk.capture_message('vexon sentry test')"`) — no code, no endpoint. Same best-effort-monitoring philosophy as `cron_heartbeat.record_heartbeat` / `llm._log_ai_call` (#37) / the drift watcher (#42): observability must never break the thing it observes. Rollback tag `backup-pre-sentry-2026-06-09`.

**61. `/dashboard/executive` sales card carries a DISPLAY-ONLY gross→net waterfall — the delivery commission is ALREADY netted into `sales_net`, so it MUST NOT be added to expense/profit (double-count).** (Session 75, 2026-06-21, commit `783f94c`) CONTEXT: FoodStory `pos_sales_daily.net_total` is GROSS (all channels); `v_daybook.pos_sale = GREATEST(0, net_total − rider_gross)` and rider `net_payout` is added back, so `_summarize_month().sales_net` (the `/dashboard` + executive headline) is CASH-BASIS = money actually received after Grab/Lineman commission (policy 2026-05-30, the "186,788.03 gross vs 170,022.38 net" question). To SHOW that story without changing any number: new `_SALES_WATERFALL_SQL` (3 scalar subqueries — `SUM(pos_sales_daily.net_total)`, `SUM(rider_deliveries.gross_sales)`, `SUM(rider_deliveries.net_payout)`; **same month+branch filter as `_summarize_month`**, params `(branch, month_start, pe)*3`) feeds the pure `_sales_waterfall(foodstory_gross, delivery_gross, delivery_net, net_received)` → `{foodstory_gross, delivery_commission=gross_sales−net_payout, other_adjustment, net_received}`, attached to the `sales_mtd` card as `waterfall` in `_build_executive_cards(..., waterfall=...)`. CONTRACT (do NOT weaken): (a) `net_received == summ["sales_net"]` — the bottom line MUST equal the `/dashboard` headline, never recompute it from gross−commission; (b) `other_adjustment = net_received − (foodstory_gross − delivery_commission)` absorbs months where they differ (pos_cashflow/manual/AR income not in `net_total`, or a `GREATEST(0)` floor day which makes the adjustment POSITIVE) so the waterfall always reconciles; (c) the commission is **display-only** — `expense_total`/`gross_profit`/AP/stock cards are untouched (adding it anywhere = double-count, the whole point of cash-basis netting); (d) `card.value` stays `sales_net` (so AI-insight + every consumer is unaffected); FE hides the adjustment row when `|adj| < 0.005` and falls back to a single number if `waterfall` is absent. **`dashboard_executive` now does TWO `fetchone()`s** (metrics row, then the waterfall row) — `tests/test_dashboard_pending_db.py` mock was updated to supply both (a 1-tuple 2nd row caused `IndexError`). Guarded by `tests/test_dashboard_executive.py` (real Apr/May/Jun reconcile + no-double-count) + FE `tests/sales-waterfall.spec.ts`. Rollback tag `backup-pre-sales-waterfall-2026-06-21`. See FE AGENTS #19 + memory `project_pnl_cash_basis`.

*Last updated: 2026-06-21 (#61 `/dashboard/executive` sales card gains a DISPLAY-ONLY gross→net waterfall — `_SALES_WATERFALL_SQL` + pure `_sales_waterfall()` attach `{foodstory_gross, delivery_commission, other_adjustment, net_received}` to the `sales_mtd` card; FoodStory net_total is GROSS, `sales_net` is cash-basis net-of-Grab/Lineman-commission, the commission is ALREADY netted in `pos_sale` so it must NEVER be added to expense/profit (double-count); `net_received==summ.sales_net` always, `other_adjustment` reconciles pos_cashflow/floor-day months; executive route now does TWO fetchone()s so test_dashboard_pending_db mock updated; tests real Apr/May/Jun reconcile; commit 783f94c; rollback backup-pre-sales-waterfall-2026-06-21). Prev 2026-06-09 (#60 opt-in Sentry error tracking re-introduced (removed Session 68, re-added boot-safe) — init in main.py ONLY when SENTRY_DSN set, the whole import+init wrapped in try/except so a missing pkg or malformed DSN can never fail startup; [fastapi] extra auto-captures unhandled 500s with tracebacks (no middleware/webhook/debug-endpoint); release=APP_VERSION single 3.7.0 constant, env default production, tracing off; NO Telegram (Discord-only); dep in requirements.txt which the Dockerfile installs; commit ff66d37 deployed but DISABLED until SENTRY_DSN set in Coolify; rollback backup-pre-sentry-2026-06-09). Prev 2026-06-09 (#59 invoice upload idempotency now keys on a FILE-LEVEL byte SHA-256 (`attachments.file_sha256`, migration 2026_06_09) checked PRE-OCR — replaces the fragile non-deterministic OCR-content comparison that triplicated a re-uploaded Makro bill's items; dup hit returns existing bill + already_uploaded:true + skips OCR/cost; column nullable+partial-index+NON-unique because multi-page shares one hash; no backfill; fail-open; tests/test_ocr_file_hash.py; builds on #58). Prev 2026-06-08 (#58 multi-page invoice OCR parallelized — GPT-Vision stage runs concurrently cap 3, persist/merge stays sequential in page order; fixes the Cloudflare 524 false "upload failed" on multi-page Makro PDFs without splitting one bill into duplicates; backend-only, single-image path unchanged; tests/test_ocr_parallel_pages.py guards order+serial-persist+cap; file-hash idempotency + async job queue deferred per lean-bar). Prev 2026-06-03 (#57 SEC-3 complete — frontend repos only, backend Bearer token unchanged; GP% costs entered for 25 Wongnai recipes via recipe_ingredients; Supabase downgraded to Free ~270 MB confirmed; all systems 200). Prev 2026-06-03 (#56 Wongnai/FoodStory menu sync — recipes has NO FoodStory menu_id so sync matches by code-prefix then normalized Thai-name; tooling committed b7ae41e: scripts/wongnai_analyze.py read-only + scripts/wongnai_apply.py apply; Phase 1 125/143 matched; Phase 2 APPLIED + verified — recipes backed up first (141 rows), 16 selling_price → in-store, 141 images imported to Supabase menu-images bucket + image_url set, 16 new items inserted → 157 total; images serve 200 image/webp through marastation-web Next optimizer; RE-SYNC = fresh wongnai_snapshot THEN wongnai_apply.py — the in-script 403 image-key refresh is a placeholder/unreliable; hardcoded x-api-key is the PUBLIC Wongnai client key, not a secret; 16 recipes-only promos/fees left untouched). Prev 2026-06-03 (#55 PNL-3 ภ.ง.ด.3 WHT gross-vs-net RESOLVED, verdict GROSS — Antigravity's net-assumption gross-up REVERTED, NO code change; settled by live-data round-number test on v_daybook_pnl (115/120 musician_fee rows round-to-100); cross-agent adversarial-review catch — wrong tax formula on a govt filing caught before shipping; minor deferred: rename tax_routes.py net_before_wht display field). Prev 2026-06-03 evening (#54 A+ remediation round 4 → A/A+: OPS-13 REAL DB-outage fix — transaction-mode-safe + connect-retry + Coolify DATABASE_URL→:6543; SEC-1b /ai/exec IP allow-list; PNL-4 ภ.ง.ด.3 exact-name tax-id prefill; AI-6 cashflow AI decision log; OPS-11 watchdog self-alert fix; duplicate-operationId cleanup + Haiku-auto-diagnose-was-wrong defense note; 211 tests passed). Prev 2026-06-03 (#53 A+ remediation round 3 → A/A+: OCR-1 confirm-gating 422 CONFIRM_BLOCKED, OCR-2 cross-vendor invoice-merge guard, OPS-11 active cron stale-job Discord watchdog, OPS-4 pg_dump on :5432, OPS-10 18 money-path tests + verify.ps1 [1b]; FE-6 /pos/compare safeFetch; re-graded OCR-1/2 to HIGH; 204 tests passed). Prev 2026-06-03 (#52 A+ remediation round 2 → ~A: SEC-4 Discord anti-replay, OPS-6 disk in /health/deep, OPS-8 snapshot floor, AI-5 forecast no-guess, PNL-2 budget cash-basis, OPS-9 rotate verify-before-delete, dup-index + 3 dead-table drops, SEC-2 web.* RLS; OPS-2 correction kept; sales_import_raw kept). Prev 2026-06-03 (#51 2026-06-02 audit B+ + A+ remediation round 1 → A-: /ai/exec SEC-1, OPS-1 DR backup, AI-2, OPS-3, OCR-3, AI-8, PNL-1 budget view). Prev 2026-06-01 (#50 OCR accuracy from production F1; #49 slip anti-tamper F11; #48 statement-import checksum AUD-DATA-01; #47 audit batch 2; #46 admin-gate; #45 secdef-views revoke).*
