# SYSTEM_OVERVIEW.md

> Compiled 2026-06-18. Purpose: give any AI agent (Claude, Antigravity/Gemini, or a human) full context on this system without having to read AGENTS.md (106KB) + CLAUDE.md (21KB) + a dozen audit/handoff files from scratch.
>
> **This file is a snapshot, not the source of truth for rules.** For the authoritative, currently-enforced rules and any rule added after 2026-06-18, read `AGENTS.md` and `CLAUDE.md` directly — they are kept up to date by the team; this file may drift. If something here conflicts with AGENTS.md/CLAUDE.md, **those win**.

---

## 1. What this system is

**vexonhq-ocr-api** is the FastAPI backend behind **mala station**, a Thai restaurant (Thewet, Bangkok; ~660 bills/month, ~282k baht/month revenue). It is reachable in production at `https://api.marastation.com`. It is one of five deployed applications in the marastation.com ecosystem — see §1a for the full landscape.

**Login flow (corrected 2026-06-18):** the literal `/login` page lives in the **marastation-web** repo, served at `https://marastation.com/` (root domain). It authenticates via Supabase Auth (`supabase.auth.signInWithPassword()`), which sets a session cookie scoped to the `.marastation.com` domain — shared across all subdomains. The internal dashboard (**VEXONHQ**, deployed at a separate subdomain, "vexonhq-frontend" in Coolify) reads that same cookie via `supabase.auth.getUser()` in its middleware and never issues its own session. When VEXONHQ calls this backend, it forwards the Supabase JWT; this backend's `verify_token()` validates it via `SUPABASE_JWT_SECRET` (its dual-path also still accepts its own legacy `JWT_SECRET`-signed tokens for any older/internal callers). **This is the flow TUM refers to as "ต้อง login ผ่าน marastation.com ถึงจะเข้าระบบฐานข้อมูลของร้านได้."**

### 1a. System landscape — all 5 deployed apps

This backend does not stand alone. There are 5 Coolify-deployed applications sharing the `marastation.com` domain and (mostly) the same Supabase project (`mara-ai-prod`, ref `osneubnwghvbwyazaedo`, Tokyo region):

| App (Coolify name) | Domain | Local repo | Role |
|---|---|---|---|
| marastation-web | marastation.com (root) | `C:\Users\rapee\marastation-web` | Public venue site (menu/events/atmosphere) **and** the actual `/login` page; fetches `GET /menu/public` from this backend |
| vexonhq-frontend (VEXONHQ) | subdomain (e.g. app.marastation.com) | `C:\Users\rapee\VEXONHQ` | Internal ops dashboard (POS, AR/AP, admin) consuming this backend's API; reads the shared Supabase cookie, issues no session of its own |
| **vexonhq-ocr-api** (this repo) | api.marastation.com | `C:\Users\rapee\vexonhq-ocr-api` | This backend — documented in the rest of this file |
| marastation-ai | ai.marastation.com | `C:\Users\rapee\marastation-ai` | Claude-powered chatbot for VEXONHQ/the restaurant; shares the same `.marastation.com` auth cookie, calls this backend's API |
| marastation-translate | translate.marastation.com | `C:\Users\rapee\marastation-translate` | Standalone Chinese↔Thai/English real-time meeting translator (Express+Socket.io+OpenAI Whisper) — **unrelated to restaurant ops**, no shared auth/data with the other four |

Each repo has its own `AGENTS.md`/`CLAUDE.md` with its own rules — this file does not attempt to replace those. It exists so anyone working specifically in `vexonhq-ocr-api` understands where its callers come from and where its data goes.

Core capabilities:
- OCR of vendor invoices/bills via OpenAI GPT-4o Vision (Structured Outputs as of 2026-06-01)
- POS analytics (FoodStory Excel ingest, menu engineering, 41+ endpoints)
- AR/AP tracking, bill payment + due-date forecasting, bank-slip 3-way matching
- P&L daily/monthly/yearly with AI-generated narrative
- LINE bot: scheduled digests, payment reminders, stock alerts, photo-based manual expense entry
- Bank statement parsing (KBank PDF → `bank_statement_entries`)
- Cashflow forecasting, budget tracking, loan tracking
- Recipe/menu cost engine, inventory forecasting, supplier analytics

**Stack:** Python 3.11, FastAPI, Supabase Postgres (transaction-pooler `:6543`, NOT session-pooler `:5432`), OpenAI API, deployed via Coolify (self-hosted on a DigitalOcean 4GB VPS) with Nixpacks auto-build on git push.

