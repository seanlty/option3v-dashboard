"""Generate a static quote_snapshot and position_valuation demo payload.

This is intentionally not imported by production code. It creates a concrete
JSON contract that can be reviewed before wiring Fugle live, cache, DB, or UI
changes into the dashboard.
"""

from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_VERSION = "0.1.0"
POINT_VALUE_TXO = 50
RISK_FREE_RATE = 0.015
CONTRACT_MONTH = "202607"
SETTLEMENT_DATE = "2026-07-15"
SNAPSHOT_AT = "2026-06-19T13:29:45+08:00"
TRADING_DATE = "2026-06-19"
FUTURE_SYMBOL = "TXFG6"
FUTURE_PRICE = 22642.0


def norm_cdf(value: float) -> float:
    return 0.5 * (1 + math.erf(value / math.sqrt(2)))


def norm_pdf(value: float) -> float:
    return math.exp(-0.5 * value * value) / math.sqrt(2 * math.pi)


def black76_price(
    future_price: float,
    strike: float,
    years: float,
    rate: float,
    volatility: float,
    option_type: str,
) -> float:
    t = max(years, 1 / (365 * 24 * 60))
    sigma = max(volatility, 0.0001)
    sqrt_t = math.sqrt(t)
    discount = math.exp(-rate * t)
    d1 = (math.log(future_price / strike) + 0.5 * sigma * sigma * t) / (sigma * sqrt_t)
    d2 = d1 - sigma * sqrt_t
    if option_type == "call":
        return discount * (future_price * norm_cdf(d1) - strike * norm_cdf(d2))
    return discount * (strike * norm_cdf(-d2) - future_price * norm_cdf(-d1))


def implied_volatility(
    future_price: float,
    strike: float,
    years: float,
    rate: float,
    option_price: float,
    option_type: str,
) -> float | None:
    if option_price <= 0:
        return None
    low = 0.0001
    high = 1.0
    while high < 10 and black76_price(future_price, strike, years, rate, high, option_type) < option_price:
        high *= 2
    if black76_price(future_price, strike, years, rate, low, option_type) > option_price + 0.01:
        return None
    for _ in range(80):
        mid = (low + high) / 2
        price = black76_price(future_price, strike, years, rate, mid, option_type)
        if abs(price - option_price) < 0.001:
            return mid
        if price > option_price:
            high = mid
        else:
            low = mid
    return (low + high) / 2


def black76_greeks(
    future_price: float,
    strike: float,
    years: float,
    rate: float,
    volatility: float,
    option_type: str,
) -> dict[str, float]:
    t = max(years, 1 / (365 * 24 * 60))
    sigma = max(volatility, 0.0001)
    sqrt_t = math.sqrt(t)
    discount = math.exp(-rate * t)
    d1 = (math.log(future_price / strike) + 0.5 * sigma * sigma * t) / (sigma * sqrt_t)
    pdf = norm_pdf(d1)
    price = black76_price(future_price, strike, t, rate, sigma, option_type)
    delta = discount * norm_cdf(d1) if option_type == "call" else -discount * norm_cdf(-d1)
    gamma = discount * pdf / (future_price * sigma * sqrt_t)
    theta = (rate * price - discount * future_price * pdf * sigma / (2 * sqrt_t)) / 365
    vega = discount * future_price * pdf * sqrt_t / 100
    return {
        "delta": delta,
        "gamma": gamma,
        "theta": theta,
        "vega": vega,
    }


def parse_dt(value: str) -> datetime:
    return datetime.fromisoformat(value)


def years_to_expiry(snapshot_at: str, settlement_date: str) -> float:
    start = parse_dt(snapshot_at)
    expiry = datetime.fromisoformat(f"{settlement_date}T13:30:00+08:00")
    return max((expiry - start).total_seconds(), 60) / (365 * 24 * 60 * 60)


def round_tick(value: float, tick: float = 0.5) -> float:
    return round(value / tick) * tick


def round_or_none(value: float | None, digits: int) -> float | None:
    if value is None or not math.isfinite(value):
        return None
    return round(value, digits)


def option_symbol(strike: int, option_type: str) -> str:
    month_code = "G" if option_type == "call" else "S"
    year_digit = CONTRACT_MONTH[3]
    return f"TXO{strike}{month_code}{year_digit}"


