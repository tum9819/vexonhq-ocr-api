import os
from datetime import date, datetime
from unittest.mock import MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

os.environ.setdefault("DATABASE_URL", "postgresql://u:p@localhost:5432/d")

import phase12_bank_statement_routes as bank_routes


RAW_ROWS = [
    {
        "txn_date": date(2026, 6, 1),
        "description": "จาก lineman",
        "debit": 0.0,
        "credit": 1000.0,
        "balance": 5000.0,
    },
    {
        "txn_date": date(2026, 6, 2),
        "description": "โอนไป Supplier",
        "debit": 300.0,
        "credit": 0.0,
        "balance": 4700.0,
    },
]


class FakeCursor:
    def __init__(self, *, duplicate_row=None, insert_rowcounts=None):
        self.duplicate_row = duplicate_row
        self.insert_rowcounts = list(insert_rowcounts or [])
        self.rowcount = -1
        self.queries = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, sql, params=None):
        self.queries.append((sql, params))
        upper = " ".join(sql.upper().split())
        if upper.startswith("INSERT INTO PUBLIC.BANK_STATEMENT_ENTRIES"):
            self.rowcount = self.insert_rowcounts.pop(0)
        else:
            self.rowcount = 1

    def fetchone(self):
        return self.duplicate_row

    def fetchall(self):
        return []


class FakeConn:
    def __init__(self, cursor):
        self._cursor = cursor
        self.commit = MagicMock()
        self.rollback = MagicMock()
        self.close = MagicMock()

    def cursor(self):
        return self._cursor


def _patch_parser(monkeypatch, rows=None):
    monkeypatch.setattr(bank_routes, "_extract_transactions", MagicMock(return_value=rows or RAW_ROWS))
    monkeypatch.setattr(
        bank_routes,
        "_statement_checksum",
        MagicMock(return_value={"ok": True, "available": True}),
    )


def test_first_upload_reports_inserted_and_zero_skipped(monkeypatch):
    cursor = FakeCursor(insert_rowcounts=[1, 1])
    conn = FakeConn(cursor)
    monkeypatch.setattr(bank_routes, "get_db_conn", lambda: conn)
    _patch_parser(monkeypatch)

    result = bank_routes._process_statement_upload(
        b"first pdf bytes",
        "thawi_watthana",
        filename="kbank-june.pdf",
    )

    assert result["success"] is True
    assert result["total_rows"] == 2
    assert result["auto_classified"] == 2
    assert result["needs_review"] == 0
    assert result["inserted"] == 2
    assert result["skipped_duplicates"] == 0
    assert result["status"] == "success"
    assert result["file_hash"]
    conn.commit.assert_called_once()


def test_same_file_hash_returns_409_already_imported(monkeypatch):
    duplicate = (
        "import-1",
        "kbank-june.pdf",
        2,
        date(2026, 6, 1),
        date(2026, 6, 2),
        datetime(2026, 7, 3, 9, 0, 0),
    )
    cursor = FakeCursor(duplicate_row=duplicate)
    conn = FakeConn(cursor)
    monkeypatch.setattr(bank_routes, "get_db_conn", lambda: conn)
    extractor = MagicMock(return_value=RAW_ROWS)
    monkeypatch.setattr(bank_routes, "_extract_transactions", extractor)

    app = FastAPI()
    app.include_router(bank_routes.router)
    app.dependency_overrides[bank_routes._require_admin_role] = lambda: {
        "sub": "tum",
        "_role": "admin",
    }
    client = TestClient(app, raise_server_exceptions=False)

    response = client.post(
        "/bank-statement/upload?branch_code=thawi_watthana",
        files={"file": ("kbank-june.pdf", b"same pdf bytes", "application/pdf")},
    )

    assert response.status_code == 409
    body = response.json()
    assert body["detail"] == "already_imported"
    assert body["status"] == "already_imported"
    assert body["import_id"] == "import-1"
    assert body["rows_imported"] == 2
    extractor.assert_not_called()
    conn.commit.assert_not_called()


def test_different_file_with_overlapping_rows_reports_skipped_duplicates(monkeypatch):
    cursor = FakeCursor(insert_rowcounts=[1, 0])
    conn = FakeConn(cursor)
    monkeypatch.setattr(bank_routes, "get_db_conn", lambda: conn)
    _patch_parser(monkeypatch)

    result = bank_routes._process_statement_upload(
        b"different pdf bytes",
        "thawi_watthana",
        filename="kbank-june-reexport.pdf",
    )

    assert result["total_rows"] == 2
    assert result["inserted"] == 1
    assert result["skipped_duplicates"] == 1
    assert "ซ้ำ" in result["message"]
    conn.commit.assert_called_once()


def test_response_keeps_existing_fields(monkeypatch):
    cursor = FakeCursor(insert_rowcounts=[1, 1])
    conn = FakeConn(cursor)
    monkeypatch.setattr(bank_routes, "get_db_conn", lambda: conn)
    _patch_parser(monkeypatch)

    result = bank_routes._process_statement_upload(
        b"compat pdf bytes",
        "thawi_watthana",
        filename="kbank-compat.pdf",
    )

    for key in (
        "success",
        "batch_id",
        "total_rows",
        "auto_classified",
        "needs_review",
        "checksum_ok",
        "checksum",
        "message",
    ):
        assert key in result


def test_upload_requires_admin_role():
    app = FastAPI()
    app.include_router(bank_routes.router)
    client = TestClient(app, raise_server_exceptions=False)

    response = client.post(
        "/bank-statement/upload?branch_code=thawi_watthana",
        files={"file": ("kbank-june.pdf", b"pdf bytes", "application/pdf")},
    )

    # No Authorization header at all -> _require_admin_role rejects before
    # any parsing or DB work happens.
    assert response.status_code == 401


def test_uploaded_by_flows_into_pos_imports_insert(monkeypatch):
    cursor = FakeCursor(insert_rowcounts=[1, 1])
    conn = FakeConn(cursor)
    monkeypatch.setattr(bank_routes, "get_db_conn", lambda: conn)
    _patch_parser(monkeypatch)

    bank_routes._process_statement_upload(
        b"audit trail pdf bytes",
        "thawi_watthana",
        filename="kbank-june.pdf",
        uploaded_by="tum@marastation.com",
    )

    pos_import_inserts = [
        params
        for sql, params in cursor.queries
        if " ".join(sql.upper().split()).startswith("INSERT INTO PUBLIC.POS_IMPORTS")
    ]
    assert len(pos_import_inserts) == 1
    assert "tum@marastation.com" in pos_import_inserts[0]
