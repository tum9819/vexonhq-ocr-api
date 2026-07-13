-- 2026-07-13: correction record for the `beverage` label.
--
-- Original FA-019 audit checked only the P&L/bank-statement pipeline and wrongly
-- treated `beverage` as unused. FA-020 corrected that: vendor_bills.category_code
-- is a separate OCR/vendor-invoice pipeline, and SINGHA BEER vendor bills actively
-- use `beverage`.
--
-- This file is kept as the repo history for the correction already applied live
-- via Supabase migration `fix_beverage_label_correction`. Do not treat its
-- presence as a signal to rerun production migration automatically; if it is
-- rerun, the statement is idempotent and sets the same label. It is label-only
-- and does not rewrite transactions.
UPDATE public.expense_categories
SET name_th = 'เครื่องดื่ม (บิลซื้อจากผู้ขาย)'
WHERE code = 'beverage';
