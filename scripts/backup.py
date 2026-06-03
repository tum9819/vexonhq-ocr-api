#!/usr/bin/env python3
import os
import sys
import shutil
import datetime
import argparse
import subprocess
import json
import traceback
import urllib.request
import urllib.error

def send_discord_alert(message: str):
    webhook_url = os.environ.get("DISCORD_OPS_WEBHOOK_URL")
    if not webhook_url:
        # Try loading env first if not populated
        if os.path.exists(".env"):
            try:
                with open(".env", "r", encoding="utf-8-sig") as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith("#") and "=" in line:
                            k, val = line.split("=", 1)
                            if k.strip() == "DISCORD_OPS_WEBHOOK_URL":
                                webhook_url = val.strip().strip("'\"")
                                break
            except Exception:
                pass
    if not webhook_url:
        print("WARNING: DISCORD_OPS_WEBHOOK_URL not set, cannot send Discord alert.", file=sys.stderr)
        return
    
    payload = {"content": message[:1900]}
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        webhook_url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "VEXONHQ-OpsBot (vexonhq.com, 1.0)",
        },
        method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            if not (200 <= resp.status < 300):
                print(f"WARNING: Discord returned status {resp.status}", file=sys.stderr)
    except Exception as err:
        print(f"WARNING: Failed to send Discord alert: {err}", file=sys.stderr)

# Try importing the required third-party libraries early to fail fast
try:
    import psycopg2
    import boto3
    from botocore.exceptions import BotoCoreError, ClientError
except ImportError as e:
    msg = f"❌ **DR Backup Alert (ImportError)**\nRequired dependencies not found. Please install psycopg2 and boto3.\nError: {e}"
    print(f"CRITICAL: {msg}", file=sys.stderr)
    send_discord_alert(msg)
    sys.exit(1)


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


def record_backup_heartbeat(database_url: str, ok: bool, error_message: str | None = None):
    try:
        conn = psycopg2.connect(database_url)
        conn.autocommit = True
        with conn.cursor() as cur:
            if ok:
                cur.execute(
                    """
                    INSERT INTO public.job_heartbeat
                        (job_id, last_run_at, last_success_at, run_count,
                         expected_interval_hours)
                    VALUES ('dr_backup', NOW(), NOW(), 1, 24)
                    ON CONFLICT (job_id) DO UPDATE
                    SET last_run_at              = NOW(),
                        last_success_at          = NOW(),
                        run_count                = job_heartbeat.run_count + 1,
                        expected_interval_hours  = 24,
                        updated_at               = NOW()
                    """
                )
            else:
                cur.execute(
                    """
                    INSERT INTO public.job_heartbeat
                        (job_id, last_run_at, last_error_at,
                         last_error_message, run_count, error_count,
                         expected_interval_hours)
                    VALUES ('dr_backup', NOW(), NOW(), %s, 1, 1, 24)
                    ON CONFLICT (job_id) DO UPDATE
                    SET last_run_at              = NOW(),
                        last_error_at            = NOW(),
                        last_error_message       = EXCLUDED.last_error_message,
                        run_count                = job_heartbeat.run_count + 1,
                        error_count              = job_heartbeat.error_count + 1,
                        expected_interval_hours  = 24,
                        updated_at               = NOW()
                    """,
                    ((error_message or "")[:500],)
                )
        conn.close()
    except Exception as e:
        print(f"WARNING: failed to record heartbeat for dr_backup: {e}", file=sys.stderr)


def obfuscate_db_url(url: str) -> str:
    """Obfuscates credentials in database URL for safe logging."""
    try:
        if "@" in url:
            parts = url.split("@", 1)
            cred_part = parts[0]
            host_part = parts[1]
            proto_idx = cred_part.find("://")
            if proto_idx != -1:
                proto = cred_part[:proto_idx + 3]
                user_pass = cred_part[proto_idx + 3:]
                if ":" in user_pass:
                    user, _ = user_pass.split(":", 1)
                    return f"{proto}{user}:****@{host_part}"
                else:
                    return f"{proto}{user_pass}:****@{host_part}"
            return f"****@{host_part}"
    except Exception:
        pass
    return "****"


def connect_with_retry(url: str):
    """Opens a psycopg2 connection with autocommit=True and up to 3 retry attempts on OperationalError."""
    import time
    last_err = None
    for attempt in range(1, 4):
        try:
            conn = psycopg2.connect(url)
            conn.autocommit = True
            return conn
        except psycopg2.OperationalError as e:
            last_err = e
            print(f"[DB] Connection attempt {attempt} failed: {e}", file=sys.stderr)
            if attempt < 3:
                print("[DB] Retrying in 5 seconds...", file=sys.stderr)
                time.sleep(5)
    raise last_err


