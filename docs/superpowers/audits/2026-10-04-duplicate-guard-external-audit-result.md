# External audit result — 2026-10-04 duplicate-bill guard

Request: `2026-10-04-duplicate-guard-review-request.md` (round 1, pinned
Repo A `cab8fde` / Repo B `6c6599f`) and `…-review-request-round2.md`
(round 2, pinned `7ec5b91` / `58972be`).

## Round 1 (read-only; no files edited; DB background claims NOT VERIFIED)

| # | Item | Verdict | Author verification → disposition |
|---|------|---------|-----------------------------------|
| 1 | Duplicates endpoint edge cases | FAIL | Confirmed: `float("Infinity")` / `"1e309"` → inf passes `amount <= 0`; Starlette `JSONResponse.render` uses `allow_nan=False` (`.venv/.../starlette/responses.py:194`) → 500. Pre-existing in the old key; production has 0 non-finite amounts (`numeric` column). **Fixed `7ec5b91`** (`math.isfinite` guard) — new test fails on `cab8fde` (`assert 1 == 0`), passes after. |
| 2 | Consumers tolerate joined/null vendor names | PASS | — |
| 3 | Advisory only | PASS | — |
| 4 | LINE success/exception behaviour preserved | PASS | — |
| 5 | `_find_uploaded_file` semantics (fail-open, rejected bill still matches, orphan/partial → reprocess) | PASS | Rejected-bill match recorded as known behaviour (same as web). |
| 6 | Hash wiring; no hash when storage upload fails | PASS | Known limitation, not a regression. |
| 7 | Reply token safe; **dedup not atomic** under two concurrent identical images | FAIL | **Declined, no code change:** same non-atomic check as the web path since #59 (index non-unique by design — PDF pages share a hash); needs two byte-identical images processed at the same moment; the resulting pair has the same amount + date and is flagged by the widened banner. Lean-bar: a lock/unique reservation costs more than the rare case. |
| 8 | Tests exercise real code; gates | PASS | Reviewer ran 656 passed / 2 skipped excluding `test_workflow.py` (live). |
| 9 | Banner text accurate | FAIL | Confirmed: undated bills (5 of 123 in production) still need the same vendor. **Fixed `58972be`**. |
| Low | Stale API-contract comment `lib/invoice-api.ts:258` | — | **Fixed `58972be`**. |
| Low | Missing edge-case tests | — | **Added in `7ec5b91`** (negative, invalid, non-finite, empty-string date, whitespace vendor). |

Author note while fixing: the editor normalised `lib/invoice-api.ts` (mixed
CRLF lines 1–672 / LF after) to CRLF everywhere, producing a ~130-line
phantom diff. Rebuilt the file from `HEAD` bytes and swapped only the comment;
committed diff = 8 lines. Earlier commits checked with
`git diff --ignore-cr-at-eol --stat` — identical, no phantom churn.

Gates after fixes: backend hook-equivalent `pytest tests/ --ignore=test_smoke
--ignore=test_backup_prune` 672 passed / 26 skipped; compileall 0; targeted
15 passed. Frontend lint 0 errors / 14 pre-existing warnings, `tsc` 0, build
70/70 + `ƒ Proxy (Middleware)`.

## Round 2 (different reviewer — Antigravity; pinned Repo A `7ec5b91`, Repo B `58972be`; read-only)

**9/9 PASS, 0 additional defects.** Gates re-run by the reviewer: targeted 15
passed; offline suite excluding `test_workflow.py` 659 passed / 2 skipped
(consistent with 672 including the live workflow tests); frontend lint 0
errors / 14 pre-existing warnings, `tsc` 0, build 70/70 + `ƒ Proxy`.

Author spot-check (per the "demand evidence" rule): 17 cited lines checked
against the pinned code — all exact (`main.py:1136/1138/1145/1149/1151/1152/
3880/3887`, `line_bot_routes.py:280/289/290/291/2135/2153/2156`,
`InvoiceQueueGrid.tsx:407/412`). The reviewer did not challenge the declined
round-1 item 7 (concurrent identical LINE images); it stays a documented
known limitation.

**Overall: 2 rounds, 18 item checks + 2 Low notes; 3 defects fixed
(`7ec5b91`, `58972be`), 1 declined with reasons, 0 open.**
