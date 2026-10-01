# External audit result — 2026-10-01 store-context admin gate + VEXON_ADMINS default + Next.js 16.3.6

Request: `2026-10-01-storectx-admin-next1636-review-request.md` (round 1) and
`2026-10-01-storectx-admin-next1636-review-request-round2.md` (round 2).

## Round 1 (reviewer pinned Repo A `c28c86f`, Repo B `8451f26`; read-only, no files edited)

| # | Item | Verdict | Key evidence cited by reviewer |
|---|------|---------|--------------------------------|
| 1 | Middleware sets `username` and `role` together; `/store-context` not public | PASS | `main.py:350` PUBLIC_PATHS, `:364` bypass list, `:382` verify_token, `:393-394` state assignment |
| 2 | No non-admin mutation path | PASS | `store_context_routes.py` gate before insert L252 / update L290 / delete L314; Path 2 only trusts `role` inside a token signed by the configured `JWT_SECRET` |
| 3 | No remaining refs to `_ADMIN_USERS` / old 403 text | PASS | `git grep` both repos → NO_MATCH; frontend shows `detail` generically (`lib/store-context-api.ts:32`) |
| 4 | `updated_by` path has no parsing/length limit | PASS (code); DB NOT VERIFIED | `main.py:389`, `store_context_routes.py:252,283` |
| 5 | Test hits real app + DB stub intercepts; catches the bug | PASS | old gate → 9/17 fail (5 UUID-admin + 4 legacy-name-staff); `27 passed` on the three files |
| 6 | Consumers of `_ADMIN_USERNAMES` / `_get_role`; nothing broken | PASS | only `create_token` → `/auth/login`; repair script mints its own `role="admin"`; `test_workflow.py` depends on server env |
| 7 | New test skip + test-order safety | PASS | skips when `VEXON_ADMINS` exported; imports `main` first; no reload |
| 8 | Lockfile: only the 12 Next packages changed | PASS | structured diff → root + 12 packages; `sharp` resolved version unchanged 0.35.4; numstat `49 49` |
| 9 | No `next/og` / `ImageResponse` / OG route files | PASS | `git grep` + `git ls-files` → no match |
| 10 | Runtime behavior change 16.3.3 → 16.3.6 | NOT VERIFIED (no internet) | local: lint 0 errors / 14 warnings, tsc 0, build 70/70 + `ƒ Proxy (Middleware)` |

Gates re-run by the reviewer: `pytest` 27 passed; `.\verify.ps1` 648 passed / 2 skipped, `READY: safe to push`; frontend lint/tsc/build as above.

### Additional defect — Low (ACCEPTED, fixed in `6bf0667`)
`.env.example:21` still set `VEXON_ADMINS=tum,vexonhq`; an explicit env value overrides the new `"tum"` default, so a deployment copied from the template would re-grant admin to a same-named `vexonhq` account.
Author verification: the only tracked occurrence (`git grep "VEXON_ADMINS=.*vexonhq"` → `.env.example:21` only); nothing reads `.env.example` (`git grep env.example` over `*.py *.ps1 *.sh Dockerfile*` → no match). Fixed to `VEXON_ADMINS=tum`; `tests/test_legacy_auth_failclosed.py` 6 passed.

### Item 10 closed by the author (release notes + app usage)
- v16.3.4: AVIF re-enabled (#97949), testmode recursion (#97691), TS alias build fix (#97997), Turbopack `crossOrigin` (#97930).
- v16.3.5: `next/image` disk-cache 0-byte guards (#98185, #98186), standalone NFTs with an adapter (#98167), CSP nonce on loading/template scripts (#98403), `use cache` prerender fix (#98448).
- v16.3.6: the GHSA-vcvr-r3jv-pc5j `next/og` fix (GitHub page content partly failed to load; the advisory text was confirmed).
- None touch middleware/proxy, cookies, auth, headers or redirects. App usage: CSP is `Content-Security-Policy-Report-Only` with no nonce (`next.config.js:44`); no `output`, no `use cache`, no `images.formats` (AVIF not configured); `next/image` used once (`app/recipes/page.tsx:4`).

### Noted, out of scope
Build warns `The "middleware" file convention is deprecated. Please use "proxy" instead.` — a Next 16 convention change that predates this work (`middleware.ts` exists, no `proxy.ts`). Not changed here; candidate for a separate task.

## Round 2 (different reviewer — Antigravity; pinned Repo A `6bf0667`, Repo B `8451f26`; read-only, no files edited)

**11/11 PASS, 0 additional defects.** Every item cited `file:line` and the reviewer re-ran the gates: targeted `pytest` 27 passed; `verify.ps1` 648 passed / 2 skipped, `READY: safe to push`; frontend lint 0 errors / 14 warnings, `tsc` 0, build 70/70 + `ƒ Proxy (Middleware)`. Item 10 was closed independently from the official v16.3.4/16.3.5/16.3.6 release notes (same list as above) plus app-usage checks (`next.config.js` Report-Only CSP without nonce, no `output`, no `images.formats`, no `use cache`). Item 4 DB part NOT VERIFIED offline, but the reviewer found `migrations/2026_05_20_store_context.sql:44` declares `updated_by TEXT` (matches the author's live `information_schema` check). Item 11 confirmed `.env.example:21` = `VEXON_ADMINS=tum`, no other tracked file suggests `vexonhq` as an admin value, and `.env.example` is referenced only in `README.md:28` / `SYSTEM_OVERVIEW.md:151` (docs).

Author spot-check of round 2 (per the "demand evidence" rule): two factual slips, neither changes a verdict —
- The tool log shows `Viewed C:\Users\rapee\VEXONHQ\next.config.mjs`; that file does not exist (`ls next.config.*` → only `next.config.js`, the only tracked config). The final answer cites the correct `next.config.js`.
- Item 8 says `sharp` "is not installed in node_modules"; it is (`node_modules/sharp` exists; lockfile `node_modules/sharp` 0.35.4 at L6400 both before and after `8451f26`). Round 1 had this right. The verdict — no transitive version drift — holds.

**Overall: 2 independent rounds, 21 item checks, 1 Low defect found and fixed (`6bf0667`), 0 open.**
