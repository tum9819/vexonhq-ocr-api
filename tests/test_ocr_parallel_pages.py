"""Offline unit tests for the multi-page OCR parallelization (2026-06-08).

Guards the invariants that let us parallelize the slow GPT-Vision stage WITHOUT
re-introducing the duplicate-bill bug:
  - OCR pages run concurrently but results stay in PAGE ORDER.
  - _persist_invoice_page (the DB save/merge) runs SEQUENTIALLY, never overlapped.
  - In-flight OCR is capped (<= 3) so we don't overload OpenAI / the 4GB box.

These mock the OCR + persist stages, so no network / OpenAI / DB is touched.

Run: pytest tests/test_ocr_parallel_pages.py -v
"""
import os
import threading
import time

os.environ.setdefault("DATABASE_URL", "postgresql://u:p@localhost:5432/d")
os.environ.setdefault("JWT_SECRET", "testsecret")
os.environ.setdefault("OPENAI_API_KEY", "x")
os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "x")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "x")
os.environ.setdefault("SUPABASE_ANON_KEY", "x")

import main  # noqa: E402


def _install_mocks(monkeypatch, *, ocr_sleep):
    """Patch _pdf_to_images + the two pipeline stages with order/concurrency
    instrumentation. Returns the shared `state` dict the assertions read."""
    state = {
        "persist_order": [],     # tags in the order persist saw them
        "persist_overlap": False,  # set True if two persists ever overlap
        "_persist_active": False,
        "ocr_peak": 0,
        "_ocr_active": 0,
        "retry_flags": [],
    }
    lock = threading.Lock()

    def fake_ocr_page(image_bytes, file_name, mime_type, *, allow_corrective_retry=True):
        tag = image_bytes.decode()           # b"p1" -> "p1"
        idx = int(tag[1:])
        with lock:
            state["retry_flags"].append((tag, allow_corrective_retry))
            state["_ocr_active"] += 1
            state["ocr_peak"] = max(state["ocr_peak"], state["_ocr_active"])
        # Earlier pages sleep LONGER → if order were by completion it would
        # invert. map() must still hand them back in page order.
        time.sleep(ocr_sleep * (10 - idx))
        with lock:
            state["_ocr_active"] -= 1
        return {
            "image_bytes": image_bytes,
            "file_name": file_name,
            "mime_type": mime_type,
            "ocr_text": "",
            "parsed": {"tag": tag},
        }

    def fake_persist(image_bytes, file_name, mime_type, ocr_text, parsed, file_sha256=None):
        # Assert this never runs concurrently with another persist.
        if state["_persist_active"]:
            state["persist_overlap"] = True
        state["_persist_active"] = True
        state["persist_order"].append(parsed["tag"])
        time.sleep(0.01)
        state["_persist_active"] = False
        return {
            "success": True,
            "invoice_id": parsed["tag"],
            "warnings": [{"page": parsed["tag"]}],
        }

    # Skip the pre-OCR file-hash lookup (it would otherwise hit Supabase).
    monkeypatch.setattr(main, "_find_uploaded_file", lambda h, expected_pages=1: None)
    monkeypatch.setattr(main, "_ocr_page", fake_ocr_page)
    monkeypatch.setattr(main, "_persist_invoice_page", fake_persist)
    return state


def test_multipage_preserves_page_order_and_serial_persist(monkeypatch):
    state = _install_mocks(monkeypatch, ocr_sleep=0.02)
    monkeypatch.setattr(main, "_pdf_to_images", lambda c: [b"p1", b"p2", b"p3"])

    out = main._process_upload(b"%PDF-fake", "Makro.pdf", "application/pdf")

    # OCR ran concurrently but persist saw pages strictly in order 1,2,3
    assert state["persist_order"] == ["p1", "p2", "p3"]
    # persist never overlapped (sequential merge invariant)
    assert state["persist_overlap"] is False
    # last page's result is returned, warnings combined from all pages
    assert out["total_pages_processed"] == 3
    assert out["warnings"] == [{"page": "p1"}, {"page": "p2"}, {"page": "p3"}]


def test_ocr_concurrency_capped_at_three(monkeypatch):
    monkeypatch.delenv("OCR_PAGE_RETRY_MULTIPAGE", raising=False)
    state = _install_mocks(monkeypatch, ocr_sleep=0.03)
    monkeypatch.setattr(
        main, "_pdf_to_images", lambda c: [f"p{i}".encode() for i in range(1, 6)]
    )

    main._process_upload(b"%PDF-fake", "Big.pdf", "application/pdf")

    # 5 pages but never more than 3 vision calls in flight at once
    assert state["ocr_peak"] <= 3
    assert state["persist_order"] == ["p1", "p2", "p3", "p4", "p5"]
    assert state["persist_overlap"] is False
    assert dict(state["retry_flags"]) == {
        "p1": False, "p2": False, "p3": False, "p4": False, "p5": False,
    }


def test_multipage_retry_can_be_restored_by_env(monkeypatch):
    state = _install_mocks(monkeypatch, ocr_sleep=0.0)
    monkeypatch.setenv("OCR_PAGE_RETRY_MULTIPAGE", "1")
    monkeypatch.setattr(main, "_pdf_to_images", lambda c: [b"p1", b"p2"])

    main._process_upload(b"%PDF-fake", "Rollback.pdf", "application/pdf")

    assert dict(state["retry_flags"]) == {"p1": True, "p2": True}


def test_single_image_uses_one_page_path(monkeypatch):
    state = _install_mocks(monkeypatch, ocr_sleep=0.0)

    out = main._process_upload(b"p1", "bill.jpg", "image/jpeg")

    assert out["total_pages_processed"] == 1
    assert state["persist_order"] == ["p1"]
    assert state["retry_flags"] == [("p1", True)]


def test_ocr_page_can_suppress_corrective_retry_without_changing_first_parse(monkeypatch):
    calls = []
    first = {"amount": 100.0, "items": [{"amount": 10.0}], "discount": None}
    monkeypatch.setattr(main, "_run_tesseract", lambda _image: "hint")
    monkeypatch.setattr(
        main,
        "_run_gpt_vision",
        lambda *args, **kwargs: calls.append(kwargs.get("corrective_note")) or first,
    )

    result = main._ocr_page(
        b"image", "bill-p2.png", "image/png", allow_corrective_retry=False
    )

    assert calls == [None]
    assert result["parsed"] is first
