"""
test_narrative_verify.py — offline checks for the P&L narrative hallucination
guard (audit F7). No API key / no DB: tests the pure _verify_narrative +
_known_values helpers in phase10_narrative_routes.
"""

from __future__ import annotations

import os
import sys

# Make the repo root importable when run via `pytest` from anywhere.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from phase10_narrative_routes import _verify_narrative, _known_values


CURRENT = {
    "total_income": 282000.0,
    "total_expense": 240000.0,
    "net": 42000.0,
    "txn_count": 660,
    "income_by_source": [{"source": "pos_sale", "amount": 250000.0},
                         {"source": "rider_income_grab", "amount": 32000.0}],
    "top_expenses": [{"name": "ค่าวัตถุดิบ", "amount": 120000.0},
                     {"name": "ค่าเช่า", "amount": 45000.0}],
}
PREV = {
    "total_income": 270000.0, "total_expense": 235000.0, "net": 35000.0,
    "txn_count": 640, "income_by_source": [], "top_expenses": [],
}


def test_all_matching_numbers_pass():
    known = _known_values(CURRENT, PREV)
    text = ("เดือนนี้รายรับ ฿282,000 รายจ่าย ฿240,000 กำไรสุทธิ ฿42,000 "
            "ยอด POS ฿250,000 ค่าวัตถุดิบ ฿120,000 จากทั้งหมด 660 รายการ")
    r = _verify_narrative(text, known)
    assert r["ok"], r
    assert r["unmatched"] == []
    assert r["checked"] >= 5


def test_planted_wrong_number_is_flagged():
    known = _known_values(CURRENT, PREV)
    # ฿199,999 matches nothing in known values
    text = "รายรับ ฿282,000 แต่กำไรสุทธิจริง ฿199,999 (พิมพ์ผิด)"
    r = _verify_narrative(text, known)
    assert not r["ok"]
    assert 199999.0 in r["unmatched"]
    assert 282000.0 not in r["unmatched"]


def test_years_and_percentages_not_flagged():
    known = _known_values(CURRENT, PREV)
    # 2026 = year (bare int in range), 15.5% = percentage → both skipped
    text = "ในปี 2026 margin อยู่ที่ 15.5% รายรับ ฿282,000"
    r = _verify_narrative(text, known)
    assert r["ok"], r
    assert 2026.0 not in r["unmatched"]
    assert 15.5 not in r["unmatched"]


def test_prev_month_values_accepted():
    known = _known_values(CURRENT, PREV)
    text = "เทียบเดือนก่อนรายรับ ฿270,000 กำไร ฿35,000"
    r = _verify_narrative(text, known)
    assert r["ok"], r


def test_tolerance_one_percent():
    known = _known_values(CURRENT, None)
    # 282,100 is within 1% of 282,000 → accepted (Claude rounding wiggle)
    assert _verify_narrative("฿282,100", known)["ok"]
    # 290,000 is >1% off → flagged
    assert not _verify_narrative("฿290,000", known)["ok"]


def test_empty_prose_is_ok():
    assert _verify_narrative("ไม่มีตัวเลขเลย", _known_values(CURRENT, None))["ok"]
