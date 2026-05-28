# Batch 10 — Auth + RBAC + Infra/Ops Routes Audit

**Date:** 2026-05-27
**Scope:** `auth_routes.py`, `cron_heartbeat.py`, `health_monitor.py`, `auto_diagnose.py`, `do_snapshot_routes.py`, `alerts_webhook_routes.py` (+ `main.py` PUBLIC_PATHS / JWT middleware, `ai_exec_routes.py` for cross-check)
**Mode:** READ-ONLY. No source touched.

## Summary

| Severity | Count | IDs |
|---|---|---|
| CRITICAL | 3 | C1, C2, C3 |
| MEDIUM | 5 | M1, M2, M3, M4, M5 |
| LOW | 4 | L1, L2, L3, L4 |

Headline: the JWT middleware **authenticates but never authorizes** — role is computed and stashed but no route except `POST /auth/page-config` and `store_context` writes enforces admin. A Supabase `staff` token (or any valid self-issued `user` token) reaches every sensitive financial/ops endpoint (C1). Page-config is advisory-only and gives a false sense of RBAC. Secret-in-query-string on snapshot/alert endpoints (C2) leaks into proxy/access logs. ES256 audience hard-coded + silent fallthrough to legacy HS256 path widens the trust surface (C3).

---

## [C1] ✋ ACCEPTED RISK (2026-05-28) — No server-side role enforcement — staff/user tokens reach all sensitive endpoints

> **TUM decision 2026-05-28:** 5-user organization, 3 staff (may/toon/oil) are trusted employees, current page-config UI hide is sufficient for this organizational context. **No backend RBAC added.** Revisit if user count grows or trust assumption changes.

— original finding below —

**File:** `main.py:314-361` (JWTAuthMiddleware), `auth_routes.py:433-464` (page-config)

**Current code:** The middleware verifies the token, stashes `request.state.username`, and calls `call_next`. It never inspects `payload["_role"]`. The only role gates in the entire codebase are:
- `auth_routes.py:467 update_page_config` → `_require_admin_role(request)`
- `store_context_routes.py` → its own separate `_require_admin`

`GET /auth/page-config` returns `{role, pages}` purely so the **frontend** can hide menu items. Nothing stops a `user`/`staff` token from calling P&L, cashflow, OCR, bill-payment, exports, inventory, etc. directly.

**Issue:** This is privilege escalation by default. Supabase tokens default to `_role="staff"` (`auth_routes.py:252,264`) and self-issued non-admin logins get `_role="user"` — both are treated identically to admin at every endpoint that isn't one of the two hand-gated ones. The page-config visibility list is security theater: it gates the UI, not the API. Any staff member who opens devtools (or a leaked token) can read full financials and trigger writes.

**Suggested fix:** Decide the intended model and enforce it server-side:
- If page visibility is meant to be a real boundary, add a dependency (e.g. `Depends(require_visible_page)`) or middleware step that maps request path → `user_page_config.user_visible` and 403s non-admins on hidden pages.
- At minimum, add `_require_admin_role`-style guards to genuinely admin-only mutating endpoints (exports, config, payment writes) rather than relying on the frontend.
- Document explicitly in CLAUDE.md that today RBAC = "authenticated only" so it isn't mistaken for real per-role authz.

**Test plan:** Mint a self-issued token for a non-admin username (any username not in `VEXON_ADMINS`), or a Supabase token with `app_metadata.role="staff"`. `curl` a financial endpoint (e.g. `/phase2/pnl/monthly`) with that Bearer token — currently returns 200, should return 403 under the chosen model. Add a pytest asserting non-admin token → 403 on a representative gated route.

---

## [C2] Secrets passed as URL query parameters on public ops endpoints (log/referer leak)

**File:** `do_snapshot_routes.py:334-410` (`?secret=`), `alerts_webhook_routes.py:114-199` (`?secret=`)

