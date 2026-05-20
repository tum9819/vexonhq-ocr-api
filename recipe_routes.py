"""
VEXONHQ Phase 31 — Recipe & AI Menu Advisor
============================================
Endpoints:
    GET  /ingredients                    — list all ingredients
    POST /ingredients                    — create ingredient
    PUT  /ingredients/{id}              — update ingredient
    DELETE /ingredients/{id}            — delete ingredient
    POST /ingredients/import-from-stock  — import from latest pos_inventory_items snapshot

    GET  /recipes                        — list all recipes with cost + GP%
    POST /recipes                        — create recipe
    PUT  /recipes/{id}                  — update recipe
    DELETE /recipes/{id}                — delete recipe
    GET  /recipes/{id}                  — recipe detail + ingredients + cost breakdown
    POST /recipes/{id}/ingredients       — add ingredient to recipe
    DELETE /recipes/{id}/ingredients/{item_id} — remove ingredient from recipe

    POST /recipes/ai-suggest             — AI suggest menus from current stock
"""

from __future__ import annotations

import json
import logging
import os
import urllib.request
import urllib.error
from typing import Optional, List
from datetime import datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

try:
    from main import get_db_conn  # type: ignore
except ImportError:
    import psycopg2
    def get_db_conn():
        return psycopg2.connect(os.environ["DATABASE_URL"])

logger = logging.getLogger("recipe")
router = APIRouter(prefix="/recipes", tags=["recipe"])
ingredient_router = APIRouter(prefix="/ingredients", tags=["ingredients"])


# ── Pydantic Models ──────────────────────────────────────────

class IngredientCreate(BaseModel):
    name: str
    unit: str = "กก."
    price_per_unit: float = 0.0
    yield_pct: float = 100.0
    category: Optional[str] = None
    # Phase V — pack-size aware cost. pack_size is how many `unit` are in
    # one `invoice_unit` (e.g. 12 ขวด in 1 ลัง). Default 1 = "no conversion".
    # invoice_unit=NULL means the supplier invoices in the same unit as
    # the ingredient (no conversion path).
    pack_size: int = 1
    invoice_unit: Optional[str] = None
    unit_cost_source: Optional[str] = None   # 'manual' | 'invoice' | 'sales_estimate'
    # Phase V3 — stable invoice alias. When set, the sync engine uses this
    # text instead of `name` to match invoice_items.product_name. Lets TUM
    # keep kitchen-friendly names ("เบียร์ช้างคลาสสิก") while the supplier
    # invoice says something completely different ("เบียร์ช้าง 620 มล.").
    invoice_match_name: Optional[str] = None

class IngredientUpdate(BaseModel):
    name: Optional[str] = None
    unit: Optional[str] = None
    price_per_unit: Optional[float] = None
    yield_pct: Optional[float] = None
    category: Optional[str] = None
    pack_size: Optional[int] = None
    invoice_unit: Optional[str] = None
    unit_cost_source: Optional[str] = None
    invoice_match_name: Optional[str] = None

class RecipeCreate(BaseModel):
    name: str
    selling_price: float = 0.0
    category: Optional[str] = None
    notes: Optional[str] = None

class RecipeUpdate(BaseModel):
    name: Optional[str] = None
    selling_price: Optional[float] = None
    category: Optional[str] = None
    notes: Optional[str] = None

class RecipeIngredientAdd(BaseModel):
    ingredient_id: str
    qty_used: float

class AISuggestRequest(BaseModel):
    branch_code: str = "thawi_watthana"
    num_suggestions: int = 3


# ── Ingredient Endpoints ─────────────────────────────────────

@ingredient_router.get("")
def list_ingredients():
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, name, unit, price_per_unit, yield_pct, category,
                       source_item_id, created_at,
                       pack_size, invoice_unit, unit_cost_source, invoice_match_name
                FROM public.ingredients
                ORDER BY category NULLS LAST, name
            """)
            cols = [d[0] for d in cur.description]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]
            for r in rows:
                if r.get("created_at"):
                    r["created_at"] = r["created_at"].isoformat()
            return {"ingredients": rows, "count": len(rows)}
    finally:
        conn.close()


@ingredient_router.post("")
def create_ingredient(body: IngredientCreate):
    # Defensive: pack_size must be >= 1 (CHECK constraint enforces this
    # too, but catching here gives a friendlier 400 error message than
    # a Postgres constraint violation).
    pack_size = max(1, int(body.pack_size or 1))
    source = (body.unit_cost_source or 'manual').strip().lower()
    if source not in {'manual', 'invoice', 'sales_estimate'}:
        raise HTTPException(400, f"unit_cost_source must be 'manual'/'invoice'/'sales_estimate', got '{source}'")
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO public.ingredients
                    (name, unit, price_per_unit, yield_pct, category,
                     pack_size, invoice_unit, unit_cost_source, invoice_match_name)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
            """, (
                body.name, body.unit, body.price_per_unit, body.yield_pct, body.category,
                pack_size, body.invoice_unit, source,
                body.invoice_match_name or None,
            ))
            new_id = cur.fetchone()[0]
        conn.commit()
        return {"id": str(new_id), "status": "created"}
    finally:
        conn.close()


@ingredient_router.put("/{ingredient_id}")
def update_ingredient(ingredient_id: str, body: IngredientUpdate):
    updates = {k: v for k, v in body.dict().items() if v is not None}
    if not updates:
        raise HTTPException(400, "No fields to update")
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            set_clause = ", ".join(f"{k} = %s" for k in updates)
            cur.execute(
                f"UPDATE public.ingredients SET {set_clause}, updated_at = NOW() WHERE id = %s",
                list(updates.values()) + [ingredient_id]
            )
            if cur.rowcount == 0:
                raise HTTPException(404, "Ingredient not found")
        conn.commit()
        return {"status": "updated"}
    finally:
        conn.close()


