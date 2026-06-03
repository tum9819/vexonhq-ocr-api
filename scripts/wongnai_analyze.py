import os
import re
import json
import psycopg2
import difflib

def get_db_url():
    """
    Read DATABASE_URL from .env (stripping BOM if present) or fallback to environment variables.
    """
    db_url = None
    if os.path.exists(".env"):
        with open(".env", "r", encoding="utf-8-sig") as f:
            for line in f:
                line = line.strip()
                if line.startswith("DATABASE_URL="):
                    db_url = line.split("=", 1)[1].strip()
                    break
    if not db_url:
        db_url = os.environ.get("DATABASE_URL")
    return db_url

def clean_thai_name(name):
    """
    Cleans name by:
    1. Extracting/stripping leading code prefix (e.g. A001).
    2. Truncating at the first Burmese character (\u1000-\u109f).
    3. Trimming and normalizing whitespace.
    """
    # Extract code prefix
    code_match = re.match(r'^([A-Z]{1,3}\d{2,3})\s*', name)
    code = code_match.group(1) if code_match else ""
    
    # Strip leading code
    if code_match:
        name = name[code_match.end():]
        
    # Truncate at first Burmese character
    burmese_match = re.search(r'[\u1000-\u109f]', name)
    if burmese_match:
        name = name[:burmese_match.start()]
        
    # Trim and normalize whitespace
    name = re.sub(r'\s+', ' ', name).strip()
    return code, name

def normalize_name(name):
    """
    Normalizes a name for matching fallback by:
    1. Removing code prefix.
    2. Truncating Burmese.
    3. Lowercasing.
    4. Removing all whitespaces and common punctuation/symbols.
    """
    # Remove code prefix
    name = re.sub(r'^[A-Z]{1,3}\d{2,3}\s*', '', name)
    # Truncate at Burmese
    burmese_match = re.search(r'[\u1000-\u109f]', name)
    if burmese_match:
        name = name[:burmese_match.start()]
    # Lowercase
    name = name.lower()
    # Remove all spaces and punctuation
    name = re.sub(r'\s+', '', name)
    name = re.sub(r'[🔥⭐\(\)\-\+\/,\.\*\\\'"“”’`!_?\[\]{}#%@&|~:;]', '', name)
    return name.strip()

