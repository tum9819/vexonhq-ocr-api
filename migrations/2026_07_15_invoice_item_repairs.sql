-- 2026-07-15 — Invoice item repair log (OCR completeness repair flow)
--
-- Every repair (re-OCR replace / single-line replace) snapshots the bill's
-- old + new invoice_items here BEFORE the old rows are deleted, so any repair
-- is reversible from this log. Written only by the backend (service role).
--
-- RLS baseline (AGENTS #26/#45): every new public table gets RLS enabled with
-- no policy — backend connects as service role / postgres owner (BYPASSRLS),
-- anon REST gets nothing.

CREATE TABLE IF NOT EXISTS public.invoice_item_repairs (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    vendor_bill_id  uuid NOT NULL REFERENCES public.vendor_bills(id) ON DELETE CASCADE,
    action          text NOT NULL CHECK (action IN ('reocr', 'single_line')),
    old_items       jsonb NOT NULL DEFAULT '[]'::jsonb,
    new_items       jsonb NOT NULL DEFAULT '[]'::jsonb,
    old_sum         numeric(12,2),
    new_sum         numeric(12,2),
    created_by      text,
    created_at      timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_invoice_item_repairs_bill
    ON public.invoice_item_repairs (vendor_bill_id);

ALTER TABLE public.invoice_item_repairs ENABLE ROW LEVEL SECURITY;
