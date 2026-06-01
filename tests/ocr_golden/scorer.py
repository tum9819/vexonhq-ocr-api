"""
scorer.py — field-level accuracy scoring for invoice/slip OCR.

Audit Testing-phase remediation (2026-05-31): OCR is the money-data gateway but
its accuracy was never measured. This module turns an (expected, actual) pair
into a field-level accuracy score so the real number can be tracked over time
and before/after a model change.

Two ways to use it:

1. OFFLINE (CI, no API key) — score a known-good `expected` against a
   pre-recorded `actual` (the synthetic fixtures in cases/). This proves the
   SCORER works. `tests/test_ocr_golden.py` does this.

2. LIVE (local, needs OPENAI_API_KEY) — run the real OCR pipeline on a real
   image kept OUTSIDE the repo and score it against a hand-checked expected:
       python -m tests.ocr_golden.scorer --live <image_path> <expected.json>
   This is how TUM measures the true production accuracy. NOT run in CI, and
   real financial documents are never committed to the repo.

Design: pure logic, no network/DB import at module load (the --live path imports
main lazily inside the function), so importing this module is always safe.
"""

from __future__ import annotations

import json
import re
from typing import Any, Optional

# Scalar fields compared as text (exact match after normalization).
TEXT_FIELDS = ["vendor_name", "invoice_no", "bill_date", "merchant_tax_id"]
# Scalar fields compared as numbers (within MONEY_TOL).
MONEY_FIELDS = ["amount", "subtotal", "vat"]
MONEY_TOL = 0.01


def normalize_text(v: Any) -> str:
    """Lowercase, strip, collapse internal whitespace. None -> ''."""
    if v is None:
        return ""
    s = str(v).strip().lower()
    return re.sub(r"\s+", " ", s)


def to_number(v: Any) -> Optional[float]:
    """Parse a number that may carry commas/currency text. None on failure."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = re.sub(r"[^\d.\-]", "", str(v))
    if s in ("", "-", ".", "-."):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def nums_match(a: Any, b: Any, tol: float = MONEY_TOL) -> bool:
    na, nb = to_number(a), to_number(b)
    if na is None and nb is None:
        return True
    if na is None or nb is None:
        return False
    return abs(na - nb) <= tol


def _item_key(item: dict) -> tuple:
    """Identity of a line item for matching: normalized name + qty + total."""
    name = normalize_text(item.get("product_name") or item.get("name"))
    qty = to_number(item.get("qty") or item.get("quantity"))
    total = to_number(item.get("total") or item.get("amount") or item.get("line_total"))
    return (name, qty, total)


def score_items(expected: list, actual: list) -> dict:
    """Precision/recall/F1 of line items matched on (name, qty, total)."""
    exp = [e for e in (expected or []) if isinstance(e, dict)]
    act = [a for a in (actual or []) if isinstance(a, dict)]
    if not exp and not act:
        return {"precision": 1.0, "recall": 1.0, "f1": 1.0, "expected": 0, "actual": 0, "matched": 0}

    exp_keys = [_item_key(e) for e in exp]
    act_keys = [_item_key(a) for a in act]
    remaining = list(exp_keys)
    matched = 0
    for k in act_keys:
        if k in remaining:
            remaining.remove(k)
            matched += 1

    precision = matched / len(act_keys) if act_keys else (1.0 if not exp_keys else 0.0)
    recall = matched / len(exp_keys) if exp_keys else 1.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
    return {
        "precision": round(precision, 4), "recall": round(recall, 4), "f1": round(f1, 4),
        "expected": len(exp_keys), "actual": len(act_keys), "matched": matched,
    }


def score_case(expected: dict, actual: dict) -> dict:
    """Score one OCR result against ground truth. Returns per-field booleans,
    item P/R/F1, and an overall field-accuracy in [0,1] (scalar fields only;
    items reported separately so a long item list doesn't drown the scalars)."""
    fields: dict[str, bool] = {}
    for f in TEXT_FIELDS:
        fields[f] = normalize_text(expected.get(f)) == normalize_text(actual.get(f))
    for f in MONEY_FIELDS:
        fields[f] = nums_match(expected.get(f), actual.get(f))

    items = score_items(expected.get("items"), actual.get("items"))
    hits = sum(1 for v in fields.values() if v)
    field_accuracy = round(hits / len(fields), 4) if fields else 0.0

    return {
        "fields": fields,
        "field_accuracy": field_accuracy,
        "items": items,
        # overall = mean of scalar field accuracy and item F1 (equal weight)
        "overall": round((field_accuracy + items["f1"]) / 2, 4),
    }


def aggregate(results: list[dict]) -> dict:
    """Aggregate scores across cases: mean field accuracy, mean item F1, mean
    overall, and per-field hit-rate."""
    if not results:
        return {"cases": 0, "mean_field_accuracy": 0.0, "mean_item_f1": 0.0, "mean_overall": 0.0, "per_field": {}}
    n = len(results)
    mean_fa = round(sum(r["field_accuracy"] for r in results) / n, 4)
    mean_f1 = round(sum(r["items"]["f1"] for r in results) / n, 4)
    mean_ov = round(sum(r["overall"] for r in results) / n, 4)
    per_field: dict[str, float] = {}
    for f in TEXT_FIELDS + MONEY_FIELDS:
        per_field[f] = round(sum(1 for r in results if r["fields"].get(f)) / n, 4)
    return {
        "cases": n,
        "mean_field_accuracy": mean_fa,
        "mean_item_f1": mean_f1,
        "mean_overall": mean_ov,
        "per_field": per_field,
    }


def _run_live(image_path: str, expected_path: str) -> None:
    """Run the REAL OCR pipeline on an image and score it. Needs OPENAI_API_KEY.
    Imports main lazily so the module stays import-safe without env/deps."""
    import mimetypes
    import sys

    with open(expected_path, encoding="utf-8") as fh:
        expected = json.load(fh)
    with open(image_path, "rb") as fh:
        image_bytes = fh.read()
    mime = mimetypes.guess_type(image_path)[0] or "image/jpeg"

    try:
        from main import _run_gpt_vision  # type: ignore
    except Exception as e:  # noqa: BLE001
        print(f"cannot import OCR pipeline (needs deps/env): {e}", file=sys.stderr)
        raise SystemExit(2)

    actual = _run_gpt_vision(image_bytes, mime, "")
    result = score_case(expected, actual)
    print(json.dumps({"image": image_path, "result": result}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    import sys

    if len(sys.argv) == 4 and sys.argv[1] == "--live":
        _run_live(sys.argv[2], sys.argv[3])
    else:
        print(
            "usage: python -m tests.ocr_golden.scorer --live <image_path> <expected.json>\n"
            "(offline scoring of synthetic fixtures is exercised by tests/test_ocr_golden.py)",
        )
