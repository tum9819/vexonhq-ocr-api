"""Cross-file merge guard (plan docs/superpowers/plans/2026-10-31-fix-cross-upload-merge.md).

_save_invoice used to append items + an attachment to ANY existing bill found
by dedup_key / invoice_no, even when the new upload was a different file (a
re-scan or a new photo of the same paper) — duplicating the line items. These
tests drive the real _save_invoice against a small in-memory Supabase fake.
"""
import copy
import uuid
from unittest.mock import patch

import pytest
from fastapi import HTTPException

import main


class _Resp:
    def __init__(self, data, count=None):
        self.data = data
        self.count = count


class _Query:
    def __init__(self, db, table):
        self.db, self.table = db, table
        self.op, self.payload, self.filters = "select", None, []
        self.count_mode, self._limit = None, None

    def select(self, *_cols, count=None):
        self.op, self.count_mode = "select", count
        return self

    def insert(self, payload):
        self.op, self.payload = "insert", payload
        return self

    def update(self, payload):
        self.op, self.payload = "update", payload
        return self

    def eq(self, col, val):
        self.filters.append(lambda r: r.get(col) == val)
        return self

    def in_(self, col, vals):
        self.filters.append(lambda r: r.get(col) in vals)
        return self

    def limit(self, n):
        self._limit = n
        return self

    def order(self, *_a, **_k):
        return self

    def _match(self):
        return [r for r in self.db.rows[self.table] if all(f(r) for f in self.filters)]

    def execute(self):
        self.db.calls.append((self.table, self.op))
        if self.table in self.db.fail_on and self.op in self.db.fail_on[self.table]:
            raise self.db.fail_on[self.table][self.op]
        if self.op == "insert":
            rows = self.payload if isinstance(self.payload, list) else [self.payload]
            for r in rows:
                r = dict(r)
                r.setdefault("id", str(uuid.uuid4()))
                if self.table == "vendor_bills":
                    if r.get("vendor_name") and r.get("invoice_no"):
                        key = f"{r['vendor_name'].lower()}|{r['invoice_no'].lower()}"
                        if any(b.get("dedup_key") == key for b in self.db.rows["vendor_bills"]):
                            raise Exception('duplicate key value violates unique constraint "uq_vendor_bills_dedup"')
                        r["dedup_key"] = key
                self.db.rows[self.table].append(r)
            return _Resp(rows)
        if self.op == "update":
            hit = self._match()
            for r in hit:
                r.update(self.payload)
            return _Resp(hit)
        hit = self._match()
        if self._limit is not None:
            hit = hit[: self._limit]
        return _Resp(copy.deepcopy(hit), count=len(hit) if self.count_mode else None)


class FakeSB:
    def __init__(self):
        self.rows = {"vendor_bills": [], "invoice_items": [], "attachments": []}
        self.calls = []
        self.fail_on = {}

    def table(self, name):
        self.rows.setdefault(name, [])
        return _Query(self, name)

    def writes(self):
        return [c for c in self.calls if c[1] in ("insert", "update")]


VENDOR = "บริษัท ซีพี แอ็กซ์ตร้า จำกัด (มหาชน)"


def _bill(db, *, bill_id="bill-1", status="confirmed", sha="sha-old", items=1, invoice_no="068901182818"):
    db.rows["vendor_bills"].append({
        "id": bill_id, "vendor_name": VENDOR, "invoice_no": invoice_no,
        "dedup_key": f"{VENDOR.lower()}|{invoice_no.lower()}",
        "bill_date": "2026-10-30", "amount": 1000, "review_status": status, "batch_id": "b-1",
    })
    if sha is not None:
        db.rows["attachments"].append({
            "id": "att-1", "parent_type": "vendor_bill", "parent_id": bill_id,
            "file_name": "old-p1.png", "page_no": 1, "file_sha256": sha,
        })
    for i in range(items):
        db.rows["invoice_items"].append({
            "id": f"it-{i}", "vendor_bill_id": bill_id, "product_name": f"item{i}",
            "quantity": 1, "unit_price": 1000, "amount": 1000, "source_page": 1,
        })


