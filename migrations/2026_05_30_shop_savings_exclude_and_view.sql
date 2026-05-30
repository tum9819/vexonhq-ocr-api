-- ============================================================
-- VEXONHQ Migration 2026-05-30 — "ฝากเข้าร้าน" = transfer to savings, not an expense
-- APPLIED to prod via Supabase MCP 2026-05-30. Committed for repo<->prod parity.
-- ============================================================
-- TUM clarified: "ฝากเข้าร้าน" rows in pos_cashflow_entries are cash moved from the
-- till into the shop's SAVINGS account (เงินเก็บร้าน) — an internal transfer between
-- the shop's own accounts (asset -> asset). In accounting it is NOT a P&L expense (the
-- shop still has the money). Counting it as an expense understated profit (~79k) and
-- the taxable base. The money is still part of profit/equity — it has just been parked.
--
-- Fix 1: v_daybook Branch 4 emits source='pos_cash_deposit' (already in every P&L
--        exclusion list) for ฝากเข้าร้าน rows, so they drop out of v_daybook_pnl.
--        Effect verified: expense 885,055.96 -> 806,063.96 (-78,992), profit +78,992.
-- Fix 2: v_shop_savings view = how much has been set aside per month + cumulative
--        (so the owner sees the savings WITHOUT it polluting the P&L). Total = 78,992.
--
-- The matching identical clause must stay in any future v_daybook redefinition.
-- ============================================================

-- ── Fix 1: exclude ฝากเข้าร้าน from P&L (Branch 4 of v_daybook) ──
-- Full v_daybook redefinition (cash basis, AR fix, delivery dedup, bank rider exclusion)
-- with Branch 4 changed. Captured from the live def 2026-05-30.
CREATE OR REPLACE VIEW public.v_daybook AS
SELECT ps.sales_date AS entry_date, 'income'::text AS direction,
    GREATEST(0::numeric, ps.net_total - COALESCE(rd.rider_gross, 0::numeric)) AS amount,
    'pos_sale'::text AS source, NULL::text AS category_code,
    'POS ขายหน้าร้าน'::text AS label, NULL::text AS counterparty, ps.branch_code, ps.id::text AS ref_id
   FROM pos_sales_daily ps
     LEFT JOIN ( SELECT rider_deliveries.delivery_date, rider_deliveries.branch_code,
            sum(rider_deliveries.gross_sales) AS rider_gross
           FROM rider_deliveries GROUP BY rider_deliveries.delivery_date, rider_deliveries.branch_code) rd
       ON rd.delivery_date = ps.sales_date AND rd.branch_code = ps.branch_code
UNION ALL
 SELECT rd.delivery_date, 'income'::text, rd.net_payout, 'rider_income_grab'::text, 'delivery_income'::text,
    concat('ขาย grab (', rd.order_count, ' orders)'), 'Grab'::text, rd.branch_code, rd.id::text
   FROM rider_deliveries rd WHERE rd.platform = 'grab'::text
UNION ALL
 SELECT rd.delivery_date, 'income'::text, rd.net_payout, 'rider_income_lineman'::text, 'delivery_income'::text,
    concat('ขาย lineman (', rd.order_count, ' orders)'), 'Lineman'::text, rd.branch_code, rd.id::text
   FROM rider_deliveries rd WHERE rd.platform = 'lineman'::text
UNION ALL
-- Branch 4: POS cash-drawer. ฝากเข้าร้าน (drawer -> savings) = internal transfer, NOT expense.
 SELECT pce.txn_date, pce.direction, pce.amount,
    CASE WHEN pce.description ILIKE '%ฝากเข้าร้าน%' THEN 'pos_cash_deposit'::text ELSE 'pos_cashflow'::text END,
    pce.category_code, pce.description, NULL::text, pce.branch_code, pce.id::text
   FROM pos_cashflow_entries pce
UNION ALL
 SELECT p.payment_date,
    CASE ae.direction WHEN 'receivable'::text THEN 'income'::text ELSE 'expense'::text END,
    p.amount,
    CASE ae.direction WHEN 'receivable'::text THEN 'ar_payment'::text ELSE 'ap_payment'::text END,
    NULL::text,
    concat(CASE ae.direction WHEN 'receivable'::text THEN 'รับชำระ'::text ELSE 'จ่ายชำระ'::text END, ': ', ae.counterparty_name_snapshot),
    ae.counterparty_name_snapshot, 'thawi_watthana'::text, p.id::text
   FROM ar_ap_payments p JOIN ar_ap_entries ae ON ae.id = p.entry_id
UNION ALL
 SELECT me.entry_date, me.direction, me.amount, 'manual'::text, me.category_code, me.description,
    NULL::text, COALESCE(me.branch_code, 'thawi_watthana'::text), me.id::text
   FROM manual_entries me
UNION ALL
 SELECT bse.txn_date, bse.direction, bse.amount, bse.source_type, bse.category_code, bse.description,
    NULL::text, bse.branch_code, bse.id::text
   FROM bank_statement_entries bse
  WHERE bse.match_status <> 'needs_review'::text
    AND (bse.source_type <> ALL (ARRAY['rider_income_lineman'::text, 'rider_income_grab'::text]));

-- ── Fix 2: shop-savings tracker (เงินเก็บร้าน) — per month + cumulative ──
DROP VIEW IF EXISTS public.v_shop_savings;
CREATE VIEW public.v_shop_savings AS
WITH m AS (
  SELECT to_char(txn_date,'YYYY-MM') AS month, sum(amount) AS deposited, count(*) AS entries
  FROM public.pos_cashflow_entries
  WHERE direction='expense' AND description ILIKE '%ฝากเข้าร้าน%'
  GROUP BY 1
)
SELECT month, entries,
       round(deposited::numeric,0) AS deposited,
       round(sum(deposited) OVER (ORDER BY month)::numeric,0) AS cumulative_savings
FROM m ORDER BY month;
