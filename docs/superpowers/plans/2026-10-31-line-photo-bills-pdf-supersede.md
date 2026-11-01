# แผน: บิลจากรูปกลุ่ม "Mara Bill" → บันทึกชั่วคราว → PDF แทนที่ภายหลัง

วันที่: 2026-10-31 · สถานะ: **แผน (ยังไม่เขียนโค้ด)** · ผู้เขียน: Claude · ผู้ตรวจ: AI ภายนอก → Antigravity

## 1. เป้าหมาย
พนักงานถ่ายรูปบิลส่งกลุ่ม LINE "Mara Bill" → ค่าใช้จ่ายเข้าระบบทันที (ชั่วคราว) → เมื่อ TUM สแกน PDF ภายหลัง ระบบใช้ข้อมูลจาก PDF แทนที่บิลจากรูป **บนบิลใบเดิม** โดยไม่สร้างบิลซ้ำ และไม่ทำ flow เดิม (อัป PDF ทางเว็บ, บอท MARA STATION, สลิป) พัง

## 2. การตัดสินใจของ TUM (2026-10-31)
| # | หัวข้อ | ตัดสิน |
|---|---|---|
| 1 | รูปเข้าระบบเมื่อไร | **ทันที** (BOT Mara ส่งต่อตอนบันทึก) |
| 2 | บิลจากรูปนับใน P&L ไหม | **นับเลย** แล้วตรวจอีกรอบหลังได้ PDF |
| 3 | PDF แทนที่ | **ระบบทำเอง** แต่ต้องมีด่านตรวจก่อน (ไม่ชัด → คิวให้ TUM เลือก) |
| 4 | แยกรูปบิล/รูปสินค้า | **ระบบแยกเอง** (ไม่แน่ใจ → ไม่สร้างบิล) |
| 5 | บิลไม่มี PDF | **ทุกบิลต้องมี PDF ในที่สุด** |

## 3. ระบบเดิมที่เกี่ยวข้อง (อ่านจากโค้ด 2026-10-31)
- BOT Mara = Apps Script "Line Bot Mara" (`Code.gs`) เก็บไฟล์ลง Drive `Line BOT/yyyy-MM`, จำกัดเฉพาะ group ใน `ALLOWED_CHAT_IDS`, ตอบกลับด้วย reply API
- `line_bot_routes.py:267 _process_invoice_from_line` — file-hash dedup (`main._find_uploaded_file` :743) → `_run_gpt_vision` → `_save_invoice`
- `line_bot_routes.py:306+` ตัวจัดประเภท slip/invoice/other; ตอนนี้ "other" ตกไปทาง invoice (:2150)
- `main.py:3623 _save_invoice` — `dedup_key = lower(vendor)|lower(invoice_no)`; ถ้าเจอบิลเดิม (**รวมสถานะ confirmed**) จะ **merge เป็นหน้าเพิ่ม + insert items เพิ่ม**; fallback ด้วย invoice_no อย่างเดียว (:3669) ผ่าน `_should_merge_on_invoice_no`; create-conflict ก็ merge (:3847)
- `main.py:1096 GET /invoice/duplicates` เตือนเฉพาะ pending/needs_attention, key = ยอด+วันที่
- `invoice_item_repairs` (:2282) = ตาราง backup ก่อน/หลังซ่อม items — reuse แนวคิดได้
- Tie band 0.90–1.10 (`_items_tie_state`) = มาตรฐานเดียวทั้งระบบ

### ช่องโหว่ที่ต้องปิดก่อน (P0)
1. **PDF ที่ dedup_key ตรงกับบิลจากรูป → ถูก merge เป็นหน้าเพิ่ม → items ซ้ำ 2 เท่า เงียบ** (`main.py:3651-3664`, `:3847`)
2. OCR รูปอ่าน invoice_no เพี้ยน → PDF สร้างบิลใบใหม่ → ถ้า confirm ทั้งคู่ P&L ซ้ำ (แบนเนอร์ไม่เตือนเพราะใบรูป confirmed แล้ว)