---

## 2. Architecture — router map

All routers are registered in `main.py`. 38 routers, 150+ endpoints (OpenAPI-documented).

| Router | Source file | Prefix | Purpose |
|---|---|---|---|
| auth_router | auth_routes.py | /auth | JWT login/logout |
| pos_router | pos_import.py | /pos/import | POS data ingestion (FoodStory Excel, 8 report types) |
| stock_in_router | stock_in_routes.py | /stock-in | Stock inbound recording |
| phase2_router | phase2_routes.py | /dashboard, /phase2 | Dashboard overview, daily/monthly P&L |
| phase3_arap_router | phase3_arap_routes.py | /arap | AR/AP entry lifecycle |
| phase3_quick_entry_router | phase3_quick_entry_routes.py | /quick-entry | Manual income/expense entry |
| phase3_daybook_router | phase3_daybook_routes.py | /daybook | Unified day-by-day ledger view |
| phase3_category_router | phase3_category_routes.py | /categories | Category master/hierarchy |
| phase3a_ai_categorize_router | phase3a_ai_categorize_routes.py | /ai/categorize | AI auto-categorization of bills |
| phase3a_anomaly_router | phase3a_anomaly_routes.py | /ai/anomaly | AI anomaly detection |
| pnl_router | pnl_routes.py | /pnl | P&L daily/monthly |
| breakeven_router | breakeven_routes.py | /breakeven | Breakeven analysis + alerts |
| line_router | line_bot_routes.py | /line | LINE webhook + all scheduled digests |
| budget_router | budget_routes.py | /budget | Budget tracking + LINE alert |
| cron_health_router | cron_heartbeat.py | /cron | Scheduled-job heartbeat monitoring |
| export_router | export_routes.py | /export | Excel exports |
| narrative_router | phase10_narrative_routes.py | /narrative | AI-generated P&L narrative |
| search_router | phase11_search_routes.py | /search | Smart transaction search |
| bank_statement_router | phase12_bank_statement_routes.py | /bank-statement | KBank PDF parsing/classification |
| bill_payment_router | bill_payment_routes.py | /bills | Payment tracking + slip-match OCR |
| menu_router | menu_routes.py | /pos | POS analytics (41 endpoints, ~4100 lines — largest file) |
| yearly_router | yearly_routes.py | /pnl/yearly | Yearly P&L aggregation |
| inventory_forecast_router | inventory_forecast_routes.py | /inventory | Forecast, reorder, AI advice |
| supplier_router | supplier_routes.py | /suppliers | Vendor analytics |
| cashflow_router | cashflow_routes.py | /cashflow | 30/60/90-day cashflow forecast |
| stock_router | stock_routes.py | /stock | POS stock snapshots + LINE alerts |
| recipe_router / ingredient_router | recipe_routes.py | /recipes, /ingredients | Recipe + ingredient cost engine |
| menu_public_router | menu_public_routes.py | /menu/public | Public menu API (no auth) |
| tax_router | tax_routes.py | /tax | WHT tax calculation |
| alerts_router | alerts_webhook_routes.py | /alerts | Uptime Robot → Telegram/Discord webhooks |
| discord_router | discord_routes.py | /discord | Discord interaction handlers |
| do_snapshot_router | do_snapshot_routes.py | /snapshots | DigitalOcean snapshot rotation |
| ai_exec_router | ai_exec_routes.py | /ai/exec | AI code execution (IP-gated, security-sensitive) |
| rules_router | rules_routes.py | /rules | Transaction categorization rules |
| slip_router | slip_routes.py | /slips | Bank transfer slip OCR + 3-way match |
| loan_router | loan_routes.py | /loans | Loan tracking + balance forecast |
| store_context_router | store_context_routes.py | /store | Store metadata/config |
| ai_monitor_router | ai_monitor_routes.py | /ai/stats, /ai/calls | AI usage audit trail (JWT-gated) |

**Global middleware:** `JWTAuthMiddleware` (protects everything not in `PUBLIC_PATHS`), CORS (localhost:3000, Vercel deployment, marastation.com, Coolify sslip.io domain).

**PUBLIC_PATHS (no JWT):** `/`, `/health`, `/health/deep`, `/cron/health`, `/auth/login`, `/auth/logout`, `/docs`, `/openapi.json`, `/redoc`, `/alerts/uptime-webhook`, `/alerts/test-telegram`, `/alerts/discord-interaction`, `/alerts/discord-restart-test`, `/line/webhook`, `/snapshots/status`, `/snapshots/auto-rotate`, `/menu/public`.

