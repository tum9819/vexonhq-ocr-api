# Design — Loan auto-tag (Phase 2) + how-to runbook — vexonhq-ocr-api

- **Date:** 2026-05-31
- **Author:** Claude (brainstormed with TUM)
- **Status:** Approved design — pending implementation plan
- **Depends on:** Phase 1 (`2026-05-31-loan-tracking-design.md`) — `loan_in`/`loan_repayment` sources, `v_loan_balance`, `/loans`, manual classify with `lender`.

---

## 1. Purpose & key constraint

Reduce the manual tagging for the **repayment leg** of loans: a repayment is an OUTGOING transfer
whose K+ slip TUM forwards in LINE. When its memo says "คืนยืม"/"คืนเงินยืม", auto-classify the
matched bank row as `loan_repayment` and record the lender — so the per-lender ledger updates with
no clicks.

**Hard constraint discovered in the code (drives the scope):** the slip pipeline is OUTGOING-only —
`slip_routes._classify_slip_category` hard-codes `direction = 'expense'` ("slips are always outgoing
transfers in TUM's model"). Therefore:
- ✅ **Repayment (outgoing)** can be auto-tagged via the existing nightly slip-reconcile.
- ❌ **Borrow (incoming `loan_in`)** does NOT flow through the slip pipeline (it is a bank-statement
  income row with no memo). It stays a **one-time manual tag** on `/bank-statement`.

This matches TUM's framing: the ongoing "slips ที่จะส่งเพิ่มใน LINE" are the repayments — those are
what auto helps with. TUM confirmed the workflow is mostly manual and asked for a how-to (§4).

## 2. Design

### 2.1 Detection + source mapping (reuse existing machinery)

- **Seed `statement_rules`** (idempotent migration, `INSERT … ON CONFLICT (rule_type, match_value)
  DO UPDATE`): two keyword rules, both `direction='expense'`, `priority=100`:
  - `match_value='คืนยืม'   → category_code='loan_repayment'`
  - `match_value='คืนเงินยืม' → category_code='loan_repayment'`
  (`_classify_slip_category` matches memo via `ILIKE '%…%'`, orders by `priority DESC, char_length
  DESC` → these win over generic name rules and do not false-match ordinary expenses. "คืนยืม" is not
  a substring of "คืนเงินยืม", so both keywords are needed.)
- **Add to `_CAT_TO_SOURCE`** (`slip_routes.py`): `'loan_repayment': 'loan_repayment'`. The reconcile
  Pass-2 push then writes `source_type='loan_repayment'` onto the matched bank row. That source is
  excluded from the P&L (Phase 1) — correct (a repayment is not an expense).

### 2.2 Lender → `notes` (normalized) in the reconcile push

- New helper `slip_routes._normalize_lender(name)`: strip Thai titles (`นาย`/`นาง`/`นางสาว`/`น.ส.`/`น.ส`),
  strip `++` and collapse whitespace, take the **first name token**. E.g. `"น.ส. นุศรา ปรางม++"` → `"นุศรา"`.
- Extend the Pass-2 UPDATE in `reconcile_slips_to_statements`: when `category_code == 'loan_repayment'`,
  also set `notes = COALESCE(%s, notes)` with `%s = _normalize_lender(recipient_name)` (the loop
  already has `recipient_name`). For non-loan categories pass `None` so `COALESCE` leaves `notes`
  untouched. The existing `WHERE … match_status <> 'manual'` clause still protects hand-tagged rows.
- **Name-consistency rule:** the borrow leg is tagged manually — TUM types the lender's short name
  (e.g. `"นุศรา"`), which equals what `_normalize_lender` produces for the auto path, so both legs
  group under one lender in `v_loan_balance`. First-name reduction is safe with one lender today; if
  two lenders ever share a first name, edit `notes` on the dashboard. (Documented in the how-to.)

### 2.3 What is NOT changed

- `phase12_bank_statement_routes._classify` (bank-import classification) is untouched — bank
  descriptions don't carry the slip memo, so loans are not detected there. No income-direction
  auto-tagging (borrow stays manual).
- The nightly scheduler registration is unchanged; only the function body gains a loan branch.

## 3. Edge cases

- Repayment memo without "ยืม" (e.g. just "คืน") → won't match; TUM writes "คืนยืม" or adds a keyword
  via `POST /add-rule` (data, no deploy).
- A row TUM already hand-tagged (`match_status='manual'`) is never overwritten by the job.
- Lender name drift → ledger splits; visible on `/loans`, fixable by editing `notes`.
- Idempotent: re-running reconcile changes nothing once tagged (UPDATE guarded by `IS DISTINCT FROM`).

## 4. How-to runbook (deliverable)

Write `docs/HOWTO_loans.md` (Thai, for TUM "ไว้ดูเผื่อลืม"). It must cover, in plain steps:
1. **What a loan is here** — financing, not P&L; borrow in / repay out; both excluded from profit.
2. **Borrow (เงินยืมเข้า) — manual, one-time:** on `/bank-statement`, find the incoming row from the
   lender → set category/source to `loan_in`, lender = short name (e.g. "นุศรา").
3. **Repayment (โอนคืน):**
   - Preferred (auto): when paying back, write the slip memo "คืนยืม" (or "คืนเงินยืม") and forward it
     in LINE as usual → the nightly 02:00 job (or `POST /slip/reconcile` "reconcile now") tags it
     `loan_repayment` + lender automatically.
   - Manual fallback: if the memo wasn't recognised, tag the outgoing row `loan_repayment` on
     `/bank-statement` (lender = same short name).
4. **View / reconcile:** open `/loans` → outstanding per lender (borrowed − repaid); click a lender to
   see every borrow/repayment; cross-check against the LINE slips.
5. **Gotchas:** use the SAME short lender name everywhere; "คืน" alone is not enough (needs "ยืม");
   borrows are never auto (incoming) — tag them by hand; add keywords via `POST /add-rule`.

## 5. Testing & rollout

1. `ast.parse` on `slip_routes.py`.
2. Seed migration applied; verify the two `statement_rules` rows exist.
3. **End-to-end (DB):** insert a test slip (memo "คืนยืม", recipient "น.ส. นุศรา ป") + a matched
   OUTGOING bank row → run `reconcile_slips_to_statements` (or `POST /slip/reconcile`) → assert the
   bank row became `source_type='loan_repayment'`, `notes='นุศรา'`, and appears in `v_loan_balance`
   under "นุศรา" as repaid. Confirm `/pnl/monthly` unchanged (A/B). Delete test rows.
4. `.\verify.ps1`; `-Smoke` after deploy once VPS CPU < 30%.
5. Workflow: commit locally; **TUM pushes**; no `Co-Authored-By:` trailer. `slip_routes.py` +
   nightly job = coordination zone — changes are additive (new loan branch) and idempotent.

## 6. Out of scope

- Auto-tagging borrows (incoming) — manual by design.
- A `lender_aliases` table — YAGNI for one lender; revisit if loan counterparties multiply.
