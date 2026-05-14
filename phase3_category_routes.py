"""
VEXONHQ Phase 3 F4 — Category Hierarchy Routes
==============================================
Backend for the upgraded expense_categories table (parent_code + color + direction).
Companion to 10_phase3_f4_category_hierarchy.sql.

Endpoints (6):
    GET    /categories/tree         — nested parents → children, ready for tree UI
    GET    /categories/list         — flat list with parent_code
    GET    /categories/health       — smoke test
    POST   /categories              — create new (parent optional)
    PATCH  /categories/{code}       — edit fields
    DELETE /categories/{code}       — soft delete (is_active=false)
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime
from typing import Any, Optional
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

try:
    from main import get_db_conn  # type: ignore
except ImportError:
    import os
    import psycopg2
    def get_db_conn():
        return psycopg2.connect(os.environ["DATABASE_URL"])


logger = logging.getLogger("phase3_category_routes")
router = APIRouter(tags=["phase3-categories"])

VALID_DIRECTIONS = {"income", "expense", "both"}
HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


# ============================================================
# Helpers
# ============================================================

def _serialize_row(row: dict) -> dict:
    out: dict[str, Any] = {}
    for k, v in row.items():
        if isinstance(v, UUID):
            out[k] = str(v)
        elif isinstance(v, (datetime, date)):
            out[k] = v.isoformat()
        elif hasattr(v, "__float__") and not isinstance(v, (int, float, bool)):
            out[k] = float(v)
        else:
            out[k] = v
    return out


def _rows_to_dicts(cur) -> list[dict]:
    if cur.description is None:
        return []
    cols = [d[0] for d in cur.description]
    return [_serialize_row(dict(zip(cols, r))) for r in cur.fetchall()]


def _validate_code(code: str) -> str:
    """Category code must be lowercase alphanumeric + underscore, max 60 chars."""
    if not code or not code.strip():
        raise HTTPException(400, "code is required")
    code = code.strip().lower()
    if not re.match(r"^[a-z0-9_]+$", code):
        raise HTTPException(400, "code must contain only a-z, 0-9, _ (lowercase)")
    if len(code) > 60:
        raise HTTPException(400, "code must be <= 60 characters")
    return code


def _validate_color(color: Optional[str]) -> Optional[str]:
    if not color:
        return None
    color = color.strip()
    if not HEX_COLOR_RE.match(color):
        raise HTTPException(400, f"color must be hex like #16a34a, got: {color!r}")
    return color.lower()


# ============================================================
# Pydantic models
# ============================================================

class CategoryCreate(BaseModel):
    code: str
    name_th: str
    name_en: Optional[str] = None
    parent_code: Optional[str] = None
    direction: str = "expense"
    color: Optional[str] = None
    sort_order: int = 999
    description: Optional[str] = None


class CategoryPatch(BaseModel):
    name_th: Optional[str] = None
    name_en: Optional[str] = None
    parent_code: Optional[str] = None
    direction: Optional[str] = None
    color: Optional[str] = None
    sort_order: Optional[int] = None
    description: Optional[str] = None
    is_active: Optional[bool] = None


# ============================================================
# Endpoints
# ============================================================

@router.get("/categories/tree")
def get_tree(
    direction: Optional[str] = Query(None, description="Filter top-level by direction"),
    include_inactive: bool = Query(False),
):
    """Nested tree structure ready for UI rendering.
    Returns: [{ ...parent, children: [ ...child ] }, ...]"""
    if direction and direction not in VALID_DIRECTIONS:
        raise HTTPException(400, f"direction must be one of {sorted(VALID_DIRECTIONS)}")

    where_parts = []
    params: list[Any] = []
    if not include_inactive:
        where_parts.append("is_active = true")

    where = " WHERE " + " AND ".join(where_parts) if where_parts else ""

    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""SELECT code, name_th, name_en, parent_code, direction, color,
                           sort_order, is_active, description, child_count,
                           parent_name_th, effective_color
                    FROM public.v_categories_tree
                    {where}
                    ORDER BY sort_order, name_th""",
                params,
            )
            all_rows = _rows_to_dicts(cur)

        # Build tree
        by_code = {r["code"]: dict(r, children=[]) for r in all_rows}
        roots: list[dict] = []
        for r in all_rows:
            if r["parent_code"] and r["parent_code"] in by_code:
                by_code[r["parent_code"]]["children"].append(by_code[r["code"]])
            elif r["parent_code"] is None:
                roots.append(by_code[r["code"]])

        # Filter top-level by direction if requested (children inherit)
        if direction:
            roots = [r for r in roots if r["direction"] == direction]

        return {"roots": roots, "total": len(all_rows)}
    finally:
        conn.close()


