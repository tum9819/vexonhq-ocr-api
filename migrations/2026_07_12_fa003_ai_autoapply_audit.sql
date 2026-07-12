-- 2026-07-12 FA-003/C2: AI categorize auto-apply audit trail.
-- Additive only. Existing logs were already applied by the legacy worker, so the
-- default/backfill keeps them as applied=true and makes them visible in Auto.

ALTER TABLE public.ai_categorization_log
  ADD COLUMN IF NOT EXISTS applied boolean NOT NULL DEFAULT true,
  ADD COLUMN IF NOT EXISTS before_category text,
  ADD COLUMN IF NOT EXISTS applied_by text NOT NULL DEFAULT 'ai',
  ADD COLUMN IF NOT EXISTS undone_at timestamp with time zone,
  ADD COLUMN IF NOT EXISTS undone_by text,
  ADD COLUMN IF NOT EXISTS undo_reason text;

UPDATE public.ai_categorization_log
SET applied = true
WHERE applied IS NULL;

CREATE INDEX IF NOT EXISTS ix_aicatlog_review_queue
  ON public.ai_categorization_log(user_action, applied, applied_at)
  WHERE user_action IS NULL AND undone_at IS NULL;

COMMENT ON COLUMN public.ai_categorization_log.applied IS 'true when suggested_category has been written to the target row';
COMMENT ON COLUMN public.ai_categorization_log.before_category IS 'category_code on the target row before this log applied; used for undo/reject';
COMMENT ON COLUMN public.ai_categorization_log.applied_by IS 'rule | ai | human | admin_backfill/source user id';
COMMENT ON COLUMN public.ai_categorization_log.undone_at IS 'set when an applied categorization is reverted';
COMMENT ON COLUMN public.ai_categorization_log.undo_reason IS 'admin_undo | user_reject or future explicit reason';
