"""Pure selling-price calculator — no DB, no I/O. Unit-tested in tests/test_pricing.py.

channel_cost = food_cost + packaging_cost
Forward:  price = channel_cost / (target/100)            [mode=cost]
          price = channel_cost / (1 - target/100)        [mode=gp]
Net GP after platform commission (delivery):
          net_gp_baht = price * (1 - commission) - channel_cost
All rounding rounds UP, to protect margin.
"""
from __future__ import annotations

import math

LOW_MARGIN_PCT = 40.0


def round_price(price_raw: float, mode: str) -> int:
    """Round UP to a charm-friendly integer. mode in {'9','0','5','none'}."""
    if price_raw <= 0:
        return 0
    if mode == "none":
        return math.ceil(price_raw)
    if mode == "0":
        return math.ceil(price_raw / 10) * 10
    if mode == "5":
        return math.ceil(price_raw / 5) * 5
    if mode == "9":
        base = math.ceil(price_raw)
        return base + (9 - (base % 10)) % 10
    raise ValueError(f"unknown rounding mode: {mode}")


def compute_channel(food_cost, packaging_cost, commission_pct,
                    target_pct, mode, rounding) -> dict:
    """Forward calc for one channel. Returns suggested price + net GP after commission."""
    channel_cost = float(food_cost or 0) + float(packaging_cost or 0)
    comm = float(commission_pct or 0) / 100.0
    t = float(target_pct) / 100.0
    if mode == "cost":
        if not 0 < t < 1:
            raise ValueError("cost target_pct must be in (0,100)")
        price_raw = channel_cost / t
    elif mode == "gp":
        if not 0 < t < 1:
            raise ValueError("gp target_pct must be in (0,100)")
        price_raw = channel_cost / (1.0 - t)
    else:
        raise ValueError(f"unknown mode: {mode}")

    suggested = round_price(price_raw, rounding)
    if suggested > 0:
        net_gp_baht = suggested * (1 - comm) - channel_cost
        net_gp_pct = net_gp_baht / suggested * 100
    else:
        net_gp_baht = 0.0
        net_gp_pct = 0.0
    return {
        "channel_cost": round(channel_cost, 2),
        "price_raw": round(price_raw, 2),
        "suggested_price": suggested,
        "net_gp_baht": round(net_gp_baht, 2),
        "net_gp_pct": round(net_gp_pct, 1),
        "low_margin": net_gp_pct < LOW_MARGIN_PCT,
    }


def compute_reverse(food_cost, packaging_cost, commission_pct, price) -> dict:
    """Reverse calc: given an existing price, what cost% / net GP% does it yield."""
    channel_cost = float(food_cost or 0) + float(packaging_cost or 0)
    comm = float(commission_pct or 0) / 100.0
    price = float(price or 0)
    if price <= 0:
        return {"cost_pct": 0.0, "net_gp_pct": 0.0, "low_margin": True}
    cost_pct = channel_cost / price * 100
    net_gp_pct = (price * (1 - comm) - channel_cost) / price * 100
    return {
        "cost_pct": round(cost_pct, 1),
        "net_gp_pct": round(net_gp_pct, 1),
        "low_margin": net_gp_pct < LOW_MARGIN_PCT,
    }
