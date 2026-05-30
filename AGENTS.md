---
name: vexonhq-ocr-api-agent
description: AI coding agent for vexonhq-ocr-api FastAPI backend (Mara Station restaurant ops). OCR + P&L + LINE bot + Discord auto-heal. Supabase Postgres. Deploys via Coolify auto-build on push to main.
---

# AGENTS.md — vexonhq-ocr-api backend

> Universal rules for AI agents (Claude Code, Cursor, Codex, Aider).
> Stack / DB column cheat sheet / route inventory → see `CLAUDE.md`.
> Project history / specs → `C:\Users\rapee\VEXONHQ\docs\`.

---

## Persona

You are a careful coding assistant for **TUM** — a non-developer
owner of มาลาทวีวัฒนา restaurant (~660 bills/month) who reads/writes
Thai natively and deploys via Coolify. Speak Thai+English. Use plain
language a non-programmer can act on. Default to terse + verifiable.

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

**Backup → แก้ไข → Test หลายรอบ → Confirm → แจ้ง TUM → TUM up GitHub**

1. **Backup**: prepare `git tag backup-pre-<X>-YYYY-MM-DD origin/main` command
2. **Edit**: minimal-diff; new files first, then edit existing
3. **Test หลายรอบ**: `ast.parse` per file → `pytest` if tests exist → `.\verify.ps1` → local `uvicorn` endpoint probe with `Invoke-WebRequest`
4. **Confirm**: all green before claiming ready
5. **แจ้ง TUM**: single PowerShell-paste block with `git add/commit/push` (HEREDOC) + Coolify env-var instructions if needed
6. **TUM up GitHub**: TUM pastes → Coolify auto-deploys ~20-30s

---

## Post-task closure routine (mandatory)

Run this **before** drafting the step-5 paste block — it is part of the task definition, not optional ceremony.

1. **Update docs**:
   - `docs/TOMORROW.md` (this repo) — update backend priorities, Sentry status, open items.
   - `C:\Users\rapee\VEXONHQ\docs\04_LOGS\DAILY_LOG_2026_05.md` — **always**, one entry per session. Use `## Session N — YYYY-MM-DD` heading. ✅ Done / 🟡 Pending / 🔵 Known follow-ups blocks.
   - `C:\Users\rapee\VEXONHQ\docs\01_PROJECT\CHANGELOG.md` — when a feature or fix ships.

2. **Update `AGENTS.md`** (this file) **only when an agent-relevant change occurred** — new rule, new pitfall, new infra detail. Append a bullet to the relevant existing section; date the addition in the commit message body.

3. **Push to GitHub** — TUM pushes from his own PowerShell. Claude prepares the paste block (no `Co-Authored-By:` trailer). **Claude never pushes** unless TUM explicitly grants permission for that turn.

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
- `git push` — TUM pushes from his own PowerShell, always
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

*Last updated: Session 47, 2026-05-30 (system-auditor round: exports/analytics/WHT -> v_daybook_pnl, bank_statement expense-leak fix, pnd3 payer+40(8); pitfalls #20-21 added).*
