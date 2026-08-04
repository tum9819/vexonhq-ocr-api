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
        # The combined-transfer list runs after the exact-amount list.
        [],
    ])
    conn = FakeConn(cursor)
    monkeypatch.setattr(routes, "get_db_conn", lambda: conn)

    result = routes.bank_candidates_for_bill(BILL_ID, {"_role": "admin"})

    assert result["bill_id"] == BILL_ID
    assert len(result["candidates"]) == 1
    assert result["candidates"][0]["id"] == BANK_ID
    # Index 1 is the exact-amount query; index 2 is the additive combined query.
    sql = " ".join(cursor.queries[1][0].split())
    assert "direction = 'expense'" in sql
    assert "matched_invoice_id IS NULL" in sql
    assert "amount = %s" in sql
    assert "txn_date >= %s" in sql
    assert "txn_date <= %s" in sql


# ─────────────────────────────────────────────────────────
# PUT /bills/payment/{bill_id}/bank-link
#
# Writes public.bank_entry_bill_links only — never public.vendor_bills.
# One transfer may settle several bills; one bill has at most one transfer.
# ─────────────────────────────────────────────────────────


class LinkCursor(FakeCursor):
    def execute(self, sql, params=None):
        self.queries.append((sql, params))
        compact = " ".join(sql.split()).upper()
        if compact.startswith("SELECT ID, PAYMENT_STATUS"):
            self.description = [("id",), ("payment_status",)]
        elif compact.startswith("SELECT BANK_ENTRY_ID"):
            self.description = [("bank_entry_id",)]
        elif compact.startswith("SELECT ID, DIRECTION"):
            self.description = [("id",), ("direction",)]
        else:
            self.description = None


def _sqls(cursor):
    return [" ".join(s.split()) for s, _ in cursor.queries]


def _vendor_bill_writes(cursor):
    return [s for s in _sqls(cursor) if s.upper().startswith("UPDATE PUBLIC.VENDOR_BILLS")]


def _mirror_targets(cursor):
    return [p[0] for s, p in cursor.queries if "SET matched_invoice_id = (" in s]


ADMIN = {"_role": "admin", "sub": "admin-uid"}
OTHER_ENTRY_ID = "44444444-4444-4444-4444-444444444444"


def test_bank_link_writes_link_row_and_never_touches_vendor_bills(monkeypatch):
    cursor = LinkCursor([(UUID(BILL_ID), "paid"), None, (UUID(BANK_ID), "expense")])
    conn = FakeConn(cursor)
    monkeypatch.setattr(routes, "get_db_conn", lambda: conn)

    result = routes.link_bill_bank_entry(
        BILL_ID, routes.BillBankLink(bank_statement_entry_id=BANK_ID), ADMIN)

    assert result == {"bill_id": BILL_ID, "payment_status": "paid",
                      "bank_statement_entry_id": BANK_ID}
    assert conn.commits == 1
    assert _vendor_bill_writes(cursor) == []
    inserts = [p for s, p in cursor.queries
               if "INSERT INTO public.bank_entry_bill_links" in s]
    assert inserts == [(BANK_ID, BILL_ID, "admin-uid")]


def test_second_bill_may_join_a_transfer_that_already_carries_bills(monkeypatch):
    """The old 409 is gone on purpose: a transfer can settle several invoices."""
    cursor = LinkCursor([(UUID(BILL_ID), "paid"), None, (UUID(BANK_ID), "expense")])
    conn = FakeConn(cursor)
    monkeypatch.setattr(routes, "get_db_conn", lambda: conn)

    routes.link_bill_bank_entry(
        BILL_ID, routes.BillBankLink(bank_statement_entry_id=BANK_ID), ADMIN)

    assert conn.commits == 1
    assert not any("already linked to another bill" in s for s in _sqls(cursor))


def test_moving_a_bill_refreshes_the_old_and_the_new_transfer_mirror(monkeypatch):
    cursor = LinkCursor([
        (UUID(BILL_ID), "paid"),
        (OTHER_ENTRY_ID,),
        (UUID(BANK_ID), "expense"),
    ])
    conn = FakeConn(cursor)
    monkeypatch.setattr(routes, "get_db_conn", lambda: conn)

    routes.link_bill_bank_entry(
        BILL_ID, routes.BillBankLink(bank_statement_entry_id=BANK_ID), ADMIN)

    assert [p for s, p in cursor.queries
            if "DELETE FROM public.bank_entry_bill_links" in s] == [(BILL_ID,)]
    assert set(_mirror_targets(cursor)) == {OTHER_ENTRY_ID, BANK_ID}