@ingredient_router.delete("/{ingredient_id}")
def delete_ingredient(ingredient_id: str):
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM public.ingredients WHERE id = %s", (ingredient_id,))
            if cur.rowcount == 0:
                raise HTTPException(404, "Ingredient not found")
        conn.commit()
        return {"status": "deleted"}
    finally:
        conn.close()


@ingredient_router.post("/import-from-stock")
def import_ingredients_from_stock(branch_code: str = "thawi_watthana"):
    """Import ingredients from latest FoodStory inventory snapshot."""
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            # Get latest snapshot
            cur.execute("""
                SELECT id FROM public.pos_inventory_snapshots
                WHERE branch_code = %s
                ORDER BY snapshot_at DESC LIMIT 1
            """, (branch_code,))
            row = cur.fetchone()
            if not row:
                raise HTTPException(404, "No inventory snapshot found. Upload FoodStory stock first.")
            snapshot_id = row[0]

            # Get items from snapshot
            cur.execute("""
                SELECT id, item_name, unit
                FROM public.pos_inventory_items
                WHERE snapshot_id = %s
                ORDER BY item_name
            """, (snapshot_id,))
            items = cur.fetchall()

            imported = 0
            skipped = 0
            for item_id, item_name, unit in items:
                # Skip if already imported (by source_item_id)
                cur.execute(
                    "SELECT id FROM public.ingredients WHERE source_item_id = %s",
                    (item_id,)
                )
                if cur.fetchone():
                    skipped += 1
                    continue

                unit_clean = unit or "กก."
                cur.execute("""
                    INSERT INTO public.ingredients
                        (name, unit, price_per_unit, yield_pct, source_item_id)
                    VALUES (%s, %s, 0, 100, %s)
                """, (item_name, unit_clean, item_id))
                imported += 1

        conn.commit()
        return {
            "status": "ok",
            "imported": imported,
            "skipped_existing": skipped,
            "message": f"นำเข้า {imported} รายการ (ข้าม {skipped} ที่มีอยู่แล้ว) — กรุณากรอกราคาต่อหน่วยให้ครบ"
        }
    finally:
        conn.close()


# ── Auto-sync ingredient prices from invoice_items (Session 28) ──────
#
# Background: TUM uploads supplier invoices weekly. Each `invoice_items`
# row has a `product_name` + `unit_price` recorded by OCR. The
# `ingredients` table has a `price_per_unit` column that TUM previously
# had to type in by hand — error-prone + tedious.
#
# This endpoint walks confirmed invoice_items, matches them to
# ingredients by fuzzy name, and updates the ingredient's price to the
# most recent matching invoice's unit_price.
#
# Matching strategy (intentionally conservative — wrong matches are
# worse than missing matches):
#   1. Both names are normalised (lowercase + collapse whitespace/punctuation)
#   2. Match score:
#        100  — normalised strings are identical
#         50  — one name is a substring of the other (>= 3 chars)
#         0   — no overlap (excluded)
#   3. Length-ratio guard: the shorter normalised name must be ≥ 60% of
#      the longer one. Prevents "ไก่" (3 chars) from matching the
#      "สันในไก่^^^" (15) ingredient just because it's a substring.
#   4. For each ingredient, pick the highest-score match. Ties broken by
#      most recent `vendor_bills.bill_date`.
#   5. Skip ingredients whose normalised name is < 3 chars (would
#      false-match too aggressively).
#   6. Skip rows where price hasn't changed (`ABS(old - new) < 0.001`).
#
# Two-phase apply:
#   - POST .../sync-from-invoices (no body) → dry-run, returns all
#     proposals. Frontend renders with per-row checkbox.
#   - POST .../sync-from-invoices with body {"ingredient_ids": [...]}
#     → apply ONLY the ticked subset. Unit-mismatch rows are still
#     skipped even if checked, to prevent accidental kg-vs-pack price
#     updates.

class IngredientSyncApply(BaseModel):
    """Apply-mode body for /sync-from-invoices."""
    ingredient_ids: List[str]


