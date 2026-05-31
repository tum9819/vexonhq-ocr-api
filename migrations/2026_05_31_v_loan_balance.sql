-- 2026_05_31_v_loan_balance.sql
-- Per-lender loan ledger. Reads bank_statement_entries DIRECTLY (not v_daybook),
-- because v_daybook hard-codes counterparty=NULL for bank rows (spec section 3.5).
-- Lender = the notes column (set at tag time via POST /classify). Rows with no
-- lender yet group under 'ไม่ระบุผู้ให้ยืม'.
-- outstanding = borrowed - repaid. Negative => lender now owes the shop.
-- Idempotent: CREATE OR REPLACE. Reversible with DROP VIEW.

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

COMMENT ON VIEW public.v_loan_balance IS
    'Per-lender loan ledger (เงินยืม): borrowed - repaid = outstanding, grouped by the notes column. Source: bank_statement_entries rows tagged loan_in / loan_repayment.';
