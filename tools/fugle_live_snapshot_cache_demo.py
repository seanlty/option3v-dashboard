"""Build and persist a Fugle REST quote_snapshot cache demo.

This tool is deliberately outside production code. It probes what Fugle REST
can return during the current session, converts the response into the v0.1
quote_snapshot shape, writes a latest cache file, then builds a matching
position_valuation demo from the cached snapshot.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime, time as datetime_time, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.fugle_live_tquote_demo import (  # noqa: E402
    DEFAULT_RATE,
    black76_greeks,
    first_number,
    fugle_get,
    future_symbol_for_contract,
    implied_volatility,
    load_env_token,
    parse_txo_symbol,
    quick_vix_from_rows,
    settlement_date_for_contract,
)


SCHEMA_VERSION = "0.1.0"
POINT_VALUE_TXO = 50
DEFAULT_CONTRACT = "202607"
DEFAULT_CACHE_DIR = ROOT / "data" / "processed" / "fugle_live_snapshot_cache"


class ProbeError(RuntimeError):
    pass


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def is_after_hours_now() -> bool:
    current = datetime.now().time()
    return current >= datetime_time(hour=14, minute=45) or current < datetime_time(hour=6, minute=0)


def parse_iso(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def expiry_iso(settlement_date: str) -> str:
    return f"{settlement_date}T13:30:00+08:00"


def years_to_expiry(settlement_date: str, snapshot_at: str) -> float:
    if not settlement_date:
        return 1 / 365
    snapshot_dt = parse_iso(snapshot_at) or datetime.now(timezone.utc).astimezone()
    expiry_dt = parse_iso(expiry_iso(settlement_date))
    if not expiry_dt:
        return 1 / 365
    seconds = max((expiry_dt - snapshot_dt).total_seconds(), 60)
    return seconds / (365 * 24 * 60 * 60)


def trading_date_from_snapshot(snapshot_at: str) -> str:
    snapshot_dt = parse_iso(snapshot_at) or datetime.now(timezone.utc).astimezone()
    return snapshot_dt.date().isoformat()


def session_label(after_hours: bool) -> str:
    return "night" if after_hours else "day"


def fugle_ticker_session(after_hours: bool) -> str:
    return "AFTERHOURS" if after_hours else "REGULAR"


def fugle_quote_session(after_hours: bool) -> str | None:
    return "afterhours" if after_hours else None


def session_candidates(mode: str) -> list[bool]:
    if mode == "afterhours":
        return [True, False]
    if mode == "regular":
        return [False, True]
    detected = is_after_hours_now()
    return [detected, not detected]


def collect_keys(payload: dict[str, Any]) -> list[str]:
    return sorted(str(key) for key in payload.keys())


def nested_get(payload: dict[str, Any], *path: str) -> Any:
    current: Any = payload
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def first_path_number(payload: dict[str, Any], paths: list[tuple[str, ...]]) -> float | None:
    return first_number(*(nested_get(payload, *path) for path in paths))


def first_level(payload: dict[str, Any], side: str) -> dict[str, Any]:
    for key in (side, f"{side}List"):
        levels = payload.get(key)
        if isinstance(levels, list) and levels:
            return levels[0] if isinstance(levels[0], dict) else {}
    orderbook = payload.get("orderbook")
    if isinstance(orderbook, dict):
        levels = orderbook.get(side)
        if isinstance(levels, list) and levels:
            return levels[0] if isinstance(levels[0], dict) else {}
    return {}


def extract_future_price(quote: dict[str, Any]) -> float | None:
    return first_path_number(
        quote,
        [
            ("lastPrice",),
            ("closePrice",),
            ("referencePrice",),
            ("lastTrade", "price"),
            ("lastTrade", "lastPrice"),
            ("lastTrade", "close"),
        ],
    )


def extract_leg_quote(quote: dict[str, Any], meta: dict[str, Any], future_price: float | None, years: float) -> dict[str, Any]:
    bids = first_level(quote, "bids")
    asks = first_level(quote, "asks")
    bid = first_number(
        bids.get("price"),
        quote.get("bidPrice"),
        quote.get("bestBidPrice"),
        quote.get("bid"),
        nested_get(quote, "lastTrade", "bid"),
        nested_get(quote, "lastTrade", "bidPrice"),
    )
    ask = first_number(
        asks.get("price"),
        quote.get("askPrice"),
        quote.get("bestAskPrice"),
        quote.get("ask"),
        nested_get(quote, "lastTrade", "ask"),
        nested_get(quote, "lastTrade", "askPrice"),
    )
    bid_size = first_number(
        bids.get("size"),
        bids.get("volume"),
        quote.get("bidSize"),
        quote.get("bestBidSize"),
        nested_get(quote, "lastTrade", "bidSize"),
        nested_get(quote, "lastTrade", "bidVolume"),
    )
    ask_size = first_number(
        asks.get("size"),
        asks.get("volume"),
        quote.get("askSize"),
        quote.get("bestAskSize"),
        nested_get(quote, "lastTrade", "askSize"),
        nested_get(quote, "lastTrade", "askVolume"),
    )
    last = first_path_number(
        quote,
        [
            ("lastPrice",),
            ("closePrice",),
            ("lastTrade", "price"),
            ("lastTrade", "lastPrice"),
            ("lastTrade", "close"),
        ],
    )
    volume = first_path_number(
        quote,
        [
            ("total", "tradeVolume"),
            ("total", "volume"),
            ("tradeVolume",),
            ("volume",),
        ],
    )
    change = first_path_number(quote, [("change",), ("priceChange",), ("lastTrade", "change")])
    change_percent = first_path_number(quote, [("changePercent",), ("lastTrade", "changePercent")])
    mid = (bid + ask) / 2 if bid and ask and bid > 0 and ask > 0 else None
    option_type = meta["side"]
    strike = int(meta["strike"])
    bid_iv = implied_volatility(future_price, strike, years, DEFAULT_RATE, bid, option_type)
    ask_iv = implied_volatility(future_price, strike, years, DEFAULT_RATE, ask, option_type)
    mid_iv = implied_volatility(future_price, strike, years, DEFAULT_RATE, mid, option_type)
    greeks = black76_greeks(future_price, strike, years, DEFAULT_RATE, mid_iv, option_type)
    warnings = []
    if mid is None:
        warnings.append("bid/ask mid unavailable; IV and Greeks remain null unless both sides are present.")
    if last is None:
        warnings.append("last price unavailable.")
    status = "ok" if mid is not None or last is not None else "missing"
    bid_ask_state = "normal" if mid is not None else "last_only" if last is not None else "missing"
    return {
        "symbol": quote.get("symbol") or meta.get("symbol"),
        "type": option_type,
        "bid": round_or_none(bid, 6),
        "ask": round_or_none(ask, 6),
        "mid": round_or_none(mid, 6),
        "bid_size": round_or_none(bid_size, 0),
        "ask_size": round_or_none(ask_size, 0),
        "last": round_or_none(last, 6),
        "volume": round_or_none(volume, 0),
        "change": round_or_none(change, 6),
        "change_percent": round_or_none(change_percent, 6),
        "quote_at": now_iso(),
        "aggregate_at": now_iso(),
        "bid_iv": round_or_none(bid_iv, 8),
        "ask_iv": round_or_none(ask_iv, 8),
        "mid_iv": round_or_none(mid_iv, 8),
        "delta": round_or_none(greeks["delta"], 8),
        "gamma": round_or_none(greeks["gamma"], 10),
        "theta": round_or_none(greeks["theta"], 8),
        "vega": round_or_none(greeks["vega"], 8),
        "greeks_source": "black76_mid_iv" if mid_iv else "missing_mid_iv",
        "quality": {
            "status": status,
            "bid_ask_state": bid_ask_state,
            "stale": False,
            "age_seconds": 0,
            "warnings": warnings,
        },
        "source_fields": {
            "top_level": collect_keys(quote),
            "last_trade": collect_keys(quote.get("lastTrade") or {}) if isinstance(quote.get("lastTrade"), dict) else [],
            "total": collect_keys(quote.get("total") or {}) if isinstance(quote.get("total"), dict) else [],
        },
    }


def round_or_none(value: float | None, digits: int) -> float | None:
    if value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(parsed):
        return None
    if digits <= 0:
        return int(round(parsed))
    return round(parsed, digits)


def quote_symbol(token: str, symbol: str, after_hours: bool) -> tuple[dict[str, Any], str]:
    session = fugle_quote_session(after_hours)
    try:
        return fugle_get(f"/intraday/quote/{symbol}", token, session=session), "afterhours" if after_hours else "regular"
    except Exception:
        if session is None:
            raise
        return fugle_get(f"/intraday/quote/{symbol}", token), "regular_fallback"


def load_tickers(token: str, contract: str, after_hours: bool) -> tuple[list[dict[str, Any]], str]:
    settlement_date = settlement_date_for_contract(contract)
    sessions = [after_hours, not after_hours]
    last_error: Exception | None = None
    for candidate in sessions:
        try:
            payload = fugle_get(
                "/intraday/tickers",
                token,
                type="OPTION",
                exchange="TAIFEX",
                session=fugle_ticker_session(candidate),
                product="TXO",
            )
            rows = []
            for row in payload.get("data", []):
                if settlement_date and row.get("settlementDate") != settlement_date:
                    continue
                parsed = parse_txo_symbol(row.get("symbol", ""))
                if parsed:
                    rows.append({**row, **parsed})
            if rows:
                return rows, "afterhours" if candidate else "regular"
        except Exception as error:  # noqa: BLE001 - fallback to the other session.
            last_error = error
    raise ProbeError(f"No TXO tickers found for {contract}: {last_error}")


def select_symbol_meta(tickers: list[dict[str, Any]], center_price: float, strike_count: int) -> tuple[list[int], dict[str, dict[str, Any]]]:
    strikes = sorted({int(row["strike"]) for row in tickers})
    nearest = sorted(strikes, key=lambda strike: (abs(strike - center_price), strike))[:strike_count]
    selected_strikes = sorted(nearest)
    symbol_meta = {
        row["symbol"]: row
        for row in tickers
        if int(row["strike"]) in selected_strikes
    }
    return selected_strikes, symbol_meta


def empty_leg(meta: dict[str, Any], warning: str) -> dict[str, Any]:
    return {
        "symbol": meta.get("symbol"),
        "type": meta.get("side"),
        "bid": None,
        "ask": None,
        "mid": None,
        "bid_size": None,
        "ask_size": None,
        "last": None,
        "volume": None,
        "change": None,
        "change_percent": None,
        "quote_at": None,
        "aggregate_at": None,
        "bid_iv": None,
        "ask_iv": None,
        "mid_iv": None,
        "delta": None,
        "gamma": None,
        "theta": None,
        "vega": None,
        "greeks_source": "missing",
        "quality": {
            "status": "missing",
            "bid_ask_state": "missing",
            "stale": False,
            "age_seconds": None,
            "warnings": [warning],
        },
        "source_fields": {
            "top_level": [],
            "last_trade": [],
            "total": [],
        },
    }


def build_rows(
    token: str,
    selected_strikes: list[int],
    symbol_meta: dict[str, dict[str, Any]],
    future_price: float | None,
    years: float,
    after_hours: bool,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    by_strike: dict[int, dict[str, Any]] = {strike: {"strike": strike} for strike in selected_strikes}
    errors: list[str] = []
    quote_key_samples: dict[str, Any] = {}
    bid_ask_count = 0
    last_count = 0
    quote_count = 0
    session_used_counts: dict[str, int] = {}
    for symbol, meta in sorted(symbol_meta.items(), key=lambda item: (item[1]["strike"], item[1]["side"])):
        strike = int(meta["strike"])
        side = meta["side"]
        try:
            quote, quote_session = quote_symbol(token, symbol, after_hours)
            session_used_counts[quote_session] = session_used_counts.get(quote_session, 0) + 1
            quote_count += 1
            leg = extract_leg_quote(quote, meta, future_price, years)
            if leg["mid"] is not None:
                bid_ask_count += 1
            if leg["last"] is not None:
                last_count += 1
            if len(quote_key_samples) < 4:
                quote_key_samples[symbol] = leg["source_fields"]
        except Exception as error:  # noqa: BLE001 - partial snapshot is still useful for cache format.
            errors.append(f"{symbol}: {error}")
            leg = empty_leg(meta, str(error))
        by_strike[strike][side] = leg
    for strike in selected_strikes:
        by_strike[strike].setdefault("call", empty_leg({"symbol": None, "side": "call"}, "Call leg not selected."))
        by_strike[strike].setdefault("put", empty_leg({"symbol": None, "side": "put"}, "Put leg not selected."))
    return [by_strike[strike] for strike in selected_strikes], {
        "quote_count": quote_count,
        "bid_ask_count": bid_ask_count,
        "last_count": last_count,
        "quote_session_used_counts": session_used_counts,
        "quote_key_samples": quote_key_samples,
        "errors": errors,
    }


def adapt_vix(vix: dict[str, Any] | None) -> dict[str, Any] | None:
    if not vix:
        return None
    value_percent = first_number(vix.get("value"), vix.get("value_percent"))
    return {
        "value_decimal": round_or_none(value_percent / 100 if value_percent is not None else None, 8),
        "value_percent": round_or_none(value_percent, 6),
        "sample_count": vix.get("sample_count"),
        "call_count": vix.get("call_count"),
        "put_count": vix.get("put_count"),
        "method": vix.get("method"),
    }


def build_quote_snapshot(token: str, contract: str, strike_count: int, session_mode: str) -> tuple[dict[str, Any], dict[str, Any]]:
    errors: list[str] = []
    for after_hours in session_candidates(session_mode):
        try:
            future_symbol = future_symbol_for_contract(contract)
            future_quote, future_quote_session = quote_symbol(token, future_symbol, after_hours)
            future_price = extract_future_price(future_quote)
            tickers, ticker_session = load_tickers(token, contract, after_hours)
            if not future_price:
                refs = [first_number(row.get("referencePrice")) for row in tickers]
                refs = [value for value in refs if value and value > 0]
                if refs:
                    future_price = sorted(refs)[len(refs) // 2]
            if not future_price:
                raise ProbeError("Future price is unavailable.")
            selected_strikes, symbol_meta = select_symbol_meta(tickers, future_price, strike_count)
            snapshot_at = now_iso()
            years = years_to_expiry(settlement_date_for_contract(contract), snapshot_at)
            rows, row_probe = build_rows(token, selected_strikes, symbol_meta, future_price, years, after_hours)
            vix = adapt_vix(quick_vix_from_rows(rows, future_price))
            status = "ok" if row_probe["bid_ask_count"] else "partial" if row_probe["last_count"] else "missing_quotes"
            snapshot = {
                "schema": "quote_snapshot",
                "schema_version": SCHEMA_VERSION,
                "snapshot_id": f"quote_snapshot:TXO:{contract}:{snapshot_at}",
                "exchange": "TAIFEX",
                "product": "TXO",
                "contract_month": contract,
                "settlement_date": settlement_date_for_contract(contract),
                "trading_date": trading_date_from_snapshot(snapshot_at),
                "session": session_label(after_hours),
                "snapshot_at": snapshot_at,
                "received_at": snapshot_at,
                "status": status,
                "stale": False,
                "source": {
                    "type": "fugle_rest_probe",
                    "provider": "fugle",
                    "session_requested": "afterhours" if after_hours else "regular",
                    "future_quote_session_used": future_quote_session,
                    "ticker_session_used": ticker_session,
                    "quote_session_used_counts": row_probe["quote_session_used_counts"],
                },
                "underlying": {
                    "product": "TXF",
                    "symbol": future_symbol,
                    "price": round_or_none(future_price, 6),
                    "source": "fugle_rest_quote",
                    "updated_at": snapshot_at,
                    "source_fields": {
                        "top_level": collect_keys(future_quote),
                        "last_trade": collect_keys(future_quote.get("lastTrade") or {}) if isinstance(future_quote.get("lastTrade"), dict) else [],
                        "total": collect_keys(future_quote.get("total") or {}) if isinstance(future_quote.get("total"), dict) else [],
                    },
                },
                "risk_model": {
                    "model": "black76",
                    "risk_free_rate": DEFAULT_RATE,
                    "expiry_at": expiry_iso(settlement_date_for_contract(contract)),
                    "time_to_expiry_years": round_or_none(years, 8),
                    "iv_basis": "mid_price",
                },
                "rows": rows,
                "vix": vix,
                "vix_series": [{"time": snapshot_at, "value_percent": vix["value_percent"]}] if vix else [],
                "metadata": {
                    "row_count": len(rows),
                    "selected_symbol_count": len(symbol_meta),
                    "contract_multiplier": POINT_VALUE_TXO,
                    "price_unit": "index_points",
                    "iv_unit": "decimal",
                    "vix_value_unit": "percent",
                    "quote_greeks_unit": "per_one_option_before_multiplier",
                    "rest_probe": {
                        "future_symbol": future_symbol,
                        "selected_symbols": sorted(symbol_meta),
                        "api_quote_count": row_probe["quote_count"],
                        "api_bid_ask_count": row_probe["bid_ask_count"],
                        "api_last_count": row_probe["last_count"],
                        "quote_key_samples": row_probe["quote_key_samples"],
                        "errors": row_probe["errors"],
                    },
                },
            }
            return snapshot, snapshot["metadata"]["rest_probe"]
        except Exception as error:  # noqa: BLE001 - try the other session before failing.
            errors.append(f"{'afterhours' if after_hours else 'regular'}: {error}")
    raise ProbeError("; ".join(errors))


def default_demo_positions(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    rows = snapshot.get("rows") or []
    center = snapshot.get("underlying", {}).get("price") or 0
    ordered = sorted(rows, key=lambda row: (abs((row.get("strike") or 0) - center), row.get("strike") or 0))
    atm = ordered[0] if ordered else {"strike": 0}
    below = min(rows, key=lambda row: row["strike"]) if rows else atm
    above = max(rows, key=lambda row: row["strike"]) if rows else atm
    return [
        {
            "position_id": "cache-demo-001",
            "book": "manual",
            "strategy_id": None,
            "instrument": "option",
            "product": "TXO",
            "contract_month": snapshot["contract_month"],
            "option_type": "call",
            "strike": atm["strike"],
            "side": "long",
            "qty": 1,
            "entry_price": safe_entry_price(atm.get("call")),
            "opened_at": snapshot["snapshot_at"],
            "multiplier": POINT_VALUE_TXO,
        },
        {
            "position_id": "cache-demo-002",
            "book": "manual",
            "strategy_id": None,
            "instrument": "option",
            "product": "TXO",
            "contract_month": snapshot["contract_month"],
            "option_type": "put",
            "strike": below["strike"],
            "side": "short",
            "qty": 1,
            "entry_price": safe_entry_price(below.get("put")),
            "opened_at": snapshot["snapshot_at"],
            "multiplier": POINT_VALUE_TXO,
        },
        {
            "position_id": "cache-demo-003",
            "book": "automation",
            "strategy_id": "cache-demo-short-call",
            "instrument": "option",
            "product": "TXO",
            "contract_month": snapshot["contract_month"],
            "option_type": "call",
            "strike": above["strike"],
            "side": "short",
            "qty": 1,
            "entry_price": safe_entry_price(above.get("call")),
            "opened_at": snapshot["snapshot_at"],
            "multiplier": POINT_VALUE_TXO,
        },
    ]


def safe_entry_price(leg: dict[str, Any] | None) -> float:
    mark = first_number((leg or {}).get("mid"), (leg or {}).get("last"), (leg or {}).get("bid"), (leg or {}).get("ask"))
    if not mark:
        return 0.0
    return round(max(mark - 25, 0.5), 2)


def leg_lookup(snapshot: dict[str, Any]) -> dict[tuple[int, str], dict[str, Any]]:
    lookup = {}
    for row in snapshot.get("rows") or []:
        lookup[(int(row["strike"]), "call")] = row.get("call") or {}
        lookup[(int(row["strike"]), "put")] = row.get("put") or {}
    return lookup


def mark_source(snapshot: dict[str, Any], leg: dict[str, Any]) -> str:
    if leg.get("mid") is not None:
        return "cache_mid" if snapshot.get("stale") else "live_mid"
    if leg.get("last") is not None:
        return "cache_last" if snapshot.get("stale") else "live_last"
    return "missing"


def mark_price(leg: dict[str, Any]) -> float | None:
    return first_number(leg.get("mid"), leg.get("last"), leg.get("bid"), leg.get("ask"))


def value_position(position: dict[str, Any], snapshot: dict[str, Any]) -> dict[str, Any]:
    leg = leg_lookup(snapshot).get((int(position["strike"]), position["option_type"]), {})
    mark = mark_price(leg)
    source = mark_source(snapshot, leg)
    sign = 1 if position["side"] == "long" else -1
    qty = int(position["qty"])
    multiplier = int(position["multiplier"])
    unit_greeks = {
        "iv": leg.get("mid_iv"),
        "delta": leg.get("delta"),
        "gamma": leg.get("gamma"),
        "theta": leg.get("theta"),
        "vega": leg.get("vega"),
    }
    position_greeks = {}
    for key in ("delta", "gamma", "theta", "vega"):
        value = first_number(unit_greeks.get(key))
        position_greeks[key] = round(value * sign * qty * multiplier, 8) if value is not None else None
    if mark is None:
        pnl_points = None
        pnl_twd = None
        status = "missing_quote"
        warnings = ["No usable mark price from quote_snapshot."]
    else:
        pnl_points = (mark - float(position["entry_price"])) * sign * qty
        pnl_twd = pnl_points * multiplier
        status = "ok"
        warnings = []
    return {
        **position,
        "symbol": leg.get("symbol"),
        "contract_label": f'TXO {position["contract_month"]} {position["strike"]}{"C" if position["option_type"] == "call" else "P"}',
        "mark": {
            "price": round_or_none(mark, 6),
            "source": source,
            "at": snapshot["snapshot_at"],
            "stale": snapshot.get("stale", False),
        },
        "pnl": {
            "points": round_or_none(pnl_points, 6),
            "unrealized_twd": round_or_none(pnl_twd, 2),
            "day_twd": None,
        },
        "unit_greeks": unit_greeks,
        "position_greeks": position_greeks,
        "quality": {
            "status": status,
            "warnings": warnings,
        },
    }


def build_position_valuation(snapshot: dict[str, Any]) -> dict[str, Any]:
    positions = [value_position(position, snapshot) for position in default_demo_positions(snapshot)]
    ok_positions = [position for position in positions if position["quality"]["status"] == "ok"]
    totals = {
        "position_count": len(positions),
        "market_value_twd": round_or_none(
            sum(
                (position["mark"]["price"] or 0)
                * position["qty"]
                * position["multiplier"]
                * (1 if position["side"] == "long" else -1)
                for position in ok_positions
            ),
            2,
        ),
        "unrealized_pnl_twd": round_or_none(sum(position["pnl"]["unrealized_twd"] or 0 for position in ok_positions), 2),
        "delta": sum_nullable(position["position_greeks"]["delta"] for position in ok_positions),
        "gamma": sum_nullable(position["position_greeks"]["gamma"] for position in ok_positions),
        "theta": sum_nullable(position["position_greeks"]["theta"] for position in ok_positions),
        "vega": sum_nullable(position["position_greeks"]["vega"] for position in ok_positions),
    }
    return {
        "schema": "position_valuation",
        "schema_version": SCHEMA_VERSION,
        "valuation_id": f"position_valuation:{snapshot['contract_month']}:{snapshot['snapshot_at']}",
        "snapshot_id": snapshot["snapshot_id"],
        "as_of": snapshot["snapshot_at"],
        "contract_month": snapshot["contract_month"],
        "currency": "TWD",
        "positions": positions,
        "totals": totals,
        "quality": {
            "status": "ok" if len(ok_positions) == len(positions) else "partial",
            "missing_position_count": len(positions) - len(ok_positions),
            "stale_position_count": sum(1 for position in positions if position["mark"]["stale"]),
        },
    }


def sum_nullable(values: Any) -> float | None:
    parsed = [value for value in values if value is not None]
    if not parsed:
        return None
    return round(sum(parsed), 8)


def cache_paths(cache_dir: Path) -> dict[str, Path]:
    return {
        "latest_snapshot": cache_dir / "latest_quote_snapshot.json",
        "latest_valuation": cache_dir / "latest_position_valuation.json",
        "latest_payload": cache_dir / "latest_payload.json",
        "history_dir": cache_dir / "history",
    }


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def cache_age_seconds(snapshot: dict[str, Any]) -> float | None:
    snapshot_dt = parse_iso(snapshot.get("snapshot_at", ""))
    if not snapshot_dt:
        return None
    return (datetime.now(timezone.utc).astimezone() - snapshot_dt).total_seconds()


def with_cache_stale_state(snapshot: dict[str, Any], ttl_seconds: int, force_stale: bool) -> dict[str, Any]:
    cached = json.loads(json.dumps(snapshot))
    age = cache_age_seconds(cached)
    stale = force_stale or (age is not None and age > ttl_seconds)
    cached["stale"] = bool(stale)
    cached["status"] = "stale_cache" if stale else cached.get("status", "ok")
    source = dict(cached.get("source") or {})
    source["type"] = "fugle_cache" if stale else source.get("type", "fugle_rest_probe")
    source["cache_age_seconds"] = round_or_none(age, 3)
    source["cache_ttl_seconds"] = ttl_seconds
    cached["source"] = source
    for row in cached.get("rows") or []:
        for side in ("call", "put"):
            quality = row.get(side, {}).get("quality")
            if isinstance(quality, dict):
                quality["stale"] = bool(stale)
                quality["age_seconds"] = round_or_none(age, 3)
    return cached


def write_cache(cache_dir: Path, snapshot: dict[str, Any], valuation: dict[str, Any], probe: dict[str, Any]) -> dict[str, Any]:
    paths = cache_paths(cache_dir)
    history_name = snapshot["snapshot_at"].replace(":", "").replace("+", "_").replace("-", "")
    history_snapshot = paths["history_dir"] / f"{history_name}_quote_snapshot.json"
    history_valuation = paths["history_dir"] / f"{history_name}_position_valuation.json"
    payload = {
        "generated_at": now_iso(),
        "note": "Fugle live snapshot cache demo only. Production code does not import this file.",
        "api_probe": probe,
        "quote_snapshot": snapshot,
        "position_valuation": valuation,
    }
    write_json(paths["latest_snapshot"], snapshot)
    write_json(paths["latest_valuation"], valuation)
    write_json(paths["latest_payload"], payload)
    write_json(history_snapshot, snapshot)
    write_json(history_valuation, valuation)
    return {
        "latest_snapshot": str(paths["latest_snapshot"]),
        "latest_valuation": str(paths["latest_valuation"]),
        "latest_payload": str(paths["latest_payload"]),
        "history_snapshot": str(history_snapshot),
        "history_valuation": str(history_valuation),
    }


def load_cached_payload(cache_dir: Path, ttl_seconds: int, force_stale: bool) -> dict[str, Any]:
    paths = cache_paths(cache_dir)
    if not paths["latest_snapshot"].exists():
        raise ProbeError(f"No cached snapshot exists at {paths['latest_snapshot']}")
    snapshot = with_cache_stale_state(read_json(paths["latest_snapshot"]), ttl_seconds, force_stale)
    valuation = build_position_valuation(snapshot)
    return {
        "generated_at": now_iso(),
        "note": "Read-back from Fugle snapshot cache demo.",
        "quote_snapshot": snapshot,
        "position_valuation": valuation,
    }


def run_probe(args: argparse.Namespace) -> dict[str, Any]:
    token = load_env_token()
    if not token:
        raise ProbeError("FUGLE_TOKEN is not configured in .env")
    snapshot, probe = build_quote_snapshot(token, args.contract, args.strikes, args.session)
    valuation = build_position_valuation(snapshot)
    cache_files = write_cache(Path(args.cache_dir), snapshot, valuation, probe)
    read_back = load_cached_payload(Path(args.cache_dir), args.ttl_seconds, args.force_stale_readback)
    payload = {
        "generated_at": now_iso(),
        "note": "Fugle live snapshot cache demo only. Production code does not import this file.",
        "cache_files": cache_files,
        "api_probe": probe,
        "quote_snapshot": snapshot,
        "position_valuation": valuation,
        "cache_read_back": {
            "quote_snapshot": read_back["quote_snapshot"],
            "position_valuation": read_back["position_valuation"],
        },
    }
    write_json(Path(args.output), payload)
    return payload


def print_summary(payload: dict[str, Any], output_path: Path) -> None:
    snapshot = payload["quote_snapshot"]
    valuation = payload["position_valuation"]
    probe = payload["api_probe"]
    cache_read = payload["cache_read_back"]["quote_snapshot"]
    print(f"Wrote {output_path}")
    print(
        "api status={status} session={session} rows={rows} quotes={quotes} bid_ask={bid_ask} last={last} vix={vix}".format(
            status=snapshot.get("status"),
            session=snapshot.get("session"),
            rows=snapshot.get("metadata", {}).get("row_count"),
            quotes=probe.get("api_quote_count"),
            bid_ask=probe.get("api_bid_ask_count"),
            last=probe.get("api_last_count"),
            vix=(snapshot.get("vix") or {}).get("value_percent"),
        )
    )
    print(
        "valuation positions={positions} pnl_twd={pnl} cache_read_stale={stale} cache_age_seconds={age}".format(
            positions=valuation.get("totals", {}).get("position_count"),
            pnl=valuation.get("totals", {}).get("unrealized_pnl_twd"),
            stale=cache_read.get("stale"),
            age=(cache_read.get("source") or {}).get("cache_age_seconds"),
        )
    )
    if probe.get("errors"):
        print(f"partial_errors={len(probe['errors'])}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Probe Fugle REST quotes and persist quote_snapshot cache demo.")
    parser.add_argument("--contract", default=DEFAULT_CONTRACT)
    parser.add_argument("--strikes", type=int, default=9, help="Number of strikes around the TXF center price.")
    parser.add_argument("--session", choices=["auto", "regular", "afterhours"], default="auto")
    parser.add_argument("--ttl-seconds", type=int, default=60)
    parser.add_argument("--force-stale-readback", action="store_true", help="Mark cache read-back as stale for demo purposes.")
    parser.add_argument("--cache-dir", default=str(DEFAULT_CACHE_DIR))
    parser.add_argument(
        "--output",
        default=str(DEFAULT_CACHE_DIR / "fugle_live_snapshot_cache_demo.json"),
        help="Combined demo output JSON path.",
    )
    args = parser.parse_args()

    output_path = Path(args.output)
    try:
        payload = run_probe(args)
    except Exception as error:  # noqa: BLE001 - fallback read shows how stale cache avoids blank UI.
        cache_dir = Path(args.cache_dir)
        try:
            cached = load_cached_payload(cache_dir, args.ttl_seconds, True)
        except Exception:
            raise
        payload = {
            "generated_at": now_iso(),
            "note": "Fugle probe failed; loaded latest cache as stale fallback.",
            "probe_error": str(error),
            "cache_files": {key: str(value) for key, value in cache_paths(cache_dir).items()},
            "api_probe": {"errors": [str(error)]},
            "quote_snapshot": cached["quote_snapshot"],
            "position_valuation": cached["position_valuation"],
            "cache_read_back": {
                "quote_snapshot": cached["quote_snapshot"],
                "position_valuation": cached["position_valuation"],
            },
        }
        write_json(output_path, payload)
    print_summary(payload, output_path)


if __name__ == "__main__":
    main()
