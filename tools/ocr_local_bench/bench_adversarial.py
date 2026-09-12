"""Adversarial gate test: inject realistic OCR errors into the (clean) pass-1 TSVs and check that
assemble_bill() REFUSES the bill (gate_ok False). Runs on TSV text only (no re-OCR), because the
question is "does the arithmetic gate catch a wrong number", not "does pass 2 repair it".

Mutations (per bill, one at a time):
  item_total_digit   : change one digit of one item TOTAL cell (e.g. 296.00 -> 296.60)
  item_price_digit   : change one digit of one item UNIT PRICE cell
  item_qty_1to4      : change an item QUANTITY "1" -> "4"  (the fast-model confusion seen live)
  drop_item_row      : delete one whole item row (missed line)
  dup_item_row       : duplicate one item row (double-read line)
  payment_total_digit: change one digit of the payment-box TOTAL
  amount_digit       : change one digit of AMOUNT (and NET AMOUNT together, as a consistent misread)
  vat_goods_digit    : change one digit of the VAT รวม goods value
  date_digit         : change one digit of the invoice date day
  invoice_no_digit   : change one digit of the invoice number  (NOT arithmetically checkable -> expect PASS)
  drop_last_page     : remove the last page (totals page) entirely
"""
import json, pathlib, re, random, copy
import makro_extract as M

ROOT = pathlib.Path(__file__).parent
bills = json.loads((ROOT / "expected.json").read_text(encoding="utf-8"))
VARIANT = "nolines15"
random.seed(7)


def load_pages(inv):
    pngs = sorted((ROOT / "data").glob(f"{inv}-p*.png"), key=lambda p: int(p.stem.split("-p")[1]))
    return [(ROOT / "out" / f"{p.stem}.{VARIANT}.tsv").read_text(encoding="utf-8") for p in pngs]


def tsv_rows(tsv):
    return [r.split("\t") for r in tsv.splitlines()]


def rows_to_tsv(rows):
    return "\n".join("\t".join(r) for r in rows)


def mutate_digit(s):
    """Change one digit of a numeric string to a different digit (keeps separators)."""
    idx = [i for i, ch in enumerate(s) if ch.isdigit()]
    i = random.choice(idx)
    new = str((int(s[i]) + random.randint(1, 9)) % 10)
    return s[:i] + new + s[i + 1:]


def find_item_words(page, field):
    return [it["_ws"][field] for it in page["items"] if it["_ws"].get(field)]