@ingredient_router.post("/sync-from-invoices")
def sync_ingredient_prices_from_invoices(
    body: Optional[IngredientSyncApply] = None,
    dry_run: bool = True,
):
    """
    Match ingredients to invoice_items by fuzzy name and update
    `price_per_unit` to the most recent matching invoice's unit_price.

    Default mode is `dry_run=true` — returns proposed changes only.
    Apply by sending body `{"ingredient_ids": [...]}` (subset of the
    dry-run output that TUM has reviewed + ticked). The endpoint will
    auto-flip into apply-mode when the body is present.

    Response:
        {
            "success":        True,
            "dry_run":        bool,
            "proposed_count": int,
            "applied_count":  int,
            "skipped_unit_mismatch": int,
            "proposed": [
                {
                    "ingredient_id": uuid,
                    "ingredient_name": str,
                    "ingredient_unit": str,
                    "old_price":       float | None,
                    "new_price":       float,
                    "source_product_name": str,
                    "source_unit":     str | None,
                    "bill_date":       iso date | None,
                    "match_score":     int (100|50),
                    "unit_mismatch":   bool   # warn flag
                }
            ]
        }
    """
    # If a body with ingredient_ids was passed, we're in apply mode
    # regardless of the query string (the body is the more authoritative
    # signal). dry_run query param is only honoured when no body present.
    if body is not None and body.ingredient_ids:
        dry_run = False
    apply_ids = set(body.ingredient_ids) if body is not None and body.ingredient_ids else None
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                WITH normalized_ii AS (
                    SELECT
                        ii.id,
                        ii.product_name,
                        ii.unit_price,
                        ii.unit,
                        vb.bill_date,
                        regexp_replace(
                            LOWER(TRIM(COALESCE(ii.product_name, ''))),
                            '[[:space:]\\.,;:]+',
                            '',
                            'g'
                        ) AS pname_norm
                    FROM public.invoice_items ii
                    JOIN public.vendor_bills vb ON vb.id = ii.vendor_bill_id
                    WHERE vb.review_status = 'confirmed'
                      AND ii.unit_price IS NOT NULL
                      AND ii.unit_price > 0
                ),
                normalized_ing AS (
                    SELECT
                        i.id,
                        i.name,
                        i.unit,
                        i.price_per_unit AS old_price,
                        i.pack_size,
                        i.invoice_unit,
                        i.invoice_match_name,
                        -- Phase V3: use invoice_match_name as the lookup key
                        -- when it is set; fall back to name otherwise.
                        -- This lets TUM keep kitchen-friendly names while the
                        -- supplier invoice uses a completely different string.
                        regexp_replace(
                            LOWER(TRIM(COALESCE(i.invoice_match_name, i.name, ''))),
                            '[[:space:]\\.,;:]+',
                            '',
                            'g'
                        ) AS name_norm
                    FROM public.ingredients i
                ),
                matched AS (
                    SELECT DISTINCT ON (ing.id)
                        ing.id                  AS ingredient_id,
                        ing.name                AS ingredient_name,
                        ing.unit                AS ingredient_unit,
                        ing.old_price           AS old_price,
                        ing.pack_size           AS pack_size,
                        ing.invoice_unit        AS expected_invoice_unit,
                        ing.invoice_match_name  AS invoice_match_name,
                        ni.product_name         AS source_product_name,
                        ni.unit_price       AS raw_invoice_price,
                        ni.unit             AS source_unit,
                        ni.bill_date        AS bill_date,
                        CASE
                            -- Exact name match → always 100.
                            WHEN ni.pname_norm = ing.name_norm THEN 100
                            -- invoice_match_name was set explicitly by TUM,
                            -- meaning this is a confirmed alias not an
                            -- auto-guessed substring. Treat as score 100 so
                            -- the frontend auto-ticks it like an exact match.
                            WHEN ing.invoice_match_name IS NOT NULL THEN 100
                            ELSE 50
                        END AS match_score
                    FROM normalized_ing ing
                    JOIN normalized_ii ni ON (
                        ni.pname_norm LIKE '%%' || ing.name_norm || '%%'
                        OR ing.name_norm LIKE '%%' || ni.pname_norm || '%%'
                    )
                    WHERE length(ing.name_norm) >= 3
                      AND length(ni.pname_norm) >= 3
                      -- Length-ratio guard: prevent "ไก่" (3 chars) matching
                      -- "สันในไก่^^^" (15 chars) just because the short name
                      -- is a substring. The shorter side must be at least 60%
                      -- of the longer one — keeps "เนื้อสไลซ์" ⊂ "เนื้อสไลซ์โปร"
                      -- (73%) but rejects "ไก่" ⊂ "สันในไก่" (37%). Exact-name
                      -- matches always pass this gate by definition (ratio 100%).
                      -- Length-ratio guard: the shorter side must be either
                      --   (a) ≥ 60% of the longer side (catches near-equal names), OR
                      --   (b) ≥ 6 characters long (a ≥ 6-char key is specific enough
                      --       that a substring hit is almost certainly a real match,
                      --       even when the invoice embeds extra info like size/year).
                      -- This lets "SINGHA RESERVE" (12 chars) match
                      -- "เบียร์ SINGHA RESERVE (12x620CC)" while still blocking
                      -- "ไก่" (3 chars) from false-matching "สันในไก่".
                      AND (
                          LEAST(length(ing.name_norm), length(ni.pname_norm))::float
                              / GREATEST(length(ing.name_norm), length(ni.pname_norm))::float >= 0.6
                          OR LEAST(length(ing.name_norm), length(ni.pname_norm)) >= 6
                      )
                    ORDER BY ing.id,
                        CASE WHEN ni.pname_norm = ing.name_norm THEN 100 ELSE 50 END DESC,
                        ni.bill_date DESC NULLS LAST,
                        ni.unit_price DESC
                )
                SELECT
                    ingredient_id,
                    ingredient_name,
                    ingredient_unit,
                    old_price,
                    pack_size,
                    expected_invoice_unit,
                    invoice_match_name,
                    source_product_name,
                    raw_invoice_price,
                    source_unit,
                    bill_date,
                    match_score
                FROM matched
                ORDER BY match_score DESC, ingredient_name
                """
            )
            rows = cur.fetchall()
            cols = [d[0] for d in cur.description]

            # ── Phase V: pack-size aware price calculation ──────────────────
            # The CTE above returns the RAW invoice unit_price (e.g. ฿683
            # for a ลัง of ช้าง). We translate to per-ingredient-unit cost
            # here in Python so the logic is easier to read + audit.
            #
            # Three cases:
            #   (A) source_unit == ingredient.unit
            #       → no conversion; new_price = raw
            #   (B) source_unit == ingredient.invoice_unit and pack_size > 1
            #       → CONVERT; new_price = raw / pack_size
            #   (C) neither
            #       → unit_mismatch=True, won't apply automatically
            #
            # The "skip unchanged" filter from the old SQL `WHERE old_price...`
            # moved into Python because the comparison is now against the
            # CONVERTED price, not the raw one.
            def _u(s: Optional[str]) -> str:
                return (s or "").strip().lower()

            proposed = []
            for r in rows:
                d = dict(zip(cols, r))
                d["ingredient_id"] = str(d["ingredient_id"])
                old_price = float(d["old_price"]) if d.get("old_price") is not None else None
                raw_price = float(d["raw_invoice_price"])
                pack_size = int(d.get("pack_size") or 1)
                src_u  = _u(d.get("source_unit"))
                ing_u  = _u(d.get("ingredient_unit"))
                exp_u  = _u(d.get("expected_invoice_unit"))

                # Decide the conversion case.
                #
                # Why substring-match on the invoice unit:
                # The OCR sometimes returns the bare pack word ('ลัง')
                # and sometimes embeds the pack count ('ลังx12'). The
                # latter is *better* OCR but breaks plain equality. We
                # accept either when expected_invoice_unit ⊂ source_unit
                # (e.g. 'ลัง' ⊂ 'ลังx12'). Same the other way: if the
                # source is 'ลัง' and the ingredient expects 'ลัง12',
                # still treat as a match.
                def _unit_matches(a: str, b: str) -> bool:
                    if not a or not b:
                        return False
                    return a == b or a in b or b in a

                if _unit_matches(src_u, ing_u):
                    # Case A — same unit, no conversion
                    new_price = raw_price
                    conversion_applied = False
                    unit_mismatch = False
                elif exp_u and _unit_matches(src_u, exp_u) and pack_size > 1:
                    # Case B — invoice is the pack, ingredient is the unit
                    new_price = raw_price / pack_size
                    conversion_applied = True
                    unit_mismatch = False
                elif not src_u or not ing_u:
                    # Missing unit info on one side — treat as compatible.
                    new_price = raw_price
                    conversion_applied = False
                    unit_mismatch = False
                else:
                    # Case C — incompatible units, can't auto-convert
                    new_price = raw_price
                    conversion_applied = False
                    unit_mismatch = True

                # Skip unchanged rows (same ABS<0.001 filter as before, but
                # against the CONVERTED price).
                if old_price is not None and old_price > 0 and abs(old_price - new_price) <= 0.001:
                    continue

                d["old_price"]            = old_price
                d["raw_invoice_price"]    = raw_price
                d["new_price"]            = round(new_price, 2)
                d["pack_size"]            = pack_size
                d["conversion_applied"]   = conversion_applied
                d["unit_mismatch"]        = unit_mismatch
                # Keep the legacy field name for the frontend (preview
                # table reads `new_price`). Drop the raw alias from the
                # response root if it's redundant.
                if d.get("bill_date") is not None:
                    d["bill_date"] = d["bill_date"].isoformat()
                proposed.append(d)

            applied = 0
            if not dry_run and proposed:
                for p in proposed:
                    # Skip unit mismatches even when explicitly ticked —
                    # the warning exists for a reason and we never want
                    # to silently turn ฿199/EACH into ฿199/กก. behind
                    # TUM's back.
                    if p["unit_mismatch"]:
                        continue
                    # If apply_ids is set, only update rows TUM ticked.
                    # If apply_ids is None (legacy query-string mode),
                    # update everything that passed the unit-mismatch
                    # filter — backwards-compatible with old clients.
                    if apply_ids is not None and p["ingredient_id"] not in apply_ids:
                        continue
                    cur.execute(
                        """
                        UPDATE public.ingredients
                        SET price_per_unit   = %s,
                            unit_cost_source = 'invoice',
                            updated_at       = NOW()
                        WHERE id = %s
                        """,
                        (p["new_price"], p["ingredient_id"]),
                    )
                    applied += 1
                conn.commit()

        return {
            "success":        True,
            "dry_run":        dry_run,
            "proposed_count": len(proposed),
            "applied_count":  applied,
            "skipped_unit_mismatch": sum(1 for p in proposed if p["unit_mismatch"]),
            "proposed":       proposed,
        }
    finally:
        conn.close()


# ── Recipe Endpoints ─────────────────────────────────────────

def _calc_cost(cur, recipe_id: str) -> dict:
    """Calculate cost and GP% for a recipe."""
    cur.execute("""
        SELECT
            ri.id,
            ri.qty_used,
            i.name,
            i.unit,
            i.price_per_unit,
            i.yield_pct
        FROM public.recipe_ingredients ri
        JOIN public.ingredients i ON i.id = ri.ingredient_id
        WHERE ri.recipe_id = %s
    """, (recipe_id,))
    items = cur.fetchall()

    breakdown = []
    total_cost = 0.0
    missing_price_count = 0
    for ri_id, qty, name, unit, price, yield_pct in items:
        # IMPORTANT: psycopg2 returns Postgres NUMERIC columns as
        # decimal.Decimal, not float. The original code did
        # `yield_pct / 100.0` which raises TypeError when yield_pct is
        # Decimal because Decimal/float division isn't supported. The
        # bug stayed hidden as long as no recipe had any
        # recipe_ingredients rows — the loop body never executed. Once
        # TUM started linking via the AI flow, every /recipes list call
        # 500-ed. Cast everything to float up-front so the rest of the
        # arithmetic is plain Python floats.
        qty_f       = float(qty or 0)
        price_f     = float(price or 0)
        yield_f     = float(yield_pct or 100)
        effective_yield = yield_f / 100.0 if yield_f > 0 else 1.0
        item_cost = qty_f * price_f / effective_yield
        total_cost += item_cost
        # Count ingredients that contributed zero to the total — these
        # silently make GP% misleading because the cost calc is incomplete.
        # The UI surfaces a warning so TUM knows the GP% isn't trustworthy
        # until those ingredients get a price (via /ingredients sync).
        if price_f <= 0:
            missing_price_count += 1
        breakdown.append({
            "id": str(ri_id),
            "ingredient_name": name,
            "unit": unit,
            "qty_used": qty_f,
            "price_per_unit": price_f,
            "yield_pct": yield_f,
            "item_cost": round(item_cost, 2),
            "missing_price": price_f <= 0,
        })
    return {
        "breakdown":            breakdown,
        "total_cost":           round(total_cost, 2),
        "missing_price_count":  missing_price_count,
        "cost_incomplete":      missing_price_count > 0,
    }


@router.get("")
def list_recipes():
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, name, selling_price, category, notes, created_at
                FROM public.recipes
                ORDER BY category NULLS LAST, name
            """)
            cols = [d[0] for d in cur.description]
            recipes = [dict(zip(cols, r)) for r in cur.fetchall()]

            result = []
            for rec in recipes:
                cost_data = _calc_cost(cur, str(rec["id"]))
                cost = cost_data["total_cost"]
                sell = float(rec["selling_price"] or 0)
                gp_pct = round((sell - cost) / sell * 100, 1) if sell > 0 else None
                result.append({
                    **rec,
                    "id": str(rec["id"]),
                    "created_at": rec["created_at"].isoformat() if rec.get("created_at") else None,
                    "cost_per_dish":         cost,
                    "gp_pct":                gp_pct,
                    "ingredient_count":      len(cost_data["breakdown"]),
                    "missing_price_count":   cost_data["missing_price_count"],
                    "cost_incomplete":       cost_data["cost_incomplete"],
                })
        return {"recipes": result, "count": len(result)}
    finally:
        conn.close()


