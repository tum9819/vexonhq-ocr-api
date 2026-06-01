-- 2026_06_02_ai_drift_state.sql
-- Dedup/persistence state for the AI drift watcher (drift_monitor.py).
-- One row per finding_key = '<task>:<rule>'. The daily job posts a finding only
-- if it's NEW, ESCALATED in severity, or last_posted_at is older than the 7-day
-- cooldown — so a week-long unfixed issue pings at most once a week, not daily.
--
-- WHY a dedicated table (not job_heartbeat / not an in-process dict): Coolify
-- redeploys re-import every module, so an in-process dict would forget state on
-- each deploy and re-alert. job_heartbeat.last_error_message is surfaced verbatim
-- by /cron/health and clobbered by the @heartbeat decorator every run, so it
-- can't hold this. Postgres-backed state survives restarts and is private.
--
-- Security: RLS ENABLED, no policy (pitfall #26) — backend connects as
-- service_role/postgres (BYPASSRLS) so it reads/writes; anon is denied.
-- Idempotent (IF NOT EXISTS). Reversible: DROP TABLE public.ai_drift_state;

CREATE TABLE IF NOT EXISTS public.ai_drift_state (
    finding_key    text PRIMARY KEY,                 -- '<task>:<rule>'
    severity       text        NOT NULL,             -- 'INFO' | 'WARN' | 'CRIT'
    first_seen_at  timestamptz NOT NULL DEFAULT now(),
    last_posted_at timestamptz,                       -- NULL until it actually pinged
    last_value     numeric,                           -- last observed metric (rate/ms/tokens)
    updated_at     timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE public.ai_drift_state ENABLE ROW LEVEL SECURITY;
-- No policy on purpose: anon/authenticated get zero rows; service_role bypasses RLS.

COMMENT ON TABLE public.ai_drift_state IS
    'Dedup/persistence state for the AI drift watcher (drift_monitor.py). finding_key=task:rule; posts only when NEW/ESCALATED/older-than-cooldown. RLS on, no policy. Added 2026-06-02.';
