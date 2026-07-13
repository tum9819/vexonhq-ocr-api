from __future__ import annotations

import os
import sys
from datetime import date
from uuid import UUID

import pytest
from fastapi import HTTPException

os.environ.setdefault("DATABASE_URL", "postgresql://u:p@localhost:5432/d")

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import bill_payment_routes as routes


BILL_ID = "11111111-1111-1111-1111-111111111111"
BANK_ID = "22222222-2222-2222-2222-222222222222"
OTHER_BILL_ID = "33333333-3333-3333-3333-333333333333"


class FakeCursor:
    def __init__(self, fetches):
        self.fetches = list(fetches)
        self.description = None
        self.queries = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, sql, params=None):
        self.queries.append((sql, params))
        compact = " ".join(sql.split()).upper()
        if compact.startswith("UPDATE PUBLIC.VENDOR_BILLS"):
            self.description = [
                ("id",),
                ("vendor_name",),
                ("invoice_no",),
                ("bill_date",),
                ("amount",),
                ("payment_status",),
                ("paid_date",),
            ]
        elif compact.startswith("SELECT ID, DIRECTION, MATCHED_INVOICE_ID"):
            self.description = [("id",), ("direction",), ("matched_invoice_id",)]
        elif compact.startswith("SELECT ID, TXN_DATE"):
            self.description = [
                ("id",),
                ("txn_date",),
                ("description",),
                ("debit",),
                ("amount",),
            ]
        elif compact.startswith("UPDATE PUBLIC.BANK_STATEMENT_ENTRIES"):
            self.description = None

    def fetchone(self):
        if not self.fetches:
            return None
        return self.fetches.pop(0)

    def fetchall(self):
        if not self.fetches:
            return []
        return self.fetches.pop(0)


class FakeConn:
    def __init__(self, cursor):
        self.cursor_obj = cursor
        self.commits = 0
        self.rollbacks = 0
        self.closed = False

    def cursor(self):
        return self.cursor_obj

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closed = True


def _bill_row(status="paid"):
    return (
        UUID(BILL_ID),
        "Vendor A",
        "INV-1",
        date(2026, 7, 1),
        1234.0,
        status,
        date(2026, 7, 13) if status != "unpaid" else None,
    )


def test_patch_paid_links_selected_unmatched_expense_bank_row(monkeypatch):
    cursor = FakeCursor([
        _bill_row("paid"),
        (UUID(BANK_ID), "expense", None),
    ])
    conn = FakeConn(cursor)
    monkeypatch.setattr(routes, "get_db_conn", lambda: conn)

    result = routes.update_bill_payment(
        BILL_ID,
        routes.BillPaymentPatch(payment_status="paid", bank_statement_entry_id=BANK_ID),
        {"_role": "admin"},
    )

    assert result["id"] == BILL_ID
    assert result["payment_status"] == "paid"
    assert result["bank_statement_entry_id"] == BANK_ID
    assert conn.commits == 1
    link_updates = [
        (sql, params) for sql, params in cursor.queries
        if "SET matched_invoice_id = %s" in sql
    ]
    assert len(link_updates) == 1
    assert link_updates[0][1] == (BILL_ID, BANK_ID)


def test_patch_paid_clears_previous_bank_links_before_linking_new_row(monkeypatch):
    cursor = FakeCursor([
        _bill_row("paid"),
        (UUID(BANK_ID), "expense", None),
    ])
    conn = FakeConn(cursor)
    monkeypatch.setattr(routes, "get_db_conn", lambda: conn)

    routes.update_bill_payment(
        BILL_ID,
        routes.BillPaymentPatch(payment_status="paid", bank_statement_entry_id=BANK_ID),
        {"_role": "admin"},
    )

    clear_queries = [
        (sql, params) for sql, params in cursor.queries
        if "SET matched_invoice_id = NULL" in sql
    ]
    assert len(clear_queries) == 1
    assert clear_queries[0][1] == (BILL_ID, BANK_ID)


def test_patch_paid_rejects_bank_row_already_matched_to_other_bill(monkeypatch):
    cursor = FakeCursor([
        _bill_row("paid"),
        (UUID(BANK_ID), "expense", UUID(OTHER_BILL_ID)),
    ])
    conn = FakeConn(cursor)
    monkeypatch.setattr(routes, "get_db_conn", lambda: conn)

    with pytest.raises(HTTPException) as exc:
        routes.update_bill_payment(
            BILL_ID,
            routes.BillPaymentPatch(payment_status="paid", bank_statement_entry_id=BANK_ID),
            {"_role": "admin"},
        )

    assert exc.value.status_code == 409
    assert conn.commits == 0
    assert conn.rollbacks == 1


def test_patch_unpaid_clears_bank_link_for_that_bill(monkeypatch):
    cursor = FakeCursor([_bill_row("unpaid")])
    conn = FakeConn(cursor)
    monkeypatch.setattr(routes, "get_db_conn", lambda: conn)

    result = routes.update_bill_payment(
        BILL_ID,
        routes.BillPaymentPatch(payment_status="unpaid"),
        {"_role": "admin"},
    )

    assert result["payment_status"] == "unpaid"
    clear_queries = [
        (sql, params) for sql, params in cursor.queries
        if "SET matched_invoice_id = NULL" in sql
    ]
    assert len(clear_queries) == 1
    assert clear_queries[0][1] == (BILL_ID,)
    assert conn.commits == 1


def test_bank_candidates_return_only_unmatched_matching_expense_rows(monkeypatch):
    cursor = FakeCursor([
        (date(2026, 7, 1), 1234.0),
        [
            (UUID(BANK_ID), date(2026, 7, 5), "โอน Supplier", 1234.0, 1234.0),
        ],
    ])
    conn = FakeConn(cursor)
    monkeypatch.setattr(routes, "get_db_conn", lambda: conn)

    result = routes.bank_candidates_for_bill(BILL_ID, {"_role": "admin"})

    assert result["bill_id"] == BILL_ID
    assert len(result["candidates"]) == 1
    assert result["candidates"][0]["id"] == BANK_ID
    sql = " ".join(cursor.queries[-1][0].split())
    assert "direction = 'expense'" in sql
    assert "matched_invoice_id IS NULL" in sql
    assert "amount = %s" in sql
    assert "txn_date >= %s" in sql
    assert "txn_date <= %s" in sql
