-- 2026-06-01 — Executive-audit CRITICAL fix (finding CEO-SEC-01)
-- =============================================================================
-- WHAT WAS WRONG
-- Session 49 enabled RLS on all 63 `public` TABLES (closing the anon-key read
-- breach at the table level). But the 27 SECURITY DEFINER views in `public`
-- still had SELECT granted to roles `anon` + `authenticated`. A security_definer
-- view executes as its OWNER (postgres = BYPASSRLS), so the public anon key
-- (shipped in the app.marastation.com JS bundle) could read the ENTIRE business
-- straight from PostgREST, bypassing the table RLS baseline AND all FastAPI auth:
--   GET /rest/v1/v_dashboard_overview  -> May-2026 sales 254,809.75 / profit 186,474.74 / margin 73.18%
--   GET /rest/v1/v_daybook_pnl         -> full daily P&L ledger
--   GET /rest/v1/v_shop_savings        -> savings ledger ... (+24 more views)
-- This re-opened the exact Session-49 GAP-1 breach, through the views.
--
-- THE FIX
-- Revoke ALL privileges on every security-definer view in `public` from
-- anon + authenticated. The backend reads these views as the service_role /
-- postgres owner (BYPASSRLS), so it is UNAFFECTED. Neither frontend reads these
-- views via the anon role: the VEXONHQ browser Supabase client is SSO-auth-only
-- (zero `.from()` table/view reads — all data goes through the JWT backend), and
-- marastation-web talks to Postgres only via Prisma on the `web` schema.
--
-- APPLIED TO PROD 2026-06-01 via Supabase MCP apply_migration
--   (name: revoke_anon_select_security_definer_views). This file is the repo record.
--
-- VERIFIED (2026-06-01, post-fix):
--   anon REST GET v_daybook_pnl|v_dashboard_overview|v_shop_savings|v_ar_ap_summary|v_daily_sales
--       -> 42501 "permission denied for view" (was: real financial rows)
--   base table pos_bills (anon)  -> []                 (control unchanged)
--   owner SELECT count(v_dashboard_overview)=18, count(v_daybook_pnl)=1651  (backend still reads)
--   api.marastation.com/health/deep healthy; /menu/public 200  (apps unaffected)
--
-- ROLLBACK (only if something breaks — this re-opens the breach):
--   DO $$ DECLARE v record; BEGIN
--     FOR v IN SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
--       WHERE n.nspname='public' AND c.relkind='v'
--         AND COALESCE((SELECT option_value FROM pg_options_to_table(c.reloptions)
--                       WHERE option_name='security_invoker'),'false') <> 'true'
--     LOOP EXECUTE format('GRANT SELECT ON public.%I TO anon, authenticated', v.relname); END LOOP;
--   END $$;
--
-- RECURRENCE NOTE (root cause — see backend AGENTS.md pitfall #45):
--   pg_default_acl shows `postgres` and `supabase_admin` still default-GRANT all
--   privileges to anon+authenticated on FUTURE public objects. So any NEW
--   security-definer view created in `public` will re-leak the same way until it
--   is either created with `security_invoker=on` (then table RLS applies) or has
--   anon+authenticated SELECT revoked like below. Each new reporting view must do this.
-- =============================================================================

DO $$
DECLARE v record;
BEGIN
  FOR v IN
    SELECT c.relname
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'public'
      AND c.relkind = 'v'
      AND COALESCE(
            (SELECT option_value FROM pg_options_to_table(c.reloptions)
             WHERE option_name = 'security_invoker'),
            'false') <> 'true'
  LOOP
    EXECUTE format('REVOKE ALL ON public.%I FROM anon, authenticated', v.relname);
  END LOOP;
END $$;
