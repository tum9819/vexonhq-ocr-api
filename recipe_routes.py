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

class IngredientUpdate(BaseModel):
    name: Optional[str] = None
    unit: Optional[str] = None
    price_per_unit: Optional[float] = None
    yield_pct: Optional[float] = None
    category: Optional[str] = None

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
                SELECT id, name, unit, price_per_unit, yield_pct, category, source_item_id, created_at
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
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO public.ingredients (name, unit, price_per_unit, yield_pct, category)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING id
            """, (body.name, body.unit, body.price_per_unit, body.yield_pct, body.category))
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
    for ri_id, qty, name, unit, price, yield_pct in items:
        effective_yield = yield_pct / 100.0 if yield_pct > 0 else 1.0
        item_cost = float(qty) * float(price) / effective_yield
        total_cost += item_cost
        breakdown.append({
            "id": str(ri_id),
            "ingredient_name": name,
            "unit": unit,
            "qty_used": float(qty),
            "price_per_unit": float(price),
            "yield_pct": float(yield_pct),
            "item_cost": round(item_cost, 2),
        })
    return {"breakdown": breakdown, "total_cost": round(total_cost, 2)}


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
                    "cost_per_dish": cost,
                    "gp_pct": gp_pct,
                    "ingredient_count": len(cost_data["breakdown"]),
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


# ── AI Suggest Endpoint ──────────────────────────────────────

@router.post("/ai-suggest")
def ai_suggest_menus(body: AISuggestRequest):
    """
    ดู stock ที่มีในร้าน + วัตถุดิบ + สูตรที่มีอยู่
    → Claude แนะนำเมนูที่ทำได้พร้อม GP% โดยประมาณ
    """
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
