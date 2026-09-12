"""Download the confirmed Makro pages from Supabase storage into ./data (READ-ONLY).
Reads SUPABASE_URL / SUPABASE_SERVICE_KEY from the repo .env (utf-8-sig, BOM).
Never prints secrets. Expected values come from expected.json written by hand from the DB query.
"""
import json, os, sys, pathlib
ROOT = pathlib.Path(__file__).parent
env_path = pathlib.Path(r"C:\Users\rapee\vexonhq-ocr-api\.env")
for line in env_path.read_text(encoding="utf-8-sig").splitlines():
    line = line.strip()
    if line and not line.startswith("#") and "=" in line and not line.startswith("$"):
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

from supabase import create_client
sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])
bucket = "uploads"

bills = json.loads((ROOT / "expected.json").read_text(encoding="utf-8"))
out = ROOT / "data"
out.mkdir(exist_ok=True)
n = 0
for b in bills:
    seen = set()
    for pg in b["pages"]:
        fname = pg["f"]
        if fname in seen:          # duplicate re-upload rows (pre-#59) — one copy per page is enough
            continue
        seen.add(fname)
        path = pg["u"].split("/object/public/uploads/", 1)[1]
        dest = out / f"{b['invoice_no']}-p{len(seen)}.png"
        if dest.exists():
            continue
        data = sb.storage.from_(bucket).download(path)
        dest.write_bytes(data)
        n += 1
        print("saved", dest.name, len(data), "bytes")
print("downloaded", n, "files")
