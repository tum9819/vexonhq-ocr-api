import os
import sys
import argparse
import psycopg2
from datetime import datetime, timezone
import boto3
from botocore.exceptions import ClientError

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
    if not url or not isinstance(url, str):
        return None
        
    url = url.strip()
    if not url:
        return None
        
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

def normalize_s3_key(bucket_id, key):
    """Normalize S3 key to bucket-relative name."""
    if key.startswith(f"{bucket_id}/"):
        return key[len(bucket_id)+1:]
    return key

def format_size(bytes_val):
    """Format size in bytes to a human-readable string."""
    if bytes_val >= 1024 * 1024 * 1024:
        return f"{bytes_val / (1024 * 1024 * 1024):.2f} GB"
    elif bytes_val >= 1024 * 1024:
        return f"{bytes_val / (1024 * 1024):.2f} MB"
    elif bytes_val >= 1024:
        return f"{bytes_val / 1024:.2f} KB"
    else:
        return f"{bytes_val} B"

def format_age(last_modified_dt):
    """Format the last modified date to a readable age string."""
    if not last_modified_dt:
        return "unknown age"
    now = datetime.now(timezone.utc)
    delta = now - last_modified_dt
    days = delta.days
    hours = delta.seconds // 3600
    if days > 0:
        return f"{days}d {hours}h ago"
    else:
        return f"{hours}h ago"

def build_keep_set(db_url):
    """Query storage.objects and application tables to build the complete KEEP set."""
    print("Step 1: Connecting to database to build KEEP set...")
    conn = psycopg2.connect(db_url)
    cur = conn.cursor()
    
    keep_set = set()
    
    # 1. Fetch indexed objects from storage.objects
    try:
        print("  Querying storage.objects...")
        cur.execute("SELECT bucket_id, name FROM storage.objects")
        rows = cur.fetchall()
        for bucket_id, name in rows:
            if bucket_id and name:
                keep_set.add((bucket_id, name.strip()))
        print(f"    Found {len(rows)} objects in storage.objects")
    except Exception as e:
        print(f"    Error querying storage.objects: {e}")
        conn.rollback()
        
    # 2. Discover potential columns dynamically in public schema matching specifications
    dynamic_columns = []
    try:
        cur.execute("""
            SELECT table_name, column_name
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND (
                column_name LIKE '%file_url%'
                OR column_name LIKE '%preview_url%'
                OR column_name LIKE '%storage_path%'
                OR column_name LIKE '%image_url%'
                OR column_name = 'raw_image_url'
                OR column_name = 'attachment_url'
              )
            ORDER BY table_name, column_name;
        """)
        dynamic_columns = cur.fetchall()
    except Exception as e:
        print(f"    Error finding columns in information_schema: {e}")
        conn.rollback()

    # Explicit table/columns to check to guarantee perfect coverage
    explicit_queries = [
        ("public.slips", "raw_image_url"),
        ("public.vendor_bills", "file_url"),
        ("public.vendor_bills", "attachment_url"),
        ("public.attachments", "file_url"),
        ("public.documents", "file_url"),
        ("public.recipes", "image_url"),
        ("web.\"Promotion\"", "imageUrl"),
        ("web.\"EventProgram\"", "imageUrl"),
        ("web.\"SocialProfile\"", "profileUrl"),
        ("web.\"GalleryItem\"", "url"),
        ("web.\"GalleryItem\"", "urlLg")
    ]
    
    # Deduplicate queries
    queries_to_run = set()
    for table_name, col_name in dynamic_columns:
        queries_to_run.add((f"public.{table_name}", col_name))
        
    for table, col in explicit_queries:
        queries_to_run.add((table, col))
        
    print(f"  Fetching distinct references from DB across {len(queries_to_run)} columns...")
    db_ref_count = 0
    for table, col in sorted(queries_to_run):
        try:
            if "." in table:
                schema, tab = table.split(".", 1)
            else:
                schema, tab = "public", table
            
            quoted_tab = tab if tab.startswith('"') else f'"{tab}"'
            quoted_col = col if col.startswith('"') else f'"{col}"'
            full_table_path = f"{schema}.{quoted_tab}"
            
            cur.execute(f"SELECT DISTINCT {quoted_col} FROM {full_table_path} WHERE {quoted_col} IS NOT NULL")
            rows = cur.fetchall()
            for (val,) in rows:
                if isinstance(val, str):
                    parsed = parse_storage_url(val)
                    if parsed:
                        bucket, path = parsed
                        keep_set.add((bucket, path))
                        db_ref_count += 1
        except Exception:
            # Quietly rollback and skip missing tables/columns
            conn.rollback()
            
    print(f"  Successfully loaded {db_ref_count} references from application DB tables.")
    print(f"  Total unique items in KEEP set: {len(keep_set)}")
    
    cur.close()
    conn.close()
    return keep_set

