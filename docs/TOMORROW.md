# TOMORROW.md — vexonhq-ocr-api backend

**Last updated**: 2026-06-03 (A+ remediation round 3 — ~A → A/A+)

> Frontend / cross-repo context → `C:\Users\rapee\VEXONHQ\docs\01_PROJECT\TOMORROW.md`
> Full re-audit detail → `docs/superpowers/audits/2026-05-29-reaudit-batch13-RUNBOOK.md`

---

## 🟢 2026-06-03 — A+ remediation round 3 shipped (grade ~A → A/A+)

Context: continues round 1 + round 2 (same day). Grade trajectory **B+ (audit) → A- (round 1) → ~A (round 2) → A/A+ (round 3, this session)**. The remaining High/Med engineering items are now fixed; only owner-decision/info items are left. Approach: analyzed all remaining items in parallel, implemented the safe ones, and adversarially verified the money-path changes over **2 rounds** — which caught + fixed a real multi-page-split regression in OCR-2 before it shipped. Every change gated + verified; **39 new offline tests, full suite 204 passed**.

> **Re-grade note:** the analysis re-graded **OCR-1 + OCR-2 as HIGH** (money correctness), not Low. Several audit "easy fixes" were infeasible as written (**PNL-4** has no clean counterparty join key; **OPS-13** may just be an env-var port — do NOT do a 42-file pool refactor for a Low finding).

### ✅ Backend + web items shipped to prod + verified this round (6)
- **OCR-1 (HIGH) — confirm-gating** (`main.py`, commit `722d9fe`). `POST /invoice/{id}/confirm` now refuses a bill carrying an **error-severity** validation warning (e.g. `MISSING_TOTAL`) with **HTTP 422 `CONFIRM_BLOCKED`** unless `ConfirmRequest.force=True`. **Fails OPEN** on infra error; skips the gate + warnings-rewrite when forcing.
- **OCR-2 (HIGH) — cross-vendor invoice merge guard** (`main.py`, commit `722d9fe`). The `invoice_no`-only dedup fallback could fuse **DIFFERENT vendors** sharing an invoice number ("1"/"001"). New `_should_merge_on_invoice_no()`: same vendor **OR** tight non-zero amount match → merge; weak number with neither → split; strong number → trust the match UNLESS amounts both present and differ. ⚠️ A missing/drifted vendor **OR** date is the **multi-page OCR case, never a split signal** (this nuance was caught by adversarial review — the regression it would have caused was fixed before shipping).
- **OPS-11 — cron stale-job watchdog** (`cron_heartbeat.py` + `line_bot_routes.py`, commit `0faf40b`). Extracted `_compute_job_states()` (behavior-preserving — `/cron/health` response unchanged) + `check_and_alert_stale_jobs()` posts the **specific** stale/missing `job_id` to Discord, rate-limited 6h/job; registered as a 30-min APScheduler job wrapped in `@_heartbeat`. Verified live: `/cron/health` shape preserved.
- **OPS-4 (HIGH) — `pg_dump` against pooler** (`scripts/backup.py`, commit `0faf40b`). `pg_dump` cannot run against the `:6543` transaction pooler; it now uses a **direct/session url (`:5432`)** for the dump + stats, while the COPY fallback keeps the `:6543` rewrite (deliberate, **OPS-2**). Enables a real `pg_restore`-able dump once `pg_dump` is on the host PATH.
- **OPS-10 (MED) — money-path unit tests** (`tests/` + `main.py`, commit `94bf46a`). 18 offline money-path unit tests (`test_invoice_validation`, `test_merge_totals`, `test_slip_reconcile`) + a behavior-preserving `_compute_backfill()` extraction (the never-overwrite-a-present-total rule) so merge backfill is testable. `verify.ps1` gained a `[1b]` offline-test step.
- **FE-6 (MED) — `/pos/compare` error state** (web `app/pos/compare/page.tsx`, commit `c6a3680`). The page swallowed backend 500/401 and showed the empty "ไม่มีข้อมูล" state (looked like no data, not an outage). Migrated to `safeFetch` (throws on non-2xx) + explicit error state + rose error banner.

### Rollback (one-command revert)
Rollback tags (2026-06-03): `backup-pre-ocr12`, `backup-pre-batchB`, `backup-pre-fe6`, `backup-pre-ops10`. Commits: backend `722d9fe` (OCR-1/OCR-2), `0faf40b` (OPS-11/OPS-4), `94bf46a` (OPS-10); web `c6a3680` (FE-6).

