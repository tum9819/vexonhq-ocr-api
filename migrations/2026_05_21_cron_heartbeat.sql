-- ════════════════════════════════════════════════════════════════════════════
-- 2026-05-21 — Scheduled-job heartbeat table (P1.2)
-- ════════════════════════════════════════════════════════════════════════════
--
-- THE PROBLEM
-- -----------
-- VEXONHQ runs 4 cron-style scheduled jobs inside the FastAPI process
-- (APScheduler in line_bot_routes.py):
--   • daily_line_digest      — daily 06:00
--   • daily_ap_due_reminder  — daily 09:00
--   • daily_budget_alert     — daily 20:00
--   • weekly_summary         — Monday 08:00
--
-- If APScheduler crashes silently, or the FastAPI worker restarts and
-- the scheduler doesn't get re-registered, these jobs go quiet and no
-- one notices until TUM realises he hasn't received the morning digest
-- for a week. That's a stability blind spot.
--
-- THE FIX
-- -------
-- Each scheduled job calls record_heartbeat(job_id, ok, error?) on
-- entry and exit. The job_heartbeat table stores the latest run state
-- per job. /cron/health reads this table and flags any job whose
-- last_run is more than 2× its expected interval (e.g. a daily job
-- not seen in 48h → stale).
--
-- Uptime Robot or Discord ops can poll /cron/health and alert when a
-- job goes stale, even when the rest of the API is responsive.
--
-- Idempotent: CREATE TABLE IF NOT EXISTS — safe to re-run.
-- ════════════════════════════════════════════════════════════════════════════

BEGIN;

CREATE TABLE IF NOT EXISTS public.job_heartbeat (
    job_id                  TEXT PRIMARY KEY,
    last_run_at             TIMESTAMPTZ NOT NULL,
    last_success_at         TIMESTAMPTZ,
    last_error_at           TIMESTAMPTZ,
    last_error_message      TEXT,
    expected_interval_hours INT NOT NULL DEFAULT 24,
    run_count               BIGINT NOT NULL DEFAULT 0,
    error_count             BIGINT NOT NULL DEFAULT 0,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Seed the four known jobs so /cron/health surfaces them even before
-- their first run after deploy. expected_interval_hours = how long
-- between runs (24 for daily, 168 for weekly). Stale threshold = 2×.
INSERT INTO public.job_heartbeat (job_id, last_run_at, expected_interval_hours)
VALUES
    ('daily_line_digest',     NOW(), 24),
    ('daily_ap_due_reminder', NOW(), 24),
    ('daily_budget_alert',    NOW(), 24),
    ('weekly_summary',        NOW(), 168)
ON CONFLICT (job_id) DO UPDATE
SET expected_interval_hours = EXCLUDED.expected_interval_hours;

COMMIT;
