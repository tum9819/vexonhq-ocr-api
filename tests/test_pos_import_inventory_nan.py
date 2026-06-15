"""Regression test for F-STK-1: inventory importer leaked pandas NaN as the
literal string 'nan' into pos_inventory_items.material_code / tag / unit.

Root cause: float('nan') is truthy, so `str(r.get(col) or "").strip() or None`
returned 'nan' instead of None for blank FoodStory cells. parse_inventory must
use the NaN-safe string cleaner (strip_html) like the other parsers.
"""
from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd

from pos_import import parse_inventory


def _items(df: pd.DataFrame) -> list[dict]:
    result = parse_inventory(df, datetime(2026, 6, 15, 0, 0, 0))
    return result["tables"]["_inventory_items"]


def test_blank_material_code_tag_unit_become_none_not_nan_string():
    # A FoodStory inventory export row with blank รหัสวัตถุดิบ / ป้ายกำกับ / หน่วย:
    # pandas reads the empty cells as float NaN. The importer must store SQL NULL.
    df = pd.DataFrame([
        {"ชื่อ": "หมูสามชั้น", "รหัสวัตถุดิบ": np.nan, "ป้ายกำกับ": np.nan,
         "จำนวนของในสต็อก": 5, "ราคาต่อหน่วย": 120, "หน่วย": np.nan,
         "มูลค่าสินค้าในสต๊อก": 600},
    ])

    items = _items(df)

    assert len(items) == 1
    assert items[0]["material_code"] is None
    assert items[0]["tag"] is None
    assert items[0]["unit"] is None


def test_row_with_blank_name_is_skipped_not_named_nan():
    # ชื่อ = NaN is not a real item; it must be skipped, never stored as 'nan'.
    df = pd.DataFrame([
        {"ชื่อ": np.nan, "รหัสวัตถุดิบ": "A1", "จำนวนของในสต็อก": 1,
         "ราคาต่อหน่วย": 10, "มูลค่าสินค้าในสต๊อก": 10},
    ])

    assert _items(df) == []


def test_valid_string_fields_are_preserved():
    df = pd.DataFrame([
        {"ชื่อ": "พริกแห้ง", "รหัสวัตถุดิบ": "A14", "ป้ายกำกับ": "วัตถุดิบ",
         "จำนวนของในสต็อก": 3, "ราคาต่อหน่วย": 50, "หน่วย": "กก.",
         "มูลค่าสินค้าในสต๊อก": 150},
    ])

    items = _items(df)

    assert items[0]["material_code"] == "A14"
    assert items[0]["tag"] == "วัตถุดิบ"
    assert items[0]["unit"] == "กก."
