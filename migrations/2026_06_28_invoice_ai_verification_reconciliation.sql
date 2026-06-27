-- 2026-06-28 — OCR verification + discount reconciliation schema
--
-- Additive only. Do not apply to production until TUM approves the release.
--
-- Rollback instructions:
--   ALTER TABLE public.vendor_bills DROP CONSTRAINT IF EXISTS vendor_bills_last_ai_verification_id_fkey;
--   ALTER TABLE public.vendor_bills DROP CONSTRAINT IF EXISTS vendor_bills_last_reconciliation_id_fkey;
--   ALTER TABLE public.vendor_bills
--     DROP COLUMN IF EXISTS ocr_raw_text,
--     DROP COLUMN IF EXISTS verification_status,
--     DROP COLUMN IF EXISTS verification_confidence,
--     DROP COLUMN IF EXISTS last_ai_verification_id,
--     DROP COLUMN IF EXISTS reconciliation_status,
--     DROP COLUMN IF EXISTS reconciliation_difference,
--     DROP COLUMN IF EXISTS reconciliation_tolerance,
--     DROP COLUMN IF EXISTS last_reconciliation_id,
--     DROP COLUMN IF EXISTS discount_breakdown_json;
--   ALTER TABLE public.invoice_items
--     DROP COLUMN IF EXISTS gross_amount,
--     DROP COLUMN IF EXISTS line_discount_amount,
--     DROP COLUMN IF EXISTS line_discount_rate,
--     DROP COLUMN IF EXISTS net_amount,
--     DROP COLUMN IF EXISTS field_confidence,
--     DROP COLUMN IF EXISTS verifier_flags;
--   DROP TABLE IF EXISTS public.invoice_reconciliation_results;
--   DROP TABLE IF EXISTS public.invoice_ai_verifications;

BEGIN;

