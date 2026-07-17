from unittest.mock import MagicMock, patch

import pnl_routes


def test_monthly_sales_bill_count_comes_from_pos_receipt_totals():
    conn = MagicMock()
    cur = MagicMock()
    conn.cursor.return_value.__enter__.return_value = cur
    cur.description = [
        ("month",),
        ("sales_net",),
        ("expense_total",),
        ("bill_count_sales",),
        ("bill_count_expense",),
    ]
    cur.fetchall.return_value = [("2026-04", 265540.52, 100000, 660, 25)]

    with patch("pnl_routes.get_db_conn", return_value=conn):
        result = pnl_routes.pnl_monthly(year=2026, branch_code="thawi_watthana")

    sql = cur.execute.call_args.args[0]
    assert "public.pos_sales_daily" in sql
    assert "SUM(bill_count)" in sql
    assert "COUNT(DISTINCT CASE WHEN d.source='pos_sale'" not in sql
    assert result["rows"][0]["bill_count_sales"] == 660
    assert result["totals"]["bill_count_sales"] == 660