def _parsed(invoice_no="068901182818", vendor=VENDOR, amount=1000):
    return {
        "vendor_name": vendor, "invoice_no": invoice_no, "bill_date": "2026-10-30",
        "amount": amount, "items": [{"product_name": "item0", "quantity": 1, "unit_price": 1000, "amount": 1000}],
    }


@pytest.fixture
def db():
    fake = FakeSB()
    with patch("main.get_supabase", return_value=fake), \
         patch("main._auto_classify_invoice_items", return_value={}):
        yield fake


def _save(parsed, sha, file_name="new-p1.png"):
    return main._save_invoice(
        parsed=parsed, ocr_text="", file_url="http://x/f.png",
        file_name=file_name, mime_type="image/png", file_sha256=sha,
    )


@pytest.mark.parametrize("status", ["confirmed", "pending", "needs_attention"])
def test_different_file_matching_existing_bill_is_rejected_without_writes(db, status):
    _bill(db, status=status)
    before = copy.deepcopy(db.rows)
    with pytest.raises(main.BillAlreadyExists) as exc:
        _save(_parsed(), sha="sha-new")
    assert exc.value.status_code == 409
    assert isinstance(exc.value.detail, str)
    assert "068901182818" in exc.value.detail
    assert db.writes() == []
    assert db.rows == before


def test_same_file_next_page_still_merges(db):
    _bill(db, status="pending", sha="sha-pdf")
    invoice_id, _batch, page_no, merged = _save(_parsed(), sha="sha-pdf", file_name="scan-p2.png")
    assert (invoice_id, page_no, merged) == ("bill-1", 2, True)
    assert len(db.rows["invoice_items"]) == 2
    assert len(db.rows["attachments"]) == 2


def test_invoice_no_fallback_path_is_guarded(db):
    # vendor OCR drifted → dedup_key differs; the invoice_no fallback finds the bill
    _bill(db, status="confirmed")
    with pytest.raises(main.BillAlreadyExists):
        _save(_parsed(vendor="CP AXTRA"), sha="sha-new")
    assert db.writes() == []


def test_create_conflict_path_is_guarded(db):
    # rejected bills are skipped by the lookup → INSERT hits the unique index →
    # the refetch must not append into the rejected bill.
    _bill(db, status="rejected")
    with pytest.raises(main.BillAlreadyExists) as exc:
        _save(_parsed(), sha="sha-new")
    assert "rejected" in exc.value.detail
    assert [w for w in db.writes() if w != ("vendor_bills", "insert")] == []
    assert len(db.rows["invoice_items"]) == 1


def test_empty_shell_bill_still_merges_on_retry(db):
    # previous attempt created the header, then items failed before any attachment
    _bill(db, status="pending", sha=None, items=0)
    invoice_id, _b, _p, merged = _save(_parsed(), sha="sha-retry")
    assert invoice_id == "bill-1" and merged is True
    assert len(db.rows["invoice_items"]) == 1


def test_bill_with_items_but_no_attachment_is_not_an_empty_shell(db):
    # e.g. a LINE bill whose storage upload failed
    _bill(db, status="pending", sha=None, items=1)
    with pytest.raises(main.BillAlreadyExists):
        _save(_parsed(), sha="sha-new")


def test_guard_lookup_failure_fails_closed(db):
    _bill(db, status="pending")
    real_table = db.table

    def flaky(name):
        q = real_table(name)
        if name == "attachments":
            db.fail_on["attachments"] = {"select": Exception("pooler saturated")}
        return q
    db.table = flaky
    with pytest.raises(HTTPException) as exc:
        _save(_parsed(), sha="sha-new")
    assert exc.value.status_code == 503
    assert db.writes() == []


def test_without_sha_keeps_legacy_merge(db):
    _bill(db, status="confirmed")
    invoice_id, _b, _p, merged = _save(_parsed(), sha=None)
    assert invoice_id == "bill-1" and merged is True


