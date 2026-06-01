-- Security: pricing_channels was created without RLS (gap vs Session-49 hardening,
-- where every public table is RLS-on + no policy; backend uses service_role/BYPASSRLS).
-- Enabling RLS only RESTRICTS anon access — backend (psycopg2/service_role) is unaffected,
-- and the frontend reaches this table only via backend endpoints, never the anon client.
ALTER TABLE public.pricing_channels ENABLE ROW LEVEL SECURITY;
