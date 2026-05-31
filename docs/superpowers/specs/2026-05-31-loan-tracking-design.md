# Design — Loan tracking (เงินยืม) — vexonhq-ocr-api

- **Date:** 2026-05-31
- **Author:** Claude (brainstormed with TUM)
- **Status:** Approved design — pending implementation plan
- **Scope:** Backend only (this repo). Frontend dashboard card is a follow-up in the `VEXONHQ` repo, consuming `GET /loans`.

---

## 1. Problem / motivation

A co-owner (นุศรา) lends cash to the shop and is repaid later by bank transfer. Today such an
incoming transfer lands as an unreviewed bank-statement row (`source_type='bank_statement'`,
`match_status='needs_review'`), which is already **excluded** from the P&L — so it does *not*
inflate revenue. But the system has **no concept of a loan**, so TUM cannot:

1. label the money correctly as เงินยืม (a liability, not income),
2. label the repayment correctly (settling a liability, not an expense), and
3. track the **outstanding balance per lender** (borrowed − repaid) to reconcile against the
   K+ slips that arrive in LINE.

Real case from the slip that triggered this:
นุศรา ป → ระพีภัทร์ (พร้อมเพย์ร้าน), 33,000 บาท, 30 พ.ค. 2569, memo `ร้านหม่าล่า 33000-15000`.
TUM's reading of the memo: **ยืม 33,000, คืนแล้ว 15,000 → ค้าง 18,000.**

### Accounting principle (the why)

A loan is a **balance-sheet / financing** event, never a P&L event:

| Leg | Cash | Accounting reality | If counted wrong |
|---|---|---|---|
| Borrow in (`loan_in`) | money in | liability ↑ (shop owes lender) — **not revenue** | profit inflated |
| Repay out (`loan_repayment`) | money out | liability ↓ (settle debt) — **not expense** | expense inflated |

Both legs MUST be excluded from the P&L. This is the same class as the existing
`owner_capital` / `owner_advance` exclusion (Session 6 + Session 47 incidents).

---

## 2. Chosen approach — View-only (no new entity table)

Decided over (2) dedicated `loans`+`loan_payments` tables and (3) a separate `loan_ledger`
table. View-only is the leanest, keeps a single source of truth (`v_daybook`), makes the P&L
correct automatically, and covers "ไล่ยอดค้างต่อคน". TUM does not need loan terms / due dates /
interest (informal family/partner loan), so the heavier entity model is YAGNI.

---

## 3. Design

### 3.1 New `source` values + P&L exclusion (correctness core)

Two new values on `bank_statement_entries.source_type` → flow into `v_daybook.source`:

| source | meaning | direction | counted in P&L? |
|---|---|---|---|
| `loan_in` | เงินยืมเข้า (shop receives a loan) | income | **NO** (liability, not revenue) |
| `loan_repayment` | โอนคืนเงินยืม (shop repays) | expense | **NO** (settles liability, not expense) |

`category_code = 'loan'` is a display tag only (shown in the raw daybook list); it does not
affect P&L because exclusion is by `source`.

