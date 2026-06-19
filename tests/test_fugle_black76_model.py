import pytest

from tools.fugle_live_tquote_demo import (
    black76_greeks,
    black76_price,
    implied_volatility,
    option_metrics,
    quick_vix_from_rows,
)


def test_black76_implied_volatility_round_trips_call_and_put():
    future_price = 47250
    strike = 47200
    years = 27 / 365
    rate = 0.015
    volatility = 0.32

    for side in ("call", "put"):
        price = black76_price(future_price, strike, years, rate, volatility, side)
        implied = implied_volatility(future_price, strike, years, rate, price, side)
        assert implied == pytest.approx(volatility, abs=0.001)


def test_black76_greeks_have_expected_directional_signs():
    future_price = 47250
    strike = 47200
    years = 27 / 365
    rate = 0.015
    volatility = 0.32

    call = black76_greeks(future_price, strike, years, rate, volatility, "call")
    put = black76_greeks(future_price, strike, years, rate, volatility, "put")

    assert call["delta"] > 0
    assert put["delta"] < 0
    assert call["gamma"] > 0
    assert put["gamma"] > 0
    assert call["vega"] > 0
    assert put["vega"] > 0


def test_option_metrics_uses_mid_iv_for_greeks():
    metrics = option_metrics(
        future_price=47250,
        strike=47200,
        years=27 / 365,
        rate=0.015,
        option_side="call",
        bid=1720,
        ask=1760,
    )

    assert metrics["bid_iv"] is not None
    assert metrics["ask_iv"] is not None
    assert metrics["mid_iv"] is not None
    assert metrics["bid_iv"] < metrics["mid_iv"] < metrics["ask_iv"]
    assert metrics["delta"] is not None

    expected = black76_greeks(
        future_price=47250,
        strike=47200,
        years=27 / 365,
        rate=0.015,
        volatility=metrics["mid_iv"],
        option_side="call",
    )
    bid_greeks = black76_greeks(
        future_price=47250,
        strike=47200,
        years=27 / 365,
        rate=0.015,
        volatility=metrics["bid_iv"],
        option_side="call",
    )
    ask_greeks = black76_greeks(
        future_price=47250,
        strike=47200,
        years=27 / 365,
        rate=0.015,
        volatility=metrics["ask_iv"],
        option_side="call",
    )

    for greek in ("delta", "gamma", "theta", "vega"):
        assert metrics[greek] == pytest.approx(expected[greek])
        assert abs(metrics[greek] - bid_greeks[greek]) > 1e-8
        assert abs(metrics[greek] - ask_greeks[greek]) > 1e-8


def test_quick_vix_uses_four_nearest_calls_and_puts():
    rows = []
    for strike in range(46800, 47700, 100):
        rows.append({
            "strike": strike,
            "call": {"mid_iv": 0.30 + abs(strike - 47200) / 100000},
            "put": {"mid_iv": 0.32 + abs(strike - 47200) / 100000},
        })

    vix = quick_vix_from_rows(rows, future_price=47250)

    assert vix is not None
    assert vix["call_count"] == 4
    assert vix["put_count"] == 4
    assert vix["sample_count"] == 8
    assert 30 < vix["value"] < 34