@router.post("")
def create_recipe(body: RecipeCreate):
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO public.recipes (name, selling_price, category, notes)
                VALUES (%s, %s, %s, %s)
                RETURNING id
            """, (body.name, body.selling_price, body.category, body.notes))
            new_id = cur.fetchone()[0]
        conn.commit()
        return {"id": str(new_id), "status": "created"}
    finally:
        conn.close()


@router.put("/{recipe_id}")
def update_recipe(recipe_id: str, body: RecipeUpdate):
    updates = {k: v for k, v in body.dict().items() if v is not None}
    if not updates:
        raise HTTPException(400, "No fields to update")
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            set_clause = ", ".join(f"{k} = %s" for k in updates)
            cur.execute(
                f"UPDATE public.recipes SET {set_clause}, updated_at = NOW() WHERE id = %s",
                list(updates.values()) + [recipe_id]
            )
            if cur.rowcount == 0:
                raise HTTPException(404, "Recipe not found")
        conn.commit()
        return {"status": "updated"}
    finally:
        conn.close()


@router.delete("/{recipe_id}")
def delete_recipe(recipe_id: str):
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM public.recipes WHERE id = %s", (recipe_id,))
            if cur.rowcount == 0:
                raise HTTPException(404, "Recipe not found")
        conn.commit()
        return {"status": "deleted"}
    finally:
        conn.close()


@router.get("/{recipe_id}")
def get_recipe(recipe_id: str):
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, name, selling_price, category, notes, created_at
                FROM public.recipes WHERE id = %s
            """, (recipe_id,))
            row = cur.fetchone()
            if not row:
                raise HTTPException(404, "Recipe not found")
            cols = [d[0] for d in cur.description]
            rec = dict(zip(cols, row))

            cost_data = _calc_cost(cur, recipe_id)
            cost = cost_data["total_cost"]
            sell = float(rec["selling_price"] or 0)
            gp_pct = round((sell - cost) / sell * 100, 1) if sell > 0 else None

        return {
            **rec,
            "id": str(rec["id"]),
            "created_at": rec["created_at"].isoformat() if rec.get("created_at") else None,
            "cost_per_dish": cost,
            "gp_pct": gp_pct,
            "ingredients": cost_data["breakdown"],
        }
    finally:
        conn.close()


