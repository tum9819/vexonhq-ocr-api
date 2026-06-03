import os
import re
import sys
import json
import uuid
import time
import decimal
import datetime
import psycopg2
import boto3
import urllib.request
import urllib.error
from datetime import timezone

def load_env(env_path=".env"):
    """Reads a local .env file and populates os.environ if key is not already set."""
    if not os.path.exists(env_path):
        return
    with open(env_path, "r", encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, val = line.split("=", 1)
                key = key.strip()
                val = val.strip().strip("'\"")
                if key and key not in os.environ:
                    os.environ[key] = val

def serialize_db_val(val):
    """Serialize database types to JSON compatible formats."""
    if val is None:
        return None
    if isinstance(val, decimal.Decimal):
        return float(val)
    if isinstance(val, (datetime.datetime, datetime.date)):
        return val.isoformat()
    if isinstance(val, uuid.UUID):
        return str(val)
    return val

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

# Globals for image key mappings
menu_by_code_name = {}
menu_by_name = {}

def index_menus(menus):
    """Indexes menus list by (code, name) and name for fast lookup."""
    menu_by_code_name.clear()
    menu_by_name.clear()
    for item in menus:
        w_code, w_clean_name = clean_thai_name(item["name"])
        code = w_code or item.get("code") or ""
        key = (code, w_clean_name)
        if key not in menu_by_code_name:
            menu_by_code_name[key] = []
        menu_by_code_name[key].append(item)
        
        if w_clean_name not in menu_by_name:
            menu_by_name[w_clean_name] = []
        menu_by_name[w_clean_name].append(item)

# Caching for live menus fetch to avoid duplicate network calls
_cached_live_menus = None

def get_fresh_image_keys():
    """Fetch fresh menu signatures from the live menus API."""
    global _cached_live_menus
    if _cached_live_menus is not None:
        return _cached_live_menus
        
    url = "https://api.foodstory.co/v2/menus"
    headers = {
        "x-api-key": "fkkaNxYr6dyaabylBBpI2RlKPpMntHz5f5qEAtj6",
        "source": "WN",
        "branch-uuid": "8cc82c7a-6bdf-43bc-b59c-4086e2bb30b8",
        "User-Agent": "VEXONHQ-OpsBot (vexonhq.com, 1.0)"
    }
    print("Re-fetching fresh menus from live API to resolve 403...")
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            data = json.loads(response.read().decode('utf-8'))
            menus = data.get("menus", [])
            print(f"Successfully fetched {len(menus)} fresh menus from live API.")
            _cached_live_menus = menus
            return menus
    except Exception as e:
        print(f"Warning: Failed to fetch from live menus API: {e}")
        return None

def download_and_upload_image(recipe_id, item_code, item_name, initial_image_key, s3_client, bucket_name, supabase_url):
    """
    Downloads image from foodstory, retries on failure, handles signature refreshes on 403,
    and uploads the image to Supabase storage.
    """
    image_key = initial_image_key
    max_retries = 3
    
    for attempt in range(1, max_retries + 1):
        if not image_key:
            return None, "No image key available"
            
        url = f"https://images-api.foodstory.co/{image_key}"
        print(f"Downloading image for '{item_name}' (attempt {attempt})...")
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        
        try:
            with urllib.request.urlopen(req, timeout=15) as response:
                img_data = response.read()
                content_type = response.info().get_content_type()
                
                # Determine extension
                if content_type == 'image/webp':
                    ext = 'webp'
                elif content_type == 'image/jpeg':
                    ext = 'jpg'
                elif content_type == 'image/png':
                    ext = 'png'
                else:
                    ext = 'webp'
                    
                # Upload to S3
                s3_key = f"menu/{recipe_id}.{ext}"
                s3_client.put_object(
                    Bucket=bucket_name,
                    Key=s3_key,
                    Body=img_data,
                    ContentType=content_type
                )
                
                public_url = f"{supabase_url.rstrip('/')}/storage/v1/object/public/{bucket_name}/{s3_key}"
                print(f"Successfully uploaded {s3_key} (Type: {content_type})")
                return public_url, None
                
        except urllib.error.HTTPError as e:
            print(f"HTTP Error {e.code} on attempt {attempt}: {e.reason}")
            if e.code in (401, 403):
                print("Received 403/401. Attempting to refresh image_key...")
                fresh_menus = get_fresh_image_keys()
                if fresh_menus:
                    index_menus(fresh_menus)
                    candidates = menu_by_code_name.get((item_code, item_name))
                    if not candidates and item_name in menu_by_name:
                        candidates = menu_by_name[item_name]
                    if candidates:
                        new_image_key = candidates[0].get("image_key")
                        if new_image_key and new_image_key != image_key:
                            print(f"Updated image_key to fresh signature: {new_image_key[:40]}...")
                            image_key = new_image_key
            time.sleep(1)
        except Exception as e:
            print(f"Error on attempt {attempt}: {e}")
            time.sleep(1)
            
    return None, "Failed after max retries"

def main():
    load_env()
    
    # Retrieve env parameters
    database_url = os.environ.get("DATABASE_URL")
    supabase_url = os.environ.get("SUPABASE_URL")
    aws_access_key = os.environ.get("SUPABASE_S3_ACCESS_KEY_ID")
    aws_secret_key = os.environ.get("SUPABASE_S3_SECRET_ACCESS_KEY")
    aws_region = os.environ.get("SUPABASE_S3_REGION", "ap-southeast-1")
    
    if not database_url or not supabase_url or not aws_access_key or not aws_secret_key:
        print("CRITICAL: Missing environment variables in .env file.", file=sys.stderr)
        sys.exit(1)
        
    print("=" * 60)
    print("WONGNAI MENU APPLY SYSTEM (PHASE 2)")
    print(f"Target Database: {database_url.split('@')[-1] if '@' in database_url else 'Unknown'}")
    print("=" * 60)

    # -------------------------------------------------------------
    # (A) BACKUP recipes table to wongnai_snapshot/recipes_backup_<UTC timestamp>.json
    # -------------------------------------------------------------
    print("\n--- STEP A: BACKUP ---")
    try:
        conn = psycopg2.connect(database_url)
        cur = conn.cursor()
        cur.execute("SELECT * FROM public.recipes")
        columns = [desc[0] for desc in cur.description]
        rows = cur.fetchall()
        
        recipes_backup = []
        for row in rows:
            row_dict = dict(zip(columns, row))
            serialized_row = {k: serialize_db_val(v) for k, v in row_dict.items()}
            recipes_backup.append(serialized_row)
            
        cur.close()
        conn.close()
        
        timestamp = datetime.datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        backup_dir = "wongnai_snapshot"
        os.makedirs(backup_dir, exist_ok=True)
        backup_path = os.path.join(backup_dir, f"recipes_backup_{timestamp}.json")
        
        with open(backup_path, "w", encoding="utf-8") as f:
            json.dump(recipes_backup, f, indent=2, ensure_ascii=False)
            
        print(f"SUCCESS: Backed up {len(recipes_backup)} recipes to {backup_path}")
    except Exception as e:
        print(f"CRITICAL ERROR: Backup failed: {e}", file=sys.stderr)
        print("ABORTING: No database writes will be executed.", file=sys.stderr)
        sys.exit(1)

    # Load match.json and menus.json
    try:
        with open("wongnai_snapshot/match.json", "r", encoding="utf-8") as f:
            match_data = json.load(f)
        with open("wongnai_snapshot/menus.json", "r", encoding="utf-8") as f:
            menus_data = json.load(f)
        index_menus(menus_data.get("menus", []))
    except Exception as e:
        print(f"CRITICAL ERROR: Failed to load snapshots: {e}", file=sys.stderr)
        sys.exit(1)

    # Set up S3 client
    endpoint_url = supabase_url.rstrip("/") + "/storage/v1/s3"
    s3_client = boto3.client(
        "s3",
        endpoint_url=endpoint_url,
        aws_access_key_id=aws_access_key,
        aws_secret_access_key=aws_secret_key,
        region_name=aws_region,
    )
    bucket_name = "menu-images"

    # Create storage bucket and make it public in DB
    try:
        response = s3_client.list_buckets()
        existing_buckets = [b['Name'] for b in response.get('Buckets', [])]
        if bucket_name not in existing_buckets:
            print(f"Creating bucket '{bucket_name}'...")
            s3_client.create_bucket(Bucket=bucket_name)
        
        # Ensure it is public in the database
        conn = psycopg2.connect(database_url)
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute("UPDATE storage.buckets SET public = true WHERE id = %s", (bucket_name,))
        conn.close()
    except Exception as e:
        print(f"Warning/Error preparing storage bucket: {e}")

    # Connect to database for applying updates
    conn = psycopg2.connect(database_url)
    conn.autocommit = True
    
    # -------------------------------------------------------------
    # (1) UPDATE selling_price for the 16 matched items whose price differs
    # -------------------------------------------------------------
    print("\n--- STEP 1: UPDATE PRICES ---")
    prices_updated = 0
    price_fail_count = 0
    
    for m in match_data["matched"]:
        recipe_id = m["recipe_id"]
        wongnai_price = m["wongnai_price"]
        wongnai_name = m["wongnai_name"]
        
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT name, selling_price FROM public.recipes WHERE id = %s", (recipe_id,))
                row = cur.fetchone()
                if not row:
                    print(f"Warning: Recipe ID {recipe_id} not found in DB.")
                    continue
                
                db_name, db_price = row
                db_price_float = float(db_price) if db_price is not None else 0.0
                
                if abs(db_price_float - float(wongnai_price)) > 0.01:
                    cur.execute(
                        "UPDATE public.recipes SET selling_price = %s, updated_at = now() WHERE id = %s",
                        (wongnai_price, recipe_id)
                    )
                    print(f"[PRICE UPDATE] ID: {recipe_id} | Name: {db_name} | Price: {db_price_float} -> {wongnai_price}")
                    prices_updated += 1
        except Exception as e:
            print(f"Error updating price for ID {recipe_id}: {e}")
            price_fail_count += 1

    print(f"Price update completed. {prices_updated} updated, {price_fail_count} failed.")

    # -------------------------------------------------------------
    # (2) ดึงรูป → อัป Supabase → set image_url (matched 125)
    # -------------------------------------------------------------
    print("\n--- STEP 2: DOWNLOAD & UPLOAD IMAGES FOR MATCHED ITEMS ---")
    images_uploaded = 0
    images_skipped = 0
    images_failed = 0
    
    for m in match_data["matched"]:
        recipe_id = m["recipe_id"]
        w_code = m["wongnai_code"] or ""
        w_name = m["wongnai_name"]
        
        # Check if already points to Supabase
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT image_url FROM public.recipes WHERE id = %s", (recipe_id,))
                row = cur.fetchone()
                if row and row[0] and "osneubnwghvbwyazaedo.supabase.co" in row[0]:
                    print(f"[IMAGE SKIP] ID: {recipe_id} ({w_name}) already has Supabase image.")
                    images_skipped += 1
                    continue
        except Exception as e:
            print(f"Error checking image_url for ID {recipe_id}: {e}")
            continue
            
        # Get image key
        candidates = menu_by_code_name.get((w_code, w_name))
        if not candidates and w_name in menu_by_name:
            candidates = menu_by_name[w_name]
            
        if not candidates:
            print(f"Warning: No menu entry found in menus.json for matched item code={w_code}, name={w_name}")
            images_failed += 1
            continue
            
        menu_item = candidates[0]
        image_key = menu_item.get("image_key")
        
        if not image_key:
            print(f"Warning: No image_key for matched item: {w_name}")
            images_failed += 1
            continue
            
        # Download and upload
        public_url, err = download_and_upload_image(
            recipe_id=recipe_id,
            item_code=w_code,
            item_name=w_name,
            initial_image_key=image_key,
            s3_client=s3_client,
            bucket_name=bucket_name,
            supabase_url=supabase_url
        )
        
        if public_url:
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE public.recipes SET image_url = %s, updated_at = now() WHERE id = %s",
                        (public_url, recipe_id)
                    )
                print(f"[IMAGE SET] ID: {recipe_id} | Name: {w_name} | URL: {public_url}")
                images_uploaded += 1
            except Exception as e:
                print(f"Error updating image_url in DB for ID {recipe_id}: {e}")
                images_failed += 1
        else:
            print(f"[IMAGE FAILED] ID: {recipe_id} | Name: {w_name} | Error: {err}")
            images_failed += 1

    # -------------------------------------------------------------
    # (3) INSERT Wongnai-only items (except noise)
    # -------------------------------------------------------------
    print("\n--- STEP 3: INSERT WONGNAI-ONLY ITEMS ---")
    new_inserted = 0
    new_skipped = 0
    new_failed = 0
    
    for w in match_data["wongnai_only"]:
        orig_name = w["name"]
        w_price = w["price"]
        w_group = w["menu_group_name"]
        w_code = w["code"] or ""
        
        # Clean name and group
        _, clean_name = clean_thai_name(orig_name)
        _, clean_category = clean_thai_name(w_group)
        
        # Filter noise items
        if clean_name in ("…", "นำเหล้ามา มีค่าเปิดเหล้า 100 บาท"):
            print(f"[NOISE EXCLUDED] Name: {clean_name}")
            continue
            
        # Check if already exists in DB
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM public.recipes WHERE name = %s", (clean_name,))
                row = cur.fetchone()
                if row:
                    print(f"[INSERT SKIP] Name: {clean_name} already exists in DB.")
                    new_skipped += 1
                    continue
        except Exception as e:
            print(f"Error checking duplicate for {clean_name}: {e}")
            new_failed += 1
            continue
            
        # Lookup image key
        candidates = menu_by_code_name.get((w_code, clean_name))
        if not candidates and clean_name in menu_by_name:
            candidates = menu_by_name[clean_name]
            
        image_key = None
        if candidates:
            image_key = candidates[0].get("image_key")
            
        # Generate new UUID
        new_recipe_id = str(uuid.uuid4())
        
        # Upload image if available
        public_url = None
        if image_key:
            public_url, err = download_and_upload_image(
                recipe_id=new_recipe_id,
                item_code=w_code,
                item_name=clean_name,
                initial_image_key=image_key,
                s3_client=s3_client,
                bucket_name=bucket_name,
                supabase_url=supabase_url
            )
            if err:
                print(f"Warning: Failed to upload image for new item '{clean_name}': {err}")
                # We can still insert the recipe without image or log it as partial failure
                
        # Insert new recipe
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO public.recipes (id, name, selling_price, category, image_url, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s, now(), now())
                    """,
                    (new_recipe_id, clean_name, w_price, clean_category, public_url)
                )
            print(f"[INSERT SUCCESS] ID: {new_recipe_id} | Name: {clean_name} | Price: {w_price} | Category: {clean_category} | Image: {public_url}")
            new_inserted += 1
        except Exception as e:
            print(f"Error inserting new recipe {clean_name}: {e}")
            new_failed += 1

    # -------------------------------------------------------------
    # VERIFY Results
    # -------------------------------------------------------------
    print("\n--- STEP 4: VERIFICATION ---")
    verified_matched_prices = 0
    supabase_image_urls_count = 0
    total_db_recipes = 0
    sample_urls = []
    
    try:
        with conn.cursor() as cur:
            # Total recipes count
            cur.execute("SELECT COUNT(*) FROM public.recipes")
            total_db_recipes = cur.fetchone()[0]
            
            # Count images pointing to Supabase
            cur.execute("SELECT image_url FROM public.recipes WHERE image_url LIKE %s", ("%osneubnwghvbwyazaedo.supabase.co%",))
            supabase_image_urls_count = cur.rowcount
            
            # Grab some sample URLs for testing
            sample_rows = cur.fetchmany(3)
            for r in sample_rows:
                if r[0]:
                    sample_urls.append(r[0])
                    
            # Check price updates
            matched_recipe_ids = [m["recipe_id"] for m in match_data["matched"]]
            if matched_recipe_ids:
                cur.execute(
                    "SELECT id, selling_price FROM public.recipes WHERE id IN %s",
                    (tuple(matched_recipe_ids),)
                )
                db_prices = {str(row[0]): float(row[1]) for row in cur.fetchall()}
                
                for m in match_data["matched"]:
                    rid = m["recipe_id"]
                    wn_p = float(m["wongnai_price"])
                    if rid in db_prices and abs(db_prices[rid] - wn_p) < 0.01:
                        verified_matched_prices += 1
                        
        print(f"Total database recipes: {total_db_recipes}")
        print(f"Recipes with Supabase image URLs: {supabase_image_urls_count}")
        print(f"Verified prices matching Wongnai: {verified_matched_prices} / {len(match_data['matched'])}")
        
        # Test HTTP GET on sample URLs
        print("\nTesting HTTP GET on sample Supabase image URLs:")
        for surl in sample_urls:
            req = urllib.request.Request(surl, headers={'User-Agent': 'Mozilla/5.0'})
            try:
                with urllib.request.urlopen(req, timeout=5) as response:
                    print(f" - {surl} -> Status {response.status} (OK)")
            except Exception as se:
                print(f" - {surl} -> Failed: {se}")
                
    except Exception as e:
        print(f"Error during verification step: {e}")

    conn.close()

    print("\n" + "=" * 60)
    print("APPLY OPERATION COMPLETED")
    print("=" * 60)
    print(f"Backup file:        {backup_path}")
    print(f"Prices updated:     {prices_updated}")
    print(f"Images uploaded:    {images_uploaded}")
    print(f"Images skipped:     {images_skipped}")
    print(f"Images failed:      {images_failed}")
    print(f"New items inserted: {new_inserted}")
    print(f"New items skipped:  {new_skipped}")
    print(f"New items failed:   {new_failed}")
    print("=" * 60)

if __name__ == "__main__":
    main()
