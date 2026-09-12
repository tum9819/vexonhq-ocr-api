# REVIEW REQUEST — AUDIT ONLY (2026-09-12) — VEXONHQ backend + frontend

## Your role and hard rules
- You are an **independent auditor**. **Do NOT edit, patch, refactor, or "fix" any file. Do NOT commit, stage, stash, checkout, or push.** Read-only.
- Report **facts you verified in the code**, with `file:line` citations for EVERY claim. If you cannot verify a claim, say "not verified" — do not guess, do not assume, do not infer behavior you did not read.
- A verdict of "PASS" without line-number evidence is invalid. Correctness is the only goal; 100% accuracy over speed.
- Do not run anything that calls OpenAI, LINE, Supabase, or production. Offline reading + local `pytest` (offline files listed below) only.

## Scope (3 backend commits + 2 frontend commits, all local, NOT pushed)

### Repo A — `C:\Users\rapee\vexonhq-ocr-api` (FastAPI backend), branch `main`, range `origin/main..main`
1. `a5dc380` — OCR Phase A: `main.py` (`_process_upload` L879-895 passes `allow_corrective_retry` into `_ocr_page` at L889; `_ocr_page` L918+ new keyword-only arg gates the ONE corrective vision retry), `line_bot_routes.py` (`_classify_image_type` L296-330 sends explicit `image_url.detail`, default `low`), tests, `.env.example`, docs, `tools/ocr_local_bench/` (offline prototype only, not imported by the app).
2. `131d707` — **Security fix, the important one.** `auth_routes.py`: `JWT_SECRET` no longer has an in-code default (L60-84: `_INSECURE_DEFAULT_JWT_SECRET`, `_legacy_auth_enabled()` L68, `LEGACY_AUTH_ENABLED` L74); `verify_token` Path 2 (L300-301) returns `None` when disabled; `POST /auth/login` (L361-365) raises 503 when disabled; default `VEXON_USER`/`VEXON_HASH` removed (L96-97). `scripts/repair_incomplete_bills.py` `mint_admin_token()` mirrors the rule. New `tests/test_legacy_auth_failclosed.py`. `AGENTS.md` pitfall #75.
3. `84e9d37` — self-review follow-ups: `.strip()` + short-secret warning in `auth_routes.py`; `OCR_PAGE_RETRY_MULTIPAGE` accepts `1/true/yes/on` (`main.py` L883-887); LINE detail rollback documented as `auto`; extra tests; `tests/test_workflow.py` docstring note.

Background for #2 (verified live before the fix, read-only): a token signed with the old default literal, `role=staff`, was accepted by `GET /auth/me` on production (HTTP 200). Path 2 copies `role` straight from the payload (`auth_routes.py:310`), so `role=admin` would pass `_require_admin_role` (`auth_routes.py:450`) on every admin-gated endpoint.

### Repo B — `C:\Users\rapee\VEXONHQ` (Next.js dashboard), branch `main`, range `origin/main..main`
- `d34db72` + `32eb068` — `components/invoice/InvoiceUploadFlow.tsx` only: two independent queues — network queue uploads ONE file at a time (`uploadQueue`/`runUploadQueue`), review queue shows the confirmation modal one bill at a time via a `useEffect` on `[showingModal, reviewQueue, items]`. Invariants the author claims: a finishing upload never calls `setShowingModal` directly; closing the modal with X leaves the bill `pending_review` and never blocks the queue; a file is never queued twice.
- `494ff16` — docs only (skip).

## What to audit — answer each with evidence

**A. Legacy JWT fail-closed (`131d707` + `84e9d37`)**
1. With `JWT_SECRET` unset, or equal to the old literal, or whitespace: is there ANY code path left that verifies or signs a self-issued HS256 token? Check `verify_token` Path 2, `create_token`, `login`, `/auth/me`, `/auth/page-config`, `_require_admin_role`, and every `from auth_routes import` site (`bill_payment_routes.py`, `export_routes.py`, `line_bot_routes.py`, `monthly_close_routes.py`, `phase12_bank_statement_routes.py`, `phase2_routes.py`, `phase3a_ai_categorize_routes.py`, `phase3a_anomaly_routes.py`, `stock_in_routes.py`, `main.py` middleware).
2. Supabase SSO tokens (Path 1, ES256 via JWKS; HS256 via `SUPABASE_JWT_SECRET`) — confirm behavior is byte-for-byte unchanged, including the `ExpiredSignatureError` early-return and the generic-exception fall-through.
3. Any remaining caller of `POST /auth/login` in EITHER repo besides `tests/test_workflow.py` (expected: none; the dashboard only references the path in `components/AuthProvider.tsx:99` to exclude it from its 401 handler).
4. `scripts/repair_incomplete_bills.py`: confirm it cannot sign with a fallback secret any more and that `sys` is imported for `sys.exit`.
5. `tests/test_legacy_auth_failclosed.py`: does it actually exercise the production code path (not a mock of it)? Note it monkeypatches module attributes instead of `importlib.reload` — is anything under test bypassed as a result?
6. Anything the change BREAKS that the author missed (e.g. a job, cron, LINE/Discord handler, or script that relied on the default secret or the `vexonhq` default account).

**B. OCR Phase A backend (`a5dc380` + `84e9d37`)**
7. `_ocr_page(..., allow_corrective_retry=...)`: confirm both callers (`main.py:889` and `main.py:1078`) pass/omit it correctly so that: multi-page PDF → no page-level retry; 1-page PDF and single image → retry kept; `OCR_PAGE_RETRY_MULTIPAGE` truthy → old behavior. Confirm `_revalidate_bill` still raises `ITEMS_TOTAL_INCOMPLETE` at bill level so nothing is silently lost.
8. `line_bot_routes._classify_image_type`: confirm the `detail` value is validated (`low|high|auto`) and that an invalid env value falls back to `low` with a warning, not an exception. Confirm the call still routes through `llm.openai_chat` (telemetry).
9. Any API response shape, DB write, or migration change hidden in these commits (author claims none).

**C. Frontend queues (`d34db72`, `32eb068`)**
10. Verify the three invariants above against the actual code. Look specifically for: stale-closure over `items`/`reviewQueue` inside `runUploadQueue`/`processItem`; the `useEffect` dequeue racing with a manual card click (`setShowingModal` from the card path); a bill removed while in flight (`removedIds`/`activeIds`) still landing in the review queue; unmounted-component `setState`; a rejected upload (4xx/5xx/524) stalling the network queue forever (`uploadRunning` never reset).
11. TypeScript/ESLint cleanliness is claimed; do not re-run the build unless you can do so offline.

## Expected local evidence (offline, for your own confirmation)
- Backend: `cd C:\Users\rapee\vexonhq-ocr-api; .\.venv\Scripts\python.exe -m pytest tests/test_legacy_auth_failclosed.py tests/test_admin_gate.py tests/test_ocr_parallel_pages.py tests/test_line_image_classifier.py -q` → 17 passed. Full offline gate `.erify.ps1` (compileall + `pytest tests/ --ignore=tests/test_smoke.py --ignore=tests/test_backup_prune.py`) → 630 passed / 2 skipped, READY.
- Do NOT run `tests/test_smoke.py` or `tests/test_workflow.py` (they hit production).

## Output format
For each numbered item 1–11: `PASS` / `FAIL` / `NOT VERIFIED`, followed by the `file:line` evidence and, for FAIL, the concrete failure scenario (input → wrong behavior). Then a final list of any additional defects found, ranked by severity, each with `file:line`. No suggestions to rewrite; no style comments; no edits.