### 👉 STILL OPEN — all need owner decision / info (no engineering items left)
**🔵 DECIDE (Claude can implement once TUM picks the approach):**
1. **AI-6 — cashflow AI decision log.** Decide: extend `ai_categorization_log` vs a separate table; and whether to gate low-confidence entries to review.
2. **OPS-13 — DB connection pool / prod port.** Confirm prod `DATABASE_URL` port `:5432` vs `:6543` — **likely env-only**; do NOT do the 42-file pool refactor for a Low finding.
3. **PNL-4 — tax-id prefill.** Clean counterparty join is **infeasible** — choose: best-effort exact-name prefill vs schema + UI vs keep-blank + note.
4. **SEC-3 — auth token → HttpOnly cookie.** Cross-repo: needs a same-origin proxy in **BOTH** VEXONHQ + marastation-web before flipping the shared SSO cookie. **Ship CSP first.**
5. **FE-3 — POS declutter (25 → ~18 pages).** Approve the KEEP/CUT map first — **deletes are irreversible.**
6. **FE-2 — route-manifest single-source.** Do **AFTER** FE-3.

**🔴 INFO NEEDED (BLOCKED on TUM — do NOT guess):**
7. **SEC-1b — `/ai/exec` lockdown.** How does ai.marastation.com call `/ai/exec` — JWT? fixed IP? (needed before it comes off `PUBLIC_PATHS`).
8. **OPS-12 — pin `requirements.txt`.** Paste the container's `pip freeze` to pin deps, or skip.
9. **PNL-3 — WHT gross vs net.** Tax ambiguity — needs a real invoice / the accountant.

> **Supabase ops watch:** ticket **SU-387973** — orphaned storage objects (12.7 GB billed vs ~268 MB physical); grace extension to **04 Jun 2026**. Local + remote watchdogs armed for a 402.

---

## 🟢 2026-06-03 — A+ remediation round 2 shipped (grade A- → ~A)

Context: continues round 1 (same day). Grade trajectory **B+ (audit) → A- (round 1, 9 fixes) → ~A (round 2, this session)**. Backup taken FIRST: Supabase logical backup `mara-backup-20260603_005144` (83 tables / 56,842 rows / 276 storage files), verified. Every change gated (backend compileall + pytest 165 passed / 23 skipped + live smoke via pre-push hook; web lint 0-err + tsc + npm build) and verified live.

### ✅ Backend + DB items shipped to prod + verified this round (10)
- **SEC-4 — Discord anti-replay** (`discord_routes.py`). After the Ed25519 verify, added an interaction-timestamp freshness check (reject if `|now-ts| > 300s`). Commit `56b345b`.
- **OPS-6 — disk telemetry** (`main.py`). `/health/deep` now reports `disk_pct` + `disk_warn` (≥80%). Verified live `disk_pct=29.2`. Commit `56b345b`.
- **OPS-8 — snapshot keep-floor** (`do_snapshot_routes.py`). `DO_SNAPSHOT_MAX_KEEP` default 1→2 (a failed create never leaves zero); doc scope `image:create`; note "DO snapshot backs up the APP SERVER, not the Supabase DB". Commit `56b345b`.
- **AI-5 — forecast honesty** (`inventory_forecast_routes.py`). When `order_count < 2` → `insufficient_data=true`, urgency `"unknown"`, `next_order_est`/`days_until_order` = None (no guessing); None-safe sort. Commit `56b345b`.
- **dup-index drop** (DB migration `g1_drop_duplicate_vendor_bills_indexes`). Dropped `idx_vb_due_date`/`idx_vb_status`/`idx_vb_vendor` (duplicates of canonical `idx_vendor_bills_*`). Verified gone.
- **PNL-2 — budget actuals = dashboard** (`phase2_routes.py`). `/budgets/status` per-category `spent` now from `v_daybook_pnl` (was vendor_bills-only) → matches dashboard `top_categories`/`food_cost`; includes cash/manual + bank-statement expenses, excludes equity. Commit `1d1328e`.
- **OPS-9 — verify-before-delete** (`do_snapshot_routes.py`). `rotate_auto_snapshots` now verifies the DO create action was accepted (status `in-progress`/`completed`) BEFORE deleting any old snapshot — a failed create skips deletion so backup count never drops. Commit `1d1328e`.
- **table-drops** (DB migration `g2_drop_3_dead_backup_tables`). Dropped `sales_backup`, `bank_statement_entries_bak_20260530`, `pos_sales_items_dedup_bak_20260531`. **KEPT `sales_import_raw`** — it feeds `v_sales_unified → v_sales_clean → v_sales_forecast_base → v_sales_next7` (audit misclassified it as a backup).
- **SEC-2 — RLS on `web.*`** (DB migration `g2_sec2_enable_rls_web_schema`). Enabled RLS on all 20 `web.*` tables (defense-in-depth). Verified marastation.com still SSRs data identically (homepage 212017B unchanged, `/menu` `/events` unchanged) → proves marastation-web connects via a BYPASSRLS role. Reversible (DISABLE).
- **test fix** (`tests/test_discord_interactions.py`). `_sign_body` now defaults to the current timestamp (SEC-4 made the old static `"0"` stale). pytest: **165 passed, 23 skipped**.
- (Frontend, for cross-ref) **FE-4** admin-only role-gate on `/ai-review` (`app/ai-review/page.tsx`) mirroring `/ai-stats`. VEXONHQ commit `db5b6b4`, deploy settled, app 307.