## 4. ข้อมูลใหม่ (migration เดียว, additive, nullable — ไม่แตะคอลัมน์เดิม)
```sql
ALTER TABLE vendor_bills
  ADD COLUMN data_source text,            -- NULL (=เดิม) | 'line_photo' | 'pdf'
  ADD COLUMN superseded_at timestamptz,   -- เวลาที่ PDF แทนที่
  ADD COLUMN superseded_by_upload text;   -- file_sha256 ของ PDF ที่แทน

CREATE TABLE bill_supersede_log (
  id uuid PK, invoice_id uuid REFERENCES vendor_bills(id),
  mode text,               -- 'auto' | 'manual' | 'undo'
  match_score jsonb,       -- เงื่อนไขที่ตรง/ไม่ตรง
  before jsonb, after jsonb,   -- header + items ครบ (ใช้ undo)
  amount_delta numeric, created_at timestamptz DEFAULT now(), actor text
);
CREATE TABLE bill_supersede_queue (   -- กรณีไม่ชัด รอ TUM เลือก
  id uuid PK, pdf_parsed jsonb, pdf_attachment_url text, file_sha256 text,
  candidate_ids uuid[], status text DEFAULT 'open', created_at timestamptz DEFAULT now()
);
```
บิลเก่าทั้งหมด `data_source = NULL` → พฤติกรรมเดิมทุกอย่าง (backward compatible). RLS: เปิดแบบเดียวกับตาราง vendor_bills ที่มีอยู่ (ตรวจ [[reference_supabase_rls_storage_hardening]] ก่อน apply)

## 5. ขั้นตอนทำงาน (ลำดับนี้จงใจ: ปิดช่องโหว่ก่อนเปิดรับรูป)

### ขั้น A — ด่านกัน merge ข้ามแหล่ง (backend, flag ไม่ต้อง — เป็น bug fix)
ใน `_save_invoice`: ถ้า `existing.data_source == 'line_photo'` และ upload ปัจจุบันเป็น PDF → **ห้าม merge**, ส่งต่อให้ขั้น B แทน. บิลเดิม (data_source NULL) → merge เหมือนเดิมทุกประการ
- test: PDF dedup_key ตรงบิลรูป → items ไม่เพิ่ม; PDF หลายหน้าปกติ → ยัง merge ได้ (regression)

### ขั้น B — PDF แทนที่บิลจากรูป (backend + frontend)
หลัง OCR PDF ครบทุกหน้า (ระดับบิล ไม่ใช่ระดับหน้า) ใน `_process_upload`:
1. หา candidate = บิล `data_source='line_photo' AND superseded_at IS NULL` ย้อนหลัง 120 วัน
2. ให้คะแนน 4 ข้อ: (a) invoice_no ตรงหลัง normalize (ตัดช่องว่าง/ขีด/ศูนย์นำหน้า) (b) merchant_tax_id ตรง 13 หลัก + checksum ผ่าน (c) bill_date ตรง (d) amount ต่าง ≤ max(1 บาท, 10%)
3. **auto** เมื่อ: ตรง ≥3/4 **และ** (a หรือ b) ต้องอยู่ในนั้น **และ** candidate ที่เข้าเกณฑ์มีใบเดียว **และ** PDF ผ่าน `_revalidate_bill` ไม่มี error (เช่น `ITEMS_TOTAL_INCOMPLETE`)
4. ไม่เข้าเกณฑ์ auto แต่มี candidate ตรง ≥2/4 → ลง `bill_supersede_queue` + ไม่สร้างบิลใหม่ (กัน P&L ซ้ำ) + แจ้งใน UI
5. ไม่มี candidate → สร้างบิลใหม่ `data_source='pdf'` ตาม flow เดิม
6. การแทนที่ (transaction เดียวผ่าน RPC/psycopg — ไม่ใช้ supabase-py update หลายครั้ง เพราะ update ไม่ throw):
   - snapshot header+items เดิมลง `bill_supersede_log.before`
   - เขียน header จาก PDF ทับ, ลบ items เดิมแล้ว insert จาก PDF, เพิ่ม attachment PDF (เก็บรูปไว้เป็น attachment เดิม)
   - `data_source='pdf'`, `superseded_at=now()`; **id, payment_status, การจับคู่สลิป/statement, review_status คงเดิม**
   - ถ้าบิลเดิม confirmed แล้วและ `amount_delta != 0` → `review_status='needs_attention'` + เหตุผล "ยอดเปลี่ยนหลัง PDF"
7. Undo: `POST /invoice/{id}/supersede/undo` คืนจาก `before` (ใช้ได้ครั้งล่าสุดเท่านั้น)
8. Frontend: ป้าย 📷/✅ ในรายการบิล, หน้า/แท็บ "คิวรอเลือก" (เลือก candidate หรือ "เป็นบิลใหม่"), แสดงประวัติ supersede + ปุ่ม undo

