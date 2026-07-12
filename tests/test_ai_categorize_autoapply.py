import os
from unittest.mock import MagicMock, patch

os.environ.setdefault("DATABASE_URL", "postgresql://u:p@localhost:5432/d")
os.environ.setdefault("JWT_SECRET", "testsecret")
os.environ.setdefault("OPENAI_API_KEY", "x")

import phase3a_ai_categorize_routes as routes


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
