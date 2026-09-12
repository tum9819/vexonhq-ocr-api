"""Stage 1 of the benchmark: run Tesseract (same binary/tessdata as prod image) on every page
with two preprocessing variants and dump word-level TSV + plain text + timing.

  prod : cv2 gray -> fastNlMeansDenoising -> threshold(150)  (exactly main._run_tesseract)
  otsu : cv2 gray -> Otsu threshold (no NLM denoise)          (cheaper candidate)

Usage (inside container):  python ocr_pages.py /work/data /work/out [--variants prod,otsu] [--psm 6]
"""
import sys, time, json, pathlib, argparse
import cv2, numpy as np, pytesseract

ap = argparse.ArgumentParser()
ap.add_argument("src"); ap.add_argument("out")
ap.add_argument("--variants", default="prod,otsu")
ap.add_argument("--psm", default="6")
ap.add_argument("--limit", type=int, default=0)
a = ap.parse_args()
src, out = pathlib.Path(a.src), pathlib.Path(a.out)
out.mkdir(parents=True, exist_ok=True)
variants = a.variants.split(",")
timings = {}
files = sorted(src.glob("*.png"))
if a.limit:
    files = files[: a.limit]
for f in files:
    img = cv2.imread(str(f))
    gray0 = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    for v in variants:
        gray = gray0
        t0 = time.perf_counter()
        if v == "prod":
            den = cv2.fastNlMeansDenoising(gray)
            proc = cv2.threshold(den, 150, 255, cv2.THRESH_BINARY)[1]
        elif v == "otsu":
            proc = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]
        elif v == "gray":
            proc = gray
        elif v in ("nolines", "nolines15"):
            if v == "nolines15":   # x1.5 cubic upscale (~324 DPI): fixes the fast-model '1'->'4' digit confusion
                gray = cv2.resize(gray0, None, fx=1.5, fy=1.5, interpolation=cv2.INTER_CUBIC)
            else:
                gray = gray0
            # Otsu + remove long horizontal/vertical table borders (they merge with digits: '1'->'4', '0.00'->'7000')
            binv = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]
            H, W = gray.shape
            hk = cv2.getStructuringElement(cv2.MORPH_RECT, (W // 40, 1))
            vk = cv2.getStructuringElement(cv2.MORPH_RECT, (1, H // 40))
            lines_ = cv2.bitwise_or(cv2.morphologyEx(binv, cv2.MORPH_OPEN, hk), cv2.morphologyEx(binv, cv2.MORPH_OPEN, vk))
            lines_ = cv2.dilate(lines_, cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3)))
            proc = cv2.bitwise_not(cv2.bitwise_and(binv, cv2.bitwise_not(lines_)))
        else:
            raise SystemExit(f"unknown variant {v}")
        t1 = time.perf_counter()
        cfg = f"--psm {a.psm}"
        tsv = pytesseract.image_to_data(proc, lang="tha+eng", config=cfg)
        t2 = time.perf_counter()
        (out / f"{f.stem}.{v}.tsv").write_text(tsv, encoding="utf-8")
        if v.startswith("nolines"):
            cv2.imwrite(str(out / f"{f.stem}.{v}.png"), proc)   # keep the exact OCR input for targeted cell re-OCR
        # plain text reconstructed from TSV (same words, keeps line structure)
        lines = {}
        for row in tsv.splitlines()[1:]:
            c = row.split("\t")
            if len(c) < 12 or not c[11].strip():
                continue
            key = (int(c[1]), int(c[2]), int(c[3]), int(c[4]))
            lines.setdefault(key, []).append(c[11])
        txt = "\n".join(" ".join(w) for _, w in sorted(lines.items()))
        (out / f"{f.stem}.{v}.txt").write_text(txt, encoding="utf-8")
        timings[f"{f.stem}.{v}"] = {"preprocess_s": round(t1 - t0, 2), "tesseract_s": round(t2 - t1, 2),
                                    "px": f"{gray.shape[1]}x{gray.shape[0]}"}
        print(f"{f.name} [{v}] pre={t1-t0:.1f}s ocr={t2-t1:.1f}s", flush=True)
(out / "timings.json").write_text(json.dumps(timings, indent=1))