def test_relinking_to_the_same_transfer_is_idempotent(monkeypatch):
    cursor = LinkCursor([
        (UUID(BILL_ID), "paid"),
        (BANK_ID,),
        (UUID(BANK_ID), "expense"),
    ])
    conn = FakeConn(cursor)
    monkeypatch.setattr(routes, "get_db_conn", lambda: conn)

    result = routes.link_bill_bank_entry(
        BILL_ID, routes.BillBankLink(bank_statement_entry_id=BANK_ID), ADMIN)

    assert result["bank_statement_entry_id"] == BANK_ID
    assert _mirror_targets(cursor) == [BANK_ID]
    assert conn.commits == 1


def test_unlink_deletes_the_row_and_refreshes_only_that_transfer(monkeypatch):
    cursor = LinkCursor([(UUID(BILL_ID), "paid"), (BANK_ID,)])
    conn = FakeConn(cursor)
    monkeypatch.setattr(routes, "get_db_conn", lambda: conn)

    result = routes.link_bill_bank_entry(
        BILL_ID, routes.BillBankLink(bank_statement_entry_id=None), ADMIN)

    assert result["bank_statement_entry_id"] is None
    assert [p for s, p in cursor.queries
            if "DELETE FROM public.bank_entry_bill_links" in s] == [(BILL_ID,)]
    assert _mirror_targets(cursor) == [BANK_ID]
    assert _vendor_bill_writes(cursor) == []
    assert conn.commits == 1


def test_unlink_on_an_already_unlinked_bill_touches_no_mirror(monkeypatch):
    cursor = LinkCursor([(UUID(BILL_ID), "paid"), None])
    conn = FakeConn(cursor)
    monkeypatch.setattr(routes, "get_db_conn", lambda: conn)

    routes.link_bill_bank_entry(
        BILL_ID, routes.BillBankLink(bank_statement_entry_id=None), ADMIN)

    assert _mirror_targets(cursor) == []
    assert conn.commits == 1


def test_bank_link_rejects_income_row(monkeypatch):
    cursor = LinkCursor([(UUID(BILL_ID), "paid"), None, (UUID(BANK_ID), "income")])
    conn = FakeConn(cursor)
    monkeypatch.setattr(routes, "get_db_conn", lambda: conn)

    with pytest.raises(HTTPException) as exc:
        routes.link_bill_bank_entry(
            BILL_ID, routes.BillBankLink(bank_statement_entry_id=BANK_ID), ADMIN)

    assert exc.value.status_code == 400
    assert conn.commits == 0
    assert conn.rollbacks == 1
    # Nothing may be written before the direction check passes.
    assert not any("INSERT INTO public.bank_entry_bill_links" in s for s in _sqls(cursor))
    assert not any("DELETE FROM public.bank_entry_bill_links" in s for s in _sqls(cursor))


def test_bank_link_rejects_unpaid_bill(monkeypatch):
    cursor = LinkCursor([(UUID(BILL_ID), "unpaid")])
    conn = FakeConn(cursor)
    monkeypatch.setattr(routes, "get_db_conn", lambda: conn)

    with pytest.raises(HTTPException) as exc:
        routes.link_bill_bank_entry(
            BILL_ID, routes.BillBankLink(bank_statement_entry_id=BANK_ID), ADMIN)

    assert exc.value.status_code == 400
    assert conn.commits == 0


def test_bank_link_404_when_bill_missing_or_not_confirmed(monkeypatch):
    cursor = LinkCursor([None])
    conn = FakeConn(cursor)
    monkeypatch.setattr(routes, "get_db_conn", lambda: conn)

    with pytest.raises(HTTPException) as exc:
        routes.link_bill_bank_entry(
            BILL_ID, routes.BillBankLink(bank_statement_entry_id=BANK_ID), ADMIN)

    assert exc.value.status_code == 404
    assert conn.commits == 0


def test_bank_link_404_when_bank_row_missing(monkeypatch):
    cursor = LinkCursor([(UUID(BILL_ID), "paid"), None, None])
    conn = FakeConn(cursor)
    monkeypatch.setattr(routes, "get_db_conn", lambda: conn)

    with pytest.raises(HTTPException) as exc:
        routes.link_bill_bank_entry(
            BILL_ID, routes.BillBankLink(bank_statement_entry_id=BANK_ID), ADMIN)

    assert exc.value.status_code == 404
    assert conn.commits == 0


