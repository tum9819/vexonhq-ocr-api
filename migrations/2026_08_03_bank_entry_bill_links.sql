-- 2026-08-03 — One bank transfer may settle several vendor bills
--
-- Why: `bank_statement_entries.matched_invoice_id` is a single column on the
-- statement row, so a transfer could hold exactly one invoice. Real payments do
-- not work that way — verified against production, e.g. the 2025-11-04 transfer
-- of 27,039.97 is exactly 5,309.98 + 21,729.99 of two SINGHA BEER invoices, and
-- the 2026-01-12 transfer of 28,609.98 is exactly 7,970.00 + 20,639.98. Those
-- invoices could never be attached, so the tax evidence pack had no image for
-- them.
--
-- Cardinality: one transfer -> many bills; one bill -> one transfer.
-- The reverse direction (a single bill split across several transfers) was
-- tested against production and found nowhere: the only apparent hits were two
-- recurring 2,100 musician-fee transfers coincidentally summing to a 4,200 bill,
-- which is combinatorial noise, not a real split payment. The stricter rule is
-- therefore enforced now because it blocks double-counting a bill against two
-- transfers. If a genuine split payment ever appears, dropping
-- `uq_bank_entry_bill_links_bill` relaxes this without touching any stored row.
--
-- `bank_statement_entries.matched_invoice_id` is intentionally LEFT IN PLACE and
-- keeps working as the "first bill on this transfer" mirror, so every existing
-- reader (export audit package, export readiness, slip matching, bill payment)
-- behaves exactly as before until it is migrated deliberately.
--
-- RLS baseline (AGENTS #26/#45): every new public table gets RLS enabled with
-- no policy — backend connects as service role / postgres owner (BYPASSRLS),
-- anon REST gets nothing.

CREATE TABLE IF NOT EXISTS public.bank_entry_bill_links (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    bank_entry_id   uuid NOT NULL REFERENCES public.bank_statement_entries(id) ON DELETE CASCADE,
    vendor_bill_id  uuid NOT NULL REFERENCES public.vendor_bills(id) ON DELETE CASCADE,
    created_by      text,
    created_at      timestamptz NOT NULL DEFAULT now()
);

-- One bill is settled by exactly one transfer.
CREATE UNIQUE INDEX IF NOT EXISTS uq_bank_entry_bill_links_bill
    ON public.bank_entry_bill_links (vendor_bill_id);

-- A transfer may settle many bills; this is the lookup used when rendering a
-- voucher and when computing how much of a transfer is still unallocated.
CREATE INDEX IF NOT EXISTS idx_bank_entry_bill_links_entry
    ON public.bank_entry_bill_links (bank_entry_id);

ALTER TABLE public.bank_entry_bill_links ENABLE ROW LEVEL SECURITY;

-- Backfill the links that already exist as matched_invoice_id so the new table
-- is the complete picture from day one. Idempotent.
INSERT INTO public.bank_entry_bill_links (bank_entry_id, vendor_bill_id, created_by)
SELECT b.id, b.matched_invoice_id, 'backfill:matched_invoice_id'
FROM public.bank_statement_entries b
WHERE b.matched_invoice_id IS NOT NULL
ON CONFLICT (vendor_bill_id) DO NOTHING;
