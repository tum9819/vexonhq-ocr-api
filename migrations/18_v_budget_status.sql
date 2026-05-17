-- ============================================================
-- Migration 18: v_budget_status view (Session 16, 2026-05-17)
-- ============================================================
-- Restores the budget status view that the dashboard + LINE bot
-- alerts depend on. The view computes per-category actual spend
-- (from v_daybook expense rows) against budget_targets, exposing
-- budget_amount, actual_amount, variance, pct_used, and a status
-- bucket (ok / warning / over).
--
-- Status thresholds:
--   over    — actual >= 100% of budget   (🔴 red alert)
--   warning — actual >=  80% of budget   (🟡 amber alert)
--   ok      — actual <   80% of budget   (🟢 green)
--
-- Excludes owner-equity flows so the actual spend reflects real
-- operating expenses only.
-- ============================================================

DROP VIEW IF EXISTS public.v_budget_status CASCADE;

CREATE OR REPLACE VIEW public.v_budget_status AS
WITH actual_by_cat AS (
    SELECT
        to_char(d.entry_date, 'YYYY-MM') AS month,
        COALESCE(d.branch_code, 'thawi_watthana') AS branch_code,
        d.category_code,
        SUM(d.amount)::numeric AS actual_amount
    FROM public.v_daybook d
    WHERE d.direction = 'expense'
      AND d.category_code IS NOT NULL
      AND d.source NOT IN ('owner_capital', 'owner_advance', 'transfer_error')
    GROUP BY 1, 2, 3
)
SELECT
    b.month,
    b.branch_code,
    b.category_code,
    COALESCE(ec.name_th, b.category_code)             AS category_name_th,
    COALESCE(ec.name_en, b.category_code)             AS category_name_en,
    b.amount::numeric                                 AS budget_amount,
    COALESCE(a.actual_amount, 0)::numeric             AS actual_amount,
    (b.amount - COALESCE(a.actual_amount, 0))::numeric AS variance,
    CASE
        WHEN b.amount > 0
            THEN ROUND((COALESCE(a.actual_amount, 0) / b.amount * 100)::numeric, 2)
        ELSE NULL
    END                                               AS pct_used,
    CASE
        WHEN b.amount <= 0                                       THEN 'ok'
        WHEN COALESCE(a.actual_amount, 0) >= b.amount            THEN 'over'
        WHEN COALESCE(a.actual_amount, 0) >= b.amount * 0.80     THEN 'warning'
        ELSE                                                          'ok'
    END                                               AS status
FROM public.budget_targets b
LEFT JOIN public.expense_categories ec
       ON ec.code = b.category_code
LEFT JOIN actual_by_cat a
       ON a.month = b.month
      AND a.branch_code = b.branch_code
      AND a.category_code = b.category_code;

COMMENT ON VIEW public.v_budget_status IS
    'Per-month per-category budget vs actual. Used by /budget/status, /dashboard/overview, daily_budget_alert (LINE).';

-- Grants — match other public.* views
GRANT SELECT ON public.v_budget_status TO anon, authenticated, service_role;

-- ============================================================
-- Smoke-test query (run after migration to verify)
-- ============================================================
-- SELECT month, category_name_th, budget_amount, actual_amount, pct_used, status
-- FROM public.v_budget_status
-- WHERE month = to_char(CURRENT_DATE, 'YYYY-MM')
-- ORDER BY pct_used DESC NULLS LAST;