def main():
    # 1. Load Wongnai menus
    menu_path = "wongnai_snapshot/menus.json"
    if not os.path.exists(menu_path):
        print(f"Error: {menu_path} not found.")
        return

    with open(menu_path, "r", encoding="utf-8") as f:
        menus_data = json.load(f)
    wongnai_raw = menus_data.get("menus", [])

    # Process Wongnai items
    wongnai_items = []
    for item in wongnai_raw:
        w_code, w_clean_name = clean_thai_name(item["name"])
        w_price = float(item["price"]) if item["price"] is not None else 0.0
        has_image = bool(item.get("image_key"))
        
        wongnai_items.append({
            "menu_id": item["menu_id"],
            "code": w_code or item["code"],
            "clean_name": w_clean_name,
            "price": w_price,
            "has_image_key": has_image,
            "menu_group_name": item.get("menu_group_name"),
            "norm_name": normalize_name(item["name"]),
            "orig_name": item["name"]
        })

    # 2. Load recipes from Database (Read-only)
    db_url = get_db_url()
    if not db_url:
        print("Error: DATABASE_URL not found in environment or .env file.")
        return

    conn = psycopg2.connect(db_url)
    try:
        with conn.cursor() as cur:
            # Query recipes
            cur.execute("SELECT id, name, selling_price, category, image_url FROM public.recipes")
            recipes_rows = cur.fetchall()
    finally:
        conn.close()

    # Process recipes
    recipes = []
    for r in recipes_rows:
        r_code, r_clean = clean_thai_name(r[1])
        r_price = float(r[2]) if r[2] is not None else 0.0
        
        recipes.append({
            "id": str(r[0]),
            "name": r[1],
            "code": r_code,
            "clean_name": r_clean,
            "selling_price": r_price,
            "category": r[3],
            "image_url": r[4],
            "norm_name": normalize_name(r[1])
        })

    # 3. Match items
    matched = []
    w_matched_ids = set()
    r_matched_ids = set()

    # Match Stage 1: Code prefix
    for w in wongnai_items:
        if w["code"]:
            for r in recipes:
                if r["id"] not in r_matched_ids and r["code"] and r["code"] == w["code"]:
                    matched.append({
                        "wongnai": w,
                        "recipe": r,
                        "match_type": "code"
                    })
                    w_matched_ids.add(w["menu_id"])
                    r_matched_ids.add(r["id"])
                    break

    # Match Stage 2: Exact normalized Thai-name match
    for w in wongnai_items:
        if w["menu_id"] in w_matched_ids:
            continue
        for r in recipes:
            if r["id"] not in r_matched_ids:
                if w["norm_name"] == r["norm_name"] and w["norm_name"] != "":
                    matched.append({
                        "wongnai": w,
                        "recipe": r,
                        "match_type": "name"
                    })
                    w_matched_ids.add(w["menu_id"])
                    r_matched_ids.add(r["id"])
                    break

    # Match Stage 3: Fuzzy normalized Thai-name match (ratio >= 0.8)
    for w in wongnai_items:
        if w["menu_id"] in w_matched_ids:
            continue
        best_ratio = 0
        best_recipe = None
        for r in recipes:
            if r["id"] not in r_matched_ids:
                ratio = difflib.SequenceMatcher(None, w["norm_name"], r["norm_name"]).ratio()
                if ratio > best_ratio:
                    best_ratio = ratio
                    best_recipe = r
                    
        if best_ratio >= 0.8 and best_recipe is not None:
            matched.append({
                "wongnai": w,
                "recipe": best_recipe,
                "match_type": "needs-confirm",
                "similarity_ratio": best_ratio
            })
            w_matched_ids.add(w["menu_id"])
            r_matched_ids.add(best_recipe["id"])

    # Collect unmatched
    wongnai_only = [w for w in wongnai_items if w["menu_id"] not in w_matched_ids]
    recipes_only = [r for r in recipes if r["id"] not in r_matched_ids]

    # 4. Summary counts
    price_diff_count = 0
    total_abs_diff = 0.0
    total_net_diff = 0.0
    matched_missing_image = 0

    for m in matched:
        w_price = m["wongnai"]["price"]
        r_price = m["recipe"]["selling_price"]
        diff = w_price - r_price
        if abs(diff) > 0.01:
            price_diff_count += 1
            total_abs_diff += abs(diff)
            total_net_diff += diff
        if not m["recipe"]["image_url"]:
            matched_missing_image += 1

    # 5. Write wongnai_snapshot/match.json (structured)
    match_output = {
        "summary": {
            "total_wongnai_items": len(wongnai_items),
            "total_recipes": len(recipes),
            "matched_count": len(matched),
            "price_diff_count": price_diff_count,
            "total_price_diff_abs": round(total_abs_diff, 2),
            "total_price_diff_net": round(total_net_diff, 2),
            "matched_missing_image_count": matched_missing_image,
            "wongnai_only_count": len(wongnai_only),
            "recipes_only_count": len(recipes_only)
        },
        "matched": [
            {
                "wongnai_code": m["wongnai"]["code"],
                "wongnai_name": m["wongnai"]["clean_name"],
                "wongnai_price": m["wongnai"]["price"],
                "wongnai_group": m["wongnai"]["menu_group_name"],
                "recipe_id": m["recipe"]["id"],
                "recipe_name": m["recipe"]["clean_name"],
                "recipe_price": m["recipe"]["selling_price"],
                "recipe_category": m["recipe"]["category"],
                "price_diff": round(m["wongnai"]["price"] - m["recipe"]["selling_price"], 2),
                "has_recipe_image": bool(m["recipe"]["image_url"]),
                "match_type": m["match_type"],
                "similarity_ratio": m.get("similarity_ratio", 1.0)
            }
            for m in matched
        ],
        "wongnai_only": [
            {
                "code": w["code"],
                "name": w["clean_name"],
                "price": w["price"],
                "menu_group_name": w["menu_group_name"]
            }
            for w in wongnai_only
        ],
        "recipes_only": [
            {
                "id": r["id"],
                "code": r["code"],
                "name": r["clean_name"],
                "selling_price": r["selling_price"],
                "category": r["category"]
            }
            for r in recipes_only
        ]
    }

    os.makedirs("wongnai_snapshot", exist_ok=True)
    with open("wongnai_snapshot/match.json", "w", encoding="utf-8") as f:
        json.dump(match_output, f, ensure_ascii=False, indent=2)

    # 6. Write wongnai_snapshot/REPORT.md (human-readable)
    report_lines = [
        "# Wongnai & Recipe Menu Match Analysis Report",
        "",
        "## Summary Counts",
        f"- **Total Wongnai items**: {len(wongnai_items)}",
        f"- **Total Database Recipes**: {len(recipes)}",
        f"- **Matched items**: {len(matched)}",
        f"- **Matched items with price differences**: {price_diff_count}",
        f"- **Total price difference (absolute)**: {total_abs_diff:.2f} THB",
        f"- **Total price difference (net)**: {total_net_diff:.2f} THB",
        f"- **Matched recipes missing an image**: {matched_missing_image}",
        f"- **Wongnai-only items (not in recipes)**: {len(wongnai_only)}",
        f"- **Recipes-only items (not in Wongnai)**: {len(recipes_only)}",
        "",
        "## Matched Pairs",
        "| Code | Name | Wongnai Price | Current Selling Price | Difference | Has Image? | Match Type |",
        "| :--- | :--- | :---: | :---: | :---: | :---: | :---: |"
    ]

    for m in matched:
        w_code = m["wongnai"]["code"] or "-"
        name = m["wongnai"]["clean_name"]
        w_price = m["wongnai"]["price"]
        r_price = m["recipe"]["selling_price"]
        diff = w_price - r_price
        diff_str = f"+{diff:.2f}" if diff > 0 else f"{diff:.2f}" if diff < 0 else "0.00"
        has_img = "Yes" if m["recipe"]["image_url"] else "No"
        m_type = m["match_type"]
        if m_type == "needs-confirm":
            m_type = f"needs-confirm ({m['similarity_ratio']:.2f})"
        report_lines.append(f"| {w_code} | {name} | {w_price:.2f} | {r_price:.2f} | {diff_str} | {has_img} | {m_type} |")

    report_lines.extend([
        "",
        "## Wongnai-only Items (To be added / unmatched)",
        "| Code | Name | Price | Menu Group |",
        "| :--- | :--- | :---: | :--- |"
    ])
    for w in wongnai_only:
        w_code = w["code"] or "-"
        report_lines.append(f"| {w_code} | {w['clean_name']} | {w['price']:.2f} | {w['menu_group_name']} |")

    report_lines.extend([
        "",
        "## Recipes-only Items (Not in Wongnai menu)",
        "| Code | Name | Selling Price | Category |",
        "| :--- | :--- | :---: | :--- |"
    ])
    for r in recipes_only:
        r_code = r["code"] or "-"
        report_lines.append(f"| {r_code} | {r['clean_name']} | {r['selling_price']:.2f} | {r['category']} |")

    with open("wongnai_snapshot/REPORT.md", "w", encoding="utf-8") as f:
        f.write("\n".join(report_lines) + "\n")

    print(f"Analysis Complete.")
    print(f"Matched count: {len(matched)}")
    print(f"Prices differ: {price_diff_count}")
    print(f"Matched recipes missing image: {matched_missing_image}")

if __name__ == "__main__":
    main()
