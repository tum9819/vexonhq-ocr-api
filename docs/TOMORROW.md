# TOMORROW.md — vexonhq-ocr-api backend

**Last updated**: 2026-06-02 (Session 52 — Supabase transaction pooler fix)

> Frontend / cross-repo context → `C:\Users\rapee\VEXONHQ\docs\01_PROJECT\TOMORROW.md`
> Full re-audit detail → `docs/superpowers/audits/2026-05-29-reaudit-batch13-RUNBOOK.md`

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
