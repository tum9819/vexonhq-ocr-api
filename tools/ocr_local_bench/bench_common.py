"""Shared pass-2 helpers for the benchmark runners."""
import re, time
import cv2, pytesseract
import makro_extract as M


def cell_reocr(img, w, pad=6, x_from=None):
    """Targeted second pass on ONE cell: psm 7 (single line) + digit whitelist on the same cleaned image.
    x_from widens the crop to the left (cell = from the previous column's right edge), so a leading
    digit that pass 1 lost at the word boundary ("93.26" -> "3.26") is inside the crop."""
    H, W = img.shape
    x0 = max(0, (x_from if x_from is not None else w["x0"]) - pad)
    y0 = max(0, w["y"] - pad)
    x1, y1 = min(W, w["x1"] + pad), min(H, w["y"] + w["h"] + pad)
    crop = img[y0:y1, x0:x1]
    if crop.size == 0:
        return None
    crop = cv2.copyMakeBorder(crop, 20, 20, 20, 20, cv2.BORDER_CONSTANT, value=255)
    t = pytesseract.image_to_string(crop, lang="tha+eng", config="--psm 7 -c tessedit_char_whitelist=0123456789.,").strip().strip(".,")
    return t or None


def refine_page(page, img, stats):
    """Pass 2: re-OCR the payment-box cells + VAT รวม cells always (8 crops), and the numeric cells of
    item rows that fail qty x price = total (usually 0-3 rows)."""
    tot = page["totals"]
    for k in ("payment_total", "discount", "amount", "deposit", "net_amount"):
        w = tot.get(k + "_word")
        if w:
            alt = cell_reocr(img, w); stats["calls"] += 1
            if alt and M._MONEY.match(alt):
                tot[k] = M._money(alt)
    # header tax id: re-OCR the digit groups when the checksum fails (a '1'->'4' in the header)
    tid = page["header"].get("tax_id")
    if tid and not M.thai_tax_id_valid(tid) and page.get("tax_id_words"):
        ws = page["tax_id_words"]
        alt = cell_reocr(img, {"x0": ws[0]["x0"], "x1": ws[-1]["x1"], "y": min(w["y"] for w in ws), "h": max(w["h"] for w in ws)})
        stats["calls"] += 1
        if alt:
            d = re.sub(r"\D", "", alt)
            if len(d) == 13 and M.thai_tax_id_valid(d):
                page["header"]["tax_id"] = d
    for it in page["items"]:
        w = it["_ws"].get("sku")
        if w and w["conf"] < 60:
            alt = cell_reocr(img, w); stats["calls"] += 1
            core = re.sub(r"\D", "", alt or "")
            if re.fullmatch(r"\d{5,7}", core):
                it["sku"] = core
    # VAT table: re-OCR the รวม row AND the per-code rows (the gate now checks them too; review-2 #2)
    vs = page.get("vat_sum")
    for row in ([vs] if vs and vs.get("ws") else []) + [r for r in page.get("vat_rows", []) if r.get("ws")]:
        vals = []
        prev_x1 = None
        for w in row["ws"]:
            alt = cell_reocr(img, w, x_from=(prev_x1 + 12) if prev_x1 else None); stats["calls"] += 1
            prev_x1 = w["x1"]
            vals.append(M._money(alt) if alt and M._MONEY.match(alt) else None)
        for key, v in zip(("goods", "tax", "total"), vals):
            if v is not None:
                row[key] = v
    for it in page["items"]:
        if M.row_arith_ok(it["quantity"], it["unit_price"], it["amount"]):
            continue
        cand = {}
        for f, key in (("qty", "quantity"), ("price", "unit_price"), ("total", "amount")):
            w = it["_ws"].get(f)
            if w:
                alt = cell_reocr(img, w); stats["calls"] += 1
                if alt and (M._MONEY.match(alt) or M._NUM.match(alt)):
                    cand[key] = M._money(alt) if f != "qty" else float(alt.replace(",", "."))
        # try replacements ONE field at a time only. A qty+price pair that merely ties (2 x 50 vs 1 x 100)
        # is not evidence of correctness, so multi-field combos are never accepted (review finding #2).
        trials = [{k: v} for k, v in cand.items()]
        for tr in trials:
            q = tr.get("quantity", it["quantity"]); up = tr.get("unit_price", it["unit_price"]); a = tr.get("amount", it["amount"])
            if M.row_arith_ok(q, up, a):
                it["quantity"], it["unit_price"], it["amount"] = q, up, a
                it["repaired"] = tr
                break


def norm_name(s):
    return re.sub(r"[\s|().,\[\]'\"]", "", (s or "")).lower()


