# Re-audit (Batch 13) — Accountant + Small-Biz Admin lens — RUNBOOK

> Date: 2026-05-29. Method: 53-agent workflow (`mara-system-reaudit`), accountant/controller lens, every finding adversarially re-verified against the **live production DB** (Supabase `osneubnwghvbwyazaedo` / mara-ai-prod) before inclusion.
> Tally: **25 findings CONFIRMED** (17 raised-then-refuted as false positives, 15 low-severity unverified). This re-audit found NEW classes the batch 1-12 code-audit missed because it audited code correctness, not accounting correctness (double-counting, revenue recognition, tax treatment).
> Source detail: workflow run `wf_40de175f-3ad`, script `docs/.../workflows/scripts/mara-system-reaudit-*.js` (full output was in a temp file — this doc is the durable record).

## ⚠️ LIVE-DB VERIFICATION (2026-05-29, after first write) — read this first

When implementing Group A I queried the **live production view** (`pg_get_viewdef('public.v_daybook')`). Result: **the live `v_daybook` does NOT match `migrations/17_vendor_bills_daybook.sql` in the repo.** Production has extra fixes applied directly to the DB that were never committed as migrations. This corrects several workflow findings:

- **NEW / ROOT ISSUE — repo↔prod migration drift.** Live `v_daybook` Branch 1 is `GREATEST(0, ps.net_total - rider_gross)` (delivery gross removed) and Branch 7 has `AND source_type <> ALL('rider_income_lineman','rider_income_grab')`. Neither is in the repo. Risk: re-running migration 17 (or any DROP/CREATE from the repo) would **revert** these and regress prod. Fix: commit the live def back into the repo — `migrations/2026_05_29_fix_vdaybook_ar_direction.sql` (written this session) now captures it.
- **B1 (delivery double-count) = FALSE POSITIVE in prod.** Already deduped live: May pos_sale = 208,899 = 254,809 − 45,910 rider_gross (exact). The workflow verifier read the stale repo migration 17 for the view structure, not the live view. No double-count in production. (Still: commit the dedup to the repo.)
- **B10 (statement_rules triple-count) = mitigated in prod** (Branch 7 already excludes rider_income_* source types). Leave the stale DB rules cleanup as low priority.
- **A1 (AR sign bug) = REAL but LATENT.** `ar_ap_entries` is EMPTY in prod (0 rows) → zero current P&L impact. Will corrupt the moment AR/AP is used. Fix written (migration above), NOT yet applied — needs TUM confirm.
- **A2 (food-cost 0%) = REAL, MOVED TO GROUP B.** Root cause confirmed: the `/dashboard` food_cost query filters category codes `food_cost/raw_meat/raw_veggies/raw_seasoning/raw_oil_gas/raw_beverage` — **none exist**; the real COGS codes are `food_raw` + `beverage_raw`. AND 54% of all expense (1,199,928 baht, 956 rows) has `category_code = NULL`. Fixing only the code names yields a still-understated (more misleading) number, so A2 must be done WITH a categorization pass → Group B.
- **Lesson:** every B-group finding that touches SQL/views MUST be re-verified against the LIVE definition before acting (the repo is not the source of truth for the DB).

## ⚑ EXECUTION GROUPING (agreed with TUM 2026-05-29: do A → B → C)

- **Group A — clear bugs, fix now** (no policy decision needed; mostly mechanical). DO FIRST.
- **Group B — needs TUM / bookkeeper decision on accounting convention** before any code change.
- **Group C — page deletions/merges + stop exporting 3 POS files** (UI + ops cleanup).

Flow for every change: **ตรวจ → Backup tag → แก้ → เทสหลายรอบ → Confirm → TUM push**. Claude never pushes.

---

## GROUP A — CLEAR BUGS (fix now)

