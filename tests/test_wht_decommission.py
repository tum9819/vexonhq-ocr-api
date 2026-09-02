import io
import zipfile
from unittest.mock import MagicMock

import openpyxl
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

import export_routes
import yearly_routes


def test_monthly_pnd3_route_returns_gone_before_building(monkeypatch):
    built = []
    monkeypatch.setattr(
        export_routes,
        "_build_pnd3",
        lambda month: built.append(month) or openpyxl.Workbook(),
    )

    with pytest.raises(HTTPException) as exc_info:
        export_routes.export_pnd3(month="2026-07")

    assert exc_info.value.status_code == 410
    assert built == []


def test_annual_pnd3_route_returns_gone_before_querying(monkeypatch):
    get_db_conn = MagicMock()
    monkeypatch.setattr(yearly_routes, "get_db_conn", get_db_conn)

    with pytest.raises(HTTPException) as exc_info:
        yearly_routes.export_pnd3_annual(year=2026)

    assert exc_info.value.status_code == 410
    assert get_db_conn.call_count == 0


def test_retired_pnd_routes_return_gone_even_for_invalid_legacy_queries():
    app = FastAPI()
    app.include_router(export_routes.router)
    app.include_router(yearly_routes.router)
    client = TestClient(app)

    assert client.get("/export/pnd3?month=not-a-month").status_code == 410
    assert client.get("/export/pnd3-annual?year=not-a-year").status_code == 410


def test_zip_bundle_contains_only_daybook_and_category_summary(monkeypatch):
    workbook = openpyxl.Workbook()
    monkeypatch.setattr(export_routes, "_build_category_summary", lambda _month: workbook)
    monkeypatch.setattr(export_routes, "_build_daybook", lambda _month: workbook)
    monkeypatch.setattr(export_routes, "_build_pnd3", lambda _month: workbook)

    response = export_routes.export_zip_bundle(month="2026-07")
    with zipfile.ZipFile(io.BytesIO(response.body)) as archive:
        assert archive.namelist() == [
            "category_summary_2026-07.xlsx",
            "daybook_2026-07.xlsx",
        ]


class _SummaryCursor:
    def __init__(self):
        self.sql = ""

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql, _params):
        self.sql = sql

    def fetchone(self):
        if "COUNT(DISTINCT category_code)" in self.sql:
            return (2, 50)
        if "category_code IS NULL" in self.sql:
            return (0, 0)
        if "rider_deliveries" in self.sql:
            return (2, 10)
        if "CASE" in self.sql and "total_wht" in self.sql:
            return (2, 5)
        return (10, 100, 50)


class _SummaryConnection:
    def __init__(self):
        self.cursor_instance = _SummaryCursor()

    def cursor(self):
        return self.cursor_instance

    def close(self):
        return None


def test_export_summary_preserves_decommissioned_pnd3_contract(monkeypatch):
    monkeypatch.setattr(export_routes, "get_db_conn", _SummaryConnection)

    result = export_routes.export_summary(month="2026-07")

    assert result["pnd3"] == {
        "available": False,
        "status": "decommissioned",
        "rows": 0,
        "total_withholding": 0,
    }
    assert result["zip_bundle"]["files"] == 2
