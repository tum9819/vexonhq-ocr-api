# แผน v3 (แบบเล็กที่สุด): ห้ามต่อหน้าจาก "ไฟล์อื่น" เข้าบิลเดิม

วันที่: 2026-10-31 · สถานะ: **แผน (ยังไม่เขียนโค้ด)** · แทน v1/v2 (external review REJECT 3 รอบ — v2 กลายเป็นการเขียนระบบบันทึกใหม่ทั้งชุด ซึ่งเสี่ยงกว่าบั๊ก)
เป้าหมายรอบนี้: **ดีกว่าของเดิมอย่างเคร่งครัด + ไม่มีอะไรที่เคยทำงานแล้วพัง** — ไม่ใช่แก้ทุกช่องของระบบบันทึก

## 1. บั๊ก
`_save_invoice` (main.py:3623) เจอบิลเดิมด้วย dedup_key (:3651, รวม confirmed) / invoice_no fallback (:3669) / create-conflict refetch (:3845) แล้ว **append items + attachment** แม้ไฟล์ใหม่เป็นคนละไฟล์ (สแกนใหม่/ถ่ายใหม่) → items ซ้ำ
ความเสียหายจริง ณ 2026-10-31 (SELECT): 114 บิล items ซ้ำ = 0 → กันไว้ก่อนที่ปริมาณสแกนจะเพิ่ม

