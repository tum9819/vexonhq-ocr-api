-- 2026_06_01_ai_call_log.sql
-- AI call telemetry (Monitoring-phase remediation from the 2026-05-31 AI audit).
-- Every OpenAI + Anthropic call writes one best-effort row here so /ai/stats can
-- report per-feature token usage, estimated cost, latency, and error-rate, and so
-- model drift (a rising error-rate or shifting token profile) becomes visible.
--
-- Reverses the original "no cost-tracking" lean decision in llm.py (TUM approved).
--
-- Security: RLS ENABLED with NO policy (pitfall #26). The backend connects as the
-- service_role / postgres role (BYPASSRLS), so it can still INSERT/SELECT; the
-- public anon key is denied (this table records prompts/usage and must not leak).
--
-- Idempotent (IF NOT EXISTS). Reversible: DROP TABLE public.ai_call_log;

CREATE TABLE IF NOT EXISTS public.ai_call_log (
    id                bigserial PRIMARY KEY,
    created_at        timestamptz NOT NULL DEFAULT now(),
    provider          text        NOT NULL,           -- 'openai' | 'anthropic'
    task              text        NOT NULL,           -- MODELS key, e.g. 'vision_ocr'
    model             text        NOT NULL,
    ok                boolean      NOT NULL,
    prompt_tokens     integer,
    completion_tokens integer,
    total_tokens      integer,
    latency_ms        integer,
    status            integer,                          -- upstream HTTP status when known
    error             text                              -- truncated error detail (<=500)
);

CREATE INDEX IF NOT EXISTS ix_ai_call_log_created_at
    ON public.ai_call_log (created_at DESC);
CREATE INDEX IF NOT EXISTS ix_ai_call_log_task_created
    ON public.ai_call_log (task, created_at DESC);

ALTER TABLE public.ai_call_log ENABLE ROW LEVEL SECURITY;
-- No policy on purpose: anon/authenticated get zero rows; service_role bypasses RLS.

COMMENT ON TABLE public.ai_call_log IS
    'Per-call AI telemetry (provider/task/model/tokens/latency/ok). Written best-effort by llm.py; read by /ai/stats + /ai/calls. RLS on, no policy (backend = service_role/BYPASSRLS). Added 2026-06-01, audit Monitoring remediation.';