### Audit findings corrected this round (do NOT re-flag)
- **OPS-2** downgraded High → Low: `backup.py` rewriting host port 5432→6543 is **INTENTIONAL** (avoids the Supabase pooler "max clients reached"). Keep the rewrite.
- **`sales_import_raw` is NOT a dead backup table** — it is a live source for the sales-forecast view chain. Not dropped.

### Rollback (one-command revert)
Tags (2026-06-03): backend `backup-pre-g1batch1`, `backup-pre-g1batch2` (plus earlier `backup-pre-aiexec`/`backendbatch2`/`dr-tooling`/`docs`); web `backup-pre-fe4`, `backup-pre-fe`, `backup-pre-docs`. Plus the round-2 data backup `mara-backup-20260603_005144`. Commits: backend `56b345b` (SEC-4/OPS-6/OPS-8/AI-5), `1d1328e` (PNL-2/OPS-9); web `db5b6b4` (FE-4). DB migrations applied via Supabase MCP.

### 👉 STILL OPEN — path to full A+
**🔴 BLOCKED on TUM input (do NOT proceed without his decision):**
1. **SEC-1b — `/ai/exec` residual Critical lockdown.** Needs the ai.marastation.com chat-app auth mechanism (JWT? fixed IP?) before it can come off `PUBLIC_PATHS`.
2. **OPS-12 — pin `requirements.txt` versions.** Needs the container's `pip freeze` — no SSH/exec key available to Claude.
3. **PNL-3 — WHT gross-vs-net.** Tax ambiguity, both unsure; needs a real invoice / the accountant. **DO NOT guess.**

**🟡 INVOLVED — Claude can do on TUM's go:**
4. **OPS-11 — cron stale-job alert.**
5. **AI-6 — cashflow AI decision log.**
6. **OPS-13 — DB connection pool.** Riskiest of this group — do carefully.
7. **PNL-4 — tax-id prefill.**

**🟢 GROUP 3 — larger / batchable:**
8. **OCR-1 / OCR-2** (+ unit tests).
9. **SEC-3 — auth token → HttpOnly cookie.**
10. **OPS-4** (add `postgresql-client`/`pg_dump` to the image) and **OPS-10**.
11. (Frontend, cross-ref) **FE-3** page declutter / show-plan-first, **FE-2** route manifest; **FE-6** (`safeFetch` on `/budget` + `/pos/compare`) deferred.

> **Supabase ops watch:** ticket **SU-387973** — storage quota shows 12.7 GB billed vs 268 MB physical (orphaned objects at the raw storage node, invisible to the Storage API); grace extension to **04 Jun 2026**. Local + remote watchdogs armed for a 402.

---

## 🟢 2026-06-03 — A+ remediation round 1 shipped (grade B+ → A-)

