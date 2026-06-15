# System Monitoring & Follow-up Report
**Date:** 2026-06-16  
**Status:** All 3 monitoring tasks completed

---

## ✅ **Task 1: Dashboard "ครบกำหนด 7 วัน" Monitoring**

### Current Status
- **Bills due in next 7 days:** 4 bills
- **Total amount:** ฿24,493
- **Due date:** 2026-06-17 (2 days from now)

### Bills Requiring Payment:
| # | Vendor | Amount | Days | Status |
|---|--------|--------|------|--------|
| 1 | ขายส่ง | ฿5,268 | 2 | unpaid |
| 2 | ขายส่ง | ฿3,168 | 2 | unpaid |
| 3 | ขายส่ง | ฿3,225 | 2 | unpaid |
| 4 | บจก. บี.บี. ซุปเปอร์สโตร์ | ฿12,832 | 2 | unpaid |

### ✅ Dashboard Status
- ✅ View `v_invoice_due_soon` working correctly
- ✅ All NULL due_dates fixed → accurate count
- ✅ Ready to display on dashboard UI

---

## ⚠️ **Task 2: Overdue Unpaid Bills Follow-up**

### 4 Bills Overdue - Action Required:

| Priority | Vendor | Invoice | Amount | Overdue | Status | Action |
|----------|--------|---------|--------|---------|--------|--------|
| 🔴 **CRITICAL** | SINGHA BEER CO., LTD. | SS 68093823 | ฿30,285.98 | **274 days** | ❌ Rejected | **CONTACT IMMEDIATELY** |
| 🟡 HIGH | SINGHA BEER CO., LTD. | SS 690600113 | ฿20,639.98 | **12 days** | ⏳ Pending | Follow-up payment |
| 🟢 MEDIUM | ร้านเจ๊บาบา | (no invoice) | ฿699 | **1 day** | ✅ Confirmed | Collect payment |
| 🟢 MEDIUM | ร้านขายส่ง | (no invoice) | ฿649 | **1 day** | ✅ Confirmed | Collect payment |

### **Summary:**
- **Total overdue amount:** ฿52,273.95
- **Bill #1 is critical:** 274 days overdue, marked as "rejected" → needs vendor contact
- **Bills #3, #4:** Just 1 day overdue, confirmed status → routine collection

### Recommended Actions:
1. ☎️ Call SINGHA BEER about bill SS 68093823 (274 days overdue)
2. 📧 Email SINGHA BEER about bill SS 690600113 (12 days overdue, pending)
3. 💳 Collect ฿1,348 from ร้านเจ๊บาบา and ร้านขายส่ง (due tomorrow)

---

## 📋 **Task 3: Vendor Name Consolidation (Manual Processing Map)**

### Consolidation Groups (Cannot auto-update due to unique constraint)

| Group | Canonical Name | Variations | Bills | Amount | Recommendation |
|-------|---|---|-------|--------|---|
| **Singha Beer** | SINGHA BEER CO., LTD. | • SINGHA BEER CO., LTD.<br>• บริษัท เบียร์สิงห์ จำกัด<br>• ค่าเบียร์สิงห์<br>• บริษัท เบียร์ไทย | 28 | ฿440,138.49 | Consolidate to main brand |
| **BB Superstore** | บจก. บี.บี. ซุปเปอร์สโตร์ | • บจก. บี.บี. ซุปเปอร์สโตร์<br>• บจก. บี. บี. ซุปเปอร์สโตร์<br>• บจ. บี. บี. ซุปเปอร์สโตร์<br>• บจก. บี บี ซุปเปอร์สโตร์<br>• บี ซูปเปอร์สโตร์ | 18 | ฿181,913.00 | Fix abbreviation formatting |
| **WEALIMEX** | WEALIMEX COMPANY LIMITED | • WEALIMEX COMPANY LIMITED<br>• WEALMEX COMPANY LIMITED<br>• บริษัท วิลลิเม็กซ์ จำกัด<br>• บริษัท วีลิมเม็กซ์ จำกัด | 17 | ฿10,697.30 | Fix typo: WEALMEX → WEALIMEX |
| **CP Extra** | บริษัท ซีพี แอ็กซ์ตร้า จำกัด (มหาชน) | • บริษัท ซีพี แอ็กซ์ตร้า จำกัด (มหาชน)<br>• บริษัท ซีพี แอ็กซ์ตร้า จํากัด (มหาชน)<br>• บริษัท ซีพี แอ๊กซ์ตร้า จำกัด (มหาชน)<br>• บริษัท ซีพี แอ๊กซ์ตร้า จํากัด (มหาชน) | 16 | ฿56,481.00 | Standardize Thai vowel marks |
| **Wholesale** | ขายส่ง | • ขายส่ง<br>• ร้านขายส่ง | 5 | ฿19,160.00 | Drop "ร้าน" prefix |

### Why Manual Processing Needed:
- Database has unique constraint on `(vendor_name, invoice_no)` combination
- Auto-consolidation would create duplicate key violations
- Solution: Manual review + selective update per invoice

### Manual Consolidation Steps:
1. **Review invoices** in each group for exact duplicates
2. **Contact vendors** to confirm which name is official
3. **Update via Supabase Console** with filtered WHERE clauses
4. **Verify** no duplicate invoices after update

---

## 🎯 **Final Status**

| Task | Status | Impact | Next Step |
|------|--------|--------|-----------|
| **Dashboard Due Soon** | ✅ Fixed | Accurate bill tracking | Monitor displayed count |
| **Overdue Bills** | ⚠️ 4 identified | Collection needed | Follow-up by payment team |
| **Vendor Names** | 📋 Mapped | Analytics improvement | Manual review + update |

---

## 📅 **Recommendation Timeline**

| Priority | Item | Target Date | Owner |
|----------|------|-------------|-------|
| 🔴 CRITICAL | Contact SINGHA about 274-day overdue bill | TODAY (2026-06-16) | Finance |
| 🟡 HIGH | Collect from ร้านเจ๊บาบา + ร้านขายส่ง | 2026-06-17 | Finance |
| 🟡 HIGH | Vendor consolidation manual review | 2026-06-30 | Admin |
| 🟢 MEDIUM | Implement consolidated vendor names | 2026-07-07 | Admin |

---

**Report Generated:** 2026-06-16  
**System Status:** ✅ STABLE & MONITORING
