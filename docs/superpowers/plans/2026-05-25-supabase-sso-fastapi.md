# Supabase SSO — vexonhq-ocr-api (FastAPI) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Update FastAPI's JWT verification to accept Supabase-issued JWTs instead of the custom VEXONHQ JWTs, so all protected endpoints continue working after the VEXONHQ frontend migrates to Supabase Auth.

**Architecture:** The `verify_token()` function in `auth_routes.py` is the single chokepoint for JWT verification. It is imported into `main.py` and used in the `JWTAuthMiddleware` that gates every request. Updating `verify_token()` to decode using `SUPABASE_JWT_SECRET` with `audience="authenticated"` propagates to all 40+ endpoints automatically. Role is extracted from `app_metadata.role` instead of the top-level `role` field.

**Tech Stack:** PyJWT (already installed), `SUPABASE_JWT_SECRET` env var (new), Python 3.11.

**Spec:** `../../../marastation-web/docs/superpowers/specs/2026-05-25-supabase-sso-design.md`, Section 6.

**Prerequisite:** Phase 1 of the spec (Supabase users created with `app_metadata.role` set) must be done before deploying. VEXONHQ frontend migration can be deployed in parallel — FastAPI accepts Supabase JWTs, and VEXONHQ will start sending them after its own migration deploys.

**Transition note:** Deploying this plan breaks the old custom VEXONHQ JWT immediately. Old sessions will get 401 and be redirected to `/login`. This is expected — users log in once with Supabase credentials and all sessions are new after that.

---

## File Map

| File | Action | Purpose |
|------|--------|---------|
| `auth_routes.py` | Modify | Update `verify_token()`, `_require_admin_role()`, `get_me()`, `get_page_config()` |
| `main.py` | Modify | Update comment on `request.state.username` (now Supabase UUID, not username) |

Only two files change. All other route files are untouched — they call `verify_token()` indirectly through the `JWTAuthMiddleware` in `main.py`.

---

### Task 1: Update `verify_token()` in `auth_routes.py`

**Files:**
- Modify: `auth_routes.py`

The current `verify_token()` decodes with `JWT_SECRET` and no audience check. The new version:
1. Uses `SUPABASE_JWT_SECRET` (new env var)
2. Adds `audience="authenticated"` (Supabase sets this on every JWT)
3. Extracts `role` from `app_metadata.role` instead of top-level `role`
4. Keeps the return type as `Optional[dict]` (None on failure) so all callers work unchanged

- [ ] **Step 1: Read the current `auth_routes.py`**

Read `C:\Users\rapee\vexonhq-ocr-api\auth_routes.py` lines 50–195 to confirm the current `JWT_SECRET`, `JWT_ALGORITHM`, and `verify_token()` definitions.

- [ ] **Step 2: Add `SUPABASE_JWT_SECRET` constant below `JWT_SECRET`**

Find this block near line 57:
```python
JWT_SECRET = os.environ.get(
    "JWT_SECRET",
    "vexonhq-change-this-secret-key-in-production-please"
)
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_HOURS = 8
```

Replace with:
```python
JWT_SECRET = os.environ.get(
    "JWT_SECRET",
    "vexonhq-change-this-secret-key-in-production-please"
)
# Supabase JWT Secret — from Supabase → Settings → API → JWT Secret.
# Used to verify JWTs issued by Supabase Auth after SSO migration.
# Keep secret: never use NEXT_PUBLIC_ prefix, never log it.
SUPABASE_JWT_SECRET = os.environ.get("SUPABASE_JWT_SECRET", "")
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_HOURS = 8
```

- [ ] **Step 3: Replace `verify_token()` with Supabase-compatible version**

Find the current function (around line 178):
```python
def verify_token(token: str) -> Optional[dict]:
    """Return payload dict or None if invalid/expired."""
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None
```

Replace with:
```python
def verify_token(token: str) -> Optional[dict]:
    """
    Verify a Supabase JWT and return the normalized payload, or None.

    Decodes using SUPABASE_JWT_SECRET with audience="authenticated" (set by
    Supabase on every token). Adds a synthetic '_role' key with the role
    string from app_metadata so all callers can do payload['_role'] without
    re-extracting the nested field.

    Returns None on any verification failure (expired, wrong audience,
    wrong secret, malformed). Never raises.
    """
    if not SUPABASE_JWT_SECRET:
        log.error("verify_token: SUPABASE_JWT_SECRET is not set — rejecting all tokens")
        return None
    try:
        payload = jwt.decode(
            token,
            SUPABASE_JWT_SECRET,
            algorithms=[JWT_ALGORITHM],
            audience="authenticated",
        )
        # Normalize role into a top-level key for convenience
        app_meta = payload.get("app_metadata") or {}
        payload["_role"] = app_meta.get("role", "staff")
        return payload
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None
```

- [ ] **Step 4: Syntax check**

```powershell
python -c "import ast; ast.parse(open('C:/Users/rapee/vexonhq-ocr-api/auth_routes.py', encoding='utf-8').read()); print('ok')"
```
Expected output: `ok`

