# TOMORROW.md — vexonhq-ocr-api backend

**Last updated**: 2026-07-13 (vendor-bill category guard local; awaiting auditor review and push confirmation)

> Frontend / cross-repo context → `C:\Users\rapee\VEXONHQ\docs\01_PROJECT\TOMORROW.md`
> Full re-audit detail → `docs/superpowers/audits/2026-05-29-reaudit-batch13-RUNBOOK.md`

---

## 🟡 2026-07-13 — Vendor-bill category guard (FA-022)

Local backend changes only, not pushed and not deployed.

Scope:
- `phase3a_ai_categorize_routes.py` keeps the existing cashflow rule behavior but tightens the vendor-bill path.
- Broad/mixed vendor bills (CP Axtra/Makro/Big C/Lotus/Tops/B.B. Superstore/WEALIMEX/7-Eleven/ขายส่ง) skip deterministic rule auto-apply; if LLM is enabled, the suggestion is logged as pending human review instead of writing `vendor_bills.category_code`.
- SINGHA/beer vendor-bill rule results normalize `raw_beverage` to the bill-pipeline code `beverage`.
- `migrations/2026_07_13_dedupe_beverage_label.sql` now records the FA-020 corrected label history: `beverage = เครื่องดื่ม (บิลซื้อจากผู้ขาย)`.

Evidence:
- Read-only Supabase audit found no orphan `vendor_bills.category_code`, no duplicate active labels, and no vendor rules pointing to missing/inactive categories.
- Exact production rule-order simulation found 13 pending confirmed vendor bills / ฿51,644.64 that would otherwise auto-match immediately if `/ai/categorize/batch` ran.

Next:
1. Antigravity reviews the diff as audit-only.
2. If accepted, run full backend verification before asking TUM for push confirmation.
3. Avoid running production `/ai/categorize/batch` on pending vendor bills until this guard is deployed.

## 🟡 2026-07-13 — Optional bill-payment bank evidence link

Local backend commit only, not pushed and not deployed.

Scope:
- `PATCH /bills/payment/{id}` accepts optional `bank_statement_entry_id` for `paid` / `credit_card` updates.
- The selected row is linked by `bank_statement_entries.matched_invoice_id = <bill_id>` in the same DB transaction as the `vendor_bills` payment update.
- Validation rejects non-expense rows and rows already linked to another bill.
- Returning a bill to `unpaid` clears any bank rows linked to that bill.
- `GET /bills/payment/{id}/bank-candidates` lists only unlinked outgoing bank rows with the same amount and `txn_date` in `[bill_date, bill_date + 30 days]`.

Guardrails:
- This is human-confirmed only: no auto-apply, no retroactive backfill, no amount/date heuristic update for old data.
- `export_routes.py` / audit-package code remain untouched and will naturally show evidence only after real `matched_invoice_id` links exist.

Next:
1. Auditor reviews backend + frontend companion diffs.
2. Await TUM's explicit push confirmation; after deploy verify `/bills/payment` and `/health/deep` with settled CPU.

## 🟡 2026-07-12 — FA-008 cashflow AP + FA-006 reorder fixes

Local backend commits only, not pushed and not deployed.

Scope:
- `/cashflow` now uses the standard AP definition from `vendor_bills`: `payment_status='unpaid' AND review_status <> 'rejected'`.
- Overdue unpaid AP is treated as a day-0/first-window cash outflow instead of disappearing from the forecast.
- `/cashflow/summary` returns overdue AP fields and marks health as warning when any AP is overdue.
- `/inventory/reorder` excludes `tag='MENU'`, clamps negative stock to zero for order-quantity calculation, and flags uncategorized default `MAX=300` rows as "MAX ยังไม่ได้ตั้ง — ตรวจสอบ" with no estimated cost.

Verified so far:
- Focused tests: `tests/test_cashflow_ap_standard.py`, `tests/test_inventory_reorder_rules.py`.
- Read-only production-data check: current reorder estimate drops from the audit's ~฿208,565 to ฿112,351; 24 default-MAX rows are flagged/no-cost; MENU examples are excluded.

