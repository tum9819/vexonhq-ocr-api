-- AI-6 (2026-06-03): record cashflow AI categorization decisions in
-- ai_categorization_log (previously bills-only, leaving cashflow guesses with no
-- audit trail). bill_id is already nullable, so this is purely additive +
-- backward-compatible (old code ignores the new columns). Applied via Supabase MCP.
ALTER TABLE public.ai_categorization_log
  ADD COLUMN IF NOT EXISTS cashflow_entry_id uuid REFERENCES public.pos_cashflow_entries(id),
  ADD COLUMN IF NOT EXISTS source text NOT NULL DEFAULT 'bill';

CREATE INDEX IF NOT EXISTS ix_aicatlog_cashflow_entry
  ON public.ai_categorization_log(cashflow_entry_id);

COMMENT ON COLUMN public.ai_categorization_log.source IS 'bill | cashflow — entity this AI decision applies to (AI-6)';
