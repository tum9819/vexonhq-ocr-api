"""
test_order_backtest.py — offline checks for the DOW order-advice backtest scorer
(audit F8). No DB: tests the pure backtest_dow function with synthetic series.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from inventory_forecast_routes import backtest_dow


def _weeks(pattern: dict[int, float], n_weeks: int, start_day: int = 0) -> list[dict]:
    """Build n_weeks of daily rows from a {dow: sales} pattern."""
    rows = []
    day = 0
    for _ in range(n_weeks):
        for dow in range(7):
            rows.append({"date": f"d{day}", "dow": dow, "sales": pattern.get(dow, 0.0)})
            day += 1
    return rows


def test_clean_seasonal_series_scores_well():
    # Fri(5) + Sat(6) strong, rest modest — stable every week.
    pattern = {0: 100, 1: 90, 2: 90, 3: 100, 4: 120, 5: 300, 6: 320}
    train = _weeks(pattern, 8)
    test = _weeks(pattern, 4)
    r = backtest_dow(train, test)
    assert r["mape_pct"] is not None
    assert r["mape_pct"] <= 5.0           # identical pattern → near-zero error
    assert r["best_day_hit"] == 2          # Fri+Sat top-2 in both
    assert r["accuracy_pct"] >= 95.0


def test_flat_series_no_crash_defined_output():
    pattern = {d: 100.0 for d in range(7)}
    r = backtest_dow(_weeks(pattern, 6), _weeks(pattern, 3))
    assert r["mape_pct"] is not None
    assert r["mape_pct"] <= 5.0
    assert isinstance(r["best_day_hit"], int)


def test_empty_test_is_graceful():
    r = backtest_dow(_weeks({0: 100}, 4), [])
    assert r["mape_pct"] is None
    assert "ไม่พอ" in r["verdict_th"]


def test_empty_train_is_graceful():
    r = backtest_dow([], _weeks({0: 100}, 2))
    assert r["mape_pct"] is None


def test_garbage_rows_skipped():
    train = [{"date": "d", "dow": "x", "sales": "y"}, {"date": "d", "dow": 5, "sales": 200}]
    test = [{"date": "d", "dow": 5, "sales": 200}, {"bad": 1}]
    r = backtest_dow(train, test)
    assert r["mape_pct"] is not None  # the one valid pair drives it, no crash


def test_zero_actual_days_excluded_from_mape():
    # test day with sales=0 must not blow up MAPE (div-by-zero) — it's skipped
    train = _weeks({5: 200, 6: 220}, 4)
    test = [{"date": "d", "dow": 5, "sales": 0.0}, {"date": "e", "dow": 6, "sales": 220.0}]
    r = backtest_dow(train, test)
    assert r["mape_pct"] is not None