@router.post("/{recipe_id}/ingredients")
def add_recipe_ingredient(recipe_id: str, body: RecipeIngredientAdd):
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            # Verify recipe exists
            cur.execute("SELECT id FROM public.recipes WHERE id = %s", (recipe_id,))
            if not cur.fetchone():
                raise HTTPException(404, "Recipe not found")
            # Verify ingredient exists
            cur.execute("SELECT id FROM public.ingredients WHERE id = %s", (body.ingredient_id,))
            if not cur.fetchone():
                raise HTTPException(404, "Ingredient not found")

            cur.execute("""
                INSERT INTO public.recipe_ingredients (recipe_id, ingredient_id, qty_used)
                VALUES (%s, %s, %s)
                RETURNING id
            """, (recipe_id, body.ingredient_id, body.qty_used))
            new_id = cur.fetchone()[0]
        conn.commit()
        return {"id": str(new_id), "status": "added"}
    finally:
        conn.close()


@router.delete("/{recipe_id}/ingredients/{item_id}")
def remove_recipe_ingredient(recipe_id: str, item_id: str):
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM public.recipe_ingredients WHERE id = %s AND recipe_id = %s",
                (item_id, recipe_id)
            )
            if cur.rowcount == 0:
                raise HTTPException(404, "Recipe ingredient not found")
        conn.commit()
        return {"status": "removed"}
    finally:
        conn.close()


