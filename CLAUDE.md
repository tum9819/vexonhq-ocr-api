# CLAUDE.md — vexonhq-ocr-api (backend)

> Context for Claude Code. Read this first every session.

---

## Who is the user
**TUM** (`tum9819@gmail.com`) — owner of มาลาทวีวัฒนา restaurant (mala skewer shop, ทวีวัฒนา district, ~660 bills/month, ~282k baht/month). Building VEXONHQ end-to-end as a non-developer; technical enough to deploy and run SQL. Copies and pastes code Claude produces — Claude must not push to git itself.

Language: respond in Thai mixed with English, technical when needed. Keep things plain enough that a non-dev can act on them.

## Working rules (must follow)

1. **Explore → Plan → Implement → Verify → Commit.** Always.
   - *Explore*: read related files, grep for context, check git log for recent changes
   - *Plan*: state the change, files affected, and risks BEFORE writing code
   - *Implement*: make the minimal change that does the thing
   - *Verify*: run `python3 -c "import ast; ast.parse(open(file).read())"`, run pytest if tests exist, hit the endpoint with curl
   - *Commit*: produce a small focused commit message and hand the diff to TUM to push
2. **Ask questions first** — when requirements are ambiguous (edge cases, UX, technical tradeoffs), ask before writing the spec. Don't guess.
3. **Fix root cause. Never suppress errors. Never skip tests.** When something fails, explain *why* before fixing.
4. **TUM pushes to GitHub.** Claude can stage files, edit, run tests, run pytest, run black/ruff, and commit locally — but **stop before `git push`** and hand the diff to TUM with the exact PowerShell commands.
5. **Backup tag + local CI before every handoff.** Before sending TUM a `git push` command, ALWAYS:
   (a) Tag `origin/main` as a rollback target: `git fetch origin && git tag backup-pre-<descriptor>-YYYY-MM-DD origin/main && git push origin <tag>` — gives TUM one-command revert (`git reset --hard <tag>`) if the push regresses.
   (b) Run the full local check suite: `python -c "import ast; ast.parse(open(file, encoding='utf-8').read())"` on every touched .py file, plus `pytest tests/` if tests exist, plus a live endpoint probe (`Invoke-WebRequest` against staging or local uvicorn) when the change touches a route.
   (c) State plainly in the handoff what was verified — e.g. "Backup tagged as `backup-pre-X-YYYY-MM-DD`, ast.parse + endpoint probe passed, ready to push." Never silently skip a step.
6. **Verify column names against `information_schema.columns` before writing SQL.** AI tends to hallucinate column names. Every wrong SQL column has cost hours of debugging in past sessions.
7. **Don't reflexively `git checkout HEAD -- .`** based on a single bash read showing truncation — PowerShell `git status` is source of truth. (This was a Cowork-on-Windows-mount quirk; less relevant when running Claude Code on Windows directly, but still: trust PowerShell.)
8. **No emojis in code or commits.** Markdown docs and chat replies — only if user uses them first.
9. **DigitalOcean snapshot rotation — keep at most 3 snapshots on `vexonhq-core`.** DO charges $0.06/GB/month for snapshot storage, and at ~30 GB per session a snapshot per session compounds fast (Session 23 inherited 20 snapshots = ~345 GB = $20.73/month in storage alone, on top of $24 for the droplet). Standing slots:
   1. `vexonhq-clean-base` — pristine OS image, never delete (recreate only after major OS upgrade).
   2. `vexonhq-<previous-stable>` — last known-good state before the current session.
   3. `vexonhq-session<N>-complete-YYYY-MM-DD` — latest stable state.

   At the end of every successful session, the session that just finished becomes the new "previous-stable" — promote it (rename if needed) and delete the snapshot it displaces. Use the exact "complete" suffix only on the final, all-fixes-in snapshot; intermediate snapshots taken mid-session must be deleted before session close so they don't pollute the rotation. Cost ceiling: ~$5/month for snapshots. If the snapshot bill is trending over $10/month, the rotation is being skipped — audit before taking any new snapshot.