def perform_db_backup(database_url: str, output_dir: str, skip_storage: bool = False):
    """
    Performs database backup:
    1. If pg_dump is available on PATH, runs it to generate a compressed custom-format db.dump file.
    2. Otherwise, falls back to a psycopg2 COPY query per table in 'public' and 'web' schemas.
    """
    backup_url = database_url
    if ":5432" in backup_url:
        backup_url = backup_url.replace(":5432", ":6543")

    # OPS-4: pg_dump CANNOT run against the :6543 transaction pooler — PgBouncer
    # transaction mode gives each statement its own server session, which breaks
    # pg_dump's single multi-statement dump session. Use a direct/session url
    # (:5432) for the dump + its stats query; keep backup_url (:6543) for the
    # COPY fallback, which intentionally rides the transaction pooler to dodge the
    # session-pooler client cap (see OPS-2 — that rewrite is deliberate).
    dump_url = database_url
    if ":6543" in dump_url:
        dump_url = dump_url.replace(":6543", ":5432")

    pg_dump_path = shutil.which("pg_dump")
    
    if pg_dump_path:
        print("[DB] pg_dump found on PATH. Proceeding with pg_dump...")
        db_dump_file = os.path.join(output_dir, "db.dump")
        # Ensure output directory exists
        os.makedirs(output_dir, exist_ok=True)
        
        cmd = [pg_dump_path, "-Fc", "-d", dump_url, "-f", db_dump_file]
        print(f"[DB] Executing pg_dump into {db_dump_file}...")
        try:
            # Run without showing credentials in output or using shell=True to avoid injection
            result = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=True
            )
            # Fetch table stats to provide in manifest even when pg_dump is used
            # We connect via psycopg2 briefly to count tables and rows
            print("[DB] pg_dump completed successfully. Gathering stats for manifest...")
            tables_stats = []
            conn = connect_with_retry(dump_url)
            cur = conn.cursor()
            cur.execute("""
                SELECT table_schema, table_name 
                FROM information_schema.tables 
                WHERE table_type = 'BASE TABLE' 
                  AND table_schema IN ('public', 'web')
                ORDER BY table_schema, table_name;
            """)
            tables = cur.fetchall()
            total_rows = 0
            for schema, table in tables:
                try:
                    cur.execute(f'SELECT COUNT(*) FROM "{schema}"."{table}"')
                    row_count = cur.fetchone()[0]
                    total_rows += row_count
                    tables_stats.append({
                        "schema": schema,
                        "table": table,
                        "row_count": row_count
                    })
                except Exception as e:
                    print(f"[DB] Warning: Could not count rows for {schema}.{table}: {e}")
                    tables_stats.append({
                        "schema": schema,
                        "table": table,
                        "row_count": -1
                    })
            cur.close()
            conn.close()

            # Write manifest
            timestamp_utc = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d_%H%M%S")
            manifest = {
                "timestamp_utc": timestamp_utc,
                "db_method": "pg_dump",
                "schemas": ["public", "web"],
                "tables": tables_stats,
                "total_tables": len(tables_stats),
                "total_rows": total_rows,
                "storage_skipped": skip_storage,
                "storage_files": 0 if skip_storage else None
            }
            manifest_file = os.path.join(output_dir, "manifest.json")
            with open(manifest_file, "w", encoding="utf-8") as f:
                json.dump(manifest, f, indent=2)

            return "pg_dump", len(tables_stats), total_rows

        except subprocess.CalledProcessError as e:
            print(f"[DB] pg_dump failed: {e.stderr.strip()}", file=sys.stderr)
            raise e
    else:
        print("[DB] pg_dump NOT found on PATH. Falling back to psycopg2 COPY export...")
        data_dir = os.path.join(output_dir, "data")
        os.makedirs(data_dir, exist_ok=True)
        
        conn = connect_with_retry(backup_url)
        cur = conn.cursor()
        
        try:
            # Query base tables in public and web
            cur.execute("""
                SELECT table_schema, table_name 
                FROM information_schema.tables 
                WHERE table_type = 'BASE TABLE' 
                  AND table_schema IN ('public', 'web')
                ORDER BY table_schema, table_name;
            """)
            tables = cur.fetchall()
            print(f"[DB] Found {len(tables)} base tables in 'public' and 'web' schemas.")
            
            tables_stats = []
            total_rows = 0
            
            for schema, table in tables:
                # 1. Row count query
                cur.execute(f'SELECT COUNT(*) FROM "{schema}"."{table}"')
                row_count = cur.fetchone()[0]
                total_rows += row_count
                
                # 2. COPY export to CSV
                csv_filename = f"{schema}.{table}.csv"
                csv_path = os.path.join(data_dir, csv_filename)
                
                sql_copy = f'COPY (SELECT * FROM "{schema}"."{table}") TO STDOUT WITH CSV HEADER'
                print(f"[DB] Copying table {schema}.{table} ({row_count} rows) to CSV...")
                with open(csv_path, "wb") as csv_file:
                    cur.copy_expert(sql_copy, csv_file)
                
                tables_stats.append({
                    "schema": schema,
                    "table": table,
                    "row_count": row_count,
                    "file": f"data/{csv_filename}"
                })
            
            # 3. Write manifest.json
            timestamp_utc = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d_%H%M%S")
            manifest = {
                "timestamp_utc": timestamp_utc,
                "db_method": "psycopg2_copy",
                "schemas": ["public", "web"],
                "tables": tables_stats,
                "total_tables": len(tables_stats),
                "total_rows": total_rows,
                "storage_skipped": skip_storage,
                "storage_files": 0 if skip_storage else None
            }
            manifest_file = os.path.join(output_dir, "manifest.json")
            with open(manifest_file, "w", encoding="utf-8") as f:
                json.dump(manifest, f, indent=2)
                
            print(f"[DB] Backup of {len(tables)} tables completed successfully.")
            return "psycopg2_copy", len(tables), total_rows
            
        finally:
            cur.close()
            conn.close()


