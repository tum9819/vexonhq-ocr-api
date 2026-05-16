"""
batch_import_local.py — VEXONHQ Local Batch POS Import
=======================================================
รัน parser บนเครื่อง Windows โดยตรง ไม่ต้อง upload ผ่าน web UI
Insert ตรงเข้า Supabase — เร็วกว่ามาก

วิธีใช้:
    1. ใส่ DATABASE_URL ใน .env ก่อน (copy จาก Coolify)
    2. python batch_import_local.py C:\\path\\to\\folder

ตัวอย่าง:
    python batch_import_local.py C:\\Users\\rapee\\Desktop\\pos_data
    python batch_import_local.py C:\\Users\\rapee\\Desktop\\pos_data --year 2569
    python batch_import_local.py C:\\Users\\rapee\\Desktop\\pos_data --dry-run

Options:
    --year YYYY     ปี ค.ศ. สำหรับ monthly_summary (default: 2026)
    --dry-run       ตรวจสอบไฟล์และ detect type โดยไม่ insert จริง
    --branch CODE   branch_code (default: thawi_watthana)
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
import uuid
from datetime import date, datetime
from pathlib import Path

# ── Load .env ──────────────────────────────────────────────────────────────────
def _load_env():
    env_path = Path(__file__).parent / ".env"
    if not env_path.exists():
        print("❌  ไม่พบไฟล์ .env  →  กรุณาสร้างไฟล์ .env ที่:")
        print(f"   {env_path}")
        print()
        print("   เนื้อหา .env:")
        print('   DATABASE_URL=postgresql://postgres:<password>@db.<project>.supabase.co:5432/postgres')
        sys.exit(1)
    for line in env_path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

_load_env()

# ── Import parser functions จาก pos_import.py ──────────────────────────────────
try:
    import psycopg2
    from pos_import import (
        PARSERS,
        WRITER_CONFIG,
        read_and_detect,
        _upsert,
        _values_clause,
    )
except ImportError as e:
    print(f"❌  Import error: {e}")
    print("   กรุณารัน script นี้จากโฟลเดอร์ vexonhq-ocr-api/")
    print("   และตรวจสอบว่า pip install pandas openpyxl psycopg2-binary")
    sys.exit(1)


# ── สี terminal (Windows รองรับ ANSI ใน Windows Terminal / PowerShell 7+) ──────
GREEN  = "\033[92m"
YELLOW = "\033[93m"
RED    = "\033[91m"
CYAN   = "\033[96m"
DIM    = "\033[2m"
RESET  = "\033[0m"
BOLD   = "\033[1m"

def ok(msg):    print(f"  {GREEN}✅{RESET}  {msg}")
def skip(msg):  print(f"  {YELLOW}⏭️ {RESET}  {msg}")
def fail(msg):  print(f"  {RED}❌{RESET}  {msg}")
def info(msg):  print(f"  {CYAN}ℹ️ {RESET}  {msg}")


# ── DB connection ──────────────────────────────────────────────────────────────
def get_conn():
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("❌  DATABASE_URL ไม่พบใน .env")
        sys.exit(1)
    return psycopg2.connect(db_url)


# ── ตรวจว่า hash ซ้ำใน DB ไหม ─────────────────────────────────────────────────
def check_duplicate(conn, file_hash: str) -> tuple[bool, str | None]:
    """Returns (is_duplicate, original_period_str)"""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT period_start, period_end, row_count "
            "FROM public.pos_imports "
            "WHERE file_hash = %s AND status = 'success' "
            "ORDER BY uploaded_at DESC LIMIT 1",
            (file_hash,)
        )
        row = cur.fetchone()
    if not row:
        return False, None
    ps, pe = row[0], row[1]
    period = f"{ps}" if ps == pe or not pe else f"{ps} → {pe}"
    return True, f"{period}  ({row[2]} แถว)"


# ── Main import logic สำหรับ 1 ไฟล์ ───────────────────────────────────────────
def import_file(
    filepath: Path,
    branch_code: str = "thawi_watthana",
    year_hint: int = 2026,
    dry_run: bool = False,
) -> dict:
    """Import one file. Returns result dict."""
    content = filepath.read_bytes()
    if not content:
        return {"status": "error", "msg": "ไฟล์ว่าง"}

    file_hash = hashlib.sha256(content).hexdigest()

    # Detect type ก่อนเสมอ
    try:
        df, rtype = read_and_detect(content, filepath.name)
    except Exception as e:
        return {"status": "error", "msg": f"detect ไม่ได้: {e}"}

    if dry_run:
        return {"status": "dry_run", "rtype": rtype, "hash": file_hash[:12]}

    conn = get_conn()
    try:
        # ตรวจ duplicate
        is_dup, dup_info = check_duplicate(conn, file_hash)
        if is_dup:
            return {"status": "skipped", "rtype": rtype, "msg": f"import แล้ว  ({dup_info})"}

        # สร้าง import record
        import_id = str(uuid.uuid4())
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO public.pos_imports
                  (id, report_type, branch_code, source_file, file_size,
                   file_hash, status, uploaded_by, uploaded_at)
                VALUES (%s,%s,%s,%s,%s,%s,'parsing','batch_local',now())
            """, (import_id, rtype, branch_code, filepath.name,
                  len(content), file_hash))
            conn.commit()

        # Parse
        parser = PARSERS[rtype]
        result = parser(
            df,
            year_hint=year_hint,
            period_start=date(year_hint, 1, 1),
            period_end=date(year_hint, 12, 31),
            snapshot_at=datetime.now(),
        )
        ps  = result["period_start"]
        pe  = result["period_end"]
        total_rows = 0

        with conn.cursor() as cur:
            for table, rows in result["tables"].items():
                if not rows:
                    continue

                # ── Special: inventory items (needs snapshot FK) ──────────────
                if table == "_inventory_items":
                    cur.execute(
                        "SELECT id FROM pos_inventory_snapshots "
                        "WHERE source_import_id=%s "
                        "ORDER BY created_at DESC LIMIT 1",
                        (import_id,)
                    )
                    snap = cur.fetchone()
                    if not snap:
                        continue
                    snap_id = snap[0]
                    for it in rows:
                        it["snapshot_id"] = snap_id
                    cols = list(rows[0].keys())
                    cur.executemany(
                        f"INSERT INTO pos_inventory_items ({','.join(cols)}) "
                        f"VALUES ({_values_clause(rows, cols)})", rows
                    )
                    total_rows += len(rows)
                    continue

                # ── Special: bill sales items (resolve FK via receipt_code) ───
                if table == "_sales_items":
                    bill_keys = [it.pop("_bill_key") for it in rows]
                    if bill_keys:
                        # Bulk fetch ALL matching bills in ONE query (N→1)
                        branch = bill_keys[0][0]
                        receipt_codes = list({bk[1] for bk in bill_keys})
                        cur.execute(
                            "SELECT id, receipt_code, sales_date "
                            "FROM pos_bills "
                            "WHERE branch_code = %s AND receipt_code = ANY(%s)",
                            (branch, receipt_codes)
                        )
                        bill_map = {(rc, sd): bid for bid, rc, sd in cur.fetchall()}
                        for it, bk in zip(rows, bill_keys):
                            bid = bill_map.get((bk[1], bk[2]))
                            if bid:
                                it["bill_id"] = bid
                    rows = [r for r in rows if "bill_id" in r]
                    if rows:
                        cols = list(rows[0].keys())
                        cur.executemany(
                            "INSERT INTO public.pos_sales_items ({}) VALUES ({})".format(
                                ",".join(cols), _values_clause(rows, cols)
                            ),
                            rows
                        )
                        total_rows += len(rows)
                    continue

                # ── Regular tables ─────────────────────────────────────────────
                cfg = WRITER_CONFIG.get(table)
                if not cfg:
                    continue
                for r in rows:
                    r["source_import_id"] = import_id
                n = _upsert(cur, table, rows, **cfg)
                total_rows += n

            # Update status
            cur.execute(
                "UPDATE public.pos_imports "
                "SET status='success', period_start=%s, period_end=%s, "
                "row_count=%s, finished_at=now() WHERE id=%s",
                (ps, pe, total_rows, import_id)
            )
            conn.commit()

        period_str = f"{ps}" if ps == pe or not pe else f"{ps} → {pe}"
        return {"status": "success", "rtype": rtype, "rows": total_rows, "period": period_str}

    except Exception as e:
        try:
            conn.rollback()
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE public.pos_imports SET status='error', "
                    "error_message=%s WHERE id=%s",
                    (str(e)[:2000], import_id)
                )
                conn.commit()
        except Exception:
            pass
        return {"status": "error", "msg": str(e)}
    finally:
        conn.close()