### ขั้น C — รับรูปจาก BOT Mara (Apps Script + backend, flag `LINE_PHOTO_BILLS_ENABLED` default off)
- Backend: `POST /line-photo-bill/ingest` — auth ด้วย header `X-Ingest-Key` (secret ใหม่ใน Coolify, เทียบด้วย `hmac.compare_digest`), รับ image bytes + sender + LINE messageId; ตอบ 202 ทันทีแล้วทำใน background thread (กัน Cloudflare 524 / Apps Script timeout)
- งาน background: file-hash dedup (reuse `_find_uploaded_file`) → classifier เดิม (`detail:low`) แต่ **กฎใหม่สำหรับ endpoint นี้เท่านั้น**: เฉพาะ `invoice` → OCR+บันทึก `data_source='line_photo'`; `slip`/`other`/error → ไม่สร้างบิล (log อย่างเดียว). ไม่แก้กฎของบอท MARA STATION เดิม
- idempotency: unique ที่ LINE messageId (กัน Apps Script retry ส่งซ้ำ)
- Apps Script: หลังบันทึก Drive สำเร็จ ถ้าเป็นรูป → `UrlFetchApp.fetch(..., {muteHttpExceptions:true})` ส่งต่อ; ล้มเหลวไม่กระทบการเก็บ Drive/การตอบกลับเดิม. secret เก็บใน Script Properties (TUM เป็นคนวาง)
- ผล OCR แจ้งกลับกลุ่ม: push ต้องเสียโควตา → **ไม่ตอบกลับในกลุ่ม** รอบแรก; TUM เห็นในเว็บ (คิว pending เดิม)

### ขั้น D — เช็กลิสต์ "บิลที่ยังไม่มี PDF"
- ตัวกรองในหน้ารายการบิล `data_source='line_photo' AND superseded_at IS NULL` + จำนวนค้าง
- แจ้งสรุปรายสัปดาห์ (ต่อท้าย weekly-summary เดิม `/weekly-summary`) — ไม่สร้าง cron ใหม่

## 6. สิ่งที่จงใจไม่ทำ
- ไม่ auto-confirm บิลจากรูป (modal ยืนยันยังบังคับ — ตามที่ TUM ตัดสิน 09-10)
- ไม่ส่งรูปเข้า Makro local OCR (hybrid = PDF เท่านั้น)
- ไม่ย้อนแก้บิลเก่า
- ไม่เพิ่มบอท MARA STATION เข้ากลุ่ม Mara Bill

## 7. ความเสี่ยง + rollback
| ความเสี่ยง | กัน | rollback |
|---|---|---|
| auto จับคู่ผิด → เขียนทับบิลอื่น | เกณฑ์ 3/4 + ต้องมีเลขบิล/เลขภาษี + candidate เดียว + log + undo | undo ต่อบิล; ปิด auto ด้วย env `PDF_SUPERSEDE_AUTO=0` (ทุกเคสเข้าคิว) |
| P&L ขยับหลัง PDF | needs_attention + amount_delta ใน log | — (ตั้งใจ) |
| รูปสินค้ากลายเป็นบิล | endpoint ใหม่ไม่ fallback other→invoice | ปิด `LINE_PHOTO_BILLS_ENABLED` |
| ขั้น A ทำให้ PDF หลายหน้าปกติไม่ merge | เงื่อนไขเฉพาะ data_source='line_photo' + regression test | revert commit |
| ค่า API | ~฿0.64/รูปบิล + ~฿0.02 classifier/รูป | ปิด flag |

## 8. การทดสอบ
- unit: ขั้น A (merge guard), ตัวให้คะแนน 4 ข้อ (ตาราง case ครบขอบ: ยอดต่าง 9.9%/10.1%, เลขบิลมี/ไม่มี, 2 candidates), transaction rollback เมื่อ insert items ล้ม, undo
- ทั้งชุด pytest เดิมต้องผ่าน (ล่าสุด 696)
- end-to-end บน local + prod read-only ([[reference_vexonhq_local_readonly_prod_probe]]) — **ห้ามทดสอบเขียนบน prod**
- ทดลองจริง: เปิด flag C แล้ว 10 บิลแรก TUM ตรวจทุกใบ; เกณฑ์หยุด = auto จับผิด ≥1 หรือรูปสินค้ากลายเป็นบิล ≥1

## 9. ลำดับส่งงาน
A (+review) → deploy → B (+review) → deploy → C (flag off) → D → เปิด flag C. ทุกขั้นรัน `review.ps1` (Codex — งานเงิน) แล้ว Antigravity ก่อน push; push หลัง TUM ยืนยันเท่านั้น

## 10. คำถามเปิด
1. เกณฑ์ ±10% / 120 วัน เหมาะไหม (บิลค้างสแกนนานสุดกี่วัน?)
2. บิลจากรูปที่ confirm แล้ว และจ่ายเงิน/จับคู่สลิปแล้ว — ถ้า PDF ยอดต่าง ควรแค่ needs_attention หรือบล็อก auto?
