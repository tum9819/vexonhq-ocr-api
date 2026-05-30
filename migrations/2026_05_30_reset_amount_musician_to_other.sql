-- ============================================================
-- VEXONHQ Data-fix 2026-05-30 — reset amount-guessed musician_fee -> other_expense
-- Re-audit Batch 13 / slip-driven classification policy.
-- ============================================================
-- The old import heuristic tagged any 600/700/2,100/2,800 transfer to an
-- individual as musician_fee (source_type payroll_expense), which:
--   - mis-tagged owner/reimbursement transfers (e.g. to co-owner นุศรา) as
--     musician fees, and
--   - inflated the ภ.ง.ด.3 WHT base with ~261,300 of unverified "musician" payments.
--
-- POLICY (TUM, 2026-05-30): musician_fee is assigned ONLY when a K+ slip MEMO says
-- "ค่าดนตรี" (via the nightly slip reconcile). No slip note -> other_expense.
--
-- This resets every existing amount-guessed musician_fee row to other_expense
-- (still COUNTED as a cash-basis expense — expense total is unchanged — but no
-- longer reported as a musician fee / WHT). Rows TUM categorised by hand are kept.
-- The nightly slip reconcile (slip_routes.reconcile_slips_to_statements) then
-- re-tags musician_fee onto the bank rows that have a matching "ค่าดนตรี" slip.
-- Idempotent. Apply after deploying the slip-reconcile code.
-- ============================================================

UPDATE public.bank_statement_entries
SET category_code = 'other_expense',
    source_type   = 'other_expense'
WHERE category_code = 'musician_fee'
  AND match_status <> 'manual';