---

## Project overview

VEXONHQ is an AI Accounting + Restaurant Ops platform for a single-branch Thai restaurant. The backend is a **FastAPI** app deployed on a self-hosted Coolify instance on a DigitalOcean Droplet. It serves:

- POS data import (FoodStory Excel, 8 report types) + analytics (~25 endpoints across menu_routes.py)
- OCR for invoices/bills/receipts via GPT-4o Vision → `vendor_bills`
- AR/AP tracking, bill payment + slip-match OCR
- Bank statement (KBank PDF) parser → `bank_statement_entries`
- Rider income reconciliation (Grab CSV, Lineman XLSX) → `rider_deliveries`
- P&L (daily/monthly/yearly), cash flow forecast
- Recipe + ingredient cost engine (Phase 31)
- LINE bot (`/line/webhook`) — text expense entry, image OCR, daily/weekly digest, stock alert
- Inventory forecast + reorder + AI day-of-week order advice
- Scheduled jobs (APScheduler): 06:00 digest / 07:00 stock / 09:00 AP due / 20:00 budget / Mon 08:00 weekly

Frontend (`VEXONHQ` repo) is a separate Next.js 14 app talking to this backend over HTTPS.

---

## Stack

- **Python 3.11**, FastAPI, uvicorn
- `psycopg2-binary` (for high-volume INSERTs) + `supabase` client (OCR flows)
- `openai>=1.40` (GPT-4o vision)
- `pdfplumber`, `openpyxl`, `pandas`, `Pillow`, `pytesseract`, `pypdfium2` (OCR + parsing)
- `apscheduler` for cron jobs
- `PyJWT` for auth
- Hosted on Coolify (self-host, Nixpacks pack)

Full deps in `requirements.txt`.

---

## Repository layout

```
vexonhq-ocr-api/
├── main.py                          # entrypoint — middleware, CORS, JWT, include_router(...)
├── auth_routes.py                   # /auth/login, /auth/logout — JWT issue
├── pos_import.py                    # POS Excel ingest (FoodStory 8 report types)
├── phase2_routes.py                 # /dashboard/overview, /phase2/pnl/*
├── phase3_arap_routes.py            # AR/AP tracking
├── phase3_quick_entry_routes.py     # /quick-entry — manual income/expense
├── phase3_daybook_routes.py         # /daybook — unified day-by-day view
├── phase3_category_routes.py        # category hierarchy
├── phase3a_ai_categorize_routes.py  # AI auto-categorize vendor bills
├── phase3a_anomaly_routes.py        # AI anomaly detection
├── pnl_routes.py                    # P&L daily/monthly
├── line_bot_routes.py               # LINE bot — webhook + scheduled digests
├── budget_routes.py                 # budget tracking + LINE alert
├── export_routes.py                 # /export/* — Excel exports
├── phase10_narrative_routes.py      # AI P&L narrative
├── phase11_search_routes.py         # smart search
├── phase12_bank_statement_routes.py # KBank PDF parser
├── bill_payment_routes.py           # /bills/payment + /bills/payment/slip-match (Phase 32)
├── menu_routes.py                   # POS analytics — HUGE file (~4100 lines, 41 endpoints)
│                                    # /pos/menu, /pos/heatmap, /pos/menu-engineering, /pos/payments,
│                                    # /pos/bill-analysis, /pos/voids, /pos/staff, /pos/shifts, /pos/tables,
│                                    # /pos/food-cost (Phase 64), /pos/hourly, /pos/channels, /pos/discounts,
│                                    # /pos/predict, /pos/dow, /pos/flash, /pos/goals, /pos/calendar, ...
├── yearly_routes.py                 # /pnl/yearly
├── inventory_forecast_routes.py     # /inventory/forecast, /inventory/reorder, /inventory/ai-order-advice
├── supplier_routes.py               # vendor analytics
├── cashflow_routes.py               # 30/60/90 day cash flow forecast
├── stock_routes.py                  # POS stock snapshots + LINE alerts
├── recipe_routes.py                 # /recipes + /ingredients
├── tax_routes.py                    # WHT tax
├── alerts_webhook_routes.py         # /alerts/uptime-webhook → Telegram (Session 19)
├── batch_import_local.py            # CLI tool for batch POS import
└── requirements.txt
```

