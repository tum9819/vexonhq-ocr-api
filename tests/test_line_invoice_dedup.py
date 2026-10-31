"""
LINE invoice images — file-level idempotency (same rule as the web upload).

Before 2026-10-04 the LINE invoice path OCR'd and saved every image it got,
with no file hash, so re-sending the same photo created a second bill (cost
audit 2026-09-10 §3.6). It now hashes the image bytes, reuses
main._find_uploaded_file, skips OCR + save when that exact file is already on
a bill, and stores the hash on the attachment for future checks.

Offline: OCR, storage and DB helpers are monkeypatched.
Run: pytest tests/test_line_invoice_dedup.py -v
"""
from __future__ import annotations

import hashlib
import os

os.environ.setdefault("DATABASE_URL", "postgresql://u:p@localhost:5432/d")
os.environ.setdefault("OPENAI_API_KEY", "x")

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import main  # import main before modules that import auth_routes (AGENTS #75)
import line_bot_routes as lbr

IMAGE = b"\xff\xd8\xff fake jpeg bytes"
SHA = hashlib.sha256(IMAGE).hexdigest()


def test_already_uploaded_image_skips_ocr_and_save(monkeypatch):
    seen = {}

    def fake_find(sha, expected_pages=1):
        seen["sha"] = sha
        return {"invoice_id": "11111111-2222-3333-4444-555555555555", "already_uploaded": True}

    def boom(*a, **k):
        raise AssertionError("must not OCR or save a file that is already on a bill")

    monkeypatch.setattr(main, "_find_uploaded_file", fake_find)
    monkeypatch.setattr(lbr, "_ocr_invoice_image", boom)
    monkeypatch.setattr(lbr, "_save_invoice_from_line", boom)

    reply = lbr._process_invoice_from_line(IMAGE)

    assert seen["sha"] == SHA
    assert "11111111" in reply
    assert "เคยส่ง" in reply


def test_new_image_is_ocrd_and_saved_with_its_hash(monkeypatch):
    calls = {}
    parsed = {"vendor_name": "Makro", "invoice_no": "016276201221", "amount": 2082}

    monkeypatch.setattr(main, "_find_uploaded_file", lambda sha, expected_pages=1: None)
    monkeypatch.setattr(lbr, "_ocr_invoice_image", lambda b: parsed)

    def fake_save(p, b, file_sha256=None):
        calls["save"] = (p, b, file_sha256)
        return "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"

    monkeypatch.setattr(lbr, "_save_invoice_from_line", fake_save)

    reply = lbr._process_invoice_from_line(IMAGE)

    assert calls["save"] == (parsed, IMAGE, SHA)
    assert reply == lbr._format_ocr_reply(parsed, "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")


def test_save_invoice_from_line_passes_the_hash_to_save_invoice(monkeypatch):
    seen = {}
    monkeypatch.setattr(main, "_upload_to_storage", lambda b, name, mime: ("https://x/uploads/a.jpg", "a.jpg"))

    def fake_save_invoice(**kwargs):
        seen.update(kwargs)
        return ("inv-1", "batch-1", 1, False)

    monkeypatch.setattr(main, "_save_invoice", fake_save_invoice)

    assert lbr._save_invoice_from_line({"amount": 1}, IMAGE, file_sha256=SHA) == "inv-1"
    assert seen["file_sha256"] == SHA
    assert seen["file_url"] == "https://x/uploads/a.jpg"
    assert seen["mime_type"] == "image/jpeg"


EXISTING = {"id": "bbbbbbbb-1111-2222-3333-444444444444", "vendor_name": "Makro",
            "invoice_no": "016276201221", "bill_date": "2026-10-30", "amount": 2082,
            "review_status": "confirmed"}


def test_new_photo_of_existing_bill_replies_already_in_system(monkeypatch):
    # Real _process_invoice_from_line → _save_invoice_from_line chain; only the
    # DB save, storage and OCR are faked.
    live = sys.modules["main"]
    removed = []
    monkeypatch.setattr(live, "_find_uploaded_file", lambda sha, expected_pages=1: None)
    monkeypatch.setattr(lbr, "_ocr_invoice_image", lambda b: {"invoice_no": "016276201221"})
    monkeypatch.setattr(live, "_upload_to_storage", lambda b, name, mime: ("https://x/uploads/d.jpg", "invoices/d.jpg"))
    monkeypatch.setattr(live, "_remove_storage_quietly", lambda path: removed.append(path))

    def conflict(**kw):
        raise live.BillAlreadyExists(EXISTING)

    monkeypatch.setattr(live, "_save_invoice", conflict)

    reply = lbr._process_invoice_from_line(IMAGE)

    assert removed == ["invoices/d.jpg"]
    assert "มีในระบบแล้ว" in reply
    assert "016276201221" in reply
    assert "bbbbbbbb" in reply
    assert "ลองใหม่" not in reply


import pytest


@pytest.mark.parametrize("status", [409, 503])
def test_save_invoice_from_line_removes_its_upload_when_guard_blocks(monkeypatch, status):
    # Other tests reload `main`; _save_invoice_from_line imports from whatever
    # sys.modules["main"] is at call time, so patch and raise from that module.
    live = sys.modules["main"]
    removed = []
    monkeypatch.setattr(live, "_upload_to_storage", lambda b, name, mime: ("https://x/uploads/c.jpg", "invoices/c.jpg"))
    monkeypatch.setattr(live, "_remove_storage_quietly", lambda path: removed.append(path))

    def blocked(**kw):
        if status == 409:
            raise live.BillAlreadyExists(EXISTING)
        raise live.HTTPException(503, "guard down")

    monkeypatch.setattr(live, "_save_invoice", blocked)

    with pytest.raises(live.HTTPException) as exc:
        lbr._save_invoice_from_line({"amount": 1}, IMAGE, file_sha256=SHA)
    assert exc.value.status_code == status
    assert removed == ["invoices/c.jpg"]


def test_save_invoice_from_line_without_hash_keeps_old_behaviour(monkeypatch):
    seen = {}
    monkeypatch.setattr(main, "_upload_to_storage", lambda b, name, mime: ("https://x/uploads/b.jpg", "b.jpg"))
    monkeypatch.setattr(main, "_save_invoice", lambda **kw: (seen.update(kw) or ("inv-2", "batch-2", 1, False)))

    assert lbr._save_invoice_from_line({"amount": 1}, IMAGE) == "inv-2"
    assert seen["file_sha256"] is None
