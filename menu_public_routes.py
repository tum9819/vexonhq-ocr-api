"""
VEXONHQ — Public Menu endpoint (Session 33 Item A_new)
======================================================
Customer-facing menu data for marastation-web public site.

DESIGN BOUNDARY
---------------
This file holds the ONLY menu data exposed without JWT auth. All other
/menu/* and /recipes/* endpoints are JWT-authed and intentionally hide
cost / GP / ingredient / supplier information.

WHITELIST (the only fields returned)
------------------------------------
    id, name, selling_price, category, description, image_url, badge

EXPLICITLY NOT RETURNED (and must never be added without re-review):
    notes              — internal cost / supplier notes
    cost_per_dish      — recipe cost (derived from ingredients)
    gp_pct             — gross profit margin
    ingredient_count   — exposes recipe complexity
    recipe_ingredients — full recipe (sou-chef IP)
    supplier_info      — vendor pricing
    created_at / updated_at — internal audit timestamps

FILTER
------
Only recipes with selling_price > 0 are returned. A recipe with price=0
is considered a draft or internal-only entry not yet on the public menu.

CACHING
-------
Cache-Control: public, max-age=300 (5 minutes). Cloudflare in front of
api.marastation.com (Session 32 migration) will cache the response at
edge nodes. Menu changes through the admin UI take up to 5 min to
appear on the public site.

REGISTRATION
------------
- Router included from main.py (search for `menu_public_router`)
- Path `/menu/public` is added to PUBLIC_PATHS in main.py to skip
  the JWT middleware (otherwise the middleware returns 401 first).
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

from fastapi import APIRouter
from fastapi.responses import JSONResponse

try:
    from main import get_db_conn  # type: ignore
except ImportError:  # pragma: no cover — only used in standalone test imports
    import psycopg2
    def get_db_conn():
        return psycopg2.connect(os.environ["DATABASE_URL"])

logger = logging.getLogger("menu_public")
router = APIRouter(prefix="/menu", tags=["menu-public"])


@router.api_route("/public", methods=["GET", "HEAD"], include_in_schema=False)
def get_public_menu():
    """
    Return the customer-facing menu for the public website.

    Response shape:
        {
            "items": [
                {
                    "id": "<uuid>",
                    "name": "หมูสามชั้น",
                    "selling_price": 89.0,
                    "category": "ไม้ปิ้ง",
                    "description": "เนื้อหมูสามชั้นย่างเตาถ่าน..." | null,
                    "image_url": "https://...supabase.co/.../somu.jpg" | null,
                    "badge": "best_seller" | "recommended" | null
                },
                ...
            ],
            "count": 190,
            "generated_at": "2026-05-23T12:34:56+00:00"
        }

    HEAD support is included so future Uptime Robot monitoring works
    without the free-plan GET-method block (see
    [[reference-uptime-robot-head-only]]).
    """
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, name, selling_price, category, description, image_url, badge
                FROM public.recipes
                WHERE selling_price > 0
                ORDER BY category NULLS LAST, name
                """
            )
            rows = cur.fetchall()
            items = [
                {
                    "id":            str(r[0]),
                    "name":          r[1],
                    "selling_price": float(r[2]) if r[2] is not None else 0.0,
                    "category":      r[3],
                    "description":   r[4],
                    "image_url":     r[5],
                    "badge":         r[6],
                }
                for r in rows
            ]
    finally:
        conn.close()

    payload = {
        "items":        items,
        "count":        len(items),
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    return JSONResponse(
        content=payload,
        headers={"Cache-Control": "public, max-age=300"},
    )
