-- 2026-07-12 FA-015: widen ai_cat_status CHECK to accept 'rule' written by
-- pos_import.py deterministic rules (CODEX-5). Without this the next POS
-- cashflow import crashes on insert. Additive only.
ALTER TABLE public.pos_cashflow_entries DROP CONSTRAINT pos_cashflow_entries_ai_cat_status_check;
ALTER TABLE public.pos_cashflow_entries ADD CONSTRAINT pos_cashflow_entries_ai_cat_status_check
  CHECK (ai_cat_status = ANY (ARRAY['pending'::text,'confirmed'::text,'rejected'::text,'skipped'::text,'rule'::text]));
