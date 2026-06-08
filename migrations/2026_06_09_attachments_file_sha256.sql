-- 2026_06_09_attachments_file_sha256.sql
--
-- File-level idempotency for invoice uploads.
--
-- Stores the SHA-256 of the ORIGINAL uploaded file bytes on every attachment
-- row that upload produced. _process_upload computes the hash once and, before
-- any OCR, looks it up: if a vendor_bill attachment already carries this hash,
-- the identical file was uploaded before, so we skip OCR + items + attachment
-- and return the existing bill (already_uploaded:true).
--
-- This replaces the fragile OCR-content comparison that failed to dedupe
-- re-uploads because GPT vision is non-deterministic (same image -> slightly
-- different items each run -> "different page" -> duplicate insert). A byte hash
-- is deterministic and independent of OCR.
--
-- Design notes:
--   * Nullable + additive: backward compatible. Currently-deployed code ignores
--     the column; old rows stay NULL (no backfill this round).
--   * NON-UNIQUE on purpose: a multi-page upload writes N attachment rows that
--     all share the SAME file hash, so a UNIQUE constraint would reject pages
--     2..N. Dedup is enforced in code (the pre-OCR lookup), not by the DB.
--   * Partial index (WHERE file_sha256 IS NOT NULL) keeps the index small while
--     old NULL rows exist.
--
-- Rollback:
--   DROP INDEX IF EXISTS public.idx_attachments_file_sha256;
--   ALTER TABLE public.attachments DROP COLUMN IF EXISTS file_sha256;

ALTER TABLE public.attachments
  ADD COLUMN IF NOT EXISTS file_sha256 text;

CREATE INDEX IF NOT EXISTS idx_attachments_file_sha256
  ON public.attachments (file_sha256)
  WHERE file_sha256 IS NOT NULL;
