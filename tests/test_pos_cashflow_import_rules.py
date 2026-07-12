import os

import pandas as pd

os.environ.setdefault("DATABASE_URL", "postgresql://u:p@localhost:5432/d")

from pos_import import parse_cashflow_detail


def _cashflow_frame(descriptions: list[str]) -> pd.DataFrame:
    rows = []
    for idx, description in enumerate(descriptions, start=1):
        rows.append({
            "เวลา": f"12/07/2026 10:{idx:02d}",
            "รหัสถาดเก็บเงิน": "DRAWER-1",
            "รายละเอียด": description,
            "ประเภท": "เงินออก",
            "จำนวน": -100,
            "สาขา": "มาลาทวีวัฒนา",
        })
    return pd.DataFrame(rows)


def _categories_for(descriptions: list[str]) -> list[tuple[str | None, str]]:
    result = parse_cashflow_detail(_cashflow_frame(descriptions))
    return [
        (row["category_code"], row["ai_cat_status"])
        for row in result["tables"]["pos_cashflow_entries"]
    ]


def test_import_rules_exact_i_v_g_are_lower_trimmed():
    assert _categories_for([" I ", "v", " G "]) == [
        ("raw_beverage", "rule"),
        ("raw_veggies", "rule"),
        ("raw_oil_gas", "rule"),
    ]


def test_import_rules_keyword_categories_and_packaging_priority():
    assert _categories_for([
        "ใส้กรอกแดง",
        "ค่าน้ำซอส",
        "แก้วน้ำจิ้ม 2 ออนซ์",
        "ถ้วยน้ำจิ้ม",
    ]) == [
        ("raw_meat", "rule"),
        ("raw_seasoning", "rule"),
        ("packaging", "rule"),
        ("raw_seasoning", "rule"),
    ]


def test_import_rules_leave_unknown_pending():
    assert _categories_for(["ของใช้ร้าน"]) == [(None, "pending")]
