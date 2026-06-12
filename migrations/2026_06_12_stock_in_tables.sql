-- B2 M1: Stock-in import tables
-- Spec: VEXONHQ/docs/03_SPECS/B2_STOCKIN_AI_SEARCH_SPEC.md §2.3
-- Run once on production (idempotent — all CREATE IF NOT EXISTS).

-- ── stock_in_staging ──────────────────────────────────────────────────────────
-- Holds one upload's parsed rows while awaiting reconciliation + approval.
-- Cleared after approve or cancel.
CREATE TABLE IF NOT EXISTS public.stock_in_staging (
    id               uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    import_id        uuid        NOT NULL REFERENCES public.pos_imports(id),
    branch_code      text        NOT NULL,
    received_date    date        NOT NULL,
    item_name        text        NOT NULL,
    material_code    text,
    tag              text,
    refill_type      text,
    invoice_no       text,
    gr_ref           text,
    po_ref           text,
    po_date          date,
    unit             text        NOT NULL DEFAULT '',
    qty              numeric     NOT NULL,
    unit_cost        numeric     NOT NULL DEFAULT 0,
    net_cost         numeric     NOT NULL DEFAULT 0,
    canonical_key    text        NOT NULL,
    occurrence_index int         NOT NULL,
    identity_key     text        NOT NULL,
    source_row_number int        NOT NULL,
    original_row_json jsonb      NOT NULL,
    created_at       timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_stock_in_staging_import_id
    ON public.stock_in_staging (import_id);


-- ── stock_in_lines ────────────────────────────────────────────────────────────
-- Committed truth; 1 row = 1 approved received-stock line.
-- Soft-delete only: row_status = 'active' | 'voided' | 'superseded'.
CREATE TABLE IF NOT EXISTS public.stock_in_lines (
    id               uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    import_id        uuid        NOT NULL REFERENCES public.pos_imports(id),
    branch_code      text        NOT NULL,
    received_date    date        NOT NULL,
    item_name        text        NOT NULL,
    material_code    text,
    tag              text,
    refill_type      text,
    invoice_no       text,
    gr_ref           text,
    po_ref           text,
    po_date          date,
    unit             text        NOT NULL DEFAULT '',
    qty              numeric     NOT NULL,
    unit_cost        numeric     NOT NULL DEFAULT 0,
    net_cost         numeric     NOT NULL DEFAULT 0,
    canonical_key    text        NOT NULL,
    occurrence_index int         NOT NULL,
    identity_key     text        NOT NULL,
    source_row_number int        NOT NULL,
    original_row_json jsonb      NOT NULL,
    row_status       text        NOT NULL DEFAULT 'active',
    superseded_by    uuid        REFERENCES public.stock_in_lines(id),
    voided_by        text,
    voided_at        timestamptz,
    void_reason      text,
    source           text        NOT NULL DEFAULT 'foodstory_refill_report',
    created_at       timestamptz NOT NULL DEFAULT now()
);

-- Uniqueness authority: only over active rows (spec §2.3)
CREATE UNIQUE INDEX IF NOT EXISTS uq_stock_in_lines_active_key
    ON public.stock_in_lines (canonical_key, occurrence_index)
    WHERE row_status = 'active';

CREATE INDEX IF NOT EXISTS idx_stock_in_lines_import_id
    ON public.stock_in_lines (import_id);

CREATE INDEX IF NOT EXISTS idx_stock_in_lines_branch_date
    ON public.stock_in_lines (branch_code, received_date)
    WHERE row_status = 'active';


-- ── stock_in_reconcile_log ────────────────────────────────────────────────────
-- Append-only audit log; one row per approve or cancel action (spec §2.6a).
CREATE TABLE IF NOT EXISTS public.stock_in_reconcile_log (
    id              uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    import_id_new   uuid        NOT NULL REFERENCES public.pos_imports(id),
    import_id_prev  uuid        REFERENCES public.pos_imports(id),
    branch_code     text        NOT NULL,
    period_start    date        NOT NULL,
    period_end      date        NOT NULL,
    approved_by     text        NOT NULL,
    approved_at     timestamptz NOT NULL DEFAULT now(),
    decision        text        NOT NULL,   -- 'approve' | 'cancel'
    reason          text,
    counts_json     jsonb       NOT NULL,   -- {new, unchanged, changed, missing, unmapped, ambiguous}
    before_after_diff jsonb     NOT NULL    -- full before/after row diff
);

CREATE INDEX IF NOT EXISTS idx_reconcile_log_import
    ON public.stock_in_reconcile_log (import_id_new);


-- ── pos_imports: CHECK constraints + processing_started_at ───────────────────
-- Harden existing pos_imports table for M1 stock-in flow.

-- Add processing_started_at so we can detect and recover stuck 'parsing' imports.
ALTER TABLE public.pos_imports
    ADD COLUMN IF NOT EXISTS processing_started_at timestamptz;

-- CHECK: status on pos_imports (allow staged, needs_review, cancelled)
ALTER TABLE public.pos_imports
    DROP CONSTRAINT IF EXISTS chk_pos_import_status,
    ADD CONSTRAINT chk_pos_import_status
        CHECK (status IN ('pending', 'parsing', 'success', 'failed', 'staged', 'needs_review', 'cancelled'));

-- CHECK: row_status on stock_in_lines
ALTER TABLE public.stock_in_lines
    DROP CONSTRAINT IF EXISTS chk_stock_in_lines_row_status,
    ADD CONSTRAINT chk_stock_in_lines_row_status
        CHECK (row_status IN ('active', 'voided', 'superseded'));

-- CHECK: decision on stock_in_reconcile_log
ALTER TABLE public.stock_in_reconcile_log
    DROP CONSTRAINT IF EXISTS chk_reconcile_log_decision,
    ADD CONSTRAINT chk_reconcile_log_decision
        CHECK (decision IN ('approve', 'cancel'));

-- ── Rollback instructions ─────────────────────────────────────────────────────
-- To undo this migration:
--   ALTER TABLE public.pos_imports DROP COLUMN IF EXISTS processing_started_at;
--   ALTER TABLE public.pos_imports DROP CONSTRAINT IF EXISTS chk_pos_import_status;
--   ALTER TABLE public.pos_imports ADD CONSTRAINT chk_pos_import_status CHECK (status IN ('pending', 'parsing', 'success', 'failed'));
--   ALTER TABLE public.stock_in_lines DROP CONSTRAINT IF EXISTS chk_stock_in_lines_row_status;
--   ALTER TABLE public.stock_in_reconcile_log DROP CONSTRAINT IF EXISTS chk_reconcile_log_decision;
--   DROP INDEX IF EXISTS idx_reconcile_log_import;
--   DROP TABLE IF EXISTS public.stock_in_reconcile_log;
--   DROP TABLE IF EXISTS public.stock_in_lines;
--   DROP TABLE IF EXISTS public.stock_in_staging;