Context: independent full-system audit (2026-06-02, 6 parallel auditors + live read-only prod tests + Supabase advisors) returned 52 findings (2 Critical, 9 High, 19 Medium, 18 Low, 4 Info), overall grade **B+**. No active data leak found; all Session-47/49/50 fixes still hold; P&L/accounting core trustworthy. Report: `VEXONHQ_System_Audit_2026-06-02.html`. (Audit OPS-2 was a self-corrected false positive — the backup.py :5432→:6543 pooler rewrite is INTENTIONAL/correct; downgraded to Low.)

This round was implemented + verified + pushed by Claude Code under a TUM-approved relaxation **scoped to the A+ plan only** (the firm "Antigravity writes all code" rule still applies to every other task). Smoke 70/70, deploy settled each time, independent adversarial re-review = 0 regressions, live-tested. Grade moved **B+ → A-**.

### ✅ Backend items shipped to prod + verified this round
- **SEC-1/AI-1 (Critical) — `/ai/exec` hardened.** `secrets.compare_digest` (constant-time key check) + removed `shell=True` (whitelisted cmds run as argv lists; docker-restart resolved in Python, no shell pipe) + timeout message 10s→30s. Kept on `PUBLIC_PATHS` so ai.marastation.com chat keeps working. Commit `6e98a57`. Verified live: bad-key → HTTP 401.
- **OPS-1/DR (Critical) — backup + DR tooling.** Full pre-fix backup taken + verified (83 tables / 56,798 rows + 276 storage files / 268.7 MB; CSV row counts == manifest). DR tooling committed incl. off-host → Google Drive wrapper (`mara_backup_to_gdrive.ps1`, db daily / full weekly). Commit `d6d7832`. **Pending TUM:** add a Windows Task Scheduler entry to run it automatically.
- **AI-2 (High) — anomaly detection.** Replaced mean/stddev z-score with robust median(p50)+percentile-spread; `MIN_SAMPLE_FOR_BASELINE` 3→8. Commit `bd36076`. Advisory-only (no financial-number impact).
- **OPS-3 (High) — cron resilience.** `BackgroundScheduler` `job_defaults` `misfire_grace_time=3600` + `coalesce=True` (a missed run fires on recovery instead of being dropped). Commit `bd36076`. Verified: 9 cron jobs healthy.
- **OCR-3 (High) — Vision API cost cap.** 25 MB upload cap + 40-page PDF cap (bounds GPT-4o credit-burn). Commit `bd36076`.
- **AI-8 (Info) — P&L narrative warning.** Append an in-message warning to the LINE digest when a baht figure fails verification (was log-only). Commit `bd36076`.
- **PNL-1 (Medium) — budget actuals match P&L.** `v_budget_status` repointed FROM `v_daybook` → `v_daybook_pnl` so budget "actual spend" matches the cash-basis P&L/exports. Applied as a Supabase DB migration (`apply_migration`). Verified: view returns valid rows. Reversible.
- (Frontend, for cross-ref) FE-5 `/pos/compare` stale API fallback `'https://api.vexonhq.com'` → `''` and FE-1 `/budgets` EmptyState dead link `/settings` → `/categories` — VEXONHQ commit `1f727da`, lint/tsc/build green, zero-downtime deploy.

### Rollback (one-command revert)
Tags: `backup-pre-dr-tooling-2026-06-03`, `backup-pre-aiexec-2026-06-03`, `backup-pre-backendbatch2-2026-06-03` (vexonhq-ocr-api); `backup-pre-fe-2026-06-03` (VEXONHQ). Plus data backup `backups/mara-backup-20260602_171525`. Remediation report: `VEXONHQ_Remediation_Report_2026-06-03.html`.

### 👉 NEXT priorities — DEFERRED BACKEND A+ items (historical — superseded by round 2 above)
All specced in **`HANDOFF_AUDIT_FIXES.md`**. Status as of round 2 (current STILL-OPEN list lives in the round-2 section above):
1. **SEC-1b — `/ai/exec` lockdown.** STILL OPEN, BLOCKED on TUM (chat-app auth mechanism).
2. **OCR-1 — confirm-gating.** STILL OPEN (Group 3, + unit tests).
3. **OCR-2 — cross-vendor invoice merge guard.** STILL OPEN (Group 3, + unit tests).
4. **SEC-2 — enable RLS on `web.*` schema.** ✅ DONE round 2 (`g2_sec2_enable_rls_web_schema`, 20 tables; marastation.com verified unaffected).
5. **SEC-3 — auth token → HttpOnly cookie.** STILL OPEN (Group 3).
6. **OPS-12 — pin `requirements.txt` versions.** STILL OPEN, BLOCKED on TUM (needs container `pip freeze`).
7. **OPS-4 — add `postgresql-client`/`pg_dump` to the image.** STILL OPEN (Group 3).
8. **DB drops** — ✅ DONE round 2: 3 dead backup tables dropped (`g2_drop_3_dead_backup_tables`; `sales_import_raw` KEPT — live forecast source) + 3 duplicate `vendor_bills` indexes dropped (`g1_drop_duplicate_vendor_bills_indexes`).

