"""Offline unit tests for file-level SHA-256 upload idempotency (2026-06-09).

Covers the fix for the duplicate-bill bug: a re-uploaded identical file is
detected pre-OCR by a deterministic byte hash (not the old non-deterministic
OCR-content comparison), so OCR is skipped and no duplicate items/attachments
are created.

All Supabase / OCR calls are mocked — no network, no DB, no OpenAI.

Run: pytest tests/test_ocr_file_hash.py -v
"""
import hashlib
import os

os.environ.setdefault("DATABASE_URL", "postgresql://u:p@localhost:5432/d")
os.environ.setdefault("JWT_SECRET", "testsecret")
os.environ.setdefault("OPENAI_API_KEY", "x")
os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "x")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "x")
os.environ.setdefault("SUPABASE_ANON_KEY", "x")

import main  # noqa: E402


# ── A minimal fake Supabase client (chainable query API) ──────────────────────
class _FakeResult:
    def __init__(self, data, count=None):
        self.data = data
        self.count = count


class _FakeQuery:
    def __init__(self, table, store):
        self.table = table
        self.store = store
        self.filters = {}
        self._count = None

    def select(self, *a, count=None):
        self._count = count
        return self

    def eq(self, k, v):
        self.filters[k] = v
        return self

    def limit(self, n):
        return self

    def execute(self):
        return self.store(self.table, self.filters, self._count)


class _FakeSupabase:
    def __init__(self, store):
        self._store = store

    def table(self, name):
        return _FakeQuery(name, self._store)


_KNOWN_HASH = "knownhash0000000000000000000000000000000000000000000000000000abcd"


def _store(table, filters, count):
    """Pretends one file (hash=_KNOWN_HASH) is already on bill 'bill-1'."""
    if table == "attachments":
        if count == "exact":  # page-count query
            return _FakeResult([{"id": "a1"}, {"id": "a2"}], count=2)
        if filters.get("file_sha256") == _KNOWN_HASH:
            return _FakeResult([{"parent_id": "bill-1"}])
        return _FakeResult([])  # any other hash = not seen before
    if table == "vendor_bills":
        return _FakeResult([{
            "id": "bill-1", "vendor_name": "SINGHA BEER CO., LTD.",
            "invoice_no": "SS-1", "bill_date": "2026-06-02", "amount": 20639.98,
            "batch_id": "batch-1", "ocr_json": {"vendor_name": "SINGHA BEER CO., LTD."},
            "attachment_url": None,
        }])
    return _FakeResult([])


def test_sha256_is_deterministic():
    b = b"the-same-bytes"
    assert hashlib.sha256(b).hexdigest() == hashlib.sha256(b).hexdigest()


def test_find_uploaded_file_hit_returns_existing_bill(monkeypatch):
    monkeypatch.setattr(main, "get_supabase", lambda: _FakeSupabase(_store))
    out = main._find_uploaded_file(_KNOWN_HASH)
    assert out is not None
    assert out["already_uploaded"] is True
    assert out["invoice_id"] == "bill-1"
    assert out["total_pages_processed"] == 0
    assert out["parsed"]["vendor_name"] == "SINGHA BEER CO., LTD."


def test_find_uploaded_file_miss_returns_none_even_if_amount_matches(monkeypatch):
    # A DIFFERENT file (unknown hash) must NOT be skipped, regardless of any
    # vendor/amount coincidence — the lookup keys ONLY on the byte hash.
    monkeypatch.setattr(main, "get_supabase", lambda: _FakeSupabase(_store))
    assert main._find_uploaded_file("a-totally-different-hash") is None


def test_duplicate_upload_skips_ocr(monkeypatch):
    # _find_uploaded_file finds the file → _process_upload must short-circuit
    # BEFORE any OCR/Vision work.
    # For PDFs, _pdf_to_images (cheap local conversion) runs first so the page
    # count is known; the idempotency check happens after that. _ocr_page (the
    # expensive GPT-4o call) must NOT be invoked.
    sentinel = {"success": True, "already_uploaded": True, "invoice_id": "bill-1",
                "total_pages_processed": 0}
    seen = {}

    def fake_find(h, expected_pages=1):
        seen["hash"] = h
        seen["expected_pages"] = expected_pages
        return sentinel

    def boom(*a, **k):
        raise AssertionError("OCR must not run for an already-uploaded file")

    monkeypatch.setattr(main, "_find_uploaded_file", fake_find)
    monkeypatch.setattr(main, "_pdf_to_images", lambda c: [b"p1", b"p2"])
    monkeypatch.setattr(main, "_ocr_page", boom)

    contents = b"DUPLICATE-FILE-BYTES"
    out = main._process_upload(contents, "Makro.pdf", "application/pdf")

    assert out is sentinel
    assert seen["hash"] == hashlib.sha256(contents).hexdigest()
    # expected_pages must equal the page count returned by _pdf_to_images
    assert seen["expected_pages"] == 2


def test_new_multipage_writes_same_hash_to_every_page(monkeypatch):
    monkeypatch.setattr(main, "_find_uploaded_file", lambda h, expected_pages=1: None)
    monkeypatch.setattr(main, "_pdf_to_images", lambda c: [b"p1", b"p2", b"p3"])
    monkeypatch.setattr(main, "_ocr_page",
                        lambda img, fn, mt: {"image_bytes": img, "file_name": fn,
                                             "mime_type": mt, "ocr_text": "", "parsed": {}})
    hashes = []

    def fake_persist(image_bytes, file_name, mime_type, ocr_text, parsed, file_sha256=None):
        hashes.append(file_sha256)
        return {"success": True, "warnings": []}

    monkeypatch.setattr(main, "_persist_invoice_page", fake_persist)

    contents = b"PDF-BYTES"
    out = main._process_upload(contents, "Makro.pdf", "application/pdf")

    expected = hashlib.sha256(contents).hexdigest()
    assert out["total_pages_processed"] == 3
    assert hashes == [expected, expected, expected]  # one hash, every page


def test_single_page_passes_hash_once(monkeypatch):
    monkeypatch.setattr(main, "_find_uploaded_file", lambda h, expected_pages=1: None)
    monkeypatch.setattr(main, "_ocr_page",
                        lambda img, fn, mt: {"image_bytes": img, "file_name": fn,
                                             "mime_type": mt, "ocr_text": "", "parsed": {}})
    got = {}

    def fake_persist(image_bytes, file_name, mime_type, ocr_text, parsed, file_sha256=None):
        got["hash"] = file_sha256
        return {"success": True, "warnings": []}

    monkeypatch.setattr(main, "_persist_invoice_page", fake_persist)

    contents = b"JPEG-BYTES"
    out = main._process_upload(contents, "bill.jpg", "image/jpeg")

    assert out["total_pages_processed"] == 1
    assert got["hash"] == hashlib.sha256(contents).hexdigest()
