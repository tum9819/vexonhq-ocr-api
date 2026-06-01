"""
test_ocr_confidence.py — offline checks for the OCR field-confidence + image-quality
warnings (audit F6). No API key / no DB: tests the pure _confidence_warnings helper.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from main import _confidence_warnings


def _codes(warnings):
    return [w["code"] for w in warnings]


def test_low_field_confidence_flagged():
    parsed = {
        "vendor_name": "ร้านเดโม", "amount": 1070,
        "field_confidence": {"vendor_name": 0.95, "amount": 0.42},
    }
    w = _confidence_warnings(parsed)
    codes = _codes(w)
    assert codes.count("LOW_CONFIDENCE") == 1
    low = [x for x in w if x["code"] == "LOW_CONFIDENCE"][0]
    assert low["field"] == "amount"
    assert "42%" in low["message"]


def test_high_confidence_no_warning():
    parsed = {
        "vendor_name": "ร้านเดโม", "amount": 1070,
        "field_confidence": {"vendor_name": 0.97, "amount": 0.99},
        "image_quality": {"level": "good", "reason": ""},
    }
    assert _confidence_warnings(parsed) == []


def test_poor_image_quality_flagged():
    parsed = {"amount": 1070, "image_quality": {"level": "poor", "reason": "เบลอ"}}
    w = _confidence_warnings(parsed)
    assert "LOW_IMAGE_QUALITY" in _codes(w)
    assert "เบลอ" in [x for x in w if x["code"] == "LOW_IMAGE_QUALITY"][0]["message"]


def test_confidence_only_for_present_fields():
    # invoice_no is absent → its low confidence must NOT be flagged
    parsed = {
        "amount": 1070,
        "field_confidence": {"invoice_no": 0.1, "amount": 0.95},
    }
    assert _confidence_warnings(parsed) == []


def test_garbage_confidence_does_not_crash():
    for bad in [
        {"amount": 1, "field_confidence": "high"},
        {"amount": 1, "field_confidence": {"amount": "low"}},
        {"amount": 1, "field_confidence": {"amount": None}},
        {"amount": 1, "image_quality": "poor"},
        {"amount": 1, "image_quality": {"level": 123}},
        {"amount": 1},
        {},
    ]:
        assert isinstance(_confidence_warnings(bad), list)  # no exception


def test_missing_keys_no_warning():
    assert _confidence_warnings({"vendor_name": "x", "amount": 5}) == []
