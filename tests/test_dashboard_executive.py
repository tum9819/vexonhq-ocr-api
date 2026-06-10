"""
Offline unit tests for the Executive Dashboard pure logic — freshness +
card assembly + reconciliation. No DB / network / key needed.
(admin-only gating is covered separately in tests/test_admin_gate.py.)
"""

from datetime import date

from phase2_routes import _card_freshness, _build_executive_cards, _min_date

TODAY = date(2026, 6, 9)


# ── freshness helper ────────────────────────────────────────────────
def test_freshness_today_is_fresh():
    assert _card_freshness(TODAY, TODAY, 2) is True


def test_freshness_edge_exactly_threshold_is_fresh():
    assert _card_freshness(date(2026, 6, 7), TODAY, 2) is True   # 2 days, inclusive


def test_freshness_beyond_threshold_is_stale():
    assert _card_freshness(date(2026, 6, 6), TODAY, 2) is False  # 3 days


def test_freshness_none_is_not_fresh():
    assert _card_freshness(None, TODAY, 2) is False


def test_stock_uses_wider_window():
    # 5 days old: stale at sales threshold (2) but fresh at stock threshold (7)
    five_ago = date(2026, 6, 4)
    assert _card_freshness(five_ago, TODAY, 2) is False
    assert _card_freshness(five_ago, TODAY, 7) is True


def test_min_date_skips_none():
    assert _min_date(date(2026, 6, 8), None, date(2026, 6, 1)) == date(2026, 6, 1)
    assert _min_date(None, None) is None


# ── card assembly + reconciliation ──────────────────────────────────
SUMM = {  # shape of _summarize_month output
    "sales_net": 69082.06, "expense_total": 16528.0,
    "gross_profit": 52554.06, "gross_margin_pct": 76.07,
    "sales_bill_count": 0, "expense_bill_count": 0,
}
METRICS = {
    "sales_as_of": date(2026, 6, 8), "daybook_as_of": date(2026, 6, 8),
    "sales_30d": 289322.29, "bills_pending": 0,
    "ap_count": 36, "ap_total": 298861.73, "ap_overdue": 75075.02,
    "stock_as_of": date(2026, 6, 5), "low_stock_count": 64,
}


def _cards_by_key(cards):
    return {c["key"]: c for c in cards}


def test_six_cards_in_order():
    cards = _build_executive_cards(SUMM, METRICS, TODAY)
    assert [c["key"] for c in cards] == [
        "sales_mtd", "cost_mtd", "profit_est",
        "bills_pending_review", "ap_outstanding", "stock",
    ]


def test_sales_card_reconciles_with_summarize_month():
    # the whole reconcile-with-/dashboard/overview guarantee: the sales card value
    # IS _summarize_month's sales_net (same source overview's headline uses).
    cards = _cards_by_key(_build_executive_cards(SUMM, METRICS, TODAY))
    assert cards["sales_mtd"]["value"] == SUMM["sales_net"]
    assert cards["cost_mtd"]["value"] == SUMM["expense_total"]
    assert cards["profit_est"]["value"] == SUMM["gross_profit"]


def test_sales_card_has_30day_secondary():
    cards = _cards_by_key(_build_executive_cards(SUMM, METRICS, TODAY))
    sec = cards["sales_mtd"]["secondary"]
    assert sec["value"] == METRICS["sales_30d"]
    assert cards["sales_mtd"]["basis"] == "cash"


def test_ap_card_surfaces_overdue():
    cards = _cards_by_key(_build_executive_cards(SUMM, METRICS, TODAY))
    ap = cards["ap_outstanding"]
    assert ap["value"] == 298861.73 and ap["count"] == 36
    assert ap["alert"]["value"] == 75075.02
    assert ap["as_of"] == "live" and ap["fresh"] is True


def test_freshness_reflected_per_card_when_stale():
    # POS 9 days behind -> sales/cost/profit stale; stock 4 days -> fresh; AP live -> fresh
    stale_metrics = dict(METRICS, sales_as_of=date(2026, 5, 31),
                         daybook_as_of=date(2026, 5, 31), stock_as_of=date(2026, 6, 5))
    cards = _cards_by_key(_build_executive_cards(SUMM, stale_metrics, TODAY))
    assert cards["sales_mtd"]["fresh"] is False
    assert cards["cost_mtd"]["fresh"] is False
    assert cards["profit_est"]["fresh"] is False
    assert cards["stock"]["fresh"] is True          # 4 days <= 7
    assert cards["ap_outstanding"]["fresh"] is True  # live
    assert cards["stock"]["low_stock_count"] == 64


# ── P2: latest-day vs prior-day sales (daily) ───────────────────────
def test_daily_two_days_change_pct():
    m = dict(METRICS, latest_day_sales=12500.0, prev_day=date(2026, 6, 7), prev_day_sales=14200.0)
    daily = _cards_by_key(_build_executive_cards(SUMM, m, TODAY))["sales_mtd"]["daily"]
    assert daily["latest"] == {"date": "2026-06-08", "value": 12500.0}
    assert daily["prev"] == {"date": "2026-06-07", "value": 14200.0}
    assert daily["change_pct"] == round((12500.0 - 14200.0) / 14200.0 * 100, 1)


def test_daily_one_day_prev_null():
    m = dict(METRICS, latest_day_sales=12500.0, prev_day=None, prev_day_sales=0.0)
    daily = _cards_by_key(_build_executive_cards(SUMM, m, TODAY))["sales_mtd"]["daily"]
    assert daily["latest"]["value"] == 12500.0
    assert daily["prev"] is None and daily["change_pct"] is None


def test_daily_prev_zero_change_null():
    m = dict(METRICS, latest_day_sales=12500.0, prev_day=date(2026, 6, 7), prev_day_sales=0.0)
    daily = _cards_by_key(_build_executive_cards(SUMM, m, TODAY))["sales_mtd"]["daily"]
    assert daily["prev"]["value"] == 0.0 and daily["change_pct"] is None


def test_daily_absent_when_no_sales():
    m = dict(METRICS, sales_as_of=None, latest_day_sales=0.0, prev_day=None, prev_day_sales=0.0)
    card = _cards_by_key(_build_executive_cards(SUMM, m, TODAY))["sales_mtd"]
    assert "daily" not in card


# ── P3: AP due in the next 7 days (due_soon) ────────────────────────
def test_ap_due_soon_present():
    m = dict(METRICS, ap_due_7d=125000.0, ap_due_7d_count=8)
    ap = _cards_by_key(_build_executive_cards(SUMM, m, TODAY))["ap_outstanding"]
    assert ap["due_soon"] == {"days": 7, "value": 125000.0, "count": 8}


def test_ap_due_soon_zero_when_none():
    m = dict(METRICS, ap_due_7d=0.0, ap_due_7d_count=0)
    ap = _cards_by_key(_build_executive_cards(SUMM, m, TODAY))["ap_outstanding"]
    assert ap["due_soon"]["value"] == 0.0 and ap["due_soon"]["count"] == 0