def demo_iv(strike: int, option_type: str) -> float:
    distance = abs(strike - FUTURE_PRICE) / FUTURE_PRICE
    iv = 0.245 + distance * 2.2
    if option_type == "put" and strike < FUTURE_PRICE:
        iv += 0.012
    if option_type == "call" and strike > FUTURE_PRICE:
        iv += 0.006
    return iv


def build_leg(strike: int, option_type: str, years: float, index: int) -> dict[str, Any]:
    model_iv = demo_iv(strike, option_type)
    fair = black76_price(FUTURE_PRICE, strike, years, RISK_FREE_RATE, model_iv, option_type)
    spread = max(1.0, fair * 0.025)
    bid = max(0.5, round_tick(fair - spread / 2))
    ask = max(bid + 0.5, round_tick(fair + spread / 2))
    mid = (bid + ask) / 2
    bid_iv = implied_volatility(FUTURE_PRICE, strike, years, RISK_FREE_RATE, bid, option_type)
    ask_iv = implied_volatility(FUTURE_PRICE, strike, years, RISK_FREE_RATE, ask, option_type)
    mid_iv = implied_volatility(FUTURE_PRICE, strike, years, RISK_FREE_RATE, mid, option_type) or model_iv
    greeks = black76_greeks(FUTURE_PRICE, strike, years, RISK_FREE_RATE, mid_iv, option_type)
    direction = 1 if option_type == "call" else -1
    return {
        "symbol": option_symbol(strike, option_type),
        "type": option_type,
        "bid": round(bid, 2),
        "ask": round(ask, 2),
        "mid": round(mid, 2),
        "bid_size": max(1, 24 - index * 2),
        "ask_size": max(1, 18 + index),
        "last": round(round_tick(fair + direction * 0.5), 2),
        "volume": max(20, 1700 - abs(strike - int(FUTURE_PRICE)) * 2 + index * 15),
        "change": round(direction * (2.5 + index * 0.8), 2),
        "change_percent": round(direction * (0.004 + index * 0.001), 4),
        "quote_at": SNAPSHOT_AT,
        "aggregate_at": SNAPSHOT_AT,
        "bid_iv": round_or_none(bid_iv, 6),
        "ask_iv": round_or_none(ask_iv, 6),
        "mid_iv": round(mid_iv, 6),
        "delta": round(greeks["delta"], 6),
        "gamma": round(greeks["gamma"], 8),
        "theta": round(greeks["theta"], 6),
        "vega": round(greeks["vega"], 6),
        "greeks_source": "black76_mid_iv",
        "quality": {
            "status": "ok",
            "bid_ask_state": "normal",
            "stale": False,
            "age_seconds": 0,
            "warnings": [],
        },
    }


def quick_vix(rows: list[dict[str, Any]]) -> dict[str, Any]:
    samples = []
    for side in ("call", "put"):
        candidates = []
        for row in rows:
            leg = row[side]
            distance = abs(row["strike"] - FUTURE_PRICE)
            candidates.append({
                "side": side,
                "strike": row["strike"],
                "iv": leg["mid_iv"],
                "weight": 1 / max(distance, 50),
            })
        samples.extend(sorted(candidates, key=lambda item: (abs(item["strike"] - FUTURE_PRICE), item["strike"]))[:4])
    weight_sum = sum(item["weight"] for item in samples)
    value_decimal = sum(item["iv"] * item["weight"] for item in samples) / weight_sum
    return {
        "value_decimal": round(value_decimal, 6),
        "value_percent": round(value_decimal * 100, 4),
        "sample_count": len(samples),
        "call_count": sum(1 for item in samples if item["side"] == "call"),
        "put_count": sum(1 for item in samples if item["side"] == "put"),
        "method": "4 nearest call + 4 nearest put mid IV, ATM-distance weighted",
        "samples": [
            {
                "side": item["side"],
                "strike": item["strike"],
                "mid_iv": round(item["iv"], 6),
                "weight": round(item["weight"], 8),
            }
            for item in samples
        ],
    }


