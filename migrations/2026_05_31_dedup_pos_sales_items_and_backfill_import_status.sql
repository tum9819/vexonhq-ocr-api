-- =====================================================================
-- 2026-05-31 AUDIT follow-up — data cleanup (applied live via MCP, recorded here)
-- =====================================================================
-- Context: finding #14 — pos_sales_items had NO UNIQUE(bill_id,line_no), so
-- re-imported FoodStory files double-inserted line items: 4,311 (bill_id,line_no)
-- duplicate pairs (each multiplicity 2). The code fix (delete-by-bill before insert,
-- commit a05ffa1) prevents new dups; this cleaned the existing ones.
--
-- Strategy: keep the row from the LATEST import per (bill_id,line_no) — for the
-- 2,805 divergent-content groups that's the corrected re-export; for the 1,506
-- exact-dup groups it's arbitrary (identical content). Backup table holds the
-- 4,311 deleted rows for rollback.
--
-- Result (verified): pos_sales_items 39,577 -> 35,266 rows, 0 remaining dups.

-- 1) Backup the rows to delete
CREATE TABLE IF NOT EXISTS public.pos_sales_items_dedup_bak_20260531 AS
WITH ranked AS (
  SELECT *, ROW_NUMBER() OVER (PARTITION BY bill_id, line_no
                               ORDER BY created_at DESC, id DESC) AS _rn
  FROM public.pos_sales_items
)
SELECT * FROM ranked WHERE _rn > 1;

-- 2) Delete the older duplicates
DELETE FROM public.pos_sales_items
WHERE id IN (SELECT id FROM public.pos_sales_items_dedup_bak_20260531);

-- 3) finding #12 — the one import stuck at status='parsing' (sync import aborted
--    pre-fix; the error-marking UPDATE used the INVALID status 'error' which the
--    chk_pos_import_status CHECK rejects, so it silently failed). Backfilled to the
--    valid 'failed'. The code now writes status='failed' (commit in this batch).
UPDATE public.pos_imports
SET status='failed',
    error_message='backfilled 2026-05-31 audit: sync import aborted pre-fix, error never persisted',
    finished_at=now()
WHERE id='02618313-4d72-45fb-98bf-d9f8c0bb4c96' AND status='parsing';

-- Rollback for (1)+(2): INSERT the backup rows back, then DROP the backup table.
-- Drop the backup table when confident:  DROP TABLE public.pos_sales_items_dedup_bak_20260531;
