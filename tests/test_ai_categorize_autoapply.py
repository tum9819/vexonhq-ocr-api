import os
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

os.environ.setdefault("DATABASE_URL", "postgresql://u:p@localhost:5432/d")
os.environ.setdefault("JWT_SECRET", "testsecret")
os.environ.setdefault("OPENAI_API_KEY", "x")

import phase3a_ai_categorize_routes as routes


def _bill_conn(
    *,
    vendor_name: str,
    rule_category: str,
    llm: dict | None = None,
    items: list[tuple] | None = None,
    categories: list[tuple] | None = None,
):
    conn = MagicMock()
    cur = MagicMock()
    conn.cursor.return_value.__enter__.return_value = cur

    bill_desc = [
        ("id",), ("vendor_name",), ("merchant_tax_id",), ("bill_date",),
        ("amount",), ("invoice_no",), ("review_status",), ("category_code",)
    ]
    item_desc = [("product_name",), ("quantity",), ("amount",)]
    rule_desc = [("pattern",), ("category_code",)]
    category_desc = [("code",), ("name_th",), ("parent_code",), ("direction",)]

    def mock_execute(query, params=None):
        if "FROM public.vendor_bills" in query and "WHERE id = %s" in query:
            cur.description = bill_desc
        elif "FROM public.invoice_items" in query:
            cur.description = item_desc
        elif "FROM public.vendor_category_rules" in query and "SELECT" in query:
            cur.description = rule_desc
        elif "FROM public.expense_categories" in query and "direction IN" in query:
            cur.description = category_desc
        else:
            cur.description = None

    cur.execute.side_effect = mock_execute
    cur.fetchall.side_effect = [
        items or [],
        categories or [("raw_meat", "เนื้อสัตว์", None, "expense")],
    ]
    cur.fetchone.side_effect = [
        (
            "9504a58b-3023-455b-bf99-2e06180a316c",
            vendor_name,
            "12345",
            "2026-06-01",
            100.0,
            "INV-001",
            "confirmed",
            None,
        ),
        ("%singha%beer%" if "SINGHA" in vendor_name.upper() else "%ซีพี%แอ็กซ์ตร้า%", rule_category),
        (1,) if llm else ("log-id-1", "2026-06-01T00:00:00"),
        ("log-id-1", "2026-06-01T00:00:00"),
    ]
    return conn, cur


def _cashflow_conn(*, llm_confidence: float, category_exists: bool = True):
    conn = MagicMock()
    cur = MagicMock()
    conn.cursor.return_value.__enter__.return_value = cur
    cur.fetchone.side_effect = [
        ("9504a58b-3023-455b-bf99-2e06180a316d", "หมูสไลด์", False, "pending", None),
        None,
        (1,) if category_exists else None,
    ]
    cur.fetchall.return_value = [("raw_meat", "เนื้อ", None, "expense")]
    cur.description = [("code",), ("name_th",), ("parent_code",), ("direction",)]
    llm = {
        "tier": "llm",
        "category_code": "raw_meat",
        "confidence": llm_confidence,
        "reason": "matched meat keyword",
        "model_name": "test-model",
        "prompt_tokens": 10,
        "completion_tokens": 5,
    }
    return conn, cur, llm


def test_cashflow_llm_confidence_at_threshold_auto_applies(monkeypatch):
    monkeypatch.setenv("AI_AUTOAPPLY_MIN_CONF", "0.90")
    conn, cur, llm = _cashflow_conn(llm_confidence=0.91)

    with patch("phase3a_ai_categorize_routes._call_llm", return_value=llm):
        result = routes._categorize_cashflow_one(conn, "9504a58b-3023-455b-bf99-2e06180a316d")

    assert result["applied"] is True
    executed = [call.args[0] for call in cur.execute.call_args_list]
    assert any("UPDATE public.pos_cashflow_entries" in q and "ai_cat_status='confirmed'" in q for q in executed)
    assert any("applied, before_category, applied_by" in q for q in executed)