def build_quote_snapshot() -> dict[str, Any]:
    years = years_to_expiry(SNAPSHOT_AT, SETTLEMENT_DATE)
    strikes = [22400, 22500, 22600, 22700, 22800]
    rows = [
        {
            "strike": strike,
            "call": build_leg(strike, "call", years, index),
            "put": build_leg(strike, "put", years, index),
        }
        for index, strike in enumerate(strikes)
    ]
    snapshot_id = f"quote_snapshot:TXO:{CONTRACT_MONTH}:{SNAPSHOT_AT}"
    expiry_at = f"{SETTLEMENT_DATE}T13:30:00+08:00"
    return {
        "schema": "quote_snapshot",
        "schema_version": SCHEMA_VERSION,
        "snapshot_id": snapshot_id,
        "exchange": "TAIFEX",
        "product": "TXO",
        "contract_month": CONTRACT_MONTH,
        "settlement_date": SETTLEMENT_DATE,
        "trading_date": TRADING_DATE,
        "session": "day",
        "snapshot_at": SNAPSHOT_AT,
        "received_at": (parse_dt(SNAPSHOT_AT) + timedelta(seconds=1)).isoformat(),
        "status": "ok",
        "stale": False,
        "source": {
            "type": "demo_static",
            "provider": "local_demo",
        },
        "underlying": {
            "product": "TXF",
            "symbol": FUTURE_SYMBOL,
            "price": FUTURE_PRICE,
            "source": "demo_static",
            "updated_at": SNAPSHOT_AT,
        },
        "risk_model": {
            "model": "black76",
            "risk_free_rate": RISK_FREE_RATE,
            "expiry_at": expiry_at,
            "time_to_expiry_years": round(years, 8),
            "iv_basis": "mid_price",
        },
        "rows": rows,
        "vix": quick_vix(rows),
        "vix_series": [
            {"time": "2026-06-19T13:29:40+08:00", "value_percent": 25.18},
            {"time": "2026-06-19T13:29:42+08:00", "value_percent": 25.24},
            {"time": SNAPSHOT_AT, "value_percent": quick_vix(rows)["value_percent"]},
        ],
        "metadata": {
            "row_count": len(rows),
            "contract_multiplier": POINT_VALUE_TXO,
            "price_unit": "index_points",
            "iv_unit": "decimal",
            "vix_value_unit": "percent",
            "quote_greeks_unit": "per_one_option_before_multiplier",
        },
    }


def demo_positions() -> list[dict[str, Any]]:
    return [
        {
            "position_id": "manual-001",
            "book": "manual",
            "strategy_id": None,
            "instrument": "option",
            "product": "TXO",
            "contract_month": CONTRACT_MONTH,
            "option_type": "call",
            "strike": 22600,
            "side": "long",
            "qty": 2,
            "entry_price": 450.0,
            "opened_at": "2026-06-19T09:18:00+08:00",
            "multiplier": POINT_VALUE_TXO,
        },
        {
            "position_id": "manual-002",
            "book": "manual",
            "strategy_id": None,
            "instrument": "option",
            "product": "TXO",
            "contract_month": CONTRACT_MONTH,
            "option_type": "put",
            "strike": 22500,
            "side": "short",
            "qty": 1,
            "entry_price": 350.0,
            "opened_at": "2026-06-19T09:32:00+08:00",
            "multiplier": POINT_VALUE_TXO,
        },
        {
            "position_id": "auto-001",
            "book": "automation",
            "strategy_id": "demo-short-call",
            "instrument": "option",
            "product": "TXO",
            "contract_month": CONTRACT_MONTH,
            "option_type": "call",
            "strike": 22800,
            "side": "short",
            "qty": 1,
            "entry_price": 330.0,
            "opened_at": "2026-06-19T10:05:00+08:00",
            "multiplier": POINT_VALUE_TXO,
        },
    ]


def leg_lookup(snapshot: dict[str, Any]) -> dict[tuple[int, str], dict[str, Any]]:
    lookup = {}
    for row in snapshot["rows"]:
        lookup[(row["strike"], "call")] = row["call"]
        lookup[(row["strike"], "put")] = row["put"]
    return lookup


