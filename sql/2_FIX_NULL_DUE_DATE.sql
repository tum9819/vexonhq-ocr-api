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
-- ⚠️ IMPORTANT: created_at is when the record was uploaded to the system,
-- NOT necessarily the actual invoice date. If invoice date column exists,
-- use that instead. If the two differ significantly, results may be inaccurate.
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
    AND payment_status NOT IN ('paid', 'credit_card')  -- Only unpaid/pending
    AND review_status != 'rejected';  -- Skip rejected invoices

  -- For Postgres: show affected rows
  SELECT COUNT(*) as rows_updated FROM public.vendor_bills
  WHERE due_date IS NOT NULL
    AND payment_status NOT IN ('paid', 'credit_card');

COMMIT;

-- NOTE: If you want to also set due_date for paid/rejected bills (for archival),
-- add a separate transaction after reviewing the impact.

-- Verification:
SELECT COUNT(*) FROM public.vendor_bills WHERE due_date IS NULL;  -- Should be 0


-- ============================================================
-- OPTION B: Extended 45-day payment terms
-- ============================================================
-- Assumption: All vendors get 45 days from invoice_date to pay
-- Risk: More lenient than typical, but safer for small vendors
--
-- Run this for a preview:
BEGIN;
  SELECT
    id,
    vendor_name,
    invoice_no,
    created_at,
    created_at::date + 45 as calculated_due_date,
    amount
  FROM public.vendor_bills
  WHERE due_date IS NULL
  LIMIT 31;
ROLLBACK;

-- Then run the actual update:
BEGIN;
  UPDATE public.vendor_bills
  SET due_date = created_at::date + 45
  WHERE due_date IS NULL;

  SELECT changes() as rows_updated;
COMMIT;


-- ============================================================
-- OPTION C: Vendor-specific terms (MOST ACCURATE but manual)
-- ============================================================
-- Use different payment terms based on vendor_name
-- Edit the CASE statement to match your vendor agreements
--
-- Preview first:
BEGIN;
  SELECT
    id,
    vendor_name,
    invoice_no,
    created_at,
    CASE
      WHEN vendor_name LIKE '%XXX%' THEN created_at::date + 30  -- 30 days
      WHEN vendor_name LIKE '%YYY%' THEN created_at::date + 45  -- 45 days
      ELSE created_at::date + 30  -- default 30 days
    END as calculated_due_date,
    amount
  FROM public.vendor_bills
  WHERE due_date IS NULL
  LIMIT 31;
ROLLBACK;

-- Then run the actual update (EDIT VENDOR NAMES AND TERMS FIRST):
BEGIN;
  UPDATE public.vendor_bills
  SET due_date = CASE
      WHEN vendor_name LIKE '%XXX%' THEN created_at::date + 30
      WHEN vendor_name LIKE '%YYY%' THEN created_at::date + 45
      ELSE created_at::date + 30
    END
  WHERE due_date IS NULL;

  SELECT changes() as rows_updated;
COMMIT;


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
