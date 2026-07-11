-- Read-only review pack for owner-driven category cleanup.
-- No UPDATE/INSERT/DELETE is intentionally present in this file.

-- 1. Monthly size of the two review buckets.
SELECT
    date_trunc('month', entry_date)::date AS month,
    COALESCE(category_code, '<NULL>') AS current_category,
    source,
    COUNT(*) AS row_count,
    SUM(amount)::numeric(14, 2) AS amount_total
FROM public.v_daybook_pnl
WHERE direction = 'expense'
  AND entry_date >= DATE '2026-01-01'
  AND (category_code = 'other_expense' OR category_code IS NULL)
GROUP BY 1, 2, 3
ORDER BY month DESC, amount_total DESC;

-- 2. Detail for TUM to review row by row before any future reclassification.
SELECT
    entry_date,
    amount,
    source,
    category_code,
    label,
    counterparty,
    branch_code,
    ref_id
FROM public.v_daybook_pnl
WHERE direction = 'expense'
  AND entry_date >= DATE '2026-01-01'
  AND (category_code = 'other_expense' OR category_code IS NULL)
ORDER BY entry_date DESC, amount DESC, source, ref_id;
