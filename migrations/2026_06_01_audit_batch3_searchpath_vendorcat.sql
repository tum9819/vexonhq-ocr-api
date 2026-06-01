-- 2026-06-01 — Executive-audit batch 3 (applied LIVE via Supabase MCP; repo record)
-- =============================================================================
-- CEO-SEC-04 (LOW): pin a fixed search_path on our 8 own functions to clear the
--   function_search_path_mutable advisor warnings. `public, pg_temp` keeps every
--   unqualified ref working (no behavior change). pg_trgm's gtrgm_* are extension-
--   owned and left alone. (Leaked-password protection = Supabase Auth dashboard toggle = TUM.)
ALTER FUNCTION public.fn_ar_ap_recompute_status()            SET search_path = public, pg_temp;
ALTER FUNCTION public.fn_expense_categories_touch_updated_at() SET search_path = public, pg_temp;
ALTER FUNCTION public.fn_manual_entries_touch_updated_at()  SET search_path = public, pg_temp;
ALTER FUNCTION public.fn_slips_set_updated_at()             SET search_path = public, pg_temp;
ALTER FUNCTION public.fn_store_context_touch()              SET search_path = public, pg_temp;
ALTER FUNCTION public.fn_vendor_bill_auto_ap()             SET search_path = public, pg_temp;
ALTER FUNCTION public.set_updated_at()                     SET search_path = public, pg_temp;
ALTER FUNCTION public.touch_updated_at()                   SET search_path = public, pg_temp;

-- AUD-DATA-02 (MEDIUM, data, conservative): all 101 vendor_bills had category_code NULL.
--   Categorize ONLY unambiguous vendors (beer dominates value ~419k; gas + musician clear).
--   Ambiguous grocery/wholesale (Makro/CP/7-11/B.B.Superstore/Wealimex) left NULL — honest-
--   unknown beats mis-tagged analytics. 29/101 set; 72 remain NULL (need line-item rollup /
--   manual). Safe: trg_vendor_bill_auto_ap fires only on review_status changes, not category.
UPDATE public.vendor_bills SET category_code = CASE
    WHEN vendor_name ILIKE '%singha%' OR vendor_name ILIKE '%เบียร์%' THEN 'beverage'
    WHEN vendor_name ILIKE '%gas%'    OR vendor_name ILIKE '%แก๊ส%'   THEN 'raw_oil_gas'
    WHEN vendor_name ILIKE '%นักดนตรี%'                                 THEN 'musician_fee'
  END
WHERE category_code IS NULL
  AND (vendor_name ILIKE '%singha%' OR vendor_name ILIKE '%เบียร์%'
       OR vendor_name ILIKE '%gas%' OR vendor_name ILIKE '%แก๊ส%'
       OR vendor_name ILIKE '%นักดนตรี%');

-- AUD-DATA-03 (LOW): 22 vendor_bills have an implausible OCR bill_date (mostly 2022-2023
--   misreads + 1 future 2026-08). NOT auto-corrected here — the real date needs human
--   knowledge and shifting a financial date changes which month the expense lands in.
--   Surfaced to the owner for manual correction in the UI. Detection query (for reference):
--     SELECT id, vendor_name, bill_date, amount FROM public.vendor_bills
--     WHERE bill_date IS NOT NULL AND (bill_date < DATE '2025-01-01' OR bill_date > DATE '2026-07-31')
--     ORDER BY bill_date;
