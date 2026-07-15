"""One-shot repair of confirmed bills whose OCR line items don't tie to the
paid amount (the +/-10% band) — the 24-bill backlog found 2026-07-14.

Run INSIDE the backend container (Coolify -> vexonhq-ocr-api -> Terminal tab):

    python scripts/repair_incomplete_bills.py                 # DRY-RUN: preview every failing bill (no writes)
    python scripts/repair_incomplete_bills.py --apply         # apply re-OCR results that tie; the rest are reported
    python scripts/repair_incomplete_bills.py --single-line <bill_id> [<bill_id> ...]
                                                              # slip-backed bills: replace items with 1 line = amount

Design notes:
- Calls the REAL /invoice/{id}/reocr-items and /invoice/{id}/set-single-line
  endpoints over localhost, so the exact production code path runs (admin
  gate, per-repair backup to invoice_item_repairs, revalidate) and Cloudflare's
  ~100s edge timeout never applies to the long vision calls.
- Does NOT `import main` — a second import inside the running container would
  double-start the APScheduler jobs.
- The admin JWT is minted from the container's own JWT_SECRET (the legacy
  HS256 path in auth_routes.verify_token). No credentials leave the server.
  Audit trail: created_by = "repair-script".
- Apply is WYSIWYG: the previewed items are passed back to the endpoint, so
  what gets written is exactly what the preview showed (OCR is
  non-deterministic; a second run could differ).
- Safe to re-run: bills that now tie drop out of the SQL selection.
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

import jwt
import psycopg2


def getenv(name: str) -> str | None:
    """os.environ first; fall back to the app process's environment.

    The Coolify Terminal opens a plain `docker exec` shell that does NOT
    inherit the env vars Coolify injects into the app's start command, so
    JWT_SECRET/DATABASE_URL/PORT are missing there. PID 1 (the uvicorn
    process) has them — read /proc/1/environ directly.
    """
    val = os.environ.get(name)
    if val:
        return val
    try:
        with open("/proc/1/environ", "rb") as f:
            for entry in f.read().split(b"\0"):
                k, sep, v = entry.partition(b"=")
                if sep and k.decode(errors="replace") == name:
                    return v.decode(errors="replace")
    except OSError:
        pass
    return None

FAILING_BILLS_SQL = """
SELECT vb.id, vb.vendor_name, vb.bill_date, vb.amount,
       COALESCE(SUM(ii.amount), 0)::numeric(12,2) AS lines_sum,
       COUNT(ii.id) AS n_lines
FROM vendor_bills vb
LEFT JOIN invoice_items ii ON ii.vendor_bill_id = vb.id
WHERE vb.review_status = 'confirmed'
GROUP BY vb.id
HAVING COUNT(ii.id) = 0
    OR COALESCE(SUM(ii.amount), 0) <= 0
    OR vb.amount IS NULL
    OR vb.amount / NULLIF(SUM(ii.amount), 0) NOT BETWEEN 0.90 AND 1.10
