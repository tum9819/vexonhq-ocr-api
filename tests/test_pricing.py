import pytest

from pricing import round_price, compute_channel, compute_reverse, LOW_MARGIN_PCT


def test_round_price_modes():
    assert round_price(140.00, "9") == 149      # 0 -> next ...9
    assert round_price(156.67, "9") == 159
    assert round_price(149.0, "9") == 149       # already ...9
    assert round_price(156.67, "0") == 160      # up to nearest 10
    assert round_price(156.67, "5") == 160      # up to nearest 5
    assert round_price(156.67, "none") == 157   # ceil to whole baht
    assert round_price(0, "9") == 0


def test_round_price_invalid_mode():
    with pytest.raises(ValueError):
        round_price(100, "bogus")


def test_compute_channel_dine_in_cost_mode():
    r = compute_channel(food_cost=42.0, packaging_cost=0, commission_pct=0,
                        target_pct=30, mode="cost", rounding="9")
    assert r["channel_cost"] == 42.0
    assert r["suggested_price"] == 149
    assert r["net_gp_pct"] == 71.8
    assert r["low_margin"] is False


def test_compute_channel_delivery_commission_eats_margin():
    r = compute_channel(food_cost=42.0, packaging_cost=5, commission_pct=32,
                        target_pct=30, mode="cost", rounding="9")
    assert r["suggested_price"] == 159          # (42+5)/0.30=156.67 -> 159
    assert r["net_gp_pct"] == 38.4              # (159*0.68-47)/159
    assert r["low_margin"] is True              # < 40


def test_compute_channel_gp_mode_equals_cost_complement_when_no_commission():
    a = compute_channel(42.0, 0, 0, target_pct=30, mode="cost", rounding="none")
    b = compute_channel(42.0, 0, 0, target_pct=70, mode="gp", rounding="none")
    assert a["suggested_price"] == b["suggested_price"]


def test_compute_channel_zero_cost_is_safe():
    r = compute_channel(0, 0, 0, target_pct=30, mode="cost", rounding="9")
    assert r["suggested_price"] == 0
    assert r["net_gp_pct"] == 0.0


def test_compute_channel_invalid_target_raises():
    with pytest.raises(ValueError):
        compute_channel(42.0, 0, 0, target_pct=0, mode="cost", rounding="9")
    with pytest.raises(ValueError):
        compute_channel(42.0, 0, 0, target_pct=100, mode="gp", rounding="9")


def test_compute_reverse():
    r = compute_reverse(food_cost=42.0, packaging_cost=5, commission_pct=32, price=159)
    assert r["cost_pct"] == 29.6                # 47/159
    assert r["net_gp_pct"] == 38.4
    assert r["low_margin"] is True


def test_low_margin_constant():
    assert LOW_MARGIN_PCT == 40.0
