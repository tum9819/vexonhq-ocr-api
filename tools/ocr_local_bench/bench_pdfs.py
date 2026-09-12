"""End-to-end run on the REAL PDF folder (rendered prod-style into data2/, OCR'd into out2/):
group pages per PDF -> extractor + pass-2 -> gate -> compare with expected.json when the invoice
number is known in the DB, otherwise report as BLIND (gate + parsed header for eye check).

Usage (in container): python bench_pdfs.py --variant nolines15 --pass2
"""
import argparse, json, pathlib, re, time, difflib
import cv2, pytesseract
import makro_extract as M
from bench_common import cell_reocr, refine_page, norm_name

ap = argparse.ArgumentParser()
ap.add_argument("--variant", default="nolines15")
ap.add_argument("--pass2", action="store_true")
a = ap.parse_args()
ROOT = pathlib.Path(__file__).parent
expected = {b["invoice_no"]: b for b in json.loads((ROOT / "expected.json").read_text(encoding="utf-8"))}

stems = sorted({p.name.rsplit("-p", 1)[0] for p in (ROOT / "data2").glob("*.png")})
results = []
for stem in stems:
    pngs = sorted((ROOT / "data2").glob(f"{stem}-p*.png"), key=lambda p: int(p.stem.rsplit("-p", 1)[1]))
    pages = []
    t0 = time.perf_counter(); calls = 0
    for png in pngs:
        tsv = (ROOT / "out2" / f"{png.stem}.{a.variant}.tsv").read_text(encoding="utf-8")
        page = M.parse_page(tsv)
        if a.pass2:
            proc = ROOT / "out2" / f"{png.stem}.{a.variant}.png"
            img = cv2.imread(str(proc), cv2.IMREAD_GRAYSCALE)
            st = {"calls": 0}; refine_page(page, img, st); calls += st["calls"]
        pages.append(page)
    bill = M.assemble_bill(pages)
    parsed = bill["parsed"]
    inv = parsed["invoice_no"]
    exp = expected.get(inv)
    row = {"file": stem, "pages": len(pngs), "invoice_no": inv, "gate_ok": bill["gate_ok"], "reasons": bill["reasons"],
           "bill_date": parsed["bill_date"], "subtotal": parsed["subtotal"], "vat": parsed["vat"], "amount": parsed["amount"],
           "discount": parsed["discount"]["whole_bill_discount_amount"], "n_items": len(parsed["items"]),
           "items_sum": bill["items_sum"], "totals": bill["totals"], "secs": round(time.perf_counter() - t0, 1),
           "pass2_calls": calls, "known_in_db": exp is not None}
    if exp:
        def _eq(got, want):
            # None-safe exact-satang compare; a real 0.00 (VAT-exempt bill) must NOT count as missing
            return got is not None and want is not None and abs(float(got) - float(want)) <= 0.005
        exp_disc = (exp.get("discount") or {}).get("whole_bill_discount_amount") if isinstance(exp.get("discount"), dict) else None
        row["hdr_match"] = {
            "bill_date": parsed["bill_date"] == exp["bill_date"],
            "subtotal": _eq(parsed["subtotal"], exp["subtotal"]),
            "vat": _eq(parsed["vat"], exp["vat"]),
            "amount": _eq(parsed["amount"], exp["amount"]),
            "discount": (parsed["discount"]["whole_bill_discount_amount"] or 0.0) == (float(exp_disc) if exp_disc is not None else 0.0),
        }
        def _keyed(items):
            # row-level: the k-th occurrence of a SKU keys as (sku, k) so repeated SKUs never collapse (review #3)
            seen = {}; out = {}
            for it in items:
                sk = str(it.get("sku") or "").strip()
                if not sk:
                    continue
                seen[sk] = seen.get(sk, 0) + 1
                out[(sk, seen[sk])] = it
            return out
        exp_items = _keyed(exp["items"]); got = _keyed(parsed["items"])
        row["items_expected"] = len(exp["items"]); row["items_matched"] = len(set(got) & set(exp_items))
        row["items_numbers_exact"] = sum(1 for s in got if s in exp_items and all(
            abs(float(got[s][f] or 0) - float(exp_items[s][f] or 0)) <= 0.005 for f in ("quantity", "unit_price", "amount")))
    results.append(row)
    tag = "KNOWN" if exp else "BLIND"
    print(f"{stem:28s} {tag} inv={inv} gate={'PASS' if bill['gate_ok'] else 'FAIL'} date={parsed['bill_date']} "
          f"amount={parsed['amount']} disc={row['discount']} items={len(parsed['items'])} sum={bill['items_sum']} "
          f"| {row.get('hdr_match','')} items {row.get('items_matched','-')}/{row.get('items_expected','-')} exact {row.get('items_numbers_exact','-')} | {bill['reasons'][:2]}", flush=True)

(ROOT / "out2" / "report_pdfs.json").write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
known = [r for r in results if r["known_in_db"]]; blind = [r for r in results if not r["known_in_db"]]
print("\n=== PDF run summary ===")
print("PDFs:", len(results), "known:", len(known), "blind:", len(blind))
print("gate PASS known:", sum(r["gate_ok"] for r in known), "/", len(known), "| blind:", sum(r["gate_ok"] for r in blind), "/", len(blind))
for k in ("bill_date", "subtotal", "vat", "amount", "discount"):
    print(f"  known {k} match: {sum(r['hdr_match'][k] for r in known)}/{len(known)}")
print("known items matched:", sum(r["items_matched"] for r in known), "/", sum(r["items_expected"] for r in known),
      "numbers exact:", sum(r["items_numbers_exact"] for r in known))
print("avg secs/bill (extract+pass2):", round(sum(r["secs"] for r in results) / len(results), 1))
