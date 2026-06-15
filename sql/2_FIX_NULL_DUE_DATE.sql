-- STEP 2: Fix NULL due_date (Multiple options - choose ONE approach)
--
-- BUSINESS CONTEXT:
--   - 31 bills have NULL due_date (breaks cashflow forecasting)
--   - Need to infer due_date based on payment terms
--   - Default terms: 30 or 45 days from invoice date
--
-- CHOOSE ONE APPROACH BELOW:

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
BEGIN;
  SELECT
    id,
    vendor_name,
    invoice_no,
    created_at,
    created_at::date + 30 as calculated_due_date,
    amount,
    payment_status,
    review_status
  FROM public.vendor_bills
  WHERE due_date IS NULL
    AND payment_status NOT IN ('paid', 'credit_card')  -- Only unpaid bills need due_date
    AND review_status != 'rejected'  -- Skip rejected bills
  LIMIT 31;
ROLLBACK;

-- Then run the actual update (RECOMMENDED: filter to unpaid + unrejected):
BEGIN;
  UPDATE public.vendor_bills
  SET due_date = created_at::date + 30
  WHERE due_date IS NULL
    AND payment_status IS NOT NULL  -- Skip if status is NULL
    AND payment_status NOT IN ('paid', 'credit_card')  -- Only unpaid/pending
    AND review_status IS NOT NULL  -- Skip if review status is NULL
    AND review_status != 'rejected';  -- Skip rejected invoices

  -- For Postgres: show affected rows
  SELECT COUNT(*) as rows_updated FROM public.vendor_bills
  WHERE due_date IS NOT NULL
    AND payment_status NOT IN ('paid', 'credit_card');

COMMIT;

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


-- ============================================================
-- OPTION D: Manual review + CSV import
-- ============================================================
-- For each bill, manually determine correct due_date:
-- 1. Export NULL due_date bills to CSV
-- 2. Review each with vendor agreements
-- 3. Re-import with corrected due_dates
--
-- Export query:
COPY (
  SELECT
    id,
    vendor_name,
    invoice_no,
    amount,
    created_at,
    review_status,
    payment_status
  FROM public.vendor_bills
  WHERE due_date IS NULL
  ORDER BY created_at DESC
) TO STDOUT WITH (FORMAT CSV, HEADER);


-- ============================================================
-- POST-FIX VERIFICATION
-- ============================================================
-- After running the UPDATE, verify:

-- Check no NULL due_dates remain:
SELECT COUNT(*) FROM public.vendor_bills WHERE due_date IS NULL;  -- Should be 0

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
