"""
M0 — /search category-mapping fix (regression).

The AI prompt emitted category codes that do not exist in v_daybook, so
searches returned almost nothing:
  - 'beverage_raw'  -> real code is 'raw_beverage' (182 rows vs 2)
  - 'reimbursement' -> real code is 'refund_received' (no 'reimbursement' code)
  - 'food_raw' alone misses raw_meat / raw_veggies / raw_seasoning / raw_oil_gas

These are pure unit tests over `_expand_category` + `_build_sql` — no AI, no DB.
"""

from phase11_search_routes import _expand_category, _build_sql, SearchFilter


def test_beverage_raw_maps_to_real_db_code():
    # AI emits 'beverage_raw' but the real v_daybook code is 'raw_beverage'.
    assert _expand_category("beverage_raw") == ["raw_beverage"]


def test_reimbursement_maps_to_refund_received():
    # 'reimbursement' does not exist in v_daybook; the real code is 'refund_received'.
    assert _expand_category("reimbursement") == ["refund_received"]


def test_food_raw_expands_to_food_material_group():
    # "อาหาร/วัตถุดิบ" should cover all raw food categories, not just food_raw (33 rows).
    assert set(_expand_category("food_raw")) == {
        "food_raw", "raw_meat", "raw_veggies", "raw_seasoning", "raw_oil_gas",
    }


def test_already_correct_code_passes_through():
    assert _expand_category("raw_beverage") == ["raw_beverage"]
    assert _expand_category("rent") == ["rent"]
    assert _expand_category("staff_salary") == ["staff_salary"]


def test_empty_returns_empty_list():
    assert _expand_category(None) == []
    assert _expand_category("") == []


# ── _build_sql: category filter uses the expanded real codes via IN ──────────
def test_build_sql_expands_category_to_in_clause():
    sql, params = _build_sql(SearchFilter(category_code="beverage_raw"), limit=50)
    assert "d.category_code IN (" in sql
    assert "raw_beverage" in params
    assert "beverage_raw" not in params  # the non-existent code must not be queried


def test_build_sql_food_group_binds_all_codes():
    sql, params = _build_sql(SearchFilter(category_code="food_raw"), limit=50)
    for c in ["food_raw", "raw_meat", "raw_veggies", "raw_seasoning", "raw_oil_gas"]:
        assert c in params


def test_build_sql_no_category_has_no_category_condition():
    # No category filter ⇒ no category WHERE predicate (SELECT/JOIN still
    # reference category_code, which is fine).
    sql, params = _build_sql(SearchFilter(), limit=10)
    assert "d.category_code IN" not in sql
    assert "d.category_code =" not in sql
    assert params == [10]  # only the limit


def test_build_sql_other_filters_unchanged_regression():
    f = SearchFilter(date_from="2026-05-01", date_to="2026-05-31",
                     direction="expense", source="vendor_bill")
    sql, params = _build_sql(f, limit=20)
    assert "d.entry_date >= %s" in sql and "d.entry_date <= %s" in sql
    assert "d.direction = %s" in sql and "d.source = %s" in sql
    assert params[:4] == ["2026-05-01", "2026-05-31", "expense", "vendor_bill"]
    assert params[-1] == 20