Next:
1. Run final `ast.parse`, focused/full pytest as applicable, and `verify.ps1`.
2. Auditor reviews backend + frontend companion diffs.
3. Await TUM's explicit push confirmation; after deploy verify `/cashflow`, `/inventory/reorder`, `/health/deep`, and settled VPS CPU.

## 🟡 2026-07-12 — FA-003 deterministic rules + AI auto-apply audit trail

Local backend commits only, not pushed and not deployed.

Scope:
- Part 1: `pos_import.py` classifies TUM-confirmed POS cashflow shorthand `i/v/g` and proven keywords at import time, setting `category_code` plus `ai_cat_status='rule'`. Unknown descriptions remain `pending`.
- Part 1: `migrations/2026_07_12_fa003_slip_beverage_rules.sql` seeds beverage memo keywords in `statement_rules`.
- Part 2: `phase3a_ai_categorize_routes.py` adds confidence-gated auto-apply, additive audit columns, admin undo, and a dry-run-first endpoint for applying already logged pending suggestions.
- Part 2: `/ai-review` frontend companion lives in the VEXONHQ repo and separates Pending vs Auto review queues.

Deploy order:
1. Apply `migrations/2026_07_12_fa003_ai_autoapply_audit.sql` before deploying the backend code that reads the new columns.
2. Push/deploy backend, then frontend.
3. Verify `/health/deep`, `/ai-review` Pending/Auto, and VPS CPU settled.

Guardrails:
- No `v_daybook*`, WAC, amount, or historical row mutation.
- Old backlog is not auto-applied. The pending apply endpoint defaults to `dry_run=true` and should only be run for real after TUM approves the candidate list.

## 🟡 2026-07-12 — T3 daybook P&L basis fix-forward

Auditor production evidence showed the first frontend T3 fix mixed raw income with `net_pnl`, inflating June expense. Backend `/daybook/summary` now returns explicit `income_pnl` and `expense_pnl` from its existing filtered `v_daybook_pnl` scan and preserves the existing `net`/`net_pnl` contract. Frontend consumes the explicit fields directly and uses clearly labeled raw totals only as a legacy fallback.

Regression evidence: June `225,924.63 - 201,929.67 = 23,994.96`; July `80,525 - 17,016 = 63,509`. No DB/data mutation and no deploy yet.

Next: final review, explicit TUM push confirmation, deploy backend before/with frontend, then authenticate and verify `/daybook` for June and July plus `/health/deep` and settled VPS CPU.

## 🟡 2026-07-12 — FA-004 dashboard category reconciliation

Local backend commit adds an appended `ไม่ระบุหมวด` bucket for `category_code IS NULL` spend and calculates each category percentage from `current.expense_total`. The Top 5 categorized query remains unchanged; the uncategorized bucket does not consume its LIMIT. Frontend has a companion commit so the sixth bucket is not sliced out.

Verified: `ast.parse`, focused pytest, and `verify.ps1` (`466 passed, 2 skipped`). No DB/data mutation and no deploy yet.

Next: obtain review + TUM push confirmation, deploy backend before/with the frontend companion, then verify June 2026 `sum(top_categories.spent) == current.expense_total`, `/health/deep`, and settled VPS CPU.

## 🟢 2026-07-11 — Expense category integrity migration applied

Production Supabase migration `upsert_missing_expense_categories_20260711` registered:
- `beverage_raw → beverage_cost`, Thai name `วัตถุดิบเครื่องดื่ม (เบียร์/น้ำ)`.
- `gas → food_cost`, per TUM's explicit cooking-COGS decision.

The migration changes configuration rows only. It does not rewrite historical transactions or change `v_daybook*` views. June `/dashboard/overview` now returns beverage cost `฿35,684.17 / 15.8%` (previously zero) and the Thai category label.