**Current code:**
```python
@router.get("/status")
def snapshot_status(secret: str = Query("")):
    ...
    if secret != ALERTS_WEBHOOK_SECRET:
        raise HTTPException(401, "Invalid secret query param")
```
`/snapshots/status`, `/snapshots/auto-rotate`, `/alerts/uptime-webhook`, `/alerts/test-telegram` are all in `PUBLIC_PATHS` (`main.py:312`) and gate themselves on `?secret=<ALERTS_WEBHOOK_SECRET>`.

**Issue:** `ALERTS_WEBHOOK_SECRET` is the same shared secret across four endpoints, two of which perform **state-changing infra actions** (`/snapshots/auto-rotate` creates+deletes DO snapshots → real $ and data-loss surface). Putting it in the query string means it lands in: nginx/Coolify access logs, the reverse-proxy, browser history (these are GET and the docs literally say "hit in browser"), and any `Referer` header. A leaked log line = anyone can trigger snapshot rotation or spam Telegram. `secrets.compare_digest` is also not used, so the `!=` comparison is timing-comparable (minor given the bigger leak).

**Suggested fix:** Move the secret to a header (`X-Webhook-Secret` / `Authorization`), compare with `secrets.compare_digest`. If Uptime Robot can't send custom headers on the free plan, keep query-secret only for the read-only `/status` and the webhook, but require a header (or JWT) for the destructive `/snapshots/auto-rotate`. Ensure proxy access logs strip query strings for these paths.

**Test plan:** `curl "https://.../snapshots/auto-rotate?secret=WRONG"` → 401; with correct secret → 200. Grep Coolify/nginx access logs to confirm whether full query strings are currently logged. Add pytest for header-based auth once migrated.

---

## [C3] verify_token: hard-coded ES256 audience + silent fallthrough to legacy HS256 path

**File:** `auth_routes.py:213-291`

**Current code:** Path 1 decodes ES256 with `audience="authenticated"`. On *any* non-expiry exception (including wrong audience, JWKS network failure, signature failure) it logs a warning and **falls through to Path 2**, which decodes with `JWT_SECRET` and `options={"verify_aud": False}`.

**Issue:** Two concerns.
1. **Hard-coded audience.** If a future Supabase config issues tokens with a different `aud`, every legit Supabase token throws `InvalidAudienceError`, falls to Path 2, fails HS256 (different secret), and users are locked out — exactly the Session 41 class of breakage. The audience should be configurable.
2. **Fallthrough widens trust.** A genuinely invalid Supabase token (bad signature, JWKS unreachable) silently gets a *second* validation attempt against the self-issued secret. While the secrets differ so a forged ES256 token won't pass HS256, the design means a JWKS outage degrades all Supabase auth into "maybe it's a legacy token" rather than failing closed. Also, the default `JWT_SECRET` (`auth_routes.py:57-60`) is a known hard-coded string — if `JWT_SECRET` env is ever unset in prod, Path 2 accepts tokens anyone can forge.

