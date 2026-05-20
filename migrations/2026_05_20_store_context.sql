-- ════════════════════════════════════════════════════════════════════════════
-- 2026-05-20 — store_context table (Session 28 item U1)
-- ════════════════════════════════════════════════════════════════════════════
--
-- AI restaurant-knowledge layer. One row per logical context "section"
-- (brand profile, menu knowledge, customer behavior, etc.). Every AI
-- feature that benefits from store-level context pulls the active rows
-- and embeds them in its prompt:
--
--   - /recipes/{id}/ai-link-ingredients  (Claude Haiku ingredient suggestion)
--   - slip_routes._classify_slip_category (memo classifier — context-aware)
--   - future LINE bot replies, AI menu suggestion, RAG search, etc.
--
-- Design choices:
--   - `key` is a stable string identifier (e.g. 'menu_knowledge'). Used as
--     PK so application code can read specific sections by name without
--     guessing UUIDs.
--   - `content_type` declares whether the row is markdown (free-form
--     human-curated knowledge) or json (structured machine-readable data
--     like the menu mapping table). The admin UI picks the right editor
--     based on this.
--   - `is_active` lets TUM disable a section without deleting it (e.g.
--     swap an old brand profile for a new one without losing history).
--   - `priority` controls concatenation order when building the prompt
--     header. Lower number → earlier in prompt. Brand/atmosphere usually
--     comes first (sets context), structured data last (concrete refs).
--   - `updated_by` records the JWT-derived username on every write so we
--     have an audit trail for who edited which section.
--
-- Seed inserts are wrapped in $tag$ ... $tag$ Postgres dollar-quote
-- syntax so we don't have to escape every single quote in the markdown.
-- ════════════════════════════════════════════════════════════════════════════

BEGIN;

