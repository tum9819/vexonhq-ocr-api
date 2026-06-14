import pytest
from unittest.mock import MagicMock, patch
from phase2_routes import dashboard_executive

def test_dashboard_pending_bills_query_contract():
    # Arrange
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
    
    # Mock _summarize_month to avoid its own queries failing
    mock_summ = {
        "sales_net": 1000, "sales_gross": 1000, "expense_total": 500, "gross_profit": 500,
        "gross_margin_pct": 50.0,
        "expense_category": {}, "sales_channels": {},
        "expense_cogs": 200, "expense_payroll": 100, "expense_rent": 100, "expense_utility": 50, "expense_misc": 50
    }

    # row = [sales_as_of, daybook_as_of, sales_30d, bills_pending, ap_count, ap_total, ap_overdue, stock_as_of, low_stock_count, prev_day_sales, ap_due_7d, ap_due_7d_count, pos_items_count]
    # Indices:
    # 0: sales_as_of (date)
    # 1: daybook_as_of (date)
    # 2: sales_30d (numeric)
    # 3: bills_pending (int) <- This is what we care about! Wait, bills_pending is index 3.
    # 4: ap_count
    # 5: ap_total
    # 6: ap_overdue
    # 7: stock_as_of
    # 8: low_stock_count
    # 9: prev_day_sales
    # 10: ap_due_7d
    # 11: ap_due_7d_count
    # 12: pos_items_count
    from datetime import date
    mock_row = [date(2026,6,8), date(2026,6,8), 10000, 11, 5, 5000, 1000, date(2026,6,8), 0, 500, date(2026,6,7), 500, 0, 0, 0]
    
    mock_cursor.fetchone.side_effect = [mock_row, (1000,), (500,), (1,)] # First is metrics, others are for AI insight/top categories maybe?
    mock_cursor.fetchall.return_value = [] # For list queries
    
    with patch("phase2_routes.get_db_conn", return_value=mock_conn), \
         patch("phase2_routes._summarize_month", return_value=mock_summ), \
         patch("phase2_routes._require_admin_role", return_value={"uid": "admin"}):
        
        # Act
        res = dashboard_executive(month=None, branch="HQ", _admin={})
        
        # Assert Query
        executed_queries = [call[0][0] for call in mock_cursor.execute.call_args_list]
        metrics_query = executed_queries[0]
        
        # Check that the contract includes 'pending' and 'needs_attention'
        assert "WHERE review_status IN ('pending', 'needs_attention')" in metrics_query
        
        # Ensure it's read-only
        assert "INSERT" not in metrics_query.upper()
        assert "UPDATE" not in metrics_query.upper()
        assert "DELETE" not in metrics_query.upper()
        assert not mock_conn.commit.called, "Must not commit mutations"
        
        # Check response maps correctly
        cards = res["cards"]
        bills_card = next(c for c in cards if c["key"] == "bills_pending_review")
        
        assert bills_card["value"] == 11
        assert bills_card["status"] == "critical" # 11 is critical
        
        # Test 0 pending
        mock_cursor.fetchone.side_effect = [[date(2026,6,8), date(2026,6,8), 10000, 0, 5, 5000, 1000, date(2026,6,8), 0, 500, date(2026,6,7), 500, 0, 0, 0]] + [[(1000,)]]*10
        res0 = dashboard_executive(month=None, branch="HQ", _admin={})
        bills_card_0 = next(c for c in res0["cards"] if c["key"] == "bills_pending_review")
        assert bills_card_0["value"] == 0
        assert bills_card_0["status"] == "healthy"
