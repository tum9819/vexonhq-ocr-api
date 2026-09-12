"""Stage 2: parse every Makro bill with makro_extract (pass-1 TSV from ocr_pages.py, optional
pass-2 numeric re-OCR on word crops), run the gate, score against expected.json, write report.

Usage (in container): python bench_run.py --variant otsu [--pass2] [--out out/report_otsu_p2.json]
"""
import argparse, json, pathlib, re, time, difflib
import cv2, numpy as np, pytesseract
import makro_extract as M

ap = argparse.ArgumentParser()
ap.add_argument("--variant", default="otsu")
ap.add_argument("--pass2", action="store_true")
ap.add_argument("--out", default=None)
ap.add_argument("--only", default=None)
a = ap.parse_args()
ROOT = pathlib.Path(__file__).parent
bills = json.loads((ROOT / "expected.json").read_text(encoding="utf-8"))
out_path = pathlib.Path(a.out or ROOT / "out" / f"report_{a.variant}{'_p2' if a.pass2 else ''}.json")

from bench_common import cell_reocr, refine_page  # single source of the pass-2 rules (review-2 #1)


def norm_name(s):
    return re.sub(r"[\s|().,\[\]'\"]", "", (s or "")).lower()


results = []
seen_skus = set()  # chronological SKU memory (expected bills are ordered by bill_date)
for b in bills:
    inv = b["invoice_no"]
    if a.only and inv != a.only:
        continue
    pngs = sorted((ROOT / "data").glob(f"{inv}-p*.png"), key=lambda p: int(p.stem.split("-p")[1]))
    pages, p2stats = [], {"calls": 0, "secs": 0.0}
    t_start = time.perf_counter()
    for png in pngs:
        tsv = (ROOT / "out" / f"{png.stem}.{a.variant}.tsv").read_text(encoding="utf-8")
        page = M.parse_page(tsv)
        if a.pass2:
            proc_png = ROOT / "out" / f"{png.stem}.{a.variant}.png"
            img = cv2.imread(str(proc_png), cv2.IMREAD_GRAYSCALE) if proc_png.exists() else cv2.cvtColor(cv2.imread(str(png)), cv2.COLOR_BGR2GRAY)
            t0 = time.perf_counter(); st = {"calls": 0}
            refine_page(page, img, st)
            p2stats["calls"] += st["calls"]; p2stats["secs"] += time.perf_counter() - t0
        pages.append(page)
    bill = M.assemble_bill(pages)
    parsed = bill["parsed"]
    elapsed = time.perf_counter() - t_start

    # ---- header scoring vs human-confirmed values ----
    exp_disc = (b.get("discount") or {}).get("whole_bill_discount_amount") if isinstance(b.get("discount"), dict) else None
    hdr_cmp = {
        "merchant_tax_id": (parsed["merchant_tax_id"], b["merchant_tax_id"]),
        "invoice_no": (parsed["invoice_no"], b["invoice_no"]),
        "bill_date": (parsed["bill_date"], b["bill_date"]),
        "subtotal": (parsed["subtotal"], b["subtotal"]),
        "vat": (parsed["vat"], b["vat"]),
        "amount": (parsed["amount"], b["amount"]),
        "discount": (parsed["discount"]["whole_bill_discount_amount"], exp_disc),
    }
    hdr_ok = {}
    for k, (got, exp) in hdr_cmp.items():
        if isinstance(exp, (int, float)) or isinstance(got, (int, float)):
            hdr_ok[k] = (got is not None and exp is not None and abs(float(got) - float(exp)) <= 0.011) or (got is None and exp is None) \
                        or (k == "discount" and (got or 0) == (exp or 0))
        else:
            hdr_ok[k] = (got == exp)
    # NOTE: expected tax_id in DB is GPT's read (one bill stored 14 digits) — flag separately
    tax_db_valid = M.thai_tax_id_valid(b["merchant_tax_id"] or "")

    # ---- item scoring: match by SKU, compare numbers; names by similarity ----
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
    exp_items = _keyed(b["items"])
    got_items = _keyed(parsed["items"])
    matched = [s for s in got_items if s in exp_items]
    num_ok = 0
    num_diff = []
    name_sims = []
    sku_seen_before = 0
    for s in matched:
        g, e = got_items[s], exp_items[s]
        ok = all(abs(float(g[f] or 0) - float(e[f] or 0)) <= 0.011 for f in ("quantity", "unit_price", "amount"))
        num_ok += ok
        if not ok:
            num_diff.append({"sku": s, "got": [g["quantity"], g["unit_price"], g["amount"]],
                             "exp": [e["quantity"], e["unit_price"], e["amount"]]})
        name_sims.append(difflib.SequenceMatcher(None, norm_name(g["product_name"]), norm_name(e["product_name"])).ratio())
    for s in got_items:
        if s[0] in seen_skus:
            sku_seen_before += 1
    seen_skus.update(k[0] for k in exp_items)

    results.append({
        "invoice_no": inv, "bill_date": b["bill_date"], "pages": len(pngs),
        "gate_ok": bill["gate_ok"], "gate_reasons": bill["reasons"],
        "header_ok": hdr_ok, "header_all_ok": all(hdr_ok.values()),
        "header_values": {k: v[0] for k, v in hdr_cmp.items()},
        "expected_tax_id_valid_checksum": tax_db_valid,
        "items_expected": len(b["items"]), "items_got": len(parsed["items"]),
        "items_matched_by_sku": len(matched), "items_numbers_ok": num_ok, "items_number_diffs": num_diff,
        "items_missing_skus": sorted(k[0] for k in set(exp_items) - set(got_items)),
        "items_extra_skus": sorted(k[0] for k in set(got_items) - set(exp_items)),
        "name_similarity_avg": round(sum(name_sims) / len(name_sims), 3) if name_sims else None,
        "name_similarity_ge_0_8": sum(1 for x in name_sims if x >= 0.8),
        "sku_seen_in_earlier_bill": sku_seen_before,
        "totals_read": bill["totals"], "vat_sum_read": bill["vat_sum"], "items_sum": bill["items_sum"],
        "pass2": {"calls": p2stats["calls"], "secs": round(p2stats["secs"], 1)},
        "extract_secs": round(elapsed, 1),
    })
    print(f"{inv} pages={len(pngs)} gate={'PASS' if bill['gate_ok'] else 'FAIL'} hdr_ok={sum(hdr_ok.values())}/7 "
          f"items {len(matched)}/{len(b['items'])} matched, numbers ok {num_ok} | {bill['reasons'][:2]}", flush=True)

