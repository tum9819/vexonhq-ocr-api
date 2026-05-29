-- ============================================================
-- VEXONHQ Data-fix 2026-05-29 — Re-audit Batch 13 finding B2
-- Reclassify non-revenue bank credits mis-tagged as 'other_income'
-- APPLIED to prod 2026-05-29 via Supabase MCP. Committed for repo<->prod parity.
-- Idempotent: re-running is a no-op (rows no longer have source_type='other_income').
-- ============================================================
-- Problem: 18 bank_statement_entries credits were auto-classified source_type/
--   category_code = 'other_income' and counted as P&L income (v_daybook_pnl).
--   12 of them are NOT operating revenue:
--     - 4 rows / 43,500.00  = owner's own bank transfers (MR RAPEEPAT MANEER = TUM)
--                             -> owner_capital (equity, excluded from P&L)
--     - 8 rows / 34,526.98  = transfers from the sister entity 'ร้านสถานีหม่าล่า'
--                             (KB000001900737) -> transfer_error (internal, excluded)
--   Effect: P&L income 2,041,200.69 -> 1,963,173.71 (-78,026.98 overstated revenue removed).
--   The remaining 6 rows / 3,392.55 (transfers from individuals) are LEFT as income
--   pending TUM confirmation (likely genuine customer transfers).
-- Both owner_capital and transfer_error are already in every P&L exclusion list.
-- Reversible: SET source_type='other_income', category_code='other_income' for these ids.
-- ============================================================

UPDATE public.bank_statement_entries
SET source_type = 'owner_capital',
    category_code = 'owner_capital'
WHERE source_type = 'other_income'
  AND description ILIKE '%RAPEEPAT MANEER%';

UPDATE public.bank_statement_entries
SET source_type = 'transfer_error',
    category_code = 'transfer_error'
WHERE source_type = 'other_income'
  AND description ILIKE '%สถานีหม่าล่า%';