---

## 🟢 Session 52 (2026-06-02) — Supabase transaction pooler fix (:6543 + autocommit + retry)

### ✅ Applied/Fixed in code this session (awaiting TUM push + Coolify deploy)
- Fixed the database connection in `scripts/backup.py` to route through port `6543` (Transaction Pooler) instead of `5432` to avoid pool saturation.
- Added `autocommit=True` to the psycopg2 connection options immediately upon initialization to satisfy PgBouncer transaction mode for COPY streams.
- Implemented a resilient connection retry helper `connect_with_retry(url)` supporting up to 3 attempts on `psycopg2.OperationalError` with a 5-second sleep in between.
- Verified the fix end-to-end with a live dry-run (`python scripts/backup.py --skip-storage`), successfully backing up all 83 base tables (56,793 rows) cleanly.

---

## 🟢 Session 51 (2026-06-02) — Added --skip-storage / --db-only flag to backup tool

### ✅ Applied/Fixed in code this session (awaiting TUM push + Coolify deploy)
- Added command-line argument `--skip-storage` (and its alias `--db-only`) to `scripts/backup.py` using Python's `argparse`.
- Modified environment variable validation to conditionally check S3 storage configuration (`SUPABASE_URL`, `SUPABASE_S3_ACCESS_KEY_ID`, `SUPABASE_S3_SECRET_ACCESS_KEY`) only when `--skip-storage` is NOT set.
- Bypassed the `perform_storage_backup` step when `--skip-storage` is set, returning `num_files = 0` and `total_bytes = 0` for logs/summary prints.
- Updated `manifest.json` generation inside `perform_db_backup` to accept `skip_storage` argument and record `"storage_skipped": true` and `"storage_files": 0` when set.
- Added automatic update of `manifest.json` at the end of the full backup to include real S3 stats (`"storage_skipped": false`, `"storage_files": num_files`, `"storage_bytes": total_bytes`).
- Verified code syntax check using `ast.parse` and repository compile-check via `.\verify.ps1`. Both passed cleanly.

### 👉 NEXT for TUM / Daily Cron
- Configure the daily cron backup to pass `--skip-storage` (or `--db-only`) to perform lightweight database-only backups.
- Keep the weekly backup running in full mode (without `--skip-storage`) to continue backing up all Supabase storage buckets.

---

## 🟢 Session 50 (2026-05-31) — Loan tracking (เงินยืม) Phase 1

Trigger: co-owner นุศรา lent the shop money (slip 33,000 / memo "ยืม 33,000 คืนแล้ว 15,000" → ค้าง 18,000). A loan is financing, NOT P&L — both legs must be excluded. Spec + plan: `docs/superpowers/{specs,plans}/2026-05-31-loan-tracking*`. See AGENTS.md #36.

### ✅ Applied LIVE to prod Supabase this session (additive views, NOT via git push)
- `v_daybook_pnl` extended to exclude `loan_in` + `loan_repayment` (`migrations/2026_05_31_loan_sources_pnl_exclude.sql`). A/B proof: Apr totals unchanged with loan rows present.
- New `v_loan_balance` view — per-lender borrowed/repaid/outstanding, grouped by `bank_statement_entries.notes` (`migrations/2026_05_31_v_loan_balance.sql`). Verified: outstanding=18,000 on the fixture.

### ✅ FIXED in code this session (awaiting TUM push + Coolify deploy)
- `loan_routes.py` (new) — `GET /loans` + `GET /loans/{lender}` (JWT-gated); registered in `main.py`.
- `POST /classify/{entry_id}` now takes optional `lender` → written to `notes` (COALESCE; already sets `match_status='manual'`).
- Loan exclusion spliced into inline `source NOT IN` lists: `pnl_routes.py` (5), `cashflow_routes.py` (3), `line_bot_routes.py` (2), `phase2_routes.py` (2), `phase10_narrative_routes.py` macro.
- `tests/test_smoke.py` probes `/loans`. `verify.ps1` compileall ✅. Final review: APPROVED_WITH_NITS (no blockers).

