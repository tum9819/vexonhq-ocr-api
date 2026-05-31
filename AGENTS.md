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

*Last updated: Session 49b, 2026-05-31 (pitfalls #34-35 — invalid pos_imports status value; private uploads bucket + read-time signed URLs. Earlier 49: #26-33).*