def perform_storage_backup(supabase_url: str, access_key: str, secret_key: str, region: str, output_dir: str):
    """
    Downloads all objects from the buckets: uploads, marketer-images, social-images, gallery
    via the S3 protocol and boto3.
    """
    buckets = ["uploads", "marketer-images", "social-images", "gallery"]
    endpoint_url = supabase_url.rstrip("/") + "/storage/v1/s3"
    
    print(f"[Storage] Connecting to Supabase S3 endpoint: {endpoint_url} (region: {region})")
    
    s3_client = boto3.client(
        "s3",
        endpoint_url=endpoint_url,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name=region,
    )
    
    storage_dir = os.path.join(output_dir, "storage")
    os.makedirs(storage_dir, exist_ok=True)
    
    total_files = 0
    total_bytes = 0
    
    for bucket in buckets:
        bucket_dir = os.path.join(storage_dir, bucket)
        os.makedirs(bucket_dir, exist_ok=True)
        print(f"[Storage] Processing bucket '{bucket}'...")
        
        try:
            paginator = s3_client.get_paginator("list_objects_v2")
            pages = paginator.paginate(Bucket=bucket)
            
            bucket_file_count = 0
            bucket_byte_count = 0
            
            for page in pages:
                if "Contents" not in page:
                    continue
                for obj in page["Contents"]:
                    key = obj["Key"]
                    size = obj["Size"]
                    
                    # Target path locally
                    local_file_path = os.path.join(bucket_dir, key)
                    
                    # Ensure directory structure for the key is preserved
                    os.makedirs(os.path.dirname(local_file_path), exist_ok=True)
                    
                    # Download object
                    # Avoid displaying secrets or keys in logging
                    # Just print simple progress log
                    print(f"  -> Downloading {bucket}/{key} ({size} bytes)...")
                    s3_client.download_file(Bucket=bucket, Key=key, Filename=local_file_path)
                    
                    bucket_file_count += 1
                    bucket_byte_count += size
            
            print(f"[Storage] Bucket '{bucket}' backup finished. Files: {bucket_file_count}, Total Size: {bucket_byte_count} bytes")
            total_files += bucket_file_count
            total_bytes += bucket_byte_count
            
        except ClientError as ce:
            # Standard error message from boto3
            print(f"[Storage] Error listing or downloading from bucket '{bucket}': {ce}", file=sys.stderr)
            raise ce
        except Exception as e:
            print(f"[Storage] Unexpected error for bucket '{bucket}': {e}", file=sys.stderr)
            raise e
            
    return total_files, total_bytes


