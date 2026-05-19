-- ════════════════════════════════════════════════════════════════════════════
-- 2026-05-20 — vendor_aliases.product_keyword UNIQUE constraint + cleanup
-- ════════════════════════════════════════════════════════════════════════════
--
-- Context: Session 25 surfaced two issues with `vendor_aliases`:
--   1. No UNIQUE constraint on `product_keyword` — TUM's bulk seed
--      (Makro/แม็คโคร) failed with 42P10 because ON CONFLICT had
--      nothing to conflict against. Worked around with NOT EXISTS,
--      but the underlying schema gap remained.
--   2. The earlier alias dump showed two identical "เบียร์ช้าง →
--      วัฒนา" rows, confirming duplicates can land silently.
--
-- This migration:
--   - Dedups any existing duplicate (product_keyword, vendor_name)
--     pairs by keeping only the lowest-id row of each group.
--   - Adds the missing UNIQUE constraint on product_keyword so future
--     INSERTs can use ON CONFLICT cleanly and TUM-managed rule
--     editing has predictable semantics.
--
-- Safe to re-run: dedup is idempotent (no-op if zero duplicates),
-- and the UNIQUE constraint creation is wrapped in DO block so it
-- doesn't error if it already exists.
-- ════════════════════════════════════════════════════════════════════════════

BEGIN;

-- ─── 1. Dedup any (product_keyword, vendor_name) duplicates ─────────────────
-- Keep the lowest-id row of each duplicate group, delete the rest.
WITH ranked AS (
    SELECT id,
           ROW_NUMBER() OVER (
               PARTITION BY LOWER(TRIM(product_keyword))
               ORDER BY id
           ) AS rn
    FROM public.vendor_aliases
)
DELETE FROM public.vendor_aliases va
USING ranked r
WHERE va.id = r.id
  AND r.rn > 1;

-- ─── 2. Normalise product_keyword (lowercase trim) for stability ────────────
-- Prevents future case-only / whitespace-only "duplicates".
UPDATE public.vendor_aliases
SET product_keyword = LOWER(TRIM(product_keyword))
WHERE product_keyword <> LOWER(TRIM(product_keyword));

-- ─── 3. Add UNIQUE constraint (idempotent via DO block) ─────────────────────
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'vendor_aliases_product_keyword_unique'
    ) THEN
        ALTER TABLE public.vendor_aliases
            ADD CONSTRAINT vendor_aliases_product_keyword_unique
            UNIQUE (product_keyword);
    END IF;
END $$;

-- ─── 4. Preview ─────────────────────────────────────────────────────────────
SELECT product_keyword,
       vendor_name,
       is_active
FROM public.vendor_aliases
ORDER BY product_keyword
LIMIT 30;

SELECT 'Total aliases' AS metric, COUNT(*)::text AS value
FROM public.vendor_aliases
UNION ALL
SELECT 'Unique keywords' AS metric, COUNT(DISTINCT product_keyword)::text AS value
FROM public.vendor_aliases;

COMMIT;
-- ROLLBACK;  -- uncomment if preview shows ambiguity / collateral damage
