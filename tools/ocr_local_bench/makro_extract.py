"""Rule-based extractor for Makro (CP Axtra) RECEIPT / TAX INVOICE pages from Tesseract
`image_to_data` TSV. PURE: text in -> dict out, no I/O, no network. Benchmark prototype —
NOT production code. If it graduates, it plugs into main._ocr_page behind LOCAL_OCR_ENABLED.

Layout facts (fixed A4, rendered 1786x2526 by main._pdf_to_images scale=3.0):
  header : Tax ID "0 10 7 567 00041 4", "Tax Invoice No. <12 digits>", "Tax Invoice Date dd/mm/yyyy",
           "หน้าที่ i จาก N", "Payment type ..."
  items  : ITEM | ARTICLE NO | DESCRIPTION | QUANTITY | UNIT | UNIT PRICE | VAT CODE | TOTAL
  last   : VAT table (จำนวนชิ้น | รหัส ภ.พ. | ราคาสินค้า | ภาษี | รวม) + payment box
           TOTAL / DISCOUNT / AMOUNT / DEPOSIT / NET AMOUNT (numbers right-aligned in the TOTAL column)
Column bands are fractions of page width so a different render scale still works.
"""
from __future__ import annotations
import re
from typing import Any, Optional

MAKRO_TAX_ID = "0107567000414"
MAKRO_VENDOR_NAME = "บริษัท ซีพี แอ็กซ์ตร้า จำกัด (มหาชน)"

# x-bands as fraction of page width: (left, right)
BANDS = {
    "item":  (0.045, 0.095),
    "sku":   (0.095, 0.175),
    "desc":  (0.175, 0.495),
    "qty":   (0.495, 0.575),
    "unit":  (0.575, 0.665),
    "price": (0.665, 0.775),
    "vat":   (0.775, 0.855),
    "total": (0.855, 0.960),
}

_MONEY = re.compile(r"^-?\d{1,3}(,\d{3})*(\.\d{2})$|^-?\d+\.\d{2}$|^-?\d+,\d{2}$")
_NUM = re.compile(r"^\d+(\.\d+)?$")
_MONEYISH = re.compile(r"^\d[\d.,]*\d$")   # loose candidate for pass-2 cell re-OCR
_THAI = re.compile(r"[฀-๿]")


def thai_tax_id_valid(s: str) -> bool:
    """Thai 13-digit ID checksum (mod 11)."""
    d = re.sub(r"\D", "", s or "")
    if len(d) != 13:
        return False
    total = sum(int(d[i]) * (13 - i) for i in range(12))
    return (11 - total % 11) % 10 == int(d[12])


def join_thai(tokens: list[str]) -> str:
    """Tesseract emits Thai one glyph per 'word'. Re-join: no space between two Thai
    glyphs; keep a single space between Latin/number tokens."""
    out = ""
    prev_thai = False
    for t in tokens:
        if not t:
            continue
        is_thai = bool(_THAI.search(t[0])) or t[0] in ".,/()%-"
        if out and not (prev_thai and (bool(_THAI.search(t[0])) or t[0] in "./()%-")):
            out += " "
        out += t
        prev_thai = bool(_THAI.search(t[-1]))
    return re.sub(r"\s+", " ", out).strip()


def parse_tsv(tsv: str) -> tuple[list[dict], int, int]:
    """TSV -> list of lines; each line = {'y','words':[{'x0','x1','y','text','conf'}]} sorted by y then x."""
    rows = tsv.splitlines()
    width = height = 0
    lines: dict[tuple, dict] = {}
    for r in rows[1:]:
        c = r.split("\t")
        if len(c) < 12:
            continue
        level = int(c[0])
        if level == 1:
            width, height = int(c[8]), int(c[9])
            continue
        text = c[11].strip()
        if level != 5 or not text:
            continue
        key = (int(c[2]), int(c[3]), int(c[4]))
        x0, y0, w, h = int(c[6]), int(c[7]), int(c[8]), int(c[9])
        ln = lines.setdefault(key, {"y": y0, "words": []})
        ln["words"].append({"x0": x0, "x1": x0 + w, "y": y0, "h": h, "text": text, "conf": float(c[10])})
    out = []
    for ln in lines.values():
        ln["words"].sort(key=lambda w: w["x0"])
        ln["y"] = min(w["y"] for w in ln["words"])
        out.append(ln)
    out.sort(key=lambda l: (l["y"], l["words"][0]["x0"]))
    return out, width, height


