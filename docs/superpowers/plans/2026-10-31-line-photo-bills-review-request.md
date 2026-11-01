คุณคือผู้ตรวจแผนงานแบบ adversarial ของระบบบัญชีร้านอาหาร (FastAPI + Supabase Postgres, repo `vexonhq-ocr-api`)
**ห้ามรันคำสั่ง shell ใด ๆ และห้ามแก้ไฟล์** — อ่านอย่างเดียว

## งาน
ตรวจแผน `docs/superpowers/plans/2026-10-31-line-photo-bills-pdf-supersede.md` เทียบกับโค้ดจริง
เป้าหมายของแผน: บิลจากรูปถ่ายในกลุ่ม LINE บันทึกเป็นค่าใช้จ่ายชั่วคราว แล้วเมื่อสแกน PDF ภายหลัง ระบบแทนที่ข้อมูลด้วย PDF บนบิลใบเดิม โดยไม่เกิดบิลซ้ำ และไม่ทำ flow เดิมพัง

## ไฟล์ที่ต้องอ่านประกอบ
- `main.py` — `_save_invoice` (~3623), merge path (~3651-3700, ~3847), `_find_uploaded_file` (~743), `_process_upload` (~815), `GET /invoice/duplicates` (~1096), `_revalidate_bill` (~4105), backup `invoice_item_repairs` (~2282)
- `line_bot_routes.py` — `_process_invoice_from_line` (~267), classifier (~306-370), fallback other→invoice (~2150)

## สิ่งที่ต้องตอบ (ทุกข้อต้องอ้าง ไฟล์:บรรทัด — ข้อที่ไม่มีหลักฐานจะไม่ถูกนับ)
1. ช่องโหว่ P0 ใน §3 จริงไหม (PDF ที่ dedup_key ตรงบิลรูปจะ merge แล้ว items ซ้ำ)? มีเส้นทางอื่นที่ทำให้ซ้ำแบบเงียบที่แผนพลาดไหม
2. ขั้น A (ห้าม merge เมื่อ existing.data_source='line_photo') ครอบคลุมทั้ง 3 จุด merge (dedup_key lookup, invoice_no fallback, create-conflict) ไหม และมีโอกาสทำ PDF หลายหน้าปกติพังไหม
3. เกณฑ์ auto-supersede ใน §5-B ข้อ 3 — ยกตัวอย่างเคสจริงที่จะ **จับคู่ผิดแต่ผ่านเกณฑ์** (เช่น Makro ซื้อ 2 รอบวันเดียวกัน, ใบกำกับเต็ม vs ใบเสร็จของการซื้อเดียว) และเคสที่ควร auto แต่ตกคิว
4. การแทนที่บนบิล id เดิม: มีตาราง/วิว/FK อะไรที่อ้าง invoice_items.id หรือค่าบน vendor_bills ที่จะเสียหายเมื่อลบ-insert items ใหม่ (เช่น การจับคู่สลิป, monthly-by-sku, stock, P&L views)
5. ความถูกต้องของ transaction (แผนบอกใช้ RPC/psycopg เพราะ supabase-py update ไม่ throw) — มีจุดไหนที่ยัง partial-write ได้
6. endpoint `/line-photo-bill/ingest`: auth, idempotency, background thread บน VPS 2 vCPU, ผลกระทบต่อบอท MARA STATION เดิม
7. สิ่งที่แผนควรตัดออกเพื่อให้เรียบง่ายขึ้น โดยไม่ลดความปลอดภัยของข้อมูลเงิน

## รูปแบบคำตอบ
ตารางเดียว: `# | ความรุนแรง (P0/P1/P2) | หลักฐาน ไฟล์:บรรทัด | ปัญหา | ข้อเสนอแก้`
ปิดท้ายด้วย verdict: `APPROVE` / `APPROVE WITH CHANGES` / `REJECT` พร้อมเหตุผล 1-2 บรรทัด
ห้ามตอบ "PASS" ลอย ๆ โดยไม่มี citation
