import os
from datetime import date

os.environ.setdefault("DATABASE_URL", "postgresql://u:p@localhost:5432/d")

import phase12_bank_statement_routes as bank_routes


LEGACY_DELIVERY_RULES = [
    {
        "rule_type": "keyword",
        "match_value": "LINE PAY",
        "direction": "income",
        "category_code": "delivery_income",
        "source_type": "rider_income_lineman",
        "priority": 100,
    },
    {
        "rule_type": "keyword",
        "match_value": "แกร็บ",
        "direction": "income",
        "category_code": "delivery_income",
        "source_type": "rider_income_grab",
        "priority": 100,
    },
]


def _income_row(description: str, credit: float = 100.0) -> dict:
    return {
        "txn_date": date(2026, 6, 1),
        "description": description,
        "debit": 0.0,
        "credit": credit,
        "balance": 1000.0,
    }


def test_statement_page_boilerplate_is_not_appended_as_transaction_detail():
    assert bank_routes._is_statement_boilerplate_continuation(
        "ออกโดย K PLUS หน้าที่ (PAGE/OF) 2/5 ที่ DD.048 : N26070111142531819688I/2569"
    )
    assert bank_routes._is_statement_boilerplate_continuation(
        "เวลา/ ยอดคงเหลือ วันที่ รายการ ถอนเงิน / ฝากเงิน ช่องทาง รายละเอียด วันที่มีผล (บาท)"
    )
    assert not bank_routes._is_statement_boilerplate_continuation(
        "รหัสอ้างอิง NBGW3533 เคชัน /เงินโอน"
    )


def test_statement_detail_cleaner_removes_kbank_page_fragments():
    cleaned = bank_routes._clean_statement_detail(
        "ตู้เติมเงิน / โมบาย แอปพลิ จาก X3812 บจก. แกร็บแท็กซี่ ++ เคชัน /เงินโอน "
        ")52-30( )1.V-AS_AC-207MF( FDPBK ที่ DD.048 : N26070111142531819688I/2569 "
        "2/5(0391) วันที่มีผล (บาท)"
    )

    assert "แกร็บแท็กซี่" in cleaned
    assert "V-AS_AC" not in cleaned
    assert "FDPBK" not in cleaned
    assert "DD.048" not in cleaned
    assert "วันที่มีผล" not in cleaned

    cleaned_partial = bank_routes._clean_statement_detail(
        "ตู้เติมเงิน / โมบาย แอปพลิ รหัสอ้างอิง NBGW3533 เคชัน /เงินโอน )1.V-AS_AC-207MF( ที่ 5/5(0391)"
    )
    assert "V-AS_AC" not in cleaned_partial
    assert ")1." not in cleaned_partial
    assert "5/5(0391)" not in cleaned_partial


def test_line_pay_inflow_overrides_legacy_lineman_delivery_rule():
    result = bank_routes._classify(
        _income_row("Internet/Mobile BAY จาก BAY X1761 LINE PAY (THAILAND)"),
        LEGACY_DELIVERY_RULES,
    )

    assert result["source_type"] == "payment_gateway_payout"
    assert result["category_code"] == "payment_gateway"
    assert result["match_status"] == "auto"


def test_thai_grab_inflow_overrides_legacy_rider_income_rule():
    result = bank_routes._classify(
        _income_row("ตู้เติมเงิน / โมบาย แอปพลิ จาก X3812 บจก. แกร็บแท็กซี่ เคชัน /เงินโอน"),
        LEGACY_DELIVERY_RULES,
    )

    assert result["source_type"] == "grab_payout"
    assert result["category_code"] == "delivery_grab"
    assert result["match_status"] == "auto"


def test_reclass_dry_run_reports_only_rows_that_would_change():
    rows = [
        {
            "id": "linepay-1",
            "txn_date": date(2026, 6, 8),
            "description": "Internet/Mobile BAY จาก BAY X1761 LINE PAY (THAILAND)",
            "debit": 0.0,
            "credit": 897.0,
            "balance": 1000.0,
            "category_code": "delivery_income",
            "source_type": "rider_income_lineman",
            "match_status": "auto",
        },
        {
            "id": "grab-1",
            "txn_date": date(2026, 6, 2),
            "description": "จาก X3812 บจก. แกร็บแท็กซี่ เคชัน /เงินโอน",
            "debit": 0.0,
            "credit": 442.43,
            "balance": 2000.0,
            "category_code": "delivery_income",
            "source_type": "rider_income_grab",
            "match_status": "auto",
        },
        {
            "id": "already-ok",
            "txn_date": date(2026, 6, 3),
            "description": "จาก X3812 บจก. แกร็บแท็กซี่ เคชัน /เงินโอน",
            "debit": 0.0,
            "credit": 540.04,
            "balance": 2500.0,
            "category_code": "delivery_grab",
            "source_type": "grab_payout",
            "match_status": "auto",
        },
    ]

    result = bank_routes._build_reclass_dry_run(rows, LEGACY_DELIVERY_RULES)

    assert result["candidate_count"] == 2
    assert result["candidate_total_credit"] == 1339.43
    assert result["months"] == {
        "2026-06": {
            "candidate_count": 2,
            "candidate_total_credit": 1339.43,
            "by_suggested_source": {
                "grab_payout": {"count": 1, "credit": 442.43},
                "payment_gateway_payout": {"count": 1, "credit": 897.0},
            },
        }
    }
    assert [row["id"] for row in result["candidates"]] == ["linepay-1", "grab-1"]
    assert result["candidates"][0]["current_source_type"] == "rider_income_lineman"
    assert result["candidates"][0]["suggested_source_type"] == "payment_gateway_payout"