# ── Import from POS Menu (Phase 33) ─────────────────────────

@router.post("/import-from-menu")
def import_recipes_from_menu(
    branch_code: str = "thawi_watthana",
    min_qty_sold: int = 1,
):
    """
    Phase 33 — นำเข้าเมนูจากข้อมูล POS อัตโนมัติ
    Query pos_sales_by_product → INSERT into recipes (skip ที่มีแล้ว)

    Args:
        branch_code: สาขา (default: thawi_watthana)
        min_qty_sold: เฉพาะเมนูที่ขายแล้วอย่างน้อย N จาน (default: 1)
    """
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            # ── ดึงเมนู unique จาก POS ─────────────────────────────
            cur.execute("""
                SELECT
                    product_name,
                    ROUND(AVG(NULLIF(avg_price, 0)), 2) AS selling_price,
                    MAX(category)                        AS category,
                    SUM(qty_sold)::int                   AS total_qty_sold
                FROM public.pos_sales_by_product
                WHERE branch_code = %s
                GROUP BY product_name
                HAVING SUM(qty_sold) >= %s
                ORDER BY SUM(qty_sold) DESC, product_name
            """, (branch_code, min_qty_sold))
            products = cur.fetchall()

            # ── ชื่อ recipe ที่มีใน DB แล้ว (lowercase) ────────────
            cur.execute("SELECT LOWER(name) FROM public.recipes")
            existing = {r[0] for r in cur.fetchall()}

            imported = 0
            skipped_existing = 0
            skipped_blank = 0

            for product_name, selling_price, category, total_qty in products:
                name_clean = (product_name or "").strip()
                if not name_clean:
                    skipped_blank += 1
                    continue
                if name_clean.lower() in existing:
                    skipped_existing += 1
                    continue

                cur.execute("""
                    INSERT INTO public.recipes (name, selling_price, category, notes)
                    VALUES (%s, %s, %s, %s)
                """, (
                    name_clean,
                    float(selling_price or 0),
                    category,
                    f"นำเข้าจากข้อมูล POS อัตโนมัติ (ขายแล้ว {total_qty} จาน)",
                ))
                existing.add(name_clean.lower())
                imported += 1

        conn.commit()
        return {
            "imported":           imported,
            "skipped_existing":   skipped_existing,
            "skipped_blank":      skipped_blank,
            "total_found":        len(products),
            "message": (
                f"นำเข้า {imported} เมนูใหม่ "
                f"(ข้าม {skipped_existing} ที่มีแล้ว)"
            ),
        }
    finally:
        conn.close()


# ── AI Link Ingredients (Phase 33) ──────────────────────────

