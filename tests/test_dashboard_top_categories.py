from phase2_routes import _build_top_categories


def test_uncategorized_bucket_is_appended_and_reconciles_to_expense_total():
    rows = [
        ("food_cost", "วัตถุดิบอาหาร", 200),
        ("utilities", "ค่าสาธารณูปโภค", 100),
    ]

    result = _build_top_categories(rows, uncategorized_spent=50, expense_total=350)

    assert [row["category_code"] for row in result] == [
        "food_cost",
        "utilities",
        "uncategorized",
    ]
    assert result[-1] == {
        "category_code": "uncategorized",
        "name_th": "ไม่ระบุหมวด",
        "spent": 50.0,
        "pct": 14.3,
    }
    assert sum(row["spent"] for row in result) == 350
    assert result[0]["pct"] == 57.1


def test_zero_uncategorized_spend_does_not_add_an_empty_bucket():
    result = _build_top_categories(
        [("food_cost", "วัตถุดิบอาหาร", 200)],
        uncategorized_spent=0,
        expense_total=200,
    )

    assert [row["category_code"] for row in result] == ["food_cost"]
    assert result[0]["pct"] == 100.0