# ── CLI ────────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="VEXONHQ Local Batch POS Importer",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("folder", help="โฟลเดอร์ที่มีไฟล์ .xlsx / .csv")
    parser.add_argument("--year",    type=int, default=2026,              help="ปี ค.ศ. สำหรับ monthly_summary (default: 2026)")
    parser.add_argument("--branch",  default="thawi_watthana",            help="branch_code (default: thawi_watthana)")
    parser.add_argument("--dry-run", action="store_true",                 help="ตรวจสอบโดยไม่ insert จริง")
    args = parser.parse_args()

    folder = Path(args.folder)
    if not folder.exists():
        print(f"❌  ไม่พบโฟลเดอร์: {folder}")
        sys.exit(1)

    files = sorted(
        [f for f in folder.iterdir()
         if f.suffix.lower() in (".xlsx", ".csv") and not f.name.startswith("~")],
        key=lambda f: f.name
    )

    if not files:
        print(f"❌  ไม่พบไฟล์ .xlsx หรือ .csv ใน {folder}")
        sys.exit(1)

    print()
    print(f"{BOLD}VEXONHQ Batch Import{RESET}  {'(DRY-RUN)' if args.dry_run else ''}")
    print(f"{DIM}โฟลเดอร์  : {folder}{RESET}")
    print(f"{DIM}ไฟล์ทั้งหมด: {len(files)} ไฟล์{RESET}")
    print(f"{DIM}ปี        : {args.year}  |  branch: {args.branch}{RESET}")
    print("─" * 60)

    counts = {"success": 0, "skipped": 0, "error": 0, "dry_run": 0}
    total_rows = 0

    for i, f in enumerate(files, 1):
        label = f"[{i}/{len(files)}] {f.name}"
        print(f"\n{label}")

        res = import_file(f, branch_code=args.branch, year_hint=args.year, dry_run=args.dry_run)

        if res["status"] == "success":
            ok(f"{res['rtype']}  →  {res['rows']} แถว  ({res['period']})")
            counts["success"] += 1
            total_rows += res["rows"]

        elif res["status"] == "skipped":
            skip(f"{res['rtype']}  →  {res['msg']}")
            counts["skipped"] += 1

        elif res["status"] == "dry_run":
            info(f"{res['rtype']}  (hash: {res['hash']}…)  — ไม่ได้ insert จริง")
            counts["dry_run"] += 1

        elif res["status"] == "error":
            fail(res["msg"])
            counts["error"] += 1

    # Summary
    print()
    print("─" * 60)
    print(f"{BOLD}สรุป{RESET}")
    if not args.dry_run:
        print(f"  {GREEN}✅  นำเข้าสำเร็จ  : {counts['success']} ไฟล์  ({total_rows} แถว){RESET}")
        print(f"  {YELLOW}⏭️   ข้ามซ้ำ       : {counts['skipped']} ไฟล์{RESET}")
        if counts["error"]:
            print(f"  {RED}❌  ผิดพลาด       : {counts['error']} ไฟล์{RESET}")
    else:
        print(f"  {CYAN}ℹ️   Detect ได้     : {counts['dry_run']} ไฟล์  (ยังไม่ได้ insert){RESET}")
        if counts["error"]:
            print(f"  {RED}❌  Detect ไม่ได้  : {counts['error']} ไฟล์{RESET}")
    print()


if __name__ == "__main__":
    main()
