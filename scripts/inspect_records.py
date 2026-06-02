import os
import psycopg2

def load_env():
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

def main():
    load_env()
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("DATABASE_URL not found")
        return

    conn = psycopg2.connect(db_url)
    cur = conn.cursor()

    tables = [
        ("attachments", "file_url"),
        ("documents", "file_url"),
        ("slips", "raw_image_url"),
        ("vendor_bills", "attachment_url"),
        ("pos_imports", "source_file"),
    ]

    for table, col in tables:
        try:
            cur.execute(f"SELECT COUNT(*) FROM {table}")
            cnt = cur.fetchone()[0]
            print(f"Table {table}: total rows = {cnt}")
            
            cur.execute(f"SELECT {col} FROM {table} WHERE {col} IS NOT NULL LIMIT 5")
            rows = cur.fetchall()
            print(f"  Sample {col} values:")
            for r in rows:
                print(f"    - {r[0]}")
        except Exception as e:
            print(f"Error querying {table}.{col}: {e}")
            conn.rollback()

    cur.close()
    conn.close()

if __name__ == "__main__":
    main()