**Suggested fix:** Make audience an env var (`SUPABASE_JWT_AUD`, default `authenticated`). On ES256 *signature/audience* failure (as opposed to "this isn't a Supabase token at all" — i.e. alg wasn't ES256/HS256), return None instead of falling through. Refuse to start (or log CRITICAL) if `JWT_SECRET` equals the baked-in default in a production environment.

**Test plan:** Token with `alg=ES256` and wrong `aud` → currently falls through; assert it returns None. Unset `JWT_SECRET`, confirm a startup guard fires. Token with valid ES256 + correct aud → returns payload with `_role` from `app_metadata`.

---

## [M1] /auth/me leaks `exp` and reflects unverified email claim; /me & page-config not centralizing auth

**File:** `auth_routes.py:391-408, 433-446`

**Issue:** Minor — `/auth/me` returns `expires_at: payload.get("exp")` (epoch int) which is harmless but each of `/auth/me`, `GET /page-config`, `_require_admin_role` re-implements the `Bearer ` parse + `verify_token` dance independently. Drift risk: a future change to one path (e.g. tightening) won't apply to the others. The `email` field is echoed straight from the token without note that for self-issued tokens it's always absent.

**Suggested fix:** Extract a single `_authed_payload(request) -> dict` helper raising 401, and have all three reuse it. Cosmetic but reduces the chance of a partial-fix bug.

**Test plan:** Unit test the shared helper for missing header / malformed / expired / valid.

---

## [M2] page-config visibility query: relies on `sort_order` and trusts DB rows, no admin write validation of `page_href`

**File:** `auth_routes.py:455-492`

**Current code:** `GET` selects `page_href, user_visible ... ORDER BY sort_order`. `POST` upserts `(page_href, page_label=page_href, user_visible)`.

**Issue:** Not SQL injection (parameterized correctly). But `page_href` is attacker-free-text from an admin with no validation/normalization — a typo (`/cashflow ` with trailing space, or `cashflow` vs `/cashflow`) silently creates a row that never matches a real route, so a page the admin *intended* to hide stays visible. Combined with C1 this is purely a UI-correctness bug, but worth noting. Also `page_label` is set to the href (loses the human label the column implies).

**Suggested fix:** Validate `page_href` against a known route allowlist (or at least normalize leading slash / trim). Accept an optional real `page_label`.

**Test plan:** POST `{"page_href":"cashflow","user_visible":false}` then GET as user — confirm whether the frontend route `/cashflow` is actually affected.

---

## [M3] health_monitor `_check_containers` flags healthy "(healthy)" / "Up (health: starting)" correctly but misparses names with spaces; `_check_api` only localhost

**File:** `health_monitor.py:97-131`

**Issue:** `_check_containers` splits `'{{.Names}} {{.Status}}'` on the **first** space (`split(" ", 1)`) — fine for names, but Docker status like `Up 3 hours (healthy)` is correctly caught by `startswith("up")`. The real edge: if `docker ps` emits zero lines (daemon down / permission denied), the function returns `False` (healthy) — a daemon outage reads as "all containers up." `_check_api` hitting `localhost:8000/health` only reaches `/health` (env-flag check), **not** `/health/deep` — so this monitor reports API healthy even when Postgres is down, contradicting the CLAUDE.md note that `/health` "only reports env-var flags."

**Suggested fix:** Treat empty/failed `docker ps` output as an issue (or at least log distinctly). Point `_check_api` at `/health/deep` so the LINE alert reflects real DB state.

**Test plan:** Stop Postgres locally, hit the job, confirm `api` issue fires. Simulate `docker ps` returning empty → expect container issue.

---

## [M4] cron_heartbeat: stale detection uses `last_run_at`, masks a job that runs-but-always-errors

**File:** `cron_heartbeat.py:200-216`

**Issue:** `is_stale` is computed from `last_run_at` (updated on both success AND error paths — see `record_heartbeat` writing `last_run_at=NOW()` in the error branch at line 105). A job that fires every interval but throws every time keeps `last_run_at` fresh, so `stale=False` and `/cron/health` returns 200 "healthy" despite `error_count` climbing and `last_success_at` going cold. Uptime Robot polling `/cron/health` would never see the 503.

**Suggested fix:** Compute staleness (or a second "failing" flag) from `last_success_at`, not `last_run_at`. Return 503 when `now - last_success_at > 2× interval` even if `last_run_at` is recent.

**Test plan:** Insert a heartbeat row with recent `last_run_at`/`last_error_at` but old `last_success_at` → assert `/cron/health` returns 503.

---

## [M5] auto_diagnose / do_snapshot: error strings (which may embed secrets) forwarded to Discord + Anthropic

**File:** `auto_diagnose.py:210-219, 329-352`; `do_snapshot_routes.py:131-136, 306-328`

**Issue:** `_call_claude` serializes the full `check_results` dict to Anthropic, and `try_diagnose` posts the model's output to Discord. `do_snapshot._do_api` puts up to 400 chars of the DO API error body into `DOApiError`, which `_post_report` forwards to Discord (`errors[:200]`). DO/DB error bodies can contain connection strings, tokens, or droplet metadata. Low likelihood but the secret-leak-to-Discord path the checklist flags exists here. Anthropic prompt also receives whatever `/health/deep` packs into `check_results` — if that ever includes a DSN or key, it egresses to a third party.

**Suggested fix:** Scrub known secret patterns (`Bearer `, `postgres://`, `password=`, api keys) before forwarding error strings to Discord/Anthropic. Whitelist which `check_results` fields are sent rather than dumping the whole dict.

**Test plan:** Feed `try_diagnose` a `check_results` containing a fake `postgres://user:pw@host` string; assert the Discord/Anthropic payload is redacted.

---

## [L1] `\redoc` typo in middleware path check

**File:** `main.py:329` — `or path.startswith("\redoc")` uses a backslash + `\r` escape, so it matches a path starting with carriage-return + `edoc`, never `/redoc`. `/redoc` is already covered by being absent from PUBLIC_PATHS but `/docs` startswith handles docs; `/redoc` would actually require a token. Harmless (redoc just needs login) but clearly a bug. Fix to `"/redoc"`.

## [L2] Rate limiter is per-process in-memory, unbounded dict growth

**File:** `auth_routes.py:166-169, 298-310`; `ai_exec_routes.py:61-72` — `_login_attempts` / `_rate_buckets` never evict empty keys, so a distributed attacker rotating IPs grows the dict unboundedly (slow memory leak). Per-process means N workers = N× the limit. Acceptable for single-worker Coolify today; note for scale-out.

## [L3] `/auth/login` user-existence timing: placeholder hash uses iterations from a 5-part string with `0` salt

**File:** `auth_routes.py:360-361` — placeholder `pbkdf2:sha256:260000:0:...`. Good intent (constant-time-ish to avoid username enumeration) but the placeholder salt is `0` while real salts are 32 hex chars; `pbkdf2_hmac` cost depends on iterations (same 260000) not salt length, so timing is roughly matched — fine. Just confirm all real hashes use 260000 iterations or the timing oracle reopens.

## [L4] DO snapshot rotation deletes before new snapshot is durable

**File:** `do_snapshot_routes.py:248-303` — comment acknowledges create returns an async action; rotation deletes old auto-snapshots while the new one is still building (~10 min). With `DO_SNAPSHOT_MAX_KEEP=1` there is a window where 0 *completed* auto-snapshots exist if the in-flight one fails. Low risk (clean-base + manual slots survive) but worth a guard: verify the new snapshot reached `completed` before deleting the last good one, or keep N+1 during the window.

---

## Cross-checks (NOT findings)

- `/ai/exec` in PUBLIC_PATHS is **acceptable**: self-guards on `X-AI-Exec-Key` and fails closed when `AI_EXEC_SECRET` unset (`ai_exec_routes.py:93-95`), whitelist-only commands, no shell metacharacters reachable from user input. (Auth-header-on-frontend concern noted as false-positive per brief.)
- SQL in scope is fully parameterized; no injection. Column names used (`user_page_config.page_href/user_visible/sort_order/page_label`, `job_heartbeat.*`) were NOT cross-checked against `information_schema` — **verify `user_page_config` and `job_heartbeat` columns exist** before trusting (per CLAUDE.md rule 6); they are outside the documented cheat-sheet tables.
- Suppressed exceptions in `record_heartbeat`, `_push_line`, `_post_to_discord`, `try_diagnose` are **intentional and correct** (best-effort instrumentation must not break jobs) — they log via `log.exception`, so not silent.
- `auto_diagnose` cost is bounded: rate-limited 1/error_type/10min, `max_tokens` capped, `MAX_LOG_CHARS=30000` guard. No unbounded-cost path found.