def _in_band(w: dict, band: str, width: int) -> bool:
    lo, hi = BANDS[band]
    cx = (w["x0"] + w["x1"]) / 2 / width
    return lo <= cx < hi


def _money(s: str) -> Optional[float]:
    if re.fullmatch(r"-?\d+,\d{2}", s):      # OCR read the decimal point as a comma ("91,00")
        s = s.replace(",", ".")
    if re.fullmatch(r"-?\d{1,3}(\.\d{3})+\.\d{2}", s):   # thousands separator read as a dot ("1.076.77")
        head, dec = s.rsplit(".", 1)
        s = head.replace(".", "") + "." + dec
    s = s.replace(",", "")
    try:
        v = float(s)
    except ValueError:
        return None
    return v


def _line_text(ln: dict) -> str:
    return " ".join(w["text"] for w in ln["words"])


def parse_page(tsv: str, reocr=None) -> dict[str, Any]:
    """reocr(word) -> Optional[str]: optional second-pass numeric OCR on a word box (benchmark hook).
    Applied to words that look numeric OR sit in a numeric column band; the replacement is used
    only when it is a well-formed number."""
    lines, width, height = parse_tsv(tsv)
    if reocr is not None:
        for ln in lines:
            for w in ln["words"]:
                numeric_band = any(_in_band(w, b, width) for b in ("qty", "price", "vat", "total"))
                looks_num = bool(re.fullmatch(r"[\d,.()\[\]|]+", w["text"]))
                if (numeric_band or looks_num) and w["h"] >= 8:
                    alt = reocr(w)
                    if alt and (_MONEY.match(alt) or _NUM.match(alt)):
                        if alt != w["text"]:
                            w["text_pass1"] = w["text"]
                            w["text"] = alt
    page: dict[str, Any] = {"header": {}, "items": [], "totals": {}, "vat_rows": [], "vat_sum": None,
                            "warnings": []}
    hdr = page["header"]
    full_join = "\n".join(join_thai([w["text"] for w in ln["words"]]) for ln in lines)
    flat = re.sub(r"\s+", " ", full_join)

    # ---- header (regex on joined text; labels are bilingual, English part is reliable) ----
    m = re.search(r"(?:ภาษีอากร|Tax\s*ID)\D{0,10}((?:\d[ ]?){13})", flat)
    if m:
        hdr["tax_id"] = re.sub(r"\D", "", m.group(1))
        # remember the digit words of that line (top-of-page line holding 13 digits in <=6 groups)
        for ln in lines[:12]:
            digs = [w for w in ln["words"] if re.fullmatch(r"\d{1,6}", w["text"])]
            if digs and len("".join(w["text"] for w in digs)) == 13:
                page["tax_id_words"] = digs
                break
    m = re.search(r"Tax Invoice No\.?\s*(\d{12})", flat)
    if m:
        hdr["invoice_no"] = m.group(1)
    m = re.search(r"Tax Invoice Date\s*(\d{2})/(\d{2})/(\d{4})", flat)
    if m:
        dd, mm, yyyy = m.groups()
        hdr["invoice_date"] = f"{yyyy}-{mm}-{dd}"
    m = re.search(r"Order No\.?\s*(\d{8,12}[A-Z]?)", flat)
    if m:
        hdr["order_no"] = m.group(1)
    m = re.search(r"(?:หน[้่]?าท\S{0,3}|wu|INVOICE\W{0,6})\s*(\d)\s*จาก\s*(\d)", flat)
    if m:
        hdr["page_no"], hdr["page_total"] = int(m.group(1)), int(m.group(2))
    m = re.search(r"Payment type\s*([A-Za-z ]{3,40})", flat)
    if m:
        hdr["payment_type_raw"] = m.group(1).strip()

    # ---- items: a line whose item-band token is an int and sku-band token is 5-7 digits ----
    items = page["items"]
    last_item = None
    for ln in lines:
        ws = ln["words"]
        by = {b: [w for w in ws if _in_band(w, b, width)] for b in BANDS}
        item_tok = [w for w in by["item"] if _NUM.match(w["text"]) and "." not in w["text"]]
        sku_tok = []
        for w in by["sku"]:
            core = re.sub(r"\D", "", w["text"])
            if re.fullmatch(r"\d{5,7}", core) and len(w["text"]) - len(core) <= 2:
                if core != w["text"]:
                    w["text_pass1"] = w["text"]; w["text"] = core
                sku_tok.append(w)
        total_tok = [w for w in by["total"] if _MONEY.match(w["text"])]
        price_tok = [w for w in by["price"] if _MONEY.match(w["text"])]
        qty_tok = [w for w in by["qty"] if _NUM.match(w["text"])]
        if item_tok and sku_tok and total_tok:
            it = {
                "line_no": int(item_tok[0]["text"]),
                "sku": sku_tok[0]["text"],
                "name_raw": join_thai([w["text"] for w in by["desc"]]),
                "quantity": float(qty_tok[-1]["text"]) if qty_tok else None,
                "unit": " ".join(w["text"] for w in by["unit"]) or None,
                "unit_price": _money(price_tok[-1]["text"]) if price_tok else None,
                "vat_code": next((w["text"] for w in by["vat"] if re.fullmatch(r"\d", w["text"])), None),
                "amount": _money(total_tok[-1]["text"]),
                "y": ln["y"],
                "_ws": {"qty": qty_tok[-1] if qty_tok else None, "price": price_tok[-1] if price_tok else None,
                        "total": total_tok[-1], "item": item_tok[0], "sku": sku_tok[0]},
                "min_conf": min(w["conf"] for w in (item_tok[:1] + sku_tok[:1] + total_tok[-1:] + price_tok[-1:] + qty_tok[-1:])),
            }
            items.append(it)
            last_item = it
            continue
        # wrapped description continuation: only desc-band words, right below an item row
        if last_item and by["desc"] and not total_tok and not price_tok and not sku_tok and not item_tok \
                and 0 < ln["y"] - last_item["y"] < height * 0.03:
            extra = join_thai([w["text"] for w in by["desc"]])
            if extra and not re.search(r"รวม|ภาษี|TOTAL|AMOUNT", extra):
                last_item["name_raw"] = (last_item["name_raw"] + " " + extra).strip()
                last_item["y"] = ln["y"]

    # ---- payment box (last page): the 5 money cells in the TOTAL column BELOW the item rows are, in
    # print order, TOTAL / DISCOUNT / AMOUNT / DEPOSIT / NET AMOUNT. Position is the primary key;
    # the (bilingual) labels only confirm. Same for the VAT table: money triplets left of the
    # price band, the last one being the รวม row.
    tot = page["totals"]
    last_item_y = max((it["y"] for it in items), default=0)
    box_rows = []
    vat_triplets = []
    for ln in lines:
        if ln["y"] <= last_item_y:
            continue
        money_ws = [w for w in ln["words"] if _MONEY.match(w["text"]) or (_MONEYISH.match(w["text"]) and len(w["text"]) >= 3)]
        if not money_ws:
            continue
        right = [w for w in money_ws if _in_band(w, "total", width)]
        left = [w for w in money_ws if (w["x0"] + w["x1"]) / 2 / width < BANDS["price"][0]]
        if right:
            box_rows.append({"y": ln["y"], "val": _money(right[-1]["text"]), "w": right[-1],
                             "label": _line_text(ln).upper()})
        if len(left) >= 3:
            joined = join_thai([w["text"] for w in ln["words"]])
            vat_triplets.append({"y": ln["y"], "vals": [_money(w["text"]) for w in left[-3:]], "ws": left[-3:],
                                 "is_sum": bool(re.match(r"^\S{0,3}รวม", joined)),
                                 "lead": " ".join(w["text"] for w in ln["words"] if w not in left[-3:])})
    box_rows.sort(key=lambda r: r["y"])
    # The VAT table sits ABOVE the payment box; a footer line ("จำนวนเงิน/ Amount x 0.00 0.00 x")
    # below the box also carries 3 left-band numbers and must not be mistaken for a VAT row.
    if box_rows:
        first_right_y = box_rows[0]["y"]
        vat_triplets = [t for t in vat_triplets if t["y"] < first_right_y]
    names = ["payment_total", "discount", "amount", "deposit", "net_amount"]
    if box_rows:
        # The payment box starts right after the VAT table's รวม row; a footer line
        # ("จำนวนเงิน/ Amount ... ") further down also has a number in this band, so take the
        # FIRST 5 right-band rows below the รวม row (or below the last item when no VAT table).
        sum_y = max((t["y"] for t in vat_triplets), default=last_item_y)
        after = [r for r in box_rows if r["y"] > sum_y]

        def _ties(win):
            v = [r["val"] for r in win]
            if any(x is None for x in v):
                return False
            return abs(round(v[0] - v[1], 2) - v[2]) <= 0.005 and abs(round(v[2] - v[3], 2) - v[4]) <= 0.005

        chosen = None
        for i in range(0, max(0, len(after) - 4)):
            if _ties(after[i:i + 5]):
                chosen = after[i:i + 5]
                break
        if chosen is None:
            # anchor on the most distinctive label, else take the first 5
            idx = next((i for i, r in enumerate(after) if "DISCOUNT" in r["label"]), None)
            if idx is not None and idx >= 1:
                chosen = after[idx - 1: idx + 4]
            else:
                chosen = after[:5]
        label_hits = 0
        for name, r in zip(names, chosen):
            tot[name] = r["val"]
            tot[name + "_word"] = r["w"]
            expect = {"payment_total": "TOTAL", "discount": "DISCOUNT", "amount": "AMOUNT",
                      "deposit": "DEPOSIT", "net_amount": "NET"}[name]
            if expect in r["label"]:
                label_hits += 1
        tot["_box_tied"] = chosen is not None and len(chosen) == 5 and _ties(chosen)
        tot["_box_label_hits"] = label_hits
        # raw OCR text of the discount cell, for the discount-inference corroboration in assemble_bill
        if len(chosen) == 5:
            tot["_discount_raw"] = chosen[1]["w"].get("text_pass1") or chosen[1]["w"]["text"]
    if vat_triplets:
        sum_row = next((t for t in reversed(vat_triplets) if t["is_sum"]), None) or vat_triplets[-1]
        page["vat_sum"] = {"goods": sum_row["vals"][0], "tax": sum_row["vals"][1], "total": sum_row["vals"][2],
                           "ws": sum_row["ws"]}
        page["vat_rows"] = [{"lead": t["lead"], "goods": t["vals"][0], "tax": t["vals"][1], "total": t["vals"][2],
                             "ws": t["ws"]}
                            for t in vat_triplets if t is not sum_row]
    return page


