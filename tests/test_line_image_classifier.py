"""Offline payload tests for the LINE invoice/slip classifier.

The OpenAI boundary is replaced; no network or paid call is made.
"""
import os
from types import SimpleNamespace

os.environ.setdefault("DATABASE_URL", "postgresql://u:p@localhost:5432/d")
os.environ.setdefault("JWT_SECRET", "testsecret")
os.environ.setdefault("OPENAI_API_KEY", "x")
os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "x")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "x")
os.environ.setdefault("SUPABASE_ANON_KEY", "x")

import line_bot_routes  # noqa: E402
import llm  # noqa: E402


def _response(kind="invoice"):
    message = SimpleNamespace(content=f'{{"type":"{kind}","confidence":0.99}}')
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def test_line_classifier_defaults_to_low_image_detail(monkeypatch):
    captured = {}
    monkeypatch.delenv("LINE_IMAGE_CLASSIFY_DETAIL", raising=False)
    monkeypatch.setattr(llm, "openai_chat", lambda *args, **kwargs: captured.update(kwargs) or _response())

    assert line_bot_routes._classify_image_type(b"jpeg") == "invoice"

    image = captured["messages"][0]["content"][1]["image_url"]
    assert image["detail"] == "low"


def test_line_classifier_detail_can_roll_back_to_high(monkeypatch):
    captured = {}
    monkeypatch.setenv("LINE_IMAGE_CLASSIFY_DETAIL", "high")
    monkeypatch.setattr(llm, "openai_chat", lambda *args, **kwargs: captured.update(kwargs) or _response())

    line_bot_routes._classify_image_type(b"jpeg")

    image = captured["messages"][0]["content"][1]["image_url"]
    assert image["detail"] == "high"
