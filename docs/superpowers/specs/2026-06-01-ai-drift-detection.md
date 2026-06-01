# Spec — AI Model-Drift Detection + Alert (audit roadmap, final item)

**Date:** 2026-06-01 · **Repo:** vexonhq-ocr-api · **Author:** Claude (for TUM)
**Origin:** AI Life-Cycle Audit roadmap (ระยะยาว) — the last code item. Builds on `ai_call_log` (Session 51). Design adversarially reviewed (false-positive / statistics / infra-fit). TUM approved the **conservative rollout** (ping only on real errors; cost/slow-drift digest-only; dry-run until armed).

## Scope (NARROW v1)
A **quality/cost drift watcher**, explicitly NOT an outage detector (outages stay owned by `/health/deep` + Uptime Robot + `auto_diagnose`). The Discord push channel carries **exactly one signal**: a per-task persistent-error-rate regression. Everything else (latency, token/cost profile, model change, volume collapse) is **digest-only** (`GET /ai/drift` + one folded line in the Monday `weekly_summary`).

## Detection core (pure `evaluate_drift`)
- **RECENT** = trailing 7 complete Bangkok days. **BASELINE** = the 21 complete days *before* that (non-overlapping; `BASELINE_OFFSET=7`, `BASELINE_DAYS=21`, `RECENT_DAYS=7`). Each task judged only vs its OWN history.
- **Persistent error** = `ok=false AND status IN (400,401,403,404,422)`. Transient (`429,500,502,503,504,529, NULL`) excluded from numerator+denominator (a provider 5xx/529 storm can never page; expired key 401 / broken prompt 400/422 still does). `llm.py` records upstream status → this split is real.
- **Cold-start lockout:** oldest `ai_call_log` row must be ≥ `COLD_START_DAYS=28` old before ANY paging path is reachable. Migration dated 2026-06-01 → provably zero pings until ~2026-06-29.
- **Armed flag:** live posting gated behind env `AI_DRIFT_ALERTS_ARMED` (default `"0"`). Until armed: compute + write state + record heartbeat, but never call `_post_to_discord`. Dry-run is the default state.

## Rules
| rule | fires | min samples | severity |
|---|---|---|---|
| **ERROR_RATE_REGRESSION** | two-proportion lower bound of (p_rec−p_base) > 0 (95% → WARN/digest; 99% → CRIT/ping) AND lift ≥ 10pp; CRIT also needs persistence across 2 runs. er_base==0 → rule-of-three: p_rec ≥ max(0.10, 3/n_base). | n_rec≥30 AND k_rec≥5 AND n_base≥100 | WARN digest / CRIT ping |
| **ERROR_FLOOR** | persistent error-rate over RECENT ≥ 0.40 (baseline-independent; no persistence wait — broken is broken) | n_rec≥30 AND k_rec≥10 | CRIT ping |
| **LATENCY_DRIFT** | p50 (percentile_cont, ok-only) rec ≥ 1.75×base AND Δ ≥ 1500ms | n_rec≥20 ok | INFO digest only |
| **TOKEN_PROFILE** | avg total_tokens/OK-call ≥ 2.0×base (labelled "อาจมาจากบิลยาวขึ้น") | base ≥ 20 ok | INFO digest only |
| **MODEL_CHANGE** | dominant model differs rec vs base → INFO + **suppress** that task's latency/token/cost findings for a grace window (covers pending AI-consolidation swaps) | dominant differs | INFO + suppressor |
| **VOLUME_COLLAPSE** | base ≥ 5 calls/day AND recent == 0 | base avg ≥ 5/day | INFO digest only |
| **OUTAGE-DEDUP GUARD** | transient-error-rate over RECENT ≥ 0.50 → suppress ALL pings for that task ("owned by /health/deep") | n_rec≥30 | suppressor |

Cost (฿) computed via `llm.estimate_cost_thb` (matches `/ai/stats`) shown in digest, **no cost ping in v1**.

## False-positive design (the point)
Transient/persistent split · two-proportion bound (n=4 can't fire) · 28-day cold-start · armed flag · min-sample gates (sparse tasks silent by construction; collapses ~60 tests/run to the 2-4 busy tasks) · persistence-before-ping · Postgres dedup with 7-day cooldown + resolved message · model-change suppression · outage-dedup guard · robust p50 latency · **silence on clean** (no daily "all good"). One `THRESHOLDS` dict at top for retuning.

## Schedule + heartbeat + dedup
- Daily cron **08:30 Asia/Bangkok** — 5th `add_job` sibling in `line_bot_routes.py` `_scheduler` (after 06:00/before 09:00, no co-firing minute). `replace_existing=True` → clean re-register on redeploy (cron fires on wall-clock, not registration; no double-fire).
- `@_heartbeat('daily_ai_drift_check', expected_interval_hours=24)`, **re-raise on DB/logic error** (AGENTS #28). `_post_to_discord` + state-write are best-effort try/except (Discord outage doesn't fail the job).
- **Dedup table** `public.ai_drift_state(finding_key pk, severity, first_seen_at, last_posted_at, last_value, updated_at)`, `finding_key='task:rule'`. Posts only if NEW / ESCALATED / `last_posted_at` older than `DEDUP_COOLDOWN_DAYS=7`. Resolved key → one "กลับเป็นปกติ" line. Postgres-backed (survives restart-on-deploy); NEVER an in-process dict, NEVER `job_heartbeat.last_error_message`.

## Architecture (testability)
`run_drift_check(dry_run, post)` does the two `ai_call_log` GROUP BY queries (with `percentile_cont` p50) + reads/writes `ai_drift_state`, then calls **pure** `evaluate_drift(recent_rows, baseline_rows, oldest_row_age_days, prev_state, now) -> list[Finding]` (the dedup/escalation/persistence decision lives in the pure fn using `prev_state` → unit-testable with synthetic dicts, zero DB/API-key). `render_discord_message(findings)` / `render_digest_line(findings)`.

## Files
- NEW `drift_monitor.py`
- NEW `migrations/2026_06_02_ai_drift_state.sql` (RLS on, no policy; idempotent + reversible)
- EDIT `line_bot_routes.py` (job + heartbeat fn + weekly_summary digest line)
- EDIT `ai_monitor_routes.py` (`GET /ai/drift`, JWT, dry_run default; `?post=true` real run)
- EDIT `tests/test_smoke.py` (+ `/ai/drift`)
- NEW `tests/test_drift_monitor.py` (offline)
- EDIT `AGENTS.md`

## Test plan
Pure tests over `evaluate_drift` with synthetic dict rows: cold-start suppresses; sparse n=4 → insufficient_data; transient 529 vs persistent 401 split; two-proportion pass/fail; persistence WARN→CRIT→dedup→cooldown; ERROR_FLOOR independent of poisoned baseline; zero-baseline rule-of-three; model-change suppression; outage-dedup; latency/token never ping; render produces ≤1900-char message, empty on clean day; wrapper smoke (monkeypatch DB+`_post_to_discord`): dry_run never posts/writes, logic error re-raises, Discord raise swallowed. Then `.\verify.ps1` + once-live `GET /ai/drift?post=false` → `warming_up=true` today.

## Env
`AI_DRIFT_ALERTS_ARMED` (default off) — set to `1` in Coolify only after a clean shadow week. (No new dep — Wilson/Newcombe bound is ~15 lines of stdlib math.)
