"""
test_ocr_schema.py — offline checks for the strict OCR JSON Schema + normalizer
(audit roadmap, experimental structured-output OCR). No API key / no network.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ocr_schema import invoice_json_schema, normalize_structured, PAYMENT_TYPES


def _walk_objects(node, path="root"):
    """Yield (path, object-schema) for every object node."""
    if isinstance(node, dict):
        if node.get("type") == "object":
            yield path, node
            for k, v in node.get("properties", {}).items():
                yield from _walk_objects(v, f"{path}.{k}")
        if node.get("type") == "array":
            yield from _walk_objects(node.get("items", {}), f"{path}[]")


def test_strict_mode_invariants():
    """OpenAI strict mode: every object has additionalProperties:false and
    required == the full set of its properties."""
    schema = invoice_json_schema()
    objs = list(_walk_objects(schema))
    assert objs, "schema has no object nodes"
    for path, obj in objs:
        assert obj.get("additionalProperties") is False, f"{path}: additionalProperties not false"
        props = set(obj.get("properties", {}))
        req = set(obj.get("required", []))
        assert props == req, f"{path}: required != properties (diff {props ^ req})"


def test_top_level_fields_cover_consumer_contract():
    props = invoice_json_schema()["properties"]
    # every field main.py reads off the parsed dict must be in the schema
    for f in ["vendor_name", "merchant_tax_id", "invoice_no", "bill_date", "due_date",
              "subtotal", "vat", "amount", "payment_type", "currency", "items", "notes",
              "field_confidence", "image_quality"]:
        assert f in props, f"missing top-level field: {f}"


def test_payment_type_enum_locked_with_null():
    pt = invoice_json_schema()["properties"]["payment_type"]
    assert set(pt["enum"]) == set(PAYMENT_TYPES + [None])
    # the CHECK-constraint values are exactly these (AGENTS #24)
    assert "transfer" in pt["enum"] and None in pt["enum"]


def test_item_subfields_match_insert_items():
    item = invoice_json_schema()["properties"]["items"]["items"]
    for k in ["line_no", "sku", "product_name", "quantity", "unit", "unit_price", "amount"]:
        assert k in item["properties"], f"item missing {k}"


def test_normalize_round_trips_consumer_shape():
    sample = {
        "vendor_name": "ร้านเดโม", "merchant_tax_id": "0105500000000",
        "invoice_no": "INV-1", "bill_date": "2026-04-01", "due_date": None,
        "subtotal": 100.0, "vat": 7.0, "amount": 107.0,
        "payment_type": "transfer", "currency": "THB", "notes": None,
        "items": [{"line_no": 1, "sku": "A1", "product_name": "หมู", "quantity": 2,
                   "unit": "กก.", "unit_price": 50, "amount": 100}],
        "field_confidence": {"amount": 0.95}, "image_quality": {"level": "good", "reason": ""},
    }
    out = normalize_structured(sample)
    assert out["amount"] == 107.0 and out["payment_type"] == "transfer"
    assert out["items"][0]["product_name"] == "หมู"
    assert set(out["items"][0]) == {"line_no", "sku", "product_name", "quantity", "unit", "unit_price", "amount"}
    # F6 blocks pass through for _confidence_warnings
    assert out["field_confidence"] == {"amount": 0.95}
    assert out["image_quality"]["level"] == "good"


def test_normalize_tolerates_garbage():
    for bad in [None, "nope", 123, [], {"items": "notalist"}, {}]:
        out = normalize_structured(bad)
        assert isinstance(out, dict)
        assert isinstance(out["items"], list)   # always a list, never raises


def test_normalize_skips_nondict_items():
    out = normalize_structured({"items": [{"product_name": "x"}, "junk", 5, None]})
    assert len(out["items"]) == 1 and out["items"][0]["product_name"] == "x"
