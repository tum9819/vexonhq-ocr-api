-- 2026-09-09 — Root-cause fix for the recurring "table publicly accessible" advisories
-- =============================================================================
-- Revision 3. Revisions 1 and 2 were both BLOCKED by adversarial review.
--   r1 -> r2: rollback was `GRANT ALL ON ALL TABLES`, which would have granted the
--             26 views privileges they never had (wider hole than the bug).
--   r2 -> r3: (a) rollback still inexact — `GRANT ALL` on PostgreSQL 17 includes
--                 MAINTAIN, which was NOT in the measured grant set (arwdDxtm);
--             (b) pg_default_acl was never snapshotted;
--             (c) the code-search evidence was still wrong — a whole application
--                 (marastation-ai) had never been searched;
--             (d) `ops` was claimed unreachable without proof;
--             (e) the function revisit trigger was too narrow;
--             (f) verification could pass while effective access remained.
--
-- WHAT IS WRONG
-- Every table in `public` grants ALL privileges to `anon` and `authenticated`
-- (pg_default_acl for schema public, granting roles postgres AND supabase_admin:
-- anon=arwdDxtm, authenticated=arwdDxtm). Because these are DEFAULT privileges,
-- every NEW table in `public` is born fully readable AND writable by the public
-- publishable key, and RLS is the only thing in the way. One forgotten
-- `ENABLE ROW LEVEL SECURITY` therefore exposes real financial data — which has
-- now happened four times: 2026-06-01, 2026-07-07 and 2026-09-09 (five audit
-- backup tables, proven live-exposed: publishable key -> HTTP 200 with Thai
-- personal names, partial account numbers, amounts, plus write and delete rights).
-- Every previous fix treated the symptom. This treats the cause.
--
-- MEASURED PRE-CHANGE STATE (2026-09-09; the preconditions below re-assert all of it)
--   PostgreSQL 17.6 · project osneubnwghvbwyazaedo (mara-ai-prod)
--   76 base tables (relkind r), each granting BOTH anon and authenticated exactly
--     DELETE, INSERT, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE  (= arwdDxtm,
--     which notably does NOT include MAINTAIN — so rollback must never say ALL)
--   26 views: no anon/authenticated grants · 0 foreign tables · 0 partitioned · 0 matviews
--   0 relations grant anything to PUBLIC
--   anon and authenticated are members of NO role (no inherited privileges)
--   is_grantable=YES: 0 · explicit column ACLs (pg_attribute.attacl): 0
--   9 sequences carry API-role grants (deliberately out of scope, see below)
--   8 functions, all SECURITY INVOKER, all returning `trigger`
--   no table belongs to any publication · pg_cron and pg_graphql not installed
--   all objects in public owned by postgres
--
-- EVIDENCE THIS IS SAFE — CORRECTED SCOPE
--   Applications searched: VEXONHQ, vexonhq-ocr-api, marastation-web, recycle-web
--   AND marastation-ai (omitted from revisions 1-2 — that omission was the third
--   search error in this task and is why this section is now stated narrowly).
--
--   The accurate claim is: NO data path using `anon` or `authenticated` reads or
--   writes `public`. It is NOT true that nothing reads `public` — service_role does.
--     * VEXONHQ lib/supabase-browser.ts, lib/supabase-server.ts,
--       app/api/backend/[...path]/route.ts — publishable key, Auth/session only.
--     * marastation-web src/lib/auth/supabase-server.ts, src/proxy.ts,
--       src/app/api/backend/[...path]/route.ts — publishable key, Auth only;
--       src/lib/storage/marketer-storage.ts:47,56 call storage.from() with the
--       SERVICE-ROLE client (schema `storage`, unaffected).
--     * marastation-ai lib/memory.ts reads/writes public.ai_chat_sessions and
--       public.ai_chat_messages, but every call site passes the SERVICE-ROLE
--       client: app/api/chat/route.ts:43,47 and app/api/confirm/route.ts:30,31.
--       Its publishable-key clients (lib/supabase.ts, lib/supabase-server.ts:8)
--       are used only for auth.getUser() and the login page.
--     * marastation-web reaches Postgres via Prisma scoped to schema `web`.
--     * The FastAPI backend connects as service_role / postgres (BYPASSRLS).
--   Supabase Auth runs in the `auth` schema as supabase_auth_admin, so login does
--   not depend on `public` grants. Realtime is unaffected (no publications).
--
-- KNOWN CONSEQUENCE — READ THIS BEFORE DEBUGGING A FUTURE "CHAT IS BROKEN"
--   public.ai_chat_sessions and public.ai_chat_messages are the ONLY two tables in
--   the database with real RLS policies (`users_own_sessions`, `users_own_messages`,
--   USING user_id = auth.uid(), applied to all roles). They were written for the
--   standard Supabase pattern: a signed-in user querying their own rows straight
--   from the Data API as `authenticated`. Today marastation-ai does not use that
--   path — it goes through service_role — so revoking is safe NOW. But after this
--   migration those policies are inert: with no table grant, the policy never gets
--   a chance to allow anything. If anyone later moves the chat to a client-side
--   query, it will fail with a permission error that looks nothing like an RLS
--   problem. The fix at that point is a deliberate, narrow grant:
--     GRANT SELECT, INSERT, UPDATE, DELETE ON public.ai_chat_sessions,
--                                            public.ai_chat_messages
--       TO authenticated;   -- NOT to anon, and NOT to every table
--
-- UNRESOLVED RESIDUAL (not solved by this migration)
--   `public` carries a second, identical default-ACL entry owned by supabase_admin.
--   We connect as `postgres`, which has no MEMBER/USAGE/SET access to supabase_admin,
--   so this migration cannot touch it. Every object in `public` is currently owned by
--   postgres, and Supabase documents that SQL Editor and customer objects run as
--   postgres, but there is no binding guarantee that managed automation will never
--   create a `public` object as supabase_admin. If it does, that table would again be
--   born granted. The RLS baseline and the Supabase advisor remain the net for that
--   path. Re-check pg_default_acl after any Supabase platform migration.
--
-- FUNCTIONS AND SEQUENCES — EXPLICIT DECISION: OUT OF SCOPE HERE
--   All 8 functions in `public` are SECURITY INVOKER returning `trigger`; PostgreSQL
--   refuses direct invocation of a trigger function and PostgREST will not expose one
--   as an RPC, so their EXECUTE grants are inert. The 9 sequences become unreachable
--   through the Data API once table privileges are gone. Bundling them adds risk for
--   no measurable gain, so they are left to a separate, separately reviewed change.
--   REVISIT WHEN ANY OF THESE HAPPENS (widened from r2, which only listed the first):
--     * a function is added to `public` that does NOT return `trigger`
--       (PostgREST exposes every callable function in an exposed schema under /rpc)
--     * any function becomes SECURITY DEFINER
--     * function or default-function ACLs change
--
-- SCHEMA USAGE IS DELIBERATELY KEPT
--   USAGE on schema `public` is NOT revoked from the API roles. It would break any
--   future SECURITY DEFINER RPC and is unnecessary for containment: without table
--   privileges, USAGE alone exposes nothing.
--
-- ABOUT `ops` — CLAIM DOWNGRADED
--   The snapshot lives in schema `ops`, which is INTENDED to be private: PostgREST
--   serves only its configured schemas and also requires schema USAGE, and this
--   migration grants none. But PostgREST's exposed-schema list is NOT readable from
--   SQL (pg_db_role_setting for `authenticator` holds only supautils and timeouts),
--   so "ops is not in the exposed list" remains unverifiable from SQL. It turns out
--   not to matter: measured after applying, anon and authenticated hold ZERO effective
--   privileges on the ops tables (has_table_privilege over SELECT/INSERT/UPDATE/DELETE
--   = 0) and no USAGE on the schema, so even if `ops` were exposed there is nothing to
--   read. Confirming its absence in Dashboard -> Settings -> API -> Exposed schemas is
--   tidiness, not a security requirement.
--   The snapshot holds table names and privilege names only — no business data.
-- =============================================================================