def row_arith_ok(q, up, a) -> bool:
    """qty x unit_price == total, allowing Makro's nearest-0.25 rounding on weight items."""
    if q is None or up is None or a is None:
        return False
    exp = q * up
    return abs(exp - a) <= 0.005 or abs(round(exp * 4) / 4 - a) <= 0.005


def _r2(x: Optional[float]) -> Optional[float]:
    return None if x is None else round(x + 1e-9, 2)


def assemble_bill(pages: list[dict[str, Any]]) -> dict[str, Any]:
    """Merge per-page parses (in page order) into the app's parsed-invoice shape + gate result."""
    hdr: dict[str, Any] = {}
    for p in pages:
        for k, v in p["header"].items():
            hdr.setdefault(k, v)
    items = [dict(it, source_page=i + 1) for i, p in enumerate(pages) for it in p["items"]]
    last = pages[-1]
    tot = {}
    box_label_hits = None
    discount_raw = None
    for p in pages:
        tot.update({k: v for k, v in p["totals"].items() if v is not None and not k.endswith("_word") and not k.startswith("_")})
        if p["totals"].get("_box_label_hits") is not None:
            box_label_hits = p["totals"]["_box_label_hits"]
            discount_raw = p["totals"].get("_discount_raw")
    vat_sum = next((p["vat_sum"] for p in reversed(pages) if p["vat_sum"]), None)
    vat_rows = next((p["vat_rows"] for p in reversed(pages) if p["vat_sum"] and p["vat_rows"]), [])

    checks: dict[str, bool] = {}
    reasons: list[str] = []

    def chk(name: str, ok: bool, why: str = "") -> None:
        checks[name] = bool(ok)
        if not ok:
            reasons.append(f"{name}: {why}")

    tax = hdr.get("tax_id")
    chk("tax_id_checksum", bool(tax) and thai_tax_id_valid(tax), f"tax_id={tax}")
    chk("tax_id_is_makro", tax == MAKRO_TAX_ID, f"tax_id={tax}")
    chk("invoice_no_12d", bool(re.fullmatch(r"\d{12}", hdr.get("invoice_no") or "")), f"invoice_no={hdr.get('invoice_no')}")
    d = hdr.get("invoice_date")

    def _real_date(x):
        try:
            import datetime as _dt
            y, m_, dd = int(x[:4]), int(x[5:7]), int(x[8:10])
            _dt.date(y, m_, dd)              # rejects 2026-02-31 etc. (review #4)
            return 2020 <= y <= 2035
        except (TypeError, ValueError):
            return False
    chk("date_parse", bool(d) and _real_date(d), f"date={d}")
    page_nos = [p["header"].get("page_no") for p in pages]
    page_totals = {p["header"].get("page_total") for p in pages if p["header"].get("page_total")}
    found = [(i + 1, n) for i, n in enumerate(page_nos) if n is not None]
    # hard-fail only on a CONTRADICTION (a marker that says a different page index / total);
    # a garbled/missing marker is soft — completeness is proven by sum(items)==TOTAL below.
    chk("page_markers", all(i == n for i, n in found) and page_totals <= {len(pages)},
        f"page_nos={page_nos} totals={page_totals} n={len(pages)}")
    checks_soft_page_markers = page_nos == list(range(1, len(pages) + 1)) and page_totals == {len(pages)}
    chk("items_present", len(items) > 0, "no item rows parsed")
    line_nos = [it["line_no"] for it in items]
    line_no_ok = line_nos == list(range(1, len(items) + 1))   # soft: reported only (1->4 misreads on the ITEM column)
    bad_rows = []
    for it in items:
        q, up, a = it.get("quantity"), it.get("unit_price"), it.get("amount")
        if q is None and up and a and up > 0 and "EACH" in (it.get("unit") or "").upper():
            # blank/unread QUANTITY cell: for EACH rows the quantity is an integer = total / unit price
            # (weight rows carry a 3-decimal quantity - never infer those; review #7)
            ratio = a / up
            if abs(ratio - round(ratio)) <= 0.001 and 1 <= round(ratio) <= 99:
                q = float(round(ratio)); it["quantity"] = q; it["qty_inferred"] = True
        if q is None or up is None or a is None:
            bad_rows.append((it["line_no"], "missing"))
            continue
        exp = q * up
        # Makro rounds weight-item totals to the NEAREST 0.25; EACH items are exact.
        exp_q = round(exp * 4) / 4
        if abs(exp - a) > 0.011 and abs(exp_q - a) > 0.011:
            bad_rows.append((it["line_no"], f"{q}x{up}={exp:.2f} vs {a}"))
    chk("row_arith", not bad_rows, f"{bad_rows}")
    items_sum = _r2(sum(it["amount"] for it in items if it.get("amount") is not None))
    pt = tot.get("payment_total")
    chk("items_sum_eq_total", pt is not None and abs(items_sum - pt) <= 0.005, f"sum(items)={items_sum} TOTAL={pt}")
    chk("payment_box_labels", box_label_hits is not None and box_label_hits >= 2,
        f"payment box rows carry only {box_label_hits} of 5 expected labels (TOTAL/DISCOUNT/AMOUNT/DEPOSIT/NET)")
    disc = tot.get("discount", 0.0) or 0.0
    amt = tot.get("amount")
    # DISCOUNT is fully determined by TOTAL - AMOUNT. When the discount cell misreads (e.g. a scanner
    # speck: "21.00" -> "2100") but TOTAL is corroborated by sum(items) and AMOUNT by the VAT รวม
    # total, infer it and flag it — the arithmetic identity still gets checked below.
    disc_inferred = False
    if pt is not None and amt is not None and abs(_r2(pt - disc) - amt) > 0.011:
        if abs(items_sum - pt) <= 0.005 and vat_sum and abs(vat_sum["total"] - amt) <= 0.005 and pt >= amt:
            cand = _r2(pt - amt)
            raw_digits = re.sub(r"\D", "", discount_raw or "")
            cand_digits = f"{cand:.2f}".replace(".", "")
            # only when the OCR'd cell holds the same digit string (a lost separator, not lost digits; review #6).
            # cand == 0 is handled explicitly instead of via lstrip('0') == '' (review-2 #4).
            if raw_digits and (
                (cand == 0.0 and set(raw_digits) == {"0"})
                or (cand != 0.0 and raw_digits.lstrip("0") == cand_digits.lstrip("0"))
            ):
                disc = cand; disc_inferred = True
    chk("total_minus_discount_eq_amount", pt is not None and amt is not None and abs(_r2(pt - disc) - amt) <= 0.005,
        f"TOTAL={pt} DISCOUNT={disc} AMOUNT={amt}")
    net = tot.get("net_amount")
    dep = tot.get("deposit", 0.0) or 0.0
    chk("net_amount_consistent", net is None or amt is None or abs(_r2(amt - dep) - net) <= 0.005, f"AMOUNT={amt} DEPOSIT={dep} NET={net}")
    chk("vat_sum_row_found", vat_sum is not None, "no รวม row in VAT table")
    if vat_sum:
        chk("vat_goods_plus_tax_eq_total", abs(_r2(vat_sum["goods"] + vat_sum["tax"]) - vat_sum["total"]) <= 0.005,
            f"{vat_sum}")
        # Review-2 #2: the รวม row must be the SUM of the per-VAT-code rows above it, and each row must obey
        # the Thai VAT rule (code 1 exempt -> tax 0; code 2 -> tax = 7% of goods). A column shift or a
        # misread that keeps goods+tax==total cannot satisfy both of these at once.
        rows_ok = bool(vat_rows)
        row_msgs = []
        for r in vat_rows:
            rate_ok = (r["tax"] == 0.0) or abs(r["goods"] * 0.07 - r["tax"]) <= 0.02
            add_ok = abs(_r2(r["goods"] + r["tax"]) - r["total"]) <= 0.005
            if not (rate_ok and add_ok):
                rows_ok = False
                row_msgs.append(f"row {r['lead']!r}: goods={r['goods']} tax={r['tax']} total={r['total']}")
        if vat_rows:
            for key in ("goods", "tax", "total"):
                if abs(_r2(sum(r[key] for r in vat_rows)) - vat_sum[key]) > 0.005:
                    rows_ok = False
                    row_msgs.append(f"sum({key})={_r2(sum(r[key] for r in vat_rows))} != รวม {vat_sum[key]}")
        chk("vat_rows_consistent", rows_ok, "; ".join(row_msgs) or "no per-code VAT rows parsed")
        chk("vat_total_eq_amount", amt is not None and abs(vat_sum["total"] - amt) <= 0.005, f"vat_total={vat_sum['total']} AMOUNT={amt}")
    gate_ok = all(checks.values())   # (soft checks are appended after this line)

    pt_raw = (hdr.get("payment_type_raw") or "").lower()
    payment_type = "credit_card" if "credit" in pt_raw or "card" in pt_raw else (
        "transfer" if "transfer" in pt_raw else ("cash" if "cash" in pt_raw else ("other" if pt_raw else None)))
    parsed = {
        "vendor_name": MAKRO_VENDOR_NAME,
        "merchant_tax_id": tax,
        "invoice_no": hdr.get("invoice_no"),
        "bill_date": d,
        "due_date": None,
        "subtotal": vat_sum["goods"] if vat_sum else None,
        "vat": vat_sum["tax"] if vat_sum else None,
        "amount": amt,
        "payment_type": payment_type,
        "currency": "THB",
        "items": [{"line_no": it["line_no"], "sku": it["sku"], "product_name": it["name_raw"],
                   "quantity": it["quantity"], "unit": it["unit"], "unit_price": it["unit_price"],
                   "amount": it["amount"], "source_page": it["source_page"]} for it in items],
        "discount": {"line_items_discount_pct": None,
                     "whole_bill_discount_amount": disc if disc else None,
                     "whole_bill_discount_pct": None, "note": None},
        "notes": None,
    }
    checks["line_no_contiguous(soft)"] = line_no_ok
    checks["page_markers_all_read(soft)"] = checks_soft_page_markers
    checks["discount_inferred(info)"] = disc_inferred
    return {"parsed": parsed, "gate_ok": gate_ok, "checks": checks, "reasons": reasons,
            "totals": tot, "vat_sum": vat_sum, "items_sum": items_sum,
            "min_item_conf": min((it["min_conf"] for it in items), default=None)}