out_path.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
n = len(results)
print("\n=== SUMMARY", a.variant, "pass2" if a.pass2 else "pass1-only", "===")
print("bills:", n, "gate PASS:", sum(r["gate_ok"] for r in results))
print("header all 7 fields correct:", sum(r["header_all_ok"] for r in results))
for k in ("merchant_tax_id", "invoice_no", "bill_date", "subtotal", "vat", "amount", "discount"):
    print(f"  {k}: {sum(r['header_ok'][k] for r in results)}/{n}")
tot_exp = sum(r["items_expected"] for r in results); tot_match = sum(r["items_matched_by_sku"] for r in results)
tot_num = sum(r["items_numbers_ok"] for r in results)
print(f"items: expected {tot_exp}, matched by SKU {tot_match}, numbers exact {tot_num}")
print("items missing:", sum(len(r["items_missing_skus"]) for r in results), "extra:", sum(len(r["items_extra_skus"]) for r in results))
print("name sim>=0.8:", sum(r["name_similarity_ge_0_8"] for r in results), "/", tot_match)
print("SKU seen in earlier bill:", sum(r["sku_seen_in_earlier_bill"] for r in results), "/", sum(r["items_got"] for r in results))
print("pass2 calls:", sum(r["pass2"]["calls"] for r in results), "secs:", round(sum(r["pass2"]["secs"] for r in results), 1))