Local backend files awaiting push:
- `migrations/2026_07_11_upsert_missing_categories.sql` — idempotent migration record; omits `sort_order` so new rows use default 999 and reruns preserve operator ordering.
- `tests/test_category_integrity.py` — opt-in read-only integrity test using `CATEGORY_INTEGRITY_DATABASE_URL`; offline suite skips safely.
- `sql/2026_07_11_category_cleanup_review.sql` — SELECT-only owner report for `other_expense`/NULL rows; no automatic reclassification.

Verification:
- Pre-migration explicit integrity test RED: `beverage_raw` only.
- Post-migration explicit integrity test GREEN; remaining non-whitelisted orphan set is empty.
- `verify.ps1`: 464 passed, 2 skipped.
- Production HTTPS June dashboard: HTTP 200, corrected beverage cost and Thai label.

Next:
1. Obtain TUM's separate push confirmation; commit/push the migration record, test, review SQL, frontend catalog changes, and canonical docs only.
2. After deploy, run `/health/deep`, smoke the four category-consuming frontend pages, and wait for shared VPS CPU to settle.
3. Keep historical `other_expense`/NULL cleanup owner-driven and read-only until TUM approves individual classifications.

## 🟢 2026-07-09 — Monthly Close Risk Marking V1 ready

Implementation prepared after TUM request to prevent month-end statement/POS/platform issues from being discovered only after the month has passed.

Shipped design/implementation scope:
- New `monthly_close_routes.py` with admin-only `POST /monthly-close/check` and `GET /monthly-close/risks`.
- New additive table `public.monthly_close_risks` for one row per `(branch_code, month, risk_key)`, with `last_line_sent_at` for 24-hour LINE throttling.
- Existing `/alerts/summary` now includes open monthly-close risks.
- Frontend `/alerts` supports the new `monthly_close` alert type.
- V1 is read-only against source data: it does not reclassify bank rows, lock months, change P&L views, or reconcile payouts.

Production DB migration:
- Applied via Supabase MCP on 2026-07-09 as migration `monthly_close_risks_v1`.
- Verified table exists with 20 columns, RLS enabled, policy count `0`, row count `0`, and expected indexes.

Review notes:
- Antigravity blocker fixed: R4 now uses `COALESCE(source_type, '') NOT IN (...)` so `NULL` source types are not silently excluded.
- R3 keeps `K Plus shop = Grab` as a `danger` rule because TUM confirmed this POS mapping for the current bill-detail export. If the POS meaning changes later, downgrade before enabling LINE for it.
- Migration-first deploy order is required to avoid `/alerts/summary` missing-table noise.

Verification before deploy:
- `pytest tests/test_monthly_close.py -q`: `38 passed`.
- `pytest tests/test_admin_gate.py tests/test_reconcile_routes.py -q`: `10 passed`.
- Backend syntax check for `monthly_close_routes.py`, `main.py`, `menu_routes.py`: OK.
- Frontend `npm run lint`: 0 errors, existing warnings only.
- Frontend `npx tsc --noEmit`: passed.
- Frontend `npm run build`: passed.

Next order:
1. Verify deploy with `/health/deep`, `/alerts`, and CPU settle after push.
2. First live use should be a manual check for the current month only; avoid running historical months unless TUM expects LINE critical risks for old data.

## 🟢 2026-07-07 — Statement parser shipped; June-only historical reclass committed

Implementation **pushed, deployed, and verified**:
- KBank parser now filters page header/footer continuations and cleans KBank page fragments from transaction descriptions while keeping checksum validation unchanged.
- `LINE PAY` / `ไลน์ เพย์` inflows classify as `payment_gateway_payout` instead of LINE MAN delivery income. TUM confirmed these rows can include non-delivery LINE Pay / QR / payment-gateway money.
- Thai Grab descriptions containing `แกร็บ` classify as `grab_payout`, overriding legacy DB rules that mapped them to `rider_income_grab`.
- Added read-only `GET /bank-statement/reclass-dry-run` for admin users. It reports candidate historical rows and month/source totals only; it does **not** update the DB.
- Applied migration `2026_07_06_payment_gateway_payout_pnl_exclude.sql` so `payment_gateway_payout` is excluded from `v_daybook_pnl`.

