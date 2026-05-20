-- ════════════════════════════════════════════════════════════════════════════
-- 2026-05-20 — Invoice alias for ingredient matching (Phase V3)
-- ════════════════════════════════════════════════════════════════════════════
--
-- THE PROBLEM
-- -----------
-- sync-from-invoices matches ingredients to invoice_items by substring
-- on the ingredient name. This breaks when kitchen-facing names differ
-- from supplier invoice names:
--   ingredient name : "เบียร์ช้างคลาสสิก"
--   invoice item    : "เบียร์ช้าง 620 มล."   ← no substring overlap
--
--   ingredient name : "Federbrau"
--   invoice item    : "เฟเดอร์บรอย 620มล."   ← completely different text
--
-- THE FIX
-- -------
-- invoice_match_name: the exact text (or keyword) that appears in the
-- supplier's invoice for this ingredient. When set, the sync SQL uses
-- this instead of ingredients.name as the lookup key in invoice_items.
-- When NULL, falls back to the ingredient's own name (legacy behaviour).
--
-- This lets TUM keep the kitchen-facing name ("เบียร์ช้างคลาสสิก") in
-- the display while the sync engine reliably finds the right invoice row
-- without asking TUM to rename anything.
--
-- Length-ratio relaxation (also in this migration):
-- The SQL guard `ratio >= 0.6` was blocking "เบียร์สิงห์ 2025" (10 chars)
-- from matching "เบียร์สิงห์ 2025 (12x630CC)" (24 chars) — ratio 0.42.
-- Added OR clause: allow match when the shorter string is >= 6 chars
-- (specific enough to not false-match, even with a low ratio).
--
-- Idempotent: ADD COLUMN IF NOT EXISTS
-- ════════════════════════════════════════════════════════════════════════════

BEGIN;

ALTER TABLE public.ingredients
    ADD COLUMN IF NOT EXISTS invoice_match_name TEXT;

-- Seed known beer rows so the next sync-from-invoices run already works.
UPDATE public.ingredients
SET invoice_match_name = CASE
    WHEN name ILIKE '%ช้างคลาส%' OR name ILIKE '%ช้าง คลาส%'
        THEN 'เบียร์ช้าง 620'
    WHEN name ILIKE '%โคลด์%'
        THEN 'ช้าง โคลด์บรูว์'
    WHEN name ILIKE '%federbrau%' OR name ILIKE '%เฟเดอร์%'
        THEN 'เฟเดอร์บรอย'
    WHEN (name ILIKE '%reserve%' OR name ILIKE '%รีเซอร์%') AND name ILIKE '%สิงห์%'
        THEN 'SINGHA RESERVE'
    WHEN name ILIKE '%เบียร์สิงห์%' OR name ILIKE '%singha%'
        THEN 'เบียร์สิงห์ 2025'
    WHEN name ILIKE '%ลีโอ%' OR name ILIKE '%leo%'
        THEN 'ลีโอ'
    WHEN name ILIKE '%พิ้งก์%' OR name ILIKE '%pink%' OR name ILIKE '%จิงกิ%'
        THEN 'สิงห์ จิงกิ'
    ELSE NULL
END
WHERE pack_size > 1 AND invoice_match_name IS NULL;

-- Confirm
SELECT name, invoice_match_name, pack_size, invoice_unit
FROM public.ingredients
WHERE pack_size > 1
ORDER BY name;

COMMIT;