> Rule: any new public endpoint must be added to this set in `main.py` (~line 275).

---

## 3. Database schema (Supabase Postgres)

~60 tables/views, grouped by domain. Names only — verify exact columns against `information_schema.columns` before writing SQL (see Rules section — this has caused real bugs).

**POS / sales:** `pos_sales_daily`, `pos_bills`, `pos_sales_items`, `pos_cashflow_entries`, `pos_import` (import audit trail), `pos_inventory_snapshots`, `pos_inventory_items`

**Purchases / vendor bills (OCR):** `vendor_bills` (review_status: pending/confirmed/rejected; payment_status: unpaid/paid), `invoice_items`, `invoice_validation_warnings`, `attachments` (file_sha256 dedup), `products` (canonical SKU master, ~21 rows)

**AR/AP & payments:** `ar_ap_entries`, `ar_ap_line_items`, `ar_ap_payments`

**Bank & settlement:** `bank_statement_entries`, `statement_rules`, `slips` (match_status: unmatched/matched_stmt/matched_full/needs_review/rejected), `rider_deliveries` (Grab/Lineman)

**Recipes / cost:** `recipes` (87 items), `recipe_ingredients`, `ingredients`, `selling_price_channels` (per-platform pricing, added 2026-05)

**Manual ledger:** `manual_entries`, `categories`, `expense_categories`

**Audit / config:** `job_heartbeat`, `ai_call_log`, `ai_drift_state`, `ai6_cashflow_categorization_log`, `budgets`, `user_page_config`

**Key views:**
- `v_daybook` — unified income/expense ledger across all sources
- `v_daybook_pnl` — P&L aggregation, **must exclude** `source IN ('owner_capital','owner_advance','transfer_error')` (see incident below)
- `v_invoice_review_queue`, `v_invoice_due_soon` (due_date ≤ today+7, payment_status='unpaid')
- `v_loan_balance`, `v_budget_status`

55+ migrations dated 2026-05-19 through 2026-06-16 live in `migrations/` and `sql/`.

---

## 4. Scheduled jobs (APScheduler, registered in `line_bot_routes.py`)

| Job | Schedule (Bangkok time) | Purpose | Heartbeat |
|---|---|---|---|
| daily_line_digest | 06:00 | Yesterday's P&L summary → LINE | yes |
| daily_stock_digest | 07:00 | Low-stock alert → LINE | yes |
| ap_due_reminder | 09:00 | Overdue/due-soon vendor bills → LINE | yes |
| breakeven_daily | 08:00 | Breakeven status → LINE | yes |
| ai_drift_watchdog | 08:30 | AI model quality/cost monitor → Discord | yes |
| daily_budget_alert | 20:00 | Budget variance alert | yes |
| weekly_summary | Mon 08:00 | Weekly P&L + KPI summary → LINE | yes |
| breakeven_eom | 1st of month, 08:00 | End-of-month breakeven analysis | yes |
| pos_sales_stale_watchdog | continuous poll | Alerts if POS import is stale | yes |
| cron_stale_watchdog | rate-limited per job | Watches all heartbeats, Discord alert if >2x expected interval | yes |

`GET /cron/health` (HEAD supported) reports per-job status; 503 if anything stale.

> Rule: any new scheduled job must register a `@heartbeat(<job_id>)`.

---

## 5. Environment & deployment

**Deployment:** Coolify (self-hosted, DigitalOcean 4GB shared VPS, Nixpacks builder), auto-deploys on push to `main` via Git webhook (~20-30s build). Public URL `https://api.marastation.com`. Application UUID is the 24-char subdomain prefix before the first dot — **not** the Docker image tag (these look similar and have been confused before, see pitfalls).

**Health checks:** `GET /health` (liveness, used by Uptime Robot), `GET /health/deep` (deep dependency probe — Postgres/Supabase/OpenAI/scheduler; 503 triggers optional Claude-Haiku auto-diagnosis posted to Discord).

**Key env vars** (names only — see `.env.example`): `DATABASE_URL`, `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`, `SUPABASE_STORAGE_BUCKET`, `OPENAI_API_KEY`, `OPENAI_VISION_MODEL`, `JWT_SECRET`, `LINE_CHANNEL_TOKEN`, `DISCORD_BOT_TOKEN`, `DISCORD_OPS_WEBHOOK_URL`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `DO_API_TOKEN`, `ANTHROPIC_API_KEY`, `ANTHROPIC_DIAGNOSE_MODEL`, `AI_EXEC_ALLOWED_IPS`, `LOG_FORMAT`, `SLOW_QUERY_WARN_SEC`, `SLOW_QUERY_CRITICAL_SEC`, `SENTRY_DSN`/`SENTRY_ENVIRONMENT`/`SENTRY_RELEASE`/`SENTRY_TRACES_SAMPLE_RATE` (optional).

