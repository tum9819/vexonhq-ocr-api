from unittest.mock import MagicMock, patch
import pytest
from datetime import date
from tax_routes import wht_summary

@patch("tax_routes.get_db_conn")
def test_wht_summary_computes_gross_correctly(mock_get_conn):
    # Mock database connection and cursor
    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_get_conn.return_value = mock_conn
    mock_conn.cursor.return_value.__enter__.return_value = mock_cur
    
    # We will simulate returning one musician fee transaction of 10,000 baht
    # musician_fee WHT rate is 3%
    mock_cur.description = [("category_code",), ("txn_date",), ("description",), ("amount",)]
    mock_cur.fetchall.return_value = [
        ("musician_fee", date(2026, 4, 15), "นักดนตรีวันเสาร์", 10000.0)
    ]
    
    # Call the endpoint summary function for April 2026
    res = wht_summary(month="2026-04")
    
    # Verify transaction list has the correct mapped values
    assert len(res["transactions"]) == 1
    txn = res["transactions"][0]
    
    # Gross amount should be 10,000 (amount_paid)
    assert txn["amount_paid"] == 10000.0
    
    # Withholding tax (3%) is 300.0
    assert txn["wht_amount"] == 300.0
    
    # net_before_wht should be equal to the gross amount (10000.0)
    assert txn["net_before_wht"] == 10000.0
    
    # net_paid should be amount - WHT = 9700.0
    assert txn["net_paid"] == 9700.0
    
    # Summary totals check
    assert res["total_paid"] == 10000.0
    assert res["total_wht"] == 300.0
    assert res["total_net"] == 9700.0