def test_bank_link_rejects_malformed_ids(monkeypatch):
    monkeypatch.setattr(routes, "get_db_conn",
                        lambda: pytest.fail("must not open a connection"))

    for bill, bank in (("not-a-uuid", BANK_ID), (BILL_ID, "nope")):
        with pytest.raises(HTTPException) as exc:
            routes.link_bill_bank_entry(
                bill, routes.BillBankLink(bank_statement_entry_id=bank), ADMIN)
        assert exc.value.status_code == 400


def test_bank_link_locks_both_rows_before_writing(monkeypatch):
    cursor = LinkCursor([(UUID(BILL_ID), "paid"), None, (UUID(BANK_ID), "expense")])
    conn = FakeConn(cursor)
    monkeypatch.setattr(routes, "get_db_conn", lambda: conn)

    routes.link_bill_bank_entry(
        BILL_ID, routes.BillBankLink(bank_statement_entry_id=BANK_ID), ADMIN)

    locks = [s for s in _sqls(cursor) if "FOR UPDATE" in s]
    assert len(locks) == 2
    # vendor_bills is locked first, exactly like update_bill_payment, so the two
    # routes can never deadlock against each other.
    assert "public.vendor_bills" in locks[0]
    assert "public.bank_statement_entries" in locks[1]


def test_moving_a_bill_locks_both_transfers_in_id_order(monkeypatch):
    """Two admins swapping bills between the same pair of transfers must not
    deadlock, so every call locks the transfers it touches in sorted id order.
    Here the previous transfer sorts AFTER the target one."""
    later_entry = "99999999-9999-9999-9999-999999999999"
    cursor = LinkCursor([
        (UUID(BILL_ID), "paid"),
        (later_entry,),
        (UUID(BANK_ID), "expense"),   # BANK_ID starts with 2, so it locks first
        (UUID(later_entry), "expense"),
    ])
    conn = FakeConn(cursor)
    monkeypatch.setattr(routes, "get_db_conn", lambda: conn)

    routes.link_bill_bank_entry(
        BILL_ID, routes.BillBankLink(bank_statement_entry_id=BANK_ID), ADMIN)

    bank_locks = [p[0] for s, p in cursor.queries
                  if "FOR UPDATE" in s and "public.bank_statement_entries" in s]
    assert bank_locks == sorted([BANK_ID, later_entry])
    # Mirrors are refreshed in that same stable order.
    assert _mirror_targets(cursor) == sorted([BANK_ID, later_entry])


def test_mirror_query_picks_the_earliest_link():
    cursor = LinkCursor([])
    routes._refresh_bank_entry_mirror(cursor, BANK_ID)
    sql = _sqls(cursor)[0]
    assert "ORDER BY l.created_at, l.id" in sql
    assert "LIMIT 1" in sql
    assert cursor.queries[0][1] == (BANK_ID, BANK_ID)


# ─────────────────────────────────────────────────────────
# GET /bills/payment/{bill_id}/bank-candidates — combined transfers
# ─────────────────────────────────────────────────────────


class CandCursor(FakeCursor):
    def execute(self, sql, params=None):
        self.queries.append((sql, params))
        c = " ".join(sql.split()).upper()
        if c.startswith("SELECT BILL_DATE, AMOUNT"):
            self.description = [("bill_date",), ("amount",)]
        elif "REMAINING" in c:
            self.description = [("id",), ("txn_date",), ("description",), ("debit",),
                                ("amount",), ("remaining",), ("allocated",),
                                ("linked_bill_count",)]
        else:
            self.description = [("id",), ("txn_date",), ("description",),
                                ("debit",), ("amount",)]


def test_combined_candidates_expose_remaining_and_exclude_this_bill(monkeypatch):
    """Real production shape: the 2025-11-04 transfer of 27,039.97 already holds
    the 5,309.98 invoice, so 21,729.99 is still free for the second one."""
    cursor = CandCursor([
        (date(2025, 10, 24), 21729.99),
        [],
        [(UUID(BANK_ID), date(2025, 11, 4), "transfer to SINGHA", 27039.97,
          27039.97, 21729.99, 5309.98, 1)],
    ])
    conn = FakeConn(cursor)
    monkeypatch.setattr(routes, "get_db_conn", lambda: conn)

    result = routes.bank_candidates_for_bill(BILL_ID, ADMIN)

    assert result["candidates"] == []
    assert len(result["combined_candidates"]) == 1
    combined = result["combined_candidates"][0]
    assert combined["remaining"] == 21729.99
    assert combined["allocated"] == 5309.98
    assert combined["linked_bill_count"] == 1

    sql = _sqls(cursor)[-1]
    assert "l.vendor_bill_id <> %s" in sql
    assert "b.direction = 'expense'" in sql