- [ ] **Step 5: Commit**

```powershell
cd C:\Users\rapee\vexonhq-ocr-api
git add auth_routes.py
git commit -m "feat(auth): update verify_token to use SUPABASE_JWT_SECRET + audience"
```

---

### Task 2: Update `_require_admin_role()` and auth endpoints for new role path

**Files:**
- Modify: `auth_routes.py`

The `_require_admin_role()` helper and the `get_me()` / `get_page_config()` endpoints currently read role with `payload.get("role", "user")`. After the `verify_token()` change in Task 1, role is available at `payload["_role"]` (the synthetic key we added). Update these three locations.

- [ ] **Step 1: Update `_require_admin_role()`**

Find (around line 313):
```python
def _require_admin_role(request: Request) -> dict:
    """Decode JWT from request and raise 403 if not admin. Returns payload."""
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Not authenticated")
    payload = verify_token(auth_header[7:])
    if not payload:
        raise HTTPException(status_code=401, detail="Token expired or invalid")
    if payload.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return payload
```

Replace with:
```python
def _require_admin_role(request: Request) -> dict:
    """Verify JWT from request and raise 403 if not admin. Returns payload."""
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Not authenticated")
    payload = verify_token(auth_header[7:])
    if not payload:
        raise HTTPException(status_code=401, detail="Token expired or invalid")
    if payload.get("_role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return payload
```

- [ ] **Step 2: Update `get_me()` endpoint**

Find the `get_me` function (around line 285):
```python
@router.get("/me")
def get_me(request: Request):
    """Return current authenticated user info."""
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Not authenticated")

    token = auth_header[7:]
    payload = verify_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Token expired or invalid")

    return {
        "username": payload.get("sub"),
        "role": payload.get("role", "user"),
        "expires_at": payload.get("exp"),
    }
```

Replace with:
```python
@router.get("/me")
def get_me(request: Request):
    """Return current authenticated user info."""
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Not authenticated")

    token = auth_header[7:]
    payload = verify_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Token expired or invalid")

    return {
        "sub": payload.get("sub"),
        "email": payload.get("email"),
        "role": payload.get("_role", "staff"),
        "expires_at": payload.get("exp"),
    }
```

- [ ] **Step 3: Update `get_page_config()` endpoint**

Find the `get_page_config` function (around line 326):
```python
@router.get("/page-config")
def get_page_config(request: Request):
    ...
    role = payload.get("role", "user")

    if role == "admin":
        return {"role": "admin", "pages": {}}
    ...
```

Change the `role =` line from:
```python
    role = payload.get("role", "user")
```

To:
```python
    role = payload.get("_role", "staff")
```

The rest of the function body is unchanged.

- [ ] **Step 4: Syntax check**

```powershell
python -c "import ast; ast.parse(open('C:/Users/rapee/vexonhq-ocr-api/auth_routes.py', encoding='utf-8').read()); print('ok')"
```
Expected: `ok`

- [ ] **Step 5: Commit**

```powershell
git add auth_routes.py
git commit -m "feat(auth): update role extraction to app_metadata._role in auth endpoints"
```

---

### Task 3: Update `main.py` — `request.state.username` comment

**Files:**
- Modify: `main.py`

`main.py` imports `verify_token` from `auth_routes` and uses it in the `JWTAuthMiddleware`. After Task 1, the middleware automatically gets the Supabase JWT verified. The only change needed is a comment update: `request.state.username` now stores a Supabase UUID, not a plain username.

- [ ] **Step 1: Read the relevant section of `main.py`**

Read `C:\Users\rapee\vexonhq-ocr-api\main.py` lines 310–330 to confirm the `verify_token` usage and `request.state.username` assignment.

- [ ] **Step 2: Update the comment on `request.state.username`**

Find (around line 322):
```python
        # Stash the JWT subject (username) on request.state so endpoints
        # can read it for audit trail (created_by / updated_by / reviewed_by)
        # without re-parsing the token. Falls back to None if the token had
        # no `sub` claim (legacy / malformed JWTs).
        request.state.username = payload.get("sub")
```

Replace with:
```python
        # Stash the JWT subject on request.state for audit trail
        # (created_by / updated_by / reviewed_by) without re-parsing.
        # After Supabase SSO migration, sub is a UUID (e.g. "a1b2c3d4-...").
        # Falls back to None if the token had no `sub` claim.
        request.state.username = payload.get("sub")
```

- [ ] **Step 3: Syntax check**

```powershell
python -c "import ast; ast.parse(open('C:/Users/rapee/vexonhq-ocr-api/main.py', encoding='utf-8').read()); print('ok')"
```
Expected: `ok`

- [ ] **Step 4: Commit**

```powershell
git add main.py
git commit -m "chore(auth): update request.state.username comment (now Supabase UUID)"
```

---

### Task 4: CI gate + `SUPABASE_JWT_SECRET` in Coolify

**Files:**
- Verify Python syntax on all modified files

- [ ] **Step 1: Syntax check all modified files**

