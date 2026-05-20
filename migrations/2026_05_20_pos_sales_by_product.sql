-- ════════════════════════════════════════════════════════════════════════════
-- 2026-05-20 — pos_sales_by_product table (Session 28 ad-hoc fix)
-- ════════════════════════════════════════════════════════════════════════════
--
-- Context: pos_import.py has a parser for the "ยอดขายตามสินค้า" FoodStory
-- report that returns rows shaped for a `pos_sales_by_product` table, AND
-- recipe_routes.py /recipes/import-from-menu queries that exact table —
-- but the WRITER_CONFIG dict in pos_import.py never had an entry for it,
-- so the import loop logged "No WRITER_CONFIG for table — skipped" and
-- silently dropped every row.
--
-- TUM uploaded 6 monthly reports → 6 "สำเร็จ" upload cards → 0 rows
-- inserted → /recipes "นำเข้าจาก POS" found 0 menus.
--
-- This migration:
--   1. Creates pos_sales_by_product if it doesn't exist (was probably
--      drafted in a Supabase dashboard session that never made it into
--      the repo, or was never created at all).
--   2. Adds a UNIQUE constraint on (branch_code, period_start,
--      product_name) so the WRITER_CONFIG upsert has something to
--      ON CONFLICT against.
--
-- The matching code fix (add the entry to WRITER_CONFIG) ships in the
-- same commit as this migration so re-uploading the same files after
-- both are deployed populates the table cleanly.
-- ════════════════════════════════════════════════════════════════════════════

BEGIN;

CREATE TABLE IF NOT EXISTS public.pos_sales_by_product (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    branch_code         TEXT NOT NULL DEFAULT 'thawi_watthana',
    period_start        DATE NOT NULL,
    period_end          DATE NOT NULL,
    sku                 TEXT,
    product_name        TEXT NOT NULL,
    product_group       TEXT,
    category            TEXT,
    avg_cost            NUMERIC(12,2),
    avg_price           NUMERIC(12,2),
    qty_sold            INT NOT NULL DEFAULT 0,
    gross               NUMERIC(14,2) NOT NULL DEFAULT 0,
    cost_total          NUMERIC(14,2) NOT NULL DEFAULT 0,
    item_discount       NUMERIC(14,2) NOT NULL DEFAULT 0,
    net_amount          NUMERIC(14,2) NOT NULL DEFAULT 0,
    profit              NUMERIC(14,2) NOT NULL DEFAULT 0,
    avg_profit          NUMERIC(12,2),
    source_import_id    UUID,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- UNIQUE INDEX (idempotent) — required for ON CONFLICT in WRITER_CONFIG.
-- Using a unique INDEX rather than ADD CONSTRAINT so CREATE ... IF NOT
-- EXISTS works cleanly (the named-constraint route requires a DO block
-- to be idempotent).
CREATE UNIQUE INDEX IF NOT EXISTS pos_sales_by_product_uidx
    ON public.pos_sales_by_product (branch_code, period_start, product_name);

-- Helpful indexes for the /recipes import + Coolify analytics queries.
CREATE INDEX IF NOT EXISTS idx_psbp_branch       ON public.pos_sales_by_product (branch_code);
CREATE INDEX IF NOT EXISTS idx_psbp_period_start ON public.pos_sales_by_product (period_start);
CREATE INDEX IF NOT EXISTS idx_psbp_product_name ON public.pos_sales_by_product (product_name);

-- Preview
SELECT 'pos_sales_by_product columns' AS metric, COUNT(*)::text AS value
FROM information_schema.columns
WHERE table_schema='public' AND table_name='pos_sales_by_product'
UNION ALL
SELECT 'pos_sales_by_product rows', COUNT(*)::text FROM public.pos_sales_by_product;

COMMIT;
