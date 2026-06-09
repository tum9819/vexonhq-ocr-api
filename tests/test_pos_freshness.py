"""
Offline unit tests for the POS sales-freshness signal (Reliability Phase).
Pure logic — no DB, no API key — runs in verify.ps1 [1b] / CI.
"""

from datetime import date

from pos_freshness import pos_freshness_signal

TODAY = date(2026, 6, 9)


def test_stale_9_days_alerts():
    stale, days, msg = pos_freshness_signal(date(2026, 5, 31), TODAY)
    assert stale is True
    assert days == 9
    assert msg is not None
    assert "31 พ.ค. 2026" in msg        # latest sales date, Thai, CE year
    assert "9" in msg                    # days behind surfaced
    assert "POS" in msg


def test_edge_exactly_2_days_no_alert():
    # threshold default 2 -> stale only when days_behind > 2, so exactly 2 is fresh
    stale, days, msg = pos_freshness_signal(date(2026, 6, 7), TODAY)
    assert stale is False
    assert days == 2
    assert msg is None


def test_3_days_alerts():
    stale, days, msg = pos_freshness_signal(date(2026, 6, 6), TODAY)
    assert stale is True
    assert days == 3
    assert msg is not None


def test_latest_none_no_alert():
    # no POS bills at all -> never alert, never crash
    stale, days, msg = pos_freshness_signal(None, TODAY)
    assert stale is False
    assert days is None
    assert msg is None


def test_today_equals_latest_no_alert():
    stale, days, msg = pos_freshness_signal(TODAY, TODAY)
    assert stale is False
    assert days == 0
    assert msg is None


def test_threshold_env_default_is_2():
    # 2 days behind is fresh, 3 is stale -> proves the default boundary is 2
    assert pos_freshness_signal(date(2026, 6, 7), TODAY)[0] is False
    assert pos_freshness_signal(date(2026, 6, 6), TODAY)[0] is True


def test_custom_threshold_widens_window():
    # with threshold=5, a 3-day lag stays silent; a 6-day lag alerts
    assert pos_freshness_signal(date(2026, 6, 6), TODAY, threshold_days=5)[0] is False
    assert pos_freshness_signal(date(2026, 6, 3), TODAY, threshold_days=5)[0] is True
