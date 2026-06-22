import asyncio
import json
import os
import re
from pathlib import Path

import pytest
import requests
from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[1]
BASE_URL = "https://api.fugle.tw/marketdata/v1.0/futopt"
WS_URL = "wss://api.fugle.tw/marketdata/v1.0/futopt/streaming"
CONTRACT = "202607"
SETTLEMENT_DATE = "2026-07-15"


def load_env_token() -> str:
    for key in ("FUGLE_TOKEN", "FUGLE_API_KEY", "FUGLE_MARKETDATA_API_KEY"):
        if token := os.environ.get(key):
            return token
    load_dotenv(ROOT / ".env", override=False)
    for key in ("FUGLE_TOKEN", "FUGLE_API_KEY", "FUGLE_MARKETDATA_API_KEY"):
        if token := os.environ.get(key):
            return token
    return ""


def fugle_token() -> str:
    token = load_env_token()
    if not token:
        pytest.skip("FUGLE_TOKEN is not configured in environment variables or .env")
    return token


def fugle_get(path: str, token: str, **params):
    response = requests.get(
        f"{BASE_URL}{path}",
        params={key: value for key, value in params.items() if value is not None},
        headers={"X-API-KEY": token},
        timeout=20,
    )
    response.raise_for_status()
    return response.json()


def parse_txo_symbol(symbol: str):
    match = re.match(r"^TXO(\d+)([A-X])(\d)$", symbol)
    if not match:
        return None
    code = match.group(2)
    return {
        "strike": int(match.group(1)),
        "side": "call" if code in "ABCDEFGHIJKL" else "put",
    }


def txo_202607_rows(token: str):
    payload = fugle_get(
        "/intraday/tickers",
        token,
        type="OPTION",
        exchange="TAIFEX",
        session="REGULAR",
        product="TXO",
    )
    rows = []
    for row in payload.get("data", []):
        if row.get("settlementDate") != SETTLEMENT_DATE:
            continue
        parsed = parse_txo_symbol(row.get("symbol", ""))
        if parsed:
            rows.append({**row, **parsed})
    return rows


def center_symbols(rows, center_price: float, strike_count: int = 3):
    strikes = sorted({row["strike"] for row in rows})
    nearest = sorted(strikes, key=lambda strike: (abs(strike - center_price), strike))[:strike_count]
    selected_strikes = set(nearest)
    return [
        row["symbol"]
        for row in sorted(rows, key=lambda row: (row["strike"], row["side"]))
        if row["strike"] in selected_strikes
    ]


def test_fugle_rest_txo_202607_quote_smoke():
    token = fugle_token()

    future = fugle_get("/intraday/quote/TXFG6", token)
    center_price = future.get("lastPrice") or future.get("closePrice")
    assert future["symbol"] == "TXFG6"
    assert center_price and center_price > 0

    rows = txo_202607_rows(token)
    assert len(rows) > 0

    symbols = center_symbols(rows, center_price, strike_count=2)
    assert symbols

    quotes = [fugle_get(f"/intraday/quote/{symbol}", token) for symbol in symbols[:4]]
    assert all(quote.get("symbol") for quote in quotes)
    assert any((quote.get("lastTrade") or {}).get("bid") is not None for quote in quotes)


def test_fugle_websocket_books_txo_202607_smoke():
    token = fugle_token()
    websockets = pytest.importorskip("websockets")

    future = fugle_get("/intraday/quote/TXFG6", token)
    center_price = future.get("lastPrice") or future.get("closePrice")
    symbols = center_symbols(txo_202607_rows(token), center_price, strike_count=2)[:4]
    assert symbols

    async def probe():
        async with websockets.connect(WS_URL, ping_interval=None, close_timeout=3) as websocket:
            await websocket.send(json.dumps({"event": "auth", "data": {"apikey": token}}))
            authenticated = False
            subscribed = False
            book_events = []
            deadline = asyncio.get_running_loop().time() + 12
            while asyncio.get_running_loop().time() < deadline:
                raw = await asyncio.wait_for(
                    websocket.recv(),
                    timeout=deadline - asyncio.get_running_loop().time(),
                )
                message = json.loads(raw)
                event = message.get("event")
                if event == "authenticated":
                    authenticated = True
                    await websocket.send(json.dumps({
                        "event": "subscribe",
                        "data": {"channel": "books", "symbols": symbols},
                    }))
                elif event == "subscribed":
                    subscribed = True
                elif event in {"snapshot", "data"} and message.get("channel") == "books":
                    book_events.append(message)
                    if len(book_events) >= 2:
                        break
            return authenticated, subscribed, book_events

    authenticated, subscribed, book_events = asyncio.run(probe())
    assert authenticated
    assert subscribed
    assert book_events
    first_book = book_events[0]["data"]
    assert first_book["symbol"] in symbols
    assert first_book.get("bids") or first_book.get("asks")