`main.py` registers all routers near line 119–144. Add new routers there.

---

## Database (Supabase Cloud Free, project `mara-ai-prod`, Tokyo region)

**Never assume column names.** When writing SQL, verify against schema first:

```sql
SELECT column_name, data_type
FROM information_schema.columns
WHERE table_schema = 'public' AND table_name = '<table>'
ORDER BY ordinal_position;
```

Cheat sheet for tables I've already verified (use these — they are correct, written in production):

**`pos_bills`** — POS bill header
- `id` (uuid), `branch_code` (text), `receipt_code`, `invoice_no`, `sales_date` (date), `sales_time` (time)
- `drawer_code`, `order_type`, `channel`, `table_label`, `customer_name`, `customer_phone`
- `payment_type_raw`, `payment_method`, `custom_code`, `promo_type`
- `bill_gross`, `bill_discount`, `bill_net` (numeric — **NOT `net_price`, NOT `status`**)
- `opened_by`, `closed_by`, `source_import_id`, `created_at`, `updated_at`
- **There is NO `status` column.** To filter out voids, use `bill_net > 0`.
- **Branch column is `branch_code`, NOT `branch`.**

**`pos_sales_items`** — POS line items
- `id`, `bill_id` (uuid → pos_bills), `line_no`, `sku`, `item_name`, `product_group`, `category`
- `qty`, `unit_price`, `gross`, `discount`, `discount_pct`, `net_amount`, `vat_type`, `note`

**`recipes`** — menu recipes
- `id` (uuid), **`name`** (text — **NOT `menu_name`**), `selling_price`, `category`, `notes`, `created_at`, `updated_at`

**`recipe_ingredients`** — recipe ↔ ingredient join
- `id`, `recipe_id`, `ingredient_id`, **`qty_used`** (numeric — **NOT `quantity`**), `created_at`

**`ingredients`** — ingredient master (87 items currently, all priced)
- `id`, `name`, `unit`, **`price_per_unit`** (numeric), `yield_pct`, `category`, `source_item_id`, `created_at`, `updated_at`

