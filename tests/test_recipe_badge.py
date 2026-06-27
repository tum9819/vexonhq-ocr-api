import json

import pytest
from fastapi import HTTPException

import menu_public_routes
import recipe_routes


class _Cursor:
    def __init__(self, rows=None, rowcount=1):
        self.rows = rows or []
        self.rowcount = rowcount
        self.executed = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def fetchall(self):
        return self.rows


class _Conn:
    def __init__(self, cursor):
        self.cursor_obj = cursor
        self.committed = False
        self.closed = False

    def cursor(self):
        return self.cursor_obj

    def commit(self):
        self.committed = True

    def close(self):
        self.closed = True


def test_update_recipe_can_clear_badge_without_clearing_other_nullable_fields(monkeypatch):
    cur = _Cursor()
    conn = _Conn(cur)
    monkeypatch.setattr(recipe_routes, "get_db_conn", lambda: conn)

    result = recipe_routes.update_recipe(
        "recipe-1",
        recipe_routes.RecipeUpdate.model_validate({"badge": None}),
    )

    assert result == {"status": "updated"}
    sql, params = cur.executed[0]
    assert "badge = %s" in sql
    assert "description = %s" not in sql
    assert "image_url = %s" not in sql
    assert params == [None, "recipe-1"]
    assert conn.committed is True


def test_update_recipe_rejects_invalid_badge_with_400(monkeypatch):
    cur = _Cursor()
    conn = _Conn(cur)
    monkeypatch.setattr(recipe_routes, "get_db_conn", lambda: conn)

    with pytest.raises(HTTPException) as exc:
        recipe_routes.update_recipe(
            "recipe-1",
            recipe_routes.RecipeUpdate.model_validate({"badge": "invalid"}),
        )

    assert exc.value.status_code == 400
    assert "badge" in str(exc.value.detail)
    assert cur.executed == []
    assert conn.committed is False


def test_update_recipe_omits_badge_when_not_sent(monkeypatch):
    cur = _Cursor()
    conn = _Conn(cur)
    monkeypatch.setattr(recipe_routes, "get_db_conn", lambda: conn)

    result = recipe_routes.update_recipe(
        "recipe-1",
        recipe_routes.RecipeUpdate.model_validate({"description": "New public text"}),
    )

    assert result == {"status": "updated"}
    sql, params = cur.executed[0]
    assert "description = %s" in sql
    assert "badge = %s" not in sql
    assert params == ["New public text", "recipe-1"]


def test_public_menu_whitelist_includes_badge(monkeypatch):
    cur = _Cursor(
        rows=[
            (
                "recipe-1",
                "C027 เอ็นข้อไก่ทอด",
                89,
                "เมนูอาหาร",
                None,
                None,
                "best_seller",
            ),
            (
                "recipe-2",
                "D004 Pro (3ขวด) เบียร์สิงห์",
                259,
                "เครื่องดื่ม",
                None,
                None,
                None,
            ),
        ]
    )
    conn = _Conn(cur)
    monkeypatch.setattr(menu_public_routes, "get_db_conn", lambda: conn)

    response = menu_public_routes.get_public_menu()
    payload = json.loads(response.body)

    assert payload["items"][0]["badge"] == "best_seller"
    assert payload["items"][1]["badge"] is None
    sql, _params = cur.executed[0]
    assert "badge" in sql