def test_items_insert_error_on_create_is_not_treated_as_conflict(db):
    db.fail_on["invoice_items"] = {"insert": Exception("duplicate key value 23505 on invoice_items")}
    with pytest.raises(Exception) as exc:
        _save(_parsed(invoice_no="NEW-1"), sha="sha-new")
    assert "invoice_items" in str(exc.value)
    assert not isinstance(exc.value, main.BillAlreadyExists)
    assert len(db.rows["vendor_bills"]) == 1  # header written once, no refetch/merge


def test_new_bill_still_created(db):
    invoice_id, _b, page_no, merged = _save(_parsed(invoice_no="NEW-2"), sha="sha-new")
    assert merged is False and page_no == 1
    assert len(db.rows["vendor_bills"]) == 1


# ── _persist_invoice_page: 409 reaches the client and cleans only this upload ──

def test_persist_page_reraises_409_and_removes_only_its_storage_file():
    removed = []

    class _Bucket:
        def remove(self, paths):
            removed.extend(paths)

    class _Storage:
        def from_(self, _b):
            return _Bucket()

    class _SB:
        storage = _Storage()

    conflict = main.BillAlreadyExists({"id": "bill-1", "invoice_no": "X", "review_status": "confirmed"})
    with patch("main._upload_to_storage", return_value=("http://x/p3.png", "invoices/2026-10/p3.png")), \
         patch("main._save_invoice", side_effect=conflict), \
         patch("main.get_supabase", return_value=_SB()):
        with pytest.raises(main.BillAlreadyExists) as exc:
            main._persist_invoice_page(
                image_bytes=b"x", file_name="scan-p3.png", mime_type="image/png",
                ocr_text="", parsed=_parsed(), file_sha256="sha-new",
            )
    assert exc.value.status_code == 409
    assert removed == ["invoices/2026-10/p3.png"]


# ── Lifecycle through the real _process_upload → _persist_invoice_page →
#    _save_invoice (review 2026-10-31 E#1–2). Only OCR, PDF rendering, storage
#    and revalidation are faked; every DB write goes through the fake above. ──

class _Storage:
    def __init__(self):
        self.objects, self.removed = set(), []

    def upload(self, _bytes, name, _mime):
        path = f"invoices/2026-10/{uuid.uuid4()}-{name}"
        self.objects.add(path)
        return f"http://x/{path}", path

    def from_(self, _bucket):
        store = self

        class _B:
            def remove(self, paths):
                store.removed.extend(paths)
                store.objects.difference_update(paths)
        return _B()


@pytest.fixture
def pipe():
    fake, store = FakeSB(), _Storage()
    fake.storage = store
    with patch("main.get_supabase", return_value=fake), \
         patch("main._auto_classify_invoice_items", return_value={}), \
         patch("main._revalidate_bill", return_value=[]), \
         patch("main._upload_to_storage", side_effect=store.upload):
        yield fake, store


def _run_pdf(pages, content=b"%PDF fake"):
    """pages: list of parsed dicts, one per PDF page."""
    def ocr(img, name, mime, **_k):
        return {"image_bytes": img, "file_name": name, "mime_type": mime,
                "ocr_text": "", "parsed": copy.deepcopy(pages[int(img)])}
    with patch("main._pdf_to_images", return_value=[str(i).encode() for i in range(len(pages))]), \
         patch("main._ocr_page", side_effect=ocr):
        return main._process_upload(content, "scan.pdf", "application/pdf")


def _page(no, amount=None, item="x"):
    p = {"vendor_name": VENDOR, "invoice_no": no, "bill_date": "2026-10-30",
         "items": [{"product_name": item, "quantity": 1, "unit_price": 100, "amount": 100}]}
    if amount is not None:
        p["amount"] = amount
    return p


def _referenced(fake):
    return {a["file_url"].split("http://x/", 1)[1] for a in fake.rows["attachments"] if a.get("file_url")}