### 👉 NEXT for TUM
1. Push the code (paste block provided) → Coolify deploy → `verify.ps1 -Smoke` once VPS CPU < 30%.
2. Tag this slip: on `/bank-statement` set the นุศรา money-in row to `loan_in` with lender "นุศรา"; tag the repayment-out rows as `loan_repayment` (lender "นุศรา") — only the ones whose slip memo says คืนยืม (NOT the ค่าเนื้อ reimbursements).
3. Dashboard loan card = follow-up in the `VEXONHQ` frontend repo (consumes `GET /loans`).

### ✅ Phase 2 SHIPPED in code (awaiting push) — repayment auto-tag
Seeded `statement_rules` `คืนยืม`/`คืนเงินยืม` → `loan_repayment` (applied live); `slip_routes.py` maps it + writes normalized lender to `notes` in the nightly reconcile (`_normalize_lender`). Verified live: keyword resolves, simulated reconcile → `v_loan_balance` nets under "นุศรา", A/B P&L unchanged. Borrow (incoming) stays manual (slip pipeline is expense-only). Operator how-to: `docs/HOWTO_loans.md`. Spec/plan: `docs/superpowers/{specs,plans}/2026-05-31-loan-autotag-phase2*`. **NEXT for TUM:** after push+deploy, forward a repayment slip with memo "คืนยืม" (or `POST /slip/reconcile`) → confirm the row turns `loan_repayment` on `/bank-statement` + shows on `/loans`.

---

## 🔴 Session 49 (2026-05-31) — full-system audit results

**Baseline before audit:** compileall ✅, unit 88 ✅, live smoke 64/64 ✅ (system was running fine; this was a latent-bug hunt). 9-dimension workflow → **19 findings, all verified against live prod DB (0 false-positive)** + completeness critic found 2 platform-layer breaches the code audit missed.

### ✅ FIXED LIVE this session (already applied to prod Supabase — NOT via git push)
- **GAP 1 — RLS breach (was an ACTIVE unauthenticated financial-data leak).** 57/59 public tables had RLS off → the public anon key (shipped in the frontend bundle) could `GET /rest/v1/pos_bills|bank_statement_entries|vendor_bills|counterparties` directly, bypassing the backend. **Enabled RLS on all 57** (`migrations/2026_05_31_enable_rls_all_public_tables.sql`). Backend uses service_role (BYPASSRLS) → unaffected (smoke 64/64 after). anon REST now returns `[]`. Reversible per-table.
- **GAP 2 — `uploads` bucket (240 slips/statements/invoices) was world-listable + anon-uploadable.** Dropped the 4 over-permissive storage.objects policies (`migrations/2026_05_31_lock_uploads_bucket_policies.sql`). Enumeration + anon-upload now denied; public-URL download (dashboard) still works.

### ✅ FIXED in code this session (awaiting TUM push)
URGENT: #7 LINE webhook signature bypass (missing-header short-circuit → anon Claude/OpenAI credit burn + LINE spam) → fail-closed + `hmac.compare_digest`. (Verified prod has `LINE_CHANNEL_SECRET` set, so safe.)
NEXT: #2 weekly LINE digest joined non-existent `public.categories` (digest never sent) → `expense_categories`/`name_th` (validated live); #3 bank-statement upload froze the loop → `asyncio.to_thread`; #6 recipe AI-link `json.loads` not shape-checked → list-guard; #8 `/ap/due-reminder`+`/stock/alert` removed from PUBLIC_PATHS (were anon, leaked AP data / LINE-spam); #11 4 digest crons swallowed exceptions (false-healthy /cron/health) → re-`raise`; #12 POS sync import aborted-txn no rollback (stuck status='parsing') → rollback first; #14 POS line-item re-import double-counts (no dedup) → delete-by-bill before insert.
MONITOR (one-liners): #9 5× secret `!=` → `secrets.compare_digest`; #10 global 500 handler stopped echoing `str(exc)` (leaked DB host); #13 marked dead `search_routes.py`; #15 `vps_health_monitor` now `@_heartbeat`; #16 KBank guard synced to verify script; #17/#18/#19 `/pos/calendar`+`/goals`+`/compare` 422 instead of 500 on bad date input.