| ID | Bug | File:line | Impact | Fix |
|----|-----|-----------|--------|-----|
| **A1** | AR receipts booked as EXPENSE — `CASE ae.direction WHEN 'ar'` never matches stored value `'receivable'`, so every receivable payment falls to ELSE → `direction='expense'`, `source='ap_payment'` | `migrations/17_vendor_bills_daybook.sql:82-93` (also `16_bank_statement.sql:152-159`, `19_vdaybook_dedup_fix.sql:86-91`) | A ฿10,000 catering payment RECEIVED is booked as ฿10,000 expense → profit wrong by 2× the AR amount. `ap_payment` is NOT in the P&L exclusion list so it hits `v_daybook_pnl`. | Change `WHEN 'ar'` → `WHEN 'receivable'` (and AP `'ap'`→`'payable'`) in the v_daybook view. **DB view migration** — verify `SELECT direction,count(*) FROM ar_ap_entries GROUP BY 1` shows only receivable/payable first. |
| **A2** | food-cost % shows **0%** + category pie misses ~54% of expense — `pos_cashflow` raw-material cash purchases (498k) + vendor_bills have `category_code=NULL`; dashboard `top_categories`/`food_cost`/`by-category` filter `category_code IS NOT NULL`, and the food_cost query only sums 6 COGS codes that NO expense row actually uses | `phase2_routes.py:364-378` (top_categories), `:469-480` (food_cost), `pnl_routes.py:250-267` (by-category), `menu_routes.py:1623-1634` (scorecard KPI) | Owner sees 0.0% food cost (badged "excellent/green") + a category pie missing ~40-54% of spend → menu/pricing decisions on false numbers. | Backfill/AI-categorize the NULL `pos_cashflow` + `vendor_bill` rows into real COGS categories, OR map the existing categories into the food_cost code set. Needs a small design choice but the 0% display is unambiguously wrong. *(borderline A/B — confirm approach)* |
| **A3** | Dashboard "จำนวนบิล" shows ~30 not ~660 — `COUNT(CASE WHEN direction='income')` over `v_daybook` counts daybook ROWS (pos_sale = 1 row/day) not bills | `phase2_routes.py:259-262` | Month bill count wrong ~20×; average-ticket + bills/day (staffing) reasoning broken. (Logged earlier as L3, still open.) | Read `pos_sales_daily.bill_count` (parsed from "จำนวนบิล") instead of counting rows. |
| **A4** | `/pos/prices` counts VOIDED bills — both queries filter only `si.unit_price > 0`, missing `AND b.bill_net > 0` (the one si-JOIN-pos_bills site the batch5 void sweep `5b785e9` skipped) | `menu_routes.py:3649-3655` and `:3716-3722` | Void line items pollute AVG/MIN/MAX unit_price, distinct-price count, total_qty → price-drift signal distorted. | Add `AND b.bill_net > 0` to both queries (parity with lines 2290/2615/2778/4067). |

---

## GROUP B — NEEDS TUM / BOOKKEEPER DECISION (convention, then implement)

