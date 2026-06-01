# Spec — AI Monitoring (ai_call_log) + OCR Golden-Set Harness

**Date:** 2026-06-01 · **Repo:** vexonhq-ocr-api · **Author:** Claude (for TUM)
**Origin:** AI Life-Cycle Audit (2026-05-31). Two weak phases:
- **Monitoring 3/5** — no AI quality/drift/cost tracking (`llm.py` says "no cost-tracking" by design).
- **Testing 2/5** — no AI-accuracy measurement (no ground-truth/eval set).

This reverses the deliberate "no cost-tracking" lean decision in `llm.py` — TUM approved.

---

## Feature 1 — Monitoring: `ai_call_log`

### Problem
Every AI call (OpenAI vision/text + Anthropic Haiku) runs blind: nobody can see token usage, cost, latency, or error-rate per feature. If a model's quality drops or spend balloons, there is no signal. `llm.py` centralises Anthropic but OpenAI is called at 7 sites with no common logging point.

### Design
1. **Table** `public.ai_call_log` (migration `2026_06_01_ai_call_log.sql`):
   `id bigserial pk, created_at timestamptz default now(), provider text, task text, model text, ok boolean, prompt_tokens int, completion_tokens int, total_tokens int, latency_ms int, status int, error text`.
   Indexes: `(created_at desc)`, `(task, created_at desc)`. **RLS enabled, no policy** (pitfall #26 — backend connects as service_role/BYPASSRLS so it still writes/reads; anon denied). Idempotent (`CREATE TABLE IF NOT EXISTS`), reversible (`DROP TABLE`).

2. **`llm.py`**:
   - `_log_ai_call(provider, task, model, ok, *, prompt_tokens, completion_tokens, total_tokens, latency_ms, status, error)` — best-effort INSERT; swallows ALL its own exceptions (logging must NEVER break an AI call — mirrors `cron_heartbeat.record_heartbeat`). DB conn via local `_log_conn()` = `try: from main import get_db_conn except: psycopg2.connect(DATABASE_URL)` (lazy import inside fn → no circular import; `llm.py` still never imports `main` at module load).
   - `call_anthropic` — wrap the request in timing; on success read `data["usage"]` (`input_tokens`/`output_tokens`) and log ok=True; on `LLMError`/exception log ok=False then re-raise. Behaviour otherwise unchanged.
   - New `openai_chat(task, *, model=None, messages, **kwargs) -> ChatCompletion` — calls the `get_openai()` singleton's `chat.completions.create(model=model or model_for(task), messages=messages, **kwargs)`, times it, reads `resp.usage`, logs, returns the **raw response unchanged** so call sites keep reading `resp.choices[0]...`. On error logs ok=False + re-raises.
   - `PRICES` map (USD per 1M tokens, env-overridable) + `estimate_cost_thb(model, prompt, completion)` — used by `/ai/stats`. Clearly an ESTIMATE; default rates dated in a comment; `USD_THB` env-overridable (default 36.5).

3. **Route all 7 OpenAI sites through `openai_chat`** (same model each → zero behaviour change, just adds logging):
   | file | task |
   |---|---|
   | `main.py` `_run_gpt_vision` | `vision_ocr` |
   | `slip_routes.py` | `slip_vision` |
   | `phase3a_ai_categorize_routes.py` | `categorize` |
   | `product_classifier.py` | `classify` |
   | `search_routes.py` | `search_openai` |
   | `line_bot_routes.py` (image classify) | `line_image_classify` |
   | `bill_payment_routes.py` `_call_gpt_vision_for_slip` (raw urllib) | `bill_slip_vision` — migrate to `openai_chat`, also closes audit **F10** |

   Each site passes its CURRENT model explicitly so the model used never changes; the task is only the log label.

4. **New router `ai_monitor_routes.py`** (registered in `main.py`, JWT-gated — NOT in PUBLIC_PATHS):
   - `GET /ai/stats?days=30` — aggregate per task: call count, ok/error counts, error-rate, total tokens, est. cost ฿. Plus a daily total series for drift-spotting.
   - `GET /ai/calls?limit=50` — most recent calls (task/model/ok/tokens/latency/error) for spot-checks.
   Both read-only. Add `/ai/stats` to `tests/test_smoke.py` authed-route list.

### Why this is safe
- Logging is best-effort and isolated; a logging failure never affects the AI result or the user request.
- AI call volume is low (hundreds/day max at ~660 bills/month), so one extra INSERT per call is negligible — same pattern as the existing per-cron heartbeat.
- `openai_chat` returns the unchanged response object → call sites are edited by 2 lines each (drop `client = get_openai()`, swap `client.chat.completions.create` → `openai_chat`), no downstream parsing changes.

---

## Feature 2 — Testing: OCR golden-set harness

### Problem
OCR accuracy (the money-data gateway) is never measured — testing is by eye. We agreed NOT to commit real financial documents to the repo, so we ship the *measurement capability* + a tested scorer + synthetic fixtures; the real accuracy number comes from running `--live` on real images kept outside the repo.

### Design
- **`tests/ocr_golden/scorer.py`** — pure scoring logic (no API/network at import):
  - `normalize_text` (strip/space-collapse), `nums_match(a,b,tol=0.01)`.
  - `score_case(expected, actual) -> {fields:{name:bool...}, items:{precision,recall,f1}, field_accuracy, overall}`. Scalar fields: vendor_name, invoice_no, bill_date, merchant_tax_id (text exact-after-normalize); amount, subtotal, vat (numeric tolerance). Items matched on (normalized name + qty + total).
  - `aggregate(results)` → mean field accuracy + per-field hit-rate across cases.
- **`tests/ocr_golden/cases/*.json`** — synthetic cases `{name, expected:{...}, sample_actual:{...}}`. `expected` = ground truth; `sample_actual` = a simulated model output (some right, some wrong) so the offline test exercises both match + mismatch paths. 100% synthetic (fictional vendors/amounts).
- **`tests/test_ocr_golden.py`** — pytest: loads cases, asserts `score_case(expected, sample_actual)` yields the documented accuracy (so the harness is itself verified). Runs in `verify.ps1` compileall + offline pytest; needs NO API key, NO network.
- **Live mode**: `python -m tests.ocr_golden.scorer --live <image> <expected.json>` — calls `main._run_gpt_vision` on a real image (kept outside the repo) and prints field-level accuracy. Documented in `tests/ocr_golden/README.md`. Not run in CI.

### Out of scope
Backtest of order-advice (F8), narrative number-check (F7), per-field OCR confidence (F6) — separate follow-ups.

---

## Files
- NEW `migrations/2026_06_01_ai_call_log.sql`
- EDIT `llm.py` (logging + `openai_chat` + price map)
- EDIT `main.py`, `slip_routes.py`, `phase3a_ai_categorize_routes.py`, `product_classifier.py`, `search_routes.py`, `line_bot_routes.py`, `bill_payment_routes.py` (route OpenAI through `openai_chat`)
- NEW `ai_monitor_routes.py` + register in `main.py` + smoke entry
- NEW `tests/ocr_golden/{scorer.py,__init__.py,README.md,cases/*.json}` + `tests/test_ocr_golden.py`
- docs: DAILY_LOG, CHANGELOG, AGENTS.md pitfall

## Test plan
`python -c ast.parse` per file → `pytest tests/test_ocr_golden.py -v` → `.\verify.ps1` (compileall) → after TUM deploys + applies migration: `.\verify.ps1 -Smoke` + `GET /ai/stats` returns 200 with data.

## Coordination-zone note
`line_bot_routes.py` + `bill_payment_routes.py` are in the AGENTS "ask first / coordination zone" list. TUM approved touching all OpenAI sites. Edits are minimal (call-routing only).