def apply(tsv_pages, mutation):
    """Return mutated tsv_pages (list of str) or None if the mutation is not applicable."""
    pages = [M.parse_page(t) for t in tsv_pages]
    rows_pages = [tsv_rows(t) for t in tsv_pages]

    def replace_word(pi, w, new_text):
        for r in rows_pages[pi]:
            if len(r) >= 12 and r[6] == str(w["x0"]) and r[7] == str(w["y"]) and r[11] == (w.get("text_pass1") or w["text"]):
                r[11] = new_text
                return True
        return False

    item_pages = [i for i, p in enumerate(pages) if p["items"]]
    last = len(pages) - 1
    if mutation in ("item_total_digit", "item_price_digit", "item_qty_1to4", "drop_item_row", "dup_item_row"):
        if not item_pages:
            return None
        pi = random.choice(item_pages)
        it = random.choice(pages[pi]["items"])
        if mutation == "item_total_digit":
            w = it["_ws"]["total"]; return_ok = replace_word(pi, w, mutate_digit(w["text"]))
        elif mutation == "item_price_digit":
            w = it["_ws"]["price"]
            if not w: return None
            return_ok = replace_word(pi, w, mutate_digit(w["text"]))
        elif mutation == "item_qty_1to4":
            cands = [x for x in pages[pi]["items"] if x["_ws"].get("qty") and x["_ws"]["qty"]["text"] == "1"]
            if not cands: return None
            w = random.choice(cands)["_ws"]["qty"]; return_ok = replace_word(pi, w, "4")
        elif mutation == "drop_item_row":
            y = it["_ws"]["item"]["y"]
            rows_pages[pi] = [r for r in rows_pages[pi] if not (len(r) >= 12 and r[7].isdigit() and r[11].strip() and abs(int(r[7]) - y) <= 12)]
            return_ok = True
        else:  # dup_item_row
            y = it["_ws"]["item"]["y"]
            dup = [copy.copy(r) for r in rows_pages[pi] if len(r) >= 12 and r[7].isdigit() and r[11].strip() and abs(int(r[7]) - y) <= 12]
            for r in dup:
                r[4] = str(int(r[4]) + 500)   # new line_num so it groups as its own line
                r[7] = str(int(r[7]) + 40)
            rows_pages[pi].extend(dup); return_ok = True
        if not return_ok: return None
    elif mutation in ("payment_total_digit", "amount_digit", "vat_goods_digit"):
        tot = pages[last]["totals"]
        if mutation == "payment_total_digit":
            w = tot.get("payment_total_word")
            if not w: return None
            if not replace_word(last, w, mutate_digit(w["text"])): return None
        elif mutation == "amount_digit":
            w1, w2 = tot.get("amount_word"), tot.get("net_amount_word")
            if not w1: return None
            new = mutate_digit(w1["text"])
            if not replace_word(last, w1, new): return None
            if w2: replace_word(last, w2, new)
        else:
            vs = pages[last].get("vat_sum")
            if not vs or not vs.get("ws"): return None
            w = vs["ws"][0]
            if not replace_word(last, w, mutate_digit(w["text"])): return None
    elif mutation == "date_digit":
        done = False
        for r in rows_pages[0]:
            if len(r) >= 12 and re.fullmatch(r"\d{2}/\d{2}/\d{4}", r[11]):
                d = int(r[11][:2]); nd = (d % 28) + 1
                r[11] = f"{nd:02d}" + r[11][2:]; done = True; break
        if not done: return None
    elif mutation == "invoice_no_digit":
        done = False
        for r in rows_pages[0]:
            if len(r) >= 12 and re.fullmatch(r"\d{12}", r[11]):
                r[11] = mutate_digit(r[11]); done = True; break
        if not done: return None
    elif mutation == "drop_last_page":
        if len(rows_pages) < 2: return None
        rows_pages = rows_pages[:-1]
    else:
        raise ValueError(mutation)
    return [rows_to_tsv(r) for r in rows_pages]


MUTATIONS = ["item_total_digit", "item_price_digit", "item_qty_1to4", "drop_item_row", "dup_item_row",
             "payment_total_digit", "amount_digit", "vat_goods_digit", "date_digit", "invoice_no_digit",
             "drop_last_page"]
summary = {m: {"applied": 0, "rejected": 0, "passed": []} for m in MUTATIONS}
baseline_pass = 0
for b in bills:
    tsvs = load_pages(b["invoice_no"])
    base = M.assemble_bill([M.parse_page(t) for t in tsvs])
    baseline_pass += base["gate_ok"]
    for m in MUTATIONS:
        for trial in range(3):   # 3 random placements per mutation per bill
            mut = apply(tsvs, m)
            if mut is None:
                break
            res = M.assemble_bill([M.parse_page(t) for t in mut])
            summary[m]["applied"] += 1
            if res["gate_ok"]:
                summary[m]["passed"].append(b["invoice_no"])
            else:
                summary[m]["rejected"] += 1
print("baseline (unmutated, pass-1 only) gate PASS:", baseline_pass, "/", len(bills))
print(f"{'mutation':22s} {'applied':>7s} {'rejected':>8s}  leaked (should be 0 except invoice_no_digit)")
for m in MUTATIONS:
    s = summary[m]
    print(f"{m:22s} {s['applied']:7d} {s['rejected']:8d}  {len(s['passed'])} {sorted(set(s['passed']))[:5]}")
(ROOT / "out" / "adversarial.json").write_text(json.dumps(summary, indent=1))
