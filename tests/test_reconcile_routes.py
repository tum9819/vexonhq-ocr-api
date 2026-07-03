import os
from unittest.mock import MagicMock, patch

os.environ.setdefault("DATABASE_URL", "postgresql://u:p@localhost:5432/d")
os.environ.setdefault("JWT_SECRET", "testsecret")
os.environ.setdefault("OPENAI_API_KEY", "x")

from fastapi.testclient import TestClient

import auth_routes
import main
from reconcile_routes import reconcile_platform_payout_rows


def _fake_verify(token):
    if token == "STAFF":
        return {"sub": "staff-uid", "_role": "staff"}
    return None


def _client(monkeypatch):
    monkeypatch.setattr(main, "verify_token", _fake_verify)
    monkeypatch.setattr(auth_routes, "verify_token", _fake_verify)
    return TestClient(main.app, raise_server_exceptions=False)


def test_reconcile_no_data_returns_no_data_status():
    result = reconcile_platform_payout_rows([], [])

    assert result["grab"]["status"] == "no_data"
    assert result["grab"]["system_payout"] == 0.0
    assert result["grab"]["bank_payout"] == 0.0
    assert result["grab"]["diff"] == 0.0
    assert result["grab"]["diff_pct"] == 0.0
    assert result["lineman"]["status"] == "no_data"
    assert result["lineman"]["estimated"] is True


def test_reconcile_normal_diff_under_threshold():
    result = reconcile_platform_payout_rows(
        [("grab", 1000), ("lineman", 2000)],
        [("grab_payout", 1010), ("lineman_payout", 1980)],
    )

    assert result["grab"]["status"] == "ok"
    assert result["grab"]["diff"] == 10.0
    assert result["grab"]["diff_pct"] == 1.0
    assert result["grab"]["warning"] is False
    assert result["lineman"]["status"] == "ok"
    assert result["lineman"]["diff"] == -20.0
    assert result["lineman"]["diff_pct"] == -1.0
    assert result["lineman"]["warning"] is False


def test_reconcile_diff_over_threshold_warns():
    result = reconcile_platform_payout_rows(
        [("grab", 1000), ("lineman", 2000)],
        [("grab_payout", 1030), ("lineman_payout", 1900)],
    )

    assert result["grab"]["status"] == "diff_over_threshold"
    assert result["grab"]["diff_pct"] == 3.0
    assert result["grab"]["warning"] is True
    assert result["lineman"]["status"] == "diff_over_threshold"
    assert result["lineman"]["diff_pct"] == -5.0
    assert result["lineman"]["warning"] is True


def test_reconcile_system_data_without_bank_is_no_bank_data():
    result = reconcile_platform_payout_rows([("lineman", 2000)], [])

    assert result["lineman"]["status"] == "no_bank_data"
    assert result["lineman"]["system_payout"] == 2000.0
    assert result["lineman"]["bank_payout"] == 0.0
    assert result["lineman"]["diff"] is None
    assert result["lineman"]["diff_pct"] is None
    assert result["lineman"]["warning"] is False


def test_reconcile_endpoint_uses_staff_jwt_and_read_only_sql(monkeypatch):
    client = _client(monkeypatch)
    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cur
    mock_cur.fetchall.side_effect = [
        [("grab", 1000)],
        [("grab_payout", 1010)],
    ]

    with patch("reconcile_routes.get_db_conn", return_value=mock_conn):
        response = client.get(
            "/reconcile/platform-payout?month=2026-06&lag_days=7",
            headers={"Authorization": "Bearer STAFF"},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["month"] == "2026-06"
    assert data["lag_days"] == 7
    assert data["platforms"]["grab"]["diff_pct"] == 1.0
    assert data["platforms"]["grab"]["estimated"] is False

    executed = [call[0][0].strip().upper() for call in mock_cur.execute.call_args_list]
    assert len(executed) == 2
    assert all(q.startswith("SELECT ") for q in executed)
    assert not any("INSERT " in q or "UPDATE " in q or "DELETE " in q for q in executed)
    mock_conn.commit.assert_not_called()
    mock_conn.close.assert_called_once()
