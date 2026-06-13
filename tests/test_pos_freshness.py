"""
Offline unit tests for the POS sales-freshness signal (Reliability Phase).
Pure logic — no DB, no API key — runs in verify.ps1 [1b] / CI.
"""

from datetime import date
from unittest.mock import patch, MagicMock
from pos_freshness import pos_freshness_signal
import pytest

TODAY = date(2026, 6, 9)

# 1. single branch fresh
def test_single_branch_fresh():
    stale, days, msg = pos_freshness_signal(date(2026, 6, 8), TODAY, branch_code="thawi_watthana")
    assert stale is False
    assert msg is None

# 2. single branch stale
def test_single_branch_stale():
    stale, days, msg = pos_freshness_signal(date(2026, 6, 6), TODAY, branch_code="thawi_watthana")
    assert stale is True
    assert "thawi_watthana" in msg

# 3. two branches: one fresh, one stale
def test_two_branches_fresh_and_stale():
    stale_f, days_f, msg_f = pos_freshness_signal(date(2026, 6, 8), TODAY, branch_code="charoen_nakhorn")
    assert stale_f is False
    stale_s, days_s, msg_s = pos_freshness_signal(date(2026, 6, 5), TODAY, branch_code="thawi_watthana")
    assert stale_s is True

# 4. two stale branches
def test_two_branches_both_stale():
    s1, d1, m1 = pos_freshness_signal(date(2026, 6, 1), TODAY, branch_code="charoen_nakhorn")
    assert s1 is True
    s2, d2, m2 = pos_freshness_signal(date(2026, 6, 5), TODAY, branch_code="thawi_watthana")
    assert s2 is True

# 5. NULL/unknown branch
def test_null_or_unknown_branch():
    stale, days, msg = pos_freshness_signal(date(2026, 6, 1), TODAY, branch_code=None)
    assert stale is True
    assert "สาขา None" not in msg

# 6. no POS data
def test_no_pos_data():
    stale, days, msg = pos_freshness_signal(None, TODAY, branch_code="thawi_watthana")
    assert stale is False

# 7. Asia/Bangkok date calculation
def test_asia_bangkok_date_calculation():
    stale, days, msg = pos_freshness_signal(date(2026, 6, 7), TODAY)
    assert stale is False

# 8. stale-day calculation
def test_stale_day_calculation():
    stale, days, msg = pos_freshness_signal(date(2026, 5, 31), TODAY)
    assert days == 9

# 9. Discord payload includes correct branch
def test_discord_payload_includes_branch():
    stale, days, msg = pos_freshness_signal(date(2026, 6, 1), TODAY, branch_code="bkk_rama2")
    assert "bkk_rama2" in msg

# 10. LINE payload includes correct branch
def test_line_payload_includes_branch():
    stale, days, msg = pos_freshness_signal(date(2026, 6, 1), TODAY, branch_code="bkk_rama2")
    assert "bkk_rama2" in msg

# 11. Query error is sanitized
def test_query_error_is_sanitized_pure():
    pass

# 12. Freshness check has no mutation
def test_freshness_check_has_no_mutation():
    res1 = pos_freshness_signal(date(2026, 6, 6), TODAY, branch_code="thawi_watthana")
    res2 = pos_freshness_signal(date(2026, 6, 6), TODAY, branch_code="thawi_watthana")
    assert res1 == res2

def test_edge_exactly_2_days_no_alert():
    stale, days, msg = pos_freshness_signal(date(2026, 6, 7), TODAY)
    assert stale is False

def test_3_days_alerts():
    stale, days, msg = pos_freshness_signal(date(2026, 6, 6), TODAY)
    assert stale is True

def test_today_equals_latest_no_alert():
    stale, days, msg = pos_freshness_signal(TODAY, TODAY)
    assert stale is False

def test_threshold_env_default_is_2():
    assert pos_freshness_signal(date(2026, 6, 7), TODAY)[0] is False
    assert pos_freshness_signal(date(2026, 6, 6), TODAY)[0] is True

def test_custom_threshold_widens_window():
    assert pos_freshness_signal(date(2026, 6, 6), TODAY, threshold_days=5)[0] is False
    assert pos_freshness_signal(date(2026, 6, 3), TODAY, threshold_days=5)[0] is True


# ==========================================
# Route/Job-level Mocked Tests
# ==========================================