def delete_s3_objects_in_batches(s3_client, bucket_name, keys_to_delete):
    """Delete keys in S3 using delete_objects in batches of 1000."""
    total_deleted = 0
    batch_size = 1000
    for i in range(0, len(keys_to_delete), batch_size):
        batch = keys_to_delete[i:i + batch_size]
        objects_payload = [{"Key": key} for key in batch]
        
        # Log every single deletion
        for key in batch:
            print(f"[{bucket_name}] DELETING ghost S3 object: {key}")
            
        try:
            response = s3_client.delete_objects(
                Bucket=bucket_name,
                Delete={
                    "Objects": objects_payload,
                    "Quiet": True
                }
            )
            errors = response.get("Errors", [])
            deleted = response.get("Deleted", [])
            total_deleted += len(deleted)
            if errors:
                print(f"Warning: {len(errors)} errors occurred during batch deletion in '{bucket_name}':")
                for err in errors[:5]:
                    print(f"  - Key: {err.get('Key')}, Code: {err.get('Code')}, Message: {err.get('Message')}")
        except Exception as e:
            print(f"Error executing batch deletion in '{bucket_name}': {e}")
            
    return total_deleted

def main():
    load_env()
    
    parser = argparse.ArgumentParser(description="Cleanup ghost physical orphans in Supabase Storage's S3 backend")
    parser.add_argument("--apply", action="store_true", help="Apply deletions (default is dry-run)")
    args = parser.parse_args()
    
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("Error: DATABASE_URL environment variable is missing.")
        sys.exit(1)
        
    supabase_url = os.environ.get("SUPABASE_URL")
    if not supabase_url:
        print("Error: SUPABASE_URL environment variable is missing.")
        sys.exit(1)
        
    s3_access_key = os.environ.get("SUPABASE_S3_ACCESS_KEY_ID")
    s3_secret_key = os.environ.get("SUPABASE_S3_SECRET_ACCESS_KEY")
    s3_region = os.environ.get("SUPABASE_S3_REGION", "ap-southeast-1")
    
    if not s3_access_key or not s3_secret_key:
        print("Error: SUPABASE_S3_ACCESS_KEY_ID or SUPABASE_S3_SECRET_ACCESS_KEY is missing.")
        print("Please configure your S3 credentials in your environment or .env file before running.")
        sys.exit(1)
        
    endpoint_url = f"{supabase_url.rstrip('/')}/storage/v1/s3"
    
    # Initialize boto3 S3 Client
    s3_client = boto3.client(
        "s3",
        endpoint_url=endpoint_url,
        aws_access_key_id=s3_access_key,
        aws_secret_access_key=s3_secret_key,
        region_name=s3_region
    )
    
    # 1. Build KEEP set
    keep_set = build_keep_set(db_url)
    
    buckets = ["uploads", "marketer-images", "social-images", "gallery"]
    
    grand_total_s3_objects = 0
    grand_total_s3_bytes = 0
    grand_total_keep = 0
    grand_total_ghosts = 0
    grand_total_ghost_bytes = 0
    
    grand_deleted_count = 0
    grand_deleted_bytes = 0
    
    print("\nStep 2: Listing physical S3 objects and auditing per bucket...")
    
    for bucket in buckets:
        print(f"\nScanning S3 bucket '{bucket}'...")
        continuation_token = None
        s3_objects = []
        
        # Paginate S3 objects
        while True:
            list_kwargs = {"Bucket": bucket}
            if continuation_token:
                list_kwargs["ContinuationToken"] = continuation_token
            try:
                response = s3_client.list_objects_v2(**list_kwargs)
            except ClientError as e:
                print(f"Error listing bucket '{bucket}': {e}")
                break
                
            contents = response.get("Contents", [])
            s3_objects.extend(contents)
            
            if response.get("IsTruncated"):
                continuation_token = response.get("NextContinuationToken")
            else:
                break
                
        total_objects = len(s3_objects)
        total_bytes = sum(obj["Size"] for obj in s3_objects)
        
        keep_count = 0
        ghost_count = 0
        ghost_bytes = 0
        ghosts = []
        
        for obj in s3_objects:
            key = obj["Key"]
            rel_key = normalize_s3_key(bucket, key)
            
            # Check if this object is in our KEEP set
            if (bucket, rel_key) in keep_set:
                keep_count += 1
            else:
                ghost_count += 1
                ghost_bytes += obj["Size"]
                ghosts.append(obj)
                
        grand_total_s3_objects += total_objects
        grand_total_s3_bytes += total_bytes
        grand_total_keep += keep_count
        grand_total_ghosts += ghost_count
        grand_total_ghost_bytes += ghost_bytes
        
        print("=" * 80)
        print(f"BUCKET: {bucket}")
        print(f"  Total S3 Objects:   {total_objects}")
        print(f"  Total S3 Bytes:     {format_size(total_bytes)}")
        print(f"  # KEEP:             {keep_count}")
        print(f"  # GHOST:            {ghost_count} (orphan candidates)")
        print(f"  GHOST Total Bytes:  {format_size(ghost_bytes)}")
        
        if ghosts:
            print("  Sample Ghost Keys (up to 20):")
            # Sort ghosts by last modified descending
            sorted_ghosts = sorted(ghosts, key=lambda x: x.get("LastModified") or datetime.min, reverse=True)
            for g in sorted_ghosts[:20]:
                print(f"    - {g['Key']} ({format_size(g['Size'])}, Age: {format_age(g['LastModified'])})")
        else:
            print("  No ghost objects found in this bucket.")
        print("=" * 80)
        
        # If --apply is specified, perform deletion in batches of 1000
        if args.apply and ghosts:
            print(f"\n[APPLY] Deleting {ghost_count} ghost objects from '{bucket}' S3 bucket...")
            ghost_keys_to_delete = []
            for g in ghosts:
                # Double-safety verification
                rel_key = normalize_s3_key(bucket, g["Key"])
                if (bucket, rel_key) in keep_set:
                    print(f"  [SAFETY STOP] Key '{g['Key']}' matches KEEP set! Skipping deletion!")
                    continue
                ghost_keys_to_delete.append(g["Key"])
                
            if ghost_keys_to_delete:
                deleted_count = delete_s3_objects_in_batches(s3_client, bucket, ghost_keys_to_delete)
                print(f"  Successfully deleted {deleted_count} ghost objects from '{bucket}' S3 bucket.")
                grand_deleted_count += deleted_count
                grand_deleted_bytes += sum(g["Size"] for g in ghosts if g["Key"] in ghost_keys_to_delete)
            else:
                print(f"  No ghost objects to delete in '{bucket}'.")
                
    print("\n" + "=" * 80)
    print("GRAND TOTALS:")
    print(f"  Total S3 Objects:        {grand_total_s3_objects}")
    print(f"  Total S3 Bytes:          {format_size(grand_total_s3_bytes)}")
    print(f"  Total KEEP Objects:      {grand_total_keep}")
    print(f"  Total GHOST Objects:     {grand_total_ghosts}")
    print(f"  Total GHOST Bytes:       {format_size(grand_total_ghost_bytes)}")
    print("=" * 80)
    
    if args.apply:
        print("\nPOST-DELETION GRAND TOTALS (ESTIMATED):")
        print(f"  Total S3 Objects:        {grand_total_s3_objects - grand_deleted_count}")
        print(f"  Total S3 Bytes:          {format_size(grand_total_s3_bytes - grand_deleted_bytes)}")
        print(f"  Total KEEP Objects:      {grand_total_keep}")
        print(f"  Total GHOST Objects:     {grand_total_ghosts - grand_deleted_count}")
        print(f"  Total GHOST Bytes:       {format_size(grand_total_ghost_bytes - grand_deleted_bytes)}")
        print("=" * 80)

if __name__ == "__main__":
    main()