**`vendor_bills`** — purchase bills (OCR'd)
- `id`, `vendor_name`, `invoice_no`, `bill_date`, `due_date`, `amount`, `category_code`, `branch_code`
- `review_status` (`needs_review` / `confirmed` / `rejected`)
- `payment_status` (`unpaid` / `paid`), `paid_date`

**`v_daybook`** — unified day-by-day view (P&L source of truth)
- `entry_date` (date), `direction` (`income`/`expense`), `source` (`pos_sale`, `rider_income_grab`, `rider_income_lineman`, `manual_expense`, `owner_capital`, `owner_advance`, `transfer_error`, etc.)
- `amount`, `category_code`, `branch_code`, `counterparty`, `description`
- **For P&L queries always:** `WHERE source NOT IN ('owner_capital','owner_advance','transfer_error')`
- **Never** subtract equity entries separately — leads to negative expense bug (Session 6 incident, see DAILY_LOG)

Other tables: `bank_statement_entries`, `manual_entries`, `categories`, `expense_categories`, `pos_imports`, `pos_inventory_snapshots`, `pos_inventory_items`, `pos_cashflow_entries`, `rider_deliveries`, `budgets`, `invoice_validation_warnings`, `statement_rules`.

---

## Running locally

```powershell
cd C:\Users\rapee\vexonhq-ocr-api
# 1. Activate or create venv if needed
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt

# 2. Required env vars
$env:DATABASE_URL = "postgresql://..."          # Supabase pooler URL
$env:SUPABASE_URL = "https://xxx.supabase.co"
$env:SUPABASE_SERVICE_KEY = "..."
$env:OPENAI_API_KEY = "sk-..."
$env:JWT_SECRET = "..."

# 3. Run
uvicorn main:app --reload --port 8000

# 4. Hit
curl http://localhost:8000/health
```

## Deploy

1. Code change → save
2. `git diff` — review what changed (must be small + focused)
3. Hand the diff + commit message to TUM in PowerShell-paste form (TUM commits + pushes)
4. Coolify watches `tum9819/vexonhq-ocr-api` on `main` and auto-deploys via Webhook (Nixpacks, ~20-30s)
5. After deploy: `curl https://b4zhad8qkoxjushdq8465056.178.128.31.76.sslip.io/health` should return 200

If auto-deploy doesn't trigger, manual: Coolify dashboard → `vexonhq-ocr-api` → Deploy button.

---

## Auth

- All routes require `Authorization: Bearer <JWT>` except those in `PUBLIC_PATHS` in main.py:156.
- Login: `POST /auth/login` with `{username, password}` returns JWT.
- Production credentials live in TUM's password manager — don't hardcode.

---

## Testing

Smoke tests live in `tests/test_smoke.py` (actually added Session 24, 2026-05-19 — earlier CLAUDE.md claimed Session 19 but the file never existed until now).

**Easiest: the `verify.ps1` wrapper.** Run from the repo root in PowerShell.

```powershell
.\verify.ps1            # default mode: compileall syntax check on all .py (~2s, no deps)
.\verify.ps1 -Smoke     # + live pytest against deployed backend (~5s when healthy)
```

The `-Smoke` mode needs `pip install pytest requests` once. It hits 55 critical routes over HTTP and fails loudly if any returns 404 — the Phase 32 regression class. It also asserts `/health/deep` actually returns DB ok and that `/openapi.json` lists ≥ 150 paths.

The smoke suite includes `_request_with_retry()` that retries 502/503/504 + ConnectionError up to 3× with 3s backoff, so a Coolify auto-deploy window doesn't trip false failures. Real outages (> ~10s) still fail loudly.

Direct pytest invocation (if you prefer):
```powershell
$env:BACKEND_URL = "https://b4zhad8qkoxjushdq8465056.178.128.31.76.sslip.io"
pytest tests/test_smoke.py -v
```

For per-feature changes: ask user whether to add a new pytest case to `tests/test_smoke.py` before claiming the fix is complete.

---

## Monitoring (Session 24)

- **`GET /health/deep`** (public, accepts GET+HEAD) probes Postgres + Supabase with real queries — returns 200 healthy / 200 degraded / 503 unhealthy. Use this URL when adding new external monitors; `/health` only reports env-var flags.
- **Uptime Robot** monitors `/health/deep` every 5 min and fires Discord `@everyone` alerts to TUM's `VEXONHQ Ops` server on DOWN + UP transitions.
- **AI auto-diagnosis (`auto_diagnose.py`)** — when `/health/deep` reports 503, a FastAPI BackgroundTask calls Anthropic Claude Haiku with the failed check details and posts a Thai/English diagnosis to the same Discord channel ~5 s after Uptime Robot's own DOWN alert. Rate-limited to 1 diagnosis per error_type per 10 min. Skips silently if env vars aren't set, so the feature is opt-in via Coolify.
- **`/alerts/uptime-webhook` → Telegram** (Session 19) is dormant: Uptime Robot free plan locks both Telegram and Webhook integrations. Keep the code; reactivate when either upgrading the plan or switching to self-hosted UptimeKuma.

### Optional env vars for AI diagnosis (set in Coolify when ready)

| Var | Source | Notes |
|---|---|---|
| `ANTHROPIC_API_KEY` | console.anthropic.com → API Keys | Set spend cap $5/mo as safety net. Expect ~฿1-4/mo at our outage frequency. |
| `DISCORD_OPS_WEBHOOK_URL` | Discord channel → Edit → Integrations → Webhooks → Copy URL | Same channel Uptime Robot posts to. Reuse the existing webhook. |
| `ANTHROPIC_DIAGNOSE_MODEL` | optional override (default `claude-haiku-4-5`) | Upgrade to Sonnet only if Haiku diagnoses prove off-target. |

Without these, `/health/deep` behavior is unchanged — diagnosis just no-ops with a warning log.

---

## Reference docs (read when relevant)

All inside the **frontend repo** at `C:\Users\rapee\VEXONHQ\docs\`:

- `01_PROJECT/CHANGELOG.md` — every release, what changed
- `01_PROJECT/ROADMAP.md` — phases planned + done
- `01_PROJECT/TOMORROW.md` — current session priorities + open items
- `01_PROJECT/README.md`, `AI_CONTEXT.md`, `QUICK_LINKS.md`
- `02_ARCHITECTURE/ARCHITECTURE.md`, `DEPLOYMENT.md`, `SYSTEM_MAP.md`, `SYSTEM_HISTORY.md`
- `03_SPECS/API_SPEC.md`, `FINANCE_SPEC.md`, `MASTER_SPEC.md`, `OCR_SPEC.md`, `PARSER_SPEC.md`, `UPLOAD_SPEC.md`
- `04_LOGS/DAILY_LOG_2026_05.md` — append-only session-by-session log
- `04_LOGS/PROJECT_LOG.md` — chronological project log
- `05_API/API_REFERENCE.md`
- `06_SUPPORT/TROUBLESHOOTING.md`
- `END_OF_SESSION_CHECKLIST.md`

The frontend repo also has all docs under `Documents\Claude\Projects\MaraStation\docs\` (TUM uses this as a staging copy).

---

## Known pitfalls

1. **Hallucinated SQL columns** — `net_price`, `b.status`, `b.branch`, `staff`, `r.menu_name`, `ri.quantity` were all WRONG. They DON'T EXIST in production schema. Patched in Session 18 for `/pos/food-cost`, but the same pattern likely remains in ~5-10 other endpoints in `menu_routes.py`. Audit when changing those endpoints.

2. **Files removed during refactor** — Session 16 commit `742b618` accidentally deleted the entire `/inventory/ai-order-advice` endpoint (165 lines). Restored Session 18 from git history (commit `b19b23f`). Pattern: `git log -S "<function_name>"` to find deleted code.

3. **`/health` route conflict** — 4 files register `/health` (main.py, cashflow_routes.py, export_routes.py, supplier_routes.py). FastAPI uses the first (`main.py:226`). Don't rely on it for sub-routers — use `/cashflow/health` style instead.

4. **POS import duplicate** — re-uploading the same file (same hash) returns 409 silently now (`status=already_imported`), not 500. Fixed 2026-05-16.

5. **Coolify auto-deploy via Webhook works reliably.** Previous "CORS commits weren't deployed" hypothesis (Session 17/18) was wrong — deploys went through; CORS errors were a side-effect of unhandled exceptions in endpoints that hadn't been tested end-to-end.

---

## Session protocol (when wrapping a coding session)

End-of-session checklist (also in `docs/END_OF_SESSION_CHECKLIST.md`):
1. PowerShell `git status` clean → `git push origin main` (TUM does this)
2. Verify both Coolify apps Running
3. DigitalOcean Droplet snapshot (manual, name like `vexonhq-<phase>-YYYY-MM-DD`)
4. Update `CHANGELOG.md`, `DAILY_LOG_*.md`, `TOMORROW.md`, `ROADMAP.md` as needed
5. Update `AI_CONTEXT.md`, `README.md`, `PROJECT_LOG.md` only if architecture/structure changed

Docs update policy: append-only for CHANGELOG / DAILY_LOG / TROUBLESHOOTING; replace for everything else.