@router.get("/categories/list")
def list_categories(
    direction: Optional[str] = Query(None),
    parent_code: Optional[str] = Query(None, description="Filter by exact parent (use '__none__' for top-level)"),
    q: Optional[str] = Query(None, description="search name_th / name_en / code"),
    include_inactive: bool = Query(False),
    limit: int = Query(500, ge=1, le=2000),
    offset: int = Query(0, ge=0),
):
    """Flat list with parent_code column. Good for dropdowns."""
    if direction and direction not in VALID_DIRECTIONS:
        raise HTTPException(400, f"direction must be one of {sorted(VALID_DIRECTIONS)}")

    where: list[str] = []
    params: list[Any] = []
    if not include_inactive:
        where.append("is_active = true")
    if direction:
        where.append("direction = %s"); params.append(direction)
    if parent_code is not None:
        if parent_code == "__none__":
            where.append("parent_code IS NULL")
        else:
            where.append("parent_code = %s"); params.append(parent_code)
    if q:
        where.append("(name_th ILIKE %s OR name_en ILIKE %s OR code ILIKE %s)")
        params.extend([f"%{q}%", f"%{q}%", f"%{q}%"])

    sql_where = (" WHERE " + " AND ".join(where)) if where else ""

    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""SELECT code, name_th, name_en, parent_code, direction, color,
                           sort_order, is_active, description, child_count,
                           parent_name_th, effective_color
                    FROM public.v_categories_tree{sql_where}
                    ORDER BY sort_order, name_th
                    LIMIT %s OFFSET %s""",
                params + [limit, offset],
            )
            rows = _rows_to_dicts(cur)

            cur.execute(
                f"SELECT count(*) FROM public.v_categories_tree{sql_where}",
                params,
            )
            total = cur.fetchone()[0]
        return {"rows": rows, "total": total, "limit": limit, "offset": offset}
    finally:
        conn.close()


@router.post("/categories")
def create_category(body: CategoryCreate):
    """Create a new category. parent_code optional (NULL = top-level)."""
    code = _validate_code(body.code)
    name_th = body.name_th.strip() if body.name_th else None
    if not name_th:
        raise HTTPException(400, "name_th is required")
    if body.direction not in VALID_DIRECTIONS:
        raise HTTPException(400, f"direction must be one of {sorted(VALID_DIRECTIONS)}")
    color = _validate_color(body.color)

    parent = body.parent_code.strip() if body.parent_code else None

    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            # Verify parent exists if provided
            if parent:
                cur.execute(
                    "SELECT 1 FROM public.expense_categories WHERE code = %s",
                    (parent,),
                )
                if not cur.fetchone():
                    raise HTTPException(404, f"parent_code not found: {parent}")
                if parent == code:
                    raise HTTPException(400, "parent_code cannot equal code (self-parent)")

            try:
                cur.execute(
                    """INSERT INTO public.expense_categories
                         (code, name_th, name_en, parent_code, direction, color,
                          sort_order, description, is_active)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, true)
                       RETURNING code, name_th, name_en, parent_code, direction,
                                 color, sort_order, is_active""",
                    (code, name_th, body.name_en, parent, body.direction, color,
                     body.sort_order, body.description),
                )
            except Exception as e:
                conn.rollback()
                if "duplicate key" in str(e).lower() or "unique" in str(e).lower():
                    raise HTTPException(409, f"Category code already exists: {code}")
                raise HTTPException(500, f"Insert failed: {e}")
            row = cur.fetchone()
            cols = [d[0] for d in cur.description]
            conn.commit()
            return _serialize_row(dict(zip(cols, row)))
    finally:
        conn.close()


@router.patch("/categories/{code}")
def patch_category(code: str, body: CategoryPatch):
    """Edit a category. Cannot edit code itself (would break FKs in vendor_bills etc)."""
    code = _validate_code(code)

    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    if not updates:
        raise HTTPException(400, "No fields to update")

    if "direction" in updates and updates["direction"] not in VALID_DIRECTIONS:
        raise HTTPException(400, f"direction must be one of {sorted(VALID_DIRECTIONS)}")
    if "color" in updates:
        updates["color"] = _validate_color(updates["color"])
    if "parent_code" in updates:
        parent = updates["parent_code"]
        if parent == code:
            raise HTTPException(400, "parent_code cannot equal code (self-parent)")
        # parent_code might be set to empty string to clear it
        if parent == "":
            updates["parent_code"] = None

    set_clauses = ", ".join(f"{k} = %s" for k in updates.keys())
    params = list(updates.values()) + [code]

    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            # If parent_code is being set non-null, verify it exists
            new_parent = updates.get("parent_code")
            if new_parent:
                cur.execute(
                    "SELECT 1 FROM public.expense_categories WHERE code = %s",
                    (new_parent,),
                )
                if not cur.fetchone():
                    raise HTTPException(404, f"parent_code not found: {new_parent}")

            cur.execute(
                f"""UPDATE public.expense_categories
                    SET {set_clauses}, updated_at = now()
                    WHERE code = %s
                    RETURNING code, name_th, parent_code, direction, color,
                              is_active, sort_order""",
                params,
            )
            row = cur.fetchone()
            if not row:
                raise HTTPException(404, f"Category not found: {code}")
            conn.commit()
            cols = [d[0] for d in cur.description]
            return _serialize_row(dict(zip(cols, row)))
    finally:
        conn.close()


@router.delete("/categories/{code}")
def soft_delete_category(code: str, cascade_children: bool = Query(False)):
    """Soft delete (is_active=false). Existing transactions keep the category_code FK.
    If cascade_children=true, also deactivates children."""
    code = _validate_code(code)
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE public.expense_categories SET is_active = false, updated_at = now() "
                "WHERE code = %s RETURNING code",
                (code,),
            )
            row = cur.fetchone()
            if not row:
                raise HTTPException(404, f"Category not found: {code}")

            cascade_count = 0
            if cascade_children:
                cur.execute(
                    "UPDATE public.expense_categories SET is_active = false, updated_at = now() "
                    "WHERE parent_code = %s",
                    (code,),
                )
                cascade_count = cur.rowcount
            conn.commit()
            return {
                "code": code,
                "is_active": False,
                "cascade_children": cascade_children,
                "children_deactivated": cascade_count,
            }
    finally:
        conn.close()


@router.get("/categories/health")
def categories_health():
    """Smoke: count active categories + top-level + tree depth."""
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM public.expense_categories WHERE is_active = true")
            total_active = cur.fetchone()[0]
            cur.execute("SELECT count(*) FROM public.expense_categories "
                        "WHERE is_active = true AND parent_code IS NULL")
            top_level = cur.fetchone()[0]
            cur.execute(
                """SELECT direction, count(*)::int
                   FROM public.expense_categories
                   WHERE is_active = true
                   GROUP BY direction
                   ORDER BY direction"""
            )
            by_direction = {row[0]: int(row[1]) for row in cur.fetchall()}
        return {
            "db": "ok",
            "total_active": int(total_active),
            "top_level": int(top_level),
            "by_direction": by_direction,
        }
    finally:
        conn.close()