CREATE TABLE IF NOT EXISTS public.invoice_ai_verifications (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  vendor_bill_id uuid NOT NULL REFERENCES public.vendor_bills(id) ON DELETE CASCADE,
  provider text NOT NULL,
  model text NULL,
  status text NOT NULL,
  confidence numeric(5,4) NULL,
  is_real_ai boolean NOT NULL DEFAULT false,
  result_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  mismatches_json jsonb NOT NULL DEFAULT '[]'::jsonb,
  error_code text NULL,
  error_message text NULL,
  latency_ms integer NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.invoice_reconciliation_results (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  vendor_bill_id uuid NOT NULL REFERENCES public.vendor_bills(id) ON DELETE CASCADE,
  status text NOT NULL,
  gross_item_total numeric(14,2) NULL,
  line_discount_total numeric(14,2) NULL,
  net_item_total numeric(14,2) NULL,
  bill_discount_total numeric(14,2) NULL,
  voucher_discount_total numeric(14,2) NULL,
  promotion_discount_total numeric(14,2) NULL,
  service_charge numeric(14,2) NULL,
  vat numeric(14,2) NULL,
  rounding_adjustment numeric(14,2) NULL,
  calculated_total numeric(14,2) NULL,
  stated_total numeric(14,2) NULL,
  difference numeric(14,2) NULL,
  tolerance numeric(14,2) NOT NULL DEFAULT 0.05,
  breakdown_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  warnings_json jsonb NOT NULL DEFAULT '[]'::jsonb,
  blocking boolean NOT NULL DEFAULT false,
  created_at timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE public.vendor_bills
  ADD COLUMN IF NOT EXISTS ocr_raw_text text NULL,
  ADD COLUMN IF NOT EXISTS verification_status text NULL,
  ADD COLUMN IF NOT EXISTS verification_confidence numeric(5,4) NULL,
  ADD COLUMN IF NOT EXISTS last_ai_verification_id uuid NULL,
  ADD COLUMN IF NOT EXISTS reconciliation_status text NULL,
  ADD COLUMN IF NOT EXISTS reconciliation_difference numeric(14,2) NULL,
  ADD COLUMN IF NOT EXISTS reconciliation_tolerance numeric(14,2) NULL,
  ADD COLUMN IF NOT EXISTS last_reconciliation_id uuid NULL,
  ADD COLUMN IF NOT EXISTS discount_breakdown_json jsonb NULL;

ALTER TABLE public.invoice_items
  ADD COLUMN IF NOT EXISTS gross_amount numeric(14,2) NULL,
  ADD COLUMN IF NOT EXISTS line_discount_amount numeric(14,2) NULL,
  ADD COLUMN IF NOT EXISTS line_discount_rate numeric(7,4) NULL,
  ADD COLUMN IF NOT EXISTS net_amount numeric(14,2) NULL,
  ADD COLUMN IF NOT EXISTS field_confidence jsonb NULL,
  ADD COLUMN IF NOT EXISTS verifier_flags jsonb NULL;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'invoice_ai_verifications_status_check'
      AND conrelid = 'public.invoice_ai_verifications'::regclass
  ) THEN
    ALTER TABLE public.invoice_ai_verifications
      ADD CONSTRAINT invoice_ai_verifications_status_check
      CHECK (status IN ('not_configured', 'not_run', 'verified', 'mismatch', 'low_confidence', 'failed', 'timeout'));
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'invoice_ai_verifications_confidence_check'
      AND conrelid = 'public.invoice_ai_verifications'::regclass
  ) THEN
    ALTER TABLE public.invoice_ai_verifications
      ADD CONSTRAINT invoice_ai_verifications_confidence_check
      CHECK (confidence IS NULL OR (confidence >= 0.0000 AND confidence <= 1.0000));
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'invoice_reconciliation_results_status_check'
      AND conrelid = 'public.invoice_reconciliation_results'::regclass
  ) THEN
    ALTER TABLE public.invoice_reconciliation_results
      ADD CONSTRAINT invoice_reconciliation_results_status_check
      CHECK (status IN ('matched', 'mismatch', 'needs_review', 'not_run'));
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'vendor_bills_verification_status_check'
      AND conrelid = 'public.vendor_bills'::regclass
  ) THEN
    ALTER TABLE public.vendor_bills
      ADD CONSTRAINT vendor_bills_verification_status_check
      CHECK (
        verification_status IS NULL OR
        verification_status IN ('not_configured', 'not_run', 'verified', 'mismatch', 'low_confidence', 'failed', 'timeout')
      );
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'vendor_bills_verification_confidence_check'
      AND conrelid = 'public.vendor_bills'::regclass
  ) THEN
    ALTER TABLE public.vendor_bills
      ADD CONSTRAINT vendor_bills_verification_confidence_check
      CHECK (verification_confidence IS NULL OR (verification_confidence >= 0.0000 AND verification_confidence <= 1.0000));
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'vendor_bills_reconciliation_status_check'
      AND conrelid = 'public.vendor_bills'::regclass
  ) THEN
    ALTER TABLE public.vendor_bills
      ADD CONSTRAINT vendor_bills_reconciliation_status_check
      CHECK (reconciliation_status IS NULL OR reconciliation_status IN ('matched', 'mismatch', 'needs_review', 'not_run'));
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'vendor_bills_last_ai_verification_id_fkey'
      AND conrelid = 'public.vendor_bills'::regclass
  ) THEN
    ALTER TABLE public.vendor_bills
      ADD CONSTRAINT vendor_bills_last_ai_verification_id_fkey
      FOREIGN KEY (last_ai_verification_id)
      REFERENCES public.invoice_ai_verifications(id)
      ON DELETE SET NULL;
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'vendor_bills_last_reconciliation_id_fkey'
      AND conrelid = 'public.vendor_bills'::regclass
  ) THEN
    ALTER TABLE public.vendor_bills
      ADD CONSTRAINT vendor_bills_last_reconciliation_id_fkey
      FOREIGN KEY (last_reconciliation_id)
      REFERENCES public.invoice_reconciliation_results(id)
      ON DELETE SET NULL;
  END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_invoice_ai_verifications_bill_created
  ON public.invoice_ai_verifications (vendor_bill_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_invoice_ai_verifications_status
  ON public.invoice_ai_verifications (status);

CREATE INDEX IF NOT EXISTS idx_invoice_reconciliation_results_bill_created
  ON public.invoice_reconciliation_results (vendor_bill_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_invoice_reconciliation_results_status
  ON public.invoice_reconciliation_results (status);

CREATE INDEX IF NOT EXISTS idx_vendor_bills_verification_status
  ON public.vendor_bills (verification_status)
  WHERE verification_status IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_vendor_bills_reconciliation_status
  ON public.vendor_bills (reconciliation_status)
  WHERE reconciliation_status IS NOT NULL;

ALTER TABLE public.invoice_ai_verifications ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.invoice_reconciliation_results ENABLE ROW LEVEL SECURITY;

COMMIT;
