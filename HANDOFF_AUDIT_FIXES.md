# HANDOFF — Audit Remediation to A+ · VEXONHQ / Mara Station

**From:** Claude Code (audit / commander / QA) → **To:** Antigravity (implementation)
**Date:** 2026-06-03
**Source:** Full-system audit 2026-06-02 — report `C:\Users\rapee\Documents\Antigravity\IDE\VEXONHQ_System_Audit_2026-06-02.html` (52 findings, grade B+).
**Goal:** Implement the fixes below to reach A+. Antigravity writes ALL code; Claude verifies each phase against the live system and pushes after TUM Confirm.
**Note:** This is a SEPARATE file from `HANDOFF.md` (that one is the backup-tool doc — leave it intact).

> Spans BOTH repos. **[backend]** = `C:\Users\rapee\vexonhq-ocr-api`, **[frontend]** = `C:\Users\rapee\VEXONHQ`.

## Pre-fix safety net (DONE by Claude)
A full verified backup was taken before any changes: `backups/mara-backup-20260602_171525` — 83 tables / 56,798 rows + 276 storage files / 268.7 MB; CSV row counts spot-checked against the manifest (pos_bills/vendor_bills/bank_statement_entries all match). It is LOCAL ONLY — getting it off-host is P0-2.

## Ground rules for every change
- Reversible only; state the one-line rollback for each change.
- Verify column names against `information_schema.columns` before any SQL.
- No secrets in code/commits. No emojis in code/commits.
- Before handing back: **[backend]** `.\verify.ps1` + `ast.parse` touched files; **[frontend]** `npm run lint && npx tsc --noEmit && npm run build` — all green.
- One small commit per item; do NOT batch P0 with declutter. Hand each diff to Claude for live verification; Claude pushes after TUM Confirm + backup tag.

---

## P0 — CRITICAL (do first)

### P0-1 — [backend] Lock down `/ai/exec` (SEC-1 / AI-1)
**Files:** `main.py:313` (PUBLIC_PATHS) · `ai_exec_routes.py:90-132`
**Why:** Public-path (JWT-bypassed) shell executor; secret compared with `!=`; `shell=True`; secret shared with ai.marastation.com. Live-confirmed reachable (401 without key).
**Changes:**
1. Remove `"/ai/exec"` from `PUBLIC_PATHS`.
2. Require an **admin JWT** in addition to `X-AI-Exec-Key` (reuse `auth_routes._require_admin_role`). If the ai.marastation.com chat client can't send a JWT yet, instead restrict by Cloudflare service-token / source-IP allowlist — not open to the whole internet.
3. `api_key != expected` → `secrets.compare_digest(api_key, expected)` (keep empty-secret fail-closed).
4. Remove `shell=True`. Build the docker restart without a shell pipe: `docker ps -q --filter name=<uuid>` (argv) → capture IDs → `docker restart <ids...>` (argv).
5. Fix the timeout message `"10s"` → `"30s"` (line 132) (SEC-6).
**Acceptance:** `/ai/exec` → 401 without admin JWT; whitelisted cmds work with JWT+key; `grep shell=True ai_exec_routes.py` → none; ai.marastation.com chat still works.
**Rollback:** revert the file / re-add to PUBLIC_PATHS.
**Coordinate the ai.marastation.com chat app's auth BEFORE deploying — the endpoint is shared.**

### P0-2 — [backend/ops] Automated, off-host, monitored DR backup (OPS-1, +OPS-5/7)
**Why:** Supabase free has no auto-backup; the tool is manual + local-only + unmonitored. DO snapshots do NOT cover the DB.
**Changes:**
1. **Schedule (OPS-1):** add a nightly job (APScheduler in `main.py`, or a Coolify scheduled task) that runs the existing `scripts/backup.py`. Use `--db-only` for the daily run (fast; the storage rarely changes) + a weekly full run.
2. **Off-host (OPS-1):** after a successful run, upload the archive to a SEPARATE destination — the currently-INACTIVE 2nd Supabase project `vexonhq-upload`, or S3/Backblaze. Never the same VPS. Keep last ~7, prune older.
3. **Monitor (OPS-7):** wrap with `@_heartbeat("nightly_db_backup", expected_interval_hours=24)` so `/cron/health` tracks it.
4. **Verify (OPS-5):** after the run, assert each CSV line count == manifest `row_count`; fail loudly if a table the manifest says is non-empty came back empty. (Claude already demonstrated this check passes.)
**Acceptance:** `/cron/health` shows `nightly_db_backup` `run_count≥1`; an off-host copy exists; CSV/manifest verify passes.
**Rollback:** disable the scheduled job; manual run still works.

> **CORRECTION to audit OPS-2 (do NOT "remove the 6543 rewrite"):** `HANDOFF.md` documents — and the live run confirms — that the `:5432`→`:6543` rewrite (`backup.py:84-86`) is **intentional and correct**: it routes COPY through the Transaction Pooler with `autocommit=True`+retry to avoid the session-pooler 15-client `max clients reached` saturation. The COPY export works on 6543 (verified). **Keep the rewrite.** Residual (optional, lower priority): the CSV path is schema-less (OPS-4) — to also capture DDL, run `pg_dump -Fc` against the **DIRECT** db host `db.osneubnwghvbwyazaedo.supabase.co:5432` (a brief direct session, NOT the pooler — bypasses the 15-client limit) and add `postgresql-client` to the Dockerfile only if you run the dump in-container. Schema is otherwise recoverable from `migrations/` in git.

