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

```sql
BEGIN;
  UPDATE public.vendor_bills
  SET due_date = created_at::date + 30
  WHERE due_date IS NULL;
  
  SELECT COUNT(*) as rows_updated;
COMMIT;
```

**Expected result:** `rows_updated: 31`

### Step 5: Verify Fix

```sql
-- Check no NULL remain
SELECT COUNT(*) FROM public.vendor_bills 
WHERE due_date IS NULL;  -- Should be 0

-- Check new due dates look good
SELECT vendor_name, invoice_no, created_at, due_date, amount
FROM public.vendor_bills
WHERE created_at >= '2026-06-10'  -- Recently updated
ORDER BY due_date;
```

**Impact after fix:**
- ✅ Dashboard "Due Soon" card now includes these 31 bills
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