BEGIN;

-- 0) PRECONDITIONS — if reality no longer matches what was measured, change nothing.
DO $$
DECLARE v int; s text;
BEGIN
  SELECT count(*) INTO v FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace AND n.nspname = 'public',
    LATERAL aclexplode(c.relacl) a WHERE a.grantee = 0;
  IF v <> 0 THEN RAISE EXCEPTION 'precondition failed: % relations in public grant to PUBLIC; revoking direct grants would not remove access', v; END IF;

  SELECT count(*) INTO v FROM pg_auth_members m
    JOIN pg_roles r ON r.oid = m.member WHERE r.rolname IN ('anon','authenticated');
  IF v <> 0 THEN RAISE EXCEPTION 'precondition failed: anon/authenticated now inherit from % role(s); effective privileges may survive the revoke', v; END IF;

  SELECT count(*) INTO v FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace AND n.nspname = 'public',
    LATERAL aclexplode(c.relacl) a JOIN pg_roles r ON r.oid = a.grantee
   WHERE r.rolname IN ('anon','authenticated') AND c.relkind IN ('p','m');
  IF v <> 0 THEN RAISE EXCEPTION 'precondition failed: % partitioned tables/matviews carry API-role grants; the relkind=r rollback filter would not restore them', v; END IF;

  SELECT count(*) INTO v FROM information_schema.role_table_grants
   WHERE table_schema = 'public' AND grantee IN ('anon','authenticated') AND is_grantable = 'YES';
  IF v <> 0 THEN RAISE EXCEPTION 'precondition failed: % grants carry WITH GRANT OPTION; the rollback does not reproduce grant options', v; END IF;

  SELECT count(*) INTO v FROM pg_attribute a
    JOIN pg_class c ON c.oid = a.attrelid
    JOIN pg_namespace n ON n.oid = c.relnamespace AND n.nspname = 'public'
   WHERE a.attacl IS NOT NULL;
  IF v <> 0 THEN RAISE EXCEPTION 'precondition failed: % explicit column ACLs exist; the rollback restores table-level grants only', v; END IF;

  SELECT count(*) INTO v FROM pg_namespace WHERE nspname = 'ops'
     AND nspowner <> (SELECT oid FROM pg_roles WHERE rolname = 'postgres');
  IF v <> 0 THEN RAISE EXCEPTION 'precondition failed: schema ops already exists and is not owned by postgres'; END IF;

  SELECT string_agg(DISTINCT ps, ' || ') INTO s FROM (
    SELECT string_agg(privilege_type, ',' ORDER BY privilege_type) AS ps
      FROM information_schema.role_table_grants
     WHERE table_schema = 'public' AND grantee IN ('anon','authenticated')
     GROUP BY table_name, grantee) x;
  IF s IS DISTINCT FROM 'DELETE,INSERT,REFERENCES,SELECT,TRIGGER,TRUNCATE,UPDATE'
    THEN RAISE EXCEPTION 'precondition failed: grant set is no longer uniform/expected, found: %', s; END IF;
