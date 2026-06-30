"""
ocr_schema.py — strict JSON Schema for invoice OCR (audit roadmap, experimental).

OpenAI's `response_format={"type":"json_schema", strict:true}` (openai>=1.40, we run
2.37) makes the model STRUCTURALLY guarantee the output shape: every field present,
correctly typed, enums constrained, no extra keys. This kills the hallucination class
behind AGENTS #24 (payment_type CHECK), #32 (list-shape), #34 (status enum) at the
source instead of patching it after parse.

EXPERIMENTAL: the production OCR path (`main._run_gpt_vision`) is unchanged. This
module + `llm.openai_chat_structured` + the 3-way `compare.py` runner exist so a
promotion to production can be decided from real-image accuracy numbers, then done
as a one-line swap routed through `normalize_structured`.

Pure module — no I/O, no network, no API key. Unit-tested in tests/test_ocr_schema.py.

OpenAI strict-mode rules honoured here:
  - every property MUST be listed in `required` (no truly-optional keys);
    "optional" is expressed as a nullable type, e.g. ["number","null"].
  - `additionalProperties: false` on every object.
"""

from __future__ import annotations

from typing import Any

# payment_type must match main.py's chk_vb_payment_type allowed set (+ null).
PAYMENT_TYPES = ["credit_card", "transfer", "cash", "cheque", "other"]
IMAGE_QUALITY_LEVELS = ["good", "fair", "poor"]

# Scalar fields the OCR returns a per-field confidence for (mirrors F6).
_CONFIDENCE_FIELDS = [
    "vendor_name", "invoice_no", "merchant_tax_id", "bill_date",
    "subtotal", "vat", "amount",
]


def _num_or_null() -> dict:
    return {"type": ["number", "null"]}


def _str_or_null() -> dict:
    return {"type": ["string", "null"]}


def invoice_json_schema() -> dict:
    """The strict JSON Schema for one OCR'd invoice/receipt. Matches the dict
    shape the existing downstream consumers expect (main.py: parsed.get(...),
    _insert_items, the payment_type normalizer, F6 _confidence_warnings)."""
    item_props = {
        "line_no": {"type": ["integer", "null"]},
        "sku": _str_or_null(),
        "product_name": _str_or_null(),
        "quantity": _num_or_null(),
        "unit": _str_or_null(),
        "unit_price": _num_or_null(),
        "amount": _num_or_null(),
    }
    item_schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": item_props,
        "required": list(item_props.keys()),
    }

    confidence_props = {f: {"type": ["number", "null"]} for f in _CONFIDENCE_FIELDS}
    field_confidence_schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": confidence_props,
        "required": list(confidence_props.keys()),
    }

    image_quality_schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "level": {"type": "string", "enum": IMAGE_QUALITY_LEVELS},
            "reason": _str_or_null(),
        },
        "required": ["level", "reason"],
    }

    discount_schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "line_items_discount_pct": _num_or_null(),
            "whole_bill_discount_amount": _num_or_null(),
            "whole_bill_discount_pct": _num_or_null(),
            "note": _str_or_null(),
        },
        "required": ["line_items_discount_pct", "whole_bill_discount_amount", "whole_bill_discount_pct", "note"],
    }

    props = {
        "vendor_name": _str_or_null(),
        "merchant_tax_id": _str_or_null(),
        "invoice_no": _str_or_null(),
        "bill_date": _str_or_null(),       # YYYY-MM-DD or null
        "due_date": _str_or_null(),
        "subtotal": _num_or_null(),
        "vat": _num_or_null(),
        "amount": _num_or_null(),
        "payment_type": {"type": ["string", "null"], "enum": PAYMENT_TYPES + [None]},
        "currency": {"type": "string"},
        "items": {"type": "array", "items": item_schema},
        "discount": discount_schema,
        "notes": _str_or_null(),
        "field_confidence": field_confidence_schema,
        "image_quality": image_quality_schema,
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": props,
        "required": list(props.keys()),
    }


# Scalar keys a downstream consumer reads off the parsed dict.
_CONSUMER_SCALARS = [
    "vendor_name", "merchant_tax_id", "invoice_no", "bill_date", "due_date",
    "subtotal", "vat", "amount", "payment_type", "currency", "notes",
]
_DISCOUNT_KEYS = ["line_items_discount_pct", "whole_bill_discount_amount", "whole_bill_discount_pct", "note"]
_ITEM_KEYS = ["line_no", "sku", "product_name", "quantity", "unit", "unit_price", "amount"]


def normalize_structured(parsed: Any) -> dict:
    """Map a structured-OCR result to the dict shape the existing consumers expect.

    Strict output already matches the contract, so this is mostly identity — but it
    is the seam a future production promotion plugs into, and it defends against a
    malformed/partial object (returns a dict with the known keys, never raises).
    `field_confidence` / `image_quality` / `discount` are passed through untouched."""
    if not isinstance(parsed, dict):
        return {"items": []}
    out: dict[str, Any] = {}
    for k in _CONSUMER_SCALARS:
        out[k] = parsed.get(k)
    items_in = parsed.get("items")
    items_out: list[dict] = []
    if isinstance(items_in, list):
        for it in items_in:
            if isinstance(it, dict):
                items_out.append({k: it.get(k) for k in _ITEM_KEYS})
    out["items"] = items_out
    # Discount object — pass through if present
    if "discount" in parsed and isinstance(parsed["discount"], dict):
        out["discount"] = {k: parsed["discount"].get(k) for k in _DISCOUNT_KEYS}
    # F6 blocks pass through verbatim if present (validator tolerates absence).
    if "field_confidence" in parsed:
        out["field_confidence"] = parsed["field_confidence"]
    if "image_quality" in parsed:
        out["image_quality"] = parsed["image_quality"]
    return out
