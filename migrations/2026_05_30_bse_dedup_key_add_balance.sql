-- ============================================================
-- VEXONHQ Migration 2026-05-30 — bank_statement_entries dedup key + balance
-- Re-audit Batch 13 finding B6-C4 (confirmed live 2026-05-30).
-- ============================================================
-- Problem: the import dedup constraint uq_bse_txn = UNIQUE(txn_date, description,
--   debit, credit, branch_code) drops genuinely-distinct transactions that happen to
--   share the same day + amount + (truncated) description. Found in June 2025: TWO
--   2,100.00 transfers to "น.ส. นุศรา ปราง" on 2025-06-09 collapsed into ONE row, so
--   the bank import showed 43 withdrawals / 361,025.72 vs the statement's own
--   รวมถอน checksum of 44 / 363,125.72 (missing exactly 2,100).
--
-- Fix: include the running BALANCE in the unique key. The rewritten parser
--   (2026-05-30) now stores the real running balance, which is unique per transaction
--   within an account, so two identical-looking transfers are kept while a re-upload
--   of the same file still dedups (identical balances). The matching ON CONFLICT in
--   phase12_bank_statement_routes.py is updated to (txn_date, description, debit,
--   credit, balance, branch_code).
--
-- COORDINATION: apply this together with the code deploy, with NO statement import in
--   between — the old code's ON CONFLICT (without balance) needs the old constraint,
--   and the new code's ON CONFLICT (with balance) needs this new one. Sequence used:
--   deploy code -> apply this migration -> re-import/insert June.
-- The new key is strictly more permissive than the old, so existing rows satisfy it.
-- ============================================================

ALTER TABLE public.bank_statement_entries DROP CONSTRAINT IF EXISTS uq_bse_txn;

ALTER TABLE public.bank_statement_entries
  ADD CONSTRAINT uq_bse_txn
  UNIQUE (txn_date, description, debit, credit, balance, branch_code);
