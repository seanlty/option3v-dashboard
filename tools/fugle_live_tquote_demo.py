"""Local Fugle TXO live T-quote demo.

This is intentionally outside the production dashboard path. It reads
FUGLE_TOKEN from .env, queries REST for the 202607 TXO universe, then uses
Fugle futopt WebSocket books snapshots/updates to render a local T table.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, time as datetime_time, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests
import websockets


ROOT = Path(__file__).resolve().parents[1]
BASE_URL = "https://api.fugle.tw/marketdata/v1.0/futopt"
WS_URL = "wss://api.fugle.tw/marketdata/v1.0/futopt/streaming"
DEFAULT_CONTRACT = "202607"
DEFAULT_PORT = 8787
DEFAULT_RATE = 0.015
VIX_SAMPLE_INTERVAL_SECONDS = 1.0
VIX_SERIES_LIMIT = 3600


@dataclass
class DemoState:
    lock: threading.Lock = field(default_factory=threading.Lock)
    status: str = "starting"
    authenticated: bool = False
    subscribed: bool = False
    after_hours: bool = False
    contract: str = DEFAULT_CONTRACT
    settlement_date: str = ""
    future_symbol: str = ""
    future_price: float | None = None
    risk_free_rate: float = DEFAULT_RATE
    selected_symbols: list[str] = field(default_factory=list)
    selected_strikes: list[int] = field(default_factory=list)
    books: dict[str, dict[str, Any]] = field(default_factory=dict)
    aggregates: dict[str, dict[str, Any]] = field(default_factory=dict)
    symbol_meta: dict[str, dict[str, Any]] = field(default_factory=dict)
    event_counts: dict[str, int] = field(default_factory=dict)
    vix_series: list[dict[str, Any]] = field(default_factory=list)
    last_vix_sample_monotonic: float = 0
    last_event_at: str = ""
    last_book_at: str = ""
    last_aggregate_at: str = ""
    error: str = ""

    def update(self, **changes: Any) -> None:
        with self.lock:
            for key, value in changes.items():
                setattr(self, key, value)

    def increment(self, event: str) -> None:
        with self.lock:
            self.event_counts[event] = self.event_counts.get(event, 0) + 1
            self.last_event_at = now_text()

    def set_book(self, symbol: str, book: dict[str, Any]) -> None:
        with self.lock:
            self.books[symbol] = book
            self.last_book_at = now_text()
            self.record_vix_sample_unlocked()

    def set_aggregate(self, symbol: str, aggregate: dict[str, Any]) -> None:
        with self.lock:
            self.aggregates[symbol] = aggregate
            if symbol == self.future_symbol:
                price = first_number(
                    aggregate.get("lastPrice"),
                    aggregate.get("closePrice"),
                    (aggregate.get("lastTrade") or {}).get("price"),
                )
                if price and price > 0:
                    self.future_price = price
            self.last_aggregate_at = now_text()
            self.record_vix_sample_unlocked()

    def record_vix_sample_unlocked(self) -> None:
        current = time.monotonic()
        if current - self.last_vix_sample_monotonic < VIX_SAMPLE_INTERVAL_SECONDS:
            return
        years = years_to_expiry(self.settlement_date)
        rows = t_quote_rows(
            self.selected_strikes,
            self.symbol_meta,
            self.books,
            self.aggregates,
            self.future_price,
            self.risk_free_rate,
            years,
        )
        vix = quick_vix_from_rows(rows, self.future_price)
        if not vix:
            return
        self.last_vix_sample_monotonic = current
        self.vix_series.append({
            "time": now_text(),
            "value": vix["value"],
            "sample_count": vix["sample_count"],
            "call_count": vix["call_count"],
            "put_count": vix["put_count"],
        })
        if len(self.vix_series) > VIX_SERIES_LIMIT:
            del self.vix_series[:-VIX_SERIES_LIMIT]

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            years = years_to_expiry(self.settlement_date)
            rows = t_quote_rows(
                self.selected_strikes,
                self.symbol_meta,
                self.books,
                self.aggregates,
                self.future_price,
                self.risk_free_rate,
                years,
            )
            vix = quick_vix_from_rows(rows, self.future_price)
            return {
                "status": self.status,
                "authenticated": self.authenticated,
                "subscribed": self.subscribed,
                "after_hours": self.after_hours,
                "contract": self.contract,
                "settlement_date": self.settlement_date,
                "future_symbol": self.future_symbol,
                "future_price": self.future_price,
                "risk_free_rate": self.risk_free_rate,
                "time_to_expiry_years": years,
                "selected_symbols": self.selected_symbols,
                "selected_strikes": self.selected_strikes,
                "event_counts": self.event_counts,
                "last_event_at": self.last_event_at,
                "last_book_at": self.last_book_at,
                "last_aggregate_at": self.last_aggregate_at,
                "vix": vix,
                "vix_series": self.vix_series,
                "error": self.error,
                "rows": rows,
            }


def now_text() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def load_env_token() -> str:
    env_path = ROOT / ".env"
    if not env_path.exists():
        return ""
    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        if key.strip() in {"FUGLE_TOKEN", "FUGLE_API_KEY", "FUGLE_MARKETDATA_API_KEY"}:
            return value.strip().strip("'\"")
    return ""


def fugle_get(path: str, token: str, **params: Any) -> dict[str, Any]:
    response = requests.get(
        f"{BASE_URL}{path}",
        params={key: value for key, value in params.items() if value is not None},
        headers={"X-API-KEY": token},
        timeout=20,
    )
    response.raise_for_status()
    return response.json()


def first_number(*values: Any) -> float | None:
    for value in values:
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(parsed):
            return parsed
    return None


def settlement_date_for_contract(contract: str) -> str:
    year = contract[:4]
    month = int(contract[4:6])
    dates_path = ROOT / "data" / "settlement_dates.json"
    if dates_path.exists():
        data = json.loads(dates_path.read_text(encoding="utf-8"))
        for date in data.get(year, []):
            if date.startswith(f"{year}-{month:02d}-"):
                return date
    return ""


def month_code(contract: str, side: str) -> str:
    month_index = int(contract[4:6]) - 1
    digit = contract[3]
    months = "ABCDEFGHIJKL" if side == "call" else "MNOPQRSTUVWX"
    return f"{months[month_index]}{digit}"


def future_symbol_for_contract(contract: str) -> str:
    return f"TXF{month_code(contract, 'call')}"


def parse_txo_symbol(symbol: str) -> dict[str, Any] | None:
    match = re.match(r"^TXO(\d+)([A-X])(\d)$", symbol)
    if not match:
        return None
    code = match.group(2)
    return {
        "strike": int(match.group(1)),
        "side": "call" if code in "ABCDEFGHIJKL" else "put",
    }


def prepare_universe(token: str, contract: str, strike_count: int, after_hours: bool) -> dict[str, Any]:
    settlement_date = settlement_date_for_contract(contract)
    future_symbol = future_symbol_for_contract(contract)
    future = fugle_get(
        f"/intraday/quote/{future_symbol}",
        token,
        session="afterhours" if after_hours else None,
    )
    future_price = (
        future.get("lastPrice")
        or future.get("closePrice")
        or (future.get("lastTrade") or {}).get("price")
        or future.get("referencePrice")
    )

    tickers = fugle_get(
        "/intraday/tickers",
        token,
        type="OPTION",
        exchange="TAIFEX",
        session="AFTERHOURS" if after_hours else "REGULAR",
        product="TXO",
    ).get("data", [])
    if after_hours and not tickers:
        tickers = fugle_get(
            "/intraday/tickers",
            token,
            type="OPTION",
            exchange="TAIFEX",
            session="REGULAR",
            product="TXO",
        ).get("data", [])

    rows = []
    for row in tickers:
        if settlement_date and row.get("settlementDate") != settlement_date:
            continue
        parsed = parse_txo_symbol(row.get("symbol", ""))
        if parsed:
            rows.append({**row, **parsed})

    if not rows:
        raise RuntimeError(f"No TXO rows found for contract {contract} / settlement {settlement_date}")

    if not future_price:
        future_price = sorted(row.get("referencePrice", 0) for row in rows)[len(rows) // 2]

    strikes = sorted({row["strike"] for row in rows})
    nearest = sorted(strikes, key=lambda strike: (abs(strike - future_price), strike))[:strike_count]
    selected_strikes = sorted(nearest)
    symbol_meta = {
        row["symbol"]: row
        for row in rows
        if row["strike"] in selected_strikes
    }
    selected_symbols = [
        row["symbol"]
        for row in sorted(symbol_meta.values(), key=lambda row: (row["strike"], row["side"]))
    ]

    return {
        "settlement_date": settlement_date,
        "future_symbol": future_symbol,
        "future_price": future_price,
        "selected_strikes": selected_strikes,
        "selected_symbols": selected_symbols,
        "symbol_meta": symbol_meta,
    }


def best_level(book: dict[str, Any] | None, side: str) -> dict[str, Any]:
    if not book:
        return {}
    levels = book.get(side) or []
    return levels[0] if levels else {}


def years_to_expiry(settlement_date: str) -> float:
    if not settlement_date:
        return 1 / 365
    try:
        expiry_date = datetime.strptime(settlement_date, "%Y-%m-%d").date()
    except ValueError:
        return 1 / 365
    now = datetime.now().astimezone()
    expiry = datetime.combine(expiry_date, datetime_time(hour=13, minute=30), tzinfo=now.tzinfo)
    seconds = max((expiry - now).total_seconds(), 60)
    return seconds / (365 * 24 * 60 * 60)


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
    option_side: str,
) -> float:
    if future_price <= 0 or strike <= 0:
        return math.nan
    t = max(years, 1 / (365 * 24 * 60))
    sigma = max(volatility, 0.0001)
    sqrt_t = math.sqrt(t)
    discount = math.exp(-rate * t)
    d1 = (math.log(future_price / strike) + 0.5 * sigma * sigma * t) / (sigma * sqrt_t)
    d2 = d1 - sigma * sqrt_t
    if option_side == "call":
        return discount * (future_price * norm_cdf(d1) - strike * norm_cdf(d2))
    return discount * (strike * norm_cdf(-d2) - future_price * norm_cdf(-d1))


def implied_volatility(
    future_price: float | None,
    strike: float,
    years: float,
    rate: float,
    option_price: float | None,
    option_side: str,
) -> float | None:
    if not future_price or future_price <= 0 or not option_price or option_price <= 0:
        return None
    low = 0.0001
    high = 1.0
    target = float(option_price)
    while high < 10 and black76_price(future_price, strike, years, rate, high, option_side) < target:
        high *= 2
    high_price = black76_price(future_price, strike, years, rate, high, option_side)
    low_price = black76_price(future_price, strike, years, rate, low, option_side)
    if target < low_price - 0.01 or target > high_price + 0.01:
        return None
    for _ in range(80):
        mid = (low + high) / 2
        price = black76_price(future_price, strike, years, rate, mid, option_side)
        if abs(price - target) < 0.01:
            return mid
        if price > target:
            high = mid
        else:
            low = mid
    return (low + high) / 2


def black76_greeks(
    future_price: float | None,
    strike: float,
    years: float,
    rate: float,
    volatility: float | None,
    option_side: str,
) -> dict[str, float | None]:
    if not future_price or future_price <= 0 or not volatility or volatility <= 0:
        return {"delta": None, "gamma": None, "theta": None, "vega": None}
    t = max(years, 1 / (365 * 24 * 60))
    sigma = max(volatility, 0.0001)
    sqrt_t = math.sqrt(t)
    discount = math.exp(-rate * t)
    d1 = (math.log(future_price / strike) + 0.5 * sigma * sigma * t) / (sigma * sqrt_t)
    pdf = norm_pdf(d1)
    price = black76_price(future_price, strike, t, rate, sigma, option_side)
    delta = discount * norm_cdf(d1) if option_side == "call" else -discount * norm_cdf(-d1)
    gamma = discount * pdf / (future_price * sigma * sqrt_t)
    theta = (rate * price - discount * future_price * pdf * sigma / (2 * sqrt_t)) / 365
    vega = discount * future_price * pdf * sqrt_t / 100
    return {
        "delta": delta,
        "gamma": gamma,
        "theta": theta,
        "vega": vega,
    }


def mid_price(bid: float | None, ask: float | None) -> float | None:
    if bid and ask and bid > 0 and ask > 0:
        return (bid + ask) / 2
    return bid or ask


def option_metrics(
    future_price: float | None,
    strike: float,
    years: float,
    rate: float,
    option_side: str,
    bid: float | None,
    ask: float | None,
) -> dict[str, float | None]:
    mid = mid_price(bid, ask)
    bid_iv = implied_volatility(future_price, strike, years, rate, bid, option_side)
    ask_iv = implied_volatility(future_price, strike, years, rate, ask, option_side)
    mid_iv = implied_volatility(future_price, strike, years, rate, mid, option_side)
    greeks = black76_greeks(future_price, strike, years, rate, mid_iv, option_side)
    return {
        "bid_iv": bid_iv,
        "ask_iv": ask_iv,
        "mid_iv": mid_iv,
        **greeks,
    }


def median_strike_gap(strikes: list[int]) -> float:
    if len(strikes) < 2:
        return 100.0
    gaps = [
        strikes[index] - strikes[index - 1]
        for index in range(1, len(strikes))
        if strikes[index] > strikes[index - 1]
    ]
    if not gaps:
        return 100.0
    ordered = sorted(gaps)
    return float(ordered[len(ordered) // 2])


def quick_vix_from_rows(rows: list[dict[str, Any]], future_price: float | None) -> dict[str, Any] | None:
    if not future_price or future_price <= 0:
        return None
    strikes = sorted({int(row["strike"]) for row in rows if row.get("strike")})
    gap = median_strike_gap(strikes)
    floor_distance = max(gap / 2, 1)
    samples = []
    for side in ("call", "put"):
        candidates = []
        for row in rows:
            leg = row.get(side) or {}
            iv = leg.get("mid_iv")
            strike = first_number(row.get("strike"))
            if iv and iv > 0 and strike:
                distance = abs(strike - future_price)
                candidates.append({
                    "side": side,
                    "strike": strike,
                    "iv": iv,
                    "weight": 1 / max(distance, floor_distance),
                })
        samples.extend(sorted(candidates, key=lambda item: (abs(item["strike"] - future_price), item["strike"]))[:4])

    if not samples:
        return None
    weight_sum = sum(item["weight"] for item in samples)
    if weight_sum <= 0:
        return None
    value = sum(item["iv"] * item["weight"] for item in samples) / weight_sum * 100
    call_count = sum(1 for item in samples if item["side"] == "call")
    put_count = sum(1 for item in samples if item["side"] == "put")
    return {
        "value": value,
        "sample_count": len(samples),
        "call_count": call_count,
        "put_count": put_count,
        "method": "4 nearest call + 4 nearest put mid IV, ATM-distance weighted",
    }


def t_quote_rows(
    strikes: list[int],
    symbol_meta: dict[str, dict[str, Any]],
    books: dict[str, dict[str, Any]],
    aggregates: dict[str, dict[str, Any]],
    future_price: float | None,
    rate: float,
    years: float,
) -> list[dict[str, Any]]:
    by_strike: dict[int, dict[str, Any]] = {strike: {"strike": strike} for strike in strikes}
    for symbol, meta in symbol_meta.items():
        side = meta["side"]
        strike = meta["strike"]
        book = books.get(symbol, {})
        aggregate = aggregates.get(symbol, {})
        bid = best_level(book, "bids")
        ask = best_level(book, "asks")
        bid_price = first_number(bid.get("price"))
        ask_price = first_number(ask.get("price"))
        last_price = first_number(
            aggregate.get("lastPrice"),
            aggregate.get("closePrice"),
            (aggregate.get("lastTrade") or {}).get("price"),
        )
        total = aggregate.get("total") or {}
        metrics = option_metrics(future_price, strike, years, rate, side, bid_price, ask_price)
        by_strike[strike][side] = {
            "symbol": symbol,
            "bid": bid_price,
            "bid_size": bid.get("size"),
            "ask": ask_price,
            "ask_size": ask.get("size"),
            "last": last_price,
            "volume": first_number(total.get("tradeVolume")),
            "change": first_number(aggregate.get("change")),
            "change_percent": first_number(aggregate.get("changePercent")),
            "last_updated": first_number(aggregate.get("lastUpdated")),
            "bid_iv": metrics["bid_iv"],
            "ask_iv": metrics["ask_iv"],
            "mid_iv": metrics["mid_iv"],
            "delta": metrics["delta"],
            "gamma": metrics["gamma"],
            "theta": metrics["theta"],
            "vega": metrics["vega"],
            "time": book.get("time"),
        }
    return [by_strike[strike] for strike in strikes]


async def fugle_books_loop(token: str, state: DemoState) -> None:
    while True:
        try:
            state.update(status="connecting", error="")
            async with websockets.connect(WS_URL, ping_interval=None, close_timeout=3) as websocket:
                await websocket.send(json.dumps({"event": "auth", "data": {"apikey": token}}))
                state.update(status="authenticating", authenticated=False, subscribed=False)

                async for raw in websocket:
                    message = json.loads(raw)
                    event = message.get("event", "")
                    state.increment(event)

                    if event == "authenticated":
                        state.update(status="authenticated", authenticated=True)
                        snapshot = state.snapshot()
                        await websocket.send(json.dumps({
                            "event": "subscribe",
                            "data": {
                                "channel": "books",
                                "symbols": snapshot["selected_symbols"],
                                "afterHours": snapshot["after_hours"],
                            },
                        }))
                        await websocket.send(json.dumps({
                            "event": "subscribe",
                            "data": {
                                "channel": "aggregates",
                                "symbols": [snapshot["future_symbol"], *snapshot["selected_symbols"]],
                                "afterHours": snapshot["after_hours"],
                            },
                        }))
                    elif event == "subscribed":
                        state.update(status="subscribed", subscribed=True)
                    elif event in {"snapshot", "data"} and message.get("channel") == "books":
                        data = message.get("data") or {}
                        symbol = data.get("symbol")
                        if symbol:
                            state.set_book(symbol, data)
                            state.update(status="live")
                    elif event in {"snapshot", "data"} and message.get("channel") == "aggregates":
                        data = message.get("data") or {}
                        symbol = data.get("symbol")
                        if symbol:
                            state.set_aggregate(symbol, data)
                            state.update(status="live")
                    elif event == "error":
                        state.update(status="error", error=json.dumps(message.get("data"), ensure_ascii=False))
        except Exception as error:  # noqa: BLE001 - demo should reconnect and display status.
            state.update(status="reconnecting", authenticated=False, subscribed=False, error=str(error))
            time.sleep(3)


class DemoHandler(BaseHTTPRequestHandler):
    state: DemoState

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - stdlib API.
        return

    def do_GET(self) -> None:  # noqa: N802 - stdlib API.
        path = urlparse(self.path).path
        if path == "/":
            self.send_html()
        elif path == "/snapshot":
            self.send_json(self.state.snapshot())
        elif path == "/events":
            self.send_events()
        else:
            self.send_error(404)

    def send_html(self) -> None:
        body = HTML.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_events(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        while True:
            try:
                payload = json.dumps(self.state.snapshot(), ensure_ascii=False)
                self.wfile.write(f"data: {payload}\n\n".encode("utf-8"))
                self.wfile.flush()
                time.sleep(1)
            except (BrokenPipeError, ConnectionResetError):
                break


HTML = """<!doctype html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Fugle TXO Live T Quote Demo</title>
  <style>
    :root { color-scheme: light; font-family: "Segoe UI", system-ui, sans-serif; }
    body { margin: 0; background: #f4f7f9; color: #17202a; }
    main { max-width: 1680px; margin: 0 auto; padding: 24px; }
    header { display: flex; justify-content: space-between; gap: 16px; align-items: flex-end; margin-bottom: 18px; }
    h1 { margin: 0; font-size: 24px; letter-spacing: 0; }
    .muted { color: #607080; font-size: 13px; }
    .status { display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: 10px; margin-bottom: 16px; }
    .tile { background: #fff; border: 1px solid #dbe3ea; border-radius: 8px; padding: 12px; }
    .tile span { display: block; color: #607080; font-size: 12px; margin-bottom: 4px; }
    .tile strong { font-size: 18px; }
    .chart-panel { background: #fff; border: 1px solid #dbe3ea; border-radius: 8px; padding: 12px; margin-bottom: 16px; }
    .chart-head { display: flex; justify-content: space-between; gap: 12px; align-items: baseline; margin-bottom: 8px; }
    .chart-head strong { font-size: 15px; }
    .chart-head span { color: #607080; font-size: 12px; }
    #vixChart { width: 100%; height: 180px; display: block; }
    .table-wrap { overflow-x: auto; border: 1px solid #dbe3ea; background: #fff; }
    table { min-width: 1480px; width: 100%; border-collapse: collapse; background: #fff; }
    th, td { border-bottom: 1px solid #e6edf2; padding: 7px 8px; text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }
    th { background: #eef3f7; color: #405060; font-size: 12px; }
    td.strike, th.strike { text-align: center; background: #f8fafc; font-weight: 700; }
    .call { color: #0f7a54; }
    .put { color: #b4232d; }
    .positive { color: #0f7a54; }
    .negative { color: #b4232d; }
    .empty { color: #9aa8b4; }
    .ok { color: #0f7a54; }
    .warn { color: #b7791f; }
    .error { color: #b4232d; }
    @media (max-width: 760px) {
      main { padding: 14px; }
      header { display: block; }
      .status { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      th, td { padding: 7px 6px; font-size: 12px; }
    }
  </style>
</head>
<body>
  <main>
    <header>
      <div>
        <h1>Fugle TXO 202607 Live T Quote</h1>
        <div class="muted">Local demo only. Token stays on the Python server.</div>
      </div>
      <div id="updated" class="muted">connecting...</div>
    </header>
    <section class="status">
      <div class="tile"><span>連線狀態</span><strong id="status">--</strong></div>
      <div class="tile"><span>合約 / 結算</span><strong id="contract">--</strong></div>
      <div class="tile"><span>中心期貨</span><strong id="future">--</strong></div>
      <div class="tile"><span>VIX 速算</span><strong id="vixValue">--</strong></div>
      <div class="tile"><span>事件計數</span><strong id="events">--</strong></div>
    </section>
    <section class="chart-panel">
      <div class="chart-head">
        <strong>台指選擇權波動率 VIX 速算</strong>
        <span id="vixMeta">4 ATM Call + 4 ATM Put mid IV ATM-distance weighted</span>
      </div>
      <canvas id="vixChart" width="1200" height="180"></canvas>
    </section>
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th colspan="11" class="call">Call</th>
            <th class="strike">Strike</th>
            <th colspan="11" class="put">Put</th>
          </tr>
          <tr>
            <th>成交量</th><th>Last</th><th>買量</th><th>買價</th><th>賣價</th><th>賣量</th><th>IV</th><th>Delta</th><th>Gamma</th><th>Theta</th><th>Vega</th>
            <th class="strike">履約價</th>
            <th>Vega</th><th>Theta</th><th>Gamma</th><th>Delta</th><th>IV</th><th>買量</th><th>買價</th><th>賣價</th><th>賣量</th><th>Last</th><th>成交量</th>
          </tr>
        </thead>
        <tbody id="rows"><tr><td colspan="23" class="empty">waiting for books and aggregates...</td></tr></tbody>
      </table>
    </div>
    <p id="error" class="error"></p>
  </main>
  <script>
    const fmt = (value) => value === null || value === undefined ? "-" : Number(value).toLocaleString("zh-TW");
    const fmt1 = (value) => value === null || value === undefined ? "-" : Number(value).toLocaleString("zh-TW", { maximumFractionDigits: 1 });
    const fmt4 = (value) => value === null || value === undefined ? "-" : Number(value).toLocaleString("zh-TW", { maximumFractionDigits: 4 });
    const pct = (value) => value === null || value === undefined ? "-" : `${(Number(value) * 100).toFixed(1)}%`;
    const tone = (value) => Number(value) > 0 ? "positive" : Number(value) < 0 ? "negative" : "";
    const drawVixChart = (series) => {
      const canvas = document.querySelector("#vixChart");
      const rect = canvas.getBoundingClientRect();
      const dpr = window.devicePixelRatio || 1;
      canvas.width = Math.max(1, Math.floor(rect.width * dpr));
      canvas.height = Math.max(1, Math.floor(rect.height * dpr));
      const ctx = canvas.getContext("2d");
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      const width = rect.width;
      const height = rect.height;
      ctx.clearRect(0, 0, width, height);
      ctx.fillStyle = "#ffffff";
      ctx.fillRect(0, 0, width, height);
      const values = (series || []).map((point) => Number(point.value)).filter(Number.isFinite);
      if (values.length < 2) {
        ctx.fillStyle = "#94a3b8";
        ctx.font = "13px Segoe UI, sans-serif";
        ctx.fillText("waiting for VIX samples...", 16, height / 2);
        return;
      }
      const pad = { left: 44, right: 18, top: 14, bottom: 24 };
      const min = Math.min(...values);
      const max = Math.max(...values);
      const span = Math.max(0.5, max - min);
      const yMin = min - span * 0.18;
      const yMax = max + span * 0.18;
      const x = (index) => pad.left + (index / Math.max(1, values.length - 1)) * (width - pad.left - pad.right);
      const y = (value) => pad.top + ((yMax - value) / (yMax - yMin)) * (height - pad.top - pad.bottom);
      ctx.strokeStyle = "#e2e8f0";
      ctx.lineWidth = 1;
      for (let i = 0; i < 4; i += 1) {
        const yy = pad.top + (i / 3) * (height - pad.top - pad.bottom);
        ctx.beginPath();
        ctx.moveTo(pad.left, yy);
        ctx.lineTo(width - pad.right, yy);
        ctx.stroke();
      }
      ctx.strokeStyle = "#2563eb";
      ctx.lineWidth = 2;
      ctx.beginPath();
      values.forEach((value, index) => {
        const xx = x(index);
        const yy = y(value);
        if (index === 0) ctx.moveTo(xx, yy);
        else ctx.lineTo(xx, yy);
      });
      ctx.stroke();
      const last = values.at(-1);
      ctx.fillStyle = "#2563eb";
      ctx.beginPath();
      ctx.arc(x(values.length - 1), y(last), 3.5, 0, Math.PI * 2);
      ctx.fill();
      ctx.fillStyle = "#475569";
      ctx.font = "12px Segoe UI, sans-serif";
      ctx.fillText(`${yMax.toFixed(1)}%`, 6, pad.top + 4);
      ctx.fillText(`${yMin.toFixed(1)}%`, 6, height - pad.bottom + 4);
      ctx.textAlign = "right";
      ctx.fillText(`${last.toFixed(2)}%`, width - pad.right, Math.max(pad.top + 12, y(last) - 8));
      ctx.textAlign = "left";
    };
    const cls = (status) => status === "live" ? "ok" : status === "error" ? "error" : "warn";
    const source = new EventSource("/events");
    source.onmessage = (event) => {
      const data = JSON.parse(event.data);
      const status = document.querySelector("#status");
      status.textContent = data.status;
      status.className = cls(data.status);
      document.querySelector("#contract").textContent = `${data.contract} / ${data.settlement_date}${data.after_hours ? " 夜盤" : " 日盤"}`;
      document.querySelector("#future").textContent = `${data.future_symbol} ${fmt(data.future_price)}`;
      document.querySelector("#vixValue").textContent = data.vix ? `${Number(data.vix.value).toFixed(2)}%` : "--";
      document.querySelector("#vixMeta").textContent = data.vix
        ? `samples ${data.vix.call_count}C + ${data.vix.put_count}P / ${data.vix.method}`
        : "waiting for 4 ATM Call + 4 ATM Put IV samples";
      document.querySelector("#events").textContent = Object.entries(data.event_counts).map(([k, v]) => `${k}:${v}`).join(" ");
      document.querySelector("#updated").textContent = data.last_aggregate_at
        ? `last aggregate ${data.last_aggregate_at}`
        : data.last_book_at
        ? `last books ${data.last_book_at}`
        : (data.last_event_at ? `last event ${data.last_event_at}` : "waiting...");
      document.querySelector("#error").textContent = data.error || "";
      drawVixChart(data.vix_series || []);
      const rows = data.rows || [];
      document.querySelector("#rows").innerHTML = rows.map((row) => {
        const call = row.call || {};
        const put = row.put || {};
        return `<tr>
          <td>${fmt(call.volume)}</td><td class="call">${fmt1(call.last)}</td><td>${fmt(call.bid_size)}</td><td class="call">${fmt1(call.bid)}</td><td class="call">${fmt1(call.ask)}</td><td>${fmt(call.ask_size)}</td><td>${pct(call.mid_iv)}</td><td>${fmt4(call.delta)}</td><td>${fmt4(call.gamma)}</td><td class="${tone(call.theta)}">${fmt1(call.theta)}</td><td>${fmt1(call.vega)}</td>
          <td class="strike">${fmt(row.strike)}</td>
          <td>${fmt1(put.vega)}</td><td class="${tone(put.theta)}">${fmt1(put.theta)}</td><td>${fmt4(put.gamma)}</td><td>${fmt4(put.delta)}</td><td>${pct(put.mid_iv)}</td><td>${fmt(put.bid_size)}</td><td class="put">${fmt1(put.bid)}</td><td class="put">${fmt1(put.ask)}</td><td>${fmt(put.ask_size)}</td><td class="put">${fmt1(put.last)}</td><td>${fmt(put.volume)}</td>
        </tr>`;
      }).join("") || `<tr><td colspan="23" class="empty">waiting for books and aggregates...</td></tr>`;
    };
  </script>
</body>
</html>
"""


def run_demo(host: str, port: int, contract: str, strike_count: int, after_hours: bool) -> None:
    token = load_env_token()
    if not token:
        raise SystemExit("FUGLE_TOKEN is not configured in .env")

    state = DemoState(contract=contract, after_hours=after_hours)
    universe = prepare_universe(token, contract, strike_count, after_hours)
    state.update(**universe, status="prepared")

    ws_thread = threading.Thread(
        target=lambda: asyncio.run(fugle_books_loop(token, state)),
        name="fugle-books-loop",
        daemon=True,
    )
    ws_thread.start()

    handler = type("BoundDemoHandler", (DemoHandler,), {"state": state})
    server = ThreadingHTTPServer((host, port), handler)
    print(f"Fugle live T quote demo running at http://{host}:{port}/")
    session = "after-hours" if after_hours else "regular"
    print(f"Contract {contract}, settlement {state.snapshot()['settlement_date']}, future {state.snapshot()['future_symbol']}, session {session}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run local Fugle TXO live T quote demo.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--contract", default=DEFAULT_CONTRACT)
    parser.add_argument("--strikes", type=int, default=21, help="Number of strikes around the future price.")
    parser.add_argument("--after-hours", action="store_true", help="Subscribe to Fugle after-hours books.")
    args = parser.parse_args()
    run_demo(args.host, args.port, args.contract, args.strikes, args.after_hours)


if __name__ == "__main__":
    main()