def test_combined_candidates_also_offer_a_transfer_with_no_links_yet(monkeypatch):
    """Regression: the FIRST bill of a combined group must be attachable.

    The 21,729.99 invoice is too small to match the 27,039.97 transfer exactly
    and that transfer has no links yet, so if the combined list required
    bill_count > 0 neither list would show it and no group could ever start.
    """
    cursor = CandCursor([
        (date(2025, 10, 24), 21729.99),
        [],
        [(UUID(BANK_ID), date(2025, 11, 4), "transfer to SINGHA", 27039.97,
          27039.97, 27039.97, 0, 0)],
    ])
    conn = FakeConn(cursor)
    monkeypatch.setattr(routes, "get_db_conn", lambda: conn)

    result = routes.bank_candidates_for_bill(BILL_ID, ADMIN)

    assert [c["id"] for c in result["combined_candidates"]] == [BANK_ID]
    assert result["combined_candidates"][0]["linked_bill_count"] == 0

    sql = _sqls(cursor)[-1]
    assert "COALESCE(alloc.bill_count, 0) > 0" not in sql
    # An untouched transfer of the same amount belongs to the exact list only.
    assert "abs(b.amount - %s) <= 1" in sql
    assert "b.matched_invoice_id IS NULL" in sql


def test_combined_candidates_are_additive_and_leave_the_exact_list_alone(monkeypatch):
    cursor = CandCursor([
        (date(2026, 7, 1), 1234.0),
        [(UUID(BANK_ID), date(2026, 7, 5), "transfer to Supplier", 1234.0, 1234.0)],
        [],
    ])
    conn = FakeConn(cursor)
    monkeypatch.setattr(routes, "get_db_conn", lambda: conn)

    result = routes.bank_candidates_for_bill(BILL_ID, ADMIN)

    assert [c["id"] for c in result["candidates"]] == [BANK_ID]
    assert result["combined_candidates"] == []
    exact_sql = _sqls(cursor)[1]
    assert "matched_invoice_id IS NULL" in exact_sql
    assert "amount = %s" in exact_sql


# ─────────────────────────────────────────────────────────
# GET /bills/payment — the per-row "is it linked" flag
# ─────────────────────────────────────────────────────────


def test_list_bills_reads_link_table_not_the_legacy_mirror(monkeypatch):
    """Regression: a transfer that settles several bills mirrors only its FIRST
    bill into bank_statement_entries.matched_invoice_id. Reading that mirror here
    made every later bill of a combined transfer render as still unlinked, which
    is exactly what production showed for SS 681107140 after group C was linked.
    """

    class ListCursor(FakeCursor):
        def execute(self, sql, params=None):
            self.queries.append((sql, params))
            self.description = [
                ("id",), ("vendor_name",), ("invoice_no",), ("bill_date",),
                ("due_date",), ("amount",), ("category_code",), ("category_name",),
                ("payment_status",), ("paid_date",), ("review_status",), ("notes",),
                ("bank_statement_entry_id",),
            ]

    cursor = ListCursor([[
        (UUID(BILL_ID), "SINGHA BEER CO., LTD.", "SS 681107063", date(2025, 11, 21),
         None, 13759.99, "beverage_raw", "เครื่องดื่ม", "paid", date(2025, 11, 27),
         "confirmed", None, BANK_ID),
        # Second bill on the SAME transfer — must also report as linked.
        (UUID(OTHER_BILL_ID), "SINGHA BEER CO., LTD.", "SS 681107140",
         date(2025, 11, 21), None, 4609.99, "beverage_raw", "เครื่องดื่ม", "paid",
         date(2025, 11, 27), "confirmed", None, BANK_ID),
    ]])
    conn = FakeConn(cursor)
    monkeypatch.setattr(routes, "get_db_conn", lambda: conn)

    result = routes.list_bills_payment(
        month="2025-11", status=None, vendor=None, branch=routes.DEFAULT_BRANCH)

    assert [b["bank_statement_entry_id"] for b in result["bills"]] == [BANK_ID, BANK_ID]

    sql = " ".join(cursor.queries[0][0].split())
    assert "AS bank_statement_entry_id" in sql
    assert "FROM public.bank_entry_bill_links l" in sql
    assert "l.vendor_bill_id = vb.id" in sql
    # The legacy mirror must not be the source of this flag any more.
    assert "b.matched_invoice_id = vb.id" not in sql
