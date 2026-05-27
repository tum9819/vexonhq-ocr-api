-- 2026_05_27_v_daybook_pnl.sql
-- Audit batch 1 (P&L + Daybook) — shared P&L source-of-truth view.
--
-- Problem: the owner-equity / transfer exclusion list was copy-pasted into
-- 7+ query sites (pnl_routes.py, phase2_routes.py, phase10_narrative_routes.py,
-- yearly_routes.py, phase3_daybook_routes.py) and MISSING in several — the
-- root cause of audit findings C1, C3, C4, M4, M6 and the original Session-6
-- "negative expense" incident.
--
-- Fix: a single pre-filtered view. P&L queries read FROM v_daybook_pnl and no
-- longer need to remember the exclusion clause. v_daybook itself is unchanged
-- (raw ledger, includes equity) for ledger/daybook-list use.
--
-- Exclusion list mirrors pnl_routes.py:96-99 exactly so the new view is
-- behaviourally identical to the existing canonical P&L filter. Of these,
-- only owner_capital / owner_advance / transfer_error currently exist in
-- production data (verified 2026-05-27); the rest are defensive no-ops kept
-- so future bank_statement source_type values don't silently leak.
--
-- Idempotent: CREATE OR REPLACE. Additive only — does not touch v_daybook or
-- any base table. Reversible with DROP VIEW public.v_daybook_pnl.

CREATE OR REPLACE VIEW public.v_daybook_pnl AS
SELECT *
FROM public.v_daybook
WHERE source NOT IN (
    'owner_capital', 'owner_advance', 'transfer_error',
    'bank_statement', 'vendor_payment',
    'grab_payout', 'lineman_payout',
    'pos_cash_deposit', 'cash_withdrawal'
);

COMMENT ON VIEW public.v_daybook_pnl IS
    'P&L source of truth: v_daybook with owner-equity and transfer sources excluded. Use this for all profit/expense/income aggregates. Use v_daybook (raw) only for the full ledger / daybook list.';
