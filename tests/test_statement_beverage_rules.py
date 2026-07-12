from pathlib import Path


def test_slip_beverage_keyword_rules_are_seeded():
    sql = Path("migrations/2026_07_12_fa003_slip_beverage_rules.sql").read_text(encoding="utf-8")
    for keyword in ["เหล้า", "เบียร์", "beer", "singh", "chang"]:
        assert keyword in sql
    assert "beverage_raw" in sql
    assert "vendor_purchase" in sql
