# External audit result — 2026-09-12 — legacy JWT fail-closed + OCR Phase A

Request: `2026-09-12-jwt-failclosed-phase-a-review-request.md` (audit-only prompt).
Reviewer: external AI reviewer chosen by TUM (round 1). Verbatim report pasted back
by TUM; Claude verified the one defect against the code before acting on it.

## Verdict

All 11 numbered items **PASS** with `file:line` evidence. Reviewer re-ran the
gates itself: targeted pytest 17 passed; `verify.ps1` compileall + 630 passed /
2 skipped ("READY: safe to push"); frontend `tsc --noEmit` 0 errors; `npm run
lint` 0 errors (14 pre-existing warnings in unrelated files, 0 in
`InvoiceUploadFlow.tsx`).

Highlights the reviewer confirmed (not just the author's claims):

- Path 2 (`verify_token`) returns `None` before any `jwt.decode` when
  `LEGACY_AUTH_ENABLED` is false; `POST /auth/login` raises 503 before rate
  limiting, credential lookup or `create_token`; every `from auth_routes import`
  site (14 files) reaches auth only through `verify_token`/`_require_admin_role`.
- Path 1 (Supabase SSO) block has 0 changed lines between `origin/main` and
  `main`, including the `ExpiredSignatureError` early-return.
- No caller of `POST /auth/login` in either repo besides `tests/test_workflow.py`;
  the dashboard signs in with `supabase.auth.signInWithPassword`
  (`app/login/actions.ts:23`).
- APScheduler jobs, LINE webhook (HMAC), Discord interactions (Ed25519) and
  `/cron/health` do not use JWT — nothing else depended on the default secret.
- `_ocr_page` gating: multi-page PDF → no page retry; 1-page PDF and single
  image → retry kept; `OCR_PAGE_RETRY_MULTIPAGE` truthy → old behaviour;
  `_revalidate_bill(bill_level=True)` still raises `ITEMS_TOTAL_INCOMPLETE`
  (`main.py:3315-3335`) and `confirm_invoice` blocks on error-level warnings.
- No migration, DB write, or response-shape change in the three commits.
- Frontend queues: `processItem` never calls `setShowingModal`; X leaves the bill
  `pending_review` and the effect dequeues the next one; `activeIds` +
  functional `setReviewQueue` prevent double-queueing; `try/finally` resets
  `uploadRunning` so a 4xx/5xx/524 cannot stall the network queue.

## Defects reported

| # | Severity | Location | Finding | Action |
|---|---|---|---|---|
| 1 | Low | `scripts/repair_incomplete_bills.py:84-85` | Script did not `strip()` `JWT_SECRET` before its fail-closed check, so a whitespace-only secret would pass the guard, sign with the blank value and fail later with HTTP 401 instead of the intended fast-exit message. | **Fixed** in `b3dec06`: check `secret.strip()`, still sign with the raw value exactly as the app verifies it. Offline check of the guard: 4/4 cases (blank, whitespace, old literal → exit; real value → token). |

No Critical / Important findings. Nothing was left open from this round.