def value_position(position: dict[str, Any], snapshot: dict[str, Any]) -> dict[str, Any]:
    lookup = leg_lookup(snapshot)
    leg = lookup.get((position["strike"], position["option_type"]))
    sign = 1 if position["side"] == "long" else -1
    qty = position["qty"]
    multiplier = position["multiplier"]
    if not leg:
        return {
            **position,
            "symbol": None,
            "mark": {
                "price": None,
                "source": "missing",
                "at": snapshot["snapshot_at"],
                "stale": snapshot["stale"],
            },
            "pnl": {
                "points": None,
                "unrealized_twd": None,
                "day_twd": None,
            },
            "unit_greeks": None,
            "position_greeks": None,
            "quality": {
                "status": "missing_quote",
                "warnings": ["No matching quote leg in snapshot."],
            },
        }

    mark_price = leg["mid"] if leg["bid"] > 0 and leg["ask"] > 0 else leg["last"]
    pnl_points = (mark_price - position["entry_price"]) * sign * qty
    unit_greeks = {
        "iv": leg["mid_iv"],
        "delta": leg["delta"],
        "gamma": leg["gamma"],
        "theta": leg["theta"],
        "vega": leg["vega"],
    }
    position_greeks = {
        key: round(value * sign * qty * multiplier, 6)
        for key, value in unit_greeks.items()
        if key != "iv"
    }
    return {
        **position,
        "symbol": leg["symbol"],
        "contract_label": f'TXO {position["contract_month"]} {position["strike"]}{"C" if position["option_type"] == "call" else "P"}',
        "mark": {
            "price": mark_price,
            "source": "live_mid" if not snapshot["stale"] else "cache_mid",
            "at": snapshot["snapshot_at"],
            "stale": snapshot["stale"],
        },
        "pnl": {
            "points": round(pnl_points, 4),
            "unrealized_twd": round(pnl_points * multiplier, 2),
            "day_twd": None,
        },
        "unit_greeks": unit_greeks,
        "position_greeks": position_greeks,
        "quality": {
            "status": "ok",
            "warnings": [],
        },
    }


def build_position_valuation(snapshot: dict[str, Any]) -> dict[str, Any]:
    valued_positions = [value_position(position, snapshot) for position in demo_positions()]
    ok_positions = [item for item in valued_positions if item["quality"]["status"] == "ok"]
    totals = {
        "position_count": len(valued_positions),
        "market_value_twd": round(
            sum(
                item["mark"]["price"]
                * item["qty"]
                * item["multiplier"]
                * (1 if item["side"] == "long" else -1)
                for item in ok_positions
            ),
            2,
        ),
        "unrealized_pnl_twd": round(sum(item["pnl"]["unrealized_twd"] for item in ok_positions), 2),
        "delta": round(sum(item["position_greeks"]["delta"] for item in ok_positions), 6),
        "gamma": round(sum(item["position_greeks"]["gamma"] for item in ok_positions), 6),
        "theta": round(sum(item["position_greeks"]["theta"] for item in ok_positions), 6),
        "vega": round(sum(item["position_greeks"]["vega"] for item in ok_positions), 6),
    }
    return {
        "schema": "position_valuation",
        "schema_version": SCHEMA_VERSION,
        "valuation_id": f"position_valuation:{CONTRACT_MONTH}:{snapshot['snapshot_at']}",
        "snapshot_id": snapshot["snapshot_id"],
        "as_of": snapshot["snapshot_at"],
        "contract_month": CONTRACT_MONTH,
        "currency": "TWD",
        "positions": valued_positions,
        "totals": totals,
        "quality": {
            "status": "ok" if len(ok_positions) == len(valued_positions) else "partial",
            "missing_position_count": len(valued_positions) - len(ok_positions),
            "stale_position_count": sum(1 for item in valued_positions if item["mark"]["stale"]),
        },
    }


def build_payload() -> dict[str, Any]:
    snapshot = build_quote_snapshot()
    valuation = build_position_valuation(snapshot)
    generated_at = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    return {
        "generated_at": generated_at,
        "note": "Static demo payload only. Production code does not import this file.",
        "quote_snapshot": snapshot,
        "position_valuation": valuation,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate quote_snapshot and position_valuation demo JSON.")
    parser.add_argument(
        "--output",
        default=str(ROOT / "data" / "processed" / "quote_position_valuation_demo.json"),
        help="Output JSON path.",
    )
    args = parser.parse_args()

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = build_payload()
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {output_path}")
    print(
        "snapshot_rows={rows} positions={positions} total_pnl_twd={pnl}".format(
            rows=payload["quote_snapshot"]["metadata"]["row_count"],
            positions=payload["position_valuation"]["totals"]["position_count"],
            pnl=payload["position_valuation"]["totals"]["unrealized_pnl_twd"],
        )
    )


if __name__ == "__main__":
    main()