```powershell
python -c "import ast; ast.parse(open('C:/Users/rapee/vexonhq-ocr-api/auth_routes.py', encoding='utf-8').read()); print('auth_routes.py: ok')"
python -c "import ast; ast.parse(open('C:/Users/rapee/vexonhq-ocr-api/main.py', encoding='utf-8').read()); print('main.py: ok')"
```
Expected:
```
auth_routes.py: ok
main.py: ok
```

- [ ] **Step 2: Local smoke test (if uvicorn is available)**

Start the server locally and test the JWT middleware:
```powershell
cd C:\Users\rapee\vexonhq-ocr-api
$env:SUPABASE_JWT_SECRET = "YOUR_JWT_SECRET_HERE"
uvicorn main:app --port 8001 --log-level info
```

In a second terminal, test that a request without a token returns 401:
```powershell
Invoke-WebRequest -Uri "http://localhost:8001/dashboard/overview" -Method GET | Select-Object StatusCode
```
Expected: `StatusCode: 401`

Test the `/health` endpoint (public path, no token needed):
```powershell
Invoke-WebRequest -Uri "http://localhost:8001/health" -Method GET | Select-Object StatusCode
```
Expected: `StatusCode: 200`

Stop the server with Ctrl+C.

Note: If running uvicorn locally is not practical, skip this step and rely on the post-deploy smoke test in Step 5.

- [ ] **Step 3: Backup tag**

```powershell
git fetch origin
git tag backup-pre-supabase-sso-2026-05-25 origin/main
```
(TUM pushes this tag: `git push origin backup-pre-supabase-sso-2026-05-25`)

- [ ] **Step 4: Hand off to TUM for Coolify env var**

TUM must add one new env var in Coolify dashboard → `vexonhq-ocr-api` app → Environment Variables:

```
SUPABASE_JWT_SECRET = <paste JWT Secret from Supabase → Settings → API → JWT Secret>
```

**IMPORTANT:** This is the JWT Secret, NOT the anon key and NOT the service_role key. It is ~40 characters. Never put it in `NEXT_PUBLIC_*` vars, never log it, never paste it in chat — paste it directly into Coolify.

Existing env vars that can be REMOVED after SSO migration is stable and verified:
- `JWT_SECRET` — no longer needed (old custom JWT signing key)
- `VEXON_USER`, `VEXON_HASH`, `VEXON_USER_*`, `VEXON_HASH_*`, `VEXON_ADMINS` — no longer needed (Supabase manages users)

Leave them in place during the transition period. Remove only after SSO has been running for a few days without issues.

- [ ] **Step 5: Push and deploy**

Hand TUM:
```powershell
cd C:\Users\rapee\vexonhq-ocr-api
git push origin main
git push origin backup-pre-supabase-sso-2026-05-25
```

Coolify auto-deploys on push to main (~1-2 min for Python rebuilds).

- [ ] **Step 6: Post-deploy smoke test**

After Coolify shows "Running":

Test public endpoint (should still return 200):
```powershell
Invoke-WebRequest -Uri "https://api.marastation.com/health" -Method GET | Select-Object StatusCode
```

Test that old custom JWTs are rejected (should return 401):
```powershell
Invoke-WebRequest -Uri "https://api.marastation.com/dashboard/overview" -Headers @{Authorization="Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJ0ZXN0IiwicmVzIjoib2xkIn0.fake"} -Method GET | Select-Object StatusCode
```
Expected: `StatusCode: 401`

Test that a valid Supabase JWT is accepted (requires a real Supabase session token — get one by logging in via the VEXONHQ frontend after its migration is deployed):
```powershell
Invoke-WebRequest -Uri "https://api.marastation.com/auth/me" -Headers @{Authorization="Bearer <supabase_access_token>"} -Method GET
```
Expected: JSON with `sub`, `email`, `role` fields.

---

## Spec Self-Review

**Spec coverage check (Section 6):**
- `SUPABASE_JWT_SECRET` env var: ✅ Task 4 (add to Coolify)
- `verify_token` using `SUPABASE_JWT_SECRET` + `audience="authenticated"`: ✅ Task 1
- `role = payload.get("app_metadata", {}).get("role")`: ✅ Task 1 (`_role` synthetic key)
- Return `{"sub": ..., "email": ..., "role": ...}` from `/auth/me`: ✅ Task 2

**No endpoint changes required** — only the `verify_token()` function changes. All 40+ endpoints that the `JWTAuthMiddleware` gates are automatically covered.

**`/auth/login` endpoint kept:** The old `POST /auth/login` endpoint is retained as-is. After SSO migration, VEXONHQ no longer calls it (login goes through Supabase directly). Keeping it in place means no breaking change for any other potential consumers. It can be deprecated and removed in a future session once confirmed unused.

**`/auth/page-config` endpoint kept and working:** VEXONHQ's `AuthProvider` still calls `GET /auth/page-config` after SSO login to load page visibility config for staff users. This endpoint now verifies the Supabase JWT correctly via the updated `verify_token()`.