CREATE TABLE IF NOT EXISTS public.store_context (
    key          TEXT PRIMARY KEY,
    content      TEXT NOT NULL,
    content_type TEXT NOT NULL DEFAULT 'markdown'
                    CHECK (content_type IN ('markdown', 'json', 'text')),
    is_active    BOOLEAN NOT NULL DEFAULT true,
    priority     INT NOT NULL DEFAULT 50,
    notes        TEXT,
    updated_by   TEXT,
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Touch trigger so updated_at is fresh on every UPDATE.
CREATE OR REPLACE FUNCTION public.fn_store_context_touch()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_store_context_touch ON public.store_context;
CREATE TRIGGER trg_store_context_touch
    BEFORE UPDATE ON public.store_context
    FOR EACH ROW
    EXECUTE FUNCTION public.fn_store_context_touch();

-- ─── 2. Seed: brand_profile (placeholder — TUM curates later) ──────────────
INSERT INTO public.store_context (key, content, content_type, priority, notes)
VALUES (
    'brand_profile',
    $brand$# Mara Station — Brand Profile

## Identity
Mara Station ร้านดนตรีสด + ปิ้งย่างหม่าล่า บรรยากาศชิลล์
สำหรับลูกค้าวัยทำงาน-วัยรุ่น มาทานเล่นคู่กับเครื่องดื่ม

## Positioning
- ไม่ใช่ร้านอาหารพรีเมียม
- "Affordable chill restaurant + social drinking + late-night food"
- เน้นบรรยากาศ + ดนตรีสด + การสังสรรค์เป็นกลุ่ม

## Target Customer
- กลุ่มเพื่อน 3-6 คน
- วัยทำงาน 25-40 ปี
- ลูกค้าประจำที่มาฟังดนตรี + ดื่ม + ทานเล่น
- ใช้เวลานั่งนาน (2-4 ชั่วโมง/ครั้ง)

## Revenue Drivers (เรียงตามความสำคัญ)
1. แอลกอฮอล์ (เบียร์/เหล้า) — drive ยอดบิลใหญ่
2. ปิ้งย่างหม่าล่า — drive table sharing + ดื่มเยอะขึ้น
3. โปรโมชั่นชุด (10 ไม้ฟรี 1, 3 ขวด/5 ขวด) — drive ออร์เดอร์ใหญ่
$brand$,
    'markdown',
    10,
    'High-level brand positioning. Update via /admin/store-context.'
) ON CONFLICT (key) DO NOTHING;

-- ─── 3. Seed: menu_knowledge (from TUM's product_knowledge_context.md) ─────
INSERT INTO public.store_context (key, content, content_type, priority, notes)
VALUES (
    'menu_knowledge',
    $menu$# Mara Station — Product Knowledge Context

## Business Product Overview
ร้านดนตรีสด + ปิ้งย่างหม่าล่า + สังสรรค์กลุ่ม
Positioning: "Affordable chill restaurant + social drinking + late-night food"

## Core Product Categories

### 1. Mala / Grilled Skewer Products (รายได้หลัก)
ตัวอย่าง:
- Pork belly skewers (หมูสามชั้น)
- Pork neck skewers (สันคอหมู / คอหมูย่าง)
- Meat skewers (เนื้อ)
- Seafood skewers (ปลาหมึก / กุ้ง)
- Vegetable skewers (ผัก)
- Mixed grill promotions (ชุด/สุ่ม)

Customer behavior:
- สั่งทีละไม้หรือเป็นชุดใหญ่
- แชร์กันในกลุ่ม
- คู่กับเครื่องดื่ม
- สั่งซ้ำหลายรอบ

Promotion patterns:
- ซื้อ 10 ไม้ ฟรี 1
- กล่องสุ่ม mix (12 ไม้)
- ชุดย่างรวม

### 2. Alcohol Products (สำคัญที่สุดในแง่ revenue)
- เบียร์ขวด/ลัง: Singha, Leo, Chang, Cold Brew, Asahi, Federbrau
- โซจู (Soju) — ทุกรสชาติ
- สุรา: Regency, Grand, แสงโสม, หงส์ทอง
- น้ำแข็ง / mixers / โซดา

Promotion patterns:
- 3 ขวด / 5 ขวด / ลัง
- combo alcohol pricing
- โปรพิเศษช่วงดึก

### 3. Shared / Group Dining (เน้นทานคนเยอะ)
- หมูจุ่ม
- ต้มซุปเปอร์
- ของทอด
- อาหารทะเล
- ชุดย่างรวม

### 4. Add-ons / Upsell
- น้ำจิ้มซีฟู้ด
- ผักกาดดอง
- ถังน้ำแข็ง
- น้ำเปล่า
- ผลไม้ดิป

### 5. Promotion Menus
- หมูสามชั้น 10 ไม้ ฟรี 1 ไม้
- กล่องสุ่ม 12 ไม้
- ชุด combo คละสินค้า

## Customer Behavior Summary
- มาเป็นกลุ่ม 3-6 คน
- สั่งเครื่องดื่มก่อน → แล้วสั่งของทานคู่
- โปรโมชั่น-sensitive
- ดื่มหลายรอบ
- นั่งนาน 2-4 ชม.

## Important Restaurant Context
- บรรยากาศ outdoor / open-air
- มีดนตรีสด ทำให้ลูกค้านั่งนาน
- กลุ่มลูกค้า 25-40 ปี
- ราคาเข้าถึงง่าย (฿10-300 ต่อเมนู)

## AI Operational Understanding

### Revenue Hierarchy
1. แอลกอฮอล์
2. ปิ้งย่างไม้ + ชุด
3. โปรโมชั่นชุดใหญ่
4. shared dishes
5. add-ons

### Pricing Tiers (สำหรับ AI ตีความ menu)
- ฿10-50 = ไม้เดี่ยว / แก้วเดี่ยว
- ฿100-300 = ชุดเล็ก / ขวดเดี่ยว / โปร 3 ขวด
- ฿300-600 = โปรลัง / combo ใหญ่
- ฿600+ = ชุดพิเศษ / โปรหลายลัง

### Do's
- เมนูไม้เดี่ยว → ใช้วัตถุดิบหลัก 1 ตัว
- เมนูชุด/โปร → กระจายตามจำนวนไม้/ขวด
- เครื่องดื่ม → ใช้ ingredient ตามชื่อยี่ห้อตรงๆ

### Don'ts
- ห้ามใส่ข้าว/ผัก/น้ำเปล่า ในเมนูไม้เดี่ยว (ไม่ใช่ร้านข้าวกล่อง)
- ห้ามเดาน้ำจิ้ม/ซอส (เป็น overhead ไม่นับต่อจาน)
- ห้ามใส่ ingredient ที่ไม่มีใน master ingredients
$menu$,
    'markdown',
    20,
    'Master menu/product knowledge — drives AI Link prompts.'
) ON CONFLICT (key) DO NOTHING;

-- ─── 4. Seed: customer_behavior (placeholder) ──────────────────────────────
INSERT INTO public.store_context (key, content, content_type, priority, notes)
VALUES (
    'customer_behavior',
    $cb$# Customer Behavior Patterns

## Typical Group Order
- 3-6 คน / โต๊ะ
- รอบแรก: เครื่องดื่ม 3-6 ขวด + ปิ้งย่าง 10-20 ไม้
- รอบที่ 2-3: เพิ่มเครื่องดื่ม + ของทอด/หมูจุ่ม
- บิลเฉลี่ย: ฿800-2,500 ต่อโต๊ะ

## Time Pattern
- 18:00-20:00 — ลูกค้า after work (mode เครื่องดื่ม + ของกินเล่น)
- 20:00-22:00 — peak (ดนตรีสด)
- 22:00-24:00 — ทาน + ดื่มต่อ
- หลัง 24:00 — บางวันมีลูกค้าสาย

## Promotion Sensitivity
- เห็น "10 ไม้ ฟรี 1" → กดสั่งเลย
- เห็น "3 ขวด ฿XXX" → คำนวณว่าคุ้มกว่าสั่งเดี่ยว
- ลูกค้าประจำ — จำราคาโปรได้

## Order Frequency
- สั่งหลายรอบ — ไม่ใช่ครั้งเดียวจบ
- bartender / staff ต้องเชียร์ของเสริมระหว่างรอบ
$cb$,
    'markdown',
    30,
    'Customer behavior — used by recommendation/marketing AI.'
) ON CONFLICT (key) DO NOTHING;

-- ─── 5. Seed: atmosphere (placeholder) ─────────────────────────────────────
INSERT INTO public.store_context (key, content, content_type, priority, notes)
VALUES (
    'atmosphere',
    $atm$# Store Atmosphere

## Vibe
- ดนตรีสด ทุกเย็น
- บรรยากาศ outdoor / semi-outdoor
- กลุ่มเพื่อน-สาย chill
- ไม่ใช่ fine dining — relaxed ปาร์ตี้เล็ก

## Music
- มีวงดนตรีสด เล่นเพลงไทยและสากล
- เน้น mood ฟังสบาย + ปาร์ตี้เบาๆ
- ลูกค้าประจำมาเพื่อฟังศิลปินเฉพาะวัน

## Layout
- โต๊ะกลุ่มเป็นหลัก (4-6 ที่นั่ง/โต๊ะ)
- มีบาร์เล็กๆ
- เวที musicians ด้านหน้า/กลางร้าน
$atm$,
    'markdown',
    40,
    'Atmosphere/branding — used by marketing/copywriting AI.'
) ON CONFLICT (key) DO NOTHING;

-- ─── 6. Seed: menu_structured (empty JSON — populated by Phase U2 OCR) ────
INSERT INTO public.store_context (key, content, content_type, priority, notes)
VALUES (
    'menu_structured',
    $json${"menus": [], "note": "Populated by tools/ocr_menu_to_json.py against /store-context/Menu.jpg in Phase U2. Until then this is an empty stub."}$json$,
    'json',
    50,
    'Menu list with structured ingredient_keywords — generated by OCR Menu.jpg.'
) ON CONFLICT (key) DO NOTHING;

-- ─── 7. Preview ─────────────────────────────────────────────────────────────
SELECT key,
       content_type,
       length(content) AS bytes,
       is_active,
       priority
FROM public.store_context
ORDER BY priority, key;

COMMIT;