**External services:** OpenAI (GPT-4o Vision OCR, active), Supabase Cloud Tokyo (DB+storage, active), LINE Messaging API (active), Discord (ops alerts + bot, active), Telegram (dormant — Uptime Robot free plan blocks it), KBank (manual PDF upload), FoodStory POS (manual Excel import), Grab/Lineman (manual rider-income import), DigitalOcean API (snapshot rotation), Uptime Robot (HTTP monitor, 5-min HEAD polls to `/health/deep` — free plan is HEAD-only, not GET).

---

## 6. Hard rules (extracted from AGENTS.md / CLAUDE.md — verify there for latest)

**Workflow:**
- Explore → Plan → Implement → Verify → Commit, every session. State the plan (files touched, risk) before coding.
- Ask when requirements are ambiguous — never guess.
- Fix root causes; never suppress errors (no `except: pass`); never skip tests.
- **Push flow:** Claude/agent stages+edits+tests+commits locally, shows TUM the diff + message, waits for explicit Confirm, only then pushes. Never push without confirmation.
- Tag a rollback point before handoff to main: `git tag backup-pre-<descriptor>-YYYY-MM-DD origin/main`.
- Before declaring "ready to push": `ast.parse` all touched `.py` files, run pytest if applicable, probe the live endpoint if a route was touched.
- Run `.\verify.ps1` before any handoff — it catches known regression classes.
- After every push, check `/health/deep` and wait for the shared VPS's CPU to settle before calling it done.

**Code constraints:**
- Never hallucinate SQL column names — verify against `information_schema.columns` first. Known-wrong names that have bitten people before: `net_price`, bare `status`/`branch` (correct: `branch_code`), `staff`, `r.menu_name`, `ri.quantity` (correct: `qty_used`).
- Never block the event loop with sync I/O (file parsing, Tesseract, PDF render, GPT Vision calls) — use `asyncio.to_thread()` or `BackgroundTasks`.
- No emojis in code or commit messages.
- Don't reflexively `git checkout HEAD -- .` based on a Bash tool's truncated `git status` — on this Windows setup, PowerShell's `git status` is the source of truth.
- New router files must be registered in `main.py`; new pip dependencies need sign-off (Nixpacks compatibility); auth/JWT contract changes, schema changes, and edits to `line_bot_routes.py`/`alerts_webhook_routes.py`/scheduler files all require asking first.
- Never delete the `vexonhq-clean-base` DigitalOcean snapshot. Keep max 3 snapshots in rotation (clean-base, last-known-good, latest); delete intermediate snapshots before closing a session; rotation cost target ~$5/month.

