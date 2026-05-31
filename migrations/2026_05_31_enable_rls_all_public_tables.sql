-- =====================================================================
-- 2026-05-31 SECURITY AUDIT — GAP 1: enable RLS on all public tables
-- =====================================================================
-- APPLIED LIVE 2026-05-31 via Supabase MCP (TUM approved "เปิดเดี๋ยวนี้").
-- Committed here for the record / reproducibility.
--
-- WHY: 57 of 59 public tables had RLS DISABLED. The project's public anon key
-- (shipped in the frontend bundle) could therefore read every financial table
-- directly via PostgREST:
--     GET https://<proj>.supabase.co/rest/v1/pos_bills            -> 200 (real bills)
--     GET .../rest/v1/bank_statement_entries | vendor_bills | counterparties  -> 200
-- i.e. a full unauthenticated financial-data breach that bypasses the FastAPI
-- backend entirely.
--
-- WHY SAFE: the backend connects with the service_role / postgres role (both
-- BYPASSRLS), so enabling RLS with NO policy denies anon+authenticated while
-- leaving the backend 100% functional. Verified post-apply: anon REST returns
-- [] on every financial table; backend /health/deep 200; full smoke 64/64;
-- /menu/public 200; marastation.com 200. (ai_chat_messages / ai_chat_sessions
-- already had RLS + a policy and are untouched.)
--
-- REVERSIBLE per table:  ALTER TABLE public.<t> DISABLE ROW LEVEL SECURITY;
DO $$
DECLARE t text;
BEGIN
  FOR t IN
    SELECT c.relname
    FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'public' AND c.relkind = 'r' AND NOT c.relrowsecurity
  LOOP
    EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY;', t);
  END LOOP;
END $$;

-- Verify (should return 0):
-- SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
-- WHERE n.nspname='public' AND c.relkind='r' AND NOT c.relrowsecurity;
