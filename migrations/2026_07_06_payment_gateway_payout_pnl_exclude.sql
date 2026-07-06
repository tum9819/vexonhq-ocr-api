-- 2026_07_06_payment_gateway_payout_pnl_exclude.sql
-- Exclude payment_gateway_payout from P&L.
--
-- LINE PAY / QR / payment gateway bank inflows are settlement movements for
-- sales that should already be represented by POS/rider source rows. Counting
-- them as income again would double-count revenue.
--
-- Idempotent: CREATE OR REPLACE. Reversible by removing
-- payment_gateway_payout from this exclusion list and re-creating the view.

CREATE OR REPLACE VIEW public.v_daybook_pnl AS
SELECT *
FROM public.v_daybook
WHERE source NOT IN (
    'owner_capital', 'owner_advance', 'transfer_error',
    'bank_statement', 'vendor_payment',
    'grab_payout', 'lineman_payout', 'payment_gateway_payout',
    'pos_cash_deposit', 'cash_withdrawal',
    'loan_in', 'loan_repayment'
);

COMMENT ON VIEW public.v_daybook_pnl IS
    'P&L source of truth: v_daybook with owner-equity, transfer, loan, and payment-gateway settlement sources excluded. Use this for all profit/expense/income aggregates. Use v_daybook (raw) only for the full ledger / daybook list.';
