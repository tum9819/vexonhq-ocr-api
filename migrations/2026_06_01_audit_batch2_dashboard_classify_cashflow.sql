-- 2026-06-01 — Executive-audit batch 2 (applied LIVE via Supabase MCP; this file is the repo record)
-- =============================================================================
-- AUD-PNL-01 (MEDIUM): public.v_dashboard_overview computed expense = SUM(vendor_bills
--   WHERE review_status='confirmed') ONLY — ignoring payroll/cash/bank expense — so it
--   showed a false 73-87% margin every month while the real cash-basis P&L (v_daybook_pnl)
--   is ~ -8%..+15%. No code reads it (repo grep = 0) and it had 0 DB dependents. Dropped.
DROP VIEW IF EXISTS public.v_dashboard_overview;

-- AUD-TAX-03 (MEDIUM): bank_statement_entries had no who/when audit columns, so a manual
--   reclassification (POST /classify/{entry_id}) left no trace. Add nullable audit columns;
--   classify_entry now writes classified_by = JWT sub, classified_at = now(). RLS already on.
ALTER TABLE public.bank_statement_entries
  ADD COLUMN IF NOT EXISTS classified_by text,
  ADD COLUMN IF NOT EXISTS classified_at timestamptz;
COMMENT ON COLUMN public.bank_statement_entries.classified_by IS 'Supabase sub (UUID) of the admin who last manually classified this row (audit AUD-TAX-03)';
COMMENT ON COLUMN public.bank_statement_entries.classified_at IS 'when the row was last manually classified (audit AUD-TAX-03)';

-- AUD-PNL-03 (LOW, data fix): 5 pos_cashflow rows were purchases booked as income
--   (inflating revenue + hiding cost; category_code NULL). Flipped to expense + assigned a
--   food-cost-subtree category. (DML — recorded here for the audit trail.)
UPDATE public.pos_cashflow_entries SET direction='expense', category_code='packaging'    WHERE id='2e8440ad-6793-4dbd-a89e-1c8716c6f6fd' AND direction='income'; -- กล่องใส่อาหาร
UPDATE public.pos_cashflow_entries SET direction='expense', category_code='raw_meat'     WHERE id='06960fc8-08e0-4c3e-b780-5095fd5055dc' AND direction='income'; -- ใส้2โล
UPDATE public.pos_cashflow_entries SET direction='expense', category_code='raw_oil_gas'  WHERE id='be7f87e6-3fad-438e-bfaf-995c65a6a8c3' AND direction='income'; -- แก๊ส
UPDATE public.pos_cashflow_entries SET direction='expense', category_code='raw_beverage' WHERE id='3881d692-af1f-4f55-9c6a-d8131af86401' AND direction='income'; -- น้ำเปล่า
UPDATE public.pos_cashflow_entries SET direction='expense', category_code='raw_beverage' WHERE id='10856e04-a5f7-45cf-97ab-be08dd0f07e6' AND direction='income'; -- น้ำแข็ง
