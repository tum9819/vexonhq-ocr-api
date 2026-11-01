ช่วย review โค้ดแบบ adversarial (อ่านอย่างเดียว ห้ามรัน shell command ห้ามแก้ไฟล์)

Repo: C:\Users\rapee\vexonhq-ocr-api · branch `feat/possible-duplicate-warning` · commits `2f36bc7` + `c0516e6` (base `fb20d72` = prod)
ดู diff: `git diff fb20d72..feat/possible-duplicate-warning`

## เปลี่ยนอะไร
`GET /invoice/duplicates` (main.py `invoice_suspected_duplicates`, ~บรรทัด 1107–1205) = แถบเตือน "บิลน่าจะซ้ำ" ในหน้ารอตรวจ
- เดิม: เทียบเฉพาะบิล pending/needs_attention กันเอง (key = ยอด+วันที่ หรือ ยอด+ชื่อร้าน ถ้าไม่มีวันที่)
- ใหม่: ดึงบิล confirmed มาเทียบด้วย; กลุ่มจะถูกส่งออกเฉพาะเมื่อมีบิล pending/needs_attention ≥1 ใบ; บิล confirmed เรียงก่อนเสมอ (frontend ทำเครื่องหมาย bills[0] = "เก็บไว้", ที่เหลือ = "น่าจะปฏิเสธ")
- rejected ยังไม่ถูกนำมาเทียบ · เป็นการเตือนเท่านั้น ไม่ block · frontend ไม่แก้
- เหตุ: วันนี้อัป PDF สิงห์ซ้ำ OCR อ่านเลขบิล/ชื่อร้านต่างจากบิล confirmed เดิม → กลายเป็นบิล pending ใหม่ และแถบเตือนเงียบ

## หลักฐานที่ผมตรวจแล้ว
- tests/test_invoice_duplicates.py: +5 tests (rescan ของบิล confirmed, กลุ่ม confirmed ล้วนไม่โชว์, rejected ไม่เข้ากลุ่ม, confirmed เรียงก่อนแม้อัปทีหลัง, undated match ด้วยชื่อร้าน); fake `.in_()` เปลี่ยนให้กรองจริง — ทั้งชุด 772 passed
- consumers: frontend `VEXONHQ/components/invoice/InvoiceQueueGrid.tsx` (banner + `duplicateBillIds` ใช้ติดป้ายแถวในคิว) — คิวมาจาก view `v_invoice_review_queue` ที่กรอง pending/needs_attention เท่านั้น → id ของ confirmed ไม่ไปติดป้ายแถวใด
- prod (SELECT): กติกาใหม่ให้ 1 กลุ่ม = กลุ่มเดิมที่แสดงอยู่แล้ว

## ข้อจำกัดที่รู้ (AGENTS #78)
1. tooltip ของ bills[0] ยังเขียน "อัปโหลดก่อน" แม้เป็น confirmed ที่อัปทีหลัง
2. ไม่มี row cap ของตัวเอง — เกิน PostgREST max-rows (1000) บิล confirmed บางใบหลุด (เตือนน้อยลง ไม่ error); ตอนนี้ 130 บิล

## ขอให้ตอบ
- แต่ละ finding ต้องมี file:line + สถานการณ์ที่พังจริง (input → ผลผิด) — "PASS"/"No issues" โดยไม่มีหลักฐานจะไม่นับ
- โดยเฉพาะ: มีทางไหนที่การเปลี่ยนนี้ทำให้ของเดิมพัง / แนะนำให้ปฏิเสธบิลที่ confirmed แล้ว / response 500
- สรุปท้าย: APPROVE หรือ REQUEST CHANGES
