"""
VEXONHQ — store_context CRUD + AI prompt builder (Session 28 item U)
======================================================================
Single source of truth for restaurant-level knowledge that every AI
feature consults: AI Link Ingredients, slip categorization, future
LINE bot replies, menu suggestion, etc.

Endpoints (all admin-only):

    GET    /store-context                — list active rows (compact)
    GET    /store-context/{key}          — single row (full content)
    PATCH  /store-context/{key}          — update content / type / active
    POST   /store-context                — create new key
    DELETE /store-context/{key}          — hard delete
    POST   /store-context/reload         — purge in-memory cache

Plus a public helper used by other routers:

    build_context_prompt() -> str

The helper concatenates every active row in `priority ASC, key ASC`
order with a markdown separator. It caches the result for 60 seconds
to avoid hitting the DB on every AI call — the cache is invalidated
manually via the /reload endpoint (also fired automatically after any
PATCH/POST/DELETE).

Auth model:
  - read endpoints accept any JWT user (TUM + named accounts)
  - write endpoints require the "vexonhq" admin login OR a named user
    in an explicit allowlist. For now the named users are also admins
    (single-family operation); harden later before any external user.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

try:
    from main import get_db_conn  # type: ignore
except ImportError:
    import psycopg2

    def get_db_conn():
        return psycopg2.connect(os.environ["DATABASE_URL"])

log = logging.getLogger("store_context")
router = APIRouter(prefix="/store-context", tags=["store-context"])


# ────────────────────────────────────────────────────────────────────────────
# In-memory cache for build_context_prompt()
# ────────────────────────────────────────────────────────────────────────────
# Why a cache:
#   - AI endpoints (recipe AI Link, slip categorize) call build_context_prompt()
#     on every request. Doing a DB round-trip + concatenation every time would
#     waste ~50-100ms per request for content that changes rarely.
#   - TTL is short enough (60s) that TUM editing a row via the admin UI sees
#     the change reflected within a minute even if the explicit /reload call
#     fails.
#   - The mutation endpoints below also call _invalidate_cache() so most edits
#     show up immediately.

_CACHE_TTL_SECONDS = 60
_cache_lock = threading.Lock()
_cache: dict[str, object] = {
    "prompt":    "",
    "built_at":  0.0,
}


def _invalidate_cache() -> None:
    with _cache_lock:
        _cache["built_at"] = 0.0


def build_context_prompt() -> str:
    """
    Public helper — returns the concatenated active context content as
    a single string suitable for prepending to a Claude/GPT system_prompt.

    Caches for ~60s so a burst of AI calls doesn't hammer the DB.
    Cache is invalidated on any store_context mutation.
    """
    now = time.time()
    with _cache_lock:
        if _cache["built_at"] and now - _cache["built_at"] < _CACHE_TTL_SECONDS:  # type: ignore[operator]
            return _cache["prompt"]  # type: ignore[return-value]

    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT key, content, content_type
                FROM public.store_context
                WHERE is_active = true
                ORDER BY priority ASC, key ASC
                """
            )
            rows = cur.fetchall()
    finally:
        conn.close()

    sections: list[str] = []
    for key, content, content_type in rows:
        # JSON sections get a code fence so Claude knows it's structured data
        # — markdown content goes in raw so headings render naturally in the
        # final prompt the AI consumes.
        if content_type == "json":
            sections.append(f"### {key} (JSON)\n```json\n{content}\n```")
        else:
            sections.append(f"### {key}\n{content}")

    prompt = "\n\n".join(sections)

    with _cache_lock:
        _cache["prompt"] = prompt
        _cache["built_at"] = now

    return prompt


# ────────────────────────────────────────────────────────────────────────────
# Auth helpers
# ────────────────────────────────────────────────────────────────────────────

# Allowlist of usernames that may mutate store_context. The legacy admin
# account ("vexonhq") is always allowed; named family accounts are added
# explicitly so a future "read-only" account can be added without breaking
# this gate.
_ADMIN_USERS = {"vexonhq", "Tum", "tum", "May", "Toon", "Oil"}


def _current_username(request: Request) -> Optional[str]:
    return getattr(request.state, "username", None)


def _require_admin(request: Request) -> str:
    user = _current_username(request)
    if not user:
        raise HTTPException(401, "auth required")
    if user not in _ADMIN_USERS:
        raise HTTPException(403, f"user {user!r} cannot edit store_context")
    return user


# ────────────────────────────────────────────────────────────────────────────
# Models
# ────────────────────────────────────────────────────────────────────────────

