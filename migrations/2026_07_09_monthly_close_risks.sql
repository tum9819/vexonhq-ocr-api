-- 2026_07_09_monthly_close_risks.sql
-- Monthly Close Risk Marking V1 (spec: docs/superpowers/specs/2026-07-09-monthly-close-risk-marking-v1-design.md).
--
-- One row per (branch_code, month, risk_key) — a risk TYPE, not one row per affected
-- transaction. Affected ids/counts/sums/examples live in `evidence` (each list capped
-- at 10 items by the writer). Lets the checker remember open/resolved state and throttle
-- LINE to at most once per 24h per critical risk (last_line_sent_at).
--
-- Read-only detection: the checker NEVER mutates bank_statement_entries / pos_bills /
-- pos_imports. It only writes this table.
--
-- Security: RLS ENABLED with NO policy (pitfall #26, same pattern as ai_call_log). The
-- backend connects as service_role/postgres (BYPASSRLS) so it can INSERT/SELECT; the
-- public anon key is denied.
--
-- ignored_at / ignored_by columns are provisioned for a later owner-approved ignore
-- workflow. V1 does NOT implement any ignore endpoint or UI.
--
-- Idempotent (IF NOT EXISTS). Reversible: DROP TABLE public.monthly_close_risks;

CREATE TABLE IF NOT EXISTS public.monthly_close_risks (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    branch_code       text        NOT NULL,
    month             text        NOT NULL,                 -- 'YYYY-MM'
    risk_key          text        NOT NULL,                 -- one of the V1 risk_key literals
    severity          text        NOT NULL CHECK (severity IN ('danger', 'warning', 'info')),
    status            text        NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'resolved', 'ignored')),
    title             text        NOT NULL,
    message           text        NOT NULL,
    amount            numeric     NOT NULL DEFAULT 0,
    evidence          jsonb       NOT NULL DEFAULT '{}'::jsonb,
    link              text        NOT NULL,
    first_seen_at     timestamptz NOT NULL DEFAULT now(),
    last_seen_at      timestamptz NOT NULL DEFAULT now(),
    resolved_at       timestamptz NULL,
    resolved_by       text        NULL,
    ignored_at        timestamptz NULL,                     -- reserved for later ignore workflow (not used in V1)
    ignored_by        text        NULL,                     -- reserved for later ignore workflow (not used in V1)
    last_line_sent_at timestamptz NULL,
    created_at        timestamptz NOT NULL DEFAULT now(),
    updated_at        timestamptz NOT NULL DEFAULT now(),
    UNIQUE (branch_code, month, risk_key)
);

CREATE INDEX IF NOT EXISTS ix_monthly_close_risks_branch_month
    ON public.monthly_close_risks (branch_code, month);
CREATE INDEX IF NOT EXISTS ix_monthly_close_risks_status
    ON public.monthly_close_risks (status);

ALTER TABLE public.monthly_close_risks ENABLE ROW LEVEL SECURITY;
-- No policy on purpose: anon/authenticated get zero rows; service_role bypasses RLS.

COMMENT ON TABLE public.monthly_close_risks IS
    'Monthly-close risk marker V1. One row per (branch_code, month, risk_key); evidence holds ids/counts/sums (capped 10). Read-only detection — checker writes ONLY this table. RLS on, no policy (backend = service_role/BYPASSRLS). Added 2026-07-09.';
