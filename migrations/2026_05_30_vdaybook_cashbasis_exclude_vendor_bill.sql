-- ============================================================
-- VEXONHQ Migration 2026-05-30 — Cash/statement-basis P&L (re-audit Batch 13, B4 + policy)
-- APPLIED to prod via Supabase MCP 2026-05-30. Committed for repo<->prod parity.
-- ============================================================
-- POLICY DECISION (TUM, 2026-05-30): the P&L uses CASH / BANK-STATEMENT basis.
--   Expense = actual money out = bank statement debits + POS cash-drawer (pos_cashflow)
--   + payroll/rent/utility + manual entries. The OCR'd supplier invoice (vendor_bill)
--   is NOT a separate P&L expense — it is kept for AP tracking + line-item detail +
--   slip/statement matching (the remark), but the EXPENSE is recognised when the money
--   actually leaves (statement/cash), exactly once.
--
-- Why: counting BOTH the OCR invoice (vendor_bill) AND its bank/cash payment double-counted
--   supplier cost. Re-audit B4 measured 204,295 of vendor_purchase overlapping a vendor_bill;
--   and ~290k of vendor_bill carried pre-operating-period dates (2022-2023 / early-2025) =
--   bad/old data polluting the ledger. Keeping vendor_bill made total expense ~2.20M vs
--   income ~1.96M (implausible loss); dropping it gives ~1.50M expense (~23% net margin,
--   realistic for a restaurant). This is standard small-business cash-basis accounting.
--
-- Implementation: remove Branch 8 (vendor_bill) from v_daybook. Every consumer
--   (v_daybook_pnl + all inline-exclusion queries) reads v_daybook, so this one change
--   removes vendor_bill from the P&L consistently with no code changes. Columns unchanged,
--   so CREATE OR REPLACE is safe; v_daybook_pnl (depends on v_daybook) is unaffected.
--   Includes the A1 AR fix (Branch 5 'receivable'). REVERSIBLE: re-add Branch 8.
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
-- Branch 5: AR/AP payments (A1 fix: 'receivable' is the stored value)
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
    AND (bse.source_type <> ALL (ARRAY['rider_income_lineman'::text, 'rider_income_grab'::text]));
-- Branch 8 (vendor_bill) intentionally REMOVED — see policy header (cash basis).
-- vendor_bills remain available for AP / invoice detail / statement matching.
