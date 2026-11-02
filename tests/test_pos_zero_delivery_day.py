"""
Grab / LINE MAN exports with no orders (header row only) import as 0 rows.

Regression (2026-11-02): POS daily run for 2026-11-01 got header-only files
from both platforms; the parsers raised 400 "No valid ... rows found", the
10-file upload could not reconcile and the pending manifest blocked the flow.
Files that DO have rows but none usable must still be rejected.

Offline, no DB. Run: pytest tests/test_pos_zero_delivery_day.py -v
"""
from __future__ import annotations

import io
import os

os.environ.setdefault("DATABASE_URL", "postgresql://u:p@localhost:5432/d")
os.environ.setdefault("OPENAI_API_KEY", "x")

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pandas as pd
import pytest
from fastapi import HTTPException

import pos_import as p

# Real Grab Transaction header (2026-11-01 export), trimmed to what matters
# plus the columns detection and parsing read.
GRAB_HEADER = (
    "ชื่อร้าน,Merchant ID,วันที่สร้าง,หมวดหมู่,Transaction ID,ยอด,"
    "ส่วนลด (ออกโดยร้าน),ค่าคอมมิชชันแพลตฟอร์ม,ทั้งหมด"
)
LINEMAN_COLS = ["time", "sales", "orders", "avgBasketSize"]


def _grab(csv_text: str):
    data = ("﻿" + csv_text).encode("utf-8")
    df, rtype = p.read_and_detect(data, "Transaction_Store_2026-11-01.csv")
    assert rtype == "grab_transaction"
    return p.PARSERS[rtype](df)


def _lineman(rows: list[list]):
    buf = io.BytesIO()
    pd.DataFrame(rows, columns=LINEMAN_COLS).to_excel(buf, index=False, sheet_name="LINEMAN")
    df, rtype = p.read_and_detect(buf.getvalue(), "lineman.xlsx")
    assert rtype == "lineman_daily"
    return p.PARSERS[rtype](df)


def test_grab_header_only_is_zero_orders():
    out = _grab(GRAB_HEADER + "\n")
    assert out == {"period_start": None, "period_end": None, "tables": {"rider_deliveries": []}}


def test_lineman_header_only_is_zero_orders():
    out = _lineman([])
    assert out == {"period_start": None, "period_end": None, "tables": {"rider_deliveries": []}}


def test_each_call_returns_a_fresh_result():
    a = _lineman([])
    a["tables"]["rider_deliveries"].append({"x": 1})
    assert _lineman([])["tables"]["rider_deliveries"] == []


def test_grab_rows_without_any_payment_still_400():
    with pytest.raises(HTTPException) as e:
        _grab(GRAB_HEADER + "\nMara,1,01 Nov 2026 12:00 PM,คืนเงิน,T1,100,0,0,100\n")
    assert e.value.status_code == 400


def test_lineman_rows_without_sales_still_400():
    with pytest.raises(HTTPException) as e:
        _lineman([["2026-11-01", 0, 0, 0]])
    assert e.value.status_code == 400


def test_lineman_normal_day_unchanged():
    out = _lineman([["2026-11-02", 500, 2, 250]])
    rows = out["tables"]["rider_deliveries"]
    assert len(rows) == 1 and rows[0]["gross_sales"] == 500 and rows[0]["order_count"] == 2
    assert str(out["period_start"]) == "2026-11-02"


def test_grab_header_missing_parsed_columns_still_400():
    # Review 2026-11-02: passes CSV detection (Transaction ID + commission) but
    # lacks ยอด / ทั้งหมด / หมวดหมู่ — a broken export, not a 0-order day.
    with pytest.raises(HTTPException) as e:
        _grab("วันที่สร้าง,Transaction ID,ค่าคอมมิชชันแพลตฟอร์ม\n")
    assert e.value.status_code == 400


def test_empty_frame_without_the_date_column_is_not_accepted():
    # Guard only accepts a real header; a frame missing the date column falls
    # through to the old 400.
    with pytest.raises(HTTPException):
        p.parse_lineman_daily(pd.DataFrame(columns=["sales", "orders"]))
    with pytest.raises(HTTPException):
        p.parse_grab_transaction(pd.DataFrame(columns=["ยอด"]))
