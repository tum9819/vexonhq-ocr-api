from datetime import date
import sys

sys.path.append(".")

import cashflow_routes as routes


def test_overdue_vendor_bills_bucket_as_day0_outflow():
    rows = [
        (date(2026, 6, 30), "Vendor A", 25841),
        (date(2026, 7, 15), "Vendor B", 100),
    ]

    ap_by_date = routes._bucket_standard_ap_rows(rows, today=date(2026, 7, 12))

    assert ap_by_date["2026-07-12"] == [
        {
            "vendor": "Vendor A",
            "amount": 25841.0,
            "due_date": "2026-06-30",
            "overdue": True,
        }
    ]
    assert ap_by_date["2026-07-15"][0]["overdue"] is False


def test_cashflow_ap_sql_uses_vendor_bills_standard_definition():
    sql = " ".join(routes.STANDARD_AP_QUERY_SQL.split())

    assert "public.vendor_bills" in sql
    assert "payment_status = 'unpaid'" in sql
    assert "review_status <> 'rejected'" in sql
    assert "ar_ap_entries" not in sql


def test_cashflow_health_warns_when_any_ap_is_overdue():
    assert routes._cashflow_health(net_position=100_000, ap_overdue=1) == "warning"
    assert routes._cashflow_health(net_position=100_000, ap_overdue=0) == "good"
    assert routes._cashflow_health(net_position=-1, ap_overdue=0) == "warning"
