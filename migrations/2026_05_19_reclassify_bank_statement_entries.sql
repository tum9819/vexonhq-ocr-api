-- ════════════════════════════════════════════════════════════════════════════
-- 2026-05-19 — Re-classify existing bank_statement_entries by description
-- ════════════════════════════════════════════════════════════════════════════
--
-- Why: Until today, every imported row that didn't match a DB rule defaulted
-- to source_type='bank_statement' and flowed into v_daybook as either an
-- income or expense — even when the underlying business event had already
-- been recorded via vendor_bills, rider_deliveries, or pos_bills. That
-- caused double-counting in P&L (visible in /scorecard, /pnl, /budget).
--
-- The Python classifier in phase12_bank_statement_routes.py was extended
-- with built-in patterns for delivery payouts, utilities, taxes, payroll,
-- bank fees, and cash withdrawals. Future imports use those automatically.
--
-- This migration retroactively re-classifies existing rows the same way,
-- so historical P&L numbers immediately reflect the corrected accounting.
--
-- Safety:
--   • Only touches rows where source_type = 'bank_statement' (the catch-all)
--     so any row a TUM-managed rule already classified is untouched.
--   • One transaction; ROLLBACK if anything looks off in the preview at end.
-- ════════════════════════════════════════════════════════════════════════════

BEGIN;

-- ─── 1. Delivery platform payouts (income, "lineman" / "grab" tags) ─────
UPDATE public.bank_statement_entries
SET source_type   = 'lineman_payout',
    category_code = COALESCE(category_code, 'delivery_lineman')
WHERE source_type = 'bank_statement'
  AND credit > 0
  AND (description ILIKE '%lineman%'
    OR description ILIKE '%lmn%'
    OR description ILIKE '%ไลน์แมน%');

UPDATE public.bank_statement_entries
SET source_type   = 'grab_payout',
    category_code = COALESCE(category_code, 'delivery_grab')
WHERE source_type = 'bank_statement'
  AND credit > 0
  AND (description ILIKE '%grab%'
    OR description ILIKE '%กราบ%');

-- ─── 2. POS cash deposit (income) ───────────────────────────────────────
UPDATE public.bank_statement_entries
SET source_type   = 'pos_cash_deposit',
    category_code = COALESCE(category_code, 'pos_cash')
WHERE source_type = 'bank_statement'
  AND credit > 0
  AND (description ILIKE '%cash dep%'
    OR description ILIKE '%cdm%'
    OR description ILIKE '%นำฝากเงินสด%'
    OR description ILIKE '%เงินสด%'
    OR description ILIKE '%เงินฝาก%');

-- ─── 3. Utility expenses (electricity, water, telecom) ──────────────────
UPDATE public.bank_statement_entries
SET source_type   = 'utility_expense',
    category_code = COALESCE(category_code, 'utility_electricity')
WHERE source_type = 'bank_statement'
  AND debit > 0
  AND (description ILIKE '%mea%'
    OR description ILIKE '%pea%'
    OR description ILIKE '%การไฟฟ้า%'
    OR description ILIKE '%ค่าไฟ%');

UPDATE public.bank_statement_entries
SET source_type   = 'utility_expense',
    category_code = COALESCE(category_code, 'utility_water')
WHERE source_type = 'bank_statement'
  AND debit > 0
  AND (description ILIKE '%mwa%'
    OR description ILIKE '%pwa%'
    OR description ILIKE '%การประปา%'
    OR description ILIKE '%ค่าน้ำ%');

UPDATE public.bank_statement_entries
SET source_type   = 'utility_expense',
    category_code = COALESCE(category_code, 'utility_telecom')
WHERE source_type = 'bank_statement'
  AND debit > 0
  AND (description ILIKE '%ais%'
    OR description ILIKE '%true%'
    OR description ILIKE '%dtac%'
    OR description ILIKE '% tot %'  -- spaces so it doesn't match "total"
    OR description ILIKE '%3bb%'
    OR description ILIKE '%internet%'
    OR description ILIKE '%อินเตอร์เน็ต%');

-- ─── 4. Bank fees ───────────────────────────────────────────────────────
UPDATE public.bank_statement_entries
SET source_type   = 'bank_fee',
    category_code = COALESCE(category_code, 'bank_fee')
WHERE source_type = 'bank_statement'
  AND debit > 0
  AND (description ILIKE '%ค่าธรรมเนียม%'
    OR description ILIKE '%bnk chrg%'
    OR description ILIKE '%bank fee%'
    OR description ILIKE '%ค่าธรรม%');

-- ─── 5. Tax payments ────────────────────────────────────────────────────
UPDATE public.bank_statement_entries
SET source_type   = 'tax_expense',
    category_code = COALESCE(category_code, 'tax')
WHERE source_type = 'bank_statement'
  AND debit > 0
  AND (description ILIKE '%ภาษี%'
    OR description ILIKE '%revenue dept%'
    OR description ILIKE '%สรรพากร%');

-- ─── 6. Payroll ─────────────────────────────────────────────────────────
UPDATE public.bank_statement_entries
SET source_type   = 'payroll_expense',
    category_code = COALESCE(category_code, 'payroll')
WHERE source_type = 'bank_statement'
  AND debit > 0
  AND (description ILIKE '%payroll%'
    OR description ILIKE '%salary%'
    OR description ILIKE '%เงินเดือน%');

-- ─── 7. ATM cash withdrawal (neutral money movement) ────────────────────
-- Cash withdrawn is later spent via manual_entries / pos_cashflow / vendor_bills
-- so leaving it here would double-count.
UPDATE public.bank_statement_entries
SET source_type   = 'cash_withdrawal'
WHERE source_type = 'bank_statement'
  AND debit > 0
  AND (description ILIKE '%atm%'
    OR description ILIKE '%ถอนเงิน%'
    OR description ILIKE '%ถอน%');

-- ════════════════════════════════════════════════════════════════════════
-- Preview the result before committing — make sure the new classification
-- looks reasonable.
-- ════════════════════════════════════════════════════════════════════════
SELECT
  source_type,
  COUNT(*) FILTER (WHERE debit  > 0) AS expense_rows,
  COUNT(*) FILTER (WHERE credit > 0) AS income_rows,
  SUM(debit)::numeric(12,2)         AS total_debit,
  SUM(credit)::numeric(12,2)        AS total_credit
FROM public.bank_statement_entries
GROUP BY source_type
ORDER BY (SUM(debit) + SUM(credit)) DESC NULLS LAST;

-- If the preview looks right (most rows reclassified away from
-- 'bank_statement'), commit. If it looks wrong, ROLLBACK instead.

COMMIT;
-- ROLLBACK;  -- uncomment this and re-run if the preview shows mis-classifications
