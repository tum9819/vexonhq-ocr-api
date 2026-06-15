-- STEP 3: Vendor Name Normalization (P1 - Optional, lower priority)
--
-- ⚠️ CRITICAL WARNING: These scripts have unique constraint issues. Do NOT auto-run.
-- Use for analysis only. Manual review required before any UPDATE.
--
-- PURPOSE: Consolidate duplicate vendor names with different spellings/cases
-- IMPACT: Improves reporting accuracy and analytics
--
-- Constraint Risk: The database has a unique constraint on (vendor_name, invoice_no).
-- Auto-consolidation can create duplicate key violations and CORRUPT VENDOR DATA.
-- Test each vendor group separately and review conflicts BEFORE updating.
-- Any destructive UPDATE blocks have been DISABLED and COMMENTED OUT below.
--
-- ============================================================
-- Query 1: Find duplicate vendors with similar names
-- ============================================================
SELECT
    vendor_name,
    COUNT(*) as invoice_count,
    SUM(amount) as total_amount,
    MIN(created_at)::date as earliest_invoice,
    MAX(created_at)::date as latest_invoice
FROM public.vendor_bills
WHERE vendor_name IS NOT NULL
GROUP BY vendor_name
ORDER BY invoice_count DESC;


-- ============================================================
-- Query 2: Find potential duplicates (case-insensitive)
-- ============================================================
SELECT
    LOWER(TRIM(vendor_name)) as normalized_name,
    COUNT(DISTINCT vendor_name) as variant_count,
    STRING_AGG(DISTINCT vendor_name, ' | ') as variants,
    COUNT(*) as total_invoices,
    SUM(amount) as total_amount
FROM public.vendor_bills
WHERE vendor_name IS NOT NULL
GROUP BY LOWER(TRIM(vendor_name))
HAVING COUNT(DISTINCT vendor_name) > 1
ORDER BY total_invoices DESC;


-- ============================================================
-- Query 3: Check for vendor names with extra spaces
-- ============================================================
SELECT
    vendor_name,
    LENGTH(vendor_name) as name_length,
    COUNT(*) as count
FROM public.vendor_bills
WHERE vendor_name IS NOT NULL AND (
    vendor_name LIKE ' %' OR
    vendor_name LIKE '% ' OR
    vendor_name LIKE '%  %'
)
GROUP BY vendor_name
ORDER BY count DESC;


-- ============================================================
-- NORMALIZATION OPTIONS
-- ============================================================
-- Below are examples. Adjust the mapping based on YOUR vendor list.
-- Run Query 2 first to see all duplicates, then edit the mapping.

-- OPTION A: Simple trim + lowercase normalization [DISABLED]
-- ⚠️ DANGEROUS: This destroys official Thai/English vendor names.
-- Example: "บริษัท ซีพี แอ็กซ์ตร้า" (Thai company name)
-- would be corrupted if lowercase normalization applied
-- (lowercase breaks Thai legal name requirements and readability)
--
-- Also risks unique constraint (vendor_name, invoice_no) conflicts.
--
-- DO NOT RUN THIS. Use manual review + selective CASE mapping instead.


-- OPTION B: Custom mapping (RECOMMENDED)
-- Map known vendor spelling variations to canonical form
-- Edit the CASE statement with your actual vendor names
--
-- ⚠️ DISABLED: The destructive UPDATE block below risks unique constraint violations.
-- Before running: manually verify each vendor mapping, test on a staging copy,
-- and confirm no duplicate (vendor_name, invoice_no) pairs would result.
--
-- Preview query (SAFE - read-only):
-- ⚠️ IMPORTANT: Only maps EXPLICIT variants. Do NOT add broad ELSE clause
-- Unmapped vendors are left unchanged to preserve legal names
BEGIN;
  SELECT
    id,
    vendor_name,
    CASE
      WHEN vendor_name IN ('บริษัท ABC', 'ABC', 'abc') THEN 'ABC Co., Ltd.'
      WHEN vendor_name IN ('ซัพพลาย XYZ', 'XYZ Supply', 'xyz') THEN 'XYZ Supply'
      WHEN vendor_name IN ('โรงแรม 123', 'Hotel 123') THEN 'Hotel 123'
      -- ELSE clause removed — DO NOT add fallback normalization
      ELSE vendor_name  -- Keep unmapped names unchanged
    END as normalized_name
  FROM public.vendor_bills
  WHERE vendor_name IS NOT NULL
  LIMIT 20;
ROLLBACK;

-- DESTRUCTIVE UPDATE (COMMENTED OUT - DO NOT RUN WITHOUT EXPLICIT REVIEW):
-- IMPORTANT: Before uncommenting and running:
-- 1. Review the CASE statement and customize for YOUR vendor list
-- 2. Test the preview query above first
-- 3. Verify no duplicate (vendor_name, invoice_no) pairs would result
-- 4. Back up the database (snapshot or export)
-- 5. Run on a staging environment first
--
-- Once approved, uncomment these lines:
-- ⚠️ SAFETY: CASE statement must ONLY include explicitly mapped vendors.
-- Do NOT add broad ELSE clause that applies LOWER(TRIM()) to unmapped names.
--
-- BEGIN;
--   UPDATE public.vendor_bills
--   SET vendor_name = CASE
--       WHEN vendor_name IN ('บริษัท ABC', 'ABC', 'abc') THEN 'ABC Co., Ltd.'
--       WHEN vendor_name IN ('ซัพพลาย XYZ', 'XYZ Supply', 'xyz') THEN 'XYZ Supply'
--       WHEN vendor_name IN ('โรงแรม 123', 'Hotel 123') THEN 'Hotel 123'
--       ELSE vendor_name  -- Keep unmapped names unchanged
--     END
--   WHERE vendor_name IN (  -- Restrict to rows that match at least one mapping
--     'บริษัท ABC', 'ABC', 'abc',
--     'ซัพพลาย XYZ', 'XYZ Supply', 'xyz',
--     'โรงแรม 123', 'Hotel 123'
--   );
--
--   SELECT COUNT(*) as rows_updated FROM public.vendor_bills;
-- COMMIT;


-- ============================================================
-- POST-NORMALIZATION VERIFICATION
-- ============================================================

-- Check for remaining duplicates:
SELECT
    LOWER(TRIM(vendor_name)) as normalized_name,
    COUNT(DISTINCT vendor_name) as variant_count,
    STRING_AGG(DISTINCT vendor_name, ' | ') as variants,
    COUNT(*) as total_invoices
FROM public.vendor_bills
WHERE vendor_name IS NOT NULL
GROUP BY LOWER(TRIM(vendor_name))
HAVING COUNT(DISTINCT vendor_name) > 1
ORDER BY total_invoices DESC;

-- Check for leading/trailing spaces:
SELECT
    vendor_name,
    COUNT(*) as count
FROM public.vendor_bills
WHERE vendor_name IS NOT NULL AND (
    vendor_name LIKE ' %' OR
    vendor_name LIKE '% '
)
GROUP BY vendor_name;