def test_pdf_three_pages_one_bill(pipe):
    fake, store = pipe
    _run_pdf([_page("M-1", item="a"), _page("M-1", item="b"), _page("M-1", amount=300, item="c")])
    assert len(fake.rows["vendor_bills"]) == 1
    assert len(fake.rows["invoice_items"]) == 3
    assert len(fake.rows["attachments"]) == 3
    assert store.removed == []


def test_pdf_with_several_new_bills_creates_each(pipe):
    fake, _ = pipe
    _run_pdf([_page("A-1", 100), _page("B-1", 100), _page("C-1", 100)])
    assert sorted(b["invoice_no"] for b in fake.rows["vendor_bills"]) == ["A-1", "B-1", "C-1"]
    assert len(fake.rows["invoice_items"]) == 3


def test_retry_after_header_written_but_items_failed(pipe):
    fake, _ = pipe
    fake.fail_on["invoice_items"] = {"insert": Exception("connection reset")}
    with pytest.raises(HTTPException) as exc:
        _run_pdf([_page("R-1", 100)])
    assert exc.value.status_code == 500
    assert len(fake.rows["vendor_bills"]) == 1 and fake.rows["invoice_items"] == []
    fake.fail_on.clear()
    _run_pdf([_page("R-1", 100)])  # same file again
    assert len(fake.rows["vendor_bills"]) == 1
    assert len(fake.rows["invoice_items"]) == 1


def test_page_three_hits_existing_bill_409_keeps_pages_one_two(pipe):
    fake, store = pipe
    _bill(fake, bill_id="old", status="confirmed", invoice_no="OLD-9")
    before_old_items = len(fake.rows["invoice_items"])
    with pytest.raises(main.BillAlreadyExists) as exc:
        _run_pdf([_page("N-1", item="a"), _page("N-1", 200, item="b"), _page("OLD-9", 100)])
    assert exc.value.status_code == 409 and isinstance(exc.value.detail, str)
    # existing bill untouched
    assert len([i for i in fake.rows["invoice_items"] if i["vendor_bill_id"] == "old"]) == before_old_items
    assert len([a for a in fake.rows["attachments"] if a["parent_id"] == "old"]) == 1
    # only page 3's file was removed, and nothing references it
    assert len(store.removed) == 1
    assert store.removed[0] not in _referenced(fake)
    # pages 1–2 saved and still referenced
    new_bill = [b for b in fake.rows["vendor_bills"] if b["invoice_no"] == "N-1"][0]
    new_atts = [a for a in fake.rows["attachments"] if a["parent_id"] == new_bill["id"]]
    assert len(new_atts) == 2
    assert all(a["file_url"].split("http://x/", 1)[1] in store.objects for a in new_atts)


def test_rescan_of_existing_bill_writes_nothing(pipe):
    fake, store = pipe
    _bill(fake, status="pending", invoice_no="P-1")
    before = copy.deepcopy(fake.rows)
    with pytest.raises(main.BillAlreadyExists):
        _run_pdf([_page("P-1", 1000)], content=b"%PDF rescan")
    assert fake.rows == before
    assert len(store.removed) == 1 and store.objects == set()


def test_upload_endpoint_returns_409_with_string_detail(pipe):
    # The route handler itself (auth middleware is out of scope here).
    import asyncio
    import io
    from starlette.datastructures import Headers, UploadFile

    fake, _ = pipe
    _bill(fake, status="confirmed", invoice_no="E-1")
    upload = UploadFile(file=io.BytesIO(b"%PDF other scan"), filename="scan.pdf",
                        headers=Headers({"content-type": "application/pdf"}))

    def ocr(img, name, mime, **_k):
        return {"image_bytes": img, "file_name": name, "mime_type": mime,
                "ocr_text": "", "parsed": _page("E-1", 1000)}

    with patch("main._pdf_to_images", return_value=[b"0"]), patch("main._ocr_page", side_effect=ocr):
        with pytest.raises(HTTPException) as exc:
            asyncio.run(main.invoice_upload(upload))
    assert exc.value.status_code == 409
    assert isinstance(exc.value.detail, str) and "E-1" in exc.value.detail
