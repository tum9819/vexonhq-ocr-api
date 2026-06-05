from __future__ import annotations

import uuid

from pos_import import delete_pos_sales_items_by_bill_ids


class FakeCursor:
    def __init__(self):
        self.calls = []
        self.rowcount = 7

    def execute(self, sql, params):
        self.calls.append((sql, params))


def test_delete_pos_sales_items_by_bill_ids_casts_uuid_array():
    cur = FakeCursor()
    bill_ids = [uuid.UUID("11111111-1111-1111-1111-111111111111"), "22222222-2222-2222-2222-222222222222"]

    count = delete_pos_sales_items_by_bill_ids(cur, bill_ids)

    assert count == 7
    assert len(cur.calls) == 1
    sql, params = cur.calls[0]
    assert "bill_id = ANY(%s::uuid[])" in sql
    assert params == ([
        "11111111-1111-1111-1111-111111111111",
        "22222222-2222-2222-2222-222222222222",
    ],)


def test_delete_pos_sales_items_by_bill_ids_ignores_empty_list():
    cur = FakeCursor()

    count = delete_pos_sales_items_by_bill_ids(cur, [])

    assert count == 0
    assert cur.calls == []
