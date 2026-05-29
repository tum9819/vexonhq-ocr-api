-- ============================================================
-- VEXONHQ Migration 2026-05-29 — Fix v_daybook AR/AP direction sign bug
-- Run in Supabase SQL Editor (mara-ai-prod). DO NOT auto-apply — review first.
-- ============================================================
-- Re-audit Batch 13 finding A1:
--   Branch 5 (AR/AP payments) used `CASE ae.direction WHEN 'ar' ...`,
--   but ar_ap_entries.direction stores 'receivable' / 'payable' (CHECK
--   constraint, 07_phase3_arap_schema.sql:62). 'ar' NEVER matches, so EVERY
--   receivable (AR) collection fell to the ELSE branch and was booked as an
--   EXPENSE (source 'ap_payment', label 'จ่ายชำระ'). A 10,000 catering payment
--   RECEIVED would hit the P&L as a 10,000 expense (profit error = 2x the AR).
--   Currently LATENT: ar_ap_entries is empty in prod (0 rows), so no live data
--   is affected yet — but it would corrupt the P&L the moment AR/AP is used.
--
-- This migration is a faithful copy of the CURRENT LIVE v_daybook definition
-- (captured via pg_get_viewdef on 2026-05-29) with ONLY the three 'ar'->'receivable'
-- tokens changed in Branch 5. It therefore ALSO re-syncs the repo, which had
-- drifted: the live view already contains the delivery-dedup (Branch 1
-- GREATEST(net_total - rider_gross)) and the bank rider-income exclusion
-- (Branch 7) that were applied to prod but never committed as migrations.
-- migrations/17_vendor_bills_daybook.sql is STALE vs production.
--
-- Columns are unchanged, so CREATE OR REPLACE is safe and v_daybook_pnl
-- (which depends on v_daybook) is unaffected.
-- Verify before: SELECT direction, count(*) FROM ar_ap_entries GROUP BY 1;
--   -> must show only 'receivable'/'payable' (never 'ar'/'ap').
-- ============================================================

CREATE OR REPLACE VIEW public.v_daybook AS
-- Branch 1: POS daily sales (delivery gross removed to avoid double-count with riders)
SELECT ps.sales_date AS entry_date,
    'income'::text AS direction,
    GREATEST(0::numeric, ps.net_total - COALESCE(rd.rider_gross, 0::numeric)) AS amount,
    'pos_sale'::text AS source,
    NULL::text AS category_code,
    'POS ขายหน้าร้าน'::text AS label,
    NULL::text AS counterparty,
    ps.branch_code,
    ps.id::text AS ref_id
   FROM pos_sales_daily ps
     LEFT JOIN ( SELECT rider_deliveries.delivery_date,
            rider_deliveries.branch_code,
            sum(rider_deliveries.gross_sales) AS rider_gross
           FROM rider_deliveries
          GROUP BY rider_deliveries.delivery_date, rider_deliveries.branch_code) rd
       ON rd.delivery_date = ps.sales_date AND rd.branch_code = ps.branch_code
UNION ALL
-- Branch 2: Rider income — Grab
 SELECT rd.delivery_date AS entry_date,
    'income'::text AS direction,
    rd.net_payout AS amount,
    'rider_income_grab'::text AS source,
    'delivery_income'::text AS category_code,
    concat('ขาย grab (', rd.order_count, ' orders)') AS label,
    'Grab'::text AS counterparty,
    rd.branch_code,
    rd.id::text AS ref_id
   FROM rider_deliveries rd
  WHERE rd.platform = 'grab'::text
UNION ALL
-- Branch 3: Rider income — Lineman
 SELECT rd.delivery_date AS entry_date,
    'income'::text AS direction,
    rd.net_payout AS amount,
    'rider_income_lineman'::text AS source,
    'delivery_income'::text AS category_code,
    concat('ขาย lineman (', rd.order_count, ' orders)') AS label,
    'Lineman'::text AS counterparty,
    rd.branch_code,
    rd.id::text AS ref_id
   FROM rider_deliveries rd
  WHERE rd.platform = 'lineman'::text
UNION ALL
-- Branch 4: POS cashflow entries
 SELECT pce.txn_date AS entry_date,
    pce.direction,
    pce.amount,
    'pos_cashflow'::text AS source,
    pce.category_code,
    pce.description AS label,
    NULL::text AS counterparty,
    pce.branch_code,
    pce.id::text AS ref_id
   FROM pos_cashflow_entries pce
UNION ALL
-- Branch 5: AR/AP payments  -- FIX A1: 'ar' -> 'receivable' (stored value)
 SELECT p.payment_date AS entry_date,
        CASE ae.direction
            WHEN 'receivable'::text THEN 'income'::text
            ELSE 'expense'::text
        END AS direction,
    p.amount,
        CASE ae.direction
            WHEN 'receivable'::text THEN 'ar_payment'::text
            ELSE 'ap_payment'::text
        END AS source,
    NULL::text AS category_code,
    concat(
        CASE ae.direction
            WHEN 'receivable'::text THEN 'รับชำระ'::text
            ELSE 'จ่ายชำระ'::text
        END, ': ', ae.counterparty_name_snapshot) AS label,
    ae.counterparty_name_snapshot AS counterparty,
    'thawi_watthana'::text AS branch_code,
    p.id::text AS ref_id
   FROM ar_ap_payments p
     JOIN ar_ap_entries ae ON ae.id = p.entry_id
UNION ALL
-- Branch 6: Quick / manual entries
 SELECT me.entry_date,
    me.direction,
    me.amount,
    'manual'::text AS source,
    me.category_code,
    me.description AS label,
    NULL::text AS counterparty,
    COALESCE(me.branch_code, 'thawi_watthana'::text) AS branch_code,
    me.id::text AS ref_id
   FROM manual_entries me
UNION ALL
-- Branch 7: Bank statement entries (classified only; rider income excluded to avoid double-count)
 SELECT bse.txn_date AS entry_date,
    bse.direction,
    bse.amount,
    bse.source_type AS source,
    bse.category_code,
    bse.description AS label,
    NULL::text AS counterparty,
    bse.branch_code,
    bse.id::text AS ref_id
   FROM bank_statement_entries bse
  WHERE bse.match_status <> 'needs_review'::text
    AND (bse.source_type <> ALL (ARRAY['rider_income_lineman'::text, 'rider_income_grab'::text]))
UNION ALL
-- Branch 8: Vendor bills (confirmed only)
 SELECT vb.bill_date AS entry_date,
    'expense'::text AS direction,
    vb.amount,
    'vendor_bill'::text AS source,
    vb.category_code,
    COALESCE(vb.vendor_name, 'บิลซัพพลายเออร์'::text) AS label,
    vb.vendor_name AS counterparty,
    COALESCE(vb.branch_code, 'thawi_watthana'::text) AS branch_code,
    vb.id::text AS ref_id
   FROM vendor_bills vb
  WHERE vb.review_status = 'confirmed'::text AND vb.bill_date IS NOT NULL;
