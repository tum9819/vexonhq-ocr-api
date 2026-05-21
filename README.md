# vexonhq-ocr-api

FastAPI backend powering [VEXONHQ](https://github.com/tum9819/VEXONHQ) — a Thai SMB ops platform that runs invoice OCR, P&L, recipe costing, LINE bot, slip matching, and cashflow tooling for a music-bar / grill restaurant (Mara Station).

Production deploys to DigitalOcean via Coolify auto-build on every push to `main`. Live URL: `https://b4zhad8qkoxjushdq8465056.178.128.31.76.sslip.io`

---

## Stack

- **Python 3.11** · FastAPI · uvicorn
- **Postgres** via Supabase (psycopg2 driver) + supabase-py for storage / RLS
- **OCR**: OpenAI GPT-4o (Vision) for invoices, GPT-4o-mini for slip/invoice classification
- **AI**: Anthropic Claude (Haiku for diagnosis, Sonnet for menu/recipe AI)
- **Scheduler**: APScheduler in-process (4 cron jobs — see Heartbeats below)
- **Deploy**: Coolify on a single DO droplet `vexonhq-core`, with Traefik proxy

---

## Local setup

```powershell
git clone https://github.com/tum9819/vexonhq-ocr-api.git
cd vexonhq-ocr-api
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env   # fill in DATABASE_URL etc.
uvicorn main:app --reload --port 8000
```

Required env vars (production = Coolify env, local = `.env`):

| Var | Purpose |
|-----|---------|
| `DATABASE_URL` | Supabase pooler Postgres URL |
| `SUPABASE_URL` + `SUPABASE_SERVICE_KEY` | Supabase REST + storage |
| `OPENAI_API_KEY` | Invoice OCR + slip OCR |
| `ANTHROPIC_API_KEY` | AI Link / recipe AI / `/health/deep` auto-diagnosis |
| `LINE_CHANNEL_TOKEN` + `LINE_CHANNEL_SECRET` + `LINE_USER_ID` | LINE bot |
| `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` | (optional) Telegram fallback when LINE push fails |
| `DISCORD_OPS_WEBHOOK_URL` | (optional) auto-diagnosis posts here on outage — P1.4 MVP plain-text channel |
| `DISCORD_BOT_TOKEN` | (optional) Bot token from Developer Portal → Bot → Reset Token. When set, auto-diagnosis switches to Bot API + inline Restart button (P1.4 v2) |
| `DISCORD_APP_PUBLIC_KEY` | (required when bot is set) hex public key from Developer Portal → General Information → Public Key. Used to verify Ed25519 signature on `/alerts/discord-interaction` |
| `DISCORD_APP_ID` | (required when bot is set) Application ID. Used in interaction-response PATCH URL |
| `DISCORD_OPS_CHANNEL_ID` | (required when bot is set) channel ID where the bot posts (right-click channel → Copy ID with Developer Mode on) |
| `COOLIFY_API_TOKEN` | (required when bot is set) Coolify dashboard → Keys & Tokens → API tokens. Lets the Restart button call back to Coolify |
| `COOLIFY_BACKEND_APP_UUID` | (required when bot is set) UUID of `vexonhq-ocr-api` in Coolify (visible in app URL) |
| `COOLIFY_API_BASE_URL` | (optional) override Coolify API base — default `http://178.128.31.76:8000` |
| `COOLIFY_LOG_TAIL_LINES` | (optional, P1.4 v3) how many Coolify stdout lines to send to Claude Haiku when 🩹 Show patch is clicked — default `200`, min `20` |
| `DO_API_TOKEN` | (optional, P2.4) DigitalOcean Personal Access Token (read+write). Required for weekly auto-snapshot rotation. Create at cloud.digitalocean.com/account/api/tokens with scopes: `droplet:read`, `image:read`, `image:delete` |
| `DO_DROPLET_NAME` | (optional, P2.4) name of the droplet to snapshot — default `vexonhq-core` |
| `DO_SNAPSHOT_PREFIX` | (optional, P2.4) prefix for auto-rotated snapshots — default `vexonhq-auto-`. NEVER touches snapshots with other prefixes (e.g. `vexonhq-clean-base`, `vexonhq-session*`) |
| `DO_SNAPSHOT_MAX_KEEP` | (optional, P2.4) how many auto-prefixed snapshots to retain after rotation — default `1`. Bump to `2` for extra rollback safety (+~$1.80/mo per slot) |
| `LOG_FORMAT` | `json` for structured logging, anything else for human text (default) |
| `SLOW_QUERY_WARN_SEC` | Slow query WARNING threshold in seconds (default `3.0`) |
| `SLOW_QUERY_CRITICAL_SEC` | Slow query ERROR threshold in seconds (default `10.0`) |

---

## Health + observability

### `GET /health` — fast liveness ping
Returns `200` immediately. Used by Uptime Robot for primary uptime tracking.

### `GET /health/deep` — full dependency check
Checks Postgres, Supabase REST, OpenAI/LINE config presence. Returns `200` healthy / `200` degraded (missing optional config) / `503` unhealthy (DB down). On `503`, fires `auto_diagnose.py` as a FastAPI BackgroundTask that calls Claude Haiku to read the failure context and posts a diagnosis to the Discord ops channel.

### `GET /cron/health` — scheduled-job staleness check *(P1.2)*
Reads `public.job_heartbeat` and reports per-job last-run state. Flags a job as `stale` when `last_run_at` is older than `2 × expected_interval_hours`. Returns `503` if any job is stale so Uptime Robot can alert on it directly. Sample response:

```json
{
  "status": "healthy",
  "jobs": [
    {"job_id": "daily_line_digest", "last_run_at": "2026-05-21T06:00:12+07:00",
     "last_success_at": "...", "minutes_since_last_run": 45,
     "expected_interval_hours": 24, "run_count": 12, "error_count": 0,
     "stale": false}
  ]
}
```

### Logging

Default format (local dev) is line-based text:
```
2026-05-21 06:00:01 INFO line_bot: Daily digest sent successfully
```

In production set `LOG_FORMAT=json` and Coolify will index by `level`, `logger`, `msg`, plus any `extra={...}` fields the caller passed:
```json
{"ts":"2026-05-21T06:00:01","level":"INFO","logger":"line_bot","msg":"Daily digest sent"}
```

### Slow queries *(P2.2)*

Every cursor opened through `get_db_conn()` times its `execute()`. Queries ≥ `SLOW_QUERY_WARN_SEC` log a `WARNING`; queries ≥ `SLOW_QUERY_CRITICAL_SEC` log an `ERROR`. The slow-query logger name is `vexon.slow_query` — filter on it in Coolify to see the slowest queries over time.

---

## Scheduled jobs

Four APScheduler cron jobs run inside the FastAPI process (`line_bot_routes.py`):

| Job | Schedule (Asia/Bangkok) | What it does |
|-----|------------------------|--------------|
| `daily_line_digest` | Daily 06:00 | LINE push: yesterday's revenue / expense / open anomalies |
| `daily_ap_due_reminder` | Daily 09:00 | LINE push: today's accounts-payable due |
| `daily_budget_alert` | Daily 20:00 | LINE push: any budget category that crossed its threshold today |
| `weekly_summary` | Mon 08:00 | LINE push: last week's P&L + AR/AP balance |

Each job is wrapped in `@heartbeat(job_id)` (from `cron_heartbeat.py`) that writes a row in `public.job_heartbeat` on every run — last success, last error, run count, error count. `/cron/health` reads from there.

---

## LINE bot reliability *(P0.3)*

The `_push_text()` helper in `line_bot_routes.py` retries on transient failures:

- **5xx / timeout / connection error** → retry up to 3× with exponential backoff (1s, 4s)
- **4xx** → fail fast (config / auth / payload error — retry won't help)
- **Terminal failure** → try Telegram fallback (`TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID`), then raise `HTTPException(502)` so the caller's existing error handling fires

TUM still gets the alert through Telegram even when LINE is down. If Telegram env vars are unset the fallback no-ops silently — LINE remains the primary channel.

---

## Migrations

SQL migrations live in `migrations/YYYY_MM_DD_<name>.sql`. They are idempotent (`CREATE TABLE IF NOT EXISTS`, `ADD COLUMN IF NOT EXISTS`, etc.). Apply through the Supabase MCP `apply_migration` tool or paste into the SQL editor. Recent migrations:

| File | Purpose |
|------|---------|
| `2026_05_21_cron_heartbeat.sql` | `job_heartbeat` table + seed 4 jobs (P1.2) |
| `2026_05_21_fix_ocr_unit_artifact.sql` | Normalise OCR garbage `ตคก` → `ดอก` in `invoice_items` |
| `2026_05_20_ingredients_invoice_match_name.sql` | `invoice_match_name` alias column (Phase V3) |
| `2026_05_20_ingredients_pack_size.sql` | `pack_size`, `invoice_unit`, `unit_cost_source` (Phase V) |
| `2026_05_20_remove_bundle_ingredients.sql` | Drop POS-seeded promo-bundle ingredients |

---

## Pre-push verification

Run before every `git push origin main`:

```powershell
.\verify.ps1            # syntax check only (no deps)
.\verify.ps1 -Smoke     # syntax + 55 live smoke tests against deployed backend
```

`-Smoke` is normally run AFTER deploy to verify the new container serves every critical route. A `404` on any tested route = regression (deleted/renamed endpoint).

Backup the current `origin/main` HEAD as a rollback target before pushing:

```powershell
git fetch origin
git tag backup-pre-<short-name>-YYYY-MM-DD origin/main
git push origin backup-pre-<short-name>-YYYY-MM-DD
```

If a push regresses production:
```powershell
git reset --hard backup-pre-<short-name>-YYYY-MM-DD
git push --force-with-lease origin main
```

Coolify will rebuild from the rollback point within ~2 minutes.

---

## Repo layout

```
main.py                       — FastAPI app, /health/*, slow-query cursor, JSON log wiring
line_bot_routes.py            — LINE webhook + scheduled digests + push helpers
cron_heartbeat.py             — Heartbeat decorator + /cron/health endpoint
recipe_routes.py              — Recipe CRUD + ingredient sync from invoices
slip_routes.py                — Bank-transfer slip OCR + match engine
phase11_search_routes.py      — Natural-language AI search
phase12_bank_statement_routes.py — Bank statement upload + 3-way match
auto_diagnose.py              — /health/deep failure → Claude diagnosis → Discord
verify.ps1                    — Pre-push verification (syntax + smoke)
tests/test_smoke.py           — 55 route-existence smoke tests
migrations/*.sql              — Idempotent SQL migrations
```

---

## Stability roadmap status (Session 24+)

- ✅ P0.1 — `/health/deep` + Discord alerts via Uptime Robot
- ✅ P0.2 — `tests/test_smoke.py` + `verify.ps1`
- ✅ P0.3 — LINE retry + Telegram fallback *(this batch)*
- ✅ P1.1 — `except:pass` cleanup → `log.exception()` *(this batch)*
- ✅ P1.2 — Cron heartbeat table + `/cron/health` *(this batch)*
- ✅ P1.4 MVP — Auto-diagnosis on `/health/deep` failure (`auto_diagnose.py`)
- ✅ P2.1 — JSON structured logging *(this batch)*
- ✅ P2.2 — Slow query log warning *(this batch)*
- ⏳ P1.4 v2 — Discord inline restart button (Coolify API)
- ⏳ P2.3 — Separate scheduler out of FastAPI process
- ⏳ P2.4 — DigitalOcean weekly auto-snapshot
