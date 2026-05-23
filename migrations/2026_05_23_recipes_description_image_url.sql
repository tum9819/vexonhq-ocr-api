-- ════════════════════════════════════════════════════════════════════════════
-- 2026-05-23 — Public-facing menu fields on recipes (Item A_new, Session 33)
-- ════════════════════════════════════════════════════════════════════════════
--
-- THE PROBLEM
-- -----------
-- The new GET /menu/public endpoint (consumed by marastation-web public
-- site at marastation.com) needs to expose a customer-friendly menu:
-- name + price + category + description blurb + dish photo.
--
-- The recipes table today carries only the operational fields
-- (id, name, selling_price, category, notes, created_at, updated_at).
-- `notes` is internal-only (cost notes, supplier comments) so it must
-- NOT be exposed publicly. We need two new public-safe fields.
--
-- THE SCHEMA CHANGE
-- -----------------
-- description:
--   Free-text Thai blurb (1-3 sentences) shown on the public menu page.
--   Example: "เนื้อหมูสามชั้นย่างเตาถ่าน หมักสูตรเฉพาะ เสิร์ฟพร้อมน้ำจิ้มแจ่ว"
--   NULLABLE: TUM may leave blank → public site hides description line.
--
-- image_url:
--   Public URL of the dish photo. Host: Supabase Storage bucket
--   `menu-images` (separate Session 33 setup step). TUM uploads via the
--   Supabase dashboard and pastes the public URL into the admin UI.
--   Same column will later host restaurant interior / event photos
--   (different bucket or sub-path within the same bucket).
--   NULLABLE: TUM may leave blank → public site renders placeholder.
--
-- BOUNDARY
-- --------
-- Neither field is required at the API or UI level. Existing 190
-- recipes get NULL on both columns. The public endpoint returns NULL
-- as-is — public site is responsible for placeholder rendering.
--
-- Idempotent: ADD COLUMN IF NOT EXISTS — safe to re-run.
-- ════════════════════════════════════════════════════════════════════════════

BEGIN;

ALTER TABLE public.recipes
    ADD COLUMN IF NOT EXISTS description TEXT,
    ADD COLUMN IF NOT EXISTS image_url   TEXT;

-- ─── Verify ────────────────────────────────────────────────────────────────
SELECT column_name, data_type, is_nullable
FROM information_schema.columns
WHERE table_schema = 'public'
  AND table_name   = 'recipes'
ORDER BY ordinal_position;

COMMIT;
