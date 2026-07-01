"""F-OCR: the production OCR path (_run_gpt_vision) must use the strict
structured-output JSON Schema so the model STRUCTURALLY guarantees field shape
(kills the omit/wrong-type/bad-enum class). Wiring test only — no network
(openai_chat / openai_chat_structured are monkeypatched).
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("OPENAI_API_KEY", "test")

import main


class _Msg:
    def __init__(self, content):
        self.content = content


class _Choice:
    def __init__(self, content):
        self.message = _Msg(content)


class _Resp:
    def __init__(self, content):
        self.choices = [_Choice(content)]


def _image_attached(messages) -> bool:
    return any(
        isinstance(m.get("content"), list)
        and any(p.get("type") == "image_url" for p in m["content"])
        for m in messages
    )


def test_run_gpt_vision_routes_through_strict_schema(monkeypatch):
    captured = {}

    def fake_structured(task, *, messages, schema, schema_name="result", model=None, **kw):
        captured["task"] = task
        captured["schema"] = schema
        captured["has_image"] = _image_attached(messages)
        return _Resp('{"vendor_name": "ร้านทดสอบ", "amount": 107.0, "items": []}')

    def _no_json_object(*a, **k):
        raise AssertionError("structured mode must NOT use the json_object path")

    monkeypatch.setattr(main, "openai_chat_structured", fake_structured, raising=False)
    monkeypatch.setattr(main, "openai_chat", _no_json_object, raising=False)
    monkeypatch.setattr(main, "_OCR_STRUCTURED", True, raising=False)

    out = main._run_gpt_vision(b"fakeimagebytes", "image/png", "tesseract hint")

    # returns the model's fields, normalized to the consumer dict shape
    assert out["vendor_name"] == "ร้านทดสอบ"
    assert out["amount"] == 107.0
    assert out["items"] == []
    # routed through the strict invoice schema, with the image attached
    assert captured["task"] == "vision_ocr"
    assert captured["has_image"] is True
    assert "payment_type" in captured["schema"]["properties"]
    assert captured["schema"]["additionalProperties"] is False


def test_run_gpt_vision_falls_back_to_json_object_when_disabled(monkeypatch):
    captured = {}

    def fake_chat(task, *, messages, model=None, **kw):
        captured["response_format"] = kw.get("response_format")
        captured["has_image"] = _image_attached(messages)
        return _Resp('{"vendor_name": "fallback", "items": []}')

    monkeypatch.setattr(main, "openai_chat", fake_chat, raising=False)
    monkeypatch.setattr(main, "_OCR_STRUCTURED", False, raising=False)

    out = main._run_gpt_vision(b"img", "image/png", "")

    assert out["vendor_name"] == "fallback"
    assert captured["response_format"] == {"type": "json_object"}
    assert captured["has_image"] is True


def test_run_gpt_vision_regex_fills_missing_discount_when_totals_present(monkeypatch):
    def fake_structured(task, *, messages, schema, schema_name="result", model=None, **kw):
        return _Resp(
            '{"vendor_name": "Makro", "subtotal": 1076.77, "vat": 27.48, '
            '"amount": 1104.25, "items": [], '
            '"discount": {"line_items_discount_pct": null, '
            '"whole_bill_discount_amount": null, '
            '"whole_bill_discount_pct": null, "note": null}}'
        )

    monkeypatch.setattr(main, "openai_chat_structured", fake_structured, raising=False)
    monkeypatch.setattr(main, "_OCR_STRUCTURED", True, raising=False)

    out = main._run_gpt_vision(
        b"fakeimagebytes",
        "image/png",
        "รวม | | 1,076.77 | 27.48 | 1,104.25\nDISCOUNT 21.00\nNET AMOUNT 1,104.25",
    )

    assert out["discount"]["whole_bill_discount_amount"] == 21.00
