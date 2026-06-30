import os
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest
from fastapi.testclient import TestClient

# Prevent actual DB/auth issues during import
os.environ.setdefault("DATABASE_URL", "postgresql://u:p@localhost:5432/d")
os.environ.setdefault("JWT_SECRET", "testsecret")
os.environ.setdefault("OPENAI_API_KEY", "x")

import main
import auth_routes
import phase3a_ai_categorize_routes

def _fake_verify(token):
    if token == "ADMIN":
        return {"sub": "admin-uid", "_role": "admin"}
    return None

@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setattr(main, "verify_token", _fake_verify)
    monkeypatch.setattr(auth_routes, "verify_token", _fake_verify)
    return TestClient(main.app, raise_server_exceptions=False)

def test_batch_categorize_dry_run(client):
    # Mock database connections
    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cur

    # Mock DB query results
    # 1. Fetching pending bills
    # 2. Fetching one bill details
    # 3. Fetching items (empty list)
    # 4. _try_rule_match -> rule found
    mock_cur.fetchall.side_effect = [
        [("9504a58b-3023-455b-bf99-2e06180a316c",)],  # v_bills_needing_category
        [],                                            # invoice_items
    ]
    
    mock_cur.fetchone.side_effect = [
        ("9504a58b-3023-455b-bf99-2e06180a316c", "Mock Vendor", "12345", "2026-06-01", 100.0, "INV-001", "confirmed", None), # vendor_bills details
        ("Mock Vendor Rule", "food_raw"),              # vendor_category_rules pattern, category_code
    ]

    # Description mapping for rows_to_dicts and columns fetch
    # _fetch_bill_with_items columns
    bill_desc = [
        ("id",), ("vendor_name",), ("merchant_tax_id",), ("bill_date",),
        ("amount",), ("invoice_no",), ("review_status",), ("category_code",)
    ]
    item_desc = [("product_name",), ("quantity",), ("amount",)]
    rule_desc = [("pattern",), ("category_code",)]

    mock_cur.description = None # default

    def mock_execute(query, params=None):
        if "vendor_bills" in query:
            mock_cur.description = bill_desc
        elif "invoice_items" in query:
            mock_cur.description = item_desc
        elif "vendor_category_rules" in query and "SELECT" in query:
            mock_cur.description = rule_desc
        else:
            mock_cur.description = None

    mock_cur.execute.side_effect = mock_execute

    with patch("phase3a_ai_categorize_routes.get_db_conn", return_value=mock_conn):
        response = client.post(
            "/ai/categorize/batch?dry_run=true",
            headers={"Authorization": "Bearer ADMIN"}
        )

    assert response.status_code == 200
    data = response.json()
    assert data["dry_run"] is True
    assert data["processed"] == 1
    assert data["by_tier"]["rule"] == 1

    # In dry_run, conn.rollback must be called and commit must not be called inside _categorize_one
    assert mock_conn.rollback.called
    assert not mock_conn.commit.called

    # Ensure no UPDATE vendor_bills or INSERT ai_categorization_log was called
    executed_queries = [call[0][0] for call in mock_cur.execute.call_args_list]
    for q in executed_queries:
        assert "UPDATE public.vendor_bills" not in q
        assert "INSERT INTO public.ai_categorization_log" not in q

def test_batch_categorize_no_dry_run(client):
    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cur

    mock_cur.fetchall.side_effect = [
        [("9504a58b-3023-455b-bf99-2e06180a316c",)],  # v_bills_needing_category
        [],                                            # invoice_items
    ]
    mock_cur.fetchone.side_effect = [
        ("9504a58b-3023-455b-bf99-2e06180a316c", "Mock Vendor", "12345", "2026-06-01", 100.0, "INV-001", "confirmed", None), # vendor_bills details
        ("Mock Vendor Rule", "food_raw"),              # vendor_category_rules pattern, category_code
        ("log-id-1", "2026-06-01T00:00:00")            # RETURNING id, applied_at
    ]

    bill_desc = [
        ("id",), ("vendor_name",), ("merchant_tax_id",), ("bill_date",),
        ("amount",), ("invoice_no",), ("review_status",), ("category_code",)
    ]
    item_desc = [("product_name",), ("quantity",), ("amount",)]
    rule_desc = [("pattern",), ("category_code",)]

    def mock_execute(query, params=None):
        if "vendor_bills" in query:
            mock_cur.description = bill_desc
        elif "invoice_items" in query:
            mock_cur.description = item_desc
        elif "vendor_category_rules" in query and "SELECT" in query:
            mock_cur.description = rule_desc
        else:
            mock_cur.description = None

    mock_cur.execute.side_effect = mock_execute

    with patch("phase3a_ai_categorize_routes.get_db_conn", return_value=mock_conn):
        response = client.post(
            "/ai/categorize/batch",
            headers={"Authorization": "Bearer ADMIN"}
        )

    assert response.status_code == 200
    data = response.json()
    assert "dry_run" not in data
    assert data["processed"] == 1

    # In regular run, commit must be called
    assert mock_conn.commit.called
    assert not mock_conn.rollback.called

    # Ensure UPDATE vendor_bills and INSERT ai_categorization_log were called
    executed_queries = [call[0][0] for call in mock_cur.execute.call_args_list]
    assert any("UPDATE public.vendor_bills" in q for q in executed_queries)
    assert any("INSERT INTO public.ai_categorization_log" in q for q in executed_queries)