**Both values must be added to EVERY P&L exclusion list — missing one leaks a loan into profit:**
- canonical view `public.v_daybook_pnl` (`migrations/2026_05_27_v_daybook_pnl.sql`)
- inline lists still reading raw `v_daybook`: `pnl_routes.py` (2 sites), `cashflow_routes.py` (3 sites)
- gate before ship (AGENTS #20): `rg "FROM public\.v_daybook\b"` must leave no P&L/export/analytics
  path without loan in its exclusion. Prefer migrating any straggler to `v_daybook_pnl`.

### 3.2 Tagging: auto from slip memo (primary) + manual fallback

**Auto (primary):** seed direction-aware keyword rules into the `statement_rules` table (data,
not code — TUM can add keywords later with no deploy). Both `_classify`
(`phase12_bank_statement_routes.py`) and the nightly slip-reconcile
(`slip_routes.reconcile_slips_to_statements`) consult these.

| memo contains | + direction | → source_type | category_code |
|---|---|---|---|
| `เงินยืม` / `ยืมเงิน` / `ยืม` | income | `loan_in` | `loan` |
| `คืนเงินยืม` / `คืนยืม` | expense | `loan_repayment` | `loan` |

Slip-reconcile integration: add the loan category → loan source mapping to `_CAT_TO_SOURCE`
(or equivalent) so a matched repayment slip pushes `loan_repayment` onto its bank row.
⚠️ `slip_routes` + the scheduler are a **coordination zone** (AGENTS Boundaries) — touch
minimally and re-verify the live behavior.

**Manual (fallback):** the existing reclassify endpoint
(`phase12_bank_statement_routes.py` PUT, ~line 484) already accepts an arbitrary `source_type`,
so TUM can set a row to `loan_in` / `loan_repayment` by hand on the bank-statement page. This is
required for the trigger slip (`33000-15000` has no "ยืม" keyword). Going forward, writing memos
that include "เงินยืม" / "คืนยืม" makes auto-tagging work.

**Lender capture (drives the per-lender ledger):** because there is no name column (§3.5), the
reclassify endpoint is extended so that when `source_type` is a loan type, the request also carries
a `lender` string written to `bank_statement_entries.notes` (e.g. `"นุศรา"`), AND it sets
`match_status='manual'` so the row leaves `needs_review` and appears in `v_daybook`. The auto path
sets the loan `source_type` from the memo but leaves `notes` for TUM to fill on the dashboard (or a
later iteration can derive it from the matched slip's name) — until `notes` is set, the row groups
under "ไม่ระบุผู้ให้ยืม".

**Direction guard (the trap):** rules are bound to income vs expense separately, so นุศรา's
*outgoing* shop-expense transfers (e.g. memo "ค่าเนื้อ") are NOT mistagged as loans — only a
transfer whose memo literally says "คืนยืม" becomes `loan_repayment`. This matches the existing
memo-driven policy (AGENTS #19, memory `project_slip_classification`).

### 3.3 Balance view + endpoint (API contract for the dashboard)

**Revised after live-DB verification (see §3.5):** `v_daybook` hard-codes `counterparty = NULL`
for every bank row, and `bank_statement_entries` has NO structured name column — the lender name
lives only in free-text `description`. So the ledger groups by the **`notes` column** (the chosen
lender label, set at tag time) and reads `bank_statement_entries` **directly** (not `v_daybook`).

`public.v_loan_balance` — aggregate per lender (grouped by `notes`):

```sql
CREATE OR REPLACE VIEW public.v_loan_balance AS
SELECT
  COALESCE(NULLIF(btrim(notes), ''), 'ไม่ระบุผู้ให้ยืม')                  AS lender,
  COALESCE(SUM(amount) FILTER (WHERE source_type = 'loan_in'), 0)        AS borrowed,
  COALESCE(SUM(amount) FILTER (WHERE source_type = 'loan_repayment'), 0) AS repaid,
  COALESCE(SUM(amount) FILTER (WHERE source_type = 'loan_in'), 0)
    - COALESCE(SUM(amount) FILTER (WHERE source_type = 'loan_repayment'), 0) AS outstanding,
  MAX(txn_date) AS last_activity,
  COUNT(*)      AS txn_count
FROM public.bank_statement_entries
WHERE source_type IN ('loan_in', 'loan_repayment')
GROUP BY 1;
```

(Verified columns exist on `bank_statement_entries`: `notes`, `amount`, `source_type`, `txn_date`.)

New `loan_routes.py` (registered in `main.py`; behind JWT, NOT in `PUBLIC_PATHS`):
- `GET /loans` → `[{lender, borrowed, repaid, outstanding, last_activity, txn_count}]` from `v_loan_balance`
- `GET /loans/{lender}` → per-lender entry list (each loan row: `txn_date`, `amount`, `direction`,
  `source_type`, `description`) from `bank_statement_entries WHERE notes = %s AND source_type IN (...)`.

Expected once the trigger data is tagged: `lender='นุศรา', borrowed=33000, repaid=15000, outstanding=18000`.

### 3.5 Live-DB verification results (2026-05-31, project `mara-ai-prod`)

- ✅ **No CHECK constraint on `bank_statement_entries.source_type`** — `loan_in` / `loan_repayment`
  can be written freely (only `match_status` has a CHECK: `auto|manual|needs_review`).
- ⚠️ **`v_daybook` bank branch** = `SELECT ... bse.source_type AS source, ..., NULL::text AS counterparty
  ... WHERE bse.match_status <> 'needs_review' AND source_type NOT IN ('rider_income_*')`. Two consequences:
  (a) counterparty is always NULL for bank rows → cannot group the ledger by it (drove the §3.3 rewrite);
  (b) a row with `match_status='needs_review'` is **excluded from `v_daybook` entirely** → when tagging
  a loan row via reclassify, also set `match_status='manual'` so it surfaces in the daybook and is
  caught by the P&L exclusion.
- ⚠️ **Trigger slip (33,000, 30 พ.ค.) is NOT imported yet** — latest bank data is 30 เม.ย. Existing
  นุศรา money-in rows currently sit as `source_type='bank_statement'`, `category_code='other_income'`
  → already excluded from P&L (safe). Tests use a fixture, not this row.
- ⚠️ **Repayment (ร้าน→นุศรา) is textually identical to reimbursement** — both read
  `K PLUS โอนไป BAY X0648 น.ส. นุศรา`. Only the slip memo distinguishes `loan_repayment` from a real
  `other_expense` (ค่าเนื้อ ฯลฯ). TUM owns that judgment per row; auto-tag only fires on an explicit
  "คืนยืม" memo keyword.

### 3.4 Edge cases

- **Over-repayment** (outstanding < 0) → lender now owes the shop; show the negative value as-is,
  do not clamp to 0.
- **Ambiguous memo** (e.g. the trigger slip) → manual tagging per 3.2.
- **Name spelling drift** → handled by view normalization (3.3).
- **NULL counterparty** → grouped as "ไม่ระบุผู้ให้ยืม" for TUM to reconcile later.

---

## 4. Testing & rollout

1. `ast.parse` every touched `.py`.
2. **A/B P&L proof of the exclusion mechanism** — insert a TEMP `loan_in` and `loan_repayment`
   row (match_status='manual') in a test month, confirm `/pnl/monthly` income / expense / profit are
   **unchanged by them** (they are excluded), then delete the temp rows.
   ⚠️ Note: separately *re-tagging an existing `other_expense` repayment → `loan_repayment`* WILL
   reduce that month's expense (it correctly leaves the P&L) — that is an intended correction, not a
   regression. The A/B proof is about the mechanism, not historical immutability.
3. `rg "FROM public\.v_daybook\b"` leaves no P&L path missing the loan exclusion.
4. `pytest` for `/loans` (fixture: a `loan_in` 33,000 + `loan_repayment` 15,000 with `notes='นุศรา'`
   → `outstanding = 18,000`).
5. `.\verify.ps1` (and `-Smoke` after deploy once VPS CPU < 30%, per memory `smoke_after_deploy_wait`).

**Pre-implementation DB verifications — DONE 2026-05-31, results recorded in §3.5.** (No CHECK on
`source_type`; v_daybook counterparty is NULL for bank rows; needs_review rows are dropped from
v_daybook. These drove the §3.2 `match_status='manual'` rule and the §3.3 view rewrite.)

**Migrations (idempotent; commit to repo before applying):**
- `2026_05_31_loan_sources_pnl_exclude.sql` — extend `v_daybook_pnl` exclusion. (The
  `statement_rules` loan-keyword seeding belongs to the §3.2 auto-tag path → **Phase 2**, not this
  migration.)
- `2026_05_31_v_loan_balance.sql` — the balance view.

**Code:** splice `loan_in` / `loan_repayment` into the inline exclusion lists in `pnl_routes.py`
and `cashflow_routes.py`; extend the reclassify PUT (`phase12_bank_statement_routes.py`) to accept
an optional `lender` (→ `notes`) and to set `match_status='manual'` when `source_type` is a loan
type; add the loan keyword rules + slip-reconcile mapping; new `loan_routes.py` + register in
`main.py`.

**Workflow (AGENTS 6-step):** Backup tag `origin/main` → edit → test หลายรอบ → confirm → hand TUM a
single PowerShell paste block (no `Co-Authored-By:` trailer) → **TUM pushes** (Claude never pushes).

---

## 5. Out of scope (this spec)

- Frontend dashboard card → follow-up in the `VEXONHQ` repo, consuming `GET /loans`.
- Loan terms / due dates / interest (not needed — informal loan).
- General third-party lenders beyond grouping-by-name (current need is one co-owner).
