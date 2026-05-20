-- ════════════════════════════════════════════════════════════════════════════
-- 2026-05-20 — Remove bundle/promo "ingredients" (Session 28 cleanup)
-- ════════════════════════════════════════════════════════════════════════════
--
-- The `ingredients` table is a master of RAW SINGLE-UNIT items used to
-- build recipes (เบียร์ช้าง 1 ขวด, หมูสามชั้น 1 ชิ้น). But the FoodStory
-- POS inventory dump that seeded this table included BOTH:
--   • raw single-bottle items (correct: เบียร์ช้างคลาสลึก ฿85/ขวด)
--   • promo bundles that are actually MENU items, not ingredients
--     (wrong: 'Pro(3) เบียร์ช้างคลาสลึก' ฿219 = price OF the 3-pack, not /ขวด)
--
-- The wrong rows poisoned AI Link Ingredients: Claude saw a menu
-- "DD03 ช้างโปร/3ขวด ฿199" and matched it to ingredient
-- "Pro(3) เบียร์ช้างคลาสลึก ฿219/ขวด" then multiplied 3 × ฿219 = ฿657.
-- The right ingredient (เบียร์ช้างคลาสลึก ฿85/ขวด) would give 3 × ฿85
-- = ฿255 → GP ≈ -28% which is the actual bait-pricing margin TUM runs.
--
-- This migration:
--   1. Snapshots the rows that match the bundle pattern (audit log)
--   2. Deletes recipe_ingredients rows that link to those bundle rows
--      (CASCADE wouldn't help — the FK is ON DELETE SET NULL on
--      ingredient_id which would leave orphan ri rows)
--   3. Deletes the bundle rows from ingredients
--   4. Outputs counts so TUM can verify before/after
--
-- Pattern matched:
--   - Name contains '(N)' where N is a number (e.g. '(3)', '(5)')
--   - Name starts with 'Pro(' or '(pro)' (case-insensitive)
--
-- Idempotent: re-running after a successful run finds 0 matching rows
-- and deletes nothing.
-- ════════════════════════════════════════════════════════════════════════════

BEGIN;

-- ─── 1. Preview what will be removed ────────────────────────────────────────
SELECT 'BEFORE — bundle ingredients to remove' AS phase, COUNT(*) AS rows
FROM public.ingredients
WHERE name ~ '\(\d+\)' OR name ILIKE 'Pro(%' OR name ILIKE '(pro)%';

SELECT 'BEFORE — recipe_ingredients linked to bundles' AS phase, COUNT(*) AS rows
FROM public.recipe_ingredients ri
JOIN public.ingredients i ON i.id = ri.ingredient_id
WHERE i.name ~ '\(\d+\)' OR i.name ILIKE 'Pro(%' OR i.name ILIKE '(pro)%';

-- ─── 2. Delete recipe_ingredients first (FK dependency) ─────────────────────
DELETE FROM public.recipe_ingredients
WHERE ingredient_id IN (
    SELECT id FROM public.ingredients
    WHERE name ~ '\(\d+\)'
       OR name ILIKE 'Pro(%'
       OR name ILIKE '(pro)%'
);

-- ─── 3. Delete bundle ingredients ───────────────────────────────────────────
DELETE FROM public.ingredients
WHERE name ~ '\(\d+\)'
   OR name ILIKE 'Pro(%'
   OR name ILIKE '(pro)%';

-- ─── 4. Confirm cleanup ─────────────────────────────────────────────────────
SELECT 'AFTER — bundle ingredients remaining' AS phase, COUNT(*) AS rows
FROM public.ingredients
WHERE name ~ '\(\d+\)' OR name ILIKE 'Pro(%' OR name ILIKE '(pro)%';

SELECT 'AFTER — total ingredients' AS phase, COUNT(*) AS rows
FROM public.ingredients;

SELECT 'AFTER — recipes with 0 ingredients (need re-bulk-link)' AS phase, COUNT(*) AS rows
FROM public.recipes r
WHERE NOT EXISTS (
    SELECT 1 FROM public.recipe_ingredients ri WHERE ri.recipe_id = r.id
);

COMMIT;