**Domain-specific correctness rules (each tied to a real past incident — see §7):**
- Every P&L query over `v_daybook` must exclude `source IN ('owner_capital','owner_advance','transfer_error')`.
- Never set `source_type='bank_statement'` on an expense row (it's on the P&L exclusion list — silently drops the expense from profit calculations); if a bank entry is matched to a vendor bill via 3-way match, use the bill's category instead.
- POS field mapping must use the canonical Thai-column-name lookup (`r.get("<name>")` via `_CANONICAL_COLS`), never positional `iloc` — FoodStory has reordered export columns before.
- WHT tax gross-vs-net formula must be verified against real invoices/accountant input — never assumed.
- `create_token()` and `verify_token()` (JWT) must stay in lockstep — `verify_token()` must accept both `JWT_SECRET` (self-issued) and `SUPABASE_JWT_SECRET` (SSO) since both token types are valid.

---

## 7. Incident timeline (dated, for context on *why* rules exist)

| Date | What happened | Fix / status |
|---|---|---|
| 2026-05-15/19 | 31–69 vendor bills had NULL `due_date`, breaking cashflow/AP-aging | Migrations applied; 0 NULLs remained per 2026-06-15 audit |
| 2026-05-16 | Re-uploading the same POS file caused a 500 | File-hash dedup; now returns 409 `already_imported` |
| ~Session 16 | A refactor accidentally deleted the `/inventory/ai-order-advice` endpoint (165 lines) | Restored from git history (`git log -S "<fn>"`) — lesson: grep history before assuming a route doesn't exist |
| 2026-05-20 | Added `audit_trail` + enabled Row-Level Security on all public tables | Done |
| 2026-05-27 | `phase10_narrative_routes.py` missed the `v_daybook` equity-exclusion filter → P&L narrative overstated income | Fixed (commit 9296ed5); same pattern re-checked across 7+ files |
| 2026-05-30 | Bank-statement expense rows tagged `source_type='bank_statement'` silently leaked out of P&L | Documented as a hard rule; no active violations found |
| 2026-05-31 | Added per-query slow-query timing (warn 3s / critical 10s) | Active in production |
| 2026-06-01 | Rolled out OpenAI Structured Outputs for OCR (toggle `OCR_STRUCTURED`) | Active, quality baseline being monitored |
| 2026-06-02 | Added Claude-Haiku auto-diagnosis on `/health/deep` 503s, posted to Discord | Active, opt-in, rate-limited to 1/error-type/10min |
| 2026-06-03 | Switched Supabase pooler from session-mode (`:5432`) to transaction-mode (`:6543`) | Fixed a real connection-saturation outage |
| 2026-06-08 | Division-of-labor swap: Claude/Antigravity both write code now; push-after-confirm rule stays with Claude | Reflected in CLAUDE.md, see also `~/.claude/CLAUDE.md` global notes |
| 2026-06-09 | Added `attachments.file_sha256` dedup so multi-page re-uploads skip re-OCR | Active — saves GPT-4o cost |
| 2026-06-12 | Added `stock_in_*` tables for inbound stock recording | New, integration ongoing |
| 2026-06-15 | Full accuracy audit: due-date fixes verified, OCR baseline confirmed, 8 rounds of Codex review completed, ops reports removed from git | **Certified production-ready** |
| 2026-06-16 | Monitoring report: 4 bills due within 7 days (฿24,493), 4 overdue (฿52,273.95 total), 5 vendor-name consolidation groups identified | Business follow-up needed, not a system bug |
| 2026-06-18 | TUM confirms the system is working end-to-end via marastation.com login in production | This document compiled |

**Critical environment-confusion pitfalls** (cost real debugging time before):
- Coolify application UUID (24-char subdomain prefix) is **not** the Docker image tag shown nearby in the UI.
- Discord **Bot Token** (~70 chars, 2 dots) is not the OAuth2 **Client Secret** (32 chars, 0 dots) — check `len()` and dot-count before assuming you have the right one.
- Multiple route files register `/health` — `main.py`'s registration order wins; other modules' health routes are effectively shadowed unless namespaced.

---

## 8. Open items as of 2026-06-18

| Item | Severity | Detail |
|---|---|---|
| SINGHA BEER bill SS 68093823 | 🔴 Critical (business, not code) | ฿30,285.98, 274 days overdue, marked REJECTED — needs vendor contact, possible dispute |
| SINGHA BEER bill SS 690600113 | 🟡 High (business) | ฿20,639.98, 12 days overdue, pending review |
| 2 small bills (ร้านเจ๊บาบา, ร้านขายส่ง) | 🟢 Routine | ~฿1,348 combined, 1 day overdue |
| OPS-12: pin `requirements.txt` | 🟡 Ops | Needs TUM to paste the container's `pip freeze` output |
| SEC-1b: `AI_EXEC_ALLOWED_IPS` | 🟡 Security | Needs to be set in Coolify to finish locking down `/ai/exec` |
| Vendor name consolidation | 🟢 Low | 5 groups with spelling variants (SINGHA, BB Superstore, WEALIMEX, CP Extra, ขายส่ง) — blocked on a unique constraint on (vendor_name, invoice_no); needs manual per-invoice review |
| WHT tax gross/net formula (PNL-3) | 🟡 Medium | Needs accountant/invoice verification before trusting the current formula |
| DB latency (~590ms) | 🟢 Optional | Could improve with in-process connection pooling; low priority |

---

## 9. Quick stats

- 38 routers, 150+ endpoints, ~60 DB tables/views, 55+ migrations (2026-05-19 → 2026-06-16)
- 10 scheduled jobs + 2 watchdog processes
- 104 vendor bills as of the 2026-06-15 audit
- Production stack: FastAPI + Supabase Postgres + OpenAI GPT-4o + Coolify/DigitalOcean
- Monitored by Uptime Robot (5-min polls) → Discord ops alerts; optional Claude-Haiku auto-diagnosis on outage

---

*For day-to-day working rules, security boundaries, and anything not covered here, read `AGENTS.md` and `CLAUDE.md` in this repo directly — they are the living source of truth.*
