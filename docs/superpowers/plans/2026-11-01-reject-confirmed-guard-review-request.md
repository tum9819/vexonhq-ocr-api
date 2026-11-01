ช่วย review โค้ดแบบ adversarial (อ่านอย่างเดียว ห้ามรัน shell command ห้ามแก้ไฟล์)

2 repo · branch `fix/reject-confirmed-guard` ทั้งคู่
- Backend `C:\Users\rapee\vexonhq-ocr-api` commit `60c5a00` (base `c1f94e1` = prod) — `git diff c1f94e1..fix/reject-confirmed-guard`
- Frontend `C:\Users\rapee\VEXONHQ` commit `6cc46c6` (base `aa673a8`) — `git diff aa673a8..fix/reject-confirmed-guard`

## เปลี่ยนอะไร (ต่อจาก finding ของคุณรอบที่แล้ว: ปุ่มปฏิเสธใช้กับบิล confirmed ได้)
- Backend `main.py` `invoice_reject`: อ่าน review_status ก่อน; ถ้า `confirmed` และ body ไม่มี `allow_confirmed` ที่เป็น `True` จริง ๆ → 409 ไม่แตะแถว. pending/needs_attention เหมือนเดิม. บิลไม่มี → 404 (เช็กก่อน update)
- Frontend `lib/invoice-api.ts` `rejectInvoice(..., allowConfirmed=false)` ส่ง `allow_confirmed`
- `components/invoice/InvoiceDetailView.tsx`: ใน modal ปฏิเสธ ถ้าบิล confirmed → กล่องแดง + checkbox; ปุ่มปฏิเสธ disabled จนกว่าติ๊ก; reset ทุกครั้งที่เปิด modal
- `InvoiceUploadFlow.tsx:259` ไม่แก้ (ปฏิเสธบิลที่เพิ่งอัป = pending) — ถ้าเป็น confirmed จะได้ 409 ซึ่งปลอดภัย
- ลำดับ deploy: frontend ก่อน (backend เก่าไม่สนฟิลด์เกิน) แล้วค่อย backend

## หลักฐาน
- tests/test_invoice_reject_confirmed_guard.py 7 tests (pending ผ่าน, confirmed ไม่มี flag → 409, "true"/1/"yes" ไม่นับ, flag True → rejected, ไม่มีบิล → 404); ทั้งชุด 780 passed
- frontend: `tsc --noEmit` exit 0, eslint ไฟล์ที่แก้ exit 0

## ข้อจำกัดที่รู้
- read-then-update ไม่ atomic: ถ้ามีคนกด confirm ระหว่างอ่านกับเขียน (มิลลิวินาที) บิลอาจถูกปฏิเสธโดยไม่มี flag — ยอมรับได้เพราะเป็นงาน admin คนเดียว
- ยังไม่มี "ยกเลิกปฏิเสธ" — ไม่อยู่ใน scope

## ขอให้ตอบ
- แต่ละ finding ต้องมี file:line + สถานการณ์ที่พังจริง (input → ผลผิด) — "PASS" ไม่มีหลักฐานไม่นับ
- โดยเฉพาะ: caller อื่นของ reject ที่จะพังเพราะ 409 / ทางที่ยังปฏิเสธบิล confirmed ได้โดยไม่ติ๊ก / 500
- สรุปท้าย: APPROVE หรือ REQUEST CHANGES
