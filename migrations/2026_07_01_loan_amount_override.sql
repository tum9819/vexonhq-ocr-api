-- 2026_07_01_loan_amount_override.sql
-- Applied live via Supabase MCP on 2026-07-01. Committed here for repo history.
--
-- Problem: bank_statement_entries.amount is a GENERATED column
-- (GREATEST(credit, debit)) tied to the real bank movement, needed intact
-- for statement checksum reconciliation. When a single bank transfer mixes
-- a shop loan with an unrelated personal amount (e.g. lender sends 33,000
-- in one transfer, only 18,000 of it is actually a loan to the shop), there
-- was no way to tag that row loan_in without either overcounting the loan
-- (using the full 33,000) or corrupting the real bank total.
--
-- Fix: add a nullable override column read by v_loan_balance instead of
-- amount directly. NULL = normal case (99% of rows), falls back to the real
-- amount. Set only on the rare split-transfer row.
--
-- Idempotent: ADD COLUMN IF NOT EXISTS + CREATE OR REPLACE VIEW. Reversible:
-- DROP COLUMN loan_amount_override (view auto-reverts via COALESCE fallback
-- once the column no longer has overrides, or re-run 2026_05_31_v_loan_balance.sql).

ALTER TABLE public.bank_statement_entries
  ADD COLUMN IF NOT EXISTS loan_amount_override numeric;

COMMENT ON COLUMN public.bank_statement_entries.loan_amount_override IS
  'Optional override for v_loan_balance when only part of this bank row''s amount is a loan_in/loan_repayment (rest is unrelated, e.g. mixed personal+shop transfer). NULL = use amount as-is.';

CREATE OR REPLACE VIEW public.v_loan_balance AS
SELECT
  COALESCE(NULLIF(btrim(notes), ''), 'ไม่ระบุผู้ให้ยืม')                                                    AS lender,
  COALESCE(SUM(COALESCE(loan_amount_override, amount)) FILTER (WHERE source_type = 'loan_in'), 0)        AS borrowed,
  COALESCE(SUM(COALESCE(loan_amount_override, amount)) FILTER (WHERE source_type = 'loan_repayment'), 0) AS repaid,
  COALESCE(SUM(COALESCE(loan_amount_override, amount)) FILTER (WHERE source_type = 'loan_in'), 0)
    - COALESCE(SUM(COALESCE(loan_amount_override, amount)) FILTER (WHERE source_type = 'loan_repayment'), 0) AS outstanding,
  MAX(txn_date) AS last_activity,
  COUNT(*)      AS txn_count
FROM public.bank_statement_entries
WHERE source_type IN ('loan_in', 'loan_repayment')
GROUP BY 1;

COMMENT ON VIEW public.v_loan_balance IS
    'Per-lender loan ledger (เงินยืม): borrowed - repaid = outstanding, grouped by the notes column. Source: bank_statement_entries rows tagged loan_in / loan_repayment. Uses loan_amount_override when a bank row is a mixed transfer (only part is the shop loan).';

-- Data fix applied same session (นุศรา, 5 slips reconciled against physical
-- slip images at C:\Users\rapee\Desktop\PJ-MARA\VPS-VEXONHQ\Money\):
--   2026-03-31  30,000  loan_in  (full, memo "ยืมจ่ายร้านหม่าล่า")
--   2026-04-30  15,000  loan_in  (full, memo "ยืม ร้านหม่าล่าค่าพนง")
--   2026-05-30  33,000 real transfer, loan_amount_override=18,000
--               (slip's own K+ "บันทึกช่วยจำ" note: "ร้านหม่าล่า 33000-15000"
--               -> 15,000 of the 33,000 is NOT the shop's; 18,000 is)
--   2026-06-29   6,000  loan_in  (full, memo "ยืมร้านหม่าล่า")
--   2026-06-30  20,000  loan_in  (full, memo "ยืมร้านหม่าล่า"; LINE-bot OCR
--               had misread it as "ยิ้ม" instead of "ยืม" -- OCR error only,
--               real slip text confirmed correct via image re-read)
-- Result: v_loan_balance นุศรา borrowed=89,000 repaid=0 outstanding=89,000 txn_count=5
