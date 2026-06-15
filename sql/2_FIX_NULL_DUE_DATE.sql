-- STEP 2: Fix NULL due_date (Multiple options - choose ONE approach)
--
-- ⚠️ CRITICAL BUSINESS DECISION:
-- This fix uses created_at (upload timestamp) to calculate due_dates.
-- created_at is NOT the invoice date — it's when the invoice was uploaded to the system.
--
-- If invoices are uploaded days/weeks after issue, this will CORRUPT:
--   ❌ AP aging reports
--   ❌ Cashflow forecasts
--   ❌ Cash collection timing
--
-- BEFORE running any UPDATE:
-- 1. Verify the NULL due_date bills have bill_date populated from OCR (see Query 0)
-- 2. If bill_date exists, use it instead of created_at
-- 3. If bill_date is also NULL, require manual due_date assignment per vendor
-- 4. NEVER use created_at without confirming upload timing is same-day
--
--
-- BUSINESS CONTEXT:
--   - 31 bills have NULL due_date (breaks cashflow forecasting)
--   - Need to infer due_date based on payment terms
--   - Default terms: 30 or 45 days from invoice date
--
-- CHOOSE ONE APPROACH BELOW:
--
-- ============================================================
-- MANDATORY FIRST STEP: Query 0 — Check invoice dates
-- ============================================================
-- Before running any UPDATE, verify that NULL due_date records can be fixed safely.
-- Run this query FIRST:
--
-- SELECT
--   id, vendor_name, invoice_no, bill_date,
--   created_at::date as upload_date,
--   (created_at::date - bill_date) as days_after_issue,
--   amount
-- FROM public.vendor_bills
-- WHERE due_date IS NULL
-- ORDER BY bill_date DESC NULLS LAST;
--
-- ⚠️ THEN CHECK:
-- • If bill_date IS NULL for any row: ❌ Cannot auto-fix (use Option D)
-- • If (created_at - bill_date) > 3 days: ⚠️ Delayed upload (risky auto-fix)
-- • If (created_at - bill_date) is 0-2 days: ✅ Safe for Options A/B
--
-- Do NOT proceed to Options A-D until you understand the upload timing.

-- ============================================================
-- OPTION A: Standard 30-day payment terms (RECOMMENDED)
-- ============================================================
-- Assumption: All vendors get 30 days from invoice_date to pay
-- Risk: Vendor-specific terms ignored (low risk if mostly uniform)
--
-- ⚠️ CRITICAL: created_at is when the record was uploaded to the system.
-- It is NOT the invoice date. If the system has an actual invoice_date column,
-- use that instead. If invoices are uploaded days/weeks after issue, using
-- created_at will corrupt AP aging and cashflow forecasts.
--
-- For this system: verify that invoices are uploaded same-day before using
-- created_at as a proxy for invoice_date. If not, require manual due_date
-- assignment instead of calculated dates.
--
-- Run this for a preview first:
-- BEST APPROACH: Use bill_date when available; mark NULL bill_date for manual review
BEGIN;
  SELECT
    id,
    vendor_name,
    invoice_no,
    bill_date,
    created_at::date as upload_date,
    CASE
      WHEN bill_date IS NOT NULL THEN bill_date::date + 30
      ELSE created_at::date + 30
    END as calculated_due_date,
    CASE
      WHEN bill_date IS NULL THEN '⚠️ MANUAL REVIEW REQUIRED'
      ELSE 'OK'
    END as status,
    amount,
    payment_status,
    review_status
  FROM public.vendor_bills
  WHERE due_date IS NULL
    AND payment_status NOT IN ('paid', 'credit_card')  -- Only unpaid bills need due_date
    AND review_status != 'rejected'  -- Skip rejected bills
  ORDER BY bill_date DESC NULLS FIRST
  LIMIT 50;
ROLLBACK;

-- ⚠️ BEFORE PROCEEDING:
-- Count how many have NULL bill_date:
-- SELECT COUNT(*) as null_bill_date_count
-- FROM public.vendor_bills
-- WHERE due_date IS NULL AND bill_date IS NULL
--   AND payment_status NOT IN ('paid', 'credit_card')
--   AND review_status != 'rejected';
--
-- If count > 0: Some bills cannot be auto-fixed. Either:
--   • Use created_at as best guess (risky for delayed uploads)
--   • Export those N bills and assign due_dates manually

-- Then run the actual update (RECOMMENDED: uses bill_date when available):
-- ⚠️ DISABLED: Uncomment only after:
--   1. Running Query 0 to check invoice dates
--   2. Confirming bill_date is populated for the NULL due_date records
--   3. Full database backup
--
-- BEGIN;
--   UPDATE public.vendor_bills
--   SET due_date = CASE
--       WHEN bill_date IS NOT NULL THEN bill_date::date + 30
--       ELSE created_at::date + 30  -- Fallback if bill_date missing
--     END
--   WHERE due_date IS NULL
--     AND payment_status IS NOT NULL  -- Skip if status is NULL
--     AND payment_status = 'unpaid'  -- Only unpaid bills (avoid paid/disputed)
--     AND review_status IS NOT NULL  -- Skip if review status is NULL
--     AND review_status != 'rejected';  -- Skip rejected invoices
--
-- COMMIT;
--
-- AFTER COMMIT, verify with:
--   SELECT COUNT(*) as recently_fixed
--   FROM public.vendor_bills
--   WHERE payment_status = 'unpaid'
--     AND review_status != 'rejected'
--     AND due_date >= CURRENT_DATE - 30;
-- (This counts unpaid bills with recent due dates)

