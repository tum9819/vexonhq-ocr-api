-- ============================================================
-- VEXONHQ Migration 17 — Add vendor_bills back to v_daybook
-- Run in Supabase SQL Editor
-- ============================================================
-- Background: Migration 16 (bank_statement) rewrote v_daybook
-- and accidentally dropped the vendor_bills UNION branch.
-- This migration adds it back as Branch 8.
-- ============================================================

-- DROP and recreate v_daybook to add Branch 8 (vendor_bills)
-- NOTE: Must DROP first (cannot reduce columns with CREATE OR REPLACE)

DROP VIEW IF EXISTS public.v_daybook CASCADE;

CREATE VIEW public.v_daybook AS

-- Branch 1: POS daily sales
SELECT
    ps.sales_date          AS entry_date,
    'income'               AS direction,
    ps.net_total           AS amount,
    'pos_sale'             AS source,
    NULL                   AS category_code,
    'POS ขายหน้าร้าน'      AS label,
    NULL                   AS counterparty,
    ps.branch_code,
    ps.id::text            AS ref_id
FROM public.pos_sales_daily ps

UNION ALL

-- Branch 2: Rider income — Grab
SELECT
    rd.delivery_date       AS entry_date,
    'income'               AS direction,
    rd.net_payout          AS amount,
    'rider_income_grab'    AS source,
    'delivery_income'      AS category_code,
    CONCAT('ขาย grab (', rd.order_count, ' orders)') AS label,
    'Grab'                 AS counterparty,
    rd.branch_code,
    rd.id::text            AS ref_id
FROM public.rider_deliveries rd
WHERE rd.platform = 'grab'

UNION ALL

-- Branch 3: Rider income — Lineman
SELECT
    rd.delivery_date       AS entry_date,
    'income'               AS direction,
    rd.net_payout          AS amount,
    'rider_income_lineman' AS source,
    'delivery_income'      AS category_code,
    CONCAT('ขาย lineman (', rd.order_count, ' orders)') AS label,
    'Lineman'              AS counterparty,
    rd.branch_code,
    rd.id::text            AS ref_id
FROM public.rider_deliveries rd
WHERE rd.platform = 'lineman'

UNION ALL

-- Branch 4: POS cashflow entries
SELECT
    pce.txn_date           AS entry_date,
    pce.direction          AS direction,
    pce.amount             AS amount,
    'pos_cashflow'         AS source,
    pce.category_code      AS category_code,
    pce.description        AS label,
    NULL                   AS counterparty,
    pce.branch_code,
    pce.id::text           AS ref_id
FROM public.pos_cashflow_entries pce

UNION ALL

-- Branch 5: AR/AP payments
SELECT
    p.payment_date         AS entry_date,
    CASE ae.direction
        WHEN 'ar' THEN 'income'
        ELSE 'expense'
    END                    AS direction,
    p.amount               AS amount,
    CASE ae.direction
        WHEN 'ar' THEN 'ar_payment'
        ELSE 'ap_payment'
    END                    AS source,
    NULL                   AS category_code,
    CONCAT(
        CASE ae.direction WHEN 'ar' THEN 'รับชำระ' ELSE 'จ่ายชำระ' END,
        ': ', ae.counterparty_name_snapshot
    )                      AS label,
    ae.counterparty_name_snapshot AS counterparty,
    'thawi_watthana'       AS branch_code,
    p.id::text             AS ref_id
FROM public.ar_ap_payments p
JOIN public.ar_ap_entries ae ON ae.id = p.entry_id

UNION ALL

-- Branch 6: Quick / manual entries
SELECT
    me.entry_date          AS entry_date,
    me.direction           AS direction,
    me.amount              AS amount,
    'manual'               AS source,
    me.category_code       AS category_code,
    me.description         AS label,
    NULL                   AS counterparty,
    COALESCE(me.branch_code, 'thawi_watthana') AS branch_code,
    me.id::text            AS ref_id
FROM public.manual_entries me

UNION ALL

-- Branch 7: Bank statement entries (classified only)
SELECT
    bse.txn_date           AS entry_date,
    bse.direction          AS direction,
    bse.amount             AS amount,
    bse.source_type        AS source,
    bse.category_code      AS category_code,
    bse.description        AS label,
    NULL                   AS counterparty,
    bse.branch_code,
    bse.id::text           AS ref_id
FROM public.bank_statement_entries bse
WHERE bse.match_status != 'needs_review'

UNION ALL

-- Branch 8: Vendor bills (confirmed only)
-- These are supplier invoices scanned via OCR (Makro, CP, etc.)
SELECT
    vb.bill_date                              AS entry_date,
    'expense'                                 AS direction,
    vb.amount                                 AS amount,
    'vendor_bill'                             AS source,
    vb.category_code                          AS category_code,
    COALESCE(vb.vendor_name, 'บิลซัพพลายเออร์') AS label,
    vb.vendor_name                            AS counterparty,
    COALESCE(vb.branch_code, 'thawi_watthana') AS branch_code,
    vb.id::text                               AS ref_id
FROM public.vendor_bills vb
WHERE vb.review_status = 'confirmed'
  AND vb.bill_date IS NOT NULL;
