import os
import sys
import psycopg2
from datetime import datetime

def load_env():
    """Load environment variables from .env file, handling UTF-8 BOM if present."""
    if os.path.exists(".env"):
        with open(".env", "r", encoding="utf-8-sig") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    key, val = line.split("=", 1)
                    key = key.strip()
                    val = val.strip().strip('"').strip("'")
                    os.environ[key] = val

def parse_storage_url(url):
    """
    Parse a Supabase storage URL or relative path.
    Returns a tuple of (bucket_id, storage_path) or None.
    """
    if not url:
        return None
        
    url = url.strip()
    
    # Common Supabase Storage URLs
    if "/storage/v1/object/public/" in url:
        parts = url.split("/storage/v1/object/public/", 1)[1]
    elif "/storage/v1/object/sign/" in url:
        parts = url.split("/storage/v1/object/sign/", 1)[1]
    elif "/storage/v1/object/" in url:
        parts = url.split("/storage/v1/object/", 1)[1]
    else:
        # Check if it is a relative path starting with one of the buckets
        for b in ['uploads', 'marketer-images', 'social-images', 'gallery']:
            if url.startswith(f"{b}/"):
                return b, url[len(b)+1:]
        return None
        
    bucket, _, path = parts.partition("/")
    if "?" in path:
        path = path.split("?", 1)[0]
    return bucket, path.strip()