-- ⚠️ SEPARATE MANUAL REVIEW STEP:
-- If the above UPDATE leaves any NULL due_dates for unpaid bills, export them:
-- SELECT id, vendor_name, invoice_no, bill_date, created_at, amount
-- FROM public.vendor_bills
-- WHERE due_date IS NULL
--   AND payment_status = 'unpaid'
--   AND review_status != 'rejected'
-- ORDER BY created_at DESC;

-- NOTE: If you want to also set due_date for paid/rejected bills (for archival),
-- review each case individually in a separate transaction.

-- Verification:
-- ⚠️ NOTE: If you used status filtering (unpaid/pending only), NULLs may remain.
SELECT COUNT(*) as remaining_null_due_dates FROM public.vendor_bills
WHERE due_date IS NULL;  -- Should be 0 if Option A run with no filters;
                         -- May be >0 if filtering by status (paid/rejected excluded)


-- ============================================================
-- OPTION B: Extended 45-day payment terms [DISABLED - USE OPTION A]
-- ============================================================
-- ⚠️ DISABLED: This option uses SQLite syntax (SELECT changes())
-- which does NOT work in Postgres/Supabase and will error/rollback.
--
-- If you need 45-day terms: Copy Option A, change "+30" to "+45"
-- and use the corrected Postgres syntax.
--
-- DO NOT UNCOMMENT OR RUN. Option A is active above.


-- ============================================================
-- OPTION C: Vendor-specific terms [DISABLED - REQUIRES CAUTION]
-- ============================================================
-- ⚠️ DISABLED: Vendor-specific terms require:
--   1. Verification that created_at = invoice_date (see warning above)
--   2. Manual review of each vendor's actual payment terms
--   3. Testing before production use (can corrupt AP aging if wrong)
--
-- If you implement this: Use Option A as template, add CASE statement,
-- and test thoroughly on a copy of the data first.
--
-- DO NOT UNCOMMENT OR RUN. Option A is active above.


-- ============================================================
-- OPTION D: Manual review + CSV import [DISABLED - OPTIONAL ONLY]
-- ============================================================
-- For each bill, manually determine correct due_date:
-- 1. Export NULL due_date bills to CSV
-- 2. Review each with vendor agreements
-- 3. Re-import with corrected due_dates
--
-- DISABLED: This is a fallback option only. Use Option A above.
-- If you need to export for manual review, uncomment the query below.
--
-- Export query (COMMENTED OUT - uncomment only if needed):
-- COPY (
--   SELECT
--     id,
--     vendor_name,
--     invoice_no,
--     amount,
--     created_at,
--     review_status,
--     payment_status
--   FROM public.vendor_bills
--   WHERE due_date IS NULL
--   ORDER BY created_at DESC
-- ) TO STDOUT WITH (FORMAT CSV, HEADER);


-- ============================================================
-- POST-FIX VERIFICATION
-- ============================================================
-- After running the UPDATE, verify:

-- ⚠️ IMPORTANT: Check remaining NULLs (not all will be zero if you used status filters)
SELECT
  COUNT(*) as remaining_nulls,
  COUNT(*) FILTER (WHERE payment_status IN ('paid', 'credit_card')) as in_paid_cc,
  COUNT(*) FILTER (WHERE review_status = 'rejected') as in_rejected,
  COUNT(*) FILTER (WHERE payment_status IS NULL) as null_status
FROM public.vendor_bills
WHERE due_date IS NULL;
-- Expected: remaining_nulls may be > 0 (intentional for paid/rejected/null-status rows)

-- Count rows updated in the last 5 minutes (check if UPDATE actually ran):
SELECT COUNT(*) as recently_updated
FROM public.vendor_bills
WHERE (due_date >= CURRENT_DATE - 30)  -- Recent due dates
  AND payment_status NOT IN ('paid', 'credit_card')
  AND review_status != 'rejected';

-- Check new due_dates look reasonable:
SELECT
  vendor_name,
  invoice_no,
  amount,
  created_at::date as invoice_date,
  due_date,
  (due_date - created_at::date) as days_to_pay
FROM public.vendor_bills
WHERE due_date >= (CURRENT_DATE - 60)  -- recently updated
ORDER BY created_at DESC
LIMIT 31;

-- Check for any due_dates in the past (should be minimal):
SELECT COUNT(*) FROM public.vendor_bills
WHERE due_date < CURRENT_DATE AND payment_status = 'unpaid';  -- How many overdue?