@router.post("/{recipe_id}/ai-link-ingredients")
def ai_link_ingredients(
    recipe_id: str,
    apply: bool = False,
):
    """
    Phase 33 — AI แนะนำวัตถุดิบ + ปริมาณสำหรับ recipe

    1. ดึงชื่อ recipe + รายการ ingredients ทั้งหมดใน DB
    2. Claude Haiku วิเคราะห์ว่าเมนูนี้น่าจะใช้วัตถุดิบอะไร
    3. คืนรายการแนะนำ {ingredient_id, name, qty_used, unit}
    4. ถ้า apply=true → INSERT จริงใน recipe_ingredients (skip ที่มีแล้ว)
    """
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            # ── ดึงชื่อ recipe ───────────────────────────────────
            cur.execute(
                "SELECT name, selling_price FROM public.recipes WHERE id = %s",
                (recipe_id,)
            )
            row = cur.fetchone()
            if not row:
                raise HTTPException(404, "Recipe not found")
            recipe_name, selling_price = row[0], float(row[1] or 0)

            # ── ดึง ingredients ทั้งหมด ──────────────────────────
            cur.execute("""
                SELECT id, name, unit, price_per_unit
                FROM public.ingredients
                ORDER BY name
            """)
            all_ingredients = [
                {"id": str(r[0]), "name": r[1], "unit": r[2], "price": float(r[3] or 0)}
                for r in cur.fetchall()
            ]

            # ── ingredients ที่ link แล้ว (skip ถ้า apply) ───────
            cur.execute(
                "SELECT ingredient_id FROM public.recipe_ingredients WHERE recipe_id = %s",
                (recipe_id,)
            )
            already_linked = {str(r[0]) for r in cur.fetchall()}
    finally:
        conn.close()

    if not all_ingredients:
        raise HTTPException(400, "ยังไม่มีวัตถุดิบในระบบ — import จาก Stock ก่อน")

    # ── สร้าง ingredient list สำหรับ prompt ─────────────────
    ingr_list = "\n".join(
        f"- ID:{i['id']} | {i['name']} ({i['unit']}) ราคา฿{i['price']:.2f}"
        for i in all_ingredients
    )

    # Build the store-level context block from the store_context table.
    # This pulls TUM-curated knowledge (brand profile, menu structure,
    # customer behavior, atmosphere, structured menu JSON) and embeds it
    # before the per-call instructions. Failure to load context falls
    # through silently — AI still works, just less informed.
    try:
        from store_context_routes import build_context_prompt
        store_context = build_context_prompt()
    except Exception:
        logger.exception("store_context load failed — proceeding without it")
        store_context = ""

    system_prompt_intro = (
        "คุณเป็นผู้ช่วยเชฟร้านปิ้งย่าง+บาร์ดนตรี Mara Station\n"
        "─────────────────────────────────────────────\n"
        "**บริบทร้าน (สำคัญมาก ห้ามพลาด):**\n"
        "• ร้านดนตรีสด + ปิ้งย่างทานเล่นคู่กับเครื่องดื่ม\n"
        "• เมนูส่วนใหญ่ขาย 'ไม้ละ' หรือ 'แก้วละ' ลูกค้าสั่งทีละชิ้น\n"
        "• ไม่ใช่ร้านข้าว/ตามสั่ง — **ไม่มีข้าวเสิร์ฟพร้อมเมนูเดี่ยว**\n"
        "• ลูกค้าสั่งเครื่องดื่มแยก ไม่ใช่แถมในเมนูปิ้งย่าง\n"
        "• ผักย่าง/ผักเคียง เสิร์ฟเฉพาะเมนูชุด (10 ไม้ขึ้นไป) ไม่ใช่ไม้เดี่ยว\n"
        "\n"
        "**ใช้ราคาขายเป็นสัญญาณ:**\n"
        "• ฿10-50 = ของชิ้นเดี่ยว/แก้วเดี่ยว → ใช้วัตถุดิบหลัก 1 ตัวเท่านั้น\n"
        "• ฿100-300 = เมนูชุด/รวม → วัตถุดิบหลัก + ผักเคียง 1-2 ตัว\n"
        "• ฿300+ พร้อมคำว่า 'ชุด/Combo/รวม' = ชุดใหญ่ → 3-6 ตัว\n"
        "\n"
        "**ตัวอย่างที่ถูก:**\n"
        "• 'หมูสามชั้น ฿18' (ไม้เดี่ยว) → [หมูสามชั้น 1 ชิ้น] **เท่านั้น**\n"
        "• 'หมูสามชั้น 10 ไม้ ฿190' (ชุด) → [หมูสามชั้น 10 ชิ้น, ผักเคียง 2 ชิ้น]\n"
        "• 'เบียร์สิงห์ ฿120' (แก้วเดียว) → [เบียร์สิงห์ 1 ขวด] **เท่านั้น**\n"
        "• 'สิงห์โปร 3 ขวด ฿239' (โปร) → [เบียร์สิงห์ 3 ขวด]\n"
        "\n"
        "**ห้ามทำ:**\n"
        "• อย่าใส่ข้าวเปล่า/น้ำเปล่า/ผัก สำหรับเมนู ฿10-50 (ไม้เดี่ยว/แก้วเดี่ยว)\n"
        "• อย่าเดาเครื่องปรุง (น้ำจิ้ม ซอส) — เป็น overhead ไม่นับต่อจาน\n"
        "\n"
        "**ใช้ข้อมูลจาก STORE CONTEXT ด้านล่างเป็นแหล่งความจริงหลัก:**\n"
        "• section `menu_knowledge` บอกหมวดสินค้า + customer behavior\n"
        "• section `menu_structured` (JSON) คือ menu list + ingredient_keywords จริงจากร้าน\n"
        "• ถ้าเมนูใน prompt user ตรงกับ entry ใน menu_structured → ใช้ ingredient_keywords ตรงๆ\n"
        "\n"
        "ตอบเป็น JSON array เท่านั้น ห้ามมีข้อความอื่น"
    )

    if store_context:
        system_prompt = (
            f"━━━━━━━━ STORE CONTEXT ━━━━━━━━\n"
            f"{store_context}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"{system_prompt_intro}"
        )
    else:
        system_prompt = system_prompt_intro
    user_prompt = f"""เมนู: {recipe_name}
ราคาขาย: ฿{selling_price:.0f}

วัตถุดิบที่มีในระบบ:
{ingr_list}

เลือกเฉพาะวัตถุดิบที่ "ใช้จริงในจาน/แก้วนี้" — ตามบริบทร้านปิ้งย่าง+บาร์
• ราคา ฿10-50 → 1 ตัวเท่านั้น (วัตถุดิบหลัก)
• ราคา ฿100-300 → 1-3 ตัว
• ราคา ฿300+ พร้อม 'ชุด/Combo' → 3-6 ตัว

คืน JSON array รูปแบบ:
[
  {{"ingredient_id": "<ID>", "ingredient_name": "<ชื่อ>", "qty_used": <ปริมาณต่อจาน>, "unit": "<หน่วย>", "reason": "<เหตุผลสั้นๆ>"}}
]
qty_used = ปริมาณต่อ 1 จาน/แก้ว/ไม้ ใช้หน่วยเดียวกับ unit ของวัตถุดิบ"""

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise HTTPException(500, "ANTHROPIC_API_KEY not configured")

    payload = json.dumps({
        "model": "claude-haiku-4-5-20251001",
        "max_tokens": 1024,
        "system": system_prompt,
        "messages": [{"role": "user", "content": user_prompt}],
    }).encode("utf-8")

    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=payload,
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            raw = data["content"][0]["text"].strip()
            if "```" in raw:
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
                raw = raw.split("```")[0]
            suggestions = json.loads(raw.strip())
    except urllib.error.HTTPError as e:
        body_err = e.read().decode("utf-8", errors="replace")
        raise HTTPException(502, f"Claude API error {e.code}: {body_err}")
    except json.JSONDecodeError:
        raise HTTPException(502, "Claude returned invalid JSON")

    # ── Apply: INSERT จริงถ้า apply=true ──────────────────────
    applied = 0
    skipped = 0
    if apply:
        conn2 = get_db_conn()
        try:
            with conn2.cursor() as cur:
                for s in suggestions:
                    ing_id = s.get("ingredient_id")
                    qty    = float(s.get("qty_used") or 0)
                    if not ing_id or qty <= 0:
                        continue
                    if ing_id in already_linked:
                        skipped += 1
                        continue
                    cur.execute("""
                        INSERT INTO public.recipe_ingredients (recipe_id, ingredient_id, qty_used)
                        VALUES (%s, %s, %s)
                        ON CONFLICT DO NOTHING
                    """, (recipe_id, ing_id, qty))
                    already_linked.add(ing_id)
                    applied += 1
            conn2.commit()
        finally:
            conn2.close()

    return {
        "recipe_name":   recipe_name,
        "suggestions":   suggestions,
        "applied":       applied,
        "skipped":       skipped,
        "apply_hint":    "เพิ่ม ?apply=true เพื่อบันทึกวัตถุดิบเหล่านี้เข้า recipe จริง",
    }