---

## P1 — HIGH

- **P1-1 [backend] OCR confirm-gating (OCR-1)** — `main.py` `invoice_confirm` (~1507): re-run `_revalidate_bill`; refuse if any warning severity=`error` unless `force=true`. *Accept:* confirming a `MISSING_TOTAL` bill → 422 unless force.
- **P1-2 [backend] Cross-vendor merge guard (OCR-2)** — `main.py:2210-2224`: the `invoice_no`-only fallback must also match amount(±tol) or `bill_date`, or be same-vendor; skip when `invoice_no` is short/numeric-only. *Accept:* two different-vendor `invoice_no="1"` don't merge.
- **P1-3 [backend] Vision cost caps (OCR-3)** — `/invoice/upload` body-size cap (mirror 20MB statement cap); page cap in `_pdf_to_images`; `timeout=` on the vision call. *Accept:* oversized upload → 413; many-page PDF rejected.
- **P1-4 [backend] Robust anomaly method (AI-2)** — `phase3a_anomaly_routes.py:49`: `MIN_SAMPLE_FOR_BASELINE`≥8; mean/stddev z-score → median+IQR (or MAD); surface `usable_baselines` to UI/health. *Accept:* `/health` reports usable baselines.
- **P1-5 [frontend] Merge budget pages (FE-1)** — keep `/budgets`, port AI-suggest+LINE from `/budget`, delete `/budget`, remove dead `/settings` link (`app/budgets/page.tsx:489`), update Navbar. *Accept:* one budget page, no dead link.
- **P1-6 [frontend] Single route manifest (FE-2)** — generate Navbar groups AND `admin/page-permissions` `ALL_PAGES` from ONE manifest. *Accept:* toggling a permission affects every nav-visible page.
- **P1-7 [frontend] POS declutter (FE-3)** — CUT `pos/tables, pos/staff, pos/shifts, pos/combos, pos/discounts, pos/voids`; MERGE `dow+hourly+heatmap`→"Sales Timing", `payments`→`channels`, `items`→`menu`, `prices`→`supplier/price-trend`, `revenue`→dashboard, `scorecard`→dashboard, `pnl/compare`→`pnl`. *Accept:* ~18 core pages + admin; build green.
- **P1-8 [backend/ops] cron misfire/coalesce (OPS-3)** — all `add_job(...)` in `line_bot_routes.py`: add `misfire_grace_time=3600` + `coalesce=True` (or `job_defaults`). *Accept:* a restart during a job window still fires once on recovery.

---

## P2 — MEDIUM (needed for A+)

- **PNL-1 [db]** recreate `v_budget_status` to read `v_daybook_pnl`. **PNL-2 [backend]** align `phase2.budgets_status` + budget reads to `v_daybook_pnl`.
- **FE-4 [frontend]** move `/ai-review` + `/ai-stats` under `/admin/` (edge middleware role gate).
- **FE-5 [frontend]** `app/pos/compare/page.tsx:10` fallback → `?? ''`. **FE-6 [frontend]** `/budget` + `/pos/compare` → `lib/safeFetch.ts` + error banner.
- **SEC-2 [db]** enable RLS (no policies) on all `web.*` tables. **SEC-3 [frontend]** auth token → HttpOnly cookie (`@supabase/ssr`) + strict CSP (larger task).
- **SEC-4 [backend]** Discord: reject if `abs(now - X-Signature-Timestamp) > 300s`.
- **OPS-6 [backend]** add disk% to `/health/deep`. **OPS-8 [ops]** DO scope doc include `image:create`, `DO_SNAPSHOT_MAX_KEEP=2`, document "snapshot ≠ DB backup". **OPS-10 [backend]** offline unit tests: merge totals / slip-reconcile idempotency / invoice validation math. **OPS-12 [backend]** pin `requirements.txt` `==`; align Dockerfile to py3.11.
- **AI-5 [backend]** forecast "insufficient data" for `<2-3` orders. **AI-6 [backend]** log cashflow AI decisions. **AI-8 [backend]** narrative warn-line on failed verification.

---

## P3 — DECLUTTER (low risk, last; one migration where possible)

- **DB:** snapshot then `DROP TABLE` `bank_statement_entries_bak_20260530`, `pos_sales_items_dedup_bak_20260531`, `sales_backup`, `sales_import_raw`; drop duplicate `idx_vb_due_date`/`idx_vb_status`/`idx_vb_vendor`; drop unused indexes + add covering FK indexes (one migration).
- **Dead code:** delete `search_routes.py`; remove EXPERIMENTAL `ocr_schema.py` + `llm.openai_chat_structured`/`call_anthropic_vision` OR promote the strict-schema path (also closes OCR-4); remove unused `MUSICIAN_AMOUNTS`; fix stale "50-item cap" comments; consider gating `/docs`+`/openapi.json` in prod.

---

## Claude's verification per phase (not skipped)
- **[backend]** `.\verify.ps1 -Smoke`, live endpoint probes, `/cron/health`, re-run Supabase advisors (security+performance).
- **[frontend]** `npm run lint && npx tsc --noEmit && npm run build`; load `app.marastation.com` + hard refresh.
- Backup tag before push; push only after TUM Confirm; re-check `/health/deep` settles before reporting done.

**Definition of A+:** P0 + P1 + P2 complete & verified, P3 mostly done, no Critical/High open, prior fixes still holding.
