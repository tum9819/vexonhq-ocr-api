-- STEP 1: Analyze 31 bills with NULL due_date
-- Execute this to understand the pattern before fixing

-- ===== Query 1: List all NULL due_date bills =====
SELECT
    id,
    vendor_name,
    invoice_no,
    amount,
    created_at,
    review_status,
    payment_status,
    due_date
FROM public.vendor_bills
WHERE due_date IS NULL
ORDER BY created_at DESC;

-- ===== Query 2: Summary statistics =====
SELECT
    COUNT(*) as total_count,
    SUM(amount) as total_amount,
    MIN(created_at)::date as earliest_invoice,
    MAX(created_at)::date as latest_invoice
FROM public.vendor_bills
WHERE due_date IS NULL;

-- ===== Query 3: Breakdown by payment_status =====
SELECT
    payment_status,
    COUNT(*) as count,
    SUM(amount) as total_amount
FROM public.vendor_bills
WHERE due_date IS NULL
GROUP BY payment_status
ORDER BY count DESC;

-- ===== Query 4: Breakdown by review_status =====
SELECT
    review_status,
    COUNT(*) as count,
    SUM(amount) as total_amount
FROM public.vendor_bills
WHERE due_date IS NULL
GROUP BY review_status
ORDER BY count DESC;

-- ===== Query 5: Vendors with NULL due_date =====
SELECT
    vendor_name,
    COUNT(*) as bill_count,
    SUM(amount) as total_amount,
    MIN(created_at)::date as earliest,
    MAX(created_at)::date as latest
FROM public.vendor_bills
WHERE due_date IS NULL
GROUP BY vendor_name
ORDER BY bill_count DESC;
