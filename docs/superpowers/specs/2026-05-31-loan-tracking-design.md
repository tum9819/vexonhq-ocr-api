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

**Direction guard (the trap):** rules are bound to income vs expense separately, so นุศรา's
*outgoing* shop-expense transfers (e.g. memo "ค่าเนื้อ") are NOT mistagged as loans — only a
transfer whose memo literally says "คืนยืม" becomes `loan_repayment`. This matches the existing
memo-driven policy (AGENTS #19, memory `project_slip_classification`).

### 3.3 Balance view + endpoint (API contract for the dashboard)

`public.v_loan_balance` — aggregate per lender (grouped by `counterparty`):

```sql
CREATE OR REPLACE VIEW public.v_loan_balance AS
SELECT
  counterparty AS lender,
  COALESCE(SUM(amount) FILTER (WHERE source = 'loan_in'), 0)        AS borrowed,
  COALESCE(SUM(amount) FILTER (WHERE source = 'loan_repayment'), 0) AS repaid,
  COALESCE(SUM(amount) FILTER (WHERE source = 'loan_in'), 0)
    - COALESCE(SUM(amount) FILTER (WHERE source = 'loan_repayment'), 0) AS outstanding,
  MAX(entry_date) AS last_activity,
  COUNT(*)        AS txn_count
FROM public.v_daybook
WHERE source IN ('loan_in', 'loan_repayment')
GROUP BY counterparty;
```

Light name normalization in the view (trim + strip คำนำหน้า like "น.ส./นาย/นาง") so the same
lender does not split into two rows ("นุศรา" vs "น.ส. นุศรา ป"). Exact expression verified at
implementation time against live data.

New `loan_routes.py` (registered in `main.py`; behind JWT, NOT in `PUBLIC_PATHS`):
- `GET /loans` → `[{lender, borrowed, repaid, outstanding, last_activity, txn_count}]`
- `GET /loans/{lender}` → per-lender entry list (each loan_in / loan_repayment row: date, amount,
  memo, source) for the dashboard drill-in.

Expected for the trigger case: `borrowed=33000, repaid=15000, outstanding=18000`.

### 3.4 Edge cases

- **Over-repayment** (outstanding < 0) → lender now owes the shop; show the negative value as-is,
  do not clamp to 0.
- **Ambiguous memo** (e.g. the trigger slip) → manual tagging per 3.2.
- **Name spelling drift** → handled by view normalization (3.3).
- **NULL counterparty** → grouped as "ไม่ระบุผู้ให้ยืม" for TUM to reconcile later.

---

## 4. Testing & rollout

1. `ast.parse` every touched `.py`.
2. **A/B P&L proof** — for a month containing loan rows, income / expense / profit totals are
   **identical before vs after** (proves loan rows do not leak into P&L); cross-check `/pnl/monthly`.
3. `rg "FROM public\.v_daybook\b"` leaves no P&L path missing the loan exclusion.
4. `pytest` for `/loans` (นุศรา fixture → outstanding = 18,000).
5. `.\verify.ps1` (and `-Smoke` after deploy once VPS CPU < 30%, per memory `smoke_after_deploy_wait`).

**Pre-implementation DB verifications (do these FIRST — AGENTS #34 class):**
- `SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid='public.bank_statement_entries'::regclass AND contype='c'` — confirm NO CHECK constraint on `source_type` would reject `loan_in` / `loan_repayment` (if one exists, extend it in a migration, else the reclassify UPDATE 500s like the `pos_imports.status='error'` bug).
- `pg_get_viewdef('public.v_daybook')` — confirm the bank-statement branch surfaces `source_type` as `v_daybook.source` (so the new values actually appear in the daybook + are caught by the exclusion). The live view may DRIFT from the repo migration (AGENTS #17) — read the live definition, do not trust the repo file.
- Confirm `counterparty` is populated for bank rows (the group-by key in `v_loan_balance`); if some bank rows carry the name elsewhere, adjust the view's lender expression.

**Migrations (idempotent; commit to repo before applying):**
- `2026_05_31_loan_sources_pnl_exclude.sql` — extend `v_daybook_pnl` exclusion + seed
  `statement_rules` loan keywords.
- `2026_05_31_v_loan_balance.sql` — the balance view.

**Code:** splice `loan_in` / `loan_repayment` into the inline exclusion lists in `pnl_routes.py`
and `cashflow_routes.py`; add the loan mapping to slip-reconcile; new `loan_routes.py` + register
in `main.py`.

**Workflow (AGENTS 6-step):** Backup tag `origin/main` → edit → test หลายรอบ → confirm → hand TUM a
single PowerShell paste block (no `Co-Authored-By:` trailer) → **TUM pushes** (Claude never pushes).

---

## 5. Out of scope (this spec)

- Frontend dashboard card → follow-up in the `VEXONHQ` repo, consuming `GET /loans`.
- Loan terms / due dates / interest (not needed — informal loan).
- General third-party lenders beyond grouping-by-name (current need is one co-owner).
