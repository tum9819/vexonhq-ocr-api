"""
VEXONHQ — Rules CRUD (Session 26 item E)
=========================================
Endpoints behind the `/rules` page that lets TUM manage two tables
without touching SQL:

  - `statement_rules`  → keyword / name / amount-pattern rules that
                         classify imported bank_statement_entries
  - `vendor_aliases`   → keyword → vendor_name mapping used by the
                         /search empty-results panel and the LINE-bot
                         search hint (Session 19+)

This module is read/write — every mutation records the JWT-derived
username (per Session 25 multi-user + Session 26 audit-trail item O).
The frontend page is intentionally minimal at MVP: list + add + delete.
Full inline-edit lives in a follow-up.

Schema assumptions:
  - `vendor_aliases.product_keyword` now has UNIQUE constraint via
    `migrations/2026_05_20_vendor_aliases_unique.sql`.
  - `statement_rules` schema was set up in `migrations/16_bank_statement.sql`.

Mounted in `main.py` via `app.include_router(rules_router)`.
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

try:
    from main import get_db_conn  # type: ignore
except ImportError:
    import os
    import psycopg2

    def get_db_conn():
        return psycopg2.connect(os.environ["DATABASE_URL"])

log = logging.getLogger("rules")
router = APIRouter(prefix="/rules", tags=["rules"])


def _current_username(request: Request) -> Optional[str]:
    """Mirror of main._current_username so this module is standalone."""
    return getattr(request.state, "username", None)


# ────────────────────────────────────────────────────────────────────
# Models
# ────────────────────────────────────────────────────────────────────

class StatementRuleIn(BaseModel):
    rule_type:     str           # 'keyword' / 'name' / 'amount_pattern'
    match_value:   str
    direction:     str           # 'income' / 'expense'
    category_code: str
    source_type:   Optional[str] = None
    priority:      int = 10


class VendorAliasIn(BaseModel):
    product_keyword: str
    vendor_name:     str
    is_active:       bool = True


# ────────────────────────────────────────────────────────────────────
# statement_rules — list / add / delete
# ────────────────────────────────────────────────────────────────────

@router.get("/statement-rules")
def list_statement_rules():
    """Return every row in statement_rules, ordered by priority (highest first)."""
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, rule_type, match_value, direction, category_code,
                       source_type, priority, created_at
                FROM public.statement_rules
                ORDER BY priority DESC, created_at DESC
                """
            )
            rows = []
            for r in cur.fetchall():
                rows.append({
                    "id":            str(r[0]),
                    "rule_type":     r[1],
                    "match_value":   r[2],
                    "direction":     r[3],
                    "category_code": r[4],
                    "source_type":   r[5],
                    "priority":      int(r[6] or 10),
                    "created_at":    r[7].isoformat() if r[7] else None,
                })
    finally:
        conn.close()
    return {"success": True, "rules": rows, "count": len(rows)}


@router.post("/statement-rules")
def create_statement_rule(body: StatementRuleIn, request: Request):
    actor = _current_username(request)
    if body.rule_type not in ("keyword", "name", "amount_pattern"):
        raise HTTPException(400, f"invalid rule_type {body.rule_type!r}")
    if body.direction not in ("income", "expense"):
        raise HTTPException(400, f"invalid direction {body.direction!r}")

    # Audit B9-C3 fix (2026-05-28): a 1-char or blank match_value becomes
    # `ILIKE '%x%'` (or `%%`, matching everything) and misclassifies every slip
    # on the next rematch-all. Require >= 2 characters after trim. Thai keywords
    # are short but 2 chars is the floor — 1 char is always a catch-all.
    match_value = body.match_value.strip()
    if len(match_value) < 2:
        raise HTTPException(
            400,
            "match_value ต้องยาวอย่างน้อย 2 ตัวอักษร (กฎสั้นไปจะกลายเป็น catch-all จับทุก slip)",
        )

    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO public.statement_rules
                    (rule_type, match_value, direction, category_code,
                     source_type, priority)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (rule_type, match_value) DO UPDATE
                SET direction     = EXCLUDED.direction,
                    category_code = EXCLUDED.category_code,
                    source_type   = EXCLUDED.source_type,
                    priority      = EXCLUDED.priority
                RETURNING id
                """,
                (
                    body.rule_type,
                    match_value,
                    body.direction,
                    body.category_code,
                    body.source_type,
                    body.priority,
                ),
            )
            new_id = cur.fetchone()[0]
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    log.info("statement_rule upsert id=%s by %s", new_id, actor)
    return {"success": True, "id": str(new_id)}


@router.delete("/statement-rules/{rule_id}")
def delete_statement_rule(rule_id: str, request: Request):
    actor = _current_username(request)
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM public.statement_rules WHERE id = %s",
                (rule_id,),
            )
            if cur.rowcount == 0:
                raise HTTPException(404, "rule not found")
        conn.commit()
    except HTTPException:
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    log.info("statement_rule delete id=%s by %s", rule_id, actor)
    return {"success": True, "deleted_id": rule_id}


# ────────────────────────────────────────────────────────────────────
# vendor_aliases — list / add / delete
# ────────────────────────────────────────────────────────────────────

@router.get("/vendor-aliases")
def list_vendor_aliases(active_only: bool = False):
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            sql = """
                SELECT id, product_keyword, vendor_name, is_active, created_at
                FROM public.vendor_aliases
            """
            if active_only:
                sql += " WHERE is_active = true"
            sql += " ORDER BY product_keyword"
            cur.execute(sql)
            rows = []
            for r in cur.fetchall():
                rows.append({
                    "id":              str(r[0]),
                    "product_keyword": r[1],
                    "vendor_name":     r[2],
                    "is_active":       bool(r[3]),
                    "created_at":      r[4].isoformat() if r[4] else None,
                })
    finally:
        conn.close()
    return {"success": True, "aliases": rows, "count": len(rows)}


@router.post("/vendor-aliases")
def create_vendor_alias(body: VendorAliasIn, request: Request):
    actor = _current_username(request)
    keyword = body.product_keyword.strip().lower()
    if not keyword:
        raise HTTPException(400, "product_keyword must not be empty")
    if not body.vendor_name.strip():
        raise HTTPException(400, "vendor_name must not be empty")

    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            # UNIQUE constraint on product_keyword (set by migration
            # 2026_05_20_vendor_aliases_unique.sql) lets us use a real
            # upsert here.
            cur.execute(
                """
                INSERT INTO public.vendor_aliases
                    (product_keyword, vendor_name, is_active)
                VALUES (%s, %s, %s)
                ON CONFLICT (product_keyword) DO UPDATE
                SET vendor_name = EXCLUDED.vendor_name,
                    is_active   = EXCLUDED.is_active
                RETURNING id
                """,
                (keyword, body.vendor_name.strip(), body.is_active),
            )
            new_id = cur.fetchone()[0]
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    log.info("vendor_alias upsert id=%s by %s", new_id, actor)
    return {"success": True, "id": str(new_id)}


@router.delete("/vendor-aliases/{alias_id}")
def delete_vendor_alias(alias_id: str, request: Request):
    actor = _current_username(request)
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM public.vendor_aliases WHERE id = %s",
                (alias_id,),
            )
            if cur.rowcount == 0:
                raise HTTPException(404, "alias not found")
        conn.commit()
    except HTTPException:
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    log.info("vendor_alias delete id=%s by %s", alias_id, actor)
    return {"success": True, "deleted_id": alias_id}