ORDER BY vb.amount DESC NULLS LAST;
"""


def mint_admin_token() -> str:
    secret = getenv("JWT_SECRET")
    if not secret:
        sys.exit("JWT_SECRET not found (checked shell env and /proc/1/environ) — "
                 "this script must run inside the backend container")
    return jwt.encode(
        {"sub": "repair-script", "role": "admin", "exp": int(time.time()) + 2 * 3600},
        secret,
        algorithm="HS256",
    )


def find_base_url() -> str:
    candidates = [p for p in (getenv("PORT"), "8000", "80", "3000") if p]
    for port in candidates:
        url = f"http://127.0.0.1:{port}"
        try:
            with urllib.request.urlopen(f"{url}/health", timeout=5) as r:
                if r.status == 200:
                    return url
        except Exception:
            continue
    sys.exit(f"could not reach the app on localhost (tried ports {candidates})")


def post(base: str, token: str, path: str, body: dict, timeout: int = 900) -> tuple[int, dict]:
    req = urllib.request.Request(
        f"{base}{path}",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try:
            detail = json.loads(e.read().decode())
        except Exception:
            detail = {"detail": str(e)}
        return e.code, detail


def fetch_failing_bills() -> list[dict]:
    dsn = getenv("DATABASE_URL")
    if not dsn:
        sys.exit("DATABASE_URL not found (checked shell env and /proc/1/environ)")
    conn = psycopg2.connect(dsn)
    try:
        with conn.cursor() as cur:
            cur.execute(FAILING_BILLS_SQL)
            rows = cur.fetchall()
    finally:
        conn.close()
    return [
        {"id": r[0], "vendor": r[1], "date": str(r[2]), "amount": float(r[3] or 0),
         "lines_sum": float(r[4] or 0), "n_lines": r[5]}
        for r in rows
    ]


def fmt(n: float) -> str:
    return f"{n:,.2f}"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true",
                    help="apply re-OCR results that tie (default: preview only)")
    ap.add_argument("--single-line", nargs="+", metavar="BILL_ID", default=None,
                    help="replace these bills' items with 1 synthetic line = amount (slip-backed bills)")
    args = ap.parse_args()

    token = mint_admin_token()
    base = find_base_url()
    print(f"app: {base}  mode: {'APPLY' if args.apply else 'DRY-RUN (no writes)'}\n")

    if args.single_line:
        for bill_id in args.single_line:
            code, res = post(base, token, f"/invoice/{bill_id}/set-single-line", {})
            if code == 200:
                print(f"SINGLE-LINE OK  {bill_id}  -> 1 line = {fmt(res['amount'])} "
                      f"(was {res['old_count']} items, sum {fmt(res['old_sum'])})")
            else:
                print(f"SINGLE-LINE FAIL {bill_id}  HTTP {code}: {res.get('detail')}")
        return

    bills = fetch_failing_bills()
    print(f"failing bills: {len(bills)}\n")
    applied, tied_preview, not_tied, errors = 0, 0, [], []

    for i, b in enumerate(bills, 1):
        head = f"[{i}/{len(bills)}] {b['vendor']} {b['date']} amount {fmt(b['amount'])}"
        code, res = post(base, token, f"/invoice/{b['id']}/reocr-items", {"apply": False})
        if code != 200:
            print(f"{head}\n  ERROR preview HTTP {code}: {res.get('detail')}")
            errors.append({**b, "error": res.get("detail")})
            continue
        line = (f"  items {res['old_count']} (sum {fmt(res['old_sum'])}) -> "
                f"{res['new_count']} (sum {fmt(res['new_sum'])})  ratio {res.get('ratio')}  "
                f"{'TIE' if res['tie_ok'] else 'NOT-TIED'}")
        if res["tie_ok"] and args.apply:
            code2, res2 = post(base, token, f"/invoice/{b['id']}/reocr-items",
                               {"apply": True, "items": res.get("new_items") or []})
            if code2 == 200 and res2.get("applied"):
                applied += 1
                print(f"{head}\n{line}  -> APPLIED")
            else:
                print(f"{head}\n{line}  -> APPLY FAILED HTTP {code2}: {res2.get('detail')}")
                errors.append({**b, "error": res2.get("detail")})
        else:
            if res["tie_ok"]:
                tied_preview += 1
            else:
                not_tied.append({**b, "new_sum": res["new_sum"], "new_count": res["new_count"]})
            print(f"{head}\n{line}")
        time.sleep(3)  # be gentle on the shared 4GB box

    print("\n===== SUMMARY =====")
    print(f"applied: {applied}   tie-but-not-applied (dry-run): {tied_preview}   "
          f"not-tied: {len(not_tied)}   errors: {len(errors)}")
    if not_tied:
        print("\nNOT-TIED (need human decision — check the bill image; "
              "if the AMOUNT header is wrong, fix it in the UI first, then re-run):")
        for b in not_tied:
            print(f"  {b['vendor']} {b['date']}  amount {fmt(b['amount'])}  "
                  f"reocr sum {fmt(b['new_sum'])} ({b['new_count']} items)  id {b['id']}")
    if errors:
        print("\nERRORS:")
        for b in errors:
            print(f"  {b['vendor']} {b['date']}  id {b['id']}  {b['error']}")


if __name__ == "__main__":
    main()