END $$;

-- 1) Snapshot the exact pre-change state so rollback is driven by data, not prose.
CREATE SCHEMA IF NOT EXISTS ops;
REVOKE ALL ON SCHEMA ops FROM anon, authenticated;

CREATE TABLE ops.acl_snapshot_20260909 AS
SELECT g.grantee, g.table_schema, g.table_name, g.privilege_type, g.is_grantable,
       c.relkind, now() AS captured_at
  FROM information_schema.role_table_grants g
  JOIN pg_class c ON c.relname = g.table_name
  JOIN pg_namespace n ON n.oid = c.relnamespace AND n.nspname = 'public'
 WHERE g.table_schema = 'public' AND g.grantee IN ('anon','authenticated');
ALTER TABLE ops.acl_snapshot_20260909 ENABLE ROW LEVEL SECURITY;

-- pg_default_acl was missing from revision 2 — without it the default grant cannot
-- be restored to what it actually was (only to a guess).
CREATE TABLE ops.defacl_snapshot_20260909 AS
SELECT pg_get_userbyid(d.defaclrole) AS granting_role,
       n.nspname AS schema, d.defaclobjtype, d.defaclacl::text AS acl,
       now() AS captured_at
  FROM pg_default_acl d LEFT JOIN pg_namespace n ON n.oid = d.defaclnamespace;
ALTER TABLE ops.defacl_snapshot_20260909 ENABLE ROW LEVEL SECURITY;

-- 2) Future tables are no longer auto-granted to the public API roles.
ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public
  REVOKE ALL ON TABLES FROM anon, authenticated;

-- 3) Existing tables stop relying on RLS as a single point of failure.
--    (ALL TABLES also covers views and foreign tables; they hold no API-role
--     grants today — asserted above — so for them this is a no-op.)
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM anon, authenticated;

COMMIT;

-- Optional after COMMIT; refreshes PostgREST metadata. NOT the security boundary —
-- the database rejects the access immediately either way.
--   NOTIFY pgrst, 'reload schema';

