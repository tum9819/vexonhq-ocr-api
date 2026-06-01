"""
gen_golden_from_confirmed.py — build a REAL OCR golden set from confirmed bills.

Audit Testing baseline: the human-confirmed vendor bills in the DB
(`review_status='confirmed'`) ARE ground truth. This LOCAL CLI exports each as a
`<invoice_id>.expected.json` (the shape tests/ocr_golden/scorer.py expects) plus a
`<invoice_id>.source.txt` with the stored image URL, so you can pair each expected
file with its image and run the gpt-4o-vs-Claude comparison.

⚠️ Writes REAL financial data — the output dir MUST be OUTSIDE this repo (the
script refuses a path under the repo root). Never commit a real golden set.

Usage (local, needs DATABASE_URL):
    python -m tools.gen_golden_from_confirmed --out C:\\Users\\rapee\\ocr-golden-private --limit 50
    python -m tools.gen_golden_from_confirmed --out <dir> --since 2026-04-01

Then measure accuracy:
    python -m tests.ocr_golden.compare --dir <out>      # gpt-4o vs Claude
    python -m tests.ocr_golden.scorer  --live <img> <expected.json>   # single, OpenAI only
"""

from __future__ import annotations

import argparse
import json
import os
import sys


def bill_row_to_expected(row: dict, items: list[dict]) -> dict:
    """Pure mapping: a vendor_bills row (+ its invoice_items) → the expected.json
    shape used by tests/ocr_golden/scorer.py. No DB/IO — unit-testable."""
    def num(v):
        return float(v) if v is not None else None

    return {
        "vendor_name": row.get("vendor_name"),
        "invoice_no": row.get("invoice_no"),
        "bill_date": str(row["bill_date"]) if row.get("bill_date") else None,
        "merchant_tax_id": row.get("merchant_tax_id"),
        "subtotal": num(row.get("subtotal")),
        "vat": num(row.get("vat")),
        "amount": num(row.get("amount")),
        "items": [
            {
                "product_name": it.get("product_name") or it.get("item_name"),
                "qty": num(it.get("qty") or it.get("quantity")),
                "total": num(it.get("total") or it.get("line_total") or it.get("amount")),
            }
            for it in items
        ],
    }


def _image_url(row: dict) -> str | None:
    """Best-effort: pull the stored image URL from the row / ocr_json."""
    for k in ("preview_url", "file_url", "image_url", "storage_url"):
        if row.get(k):
            return row[k]
    oj = row.get("ocr_json")
    if isinstance(oj, dict):
        for k in ("preview_url", "file_url", "image_url", "source_url"):
            if oj.get(k):
                return oj[k]
    return None


def _refuse_in_repo(out_dir: str) -> None:
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if os.path.abspath(out_dir).startswith(repo_root):
        sys.exit(
            f"REFUSED: --out is inside the repo ({repo_root}). Real financial data "
            "must NOT be committed. Choose a folder outside the repo."
        )


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", required=True, help="output dir (MUST be outside the repo)")
    ap.add_argument("--limit", type=int, default=50)
    ap.add_argument("--since", default=None, help="only bills with bill_date >= YYYY-MM-DD")
    args = ap.parse_args(argv)

    _refuse_in_repo(args.out)
    os.makedirs(args.out, exist_ok=True)

    try:
        from main import get_db_conn  # type: ignore
    except Exception:
        import psycopg2
        def get_db_conn():
            return psycopg2.connect(os.environ["DATABASE_URL"])

    where = "WHERE review_status = 'confirmed'"
    params: list = []
    if args.since:
        where += " AND bill_date >= %s"
        params.append(args.since)

    conn = get_db_conn()
    written = 0
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""SELECT id, vendor_name, invoice_no, bill_date, merchant_tax_id,
                           subtotal, vat, amount, ocr_json
                    FROM public.vendor_bills
                    {where}
                    ORDER BY bill_date DESC NULLS LAST
                    LIMIT %s""",
                (*params, args.limit),
            )
            cols = [c[0] for c in cur.description]
            bills = [dict(zip(cols, r)) for r in cur.fetchall()]

            for b in bills:
                cur.execute(
                    """SELECT product_name, qty, total
                       FROM public.invoice_items
                       WHERE vendor_bill_id = %s
                       ORDER BY id""",
                    (b["id"],),
                )
                icols = [c[0] for c in cur.description]
                items = [dict(zip(icols, r)) for r in cur.fetchall()]

                expected = bill_row_to_expected(b, items)
                bid = str(b["id"])
                with open(os.path.join(args.out, f"{bid}.expected.json"), "w", encoding="utf-8") as f:
                    json.dump(expected, f, ensure_ascii=False, indent=2)
                url = _image_url(b)
                with open(os.path.join(args.out, f"{bid}.source.txt"), "w", encoding="utf-8") as f:
                    f.write((url or "(no stored image url — fetch from Supabase Storage manually)") + "\n")
                written += 1
    finally:
        conn.close()

    print(f"Wrote {written} expected.json file(s) to {args.out}")
    print("Next — download each .source.txt image next to its .expected.json, then:")
    print(f"  python -m tests.ocr_golden.compare --dir {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