# ── AI Suggest Endpoint ──────────────────────────────────────

@router.post("/ai-suggest")
def ai_suggest_menus(body: Optional[AISuggestRequest] = None):
    """
    ดู stock ที่มีในร้าน + วัตถุดิบ + สูตรที่มีอยู่
    → Claude แนะนำเมนูที่ทำได้พร้อม GP% โดยประมาณ

    Session 16 fix (2026-05-17): body now optional — frontend ส่ง POST
    เปล่าๆ ได้, ระบบจะใช้ default {branch_code: thawi_watthana,
    num_suggestions: 3}. แก้ HTTP 422 ที่เคยเกิดเพราะ pydantic require body.
    """
    if body is None:
        body = AISuggestRequest()
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            # 1. Get current stock (latest snapshot)
            cur.execute("""
                SELECT ii.item_name, ii.qty, ii.unit
                FROM public.pos_inventory_items ii
                JOIN public.pos_inventory_snapshots s ON s.id = ii.snapshot_id
                WHERE s.branch_code = %s
                ORDER BY s.snapshot_at DESC, ii.item_name
                LIMIT 100
            """, (body.branch_code,))
            stock_items = cur.fetchall()

            # 2. Get ingredients with prices
            cur.execute("""
                SELECT name, unit, price_per_unit, yield_pct
                FROM public.ingredients
                WHERE price_per_unit > 0
                ORDER BY name
            """)
            ingredients = cur.fetchall()

            # 3. Get existing recipes (to avoid suggesting duplicates)
            cur.execute("SELECT name FROM public.recipes ORDER BY name")
            existing_menus = [r[0] for r in cur.fetchall()]

    finally:
        conn.close()

    if not stock_items:
        raise HTTPException(404, "ไม่พบข้อมูล stock — กรุณา upload FoodStory stock ก่อน")

    # Build context for Claude
    stock_text = "\n".join(
        f"- {name}: {qty} {unit}" for name, qty, unit in stock_items
    )
    ingr_text = "\n".join(
        f"- {name} ({unit}): {price:.2f} บาท (Yield {yield_pct:.0f}%)"
        for name, unit, price, yield_pct in ingredients
    ) or "ยังไม่มีข้อมูลราคาวัตถุดิบ"

    existing_text = ", ".join(existing_menus) if existing_menus else "ยังไม่มี"

    system_prompt = (
        "คุณคือเชฟ AI ผู้ช่วยของร้านอาหารไทย มีความเชี่ยวชาญด้านการคิดเมนูและต้นทุน "
        "ตอบเป็นภาษาไทย กระชับ และมีประโยชน์สำหรับเจ้าของร้าน"
    )

    user_prompt = f"""วัตถุดิบที่มีในร้านตอนนี้:
{stock_text}

ราคาวัตถุดิบ (พร้อม Yield%):
{ingr_text}

เมนูที่มีอยู่แล้ว: {existing_text}

กรุณาแนะนำ {body.num_suggestions} เมนูที่:
1. ทำได้จากวัตถุดิบในร้านที่มีอยู่
2. ไม่ซ้ำกับเมนูที่มีอยู่แล้ว
3. น่าสนใจสำหรับร้านมาลาปิ้งย่าง

สำหรับแต่ละเมนู ให้ระบุ:
- ชื่อเมนู
- วัตถุดิบหลักที่ใช้ (จากรายการที่มี)
- ต้นทุนโดยประมาณ (บาท/จาน) — คำนวณจากราคาวัตถุดิบ
- ราคาขายที่แนะนำ
- GP% โดยประมาณ
- เหตุผลที่แนะนำเมนูนี้

ตอบในรูปแบบ JSON array เท่านั้น:
[
  {{
    "name": "ชื่อเมนู",
    "main_ingredients": ["วัตถุดิบ1", "วัตถุดิบ2"],
    "estimated_cost": 25.50,
    "suggested_price": 79,
    "estimated_gp_pct": 67.7,
    "reason": "เหตุผล"
  }}
]"""

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise HTTPException(500, "ANTHROPIC_API_KEY not configured")

    payload = json.dumps({
        "model": "claude-haiku-4-5-20251001",
        "max_tokens": 1024,
        "system": system_prompt,
        "messages": [{"role": "user", "content": user_prompt}],
    }).encode("utf-8")

    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=payload,
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            raw = data["content"][0]["text"].strip()
            # Strip markdown code block if present
            if "```" in raw:
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
                raw = raw.split("```")[0]
            suggestions = json.loads(raw.strip())
    except urllib.error.HTTPError as e:
        body_err = e.read().decode("utf-8", errors="replace")
        raise HTTPException(502, f"Claude API error {e.code}: {body_err}")
    except json.JSONDecodeError:
        raise HTTPException(502, "Claude returned invalid JSON")

    return {
        "suggestions": suggestions,
        "stock_items_used": len(stock_items),
        "ingredients_with_price": len(ingredients),
        "generated_at": datetime.now().isoformat(),
    }