class StoreContextCreate(BaseModel):
    key:          str
    content:      str
    content_type: str = "markdown"
    is_active:    bool = True
    priority:     int = 50
    notes:        Optional[str] = None


class StoreContextUpdate(BaseModel):
    content:      Optional[str] = None
    content_type: Optional[str] = None
    is_active:    Optional[bool] = None
    priority:     Optional[int] = None
    notes:        Optional[str] = None


# ────────────────────────────────────────────────────────────────────────────
# Endpoints
# ────────────────────────────────────────────────────────────────────────────

@router.get("")
def list_store_context(include_inactive: bool = False):
    """Compact list — content NOT included (it can be large)."""
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            if include_inactive:
                cur.execute(
                    """
                    SELECT key, content_type, is_active, priority,
                           length(content) AS bytes,
                           notes, updated_by, updated_at
                    FROM public.store_context
                    ORDER BY priority, key
                    """
                )
            else:
                cur.execute(
                    """
                    SELECT key, content_type, is_active, priority,
                           length(content) AS bytes,
                           notes, updated_by, updated_at
                    FROM public.store_context
                    WHERE is_active = true
                    ORDER BY priority, key
                    """
                )
            cols = [d[0] for d in cur.description]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]
            for r in rows:
                if r.get("updated_at"):
                    r["updated_at"] = r["updated_at"].isoformat()
    finally:
        conn.close()
    return {"success": True, "entries": rows, "count": len(rows)}


@router.get("/{key}")
def get_store_context(key: str):
    """Full content for a single key."""
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT key, content, content_type, is_active, priority,
                       notes, updated_by, updated_at
                FROM public.store_context
                WHERE key = %s
                """,
                (key,),
            )
            row = cur.fetchone()
            if not row:
                raise HTTPException(404, f"store_context key {key!r} not found")
            cols = [d[0] for d in cur.description]
            d = dict(zip(cols, row))
            if d.get("updated_at"):
                d["updated_at"] = d["updated_at"].isoformat()
    finally:
        conn.close()
    return {"success": True, "entry": d}


@router.post("")
def create_store_context(body: StoreContextCreate, request: Request):
    user = _require_admin(request)
    if body.content_type not in ("markdown", "json", "text"):
        raise HTTPException(400, f"invalid content_type {body.content_type!r}")
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO public.store_context
                    (key, content, content_type, is_active, priority,
                     notes, updated_by)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (body.key, body.content, body.content_type,
                 body.is_active, body.priority, body.notes, user),
            )
        conn.commit()
    except Exception as exc:
        conn.rollback()
        if "duplicate key" in str(exc):
            raise HTTPException(409, f"key {body.key!r} already exists")
        raise
    finally:
        conn.close()
    _invalidate_cache()
    log.info("store_context create key=%s by %s", body.key, user)
    return {"success": True, "key": body.key, "status": "created"}


@router.patch("/{key}")
def update_store_context(key: str, body: StoreContextUpdate, request: Request):
    user = _require_admin(request)
    updates = {k: v for k, v in body.model_dump(exclude_unset=True).items() if v is not None}
    if not updates:
        raise HTTPException(400, "no fields to update")
    if "content_type" in updates and updates["content_type"] not in ("markdown", "json", "text"):
        raise HTTPException(400, f"invalid content_type {updates['content_type']!r}")

    set_clauses = [f"{k} = %s" for k in updates]
    set_clauses.append("updated_by = %s")
    values = list(updates.values()) + [user, key]

    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"UPDATE public.store_context SET {', '.join(set_clauses)} WHERE key = %s",
                values,
            )
            if cur.rowcount == 0:
                raise HTTPException(404, f"store_context key {key!r} not found")
        conn.commit()
    except HTTPException:
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    _invalidate_cache()
    log.info("store_context update key=%s by %s fields=%s", key, user, list(updates.keys()))
    return {"success": True, "key": key, "updated_fields": list(updates.keys())}


@router.delete("/{key}")
def delete_store_context(key: str, request: Request):
    user = _require_admin(request)
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM public.store_context WHERE key = %s", (key,))
            if cur.rowcount == 0:
                raise HTTPException(404, f"store_context key {key!r} not found")
        conn.commit()
    except HTTPException:
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    _invalidate_cache()
    log.info("store_context delete key=%s by %s", key, user)
    return {"success": True, "key": key, "status": "deleted"}


@router.post("/reload")
def reload_store_context_cache(request: Request):
    """Force the in-memory cache to refresh on next read. Idempotent."""
    user = _require_admin(request)
    _invalidate_cache()
    log.info("store_context cache invalidated by %s", user)
    return {"success": True, "message": "cache invalidated"}
