# HANDOFF — OCR-1: Confirm-Gating for AI Batch Categorization

**From:** Claude Code → **To:** Antigravity · **Date:** 2026-06-04 · **Requested by:** TUM

---

## Related M1 docs

For the stock-in importer handoff and quick operator checklist, use:

- [M1_STOCK_IN_HANDOFF.md](./M1_STOCK_IN_HANDOFF.md)
- [M1_STOCK_IN_CHECKLIST.md](./M1_STOCK_IN_CHECKLIST.md)

---

## ปัญหา

`POST /ai/categorize/batch` และ `POST /ai/categorize/cashflow/batch` ที่ cron เรียกทุกชั่วโมง → AI categorize แล้ว `UPDATE vendor_bills SET category_code` ทันที โดยไม่มีขั้นตอนให้คนอนุมัติก่อน  
ถ้า AI ผิด → financial data เสียหายโดยไม่รู้ตัว

มีอีก bug: `PATCH /ai/categorize/log/{log_id}` action=`reject` อัปเดตแค่ log table แต่ **ไม่** null-out `vendor_bills.category_code` → reject แล้วยังเห็น AI category ผิดอยู่

---

## งาน — แก้ `phase3a_ai_categorize_routes.py` เท่านั้น (2 จุด)

### จุดที่ 1 — เพิ่ม `dry_run` parameter ให้ทั้ง 2 batch endpoints

**`POST /ai/categorize/batch`** เพิ่ม `dry_run: bool = Query(False)`:

```python
@router.post("/ai/categorize/batch")
def categorize_batch(
    limit: int = Query(50, ge=1, le=200),
    allow_llm: bool = Query(True),
    dry_run: bool = Query(False),
):
```

เมื่อ `dry_run=True`:
- Fetch pending bill IDs และ run Tier 1/Tier 2 เหมือนเดิม (เพื่อให้ได้ proposed category)
- **ข้าม** `UPDATE public.vendor_bills SET category_code`
- **ข้าม** `INSERT INTO public.ai_categorization_log`
- **ข้าม** `conn.commit()` — ถ้ามีการ write ใด ๆ เกิดขึ้น ให้ `conn.rollback()` ตอนท้าย
- Return รูปแบบเดิม แต่เพิ่ม `"dry_run": true` ใน response JSON

เมื่อ `dry_run=False` (default — cron ยังทำงานเหมือนเดิม ห้ามเปลี่ยน):
- ทำงานเหมือนเดิมทุกอย่าง ห้าม break existing behavior

**`POST /ai/categorize/cashflow/batch`** — pattern เดียวกัน เพิ่ม `dry_run: bool = Query(False)`:
- เมื่อ `dry_run=True`: คำนวณ categories แต่ skip DB writes ใน `_categorize_cashflow_one` และ skip commit
- เมื่อ `dry_run=False`: เหมือนเดิมทุกอย่าง

### จุดที่ 2 — แก้ `reject` ใน `PATCH /ai/categorize/log/{log_id}`

ตอนนี้ action=`reject` เขียนแค่ `ai_categorization_log` แต่ `vendor_bills.category_code` ยังค้างอยู่  

เพิ่ม block นี้ก่อน `UPDATE public.ai_categorization_log`:

```python
if body.action == 'reject':
    cur.execute(
        "UPDATE public.vendor_bills SET category_code = NULL WHERE id = %s",
        (bill_id,),
    )
```

---

## Acceptance Criteria

1. `POST /ai/categorize/batch?dry_run=true` → returns `{"dry_run": true, ...}` และ `vendor_bills.category_code` ยัง NULL หลัง call
2. `POST /ai/categorize/batch` (ไม่มี dry_run) → ทำงานเหมือนเดิม cron ไม่พัง
3. `POST /ai/categorize/cashflow/batch?dry_run=true` → preview เท่านั้น ไม่ write DB
4. `POST /ai/categorize/cashflow/batch` → ทำงานเหมือนเดิม
5. `PATCH /ai/categorize/log/{log_id}` action=`reject` → null-out `vendor_bills.category_code`
6. `pytest tests/ -q` ผ่าน (skip smoke + backup tests เหมือนเดิม)

---

## Guardrails

- แก้เฉพาะ `phase3a_ai_categorize_routes.py` เท่านั้น
- ห้าม commit/push — Claude จะ review + push เอง

<!-- Antigravity: เสร็จ → append '[ocr1 done — dry_run added to both batch endpoints / reject fix applied / pytest: PASS]'. ห้าม commit/push. -->
[ocr1 done — dry_run added to both batch endpoints / reject fix applied / pytest: PASS]

---

## Update 2026-06-06 — invoice admin gating

- Added explicit admin gate to `POST /invoice/{invoice_id}/confirm` and `POST /invoice/{invoice_id}/reject` in `main.py`.
- `reject` now validates `reject_reason` after the admin check so non-admin users cannot trigger schema-validation leakage before the gate.
- Verified via direct TestClient run: missing-token → 401, staff-token → 403, admin-token → passes the gate for both confirm/reject routes.
