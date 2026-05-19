-- ════════════════════════════════════════════════════════════════════════════
-- 2026-05-19 — Canonical SKU classification (Session 25 / 26 prep)
-- ════════════════════════════════════════════════════════════════════════════
--
-- Context: invoice_items.product_name is free text — OCR variants ("เบียร์
-- ช้าง 620 มล." vs "เบียร์ ช้าง 620 มล" with trailing-period difference) and
-- handwritten-shorthand bills ("ของ 8 กล่อง", "ทอส 4 กล่อง") make monthly
-- aggregation noisy or impossible.
--
-- Fix: add a `products` master table with a small list of canonical SKUs
-- (TUM's beverage / liquor / ice / service-charge taxonomy, ~21 entries),
-- then add `canonical_sku` + `canonical_confidence` columns on
-- `invoice_items` so an AI classifier can suggest a SKU at OCR/edit time
-- and TUM confirms via a dropdown.
--
-- Schema only — no data backfill of existing items. Existing rows keep
-- `canonical_sku = NULL`; the UI shows "—" for them. A future "AI ช่วยจัด
-- หมวด" button can backfill on demand.
-- ════════════════════════════════════════════════════════════════════════════

BEGIN;

-- ─── 1. products — master list of canonical SKUs ────────────────────────────
CREATE TABLE IF NOT EXISTS public.products (
    sku           TEXT PRIMARY KEY,            -- machine-stable, e.g. 'beer_chang_classic'
    name_th       TEXT NOT NULL,               -- display name shown in dropdowns
    category      TEXT NOT NULL,               -- 'water' / 'soft_drink' / 'beer' / ...
    default_unit  TEXT,                        -- 'ขวด' / 'ลัง' / 'กล่อง' / NULL if N/A
    notes         TEXT,                        -- free-text hints used by AI prompt
    is_active     BOOLEAN NOT NULL DEFAULT TRUE,
    sort_order    INT NOT NULL DEFAULT 100,    -- override for dropdown ordering
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_products_category   ON public.products (category);
CREATE INDEX IF NOT EXISTS idx_products_is_active  ON public.products (is_active);

-- ─── 2. invoice_items — link to canonical SKU ───────────────────────────────
ALTER TABLE public.invoice_items
    ADD COLUMN IF NOT EXISTS canonical_sku        TEXT
        REFERENCES public.products(sku) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS canonical_confidence NUMERIC(3,2)
        CHECK (canonical_confidence IS NULL OR
               (canonical_confidence >= 0.0 AND canonical_confidence <= 1.0));

CREATE INDEX IF NOT EXISTS idx_invoice_items_canonical_sku
    ON public.invoice_items (canonical_sku);

-- ─── 3. Seed TUM's master list (Session 25 conversation) ────────────────────
-- 20 product SKUs + 1 fallback. `notes` field gives the AI classifier the
-- aliases / variant hints it needs to match free-text invoice rows.
INSERT INTO public.products (sku, name_th, category, default_unit, notes, sort_order)
VALUES
    -- WATER
    ('water_550ml',          'น้ำเปล่า (ขวดเล็ก 550 ml.)',          'water',      'ลัง',  'น้ำดื่ม ขวด 500-600 ml เช่น สิงห์ คริสตัล',                          10),
    ('water_1500ml',         'น้ำเปล่า (ขวดใหญ่ 1.5 ลิตร)',          'water',      'แพ็ค', 'น้ำดื่ม ขวด 1.5 ลิตร',                                                11),

    -- SOFT DRINKS
    ('pepsi_345ml',          'เป๊ปซี่ 345 มล.',                    'soft_drink', 'แพ็ค', 'เป๊ปซี่ ขวด/กระป๋อง 320-345 ml',                                    20),
    ('pepsi_1l',             'เป๊ปซี่ 1 ลิตร',                     'soft_drink', 'แพ็ค', 'เป๊ปซี่ ขวด 1 ลิตร',                                                  21),
    ('mirinda_345ml',        'มิรินด้า (แดง/ส้ม/เขียว) 345 มล.',   'soft_drink', 'แพ็ค', 'มิรินด้า ทุกรส (แดง สตรอเบอร์รี ส้ม เขียว) ขวด 320-345 ml',           22),
    ('soda',                 'โซดา',                              'soft_drink', 'ลัง',  'โซดา ทุกยี่ห้อ เช่น สิงห์ ช้าง',                                       23),

    -- LIQUOR
    ('soju',                 'โซจู (ทุกรสชาติ)',                   'liquor',     'ขวด',  'โซจู ทุกรส เช่น จินโร โซจู',                                          30),
    ('regency',              'รีเจนซี่',                          'liquor',     'ขวด',  'บรั่นดี Regency',                                                     31),
    ('grand',                'แกรนด์',                            'liquor',     'ขวด',  'แกรนด์ รอยัล',                                                         32),
    ('sangsom',              'แสงโสม',                            'liquor',     'ขวด',  'รัมแสงโสม',                                                            33),
    ('hongthong',            'หงส์ทอง',                            'liquor',     'ขวด',  'หงส์ทอง',                                                              34),

    -- BEER
    ('beer_chang_classic',   'เบียร์ช้างคลาสสิก',                  'beer',       'ลัง',  'เบียร์ช้าง คลาสสิก ขวด/กระป๋อง',                                       40),
    ('beer_chang_coldbrew',  'เบียร์ช้างโคลด์บรูว์',                'beer',       'ลัง',  'เบียร์ช้าง Cold Brew (โคลด์บรูว์) ขวด',                                  41),
    ('beer_leo',             'เบียร์ลีโอ',                        'beer',       'ลัง',  'เบียร์ลีโอ ขวด/กระป๋อง',                                              42),
    ('beer_singha',          'เบียร์สิงห์',                        'beer',       'ลัง',  'เบียร์สิงห์ ขวด/กระป๋อง รวมทุกขนาด',                                   43),
    ('beer_federbrau',       'เบียร์ Federbrau',                  'beer',       'ลัง',  'เบียร์ Federbrau (เฟเดอร์เบราว์)',                                    44),
    ('beer_asahi',           'เบียร์อาซาฮี',                       'beer',       'ลัง',  'เบียร์ Asahi (อาซาฮี) Super Dry รวม',                                  45),

    -- ICE
    ('ice_small',            'น้ำแข็งถังเล็ก',                     'ice',        'ถัง',  'น้ำแข็ง ถังเล็ก ก้อน',                                                  50),
    ('ice_large',            'น้ำแข็งถังใหญ่',                     'ice',        'ถัง',  'น้ำแข็ง ถังใหญ่ ก้อน',                                                  51),

    -- SERVICE
    ('corkage_fee',          'ค่าเปิดเหล้า (บาท/ขวด)',              'service',    NULL,   'ค่าเปิดเหล้า / corkage / service charge ต่อขวด',                       60),

    -- FALLBACK
    ('other',                'อื่นๆ (ไม่ระบุ)',                    'other',      NULL,   'ทุกอย่างที่ไม่ตรงกับ SKU อื่น — TUM จะ review ทีหลัง',                  999)
ON CONFLICT (sku) DO NOTHING;

-- ─── 4. Preview ──────────────────────────────────────────────────────────────
SELECT category,
       COUNT(*)::int AS sku_count,
       string_agg(name_th, ', ' ORDER BY sort_order)
         FILTER (WHERE LENGTH(name_th) < 30) AS sample_names
FROM public.products
GROUP BY category
ORDER BY MIN(sort_order);

-- Confirm columns landed on invoice_items
SELECT column_name, data_type, is_nullable
FROM information_schema.columns
WHERE table_schema = 'public'
  AND table_name   = 'invoice_items'
  AND column_name IN ('canonical_sku', 'canonical_confidence');

COMMIT;
-- ROLLBACK;  -- uncomment + re-run if the preview looks wrong