def test_cashflow_batch_categorize_dry_run(client):
    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cur

    mock_cur.fetchall.side_effect = [
        [("9504a58b-3023-455b-bf99-2e06180a316d",)],  # pos_cashflow_entries
    ]
    mock_cur.fetchone.side_effect = [
        ("9504a58b-3023-455b-bf99-2e06180a316d", "ผักสด", False, "pending"), # pos_cashflow_entries details
        ("ผักสด Rule", "food_raw"),                   # vendor_category_rules pattern, category_code
    ]

    rule_desc = [("pattern",), ("category_code",)]
    def mock_execute(query, params=None):
        if "vendor_category_rules" in query and "SELECT" in query:
            mock_cur.description = rule_desc
        else:
            mock_cur.description = None

    mock_cur.execute.side_effect = mock_execute

    with patch("phase3a_ai_categorize_routes.get_db_conn", return_value=mock_conn):
        response = client.post(
            "/ai/categorize/cashflow/batch?dry_run=true",
            headers={"Authorization": "Bearer ADMIN"}
        )

    assert response.status_code == 200
    data = response.json()
    assert data["dry_run"] is True
    assert data["processed"] == 1
    assert data["by_tier"]["rule"] == 1

    assert mock_conn.rollback.called
    assert not mock_conn.commit.called

    executed_queries = [call[0][0] for call in mock_cur.execute.call_args_list]
    for q in executed_queries:
        assert "UPDATE public.pos_cashflow_entries" not in q
        assert "INSERT INTO public.ai_categorization_log" not in q

def test_cashflow_batch_categorize_no_dry_run(client):
    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cur

    mock_cur.fetchall.side_effect = [
        [("9504a58b-3023-455b-bf99-2e06180a316d",)],  # pos_cashflow_entries
    ]
    mock_cur.fetchone.side_effect = [
        ("9504a58b-3023-455b-bf99-2e06180a316d", "ผักสด", False, "pending"), # pos_cashflow_entries details
        ("ผักสด Rule", "food_raw"),                   # vendor_category_rules pattern, category_code
    ]

    rule_desc = [("pattern",), ("category_code",)]
    def mock_execute(query, params=None):
        if "vendor_category_rules" in query and "SELECT" in query:
            mock_cur.description = rule_desc
        else:
            mock_cur.description = None

    mock_cur.execute.side_effect = mock_execute

    with patch("phase3a_ai_categorize_routes.get_db_conn", return_value=mock_conn):
        response = client.post(
            "/ai/categorize/cashflow/batch",
            headers={"Authorization": "Bearer ADMIN"}
        )

    assert response.status_code == 200
    data = response.json()
    assert "dry_run" not in data
    assert data["processed"] == 1

    assert mock_conn.commit.called
    assert not mock_conn.rollback.called

    executed_queries = [call[0][0] for call in mock_cur.execute.call_args_list]
    assert any("UPDATE public.pos_cashflow_entries" in q for q in executed_queries)
    assert any("INSERT INTO public.ai_categorization_log" in q for q in executed_queries)

def test_reject_user_action_nulls_out_category(client):
    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cur

    # SELECT bill_id, suggested_category FROM public.ai_categorization_log WHERE id = %s
    mock_cur.fetchone.return_value = ("bill-uuid-1", "food_raw")

    with patch("phase3a_ai_categorize_routes.get_db_conn", return_value=mock_conn):
        response = client.patch(
            "/ai/categorize/log/9504a58b-3023-455b-bf99-2e06180a316e",
            headers={"Authorization": "Bearer ADMIN"},
            json={"action": "reject"}
        )

    assert response.status_code == 200
    data = response.json()
    assert data["applied"] is True

    assert mock_conn.commit.called

    executed_queries = [call[0][0] for call in mock_cur.execute.call_args_list]
    assert any("UPDATE public.vendor_bills SET category_code = NULL WHERE id = %s" in q for q in executed_queries)
