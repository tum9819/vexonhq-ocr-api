# REVIEW REQUEST — AUDIT ONLY (2026-10-01) — VEXONHQ backend + frontend

## Your role and hard rules
- You are an **independent auditor**. **Do NOT edit, patch, refactor, format, or "fix" any file. Do NOT commit, stage, stash, checkout, reset, install, or push.** Read-only.
- Report **only facts you verified in the code or in command output you actually ran**, with `file:line` citations for EVERY claim. If you cannot verify something, write `NOT VERIFIED` — do not guess, do not assume, do not infer behavior you did not read.
- A `PASS` without line-number evidence is invalid. Correctness is the only goal; 100% accuracy over speed.
- Do not run anything that calls OpenAI, LINE, Supabase, Coolify, or production. Offline reading + the local commands listed below only.
- **Pin check first:** confirm `git rev-parse --short HEAD` is `c28c86f` in Repo A and `8451f26` in Repo B. If not, STOP and report the mismatch — you are looking at the wrong version.

## Scope (all commits are local, NOT pushed)

### Repo A — `C:\Users\rapee\vexonhq-ocr-api` (FastAPI backend), branch `main`, range `origin/main..main`
- `08a7f55` — docs only (AGENTS.md #75 status line). Skip.
- `539c2af` — **store-context admin gate.** `store_context_routes.py`: removed the username allowlist `_ADMIN_USERS = {"vexonhq","Tum","tum","May","Toon","Oil"}`; `_require_admin` (L144-150) now raises 403 unless `request.state.role == "admin"` (L148). Callers unchanged: create L242/L244, patch L273/L275, delete L308/L310, reload L330/L333. Reads (`GET ""` L178, `GET /{key}` L215) are untouched. New test `tests/test_store_context_admin_gate.py`.
- `c28c86f` — `auth_routes.py` L104-108: default of `VEXON_ADMINS` changed from `"tum,vexonhq"` to `"tum"` (comment L100-103). New test `test_removed_default_account_is_not_a_default_admin` at the end of `tests/test_legacy_auth_failclosed.py`.

Background (author's evidence, verify independently where you can offline):
- The auth middleware stores the JWT subject and role: `main.py:393` `request.state.username = payload.get("sub")`, `main.py:394` `request.state.role = payload.get("_role")`.
- For Supabase SSO tokens `sub` is the user UUID and `_role` comes from `app_metadata.role` (`auth_routes.py` `verify_token`, Path 1). Production data shows `vendor_bills.reviewed_by` (written from the same `state.username`) holding UUIDs since 2026-05-30 — so the old name allowlist rejected every SSO user, admins included. `store_context` was last edited 2026-05-20.
- The SSO path landed 2026-05-25 (`de40caf`); the allowlist dates from 2026-05-20 (`529dc35`).
- Coolify sets no `VEXON_ADMINS`; the legacy login path is fail-closed while `JWT_SECRET` is unset (AGENTS #75), so the `c28c86f` default change has no runtime effect today.

### Repo B — `C:\Users\rapee\VEXONHQ` (Next.js dashboard), branch `main`, range `origin/main..main`
- `32c8c50` — docs only. Skip.
- `8451f26` — `package.json` + `package-lock.json`: `next` and `eslint-config-next` `^16.3.3` → `^16.3.6`, for advisory GHSA-vcvr-r3jv-pc5j (affected `>=16.2.0 <16.3.6`, Node.js `next/og` `ImageResponse` with attacker-controlled SVG input). Author claims the app never imports `next/og`/`ImageResponse`, and the lockfile changes ONLY these 12 packages: `next`, `@next/env`, `@next/eslint-plugin-next`, `eslint-config-next`, and 8 `@next/swc-*` binaries.

## What to audit — answer each with evidence

**A. store-context gate (`539c2af`)**
1. Is there any path where an authenticated request reaches `_require_admin` with `request.state.username` set but `request.state.role` NOT set (or set from an untrusted source)? Check `main.py` middleware L355-396 and `PUBLIC_PATHS` L350 — is `/store-context` excluded from public paths?
2. Can a non-admin still mutate store_context through ANY route (including the legacy Path 2 token, where `_role` comes from the payload `role` claim — `auth_routes.py` ~L310)? Note: Path 2 is only active when `LEGACY_AUTH_ENABLED`.
3. Was `_ADMIN_USERS` (or the old 403 message text) referenced anywhere else in either repo — code, tests, or the frontend (`lib/store-context-api.ts`, `app/admin/store-context/page.tsx`)? Expected: none; frontend shows `detail` generically (`lib/store-context-api.ts:34-42`).
4. `updated_by` now stores the token subject (a UUID for SSO). Confirm in the code that nothing parses or length-limits it. (Author verified the DB column is `text`, nullable, no length limit — you cannot check the DB offline; mark that part NOT VERIFIED.)
5. `tests/test_store_context_admin_gate.py`: does it exercise the REAL route + middleware (not a mock of `_require_admin`)? Does the DB stub (`get_db_conn` monkeypatched on `store_context_routes`) actually intercept the handlers' DB calls (check how `store_context_routes` imports `get_db_conn`, ~L46-50)? Would any assertion pass even if the bug were still present?

**B. VEXON_ADMINS default (`c28c86f`)**
6. List every consumer of `_ADMIN_USERNAMES` / `_get_role` in Repo A. Does removing `vexonhq` from the default break any job, script, or test? Specifically check `scripts/repair_incomplete_bills.py` (mints its own token with explicit `role`), `tests/test_workflow.py` (live E2E, skips without `VEXONHQ_TEST_PASS`; its default user is `vexonhq` — does it depend on the in-code default or on server env?), and `tests/test_legacy_auth_failclosed.py::test_real_secret_keeps_legacy_path_working`.
7. Does the new test skip correctly when `VEXON_ADMINS` is exported, and is it immune to the test-order hazards documented in AGENTS #75 (import `main` before `auth_routes`; `tests/test_ai_exec.py` stubbing `sys.modules`)?

**C. Next.js 16.3.6 (`8451f26`)**
8. Confirm with `git show 8451f26 -- package-lock.json` that ONLY the 12 packages above changed version, and nothing else (no transitive drift).
9. Confirm with a repo-wide search that no file in Repo B imports `next/og`, uses `ImageResponse`, or defines `opengraph-image`/`twitter-image` routes.
10. Anything in the 16.3.3 → 16.3.6 bump that could change runtime behavior for this app (middleware/proxy, auth cookies, `output` config)? Only report what you can cite from the installed package or its changelog; otherwise NOT VERIFIED.

## Expected local evidence (offline, for your own confirmation)
- Repo A: `cd C:\Users\rapee\vexonhq-ocr-api; .\.venv\Scripts\python.exe -m pytest tests/test_store_context_admin_gate.py tests/test_legacy_auth_failclosed.py tests/test_admin_gate.py -q` → 27 passed. Full offline gate `.\verify.ps1` → 648 passed / 2 skipped, `READY: safe to push`.
- Author's RED proof: the new store-context test fails 9/17 on `origin/main` code (SSO admin → 403 `user '<uuid>' cannot edit store_context`), and the new `VEXON_ADMINS` test fails on the old `auth_routes.py` (`assert 'admin' == 'user'`). You may reproduce only if you can do it WITHOUT modifying the working tree (e.g. `git worktree add <tmp> origin/main` in a temp folder, then delete the worktree).
- Repo B: `npm run lint` → 0 errors / 14 warnings (all pre-existing: 11 unused eslint-disable directives, 2 `no-location-assign-relative-destination` on the deliberate hard navigation in `components/AuthProvider.tsx:119,264`, 1 `no-img-element`); `npx tsc --noEmit` → 0 errors; `npm run build` → success, 70/70 pages, `ƒ Proxy (Middleware)` present. Do NOT run `npm install`/`npm audit fix` (it would modify the lockfile).
- Do NOT run `tests/test_smoke.py` or `tests/test_workflow.py` (they hit production).

## Output format
Line 1: the two pinned SHAs you saw. Then for each numbered item 1–10: `PASS` / `FAIL` / `NOT VERIFIED`, followed by the `file:line` evidence (and the exact command + output where you ran one) and, for FAIL, the concrete failure scenario (input → wrong behavior). Then a final list of any additional defects found, ranked by severity, each with `file:line`. No rewrite suggestions; no style comments; no edits.
