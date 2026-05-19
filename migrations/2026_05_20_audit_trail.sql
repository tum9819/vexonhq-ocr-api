-- ════════════════════════════════════════════════════════════════════════════
-- 2026-05-20 — Per-user audit trail on vendor_bills (Session 26 item O)
-- ════════════════════════════════════════════════════════════════════════════
--
-- Context: multi-user login landed in Session 25 (Tum / May / Toon / Oil +
-- legacy vexonhq admin). Until now, write endpoints either ignored the
-- signed-in user entirely or trusted a client-supplied `reviewed_by` field.
-- This migration adds `updated_by` + `updated_at` columns on vendor_bills
-- so every edit / confirm / reject records who actually did it.
--
-- The backend reads JWT subject from request.state.username (set by the
-- JWTAuthMiddleware after `verify_token`) and stamps both fields on every
-- mutation. Client-supplied `reviewed_by` is now only a fallback for
-- legacy callers.
--
-- Safe to re-run: every ADD COLUMN is guarded by IF NOT EXISTS.
-- ════════════════════════════════════════════════════════════════════════════

BEGIN;

-- ─── vendor_bills — add audit columns ────────────────────────────────────────
ALTER TABLE public.vendor_bills
    ADD COLUMN IF NOT EXISTS updated_by  TEXT,
    ADD COLUMN IF NOT EXISTS updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW();

CREATE INDEX IF NOT EXISTS idx_vendor_bills_updated_by ON public.vendor_bills (updated_by);
CREATE INDEX IF NOT EXISTS idx_vendor_bills_updated_at ON public.vendor_bills (updated_at DESC);

-- ─── invoice_items — same audit pair, for inline edits ──────────────────────
ALTER TABLE public.invoice_items
    ADD COLUMN IF NOT EXISTS updated_by  TEXT,
    ADD COLUMN IF NOT EXISTS updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW();

-- ─── Preview ────────────────────────────────────────────────────────────────
SELECT
    table_name,
    column_name,
    data_type,
    is_nullable
FROM information_schema.columns
WHERE table_schema = 'public'
  AND table_name IN ('vendor_bills', 'invoice_items')
  AND column_name IN ('updated_by', 'updated_at')
ORDER BY table_name, column_name;

COMMIT;
-- ROLLBACK;  -- uncomment if preview looks wrong