def main():
    parser = argparse.ArgumentParser(description="VEXONHQ Disaster Recovery Backup Tool")
    parser.add_argument("--out", default="./backups", help="Base directory where timestamped backups will be written")
    parser.add_argument("--skip-storage", "--db-only", action="store_true", help="Skip Supabase storage download step and back up only the database")
    args = parser.parse_args()
    
    # Load env variables from .env file
    load_env()
    
    # Retrieve env parameters
    database_url = os.environ.get("DATABASE_URL")
    supabase_url = os.environ.get("SUPABASE_URL")
    aws_access_key = os.environ.get("SUPABASE_S3_ACCESS_KEY_ID")
    aws_secret_key = os.environ.get("SUPABASE_S3_SECRET_ACCESS_KEY")
    aws_region = os.environ.get("SUPABASE_S3_REGION", "ap-southeast-1")
    
    # Verify required environment variables are set
    missing_vars = []
    if not database_url:
        missing_vars.append("DATABASE_URL")
    if not args.skip_storage:
        if not supabase_url:
            missing_vars.append("SUPABASE_URL")
        if not aws_access_key:
            missing_vars.append("SUPABASE_S3_ACCESS_KEY_ID")
        if not aws_secret_key:
            missing_vars.append("SUPABASE_S3_SECRET_ACCESS_KEY")
        
    if missing_vars:
        msg = f"❌ **DR Backup Alert (ConfigError)**\nMissing environment variables: {', '.join(missing_vars)}\nPlease check your .env file or environment configuration."
        print(msg, file=sys.stderr)
        send_discord_alert(msg)
        sys.exit(1)

    # Generate unique output timestamp folder
    timestamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d_%H%M%S")
    backup_folder_name = f"mara-backup-{timestamp}"
    output_path = os.path.abspath(os.path.join(args.out, backup_folder_name))
    
    print("=" * 60)
    print("VEXONHQ DISASTER RECOVERY BACKUP")
    print(f"Started at (UTC): {datetime.datetime.now(datetime.timezone.utc).isoformat()}")
    print(f"Database: {obfuscate_db_url(database_url)}")
    print(f"Target Output Path: {output_path}")
    print("=" * 60)
    
    try:
        # Step 1: Database backup
        print("\n--- STEP 1: DATABASE BACKUP ---")
        db_method, num_tables, total_rows = perform_db_backup(database_url, output_path, skip_storage=args.skip_storage)
        
        # Step 2: Storage backup
        if args.skip_storage:
            print("\n--- STEP 2: STORAGE BACKUP (SKIPPED) ---")
            num_files, total_bytes = 0, 0
        else:
            print("\n--- STEP 2: STORAGE BACKUP ---")
            num_files, total_bytes = perform_storage_backup(
                supabase_url=supabase_url,
                access_key=aws_access_key,
                secret_key=aws_secret_key,
                region=aws_region,
                output_dir=output_path
            )
            # Update manifest.json with storage statistics since they were not skipped
            manifest_file = os.path.join(output_path, "manifest.json")
            if os.path.exists(manifest_file):
                try:
                    with open(manifest_file, "r", encoding="utf-8") as f:
                        manifest = json.load(f)
                    manifest["storage_skipped"] = False
                    manifest["storage_files"] = num_files
                    manifest["storage_bytes"] = total_bytes
                    with open(manifest_file, "w", encoding="utf-8") as f:
                        json.dump(manifest, f, indent=2)
                except Exception as me:
                    print(f"[DB] Warning: Could not update manifest with storage stats: {me}")
        
        # Step 3: Print summary
        print("\n" + "=" * 60)
        print("BACKUP COMPLETED SUCCESSFULLY")
        print("=" * 60)
        print(f"DB Backup Method:      {db_method}")
        print(f"Tables Exported:       {num_tables}")
        print(f"Total Database Rows:   {total_rows}")
        print(f"Storage Files Saved:   {num_files}")
        print(f"Total Storage Bytes:   {total_bytes} bytes ({total_bytes / (1024 * 1024):.2f} MB)")
        print(f"Absolute Output Path:  {output_path}")
        print("=" * 60)

        # Record success heartbeat
        record_backup_heartbeat(database_url, ok=True)
        
    except Exception as e:
        print("\n" + "=" * 60, file=sys.stderr)
        print("BACKUP FAILED!", file=sys.stderr)
        print("=" * 60, file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        
        # Record failure heartbeat
        if database_url:
            record_backup_heartbeat(database_url, ok=False, error_message=str(e))
            
        # Send Discord alert
        err_msg = f"❌ **DR Backup Alert (RuntimeError)**\nBackup script failed with error:\n`{e}`"
        send_discord_alert(err_msg)
        
        # Clean up output directory on failure to preserve idempotence and cleanliness if folder is empty or partial
        # Actually, let's keep the error directory or clean it up if it has partially written data?
        # Standard DR practice: keep the directory for debugging or clean it to avoid corrupted/incomplete backups.
        # Let's clean it up to prevent incomplete backups from being mistaken for good backups!
        if os.path.exists(output_path):
            try:
                print(f"Cleaning up partial backup directory: {output_path}", file=sys.stderr)
                shutil.rmtree(output_path)
            except Exception as cleanup_err:
                print(f"Failed to clean up partial directory {output_path}: {cleanup_err}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
