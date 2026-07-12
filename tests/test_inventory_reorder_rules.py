import sys

sys.path.append(".")

import inventory_forecast_routes as routes


def test_reorder_excludes_menu_tags():
    assert routes._is_orderable_inventory_tag("MENU") is False
    assert routes._is_orderable_inventory_tag("menu") is False
    assert routes._is_orderable_inventory_tag("วัตถุดิบ") is True


def test_negative_stock_is_clamped_only_for_order_calculation():
    item = routes._build_reorder_item(
        name="เนื้อ",
        tag="วัตถุดิบ",
        qty_current=-90,
        qty_max=100,
        price=10,
        unit="ชิ้น",
    )

    assert item["qty_current"] == -90.0
    assert item["qty_to_order"] == 100.0
    assert item["est_cost"] == 1000.0


def test_uncategorized_default_max_300_is_flagged_not_costed():
    item = routes._build_reorder_item(
        name="ปูอัด",
        tag="ไม่ระบุ",
        qty_current=-5,
        qty_max=300,
        price=10,
        unit="ชิ้น",
    )

    assert item["max_needs_review"] is True
    assert item["reorder_note"] == "MAX ยังไม่ได้ตั้ง — ตรวจสอบ"
    assert item["qty_to_order"] == 0.0
    assert item["est_cost"] == 0.0
    assert item["cost_included"] is False


def test_reorder_summary_excludes_max_review_items_from_estimate():
    valid = routes._build_reorder_item("น้ำ", "เครื่องดื่ม", 2, 12, 5, "ขวด")
    review = routes._build_reorder_item("โบโลน่า", "???????", -5, 300, 10, "ชิ้น")

    summary = routes._summarize_reorder_items([valid, review])

    assert summary["total_items"] == 2
    assert summary["needs_max_review"] == 1
    assert summary["est_total_cost"] == valid["est_cost"]