| ID | Issue | File:line | Why it needs a decision |
|----|-------|-----------|-------------------------|
| **B1** | **Delivery revenue double-counted** (CRITICAL, confirmed by 2 agents). `pos_sales_daily.net_total` already includes Grab/Lineman orders rung through POS; `v_daybook` ALSO books `rider_income_grab/lineman` on top. Proof: May POS delivery = 234 bills = 234 rider orders exactly. Overstates revenue ~30-42k/mo (8-15%). | `migrations/17_vendor_bills_daybook.sql:17-60` | Pick ONE delivery revenue source: (a) exclude delivery channels from the pos_sale branch, OR (b) drop rider_income_* from income and treat commission as expense. Prior audit C4 wrongly assumed POS/rider disjoint. Document in FINANCE_SPEC. |
| **B2** | **Owner capital + inter-entity transfers tagged `other_income`** leak into revenue (81,419: ฿43,500 = owner's own TTB transfers, ~฿34,527 = ร้านสถานีหม่าล่า). `other_income` not in exclusion list. | `migrations/17_vendor_bills_daybook.sql:120-131` + every exclusion list | Reclassify owner self-transfers → owner_capital, inter-entity → transfer_error; OR segregate other_income until reviewed. |
| **B3** | **Vendor bill double-counted via two paths**: `vendor_bill` (accrual, bill_date) + `ap_payment` (when paid via AR/AP module, payment_date) both expense, neither excluded → credit-vendor bill (Makro/CP/Singha) counted twice. | `migrations/19_vdaybook_dedup_fix.sql:86-96 + :128-138`; trigger `07_phase3_arap_schema.sql:213-273` | Choose accrual basis (exclude ap_payment) or cash basis (exclude vendor_bills with linked AP). |
| **B4** | **`vendor_purchase` (bank) vs `vendor_bill` double-count** — food_raw/beverage_raw bank debits (374k) counted in P&L alongside OCR'd invoices (701k) unless the ±1฿/±7d matcher fires (swallowed try/except, single-candidate only). | `migrations/2026_05_19_phase3_reclassify_by_category.sql:43-52`; matcher `main.py:1541` | Same as B3 — one canonical purchase source; surface unmatched as reconciliation gap. |
| **B5** | **ภ.ง.ด.3 WHT — multiple defects (TAX, ask bookkeeper).** (a) 3 generators (`/export/pnd3`, `/export/pnd3-annual`, `/tax/wht-export`) give different totals/rates/sections; (b) musician fee stamped 40(2) not 40(8); (c) annual export misses ~76,010 of cash-drawer musician fees (category NULL) → under-report ~2,280 tax; (d) hard-coded flat 3%, codes `freelance`/`pnd3` don't exist. | `export_routes.py:466-573`, `yearly_routes.py:313-440`, `tax_routes.py:40-194`, `phase12_bank_statement_routes.py:292` | Confirm with accountant: net-vs-gross WHT base, correct มาตรา + rate per payee type, which payees are in scope. Then collapse all 3 exports onto one shared WHT rule table. |
| **B6** | **Bank statement direction from Thai prefix only** — non-`จาก` credit (interest/refund/reversal/LINE PAY) booked as expense (sign flip). Partly mitigated (needs_review + bank_statement excluded from P&L) but real for rider_income_* credits + manually-classified rows. | `phase12_bank_statement_routes.py:159-198` | Use KBank's own รับ/โอนออก token (currently discarded by the date regex) as source of truth; route unknown prefixes to needs_review. |
| **B7** | **Ambiguous invoice↔statement match silently mutates PAST months' P&L** — multi-candidate match flips already-counted `vendor_purchase` rows to `needs_review` (dropped from v_daybook) with no notes/audit trail. | `main.py:1627-1642` | Use a separate flag (not `needs_review`) so rows stay counted once until resolved; never silently remove a closed-period row. |
| **B8** | **Lineman commission = hard-coded 32.1% ESTIMATE** treated as actual money in P&L (~8% of revenue on an assumption; Grab uses actuals). No actual-payout column ingested. | `pos_import.py:880,894-904` | Ingest actual Lineman payout if available; else surface `gp_is_estimated` in P&L/revenue and reconcile monthly vs bank deposit. |
| **B9** | **Delivery commission never booked as expense** — net presentation hides ~30% platform cost; food-cost% denominator mixes gross POS with net-of-commission delivery. | `migrations/17_vendor_bills_daybook.sql:33-60` | Decide gross-up + commission expense line, or document net convention. (Couples with B1.) |
| **B10** | **Stale `statement_rules` map GRAB/LINE PAY bank credits → rider_income_*** (counted in P&L) → potential TRIPLE-count of delivery (POS + CSV + bank). LINE PAY case already escapes the builtin guard. | `migrations/16_bank_statement.sql:56-59` vs `phase12_bank_statement_routes.py:234,300-312` | Delete/fix the 4 delivery rows so bank credits tag grab_payout/lineman_payout (excluded), not rider_income_*. |
| **B11** | **`pos_sales_items` re-import double-counts** item qty/revenue — bare INSERT, no ON CONFLICT/DELETE (pos_bills is idempotent, lines are not). Byte-different re-export of overlapping dates doubles item-level analytics. (= B7-C4, deferred design.) | `pos_import.py:1150-1167` + `:1428-1445` | DELETE-by-bill_id before insert, or UNIQUE(bill_id,line_no) + ON CONFLICT. |
| **B12** | **AR/AP duplicate-payment guard = 30-second window only**, no reference_no uniqueness → same payment re-entered after 30s double-counts (ap_payment expense + understated AP). | `phase3_arap_routes.py:560-575` | Unique/soft-warn on (entry_id, reference_no); surface same-entry same-amount regardless of age. |
| **B13** | **/pos/food-cost = 3rd unreconciled food-cost number** — recipe-estimate COGS ignores FoodStory's own imported actual cost (`pos_sales_by_product.avg_cost/cost_total/profit`, currently dead data). | `menu_routes.py:4044-4137` vs `pos_import.py:532,548,551` | Surface FoodStory actual cost as a reconciliation column, or pick one authoritative COGS source + label estimates. |

> Known-already items folded into B (from batch 1-12): B5(d) int() truncation = B6-C3; B3/B4 = batch4 M3 + batch-bank; B11 = B7-C4; A3 = L3.

---

## GROUP C — PAGES + POS FILES (UI/ops cleanup, do last)

### C-files: stop exporting 3 of 10 POS files (verified: written to DB but never read by any endpoint/view AND derivable from bill_detail)
- ✂️ STOP: `รายงานสรุปยอดขายรายวันแยกตามรหัสถาดเก็บเงิน` (daily_drawer → pos_sales_drawer_daily/pos_cash_drawers, 0 readers)
- ✂️ STOP: `รายงานสรุปยอดขายแยกตามประเภทการชำระเงิน` (payment_type_summary → pos_sales_payment_summary, 0 readers)
- ✂️ STOP: `รายงานสรุปยอดขายแยกตามเดือน` (monthly_summary → pos_sales_monthly, 0 readers)
- ✅ KEEP 7 — incl. two name-misleading ones: `Transaction_Store*.csv` = **Grab income**; `ภาพรวมยอดขายรายวันทุกสาขา.xlsx` = **Lineman income** (sheet named LINEMAN). And `แยกตามวัน` (#7) = the literal POS revenue line in v_daybook. Cutting any of these zeroes revenue.

### C-pages: nav has ~64 pages; POS dropdown alone = 33 items
- **CUT (6):** `/pos/staff`, `/pos/tables`, `/pos/combos`, `/pos/shifts`, `/pos/prices`, `/budgets` (legacy, not in nav)
- **MERGE (~11):** `/menu` absorbs Item Trend + Menu Engineering + Category Mix; Heatmap absorbs Peak-hours + DOW; Recipes absorbs Ingredients; Reorder absorbs AI-forecast; + Daily Calendar / Revenue Forecast / Payments as drill-downs; OCR Studio fold.
- **ELEVATE (daily-decision pages):** Flash Report, Dashboard, P&L, สมุดรายวัน, Cash Flow, Revenue Goals, ค้างรับ/ค้างจ่าย, จ่ายบิล, Quick Entry, ภ.ง.ด.3, Invoices.
- Result: POS group 33 → ~13-15.

---

## Recommended order within A (safest → highest-impact)
1. **A4** /pos/prices void filter (2-line, isolated)
2. **A3** bill_count (read pos_sales_daily.bill_count)
3. **A1** AR sign bug (DB view migration — backup + A/B verify on Supabase; HIGH impact on profit)
4. **A2** food-cost category (confirm backfill-vs-query approach first)