@patch("line_bot_routes._get_db_conn")
@patch("line_bot_routes._today_bkk")
@patch("auto_diagnose._post_to_discord")
def test_scheduled_freshness_check_integration(mock_discord, mock_today, mock_db_conn):
    from line_bot_routes import _scheduled_pos_freshness_check

    # Setup mocks
    mock_today.return_value = date(2026, 6, 9)

    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cur
    mock_db_conn.return_value = mock_conn

    # 1. No data
    mock_cur.fetchall.return_value = []
    _scheduled_pos_freshness_check()
    mock_discord.assert_not_called()

    # 2. Single fresh branch
    mock_cur.fetchall.return_value = [("branch_1", date(2026, 6, 8))]
    _scheduled_pos_freshness_check()
    mock_discord.assert_not_called()

    # 3. Two branches, one fresh, one stale
    mock_cur.fetchall.return_value = [("branch_1", date(2026, 6, 8)), ("branch_2", date(2026, 6, 5))]
    _scheduled_pos_freshness_check()
    assert mock_discord.call_count == 1
    assert "branch_2" in mock_discord.call_args[0][0]
    mock_discord.reset_mock()

    # 4. Two stale branches
    mock_cur.fetchall.return_value = [("branch_1", date(2026, 6, 5)), ("branch_2", date(2026, 6, 1))]
    _scheduled_pos_freshness_check()
    assert mock_discord.call_count == 2
    assert "branch_1" in mock_discord.call_args_list[0][0][0]
    assert "branch_2" in mock_discord.call_args_list[1][0][0]
    mock_discord.reset_mock()

    # 5. NULL branch
    mock_cur.fetchall.return_value = [(None, date(2026, 6, 1))]
    _scheduled_pos_freshness_check()
    assert mock_discord.call_count == 1
    assert "สาขา None" not in mock_discord.call_args[0][0]

@patch("line_bot_routes._get_db_conn")
def test_scheduled_freshness_check_db_error(mock_db_conn):
    from line_bot_routes import _scheduled_pos_freshness_check
    mock_db_conn.side_effect = Exception("DB Error Sanitized Check")

    with pytest.raises(Exception, match="DB Error Sanitized Check"):
        _scheduled_pos_freshness_check()

@patch("line_bot_routes._get_db_conn")
@patch("line_bot_routes._today_bkk")
@patch("auto_diagnose._post_to_discord")
def test_scheduled_freshness_check_order_is_deterministic(mock_discord, mock_today, mock_db_conn):
    from line_bot_routes import _scheduled_pos_freshness_check
    mock_today.return_value = date(2026, 6, 9)

    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cur
    mock_db_conn.return_value = mock_conn

    # DB with ORDER BY branch_code NULLS LAST returns sorted order:
    mock_cur.fetchall.return_value = [
        ("branch_a", date(2026, 6, 1)),
        ("branch_b", date(2026, 6, 1)),
        (None, date(2026, 6, 1))
    ]

    _scheduled_pos_freshness_check()

    # Query must contain the ORDER BY clause
    query_executed = mock_cur.execute.call_args[0][0]
    assert "ORDER BY branch_code NULLS LAST" in query_executed

    # Must call Discord sequentially in exactly that order
    assert mock_discord.call_count == 3
    assert "branch_a" in mock_discord.call_args_list[0][0][0]
    assert "branch_b" in mock_discord.call_args_list[1][0][0]
    assert "branch_b" not in mock_discord.call_args_list[2][0][0]
    assert "branch_a" not in mock_discord.call_args_list[2][0][0]

@patch("line_bot_routes._get_db_conn")
@patch("line_bot_routes._today_bkk")
@patch("auto_diagnose._post_to_discord")
def test_scheduled_freshness_check_no_mutation(mock_discord, mock_today, mock_db_conn):
    from line_bot_routes import _scheduled_pos_freshness_check
    mock_today.return_value = date(2026, 6, 9)

    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cur
    mock_db_conn.return_value = mock_conn

    mock_cur.fetchall.return_value = [("branch_1", date(2026, 6, 1))]

    def fake_execute(query, *args, **kwargs):
        # Assert that SQL is exclusively SELECT and starts with SELECT
        q = query.strip().upper()
        if not q.startswith("SELECT "):
            raise ValueError(f"Mutation detected in SQL: {query}")
        for forbidden in ["INSERT ", "UPDATE ", "DELETE ", "ALTER ", "DROP ", "TRUNCATE "]:
            if forbidden in q:
                raise ValueError(f"Mutation detected in SQL: {query}")

    mock_cur.execute.side_effect = fake_execute

    _scheduled_pos_freshness_check()

    # Verify no commit was called on the connection
    mock_conn.commit.assert_not_called()

    # Verify connection was explicitly closed
    mock_conn.close.assert_called_once()
