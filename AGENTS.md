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

1. **Update docs** in `C:\Users\rapee\VEXONHQ\docs\` (backend มี `docs/` ของตัวเองที่ frontend repo):
   - `docs/04_LOGS/DAILY_LOG_2026_05.md` — **always**, one entry per session. Use `## Session N — YYYY-MM-DD` heading. ✅ Done / 🟡 Pending / 🔵 Known follow-ups blocks.
   - `docs/01_PROJECT/CHANGELOG.md` — when a feature or fix ships.
   - `docs/01_PROJECT/TOMORROW.md` — update open items and next-session priorities.

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

*Last updated: Session 41, 2026-05-25.*
