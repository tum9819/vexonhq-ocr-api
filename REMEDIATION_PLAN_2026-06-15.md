# Data Remediation Plan: NULL due_date & Vendor Normalization
**Date:** 2026-06-15  
**Priority:** P0 (due_date), P1 (vendor normalization)  
**Status:** Ready for execution

---

## Overview

**Antigravity's Findings:**
- ❌ 31 bills with NULL `due_date` — **breaks cashflow forecasting**
- ⚠️ Vendor names with spelling variations — **impacts reporting**

**Remediation Scripts Prepared:**
1. `sql/1_ANALYZE_NULL_DUE_DATE.sql` — Analyze the 31 NULL records
2. `sql/2_FIX_NULL_DUE_DATE.sql` — Fix with payment term logic
3. `sql/3_VENDOR_NAME_NORMALIZATION.sql` — Consolidate vendor names (P1)

---

## P0: Fix NULL due_date (31 bills)

### ⚠️ CRITICAL PREREQUISITE — READ BEFORE PROCEEDING

**created_at is UPLOAD time, NOT invoice date.**

If invoices are uploaded days/weeks after they were issued, using `created_at::date + 30` for due_date will:
- ❌ Corrupt AP aging reports
- ❌ Corrupt cashflow forecasts
- ❌ Show invoices as overdue when they're not

**MUST verify before applying fix:**
1. Check actual invoice dates in OCR data (`ocr_json->>'bill_date'`)
2. Confirm invoices are uploaded same-day (or very close to) issue date
3. If NOT same-day uploads, require manual due_date assignment per invoice

**This fix assumes:** All NULL due_date invoices were uploaded within 1-2 days of issue.
**If this assumption is wrong,** the fix will corrupt your financial data.

---

### Step 1: Analyze Current State
Run `1_ANALYZE_NULL_DUE_DATE.sql` to:
- List all 31 bills
- Group by vendor, payment_status, review_status
- Identify patterns

**Expected output:**
- Total count: 31
- Total amount: ~฿xxx,xxx
- Vendors affected: ~Y vendors
- Payment statuses: mostly 'unpaid' or 'pending_review'

### Step 2: Choose Remediation Approach

From `2_FIX_NULL_DUE_DATE.sql`, pick ONE:

| Option | Terms | Recommendation | Notes |
|--------|-------|---|---|
| **A** | 30 days | ✅ RECOMMENDED | Standard business terms, balances accuracy vs. guessing |
| **B** | 45 days | ℹ️ Alternative | More lenient, suits small vendors |
| **C** | Per-vendor | ✅ MOST ACCURATE | Requires vendor agreement research |
| **D** | Manual review | 📋 THOROUGH | Export to CSV, manually review with vendors |

**Recommendation: Start with Option A (30 days)**
- Matches Thai business standard
- Easy to audit
- If vendor-specific terms needed later, can adjust per vendor

### Step 3: Execute Preview

Before committing, run the preview query:

```sql
-- Preview Option A (30 days)
BEGIN;
  SELECT
    id, vendor_name, invoice_no, created_at,
    created_at::date + 30 as calculated_due_date,
    amount
  FROM public.vendor_bills
  WHERE due_date IS NULL
  LIMIT 31;
ROLLBACK;
```

**Check:**
- ✅ Due dates make sense (not in the past for 'unpaid' bills)?
- ✅ Spread across next 30 days?
- ✅ Amount matches expectations?

### Step 4: Execute Update

⚠️ **IMPORTANT:** The UPDATE block in `sql/2_FIX_NULL_DUE_DATE.sql` is **DISABLED by default** for safety.
Before running, manually uncomment and verify the date logic.

Postgres-correct syntax to report affected rows:

```sql
BEGIN;
  UPDATE public.vendor_bills
  SET due_date = created_at::date + 30
  WHERE due_date IS NULL
  RETURNING id;  -- Returns all updated row IDs
  
  -- Then count them:
  -- SELECT COUNT(*) FROM (above query) as updated;
COMMIT;
```

Or verify separately after UPDATE:
```sql
SELECT COUNT(*) FROM public.vendor_bills 
WHERE due_date IS NOT NULL AND created_at >= '2026-06-15'::date;
```

**Expected result after execution:** All NULL due_dates converted to `created_at + 30`

