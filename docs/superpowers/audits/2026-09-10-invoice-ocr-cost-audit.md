# Invoice OCR cost audit — Phase 1 (AUDIT ONLY, no production change)

Date: 2026-09-10 · Author: Claude Code · Status: **DRAFT for TUM review** (nothing in production was touched)
Scope: `vexonhq-ocr-api` (backend) + `VEXONHQ` (frontend) invoice upload → OCR → save.
Evidence sources: code on `main` (backend HEAD `7fa763f`, frontend HEAD `f1c3886`), production DB via Supabase MCP (read-only SQL), `/health/deep`, git history.
Rule followed: every number below is measured or read from code; anything inferred is labelled *(inferred)*.

---

## 0. TL;DR (อ่านตรงนี้พอถ้ารีบ)

| ข้อ | ผลตรวจ |
|---|---|
| ค่า API จริงต่อการเรียก vision 1 ครั้ง | **฿0.64** (avg; มิ.ย. ฿0.71, ก.ค. ฿0.61) — วัดจาก `ai_call_log` 134 calls |
| ต่อ Invoice (ถ่วงน้ำหนัก 1.4 หน้า/ใบ) | **≈ ฿1.0** · 100 ใบ ≈ ฿100 · 1,000 ใบ ≈ ฿1,000 (~$27) — รวม retry/repair แล้ว +15% |
| เงินที่จ่ายจริงให้ OCR ทั้งระบบ มิ.ย.–ก.ย. 2026 | **≈ ฿148 ใน 3 เดือน** (vision ฿86 + slip ฿34 + LINE classifier ฿28) |
| Token ที่จ่ายต่อ call ไปที่ไหน | **68% = prompt คงที่ (3,424 tok)**, 22% = รูป (~1,105 tok), ~27% = Tesseract hint (สูงสุด ~1,400 tok) — และ **OpenAI prompt-cache ไม่ทำงานข้ามบิลที่อัปทางเว็บ** เพราะ hint ถูกแทรกไว้ต้นข้อความ (prefix คงที่แค่ 68 tok < 1,024 ขั้นต่ำ) — *แก้ถ้อยคำหลัง review: call retry (hint เดิม) และ LINE (hint ว่าง) มี prefix เหมือนกันจึง cache ได้; telemetry ไม่ได้เก็บ cached_tokens เลยพิสูจน์ตรง ๆ ไม่ได้* |
| ไฟล์เดิมถูก OCR ซ้ำไหม | ฝั่งเว็บ: **ไม่** — มี SHA-256 dedup ก่อน OCR อยู่แล้ว (#59). ฝั่ง LINE: **ซ้ำได้** — ไม่มี hash, ไม่มี Tesseract hint, ไม่มี retry ครบถ้วน |
| เก็บ raw OCR ไว้ไหม | เก็บ **แค่ผล GPT ของหน้า 1 + ยอด backfill** ใน `vendor_bills.ocr_json`; Tesseract text **ไม่เก็บ**; ผล GPT หน้า 2+ **ไม่เก็บ** (เหลือแค่ items ที่ insert) → reuse ได้บางส่วน |
| จุดเปลืองที่พบ (เรียงตามผลกระทบ) | (1) prompt-cache ไม่ทำงาน (2) บิลหลายหน้า **หน้าสุดท้ายยิง retry ทุกครั้ง** โดยโครงสร้างสำเร็จไม่ได้ (3) LINE classifier ส่งรูป high-detail ให้ gpt-4o-mini = 25,756 tok/ครั้ง (แพงเท่า gpt-4o) (4) Tesseract+denoise รันทุกหน้าก่อน vision — กิน CPU เป็นต้นเหตุ 524 (5) upload หน้าเว็บยิงหลายไฟล์พร้อมกัน ไม่ได้เรียงคิว |
| Local OCR ใส่ตรงไหนได้โดยไม่กระทบเดิม | ใน `_ocr_page()` ระหว่าง step 1 (Tesseract — **มีอยู่แล้ว**) กับ step 2 (GPT vision) หลัง feature flag — เพราะ local OCR pass รันอยู่แล้วทุกหน้า แค่ยังไม่ถูก "ใช้" |
| Engine ที่แนะนำ | **Stage 1 = Tesseract ที่มีอยู่** (image_to_data + vendor template + validator) → วัดก่อน; **Stage 2 (ถ้าไม่พอ) = RapidOCR/PP-OCRv5 ผ่าน onnxruntime** (ไม่มี paddlepaddle). **ห้าม** paddlepaddle / EasyOCR / torch บนเครื่องนี้ — PaddleOCR เคยล้มบน VPS นี้ 2026-05-12 (4 commit ใน 30 นาทีแล้วถอย) |
| Server | 2 vCPU / 4 GB / RAM ใช้ 50% / แชร์ ~6 แอป — Tesseract อยู่แล้ว (0 เพิ่ม); RapidOCR +~400–600 MB RSS (พอแต่ตึงตอน Coolify build); ไม่ต้องใช้ GPU |
| **คำแย้งสำคัญ** | **ค่า API ไม่ใช่ปัญหาจริงในเชิงเงิน** (฿1,000/1,000 ใบ) — ผลตอบแทนของ Hybrid Local OCR อยู่ที่ *ความนิ่ง (deterministic) กับ vendor ซ้ำ, ไม่พึ่ง OpenAI ตอนล่ม, และลด CPU/524 ตอนอัปยกชุด* ไม่ใช่ประหยัดบาท — ถ้า backlog < ~2,000 ใบ ทำ "quick wins" (§7) แล้วอัปเลยคุ้มกว่าสร้างระบบใหม่ |

---

## 1. Flow ปัจจุบัน (Upload → OCR → AI → Parse → Validate → Save)

### 1.1 ทางเข้า 3 ทาง

| ทางเข้า | ไฟล์ | หมายเหตุ |
|---|---|---|
| **Web** `POST /invoice/upload` | `main.py:705` → `_process_upload` `:815` | ทางหลัก, มี SHA-256 dedup, มี Tesseract hint, มี completeness retry |
| **LINE bot** (ส่งรูปเข้า OA) | `line_bot_routes.py:2078-2107` | classifier (gpt-4o-mini) → `_ocr_invoice_image` `:220` → `_run_gpt_vision(bytes,"image/jpeg","")` → `_save_invoice_from_line` `:234` — **ไม่มี** hash / hint / retry |
| **Repair** `POST /invoice/{id}/reocr-items` | `main.py:2321` | admin เท่านั้น, OCR ใหม่จาก attachment ใน storage, preview → apply แบบ WYSIWYG |
| (legacy) `POST /ocr` | `main.py:622` | Tesseract อย่างเดียว, ฟรี, frontend ไม่ได้ใช้แล้ว |

### 1.2 Web path ทีละขั้น (`_process_upload`)

```
contents (bytes)
 ├─ sha256(contents)                                    main.py:822
 ├─ image?  → _find_uploaded_file(hash, 1)             :743   ← hit = return already_uploaded, 0 API
 ├─ PDF?    → _pdf_to_images(scale=3.0 → 216 DPI PNG)  :2535  (pypdfium2, ~1.6 MB/หน้า)
 │            cap 40 หน้า / 25 MB                       :725, :846
 │            _find_uploaded_file(hash, n_pages)        :851   ← partial upload = re-process
 ├─ Phase 1  ThreadPoolExecutor(min(3,n)) → _ocr_page  :879-881  (ขนานสูงสุด 3 หน้า)
 │     _ocr_page (:905)
 │       1) _run_tesseract(img)      :2564  cv2 fastNlMeansDenoising + threshold + tesseract tha+eng psm6
 │       2) _run_gpt_vision(img, hint) :2932  gpt-4o, Structured Outputs (OCR_STRUCTURED=1), temp 0.7, max_tokens 6000
 │             └ fallback regex Makro totals จาก hint   :2874 (ถ้า subtotal/vat/amount หรือ discount หาย)
 │       3) _items_tie_state(amount, items)  :3050  ไม่ tie ±10% → _run_gpt_vision อีก 1 ครั้ง + corrective note (:934-976)
 └─ Phase 2  sequential per page → _persist_invoice_page :988
         _validate_invoice → _upload_to_storage → _save_invoice (merge by dedup_key / invoice_no) → _insert_items → _revalidate_bill
```

### 1.3 ผลลัพธ์ที่ถูกเก็บ (สำหรับคำถาม "reuse ได้ไหม")

| สิ่งที่เกิดขึ้น | เก็บที่ | เก็บครบไหม |
|---|---|---|
| ไฟล์ต้นฉบับ / หน้า PNG | Supabase storage `uploads` + `attachments.file_url` | ✅ ทุกหน้า (PDF เก็บเป็น PNG ที่ render แล้ว **ไม่ใช่ PDF ต้นฉบับ**) |
| SHA-256 ของไฟล์อัป | `attachments.file_sha256` | ✅ เฉพาะบิลหลัง 2026-06-09 (22/139 หน้าของบิล confirmed) — ของเก่า NULL |
| Tesseract text | — | ❌ ส่งเข้า `_save_invoice(ocr_text=…)` `:3588` แต่ **ไม่ถูกเขียนลง DB ที่ไหนเลย** |
| ผล GPT หน้า 1 | `vendor_bills.ocr_json` | ✅ |
| ผล GPT หน้า 2+ | — | ❌ `_merge_ocr_json` `:3559` backfill เฉพาะ scalar; items หน้า 2+ เหลือแค่แถวใน `invoice_items` (ผ่าน filter `_is_real_item`) |
| token / latency / ok ต่อ call | `public.ai_call_log` (task=`vision_ocr`) | ✅ แต่ **ไม่มี cached_tokens, ไม่ผูก invoice_id/page, ไม่บอกว่าเป็น retry** |

→ ตอบข้อ 4: **reuse ได้แค่ header + items หน้า 1 ของบิลเดิม** (ที่ `_find_uploaded_file` ทำอยู่แล้ว); ไม่มีอะไรให้ "อ่านซ้ำโดยไม่เรียก API" สำหรับหน้า 2+ หรือสำหรับ Tesseract

---

## 2. API / Model ที่ใช้ และจำนวนครั้งต่อ Invoice

### 2.1 Model registry (`llm.py:50-60`)

| task | model | ใช้ที่ |
|---|---|---|
| `vision_ocr` | gpt-4o (`OPENAI_VISION_MODEL`) | invoice OCR ทุกทาง + repair |
| `line_image_classify` | gpt-4o-mini | LINE เท่านั้น, ก่อน OCR ทุกรูป |
| `slip_vision` | gpt-4o | สลิปโอน (นอก scope แต่เกี่ยว) |
| `classify` | gpt-4o-mini | product_classifier หลัง confirm (canonical_sku) — *นอก scope OCR* |

Client: `OpenAI(timeout=180)` `llm.py:132` — **`max_retries` ไม่ได้ตั้ง = SDK default 2** → error 429/5xx/timeout จะ retry เงียบ ๆ สูงสุด 3 attempt และ `ai_call_log` เห็นแค่ผลสุดท้าย 1 แถว *(อ่านจาก code; ยังไม่เคยเห็น 429 ใน log)*

### 2.2 จำนวน API call ต่อ Invoice — จาก code path

| กรณี | classifier (mini) | vision (gpt-4o) | ที่มา |
|---|---|---|---|
| รูปเดี่ยว ผ่านเว็บ, items tie | 0 | **1** | `_ocr_page` |
| รูปเดี่ยว ผ่านเว็บ, items ไม่ tie | 0 | **2** | +corrective retry `:934` |
| รูปเดี่ยว ผ่าน LINE | **1** | **1** (ไม่มี retry) | `line_bot_routes.py:2096,2106` |
| PDF N หน้า | 0 | **N + 1** (เมื่อยอดรวมอยู่หน้าสุดท้ายและ items หน้านั้น < 90% ของยอด — Makro ทุกใบใน DB) | หน้าสุดท้ายยิง retry — ดู §3.2 |
| อัปไฟล์เดิมซ้ำ (เว็บ, หลัง 06-09) | 0 | **0** | `_find_uploaded_file` |
| อัปไฟล์เดิมซ้ำ (เว็บ แต่บิลเก่าก่อน 06-09 / หรือ LINE) | 0-1 | **เต็มจำนวน** | ไม่มี hash |
| vision fail กลางทางหน้า k ของ N | 0 | **k** เสียเปล่า, กด retry = **N+1 ใหม่ทั้งหมด** | `_ocr_page` raise → ยังไม่ persist → hash ไม่อยู่ใน DB |
| Repair `reocr-items` | 0 | **N ต่อรอบ** (non-deterministic — เกิน 2 รอบเปลืองเปล่า, memory 2026-07-15) | `:2321` |

### 2.3 หลักฐานจาก production (`ai_call_log`, task=`vision_ocr`, ok=true)

| เดือน | calls | avg prompt tok | avg completion tok | ฿/call | avg วินาที |
|---|---|---|---|---|---|
| 2026-06 | 44 | 4,512 | 818 | 0.71 | 11.9 |
| 2026-07 | 89 | 5,351 | 326 | 0.61 | 5.5 (ส่วนใหญ่คือ repair 24 บิล) |
| 2026-08 | 1 | 4,588 | 172 | 0.48 | 2.6 |
| **รวม** | **134** (+6 fail = key incident #64 ไม่ใช่ OCR) | 5,070 | 486 | **0.64** | 7.2 |

บิลใน `vendor_bills` ทั้งหมด **121 ใบ** (พ.ค. 101, มิ.ย. 15, ก.ค. 4, ส.ค. 1) · หน้าเฉลี่ย 1.36–1.73 · **70/101 ใบเดือน พ.ค. มาจาก LINE** · confirmed 107 ใบ

---

## 3. จุดที่เกิดค่าใช้จ่าย/งานเกินจำเป็น (เรียงตามผลกระทบ)

### 3.1 Prompt caching ไม่เคยทำงาน — เสีย ~24% ของค่า call ทุกครั้ง
- `VISION_PROMPT` = **3,424 tokens** (วัดด้วย tiktoken o200k) = 68% ของ input เฉลี่ย
- OpenAI cache อัตโนมัติต้องมี prefix **เหมือนกันเป๊ะ ≥1,024 tok** — แต่ `{ocr_hint}` ถูกแทรกที่ token ที่ **68** (`main.py:2594-2598`) → prefix คงที่ = 68 tok → ไม่เคยเข้าเงื่อนไข
- ถ้าย้าย hint (และรูป) ไปท้าย prompt: 3,355 tok คงที่ → cache hit ราคา 50% → ประหยัด ≈ ฿0.15/call (~24%) ตอนอัปหลายใบติดกัน (cache TTL 5–10 นาที) — **ไม่เปลี่ยน model/contract** แต่เป็นการเปลี่ยนลำดับ prompt → ต้องผ่าน shadow/benchmark ก่อน (ลำดับ prompt กระทบพฤติกรรมโมเดลได้)

### 3.2 บิลหลายหน้า: หน้าสุดท้ายยิง corrective retry ทุกครั้ง โดยที่สำเร็จไม่ได้ *(อ่านจาก code; ยืนยันเงื่อนไขด้วยข้อมูลจริง 2026-09-10: บิล Makro หลายหน้าใน DB ทั้ง 9 ใบ มี items หน้าสุดท้ายรวมแค่ 0.5–20% ของยอดบิล → ratio 4–212 อยู่นอก band ±10% ทุกใบ; ยังไม่มีบิลหลายหน้าอัปหลัง 07-15 ให้เห็นใน ai_call_log)*
- `_ocr_page` `:934` เช็ค tie **ต่อหน้า**: `amount` ของหน้า vs `items` ของหน้า
- Makro N หน้า: หน้า 1..N-1 amount=null → ok; **หน้า N มี amount = ยอดรวมทั้งบิล แต่ items = ของหน้า N เท่านั้น** → ratio ≫ 1.10 → retry ฿0.64 → ผล retry ก็ไม่ tie → "keeping first parse"
- ผล: **+1 call เสียเปล่าต่อบิลหลายหน้าทุกใบ** (Makro = 15/107 ใบ, 63/139 หน้าใน corpus คือ PDF)
- **ความเสี่ยงด้านความถูกต้องแฝง:** `_tie_score` `:959` เลือกผลที่ ratio ใกล้ 1 กว่า — ถ้า retry (ที่ถูกสั่งว่า "คุณอ่านตกแน่ ๆ") เติมแถวเกินจริงจนใกล้ยอดรวม จะถูกเลือกแทนของจริง. ไม่มี test คลุมกรณีนี้ (`tests/test_items_tie.py` เทสแค่ `_validate_invoice` bill_level) — ควรเก็บ log แยก first/retry ใน shadow phase เพื่อดูว่าเคยเกิดไหม

### 3.3 LINE classifier ส่งรูป high-detail ให้ gpt-4o-mini — แพงกว่าที่ comment บอก ~4 เท่า
- `line_bot_routes.py:300-317` ไม่ตั้ง `detail` → auto=high → gpt-4o-mini คิด token รูป ×33 → **p50 = 25,637 tok/call** (= 4 tiles: (85+170×4)×33.33 + text) → ≈ ฿0.14/รูป (comment บอก "~$0.001" = ฿0.04)
- ใช้ `"detail": "low"` → ~2,970 tok → ≈ ฿0.016 (−89%) — งาน "สลิป vs บิล" แยกได้ที่ 512px แน่นอน แต่ต้อง verify กับรูปจริงก่อนตามกติกา
- ยอดจริง 3 เดือน: 197 calls ≈ ฿28 (เล็ก แต่ = 20% ของต้นทุนบิล LINE 1 ใบ)

### 3.4 Tesseract + `fastNlMeansDenoising` รันทุกหน้า **ก่อน** vision — ต้นทุนเป็น CPU ไม่ใช่เงิน
- `_run_tesseract` `:2564` บนภาพ 1786×2525 (216 DPI) — denoise NLM เป็น O(pixels×window) ช้ามากบน CPU; known-issue 2026-06-09 อนุมาน ~60 วิก่อน vision call แรกตอนอัป 3 ไฟล์พร้อมกัน → 524 (**ยังไม่มี instrumentation รายขั้น** — known-issue ระบุว่า step 1 = วัดก่อน)
- hint ถูกใช้ 2 ทาง: (a) แปะใน prompt (~1,400 tok = ฿0.13/call ที่ *อาจ* ไม่ช่วยความแม่น — ยังไม่เคยวัด A/B) (b) regex fallback Makro totals `:2874`
- **นี่คือข้อดีของแผน Hybrid:** local OCR pass "ฟรี" ที่รันอยู่แล้ว ปัจจุบันถูกใช้แค่เป็น hint

### 3.5 Frontend อัปหลายไฟล์พร้อมกัน ไม่ได้เรียงคิว
- `InvoiceUploadFlow.tsx:83-86` comment ว่า "sequentially" แต่ `newItems.forEach(processItem)` ไม่ `await` → k ไฟล์ = k request พร้อมกัน → แต่ละ request เปิด pool 3 thread → 2 vCPU อิ่มทันที → 524 → user กด retry (`:128`) — ถ้า request แรกยังไม่ persist (hash ยังไม่ลง DB) = **OCR ซ้ำเต็มจำนวน** (race window = ระยะ OCR phase ทั้งหมด)
- สำหรับ backlog "จำนวนมาก" นี่คือความเสี่ยงอันดับ 1 ของ *เงินและเวลา* มากกว่าราคาต่อ call

### 3.6 LINE path ไม่มี file-hash → ส่งรูปเดิมซ้ำ = OCR ซ้ำ + บิลซ้ำได้ถ้า dedup_key ไม่ตรง (vendor/invoice_no drift)
- `_save_invoice_from_line` `:234` ไม่ส่ง `file_sha256`; ไม่เรียก `_find_uploaded_file`
- หลักฐาน drift: Wealimex สะกด **5 แบบ**, Makro 3 แบบ, BB Superstore 3 แบบ ใน `vendor_name` (query §5) → dedup_key ไม่ช่วยข้าม spelling

### 3.7 เล็กน้อย
- SDK `max_retries` default 2 (§2.1) — retry ที่มองไม่เห็นใน telemetry
- ผลหน้า 2+ และ Tesseract text ไม่ถูกเก็บ (§1.3) → repair ต้อง OCR ใหม่เสมอ
- `ai_call_log` ไม่ผูกกับ invoice/page/attempt → คำนวณ "call ต่อบิล" ต้องอนุมาน

---

## 4. ประเมินค่าใช้จ่าย (จากตัวเลขจริง §2.3 + list price gpt-4o $2.50/$10 per 1M, `llm.py:86`, USD_THB 36.5)

| หน่วย | ฿ | วิธีคิด |
|---|---|---|
| 1 vision call | **0.64** | 5,070 in × 2.5 + 486 out × 10 (÷1M) × 36.5 |
| รูปเดี่ยว ผ่านเว็บ | 0.64 + 20%×0.64 (retry) ≈ **0.77** | อัตรา not-tie ประมาณจาก backlog 24/98 ใบ *(inferred)* |
| รูปเดี่ยว ผ่าน LINE | 0.14 + 0.64 ≈ **0.78** | ไม่มี retry แต่มี classifier |
| Makro 3 หน้า (PDF) | 3×0.64 + 0.64 (retry หน้าสุดท้าย) ≈ **2.56** | §3.2 |
| **ต่อ Invoice ถ่วงน้ำหนัก** (1.4 หน้า/ใบ, 14% Makro) | **≈ 1.0** | |
| **100 Invoice** | **≈ ฿100–115** | +15% repair/manual-retry/fail-mid-PDF |
| **1,000 Invoice** | **≈ ฿1,000–1,150** (~$27–31) | |
| เคยจ่ายจริงทั้งระบบ OCR มิ.ย.–ก.ย. | **≈ ฿148** | vision ฿86 + slip ฿34 + classifier ฿28 |

หมายเหตุ: ราคาใน `llm.PRICES` เป็น list price 2026-06-01 (override ได้ด้วย `AI_PRICES_JSON`) — ควรเทียบกับบิล OpenAI จริงของ TUM 1 ครั้ง

---

## 5. Field จริงที่ระบบใช้ (ห้ามสร้าง schema ใหม่)

**Header (`vendor_bills`)**: `vendor_name, merchant_tax_id, invoice_no, bill_date, due_date, subtotal, vat, amount, currency, payment_type, notes` + `ocr_json` (มี `discount{line_items_discount_pct, whole_bill_discount_amount, whole_bill_discount_pct, note}`, `field_confidence{}`, `image_quality{}`)
**Items (`invoice_items`)**: `line_no, sku, product_name, quantity, unit, unit_price, amount, source_page` (+ `canonical_sku` จาก classifier หลัง confirm)
**Validation ที่มีอยู่** (`_validate_invoice` `:3104`): `MISSING_VENDOR/TOTAL/INVOICE_NO/TAX_ID, VAT_MISMATCH, ITEMS_SUBTOTAL_MISMATCH, ITEMS_TOTAL_INCOMPLETE (bill-level, error), DISCOUNT_CALCULATION_MISMATCH, HIGH_VALUE, LOW_CONFIDENCE, LOW_IMAGE_QUALITY`
**ที่ยังไม่มี**: เช็ค checksum เลขผู้เสียภาษี 13 หลัก (mod-11) — เป็น validator local ที่แรงมากสำหรับ confidence score
**Coverage ใน 107 ใบ confirmed**: amount 100%, bill_date 95%, subtotal 91%, vat 79%, tax_id 79% (32 tax id ไม่ซ้ำ), invoice_no 74%

**Vendor concentration** (สำคัญต่อ template approach): Singha family 24 ใบ/฿384k · Wealimex family 19 · BB Superstore 16 · Makro (CP Axtra) 15 → **4 ตระกูล = ~69% ของบิล** และทั้ง 4 เป็น**เอกสารพิมพ์/สแกน layout คงที่** (ตัวอย่าง Makro `Desktop/MaraStation/69.04.23 Inv.Makro.PDF`: สแกน 600 DPI, ไม่มี text layer — เช็คด้วย pdfplumber แล้ว = 0 ตัวอักษร, 1 ภาพ/หน้า)

---

## 6. Local OCR ใส่ตรงไหน (ไม่กระทบเดิม)

```
_ocr_page(image, ...)                                  main.py:905
  1) ocr_text = _run_tesseract(image)                  ← มีอยู่แล้ว (เปลี่ยนเป็น image_to_data เพื่อได้ bounding box)
  1.5) [NEW, behind LOCAL_OCR_ENABLED]
       local = local_extract(ocr_text/boxes, vendor_template)   ← header + items + confidence
       log ocr_runs(engine='tesseract', mode=shadow|hybrid, confidence, reasons, ms)
       if HYBRID_OCR_ENABLED and local.confidence >= threshold:
            parsed = local.parsed ; api_calls = 0 ; return       ← ข้าม step 2-3 ทั้งหมด
  2) parsed = _run_gpt_vision(image, hint)             ← เดิม ไม่แตะ (fallback / safety net)
  3) completeness retry                                ← เดิม
```
- `_persist_invoice_page`, `_save_invoice`, `_validate_invoice`, response shape ของ `/invoice/upload` **ไม่เปลี่ยน** → API contract เดิม 100%
- LINE path เรียก `_run_gpt_vision` ตรง (ข้าม `_ocr_page`) → ต้องตัดสินใจว่าจะให้ LINE เข้าทางเดียวกันไหม (แนะนำ: ใช่ แต่เป็นงานแยก)
- Flags (env ใน Coolify, อ่านตอน import เหมือน `OCR_STRUCTURED`): `LOCAL_OCR_ENABLED=0/1` (รัน local + log), `HYBRID_OCR_ENABLED=0/1` (ใช้ผล local จริง), `LOCAL_OCR_MIN_CONFIDENCE` — ปิดทั้งคู่ = โค้ดเดิมเป๊ะ

---

## 7. คำแนะนำ engine + ลำดับที่ควรทำ (พร้อมคำแย้ง)

### 7.1 คำแย้งก่อน: เงินไม่ใช่เหตุผลหลัก
ที่ ฿0.64/call, **1,000 ใบ ≈ ฿1,000** — งาน Hybrid เต็มรูปแบบ (Phase 2–4) คือหลายสัปดาห์ + ความเสี่ยงบน VPS 4 GB ที่แชร์ 6 แอป + ความเสี่ยงตัวเลขการเงินผิดจาก OCR ที่แม่นน้อยกว่า GPT-4o. ตาม lean bar ของ TUM ควรตัดสินจาก: (ก) backlog กี่ใบจริง (ข) ต้องการ line items ของบิลเก่าไหม (P&L cash-basis ใช้แค่ `vendor_bills.amount`) (ค) ผลตอบแทนที่ไม่ใช่เงิน = deterministic กับ vendor ซ้ำ, ไม่ตายเมื่อ OpenAI ล่ม (เคยตาย 3 วัน #64), และตัด CPU/524

### 7.2 Quick wins ที่ควรทำ *ก่อน* Local OCR (ไม่เปลี่ยน architecture, ปิด/เปิดด้วย env, ต้องผ่าน benchmark เดิม `tests/ocr_golden/compare.py`)
1. **ย้าย OCR hint + รูปไปท้าย prompt** → เปิด prompt cache (−~24%/call **เฉพาะ call ที่ห่างกันไม่เกิน 5–10 นาที** คือตอนอัปหลายใบติดกัน; อัปวันละใบ = 0) + log `cached_tokens` ใน `_log_ai_call` — *self-review 2026-09-10: เมื่อ Makro ย้ายไป local แล้ว call ที่เหลือคือ vendor อื่น ~30% ของบิล → ประหยัดจริงหลักไม่กี่บาท/เดือน ไม่คุ้มค่า compare ฿25 + ความเสี่ยงเปลี่ยน prompt → เลื่อนไปหลัง Makro ship*
2. **LINE classifier `detail:"low"`** (−89% ของ call นั้น)
3. **ปิด corrective retry เมื่อ `page_no < n_pages` หรือหน้าที่มี amount แต่ items เป็นของหน้าเดียว** — ย้าย tie-retry ไปทำ *bill-level* หลัง merge (ถ้ายังไม่ tie ค่อย retry เฉพาะหน้า) → −1 call/บิลหลายหน้า + ปิดความเสี่ยง §3.2
4. **Instrumentation รายขั้น** (upload / render / tesseract / vision / persist, ms) ลง `ocr_runs` — known-issue ค้างมาตั้งแต่ 06-09 และเป็น prerequisite ของทุก phase ถัดไป
5. **Frontend เรียงคิวอัปทีละไฟล์** (เปลี่ยน forEach → for-await) — กัน 524/OCR ซ้ำตอนอัป backlog
**Rollback ต่อข้อ (เพิ่มหลัง review #9):** ข้อ 1 env `OCR_HINT_AT_END=0` · ข้อ 2 env `LINE_IMAGE_CLASSIFY_DETAIL=auto` (= พฤติกรรมเดิมเป๊ะ เพราะเดิมไม่ส่ง `detail`; `high` = บังคับคุณภาพสูงสุด) · ข้อ 3 env `OCR_PAGE_RETRY_MULTIPAGE=1` (คืนพฤติกรรมเดิม) · ข้อ 4 ไม่มีผลต่อพฤติกรรม · ข้อ 5 frontend ไม่มี env — revert commit + redeploy (~1 นาที). **เกณฑ์ที่ต้อง rollback:** LINE จัดประเภทสลิป/บิลผิด ≥ 1 ครั้งใน 20 รูปแรก · อัตรา 524/upload สูงกว่าก่อนเปลี่ยน · บิลหลายหน้าโดน `ITEMS_TOTAL_INCOMPLETE` มากกว่าเดิม 2 เท่าใน 10 ใบแรก
**ข้อ 5 ออกแบบใหม่หลัง review รอบ 1 #10 + รอบ 2 #3:** แยก 2 คิว — (ก) *network*: อัปทีละไฟล์ (ตัวถัดไปเริ่มเมื่อ response ของตัวก่อนกลับมา ไม่รอคน) (ข) *review*: ผลที่เสร็จแล้วเข้าคิว, modal โชว์หัวคิว, กดยืนยัน/ปฏิเสธ/ปิด(X)/อัปใหม่ = pop แล้วโชว์ใบถัดไป; ปิด(X) ปล่อยบิลเป็น `pending_review` ไปตรวจทีหลังในหน้าคิว ไม่บล็อกการอัป; งานที่อัปเสร็จ**ห้าม** `setShowingModal` ตรง ๆ (`showingModal` เป็น state เดียว ทับใบที่ยังไม่ตรวจ)
6. (ทดสอบก่อน) **Tesseract แบบ lazy**: เรียก vision ก่อน, รัน Tesseract เฉพาะเมื่อต้อง fallback regex — ตัด CPU ~80% *แต่ขัดกับแผน Hybrid ที่อยาก local ก่อน* → ตัดสินหลังเห็นผล shadow

**Implementation status 2026-09-11 (ยังไม่เปิด Local OCR):** ข้อ 2 ทำแล้วแบบ explicit `detail` โดย default `LINE_IMAGE_CLASSIFY_DETAIL=low` และคืนพฤติกรรมเดิมได้ด้วย `auto` (`high` = คุณภาพสูงสุด); ข้อ 3 ทำแล้วโดยปิด page-level corrective retry เฉพาะ PDF หลายหน้า และคืนพฤติกรรมเดิมได้ด้วย `OCR_PAGE_RETRY_MULTIPAGE=1`; ข้อ 5 อยู่ใน frontend commits `d34db72` + review fixes `32eb068` (network queue + review queue). ข้อ 1/4/6 ยัง deferred. Backend focused regression: `tests/test_line_image_classifier.py`, `tests/test_ocr_parallel_pages.py`, `tests/test_items_tie.py`, `tests/test_ocr_file_hash.py` — tests use mocks only andไม่เรียก OpenAI.

### 7.3 Engine
| ตัวเลือก | ไทย | RAM/CPU บน 2 vCPU/4 GB | ความเห็น |
|---|---|---|---|
| **Tesseract 5 tha+eng** (ติดตั้งอยู่แล้ว, Dockerfile) | พอใช้บนเอกสารพิมพ์/สแกน, แย่บนรูปถ่ายมือถือ | 0 เพิ่ม | **Stage 1** — ใช้ `image_to_data` (กล่องคำ) + template คอลัมน์ + SKU→ชื่อจากประวัติ `invoice_items` (Makro article no. 6 หลัก, Singha รหัสสินค้า) |
| **RapidOCR** (PP-OCRv5 บน onnxruntime, ไม่มี paddlepaddle) | PP-OCRv5 มี multilingual rec — **ต้อง verify ว่ามีโมเดลไทยจริงตอน POC** | +~400–600 MB RSS, ~3–8 s/หน้า *(inferred)* | **Stage 2** ถ้า Tesseract ตกเกณฑ์ชื่อสินค้าไทย; lazy-load, จำกัด 1 worker |
| PaddleOCR (paddlepaddle) | ดี | 1–2 GB | **ห้าม** — ล้มบนเครื่องนี้แล้ว 2026-05-12 (commits `843c161 6b73ee7 1efb1bd f0c2651` → `4629fc7` switch to tesseract); Dockerfile ยังมีซาก `FLAGS_use_mkldnn`, `CPU_NUM=1`, py3.10 pin |
| EasyOCR / Surya (torch) | ดี | 1.5–3 GB, ช้ามากบน CPU | **ห้าม** บนกล่องนี้ |
| Typhoon-OCR / Google Vision / Azure DI | ดีมาก | — | เป็น external API เหมือนกัน (Google ~฿0.055/หน้า) — เป็นทางเลือก "fallback ถูกกว่า" ไม่ใช่ local |
| gpt-4.1-mini / รุ่นถูกกว่าของ OpenAI เป็น fallback | — | — | อาจถูกกว่า gpt-4o หลายเท่า — **ต้องเช็คราคา/คุณภาพจาก pricing page + `compare.py` ก่อน ไม่ฟันธงจากความจำ** |

### 7.4 Benchmark corpus — มีอยู่แล้ว ไม่ต้องติดป้ายใหม่
- `tools/gen_golden_from_confirmed.py` → `expected.json` จาก **102 ใบ confirmed / 139 หน้าในการ storage** (63 PNG จาก PDF, 76 JPEG ซึ่ง 73 มาจาก LINE) — ground truth ที่มนุษย์ยืนยันแล้ว
- `tests/ocr_golden/scorer.py` (field-level) + `compare.py` (gpt-4o / structured / Claude) → เพิ่ม runner `local` 1 ตัว
- Baseline ที่วัดแล้ว (F1, memory): gpt-4o บน 92 ใบ = bill_date 100%, invoice_no 100%, amount 83.7%, vendor 82.6% (ผิดกระจุกที่ 7-Eleven/ใบเสร็จร้านสะดวกซื้อ)
- **เกณฑ์ผ่านของ local ต้อง ≥ baseline นี้ต่อ vendor** ไม่ใช่รวม

---

## 8. Database ต้องเพิ่มอะไร (additive เท่านั้น, ไม่มี destructive)

| ตาราง/คอลัมน์ | ทำไม | rollback |
|---|---|---|
| **`ocr_runs`** (ใหม่): `id, vendor_bill_id NULL, attachment_id NULL, file_sha256, page_no, engine, mode(shadow/local/external/cache), confidence numeric, fallback_reason text, parsed jsonb, raw_text text, api_calls int, retries int, cache_hit bool, timings jsonb, created_at` | logging ที่ TUM ขอครบทุกข้อ + เก็บ Tesseract text และผล GPT **ทุกหน้า** (ปิดช่อง §1.3) + shadow comparison | `DROP TABLE` ไม่กระทบอะไร |
| **`ocr_vendor_templates`** (ใหม่ หรือเริ่มเป็นไฟล์ JSON ใน repo): `tax_id PK, canonical_vendor_name, layout rules jsonb, min_confidence` | template + แก้ปัญหา vendor สะกด 5 แบบ (key ด้วยเลขผู้เสียภาษี) | drop |
| `ai_call_log` +`cached_tokens int NULL` +`ref jsonb NULL` (invoice_id/page/attempt) | วัด cache hit จริง, นับ call ต่อบิลตรง ๆ | คอลัมน์ nullable |
| `attachments.file_sha256` backfill เฉพาะ JPEG (LINE) ที่ไฟล์ใน storage = ไฟล์ต้นฉบับ | กัน LINE ส่งซ้ำ | UPDATE คอลัมน์ที่ NULL อยู่แล้ว |

ไม่ต้องแก้ `vendor_bills` / `invoice_items` เลย

---

## 9. ความเสี่ยงต่อระบบเดิม

1. **ตัวเลขการเงินผิดจาก local OCR** — ลดด้วย: gate หลายชั้น (tax-id checksum, subtotal+vat=amount ±1, items tie ±10%, วันที่สมเหตุสมผล, SKU→ชื่อ match ≥ X%), เปิดต่อ vendor ที่ผ่าน shadow ≥ N ใบ, confirm modal ยังบังคับเหมือนเดิม
2. **CPU/RAM บน 2 vCPU/4 GB** — Stage 1 ไม่เพิ่ม; ถ้าไป RapidOCR ต้อง lazy-load + 1 worker + วัด `/health/deep` ระหว่าง Coolify build (RAM เคยพุ่ง 85%)
3. **Concurrency** — ห้ามเพิ่ม pool; ต้องเรียงคิวจาก frontend ก่อน (§7.2 ข้อ 5)
4. **Prompt-order change (cache)** เปลี่ยนพฤติกรรม GPT ได้ → ต้องรัน compare บน corpus เดิมก่อน/หลัง
5. **Thread-safety** — local extractor ต้อง side-effect-free เหมือน `_ocr_page` (pool 3)
6. **Dependency ใหม่ (onnxruntime)** — ต้องมี manylinux wheel py3.10 (AGENTS "Ask first")
7. **LINE path** ต่างจากเว็บอยู่แล้ว (§3.6) — ถ้า hybrid เปิดเฉพาะเว็บ จะเกิด 2 พฤติกรรม

## 10. Test / Rollback plan (สรุป)
- Unit: extractor เป็น pure function (text/boxes → parsed) เทสด้วย fixture สังเคราะห์ใน repo (ห้าม commit บิลจริง — กติกาเดิม)
- Golden: `compare.py --dir <corpus นอก repo>` ต่อ vendor; เกณฑ์ผ่าน ≥ baseline §7.4
- Shadow (Phase 3): flag `LOCAL_OCR_ENABLED=1, HYBRID_OCR_ENABLED=0` → ทุก upload เขียน `ocr_runs` 2 แถว (local vs external) → query เทียบ field-by-field กับค่าที่ TUM confirm จริง
- Hybrid (Phase 4): เปิด `HYBRID_OCR_ENABLED=1` **ทีละ vendor** (`ocr_vendor_templates.enabled`)
- Rollback ทุกชั้น: ตั้ง env กลับ 0 + restart (ไม่ต้อง redeploy) → path เดิม 100%; DB additive → ทิ้งได้; backup tag ก่อนทุก push ตาม 6-step

## 11. คาดว่าจะลด External API ได้เท่าไร (สมมติฐาน — ต้องพิสูจน์ใน shadow)
- Quick wins อย่างเดียว (§7.2 ข้อ 1–3): **−30–35% ของค่า call** ทันที โดยจำนวน call ลด 1/บิลหลายหน้า
- Hybrid Stage 1 (Tesseract + template 4 vendor family): vendor เหล่านี้ = ~69% ของบิล; ถ้า gate ผ่าน ~60–70% ของบิลกลุ่มนี้ (เอกสารสแกนสะอาด) → **−40–50% ของ vision calls**; รูปถ่ายมือถือ/ร้านสะดวกซื้อยังไป GPT-4o เกือบทั้งหมด
- ตัวเลขนี้เป็นการคาดการณ์จากชนิดเอกสาร ไม่ใช่ผลวัด — Phase 3 คือที่พิสูจน์

---

## Appendix — คำถามที่ต้องสัมภาษณ์ TUM ก่อน Phase 2
1. Backlog มีกี่ใบ / กี่หน้า / รูปแบบ (ถ่ายมือถือ, สแกน PDF, LINE) / vendor หลักคือใคร
2. ต้องการ line items ของบิลเก่าด้วยไหม หรือ header (ยอด/วันที่/vendor) พอสำหรับเดือนเก่า
3. งบ API ต่อเดือนที่ "รับได้" คือเท่าไร (ปัจจุบัน ≈ ฿50/เดือน)
4. จะอัป backlog ทางไหน (เว็บ / LINE / สคริปต์ server-side ใหม่)
5. ยอมรับ model OpenAI ที่ถูกกว่าเป็น fallback ไหม ถ้า benchmark ผ่าน
6. รับ RAM +500 MB บน VPS ได้ไหม หรือวางแผนอัป 8 GB ($48/เดือน)
