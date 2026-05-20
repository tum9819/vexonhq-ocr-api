"""
VEXONHQ — Menu.jpg OCR → store_context.menu_structured JSON (Session 28 Phase U2)
================================================================================
Reads a menu photo (e.g. /store-context/Menu.jpg) via GPT-4o Vision and
emits a structured JSON list of menus + price tiers + ingredient_keywords
that the AI Link prompt and slip categorizer can use as a lookup table.

Run manually after TUM updates the menu:

    cd vexonhq-ocr-api
    python tools/ocr_menu_to_json.py \\
        --image "C:/Users/rapee/Desktop/PJ-MARA/VPS-VEXONHQ/store-context/Menu.jpg" \\
        --out  menu_structured.json

Then the operator pastes the JSON into the /admin/store-context UI
(key = "menu_structured") OR runs the optional SQL update:

    UPDATE public.store_context
    SET content = '<paste JSON here>',
        updated_at = NOW()
    WHERE key = 'menu_structured';

Why a script + manual paste instead of a one-shot endpoint?
  - Menu.jpg lives on TUM's local disk, not in the cloud / Supabase
    Storage. A web endpoint would force TUM to upload first.
  - OCR is a one-time-per-menu-revision operation — automating it
    further isn't worth the complexity.
  - Manual paste lets TUM eyeball the JSON before it goes live
    (Claude sometimes hallucinates a price; a quick check saves a
    bad prompt baseline).
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
from pathlib import Path


MENU_VISION_PROMPT = """
You are extracting structured menu data from a Thai restaurant's menu
image. The restaurant is Mara Station — a music bar + grilled-skewer
(หม่าล่า) place where customers order individual skewers OR bundle
promos (e.g. "10 ไม้ ฟรี 1"). Some menus are drinks (beer bottles,
soju, spirits) served per-glass / per-bottle / per-crate.

Return PURE JSON in this exact shape (no markdown, no preamble):

{
  "menus": [
    {
      "name":              "เมนูตามที่ปรากฏในรูป (ภาษาไทย)",
      "category":          "skewer | drink_beer | drink_liquor | drink_soft | shared_food | promotion | addon",
      "price":             123,
      "unit":              "ไม้ | ขวด | ลัง | จาน | ชุด | แก้ว",
      "promotion": {
          "bundle_qty":   10,
          "bundle_price": 190,
          "free_qty":     1
      },
      "ingredient_keywords": [
          "คีย์เวิร์ดวัตถุดิบหลัก (เช่น 'หมูสามชั้น', 'singha', 'leo cold brew')",
          "ใช้ตรงกับชื่อใน ingredients master ของระบบ"
      ],
      "notes": "ข้อมูลเสริม เช่น 'มีให้เลือก 3 รส' / 'เสิร์ฟพร้อมน้ำจิ้ม' (ถ้ามี)"
    }
  ],
  "extraction_meta": {
      "image_filename": "...",
      "menus_count":    N,
      "confidence":     "high | medium | low"
  }
}

Rules:
  - Only the `name`, `category`, `price`, `unit` fields are required.
    Everything else is optional — omit (don't set to null) if not visible.
  - `promotion` is only present when the menu has a bundle promo
    (e.g. "10 ไม้ ฿190" or "3 ขวด ฿219"). Omit the field for non-promo menus.
  - `ingredient_keywords` should be 1-3 short strings that match how
    the ingredient would appear in invoice OCR data (lowercase ok).
  - For skewer menus: ingredient_keywords is the main protein/item
    ONLY. Do NOT add rice/sauce/veggies as keywords.
  - For drink menus: ingredient_keywords is the brand + variant
    (e.g. ["singha", "เบียร์สิงห์"]).
  - Numbers must be JSON numbers (strip commas, no quotes).
  - If a price isn't visible / is illegible, set price=0 and
    confidence="low".
""".strip()


def _b64_image(path: Path) -> tuple[str, str]:
    mime = "image/jpeg"
    if path.suffix.lower() in (".png",):
        mime = "image/png"
    elif path.suffix.lower() in (".webp",):
        mime = "image/webp"
    return base64.b64encode(path.read_bytes()).decode("utf-8"), mime


def ocr_menu_image(image_path: Path) -> dict:
    """Run GPT-4o Vision against the menu image. Returns parsed JSON dict."""
    # Lazy import so the script works without main.py loaded.
    from openai import OpenAI

    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY env var not set")
    client = OpenAI(api_key=api_key)

    b64, mime = _b64_image(image_path)
    data_url = f"data:{mime};base64,{b64}"
    model = os.environ.get("OPENAI_VISION_MODEL", "gpt-4o")

    resp = client.chat.completions.create(
        model=model,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": MENU_VISION_PROMPT},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            }
        ],
        response_format={"type": "json_object"},
        temperature=0.1,
        max_tokens=4000,
    )
    raw = (resp.choices[0].message.content or "{}").strip()
    if raw.startswith("```"):
        raw = raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    return json.loads(raw)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="OCR menu image → store_context JSON")
    parser.add_argument("--image", required=True, help="Path to Menu.jpg / PNG / WEBP")
    parser.add_argument("--out", default="menu_structured.json", help="Output JSON path")
    args = parser.parse_args(argv)

    image_path = Path(args.image)
    if not image_path.exists():
        print(f"❌ Image not found: {image_path}", file=sys.stderr)
        return 1

    print(f"📷 Reading {image_path} ({image_path.stat().st_size:,} bytes)...")
    try:
        parsed = ocr_menu_image(image_path)
    except Exception as exc:
        print(f"❌ OCR failed: {exc}", file=sys.stderr)
        return 1

    # Attach extraction meta even if Claude forgot.
    meta = parsed.setdefault("extraction_meta", {})
    meta["image_filename"] = image_path.name
    meta["menus_count"] = len(parsed.get("menus", []))

    out_path = Path(args.out)
    out_path.write_text(
        json.dumps(parsed, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"✅ Wrote {out_path} — {meta['menus_count']} menus")
    print(f"\n💡 Next: paste the JSON into /admin/store-context "
          f"(key=menu_structured) OR run:")
    print(
        "\n   UPDATE public.store_context\n"
        f"   SET content = $$<paste from {out_path}>$$, updated_at = NOW()\n"
        "   WHERE key = 'menu_structured';\n"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
