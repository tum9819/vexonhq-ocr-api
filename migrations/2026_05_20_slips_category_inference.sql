-- ════════════════════════════════════════════════════════════════════════════
-- 2026-05-20 — slips.statement_category_code + inference seed rules
-- ════════════════════════════════════════════════════════════════════════════
--
-- Context: TUM has slip memos that aren't products (ค่าน้ำ, ค่าเช่า,
-- ค่าดนตรี, เงินเดือน, etc.) — these can't map to products.sku because
-- they aren't stock items, but they DO have a clean accounting category
-- (utility / rent / musician_fee / staff_salary).
--
-- Strategy: re-use the existing `statement_rules` table (single source of
-- truth for category mapping) and cache the resolved category on the slip
-- row itself for fast queries. Resolution priority:
--
--   1. matched_statement.category_code  (verified — bank confirmed)
--   2. memo + rule_type='keyword'        (intent TUM typed himself)
--   3. recipient_name + rule_type='name' (fallback)
--   4. NULL                              (UI shows "ยังไม่จัด")
--
-- The new `category_source` column tells the UI which level fired so
-- a green ✓ chip can render for verified vs amber ? for inferred.
--
-- Disambiguation example (กาญจนา receives both rent + utility):
--   • memo "ค่าน้ำประปา"  → keyword rule (pri 100) → utility ✓
--   • memo "ค่าเช่า"      → keyword rule (pri 100) → rent ✓
--   • memo (empty)        → falls to name rule "กาญจนา" (pri 90) → rent
-- Because keyword priority (100) > name priority (90), the memo wins
-- when present. When memo is empty the name rule still keeps the
-- existing behaviour intact for legacy slips with no memo.
-- ════════════════════════════════════════════════════════════════════════════

BEGIN;

-- ─── 1. New columns on slips ────────────────────────────────────────────────
ALTER TABLE public.slips
    ADD COLUMN IF NOT EXISTS statement_category_code TEXT,
    ADD COLUMN IF NOT EXISTS category_source         TEXT
        CHECK (category_source IS NULL OR category_source IN
               ('statement', 'memo_keyword', 'recipient_name'));

CREATE INDEX IF NOT EXISTS idx_slips_category_code
    ON public.slips (statement_category_code);

-- ─── 1b. Ensure statement_rules has UNIQUE(rule_type, match_value) ──────────
-- Migration 16 declared this in CREATE TABLE IF NOT EXISTS but if the table
-- existed BEFORE that migration ran, the UNIQUE constraint never got added
-- (CREATE TABLE IF NOT EXISTS skips silently). Without it, the upsert below
-- fails with 42P10 "no unique or exclusion constraint matching". Same fix
-- pattern as the vendor_aliases unique migration (2026_05_20).
--
-- A UNIQUE INDEX is sufficient for ON CONFLICT (... ) and supports
-- CREATE ... IF NOT EXISTS out-of-the-box. Cleaner than wrangling
-- pg_constraint type casts.

-- Dedup first so the unique index can be created (no-op when zero dupes).
WITH ranked AS (
    SELECT id,
           ROW_NUMBER() OVER (
               PARTITION BY rule_type, match_value
               ORDER BY id
           ) AS rn
    FROM public.statement_rules
)
DELETE FROM public.statement_rules sr
USING ranked r
WHERE sr.id = r.id
  AND r.rn  > 1;

CREATE UNIQUE INDEX IF NOT EXISTS statement_rules_rule_type_match_value_uidx
    ON public.statement_rules (rule_type, match_value);

-- ─── 2. Seed disambiguating rules (Session 27 / Phase 6.5) ──────────────────
-- These get TUM's slips categorising on day 1. He can edit / extend through
-- the /rules page (Session 26 task E).
--
-- Priority guide:
--   100 = high-confidence keyword (must override less-specific name rule)
--    95 = name match — recipient is a known closed-set vendor
--    90 = name match — recipient may serve multiple categories
INSERT INTO public.statement_rules
    (rule_type, match_value, direction, category_code, source_type, priority)
VALUES
    -- ── Utility (ค่าน้ำประปา) ──
    ('keyword', 'ค่าน้ำประปา',  'expense', 'utility',      'bank_statement', 100),
    ('keyword', 'ค่าน้ำ',        'expense', 'utility',      'bank_statement',  95),
    ('keyword', 'ค่าไฟ',         'expense', 'utility',      'bank_statement', 100),
    ('keyword', 'ค่าไฟฟ้า',     'expense', 'utility',      'bank_statement', 100),
    ('keyword', 'ค่าแก๊ส',       'expense', 'utility',      'bank_statement', 100),
    ('keyword', 'ค่าก๊าซ',       'expense', 'utility',      'bank_statement', 100),
    ('keyword', 'ค่าอินเตอร์เน็ต', 'expense', 'utility',    'bank_statement', 100),

    -- ── Rent (ค่าเช่า) ──
    ('keyword', 'ค่าเช่า',       'expense', 'rent',         'bank_statement', 100),
    ('keyword', 'ค่าเช่าร้าน',  'expense', 'rent',         'bank_statement', 100),
    ('keyword', 'ค่าเช่าที่',   'expense', 'rent',         'bank_statement', 100),

    -- ── Beverage raw / น้ำดื่ม / เครื่องดื่ม supply ──
    ('name',    'สวนรื่นรมย์',  'expense', 'utility',      'bank_statement',  95),
    ('name',    'วิลลิเม็กซ์',  'expense', 'beverage_raw', 'bank_statement',  95),
    ('name',    'วิลลิเจกซ์',   'expense', 'beverage_raw', 'bank_statement',  95),

    -- ── Musician (ค่านักร้อง / ค่าดนตรี) ──
    ('keyword', 'ค่านักร้อง',    'expense', 'musician_fee', 'bank_statement', 100),
    ('keyword', 'ค่าดนตรี',      'expense', 'musician_fee', 'bank_statement', 100),

    -- ── Salary (เงินเดือน) ──
    ('keyword', 'เงินเดือน',     'expense', 'staff_salary', 'bank_statement', 100)
ON CONFLICT (rule_type, match_value) DO UPDATE
SET direction     = EXCLUDED.direction,
    category_code = EXCLUDED.category_code,
    source_type   = EXCLUDED.source_type,
    priority      = EXCLUDED.priority;

-- ─── 3. Preview ─────────────────────────────────────────────────────────────
SELECT 'new slips columns' AS metric,
       COUNT(*)::text AS value
FROM information_schema.columns
WHERE table_schema = 'public'
  AND table_name   = 'slips'
  AND column_name IN ('statement_category_code', 'category_source')
UNION ALL
SELECT 'seeded rules (priority>=95)' AS metric,
       COUNT(*)::text AS value
FROM public.statement_rules
WHERE priority >= 95;

COMMIT;
-- ROLLBACK;