Verification already run:
- Backend `.\verify.ps1`: `425 passed, 1 skipped`.
- Backend `.\verify.ps1 -Smoke`: `71 passed`.
- Production `/health/deep`: healthy after deploy and CPU settled.

Read-only dry-run against production data found `813` candidate historical rows from `2025-11` to `2026-06`, total credit `1,643,932.51`:
- `payment_gateway_payout`: mostly old `rider_income_lineman` / LINE PAY rows.
- `grab_payout`: old `rider_income_grab` / Thai Grab rows.

June-only production reclass **committed after Claude/Antigravity review and TUM explicit Confirm**:
- SQL draft kept rollback-safe on disk: `docs/superpowers/plans/2026-07-07-june-statement-reclass-draft.sql` still has active `ROLLBACK;` and commented `COMMIT;`.
- Local pre-COMMIT evidence backup: `C:\Users\rapee\Desktop\PJ-MARA\VPS-VEXONHQ\Audit Reports\statement_reclass_commit_20260707_175258\before_bank_statement_entries_102_rows.json`.
- DB rollback backup table: `audit.bank_statement_reclass_backup_20260707_june` (`102` rows / `175,899.24`).
- Updated rows: `102` rows / `175,899.24`; `payment_gateway_payout` 76 rows / `161,719.11`; `grab_payout` 26 rows / `14,180.13`; manual overrides 2 rows (`345.79` on 2026-06-07, `551.31` on 2026-06-30).
- P&L June check unchanged after COMMIT: `v_daybook_pnl` income `225,924.63`, expense `201,929.67`.
- Raw `v_daybook` June income increased from `252,821.73` to `427,823.87` (+`175,002.14`), not +`175,899.24`, because the 2 `pos_cash_deposit` exception rows (`897.10`) were already included in raw income before reclass. This is expected; raw ledger now shows settlement movements for reconciliation.
- Reconcile evidence after COMMIT: June Grab system payout `15,069.03`; June bank `grab_payout` `14,180.13`; remaining `888.90` is the known 2026-07-01 Grab settlement outside the June statement window.

