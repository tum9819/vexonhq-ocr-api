-- =============================================================================
-- AUDIT: Expense rows that look like fixed costs but have NULL/unmatched category_code
-- Context: breakeven_routes.py uses INNER JOIN expense_categories WHERE is_fixed=true
--          so rows with NULL category_code are silently excluded from fixed costs.
-- Run this on Supabase SQL editor to find data gaps before relying on breakeven numbers.
-- =============================================================================

-- ── Part 1: Expenses with NULL category_code (all, last 90 days) ─────────────
-- These are COMPLETELY invisible to the breakeven calculation.
SELECT
    entry_date,
    source,
    label,
    amount,
    category_code,
    branch_code
FROM public.v_daybook
WHERE direction    = 'expense'
  AND category_code IS NULL
  AND entry_date   >= CURRENT_DATE - INTERVAL '90 days'
  AND source NOT IN (
      'owner_capital', 'owner_advance', 'transfer_error',
      'bank_statement', 'vendor_payment', 'grab_payout', 'lineman_payout',
      'pos_cash_deposit', 'cash_withdrawal', 'loan_in', 'loan_repayment'
  )
ORDER BY entry_date DESC, amount DESC;

-- ── Part 2: Expenses with NULL category_code that LOOK like fixed costs ───────
-- Filter by Thai keywords for rent, salary, utilities, etc.
SELECT
    entry_date,
    source,
    label,
    amount,
    category_code,
    branch_code
FROM public.v_daybook
WHERE direction    = 'expense'
  AND category_code IS NULL
  AND entry_date   >= CURRENT_DATE - INTERVAL '90 days'
  AND source NOT IN (
      'owner_capital', 'owner_advance', 'transfer_error',
      'bank_statement', 'vendor_payment', 'grab_payout', 'lineman_payout',
      'pos_cash_deposit', 'cash_withdrawal', 'loan_in', 'loan_repayment'
  )
  AND (
      label ILIKE '%เช่า%'
   OR label ILIKE '%เงินเดือน%'
   OR label ILIKE '%salary%'
   OR label ILIKE '%ค่าแรง%'
   OR label ILIKE '%ค่าไฟ%'
   OR label ILIKE '%ค่าน้ำ%'
   OR label ILIKE '%ค่าอินเทอร์%'
   OR label ILIKE '%internet%'
   OR label ILIKE '%ค่าธรรมเนียม%'
   OR label ILIKE '%ภาษี%'
   OR label ILIKE '%นักดนตรี%'
   OR label ILIKE '%ดนตรี%'
  )
ORDER BY entry_date DESC, amount DESC;

-- ── Part 3: Expenses with category_code that does NOT match any expense_categories row ──
-- These also fall through the INNER JOIN (misspelled or orphaned codes).
SELECT
    d.entry_date,
    d.source,
    d.label,
    d.amount,
    d.category_code,
    d.branch_code
FROM public.v_daybook d
LEFT JOIN public.expense_categories ec ON ec.code = d.category_code
WHERE d.direction    = 'expense'
  AND d.category_code IS NOT NULL
  AND ec.code IS NULL                -- no matching category row
  AND d.entry_date   >= CURRENT_DATE - INTERVAL '90 days'
  AND d.source NOT IN (
      'owner_capital', 'owner_advance', 'transfer_error',
      'bank_statement', 'vendor_payment', 'grab_payout', 'lineman_payout',
      'pos_cash_deposit', 'cash_withdrawal', 'loan_in', 'loan_repayment'
  )
ORDER BY d.category_code, d.entry_date DESC;

-- ── Part 4: Summary — total uncategorized expense amount per month (last 3 months) ──
-- Quick sanity check: how much ฿ is invisible to breakeven per month?
SELECT
    TO_CHAR(entry_date, 'YYYY-MM') AS month,
    COUNT(*)                        AS row_count,
    SUM(amount)                     AS total_amount
FROM public.v_daybook
WHERE direction    = 'expense'
  AND category_code IS NULL
  AND entry_date   >= CURRENT_DATE - INTERVAL '90 days'
  AND source NOT IN (
      'owner_capital', 'owner_advance', 'transfer_error',
      'bank_statement', 'vendor_payment', 'grab_payout', 'lineman_payout',
      'pos_cash_deposit', 'cash_withdrawal', 'loan_in', 'loan_repayment'
  )
GROUP BY TO_CHAR(entry_date, 'YYYY-MM')
ORDER BY month DESC;

-- ── Part 5: Current is_fixed mapping — verify what's marked ─────────────────
-- Cross-check which categories are currently flagged as fixed costs.
SELECT
    code,
    name_th,
    is_fixed,
    parent_code
FROM public.expense_categories
ORDER BY is_fixed DESC, code;