def test_cashflow_llm_below_threshold_logs_pending_without_category_update(monkeypatch):
    monkeypatch.setenv("AI_AUTOAPPLY_MIN_CONF", "0.90")
    conn, cur, llm = _cashflow_conn(llm_confidence=0.89)

    with patch("phase3a_ai_categorize_routes._call_llm", return_value=llm):
        result = routes._categorize_cashflow_one(conn, "9504a58b-3023-455b-bf99-2e06180a316d")

    assert result["applied"] is False
    executed = [call.args[0] for call in cur.execute.call_args_list]
    assert not any("SET category_code=%s, ai_cat_status='confirmed'" in q for q in executed)
    assert any("ai_cat_status='review'" in q for q in executed)
    assert any("applied, before_category, applied_by" in q for q in executed)


def test_autoapply_can_be_disabled_with_threshold_above_one(monkeypatch):
    monkeypatch.setenv("AI_AUTOAPPLY_MIN_CONF", "1.10")
    assert routes._should_autoapply({"tier": "llm", "confidence": 1.0}) is False
    assert routes._should_autoapply({"tier": "rule", "confidence": 1.0}) is True


def test_singha_vendor_bill_rule_uses_bill_beverage_category():
    conn, cur = _bill_conn(
        vendor_name="SINGHA BEER CO., LTD.",
        rule_category="raw_beverage",
    )

    result = routes._categorize_one(conn, "9504a58b-3023-455b-bf99-2e06180a316c")

    assert result["applied"] is True
    assert result["category_code"] == "beverage"
    update_calls = [
        call.args for call in cur.execute.call_args_list
        if "UPDATE public.vendor_bills SET category_code = %s WHERE id = %s" in call.args[0]
    ]
    assert update_calls
    assert update_calls[0][1][0] == "beverage"


def test_ambiguous_wholesale_vendor_bill_logs_pending_without_autoapply(monkeypatch):
    monkeypatch.setenv("AI_AUTOAPPLY_MIN_CONF", "0.90")
    llm = {
        "tier": "llm",
        "category_code": "raw_meat",
        "confidence": 0.99,
        "reason": "items look like meat",
        "model_name": "test-model",
        "prompt_tokens": 10,
        "completion_tokens": 5,
    }
    conn, cur = _bill_conn(
        vendor_name="บริษัท ซีพี แอ็กซ์ตร้า จำกัด (มหาชน)",
        rule_category="raw_meat",
        llm=llm,
        items=[("หมูสไลด์", 1, 100.0)],
    )

    with patch("phase3a_ai_categorize_routes._call_llm", return_value=llm) as call_llm:
        result = routes._categorize_one(conn, "9504a58b-3023-455b-bf99-2e06180a316c")

    assert call_llm.called
    assert result["applied"] is False
    assert result["category_code"] == "raw_meat"
    executed = [call.args[0] for call in cur.execute.call_args_list]
    assert not any("UPDATE public.vendor_bills SET category_code = %s WHERE id = %s" in q for q in executed)


def test_reject_applied_cashflow_log_reverts_to_before_category():
    conn = MagicMock()
    cur = MagicMock()
    conn.cursor.return_value.__enter__.return_value = cur
    cur.fetchone.return_value = (
        None,
        "9504a58b-3023-455b-bf99-2e06180a316d",
        "cashflow",
        "raw_meat",
        "other_expense",
        True,
        None,
    )

    routes._apply_log_user_action(cur, "log-id", "reject", None)

    executed = [call.args[0] for call in cur.execute.call_args_list]
    assert any("UPDATE public.pos_cashflow_entries" in q for q in executed)
    assert any("SET category_code = %s" in q and "ai_cat_status" in q for q in executed)
    assert any("undone_at = now()" in q for q in executed)


def test_ai_categorization_log_migration_adds_audit_columns():
    with open("migrations/2026_07_12_fa003_ai_autoapply_audit.sql", encoding="utf-8") as f:
        sql = f.read()
    for column in ["applied", "before_category", "applied_by", "undone_at", "undone_by", "undo_reason"]:
        assert column in sql
