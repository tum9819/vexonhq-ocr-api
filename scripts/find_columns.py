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
                    print(f"Loaded env var: {key}")

def main():
    load_env()
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("DATABASE_URL not found in .env or environment")
        return

    conn = psycopg2.connect(db_url)
    cur = conn.cursor()
    
    query = """
        SELECT table_name, column_name, data_type
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND (
            column_name LIKE '%url%'
            OR column_name LIKE '%path%'
            OR column_name LIKE '%file%'
            OR column_name LIKE '%preview%'
            OR column_name LIKE '%image%'
          )
        ORDER BY table_name, column_name;
    """
    cur.execute(query)
    rows = cur.fetchall()
    
    print("\nFound potential columns:")
    print("-" * 80)
    for table_name, column_name, data_type in rows:
        print(f"Table: {table_name:<30} | Column: {column_name:<25} | Type: {data_type}")
        
    cur.close()
    conn.close()

if __name__ == "__main__":
    main()
