# TOMORROW.md — vexonhq-ocr-api backend

**Last updated**: 2026-05-26 (Session 42 follow-up — Sentry paused, backend stable)

> Frontend / cross-repo context → `C:\Users\rapee\VEXONHQ\docs\01_PROJECT\TOMORROW.md`

---

## What's live + stable

- **Backend**: `https://api.marastation.com` — FastAPI, Coolify auto-deploy ✅
- **Auth**: JWT dual-path — self-issued HS256 + Supabase ES256 via JWKS (Session 41) ✅
- **Monitoring**: Uptime Robot → `/health/deep` every 5 min → Discord `@everyone` alerts ✅
- **AI auto-diagnosis**: fires on 503, posts Thai/English summary to Discord `#ops` ✅
- **DO snapshot**: auto every Sunday 03:00 BKK — new token with `image:create` scope ✅
- **Tests**: 100/100 ✅ — `test_smoke.py` (63) + `test_workflow.py` (37)

---

## Session 43 priorities

### A. [OPTIONAL] Activate Sentry backend
Code committed at `0f24462` — safe, no-ops when `SENTRY_DSN` not set.

To activate:
1. Sentry → `vexonhq-api` project → copy DSN
2. Coolify → `vexonhq-ocr-api` → Environment Variables:
   ```
   SENTRY_DSN = https://...@....ingest.sentry.io/...
   ```
3. Redeploy → verify with:
   ```powershell
   python -c "import sentry_sdk; sentry_sdk.init(dsn='$env:SENTRY_DSN'); sentry_sdk.capture_message('backend test')"
   ```
   → Should appear in `vexonhq-api` Sentry within 30s

### B. [NEXT] Playwright E2E backend coverage
API-level E2E tests — separate session after Sentry stable.

---

## How to run tests

```powershell
cd C:\Users\rapee\vexonhq-ocr-api
.\.venv\Scripts\Activate.ps1
$env:VEXONHQ_TEST_PASS = "ใส่รหัส"   # VEXONHQ_TEST_USER defaults to "vexonhq"

# Full suite (100 tests, ~54s)
python -m pytest tests/test_smoke.py tests/test_workflow.py -v

# Syntax check only (~2s, no deps)
.\verify.ps1

# Syntax + live smoke against deployed backend
.\verify.ps1 -Smoke
```

> หลัง push ทุกครั้ง รอ 30-60s ให้ Coolify redeploy ก่อนรัน tests

---

## Monitoring quick-ref

| URL | Purpose |
|-----|---------|
| `https://api.marastation.com/health` | Basic health |
| `https://api.marastation.com/health/deep` | Postgres + Supabase probe |
| `https://api.marastation.com/cron/health` | Cron job status |
| `https://api.marastation.com/auth/me` | Current user + role check |

### Scheduled checks
- **Monday ≥ 08:00 BKK**: `Invoke-WebRequest https://api.marastation.com/cron/health | ConvertFrom-Json` → `weekly_summary.run_count >= 1`
- **Sunday 2026-06-01**: check `weekly_do_snapshot.last_success_at` populated

---

## Sentry account (created, not yet active)

| Item | Value |
|------|-------|
| Org slug | `mara-00` |
| Backend project | `vexonhq-api` ✅ DSN exists |
| Frontend project | `vexonhq-frontend` ❌ not created yet |
| Discord alert rules | Skipped — requires Sentry Team (paid) |
