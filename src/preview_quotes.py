"""Build the preview's monthly strike list from already cached market data."""

import asyncio
import json
import threading
import time

import websockets

try:
    from . import fugle_live as fugle
except ImportError:  # Running python src/main.py
    import fugle_live as fugle


# A preview viewer can use one spare upstream connection; the regular dashboard keeps its own.
_focus_slot = threading.Lock()
MAX_FOCUS_STRIKES = 8


def build_catalog(service, cache):
    with service.state.lock:
        contract = service.state.contract
        listed = service.state.listed_symbols
        center = service.state.future_price
        expiry = service.state.settlement_date
    future_symbol = fugle.future_symbol_for_contract(contract)
    if not center:
        future = next((v for v in cache.get("futures", []) if v.get("futures_id") == future_symbol), {})
        center = fugle.first_number(future.get("close"))
    expiry = expiry or fugle.settlement_date_for_contract(contract)
    years = fugle.years_to_expiry(expiry)
    today = fugle.taipei_now().date().isoformat()
    quotes = {v.get("options_id"): v for v in cache.get("options", []) if v.get("options_id")}
    month_codes = (fugle.month_code(contract, "call"), fugle.month_code(contract, "put"))
    rows = {}
    for symbol in listed or quotes:
        parsed = fugle.parse_txo_symbol(symbol)
        if not parsed or not symbol.endswith(month_codes):
            continue
        strike, side = parsed["strike"], parsed["side"]
        row = rows.setdefault(strike, {"strike": strike})
        quote = quotes.get(symbol)
        if not quote:
            row[side] = {"symbol": symbol, "feed": "unquoted"}
            continue
        bid = fugle.first_number(quote.get("buy_price"))
        ask = fugle.first_number(quote.get("sell_price"))
        stamp = str(quote.get("date") or "")
        leg = {
            "symbol": symbol, "feed": "finmind", "quote_at": stamp,
            "last": fugle.first_number(quote.get("close")), "volume": quote.get("total_volume"),
            "bid": bid, "ask": ask, "bid_size": quote.get("buy_volume"), "ask_size": quote.get("sell_volume"),
        }
        if center and expiry and stamp[:10] == today and bid and ask and 0 < bid <= ask:
            leg.update(fugle.option_metrics(center, strike, years, fugle.DEFAULT_RATE, side, bid, ask))
        row[side] = leg
    return {
        "contract_month": contract, "settlement_date": expiry, "future_symbol": future_symbol,
        "center": center, "updated_at": cache.get("updated_at"), "has_listing": bool(listed),
        "rows": [rows[k] for k in sorted(rows)],
    }


async def stream_focus(service, contract, strikes, emit):
    """Stream the visible far strikes, falling back to the catalog when capacity is busy."""
    with service.state.lock:
        state = service.state
        if state.contract != contract or not state.future_price:
            return
        after_hours, expiry = state.after_hours, state.settlement_date
        listed = state.listed_symbols
        base_symbols = set(state.selected_symbols)
        center = state.future_price
    wanted = {k for k in strikes if abs(k - center) <= 5000}
    meta = {}
    for symbol in listed:
        parsed = fugle.parse_txo_symbol(symbol)
        if parsed and parsed["strike"] in wanted and symbol not in base_symbols:
            meta[symbol] = parsed
    if not meta or not service.token or not _focus_slot.acquire(blocking=False):
        emit({"status": "snapshot"})
        return
    try:
        async with websockets.connect(fugle.WS_URL, ping_interval=None, open_timeout=6, close_timeout=1) as ws:
            await ws.send(json.dumps({"event":"auth", "data":{"apikey":service.token}}))
            books, aggregates = {}, {}
            started = last_emit = time.monotonic()
            authenticated = False
            while True:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=1)
                except asyncio.TimeoutError:
                    raw = None
                now = time.monotonic()
                if raw:
                    msg = json.loads(raw)
                    event = msg.get("event")
                    if event == "error":
                        emit({"status":"snapshot"})
                        return
                    if event == "authenticated":
                        authenticated = True
                        for channel in ("books", "aggregates"):
                            await ws.send(json.dumps({"event":"subscribe", "data":{
                                "channel":channel, "symbols":list(meta), "afterHours":after_hours,
                            }}))
                    elif event in {"data", "snapshot"}:
                        item = msg.get("data") or {}
                        symbol = item.get("symbol")
                        if symbol in meta and msg.get("channel") in {"books", "aggregates"}:
                            (books if msg["channel"] == "books" else aggregates)[symbol] = item
                if not authenticated and now - started > 8:
                    emit({"status":"snapshot"})
                    return
                if now - last_emit >= 1:
                    with state.lock:
                        if state.after_hours != after_hours or state.contract != contract:
                            return
                        center = state.future_price
                    rows = fugle.t_quote_rows(sorted(wanted), meta, books, aggregates, center,
                                              fugle.DEFAULT_RATE, fugle.years_to_expiry(expiry))
                    emit({"status":"stream", "contract_month":contract, "rows":rows})
                    last_emit = now
    finally:
        _focus_slot.release()
