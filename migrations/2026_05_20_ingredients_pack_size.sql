-- ════════════════════════════════════════════════════════════════════════════
-- 2026-05-20 — Pack-size aware ingredient cost (Phase V — Session 28)
-- ════════════════════════════════════════════════════════════════════════════
--
-- THE PROBLEM
-- -----------
-- The ingredients table mixed two pricing semantics:
--   (a) the SALES price of one unit (เบียร์ช้างคลาสลึก ฿85/ขวด — what we
--       charge customers for one bottle)
--   (b) the COST price of one unit (the ฿/ขวด we actually pay the
--       distributor — Boon Rawd lists ช้าง 12-ขวด ลัง = ฿683, so true
--       cost is ฿683 ÷ 12 = ฿56.92/ขวด)
--
-- The POS dump seeded the table with (a). GP% calculated against (a)
-- is always ≈ 0% (because cost ≈ price). For a bar that buys by the ลัง
-- and sells by the ขวด, the meaningful GP% needs (b).
--
-- We already have invoice OCR working — most invoices list a pack price
-- (฿/ลัง) and a pack count. To translate that into ฿/ขวด automatically
-- we need to know, for each ingredient, how many ขวด are in one ลัง.
-- That's the pack_size.
--
-- THE SCHEMA CHANGE
-- -----------------
-- pack_size:
--   How many ingredient.unit there are in one invoice_unit. For beer:
--   ingredient.unit='ขวด', invoice_unit='ลัง', pack_size=12.
--   Defaults to 1 (no conversion = legacy behaviour preserved).
--
-- invoice_unit:
--   The unit the supplier invoices in. NULL means "same as ingredient.unit"
--   (no conversion). When the sync engine sees invoice_unit='ลัง' on the
--   ingredient and the invoice line says 'ลัง', it knows to divide by
--   pack_size before storing as price_per_unit.
--
-- unit_cost_source:
--   Audit trail for where the current price_per_unit came from:
--     'manual'         — TUM entered it by hand (default for legacy rows)
--     'invoice'        — sync-from-invoices wrote it (with pack_size math)
--     'sales_estimate' — POS-derived sales price (legacy POS seed)
--   Lets the UI badge each row so TUM knows which numbers are trustworthy.
--
-- Idempotent: ADD COLUMN IF NOT EXISTS — safe to re-run.
-- ════════════════════════════════════════════════════════════════════════════

BEGIN;

ALTER TABLE public.ingredients
    ADD COLUMN IF NOT EXISTS pack_size        INT     NOT NULL DEFAULT 1,
    ADD COLUMN IF NOT EXISTS invoice_unit     TEXT,
    ADD COLUMN IF NOT EXISTS unit_cost_source TEXT    NOT NULL DEFAULT 'manual';

-- Enforce the source vocabulary so a typo doesn't silently disable
-- the badge logic in the UI.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'ingredients_unit_cost_source_check'
    ) THEN
        ALTER TABLE public.ingredients
            ADD CONSTRAINT ingredients_unit_cost_source_check
            CHECK (unit_cost_source IN ('manual', 'invoice', 'sales_estimate'));
    END IF;
END $$;

-- Sanity check: pack_size must be ≥ 1 (a 0-bottle case would divide by
-- zero in the sync engine).
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'ingredients_pack_size_positive'
    ) THEN
        ALTER TABLE public.ingredients
            ADD CONSTRAINT ingredients_pack_size_positive
            CHECK (pack_size >= 1);
    END IF;
END $$;

-- ─── Bootstrap pack_size for known beer-by-the-case items ──────────────────
-- These are the rows where TUM has already confirmed the wholesale
-- pack convention (12 ขวด/ลัง). The matching is intentionally narrow:
-- only ขวด-unit beer/spirit names. The sync engine treats any row with
-- pack_size=1 as "no conversion" so missing matches are safe — they
-- just default to the current legacy behaviour until TUM sets pack_size
-- manually on /ingredients.
UPDATE public.ingredients
SET pack_size    = 12,
    invoice_unit = 'ลัง'
WHERE unit = 'ขวด'
  AND pack_size = 1                  -- don't clobber if already set
  AND (
        name ILIKE '%เบียร์ช้าง%'
     OR name ILIKE '%ช้าง คลาส%'
     OR name ILIKE '%ช้างคลาส%'
     OR name ILIKE '%โคลด์บรูว์%'
     OR name ILIKE '%เฟเดอร์%'
     OR name ILIKE '%feder%'
     OR name ILIKE '%leo%'
     OR name ILIKE '%ลีโอ%'
     OR name ILIKE '%singha%'
     OR name ILIKE '%สิงห์%'
     OR name ILIKE '%heineken%'
     OR name ILIKE '%ไฮเนเก้น%'
  );

-- ─── Preview the bootstrap result ──────────────────────────────────────────
SELECT 'AFTER — beer-style rows with pack_size set' AS phase, COUNT(*) AS rows
FROM public.ingredients
WHERE pack_size > 1;

SELECT name, unit, invoice_unit, pack_size, price_per_unit, unit_cost_source
FROM public.ingredients
WHERE pack_size > 1
ORDER BY name;

COMMIT;
