-- 2026-06-27 — SPEC 02 manual public-menu badge on recipes
--
-- Additive only. Do not apply to production until TUM approves the release.
-- The application stores enum values only; Thai labels/emoji are mapped in UI.

BEGIN;

ALTER TABLE public.recipes
  ADD COLUMN IF NOT EXISTS badge text NULL;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1
    FROM pg_constraint
    WHERE conname = 'recipes_badge_check'
      AND conrelid = 'public.recipes'::regclass
  ) THEN
    ALTER TABLE public.recipes
      ADD CONSTRAINT recipes_badge_check
      CHECK (badge IN ('best_seller', 'recommended') OR badge IS NULL);
  END IF;
END $$;

COMMIT;