## 2. การแก้ (backend 2 ไฟล์, ไม่มี migration, ไม่แก้ frontend)
### 2.1 guard
```python
def _same_file_on_bill(bill_id: str, file_sha256: str) -> bool:
    """True ถ้าบิลนี้มี attachment จากไฟล์ต้นฉบับเดียวกัน (หน้าถัดไปของ PDF เดียวกัน / retry ไฟล์เดิม)"""
```
- query `attachments` (parent_type='vendor_bill', parent_id, file_sha256) — **ถ้า query error → raise 503** (fail-closed: ไม่เขียนอะไร) ไม่กลืนแบบ lookup เดิม
- `file_sha256 is None` → คืน True (พฤติกรรมเดิม — caller ปัจจุบันทุกตัวส่ง sha: เว็บ main.py:900/912, LINE line_bot_routes.py:290)
- **บิลเปล่า** (ไม่มี attachment **และ** ไม่มี invoice_items เลย) → คืน True — เกิดจากรอบก่อนสร้างหัวบิลแล้ว insert items ล้ม (review v3 #1); รวมเข้าบิลเปล่าไม่มีทางทำให้ items ซ้ำ จึงคงพฤติกรรม retry เดิมได้โดยไม่ต้องใช้ window เวลา

### 2.2 จุดที่เรียก (2 จุดเขียนจริง — ตาม review รอบ 2 #1)
1. หลัง lookup เสร็จ (หลัง main.py:3695) **นอก try** ก่อน page-idempotency/merge: ถ้า `existing` และ `not _same_file_on_bill(existing["id"], file_sha256)` → `raise BillAlreadyExists(existing)`
2. create-conflict (main.py:3859 หลัง refetch) ก่อน update batch_id/insert items: เงื่อนไขเดียวกัน
ทั้งสองจุดอยู่ **ก่อนการเขียน DB ใด ๆ** ของคำขอนี้ (header backfill :3800, items :3811/:3873, attachment :3878)
3. **แคบ try ของ create-conflict** (main.py:3824-3875, review v3 #4): ให้ `try/except 23505` ครอบเฉพาะ INSERT หัวบิล; ย้าย `_insert_items` ออกมาหลัง try — error จาก items จะไม่ถูกตีความเป็น conflict อีก (ไหลออกเป็น 500 ตามเดิม → retry ได้ด้วยกฎบิลเปล่า)

### 2.3 การตอบ
- `BillAlreadyExists(HTTPException)` status **409**, `detail` เป็น **string** เดียว: `บิลนี้มีในระบบแล้ว — <ร้าน> เลขที่ <เลข> วันที่ <วัน> ยอด <ยอด> (ID <8 ตัวแรก>) [สถานะ <review_status>]`
- `_persist_invoice_page` (main.py:1034): เพิ่ม `except HTTPException:` ก่อน `except Exception` → ลบ storage path ของคำขอนี้ (เพิ่งอัป, UUID ใหม่, ยังไม่มีแถวใดอ้าง เพราะ guard อยู่ก่อนการเขียน) แล้ว re-raise (ไม่แปลงเป็น 500)
- `/invoice/upload` handler: ตรวจว่า HTTPException ไหลออกเป็น 409 จริง (ไม่ถูกห่อ)
- Frontend เดิม: `lib/invoice-api.ts:711` อ่าน `body.detail` (string) → แสดงเป็น error ไม่เปิด modal → **ไม่ต้องแก้** (ต้องยืนยันใน test/manual ว่าแสดงข้อความครบ)
- LINE: `_save_invoice_from_line` (line_bot_routes.py:252) — ถ้า storage upload สำเร็จแล้วเจอ HTTPException จาก guard (**409 หรือ 503**) → ลบไฟล์นั้นแล้ว re-raise (review v3 #3); `_process_invoice_from_line` จับ `BillAlreadyExists` → คืนข้อความ `📎 บิลนี้มีในระบบแล้ว — ไม่บันทึกซ้ำ\n<รายละเอียด>\nถ้าเป็นหน้าต่อของบิลหลายแผ่น หน้านี้จะไม่ถูกเพิ่มเข้าบิล — แจ้ง admin ตรวจในเว็บ` (ไม่สัญญาว่า PDF จะเติมได้ — review v3 #2) แทนข้อความ "❌ ลองใหม่" ที่ line_bot_routes.py:2158 ซึ่งชวนให้ส่งซ้ำ

## 3. พฤติกรรมเทียบก่อน/หลัง
| สถานการณ์ | เดิม | ใหม่ |
|---|---|---|
| PDF หลายหน้า ไฟล์เดียว | รวมเป็นบิลเดียว | **เหมือนเดิม** (sha เดียวกัน) |
| PDF ไฟล์เดิม byte เดิม | already_uploaded | **เหมือนเดิม** |
| retry PDF ที่บันทึกไม่ครบ (sha เดิม) | ประมวลใหม่ทุกหน้า | **เหมือนเดิม** (ข้อจำกัดเดิม §4) |
| PDF หลายบิลในไฟล์เดียว (เลขต่างกัน, บิลใหม่ทั้งหมด) | แยกบิล | **เหมือนเดิม** |
| สแกนใหม่/รูปใหม่ ของบิลที่มีแล้ว (pending หรือ confirmed) | **append items ซ้ำ** | 409 ไม่เขียนอะไร |
| fallback invoice_no ไปเจอบิลอื่น (คนละร้าน เลขชน) | append เข้าบิลผิดใบ | 409 (ไม่เขียนผิดบิล; ถ้าเป็นบิลจริงคนละใบ TUM แก้/เพิ่มเองทางเว็บ) |
| LINE ส่งรูปหลายแผ่นของบิลเดียว (4/81 บิล) | รวม | หน้า 2+ ได้ 409 — **trade-off ที่ TUM ยอมรับโดยชัดแจ้ง** (เดิมรวมได้ แต่กลไกเดียวกันคือช่องที่ทำให้รูปซ้ำ append) |
| อัป PDF ทั้งบิล หลังบิลถูกสร้างจากรูป LINE แล้ว | append หน้า PDF เข้าบิลรูป → items ซ้ำ | 409 (ไม่ซ้ำ แต่ PDF ยังเติมบิลรูปไม่ได้ → แก้ในงานขั้น 2/3 ที่ย้ายรูปไปเป็นร่าง) |
| retry หลังรอบก่อนสร้างหัวบิลแล้ว items ล้ม | merge เข้าบิลเปล่า | **เหมือนเดิม** (กฎบิลเปล่า) |
| บิลเดิม rejected | lookup ไม่เลือก rejected → create → ชน unique → refetch → merge เข้าบิล rejected | refetch แล้ว guard → 409 (ระบุสถานะ rejected ในข้อความ) แทนการ append เข้าบิล rejected |

## 4. ข้อจำกัดที่รู้และยอมรับ (ไม่แย่กว่าเดิม — งานแยก)
1. PDF หลายบิลที่มีบิลซ้ำกลางไฟล์ → หยุดที่หน้านั้น (หน้าก่อนหน้าบันทึกแล้ว หน้าหลังไม่บันทึก) — เหมือนพฤติกรรมเดิมเมื่อหน้าใด error; ข้อความ 409 บอกว่าบิลไหนซ้ำ
2. partial retry PDF ยัง append หน้าเดิมซ้ำได้ (sha เดียวกันผ่าน guard)
3. items insert สำเร็จแต่ attachment insert ล้ม → ไม่มี transaction
4. lookup เดิมยัง fail-open เมื่อ DB error (ไม่เกี่ยวกับ guard)
5. การให้ PDF แทนข้อมูลจากรูป / ร่างบิล → งานขั้น 2 (ระบบร่างบิลจากรูป ไม่แตะ `_save_invoice`)
ข้อ 2–4 → บันทึกใน known issues

## 5. Test (TDD, pytest mock เดิม)
1. PDF 3 หน้า sha เดียว → 1 บิล items ครบ (regression)
2. ไฟล์ sha ต่าง เลขบิลตรงบิล confirmed → 409; ไม่มี update/insert ใด ๆ ใน vendor_bills/invoice_items/attachments; storage remove ถูกเรียกด้วย path ของคำขอนี้
3. เหมือน 2 แต่บิล pending → 409
4. ทาง invoice_no fallback → 409
5. ทาง create-conflict (insert ชน unique) → 409, ไม่ update batch_id
6. guard query error → 503 ไม่เขียนอะไร
7. `file_sha256=None` → พฤติกรรมเดิม (merge)
8. LINE: `_process_invoice_from_line` เจอ 409 → ข้อความ "มีในระบบแล้ว" + storage ถูกลบ
9. `/invoice/upload` คืน status 409 + detail เป็น string
10a. บิลเปล่า (หัวบิลไม่มี items/attachment) + ไฟล์ sha เดิมหรือใหม่ → merge ได้ (regression ของ retry)
10b. create path: insert items ล้ม (ไม่ใช่ 23505) → 500 ไม่เข้า refetch/merge
10c. PDF 3 หน้า: หน้า 1–2 บันทึก, หน้า 3 ชนบิลเดิม → 409, storage ที่ลบมีแค่ path หน้า 3, หน้า 1–2 อยู่ครบ
10d. LINE guard 503 → storage ถูกลบ
10e. ใช้ `_save_invoice` จริงกับ Supabase mock (ไม่ mock `_persist_invoice_page`) สำหรับข้อ 1–5 — test เดิมที่ไม่ส่ง sha (`test_ocr_confirm_merge.py:228`) ไม่ต้องเปลี่ยน expectation
10. ทั้งชุดเดิมผ่าน — test ที่ assert การ merge ข้ามไฟล์ (ถ้ามี) ต้องอัปเดตพร้อมเหตุผลใน commit
+ manual: อัปไฟล์ซ้ำบนหน้าเว็บ (local) → เห็นข้อความ error ครบ ไม่เปิด modal

## 6. Rollback / ส่งงาน
revert commit (ไม่มี migration/ข้อมูลเปลี่ยน) · TDD → pytest ทั้งชุด → `review.ps1` (Codex) → Antigravity → TUM ยืนยัน → push backend
