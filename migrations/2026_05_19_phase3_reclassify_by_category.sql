-- ════════════════════════════════════════════════════════════════════════════
-- 2026-05-19 — Phase 3: Reclassify bank_statement entries by category_code
-- ════════════════════════════════════════════════════════════════════════════
--
-- Context: After Phase 1 (P&L filter excluding bank_statement) + Phase 2
-- (built-in classifier patterns), we discovered that TUM had already
-- categorized many bank_statement_entries with meaningful category_codes
-- (beverage_raw, staff_salary, rent, food_raw, utility, other_expense,
-- other_income), but source_type was left as the catch-all 'bank_statement'.
-- The new P&L filter therefore dropped ~฿857k of legitimate expenses from
-- the books.
--
-- This migration promotes each category to its proper source_type so it
-- counts in P&L going forward. TUM confirmed scope: include ALL categories
-- (staff_salary, rent, food_raw, beverage_raw, utility, other_expense,
-- other_income). beverage_raw + food_raw carry double-count risk against
-- vendor_bill uploads — mitigated by the upcoming Invoice↔Statement
-- dedup workflow (Session 24+).
--
-- Safety: only touches rows still on source_type='bank_statement'. Any row
-- a TUM rule already moved out of that bucket is untouched.
-- ════════════════════════════════════════════════════════════════════════════

BEGIN;

-- ─── Staff salaries → payroll_expense ───────────────────────────────────
UPDATE public.bank_statement_entries
SET source_type = 'payroll_expense'
WHERE source_type = 'bank_statement'
  AND category_code = 'staff_salary';

-- ─── Rent → rent_expense ────────────────────────────────────────────────
UPDATE public.bank_statement_entries
SET source_type = 'rent_expense'
WHERE source_type = 'bank_statement'
  AND category_code = 'rent';

-- ─── Raw beverages (Singha beer etc.) → vendor_purchase ─────────────────
-- TUM accepted potential double-count vs. vendor_bills because the
-- monthly "how many ลัง of beer did we buy" report needs this data.
-- Future dedup workflow (Session 24) will mark matched rows as
-- vendor_payment so they stop double-counting once the invoice arrives.
UPDATE public.bank_statement_entries
SET source_type = 'vendor_purchase'
WHERE source_type = 'bank_statement'
  AND category_code = 'beverage_raw';

-- ─── Raw food (vegetables, meat, etc.) → vendor_purchase ────────────────
UPDATE public.bank_statement_entries
SET source_type = 'vendor_purchase'
WHERE source_type = 'bank_statement'
  AND category_code = 'food_raw';

-- ─── Utility bills not caught by Phase 2 patterns → utility_expense ─────
UPDATE public.bank_statement_entries
SET source_type = 'utility_expense'
WHERE source_type = 'bank_statement'
  AND category_code = 'utility';

-- ─── Miscellaneous business expense → other_expense ─────────────────────
UPDATE public.bank_statement_entries
SET source_type = 'other_expense'
WHERE source_type = 'bank_statement'
  AND category_code = 'other_expense';

-- ─── Miscellaneous business income → other_income ───────────────────────
UPDATE public.bank_statement_entries
SET source_type = 'other_income'
WHERE source_type = 'bank_statement'
  AND category_code = 'other_income';

-- ════════════════════════════════════════════════════════════════════════
-- Preview the result. Rows still with source_type='bank_statement' AND
-- a non-null category_code are intentionally left for TUM to decide
-- (e.g. owner draws, transfers to friends, etc.).
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

-- Sanity: rows still uncategorized in bank_statement (TUM's review queue).
SELECT
  COUNT(*) AS still_unclassified,
  SUM(debit)::numeric(12,2) AS total_debit_uncategorized
FROM public.bank_statement_entries
WHERE source_type = 'bank_statement'
  AND debit > 0
  AND (category_code IS NULL OR category_code = '');

COMMIT;
-- ROLLBACK;  -- uncomment + re-run if the preview looks wrong