-- =============================================================================
-- ROLLBACK — replays the snapshots. Restores what was revoked and nothing else.
--
--   NEVER use `GRANT ALL ON ALL TABLES IN SCHEMA public TO anon, authenticated;`
--   (revision 1's rollback). It would grant the 26 views privileges they have never
--   had, cover every table created after this migration, and on PostgreSQL 17 grant
--   MAINTAIN, which was not part of the original grant set.
--
--   Scope note: this restores the SURVIVING snapshotted objects and the measured
--   seven-privilege set. Tables created after the migration are intentionally not
--   granted. If a snapshotted table has since been dropped or renamed, the DO block
--   aborts the whole transaction — fix the snapshot rows first, then re-run.
--
-- BEGIN;
--   DO $$
--   DECLARE r record;
--   BEGIN
--     FOR r IN
--       SELECT grantee, table_name,
--              string_agg(privilege_type, ',') AS privs,
--              bool_or(is_grantable = 'YES')   AS grantable
--         FROM ops.acl_snapshot_20260909
--        WHERE relkind = 'r'
--        GROUP BY grantee, table_name
--     LOOP
--       EXECUTE format('GRANT %s ON public.%I TO %I%s',
--                      r.privs, r.table_name, r.grantee,
--                      CASE WHEN r.grantable THEN ' WITH GRANT OPTION' ELSE '' END);
--     END LOOP;
--   END $$;
--
--   -- exact privilege list, never ALL (arwdDxtm = these seven; no MAINTAIN)
--   ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public
--     GRANT DELETE, INSERT, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE
--     ON TABLES TO anon, authenticated;
-- COMMIT;
--
-- =============================================================================
-- VERIFY AFTER APPLYING — effective-privilege checks, not just direct grants.
--   A. No effective access left, over EVERY relation and EVERY verb:
--        SELECT count(*) FROM pg_class c
--          JOIN pg_namespace n ON n.oid=c.relnamespace AND n.nspname='public',
--          LATERAL unnest(ARRAY['SELECT','INSERT','UPDATE','DELETE','TRUNCATE',
--                               'REFERENCES','TRIGGER','MAINTAIN']) v(p),
--          LATERAL unnest(ARRAY['anon','authenticated']) q(role)
--         WHERE c.relkind IN ('r','v','m','p','f')
--           AND has_table_privilege(q.role, c.oid, v.p);          -- expect 0
--   B. The default ACL really lost the API roles:
--        SELECT defaclacl::text FROM pg_default_acl d
--          JOIN pg_namespace n ON n.oid=d.defaclnamespace AND n.nspname='public'
--         WHERE pg_get_userbyid(d.defaclrole)='postgres' AND d.defaclobjtype='r';
--        -- expect no anon= / authenticated= entry (supabase_admin's row still will)
--   C. Snapshots captured, not merely created:
--        SELECT count(*) FROM ops.acl_snapshot_20260909;            -- expect 1064
--        -- 76 tables x 2 roles x 7 privileges = 1064 rows. An earlier draft of this
--        -- file said 152; that was the count of (table, grantee) PAIRS, not of rows.
--        SELECT count(DISTINCT (table_name, grantee)) FROM ops.acl_snapshot_20260909; -- 152
--        SELECT count(*) FROM ops.defacl_snapshot_20260909;         -- expect > 0
--   D. Data API, several tables and more than one verb, with the publishable key:
--        GET  /rest/v1/pos_bills            -> 401 permission denied
--        GET  /rest/v1/vendors              -> 401
--        GET  /rest/v1/ai_chat_sessions     -> 401 (was reachable by policy design)
--        POST /rest/v1/manual_entries       -> 401 (write path, not just read)
--        GET  /rest/v1/v_daybook_pnl        -> 401 (unchanged control)
--   E. Applications still work — deep checks, not just liveness:
--        api.marastation.com/health/deep (exercises real DB/Supabase queries)
--        marastation.com 200 · app.marastation.com login page reachable
--   F. Sign in at app.marastation.com and load one data page (proves the Auth path
--      AND a service-role read, which /health alone does not).
--   G. get_advisors(security): no new ERROR-level finding.
--   H. Dashboard -> Settings -> API -> Exposed schemas: confirm `ops` is absent
--      (tidiness only — the API roles have no privilege on ops either way).
--   I. NOT COVERED BY THE ABOVE: every REST probe here used the publishable key, i.e.
--      the `anon` role. The evidence for `authenticated` is catalog-level only
--      (has_table_privilege = 0 for every relation and verb). A signed-in end-to-end
--      pass is what actually closes that gap — see F.
--
-- CONSUMERS OF THIS DATABASE (enumerate ALL of these before any future grant change;
-- the search scope was wrong twice in this task):
--   VEXONHQ · vexonhq-ocr-api · marastation-web · marastation-ai · "English Quest"
--   ("English Quest" points at this same project and uses SERVICE_ROLE only, verified
--    2026-09-09 — but it was found AFTER this migration was applied, not before.)
--   recycle-web uses a DIFFERENT Supabase project.
-- =============================================================================
