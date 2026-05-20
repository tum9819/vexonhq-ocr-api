-- ════════════════════════════════════════════════════════════════════════════
-- 2026-05-20 — slips.category_source += 'manual'
-- ════════════════════════════════════════════════════════════════════════════
--
-- Context: Phase 6.5 introduced the L1→L3 category cascade. TUM needs a
-- way to override the resolver's guess when:
--
--   - the rule that fired is wrong (and editing /rules would have side
--     effects on other slips)
--   - the slip is genuinely a one-off (e.g. tip / gift / personal use)
--     that doesn't fit any rule
--
-- Adding 'manual' as a valid category_source acts as the "lock" signal —
-- the auto-resolver `_resolve_and_persist_category()` checks this and
-- leaves the row alone, so TUM's override survives subsequent calls to
-- POST /slip/{id}/match and POST /slips/rematch-all.
-- ════════════════════════════════════════════════════════════════════════════

BEGIN;

-- Drop the existing CHECK so we can broaden it. The constraint was added
-- inline by the original slips_schema migration with an auto-generated
-- name — look it up first.
DO $$
DECLARE
    constraint_name TEXT;
BEGIN
    SELECT conname INTO constraint_name
    FROM pg_constraint
    WHERE conrelid = 'public.slips'::regclass
      AND contype  = 'c'
      AND pg_get_constraintdef(oid) LIKE '%category_source%';

    IF constraint_name IS NOT NULL THEN
        EXECUTE format('ALTER TABLE public.slips DROP CONSTRAINT %I', constraint_name);
    END IF;
END $$;

-- Re-add with 'manual' included.
ALTER TABLE public.slips
    ADD CONSTRAINT slips_category_source_check
    CHECK (category_source IS NULL OR category_source IN
           ('statement', 'memo_keyword', 'recipient_name', 'manual'));

-- Preview
SELECT 'valid category_source values' AS metric, 'statement / memo_keyword / recipient_name / manual' AS value;

COMMIT;
-- ROLLBACK;