### ✅ Session 49b (2026-05-31) — completed the 3 deferred follow-ups (commit `76f0d4e` + live)
1. **#1 ภ.ง.ด.3 / WHT — DONE (unified).** All 3 generators (`/tax/wht-summary`, `/export/pnd3`, `/export/pnd3-annual`) now read the single `tax_routes.WHT_RULES` dict + apply per-category rate. The `rent` 5% row (8,000 baht Apr-2026 → 400 WHT) that the Excel exports were DROPPING is now included (verified live). yearly switched raw `v_daybook` → `v_daybook_pnl`; dropped the dead `amount IN (600,700,2100,2800)` heuristic. **musician_fee kept at 3% (40(8))** for consistency with what's been withheld/filed since Nov-2025. ⚠️ **ONE open accountant question:** the strict reading of ท.ป.4/2528 ข้อ 9 treats live musicians as นักแสดงสาธารณะ (resident) = **5%**. Not auto-changed (would restate filed periods). If the accountant confirms 5%, it's a 1-line edit: `WHT_RULES["musician_fee"]["wht_pct"] = 5.0`.
2. **#14 + #12 — DONE (cleaned live).** `pos_sales_items` deduped **39,577 → 35,266** (removed 4,311 older-import dup rows, kept latest per bill_id,line_no, 0 dups remain). Backup table `pos_sales_items_dedup_bak_20260531` (4,311 rows) — drop when confident. Stuck `pos_imports` row backfilled to status='failed'. **Found + fixed a compounding bug:** the error-marking UPDATE used `status='error'` which `chk_pos_import_status` REJECTS (only pending/parsing/success/failed) — so it always failed silently even with the rollback fix → corrected to `'failed'` in both pos_import paths.
3. **GAP 2 full — DONE (private + signed URLs).** `uploads` bucket flipped to **private** (public URL now 400). New `main._sign_uploads_url()` signs stored public URLs at read time across invoice list/detail, slip list/detail, and upload previews (signature authorizes the GET — `<img>` can't send JWT). 24h expiry. **TUM spot-check pending:** open one invoice detail + one slip detail in the dashboard — images should load (signed). If any broken → revert with `UPDATE storage.buckets SET public=true WHERE id='uploads';` (1 cmd) and tell Claude the endpoint.

### 🟡 Remaining (correctly low value — MONITOR, left as-is)
- #4 dormant Telegram webhook urllib block, #5 Discord `/vex resources` 100ms psutil block.

### Critic residual platform risks (TUM to accept or schedule)
26× `security_definer_view` (incl. `v_daybook_pnl` — DON'T flip to invoker while RLS is on, or P&L reads empty), 8× `function_search_path_mutable`, `auth_leaked_password_protection` off. All low priority on a single-tenant service-role DB.

---

## What's live + stable
- **Backend** `https://api.marastation.com` — FastAPI, Coolify auto-deploy ✅
- **P&L = CASH / bank-statement basis** — `vendor_bill` excluded from `v_daybook` (Branch 8 removed). AR sign bug fixed. Owner/inter-entity credits excluded from revenue. ✅
- **Session-47 audit FIXED a critical leak**: ~1.53M of categorised bank expense (beer/salary/food/utility) was tagged `source_type='bank_statement'` (excluded) → dashboard showed an impossible ~66% margin. Reclassified to counted sources by category; Nussara reimbursements counted; statement_rules + the food rules no longer emit `bank_statement` for expenses. **Real per-month margin now ~ -6%..+35% (avg ~15%).** ✅
- **Session-47 audit FIXED the accountant EXPORTS**: `export_routes` (daybook/category/pnd3/summary), `menu_routes` (/revenue, /scorecard KPI#5/#6/#8), `tax_routes` (WHT — was empty every month) all read RAW `v_daybook` → repointed to `v_daybook_pnl`. pnd3 payer = ร้านสถานีหม่าล่า, musician WHT = มาตรา 40(8). Deployed + verified (Apr daybook export reconciles to dashboard +11,440). ✅
- **Session-47b SPLIT COGS** into ต้นทุนอาหาร (food, ~13-19% stable) vs ต้นทุนเครื่องดื่ม (beverage, 7-33% lumpy). food-cost% now sums by `food_cost`/`beverage_cost` SUBTREE (robust — `food_raw` + future codes auto-counted; was a hard-coded list missing `beverage_raw` 625k). Dashboard shows two cards; /scorecard has both KPIs. Deployed `fb4c4d3`/`8942f76`. ✅
- **Bank statements Jun 2025 – May 2026 reconciled 12/12 ZERO DRIFT** vs each statement's own `รวมฝาก/รวมถอน` checksum (line-based parser rewrite + balance dedup key). ✅
- **Slip-driven classification** — nightly `nightly_slip_reconcile` (02:00 BKK) pushes K+ slip memos → bank-row categories; manual `POST /slip/reconcile`; self-heals after re-import. ✅
- **food-cost% ~15%** (cash COGS categorised; rises toward ~30% as bank supplier purchases categorise via slips). Cash musician fees (76k) now feed ภ.ง.ด.3. ✅
- Tests / Uptime Robot / AI auto-diagnose / DO snapshot — unchanged from Session 42 ✅

---

## Next session

### A. [HIGH] After this push deploys — verify + run slip reconcile
1. Wait for Coolify (CPU<30%), then `GET /export/daybook?month=2026-04` must show profit +162k (not the old -675 loss).
2. The deploy registers `nightly_slip_reconcile` (auditor found it had never run). Run `POST /slip/reconcile` once to categorise the 26 waiting slips (rent 8k, salary 34k, beer ~28k → their own lines instead of the `other_expense` catch-all). Confirm `/cron/health` shows the job after 02:00 BKK 2026-05-31 (scheduled routine checks this at 02:30).

### B. [MED] B5 — ภ.ง.ด.3 / WHT (mostly resolved Session 47)
RESOLVED: all 3 generators (`/export/pnd3`, `/export/pnd3-annual`, `/tax/wht-export`) now agree — musician WHT = มาตรา 40(8) เงินได้อื่น @ 3%, payer = ร้านสถานีหม่าล่า (255/4 ถ.พุทธมณฑลสาย 2 เขตทวีวัฒนา). `/tax/wht-export` reads `v_daybook_pnl` (was reading bank_statement_entries → empty every month). REMAINING with accountant: confirm 3% is correct for live-music performers, and the per-payee เลขประจำตัวผู้เสียภาษี is still blank (กรอกเอง before filing).

### B. [MED] Let food-cost complete via slips
Bank supplier purchases (เบียร์/เนื้อ) sit in `other_expense` until a slip memo categorises them. As TUM backfills slips via LINE, the nightly reconcile lifts food-cost% toward ~30%. Seeded memo rules: ค่าเนื้อ→raw_meat, ค่าเหล้า→raw_beverage, etc. (add more in `statement_rules` as memos require).

### C. [MED] B8 / B9 / B13
- B8 Lineman 32.1% commission is a hardcoded estimate (no actual payout column).
- B9 delivery commission never shown as a cost line.
- B13 `/pos/food-cost` recipe-estimate vs FoodStory actual cost reconcile.

### D. [LOW] robustness
- `food_cost` query hardcodes 6 COGS codes — could sum by `parent_code='food_cost'` so any new sub-code counts automatically (would also catch `food_raw`).
- slip reconcile `_CAT_TO_SOURCE` doesn't list raw_meat/raw_veggies/etc. (defaults to `other_expense` source — harmless, both counted).

---

## Monitoring quick-ref
| URL | Purpose |
|-----|---------|
| `/health/deep` | Postgres + Supabase probe |
| `/cron/health` | Cron job status — confirm `nightly_slip_reconcile` `run_count ≥ 1` after its first 02:00 run |

## After ANY KBank statement (re-)import
```powershell
python scripts/verify_statement_parse.py "<each KBank PDF>"   # must print PASS (zero drift)
```
Then the nightly job (or `POST /slip/reconcile`) re-matches slips + pushes memo categories. A re-import orphans slips (FK SET NULL) but the reconcile self-heals them.

## Backups
- `bank_statement_entries_bak_20260530` (1033 rows) — pre-reimport snapshot, drop when confident.
- Backup tags pushed before each Session-46 commit (`backup-pre-*-2026-05-30`).
