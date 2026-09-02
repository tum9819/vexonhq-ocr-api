from unittest.mock import patch

import pytest
from fastapi import HTTPException

from tax_routes import wht_export, wht_summary


@patch("tax_routes.get_db_conn", create=True)
def test_wht_summary_returns_gone_without_querying_database(mock_get_conn):
    with pytest.raises(HTTPException) as exc_info:
        wht_summary(month="2026-04")

    assert exc_info.value.status_code == 410
    assert "ยกเลิก" in str(exc_info.value.detail)
    mock_get_conn.assert_not_called()


@patch("tax_routes.get_db_conn", create=True)
def test_wht_export_returns_gone_without_querying_database(mock_get_conn):
    with pytest.raises(HTTPException) as exc_info:
        wht_export(month="2026-04")

    assert exc_info.value.status_code == 410
    assert "ยกเลิก" in str(exc_info.value.detail)
    mock_get_conn.assert_not_called()