### Step 5: Verify Fix

```sql
-- Check remaining NULL due_dates (will vary by filtering)
SELECT COUNT(*) FROM public.vendor_bills 
WHERE due_date IS NULL;
-- Expected: 0 if no filters, or >0 if you filtered out paid/rejected bills

-- Check new due dates look good
SELECT vendor_name, invoice_no, created_at, due_date, amount, payment_status
FROM public.vendor_bills
WHERE due_date >= '2026-06-15'  -- Recently set due_dates
ORDER BY due_date;

-- If using the recommended filters (unpaid + unrejected only):
SELECT COUNT(*) FROM public.vendor_bills
WHERE payment_status = 'unpaid'
  AND due_date >= '2026-06-15';
-- Should match the count of bills that were fixed
```

**⚠️ Important:** If you used status filtering in the UPDATE, NULL due_dates may remain for:
- `payment_status = 'paid'` or `'credit_card'` (already paid/archived)
- `review_status = 'rejected'` (disputed invoices)
- Any row with NULL status fields

This is intentional — you only want to track active unpaid bills. If you need to fix the others separately, repeat the fix with different WHERE conditions.

**Impact after fix:**
- ✅ Dashboard "Due Soon" card now includes active unpaid bills
- ✅ Overdue tracking now accurate for unpaid invoices
- ✅ Cashflow forecasting no longer has gaps
- ✅ Payment alerts can trigger on due_date

---

## P1: Vendor Name Normalization (Optional, Lower Priority)

### When to Run
- After P0 is complete & verified
- Can be deferred to next maintenance window

### Approach

From `3_VENDOR_NAME_NORMALIZATION.sql`:

1. **Run Query 2** to find duplicate vendor names
   ```sql
   SELECT
       LOWER(TRIM(vendor_name)) as normalized_name,
       COUNT(DISTINCT vendor_name) as variant_count,
       STRING_AGG(DISTINCT vendor_name, ' | ') as variants
   FROM public.vendor_bills
   WHERE vendor_name IS NOT NULL
   GROUP BY LOWER(TRIM(vendor_name))
   HAVING COUNT(DISTINCT vendor_name) > 1;
   ```

2. **Build mapping** of vendor spelling variants:
   ```
   'บริษัท ABC', 'ABC', 'abc' -> 'ABC Co., Ltd.'
   'ซัพพลาย XYZ', 'XYZ Supply' -> 'XYZ Supply'
   ```

3. **Run Option B** with your custom mapping:
   ```sql
   UPDATE public.vendor_bills
   SET vendor_name = CASE
       WHEN vendor_name IN ('บริษัท ABC', 'ABC', 'abc') THEN 'ABC Co., Ltd.'
       ...
     END
   WHERE vendor_name IS NOT NULL;
   ```

### Impact
- ✅ Dashboard vendor analytics accurate
- ✅ Reports don't split same vendor across variants
- ⚠️ Lower priority than due_date (doesn't break operations)

---

## Rollback Plan

If something goes wrong:

```bash
# Tag before fixing
git tag backup/before-null-due-date-fix

# If needed, restore from database backup
# (Supabase keeps 7-day retention)
# Contact admin or restore from backup snapshot
```

---

## Execution Checklist

### P0 (Do first)
- [ ] Run `1_ANALYZE_NULL_DUE_DATE.sql` — understand the data
- [ ] Read output and decide: Option A/B/C/D
- [ ] Run preview query from `2_FIX_NULL_DUE_DATE.sql`
- [ ] Verify preview looks correct
- [ ] Run the UPDATE
- [ ] Run verification queries
- [ ] Confirm: no NULL due_dates remain

### P1 (Later, optional)
- [ ] Run Query 2 from `3_VENDOR_NAME_NORMALIZATION.sql`
- [ ] Build vendor mapping based on output
- [ ] Test with preview CASE statement
- [ ] Run the UPDATE with your mapping
- [ ] Verify results

---

## Questions?

If any data looks wrong:
1. Check the SQL scripts — they're preview-safe (ROLLBACK first)
2. Review the generated due_dates — do they match your vendor agreements?
3. For vendor normalization — are the mappings correct per your vendor list?

Let me know when you're ready to execute! 🚀