May duplicate cleanup **committed after Claude/Antigravity review and TUM explicit Confirm**:
- SQL draft kept rollback-safe on disk: `docs/superpowers/plans/2026-07-07-may-statement-duplicate-cleanup-draft.sql` still has active `ROLLBACK;` and commented `COMMIT;`.
- Local evidence folder: `C:\Users\rapee\Desktop\PJ-MARA\VPS-VEXONHQ\Audit Reports\statement_duplicate_cleanup_may_commit_20260707_183229\`.
- DB rollback backup tables: `audit.bank_statement_duplicate_cleanup_backup_202605_20260707` (`14` rows: 7 delete + 7 keep) and `audit.statement_duplicate_cleanup_slip_backup_202605_20260707` (`1` slip).
- Verified after COMMIT: delete rows remaining `0`, keep rows `7`, target duplicate keys `0`; slip `5c788be1-50d5-4a9c-81d6-20c61efa81d9` now points to kept statement `89aa5df5-e7b0-4492-8d74-a40b3c8d62d7`.
- May `v_daybook_pnl` expense dropped from `275,926.22` to `275,226.22` (-`700.00`) because one duplicate musician-fee row had been double-counted. Income stayed `325,695.51`.
- Backend health after DB write: `/health/deep` healthy, postgres/supabase OK, CPU `0.0%`.

May statement reclass **committed after Claude/Antigravity review and TUM explicit Confirm**:
- SQL draft kept rollback-safe on disk: `docs/superpowers/plans/2026-07-07-may-statement-reclass-draft.sql` still has active `ROLLBACK;` and commented `COMMIT;`.
- Local evidence folder: `C:\Users\rapee\Desktop\PJ-MARA\VPS-VEXONHQ\Audit Reports\statement_reclass_may_commit_20260707_223428\`.
- DB rollback backup table: `audit.bank_statement_reclass_backup_20260707_may` (`110` rows / `234,703.16`).
- Updated rows: `110` rows / `234,703.16`; `payment_gateway_payout` 84 rows / `221,034.91`; `grab_payout` auto 25 rows / `13,154.93`; manual Grab exception 1 row / `513.32`.
- Manual exception row `a13569a8-7463-4874-b9a9-91d0372f8d67` was justified by May Grab CSV transfer-date aggregate `2026-05-30 = 513.32` / 4 rows despite the bank description lacking a Grab keyword.
- May P&L stayed unchanged after reclass: `v_daybook_pnl` income `325,695.51`, expense `275,226.22`.
- Backend health after DB write: `/health/deep` healthy, postgres/supabase OK, CPU `0.0%`.

Next order:
1. Do not rerun the June reclass, May duplicate-cleanup, or May reclass scripts: the audit backup tables exist by design and a second run should hard-fail.
2. For remaining historical months before May 2026, repeat the same reviewed-ID-only workflow month by month; do not bulk-update old months from keywords.
3. Treat statement `payment_gateway_payout` as cash settlement evidence, not LINE MAN delivery payout; LINE MAN actual settlement still needs better source evidence than the current 32.1% estimate.

## 🟢 2026-06-05 — Sentry removed from backend

TUM asked to remove Sentry and not add more alerts. Backend changes:
- Removed `sentry_sdk` imports and `sentry_sdk.init()` block from `main.py`.
- Removed per-request Sentry user attachment from `JWTAuthMiddleware`.
- Removed `sentry-sdk[fastapi]` from `requirements.txt` and `sentry-sdk` from `requirements-lock.txt`.

Verification: `python -m compileall main.py` passed and `python -m pytest tests/test_smoke.py -q` passed (`70 passed`).

## 🟢 2026-06-04 — OCR-1 (Confirm-Gating for AI Batch Categorization) - DONE

- **OCR-1 (Confirm-Gating) — COMPLETE.** Shipped `dry_run` support for both `/ai/categorize/batch` and `/ai/categorize/cashflow/batch`. When `dry_run=true` is query-passed, the backend runs full rule and LLM categorization but skips DB writes/logs and rolls back any rule hit increments. Returns `"dry_run": true` in response.
- **Reject action null-out — COMPLETE.** Shipped reject null-out in `/ai/categorize/log/{log_id}` to reset `vendor_bills.category_code = NULL` when user rejects an AI suggestion.
- **Added unit tests:** `tests/test_ai_categorize_dryrun.py` tests both dry-run and reject null-out behavior offline with mocks. All tests passed.

## 🟢 2026-06-03 — SEC-3 DONE, GP% costs entered, Supabase FREE, all systems healthy

**SEC-3 (HttpOnly cookie via same-origin proxy) — COMPLETE.** Phase 2+3 shipped on the frontend repos (VEXONHQ + marastation-web). Backend `auth_routes.py` and the Bearer token flow are **unchanged** — the backend was already correct; no ocr-api code change was needed or made.

**GP% / costs — DONE.** Cost data entered for 25 new Wongnai recipes via Supabase SQL (`recipe_ingredients` table).

**Supabase — downgraded back to Free.** Support confirmed actual usage ~270 MB; the SU-387973 storage-quota concern is resolved. No quota overage risk.

**All systems healthy:** `api.marastation.com` 200 ✅ · VEXONHQ app 200 ✅ · `marastation.com` 200 ✅

### 👉 OPEN items (updated as of end-of-day 2026-06-03)
**🔴 TUM data / action:**
1. **OPS-12 — pin `requirements.txt`.** Paste the container's `pip freeze` to pin deps.
2. **Set `AI_EXEC_ALLOWED_IPS` in Coolify** — completes **SEC-1b** (full `/ai/exec` lockdown).

**🟢 Antigravity (HANDOFFs written in the VEXONHQ repo):**
3. **FE-3 — POS declutter** (KEEP/CUT map approved by TUM).
4. **FE-2 — route manifest** (after FE-3).

**Optional later:** reduce the ~590ms DB latency via an in-process connection pool.

---

## 🟢 2026-06-03 — PNL-3 (ภ.ง.ด.3 WHT: gross vs net) RESOLVED — verdict GROSS, NO code change

Closes the **PNL-3** item that had been BLOCKED-on-TUM/accountant across rounds 2-4. **No commit, no push, no migration** — this is a **REVERT** (net-zero code change) plus the live-data evidence that settled the ambiguity. A clean **cross-agent adversarial-review catch**: the firm "Antigravity writes / Claude reviews" rule caught a **wrong tax formula on a government filing before it shipped.**

### What happened
- **Antigravity (headless `agy`) had changed the WHT formula to GROSS-UP the stored amount** — `gross = net / (1 − rate)`, `WHT = net × rate / (1 − rate)` — across `export_routes.py`, `tax_routes.py`, `yearly_routes.py`, **assuming the stored amount was NET-of-withholding, WITHOUT checking the real data.**

### Claude's review (the catch) — live data settled it
- Queried the LIVE `public.v_daybook_pnl`: `musician_fee` = **120 rows, 115/120 are round-to-100 face values** (฿600 ×88, ฿700 ×19, ฿2,100, ฿2,800); `rent` = ฿8,000. **ALL round = GROSS.**
- If they were net-of-3%/5%, the grossed-up values would be **non-round** (e.g. 618.56, 721.65, 8,421) — it is **impossible for 115/120 to be round by coincidence.**

### Verdict + action
- **Amounts are GROSS** → the **ORIGINAL formula (`tax = amount × rate`) is correct.**
- Claude **REVERTED all 3 files** to original. **PNL-3 is CLOSED — no code change shipped.** `HANDOFF.md` already updated with this verdict.
- **No git push for this repo** (revert only = net-zero code change).

### Minor deferred follow-up (low priority)
- `tax_routes.py` field `net_before_wht` is a **pre-existing display-only estimate with confusing naming**; it is **NOT on the official ภ.ง.ด.3 export.** Clean up the name later — not urgent, not money-affecting.

---

## 🟢 2026-06-03 round 4 (ops fixes) — grade A/A+ (real prod DB outage root-caused + fixed)

Context: continues round 1 + 2 + 3 (same day, evening). Driven partly by **live Discord alerts** (`auto_diagnose`). Headline: a **REAL production DB outage** was root-caused + fixed — the backend had lost DB connectivity the previous night (23:24) when the Supabase **session-mode pooler** (`:5432`, 15-client cap) saturated → Discord `postgres_failed` "max clients reached in session mode". Every change gated + verified; **48 new offline tests this round, full suite 211 passed / 23 skipped**. Grade holds **A/A+**.

> **Re-grade note:** **OPS-13** was previously mis-graded "resolved" — it was the **real one**. Trajectory this finding: resolved → active → fixed (see self-corrections below).

### ✅ Backend items shipped to prod + verified this round (5)
- **OPS-13 (HIGH — the real one) — session-pooler saturation → real DB outage** (`main.py`, commit `238313a`). Root cause: the app connected via the Supabase **SESSION-mode pooler** (`:5432`, 15-client cap) which **saturated** → Discord `postgres_failed` alert "max clients reached in session mode" (prev night 23:24) = backend lost DB connectivity. Fix: (a) **verified the code uses NO session features** (no named cursors / `SET` / `LISTEN`-`NOTIFY` / prepared statements) so it is **transaction-mode-safe**; (b) added **connect-retry** to `main.get_db_conn` (retry 3× w/ backoff, on the saturation class only); (c) TUM switched Coolify `DATABASE_URL` → `pooler.supabase.com:6543` (**TRANSACTION** mode). **VERIFIED on `:6543`:** reads (`/menu/public` 30KB), **WRITES** (cron heartbeat `run_count` 259→260, 0 new errors), app healthy. **NOTE:** the ~590ms postgres latency is the app↔pooler **TLS handshake** (fresh connection per request, no in-process pool) — **SEPARATE** from pooler mode; reducing it needs an in-process pool (**deferred**, not worth it now).
- **SEC-1b — `/ai/exec` optional IP allow-list** (`main.py`, commit `ae5992e`). `/ai/exec` (already hardened round 1: `X-AI-Exec-Key` constant-time + strict whitelist + rate limit) now has an **optional IP allow-list** — `_check_ip_allowed()` rejects **403** unless the caller IP is in `AI_EXEC_ALLOWED_IPS` (**unset = no restriction, back-compatible**). Caller is the separate marastation-ai app; **full lockdown = TUM sets the env var.**
- **PNL-4 — WHT tax-id prefill by exact-name match** (`export_routes.py` + `yearly_routes.py`, commit `72343a3`). PND.3 (ภ.ง.ด.3) WHT exports now **prefill the payee tax-id** by **EXACT normalized-name match** against counterparties (a clean JOIN is infeasible — WHT rows carry only a free-text name). **Exact-match ONLY (never fuzzy** — a wrong tax-id on a government filing is worse than blank); unmatched rows stay **blank** + the strengthened red manual-check note remains.
- **AI-6 — cashflow AI categorization audit log** (commit `556a119`, migration `2026_06_03_ai6_cashflow_categorization_log`). Cashflow AI categorization decisions are now **audit-logged**. Extended `ai_categorization_log` (`bill_id` already nullable) with `cashflow_entry_id` + `source` columns; instrumented `_categorize_cashflow_one` to log **BOTH** rule + LLM tiers in the **same transaction (atomic)**, with a lowered-confidence fallback reason on invalid LLM codes.
- **OPS-11 self-alert fix** (`cron`/watchdog, commit `7db1562`). The stale-job watchdog was alerting that **IT ITSELF** had "never run" (it writes its own heartbeat only after finishing, so on first run it looked missing). **Skip `_SELF_JOB_ID`** in the missing-job alert (stale-detection still applies).

### Duplicate-operation-id cleanup + a DEFENSE catch
- **dup operationId** (commit `238313a`). Set `include_in_schema=False` on the 4 GET+HEAD ops endpoints (`health`, `health/deep`, `cron/health`, `menu/public`) — FastAPI emitted the same `operationId` for GET+HEAD.
- ⚠️ **DEFENSE — do NOT blindly apply Haiku auto-diagnose patches.** The `auto_diagnose` "Patch suggestion (Claude Haiku)" was **WRONG** — it told us to **delete `menu_public_router`** as a "duplicate", but it is `include_router`-ed exactly **once**; applying it would have **broken `/menu`**. Verify before applying any auto-diagnose patch.

### New tests this round
`tests/test_ai_exec` (IP allow-list), `tests/test_cron_stale_alert` (self-skip), `tests/test_db_conn_retry` (saturation retry). Suite: **211 passed / 23 skipped** (48 new offline tests this session).

### Self-corrections (surfaced openly, not hidden)
- **OPS-2** High → Low: the `backup.py` host port rewrite (`:5432`→`:6543`) is **intentional**.
- **`sales_import_raw`** is **NOT** a backup table — **kept** (live forecast source).
- **OPS-13** resolved → active → **fixed** (the real prod outage; see above).

### Rollback (one-command revert)
Rollback tags (2026-06-03): `backup-pre-ai6`, `backup-pre-sec1b`, `backup-pre-pnl4`, `backup-pre-ops11fix`, `backup-pre-ops13retry`. Commits: `238313a` (OPS-13 retry + dup-op-id), `ae5992e` (SEC-1b), `72343a3` (PNL-4), `556a119` (AI-6 + migration `2026_06_03_ai6_cashflow_categorization_log`), `7db1562` (OPS-11 self-alert fix).

### 👉 STILL OPEN (not blocking the A/A+ grade)
**🔴 TUM data / action:**
1. **OPS-12 — pin `requirements.txt`.** Paste the container's `pip freeze` to pin deps.
2. ~~**PNL-3 — WHT gross vs net.**~~ ✅ **RESOLVED 2026-06-03 — verdict GROSS, NO code change** (see the round-5 section at the top): live-data round-number test on `v_daybook_pnl` proved the stored amounts are gross, so the original `tax = amount × rate` formula is correct; Antigravity's net-assumption gross-up was reverted across `export_routes.py` / `tax_routes.py` / `yearly_routes.py`.
3. **Set `AI_EXEC_ALLOWED_IPS` in Coolify** — completes **SEC-1b** (full `/ai/exec` lockdown).

**🟢 Antigravity (HANDOFFs written in the VEXONHQ repo):**
4. **FE-3 — POS declutter** (cut map approved by TUM).
5. ~~**SEC-3 — httpOnly cookie via same-origin proxy** (cross-repo).~~ ✅ **DONE 2026-06-03** (frontend repos only; backend auth_routes.py unchanged).
6. **FE-2 — route manifest** (after FE-3).

**Optional later:** reduce the ~590ms DB latency via an in-process connection pool.

> ~~**Supabase ops watch:** ticket **SU-387973** — orphaned storage; grace extension to **04 Jun 2026**. Watchdogs armed.~~ ✅ **RESOLVED 2026-06-03** — Supabase downgraded back to Free; support confirmed actual usage ~270 MB.

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

> ~~**Supabase ops watch:** ticket **SU-387973** — orphaned storage objects (12.7 GB billed vs ~268 MB physical); grace extension to **04 Jun 2026**. Local + remote watchdogs armed for a 402.~~ ✅ **RESOLVED 2026-06-03** — Supabase downgraded back to Free; actual usage ~270 MB confirmed.

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

> ~~**Supabase ops watch:** ticket **SU-387973** — storage quota shows 12.7 GB billed vs 268 MB physical (orphaned objects at the raw storage node, invisible to the Storage API); grace extension to **04 Jun 2026**. Local + remote watchdogs armed for a 402.~~ ✅ **RESOLVED 2026-06-03** — Supabase downgraded back to Free; actual usage ~270 MB confirmed.

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

## 2026-07-07 Historical statement cleanup status

### Committed and verified
- April 2026 reclass committed: `91` rows / `207,763.87`, backup `audit.bank_statement_reclass_backup_20260707_april`.
- March 2026 reclass committed: `105` rows / `212,939.86`, backup `audit.bank_statement_reclass_backup_20260707_march`; includes manual Grab parser-miss row `588.18`.
- February 2026 reclass committed: `98` rows / `179,513.36`, backup `audit.bank_statement_reclass_backup_20260707_february`.
- January 2026 reclass committed: `102` rows / `197,126.35`, backup `audit.bank_statement_reclass_backup_20260707_january`; includes two Grab category-only corrections from `grab_payout/pos_cash`.
- December 2025 reclass committed: `102` rows / `181,553.15`, backup `audit.bank_statement_reclass_backup_20260707_december`.
- November 2025 verified-subset reclass committed: `96` rows / `253,502.11`, backup `audit.bank_statement_reclass_backup_20260707_november_verified_subset`.
- November 2025 early-Grab manual reclass committed: `11` rows / `3,168.81`, backup `audit.bank_statement_reclass_backup_20260709_november_early_grab`.
- Local execution/log evidence folder: `C:\Users\rapee\Desktop\PJ-MARA\VPS-VEXONHQ\Audit Reports\historical_reclass_commit_20260707_231950`.
- Draft SQL files under `docs/superpowers/plans/` remain rollback-safe on disk with active `ROLLBACK;`.

### Blocked / needs TUM evidence decision
- None for this historical statement reclass batch.

### Safety reminder
- May, June, the committed April-November verified batch, and the committed 2025-11 early-Grab manual batch must not be rerun.

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
