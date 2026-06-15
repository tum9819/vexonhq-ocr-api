-- F-DASH-2 (2026-06-15): v_invoice_due_soon was filtering the legacy `status`
-- column (never transitioned out of 'unpaid') and aliasing it misleadingly as
-- payment_status, with NO lower date bound -> it returned already-paid bills and
-- long-overdue bills (32 rows live, of which the genuinely due-soon count was 0).
--
-- Fix: filter the real `payment_status`, add `review_status='confirmed'`, add a
-- lower bound (due_date >= today) and use Bangkok date to match the executive
-- dashboard AP cards (phase2_routes.py _EXEC_METRICS_SQL ap_due_7d).
--
-- Applied to prod via Supabase migration `fix_v_invoice_due_soon_use_payment_status`.
-- No production code consumes this view (orphan); the dashboard "ครบกำหนด 7 วัน"
-- card uses its own inline SQL. This rewrite is defensive correctness.

CREATE OR REPLACE VIEW public.v_invoice_due_soon AS
SELECT
    vb.id,
    vb.vendor_name,
    vb.invoice_no,
    vb.amount,
    vb.due_date,
    vb.payment_status,
    vb.due_date - (now() AT TIME ZONE 'Asia/Bangkok')::date AS days_until_due
FROM public.vendor_bills vb
WHERE vb.review_status = 'confirmed'
  AND vb.payment_status = 'unpaid'
  AND vb.due_date IS NOT NULL
  AND vb.due_date >= (now() AT TIME ZONE 'Asia/Bangkok')::date
  AND vb.due_date <= (now() AT TIME ZONE 'Asia/Bangkok')::date + 7
ORDER BY vb.due_date;

-- Rollback (previous definition):
-- CREATE OR REPLACE VIEW public.v_invoice_due_soon AS
-- SELECT id, vendor_name, invoice_no, amount, due_date,
--        status AS payment_status,
--        due_date - CURRENT_DATE AS days_until_due
--   FROM vendor_bills vb
--  WHERE review_status = 'confirmed'
--    AND status = ANY (ARRAY['unpaid','scheduled'])
--    AND due_date IS NOT NULL
--    AND due_date <= (CURRENT_DATE + '7 days'::interval)
--  ORDER BY due_date;
