-- ════════════════════════════════════════════════════════════════════════════
-- 2026-05-20 — TOMORROW.md item Q: food SKU expansion
-- ════════════════════════════════════════════════════════════════════════════
--
-- Context: existing `products` master only covers beverages / water / ice /
-- service (~21 SKUs from canonical_sku migration). Food line items on
-- TUM's invoices (เนื้อ ย่างเนื้อ, เนื้อ โปร, ไก่, หมู, ผัก, เครื่องปรุง)
-- all classify to `sku='other'` because there's nowhere else for them
-- to land. Adding 8 broad food categories solves this without exploding
-- the catalogue — the classifier handles the within-category nuance.
--
-- After this migration runs, TUM should call:
--   POST /invoice/items/auto-classify-bulk?force_other=true&limit_bills=200
-- to re-classify items that previously landed on 'other'. The endpoint
-- now (Session 27) accepts force_other=true to bypass the "skip rows
-- with canonical_sku set" guard for that specific value.
-- ════════════════════════════════════════════════════════════════════════════

BEGIN;

-- ─── 1. Add 8 food SKUs ─────────────────────────────────────────────────────
INSERT INTO public.products (sku, name_th, category, default_unit, notes, sort_order)
VALUES
    -- เนื้อสัตว์
    ('beef',         'เนื้อวัว (รวมทุกชิ้น)',  'food', 'กก.', 'เนื้อวัวทุกชนิด: สไลซ์ บริสเก็ต อก ริบอาย — รวม supplier ย่างเนื้อ / เนื้อ โปร', 70),
    ('pork',         'หมู (รวมทุกชิ้น)',       'food', 'กก.', 'หมูทุกชนิด: สามชั้น สไลซ์ หมูบด คอหมูย่าง',                                          71),
    ('chicken',      'ไก่ (รวมทุกชิ้น)',       'food', 'กก.', 'ไก่ทุกชนิด: ปีก น่อง อก สะโพก ไก่ทั้งตัว',                                            72),
    ('seafood',      'อาหารทะเล',              'food', 'กก.', 'กุ้ง ปลา ปลาหมึก หอย ปู ทะเลรวม',                                                      73),

    -- ของประกอบ
    ('vegetables',   'ผักสด',                  'food', 'กก.', 'ผักสรรพสิ่ง ผักบุ้ง คะน้า มะนาว ผลไม้',                                                80),
    ('seasonings',   'เครื่องปรุง',            'food', 'ชุด', 'ซอส พริก เกลือ น้ำมัน ขอแห้ง ผงปรุงรส',                                                81),
    ('rice_noodles', 'ข้าว / เส้น',             'food', 'ถุง', 'ข้าวสาร บะหมี่ เส้นเล็ก เส้นใหญ่ เส้นหมี่',                                            82),

    -- Fallback
    ('food_other',   'อาหารอื่นๆ',             'food', NULL,  'อาหารที่ไม่ตรงหมวด — TUM review ทีหลัง (ลดจำนวนรายการ "other" สากล)',                  89)
ON CONFLICT (sku) DO UPDATE
SET name_th      = EXCLUDED.name_th,
    category     = EXCLUDED.category,
    default_unit = EXCLUDED.default_unit,
    notes        = EXCLUDED.notes,
    sort_order   = EXCLUDED.sort_order;

-- ─── 2. Preview ─────────────────────────────────────────────────────────────
-- How many invoice_items are currently sku='other' (the ones force_other
-- will re-classify on the next bulk run)?
SELECT 'items waiting for reclassify (canonical_sku=other)' AS metric,
       COUNT(*)::text AS value
FROM public.invoice_items
WHERE canonical_sku = 'other'
UNION ALL
SELECT 'items still NULL (caught by default bulk endpoint)' AS metric,
       COUNT(*)::text AS value
FROM public.invoice_items
WHERE canonical_sku IS NULL
UNION ALL
SELECT 'total products (after seed)' AS metric, COUNT(*)::text AS value
FROM public.products
WHERE is_active = true
UNION ALL
SELECT 'food category products' AS metric, COUNT(*)::text AS value
FROM public.products
WHERE category = 'food';

COMMIT;
-- ROLLBACK;