def main():
    load_env()
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("Error: DATABASE_URL not found in .env or environment")
        sys.exit(1)
        
    # Check if Supabase client credentials are present
    supabase_url = os.environ.get("SUPABASE_URL")
    supabase_key = os.environ.get("SUPABASE_SERVICE_KEY")
    
    has_credentials = bool(supabase_url and supabase_key)
    
    # Connect to the database
    conn = psycopg2.connect(db_url)
    cur = conn.cursor()
    
    # 1. Fetch all referenced file paths/URLs from actual record columns
    referenced_paths = {
        'uploads': set(),
        'marketer-images': set(),
        'social-images': set(),
        'gallery': set()
    }
    
    # We query both public and web schema columns.
    # Note that column names are double-quoted in SQL queries below to preserve case-sensitivity.
    queries = [
        ("public.vendor_bills", "attachment_url"),
        ("public.slips", "raw_image_url"),
        ("public.attachments", "file_url"),
        ("public.documents", "file_url"),
        ("public.recipes", "image_url"),
        ("web.\"Promotion\"", "imageUrl"),
        ("web.\"EventProgram\"", "imageUrl"),
        ("web.\"SocialProfile\"", "profileUrl"),
        ("web.\"GalleryItem\"", "url"),
        ("web.\"GalleryItem\"", "urlLg")
    ]
    
    print("Step 1: Fetching database references to storage objects...")
    total_db_references = 0
    for table, col in queries:
        try:
            cur.execute(f'SELECT DISTINCT "{col}" FROM {table} WHERE "{col}" IS NOT NULL')
            rows = cur.fetchall()
            print(f"  Table {table:<25} | Column {col:<15} | {len(rows)} distinct non-null values")
            for (url,) in rows:
                parsed = parse_storage_url(url)
                if parsed:
                    bucket, path = parsed
                    if bucket not in referenced_paths:
                        referenced_paths[bucket] = set()
                    referenced_paths[bucket].add(path)
                    total_db_references += 1
        except Exception as e:
            print(f"  Warning: Failed to query {table}.{col}: {e}")
            conn.rollback()
            
    print(f"Total distinct database references parsed: {total_db_references}\n")
    
    # 2. Fetch all indexed storage records from storage.objects
    print("Step 2: Fetching indexed objects from storage.objects table...")
    indexed_objects = {
        'uploads': {},
        'marketer-images': {},
        'social-images': {},
        'gallery': {}
    }
    
    try:
        cur.execute("""
            SELECT id, bucket_id, name, created_at, (metadata->>'size')::bigint 
            FROM storage.objects
        """)
        rows = cur.fetchall()
        print(f"Total indexed objects in storage.objects: {len(rows)}")
        for obj_id, bucket_id, name, created_at, size in rows:
            if bucket_id not in indexed_objects:
                indexed_objects[bucket_id] = {}
            indexed_objects[bucket_id][name] = {
                'id': obj_id,
                'created_at': created_at,
                'size': size or 0
            }
            
        for bucket, objs in indexed_objects.items():
            print(f"  Bucket {bucket:<15}: {len(objs)} indexed files, total size: {sum(o['size'] for o in objs.values()) / (1024*1024):.2f} MB")
    except Exception as e:
        print(f"Error querying storage.objects: {e}")
        conn.rollback()
    print("")
    
    physical_files = {
        'uploads': [],
        'marketer-images': [],
        'social-images': [],
        'gallery': []
    }
    
    # 3. List physical files
    if has_credentials:
        print("Step 3: Supabase Service Key found. Listing physical files via Storage API...")
        try:
            from supabase import create_client
            supabase = create_client(supabase_url, supabase_key)
            
            def list_all_files(bucket_name, prefix=""):
                files = []
                limit = 100
                offset = 0
                while True:
                    res = supabase.storage.from_(bucket_name).list(
                        path=prefix,
                        options={
                            "limit": limit,
                            "offset": offset,
                            "sortBy": {"column": "name", "order": "asc"}
                        }
                    )
                    if not res:
                        break
                    for item in res:
                        name = item.get("name")
                        if "id" not in item or item.get("id") is None:
                            sub_prefix = f"{prefix}/{name}" if prefix else name
                            files.extend(list_all_files(bucket_name, sub_prefix))
                        else:
                            full_path = f"{prefix}/{name}" if prefix else name
                            files.append({
                                "path": full_path,
                                "size": item.get("metadata", {}).get("size", 0) if item.get("metadata") else 0,
                                "created_at": item.get("created_at") or item.get("metadata", {}).get("created_at")
                            })
                    if len(res) < limit:
                        break
                    offset += limit
                return files
                
            for bucket in ['uploads', 'marketer-images', 'social-images', 'gallery']:
                print(f"  Listing bucket '{bucket}'...")
                files = list_all_files(bucket)
                physical_files[bucket] = files
                print(f"    Found {len(files)} physical files in '{bucket}'")
        except Exception as e:
            print(f"Error calling Storage API: {e}. Falling back to database-only mode.")
            has_credentials = False
    
    if not has_credentials:
        print("Step 3: [NOTICE] SUPABASE_SERVICE_KEY not found in local environment or Storage API failed.")
        print("        Running database-only dry-run using storage.objects as a proxy for physical files.\n")
        
        # In DB-only mode, we treat storage.objects as the list of "physical files"
        for bucket, objs in indexed_objects.items():
            for name, meta in objs.items():
                physical_files[bucket].append({
                    "path": name,
                    "size": meta['size'],
                    "created_at": meta['created_at']
                })
                
    # 4. Perform the audit / classification
    print("Step 4: Compiling audit report...")
    print("=" * 80)
    print("                     SUPABASE STORAGE AUDIT REPORT")
    print("=" * 80)
    
    if not has_credentials:
        print("NOTE: RUNNING IN DATABASE-ONLY MODE (NO SERVICE ROLE KEY).")
        print("      Physical files are estimated from the storage.objects index.")
        print("      Therefore, physical orphans not in the index cannot be listed locally.")
        print("-" * 80)
        
    total_physical_count = 0
    total_physical_bytes = 0
    total_indexed_count = 0
    total_orphan_unreferenced_count = 0
    total_orphan_unreferenced_bytes = 0
    total_orphan_referenced_count = 0
    total_stale_index_count = 0
    total_stale_index_bytes = 0
    
    for bucket in ['uploads', 'marketer-images', 'social-images', 'gallery']:
        p_list = physical_files.get(bucket, [])
        ref_set = referenced_paths.get(bucket, set())
        idx_dict = indexed_objects.get(bucket, {})
        
        # Classifications
        total_objects = len(p_list)
        total_bytes = sum(f['size'] for f in p_list)
        
        total_physical_count += total_objects
        total_physical_bytes += total_bytes
        
        indexed_count = 0
        orphan_unreferenced = [] # Physical file, NO storage.objects row, NOT referenced in DB
        orphan_referenced = []   # Physical file, NO storage.objects row, IS referenced in DB (must keep)
        stale_indexed = []       # Has storage.objects row, NOT referenced in DB (candidate for delete-indexed)
        
        for f in p_list:
            path = f['path']
            is_indexed = path in idx_dict
            is_referenced = path in ref_set
            
            if is_indexed:
                indexed_count += 1
                if not is_referenced:
                    stale_indexed.append(f)
            else:
                if is_referenced:
                    orphan_referenced.append(f)
                else:
                    orphan_unreferenced.append(f)
                    
        total_indexed_count += indexed_count
        total_orphan_unreferenced_count += len(orphan_unreferenced)
        total_orphan_unreferenced_bytes += sum(f['size'] for f in orphan_unreferenced)
        total_orphan_referenced_count += len(orphan_referenced)
        total_stale_index_count += len(stale_indexed)
        total_stale_index_bytes += sum(f['size'] for f in stale_indexed)
        
        # Sample candidates: if in database-only mode, candidates are the "stale_indexed" (in index, not in DB)
        # If in has_credentials mode, candidates are physical "orphan_unreferenced"
        candidates = orphan_unreferenced if has_credentials else stale_indexed
        candidate_size = sum(f['size'] for f in candidates)
        
        print(f"BUCKET: {bucket}")
        print(f"  Total Physical Objects: {total_objects}")
        print(f"  Total Bytes:            {total_bytes:,} bytes ({total_bytes / (1024*1024):.2f} MB)")
        print(f"  # Indexed (in index):   {indexed_count}")
        print(f"  # Orphan-Unreferenced:  {len(orphan_unreferenced)} (delete-candidates)")
        print(f"  # Orphan-But-Referenced: {len(orphan_referenced)} (must keep)")
        print(f"  # Indexed-Unreferenced: {len(stale_indexed)} (in index but not in DB; delete-candidates)")
        print(f"  Candidate Total Size:   {candidate_size / (1024*1024):.2f} MB")
        
        print(f"  Sample Candidates (up to 10):")
        if not candidates:
            print("    None found")
        else:
            for f in sorted(candidates, key=lambda x: x.get('created_at') or '', reverse=True)[:10]:
                created_str = f['created_at']
                if isinstance(created_str, datetime):
                    created_str = created_str.strftime("%Y-%m-%d %H:%M:%S")
                elif created_str:
                    created_str = str(created_str)[:19]
                else:
                    created_str = "Unknown date"
                print(f"    - {f['path']} ({f['size'] / 1024:.1f} KB, Created: {created_str})")
        print("-" * 80)
        
    print("GRAND SUMMARY:")
    print(f"  Total Physical Files Analyzed:       {total_physical_count}")
    print(f"  Total Physical Bytes Analyzed:       {total_physical_bytes / (1024*1024):.2f} MB")
    print(f"  Total Indexed Files:                 {total_indexed_count}")
    print(f"  Total Physical Orphans (unindexed):  {total_orphan_unreferenced_count + total_orphan_referenced_count}")
    print(f"    - Orphan & Unreferenced:           {total_orphan_unreferenced_count} ({total_orphan_unreferenced_bytes / (1024*1024):.2f} MB) -> DELETE CANDIDATES")
    print(f"    - Orphan & Referenced (Keep):      {total_orphan_referenced_count} -> MUST KEEP")
    print(f"  Total Indexed & Unreferenced (Stale): {total_stale_index_count} ({total_stale_index_bytes / (1024*1024):.2f} MB) -> DELETE CANDIDATES")
    print("=" * 80)
    
    # 5. Fetch and print the largest 10 objects overall
    print("LARGEST 10 OBJECTS OVERALL:")
    try:
        cur.execute("""
            SELECT bucket_id, name, (metadata->>'size')::bigint, created_at
            FROM storage.objects
            ORDER BY (metadata->>'size')::bigint DESC NULLS LAST
            LIMIT 10
        """)
        largest_rows = cur.fetchall()
        for idx, r in enumerate(largest_rows, 1):
            bucket_id, name, size, created_at = r
            size_str = f"{(size or 0) / (1024*1024):.2f} MB" if size else "Unknown size"
            created_str = created_at.strftime("%Y-%m-%d %H:%M:%S") if isinstance(created_at, datetime) else str(created_at)[:19]
            print(f"  {idx:2d}. [{bucket_id}] {name:<60} ({size_str}, Created: {created_str})")
    except Exception as e:
        print(f"  Error fetching largest objects: {e}")
        conn.rollback()
    print("=" * 80)
    
    cur.close()
    conn.close()

if __name__ == "__main__":
    main()
