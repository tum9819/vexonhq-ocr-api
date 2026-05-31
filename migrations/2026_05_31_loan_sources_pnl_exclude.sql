-- 2026_05_31_loan_sources_pnl_exclude.sql
-- Add loan_in / loan_repayment to the P&L exclusion list.
-- A loan is a financing/liability event, never income or expense — both legs
-- must be excluded from profit (same class as owner_capital / owner_advance).
-- Idempotent: CREATE OR REPLACE. Reversible by restoring the prior list.
-- NOTE: keep this list in sync with the inline lists in pnl_routes.py and
-- cashflow_routes.py. See AGENTS.md #20.

CREATE OR REPLACE VIEW public.v_daybook_pnl AS
SELECT *
FROM public.v_daybook
WHERE source NOT IN (
    'owner_capital', 'owner_advance', 'transfer_error',
    'bank_statement', 'vendor_payment',
    'grab_payout', 'lineman_payout',
    'pos_cash_deposit', 'cash_withdrawal',
    'loan_in', 'loan_repayment'
);

COMMENT ON VIEW public.v_daybook_pnl IS
    'P&L source of truth: v_daybook with owner-equity, transfer, and loan sources excluded. Use this for all profit/expense/income aggregates. Use v_daybook (raw) only for the full ledger / daybook list.';
