import asyncio
import json

from src import fugle_live, preview_quotes
from src.preview_quotes import build_catalog


def test_catalog_keeps_listed_strikes_and_per_leg_snapshot_times():
    service = fugle_live.FugleLiveTQuoteService("", "202611")
    service.state.update(
        future_price=45256, settlement_date="2026-11-18",
        listed_symbols=["TXO47000K6", "TXO47000W6", "TXO47100W6"],
    )
    cache = {"options": [
        {"options_id": "TXO47000K6", "close": 399, "date": "2026-11-02 13:43:48"},
        {"options_id": "TXO47000L6", "close": 500},
    ]}
    catalog = build_catalog(service, cache)
    assert catalog["contract_month"] == "202611"
    assert catalog["center"] == 45256
    assert [r["strike"] for r in catalog["rows"]] == [47000, 47100]
    call = catalog["rows"][0]["call"]
    assert (call["feed"], call["last"], call["quote_at"]) == ("finmind", 399, "2026-11-02 13:43:48")
    assert catalog["rows"][0]["put"]["feed"] == "unquoted"
    assert "call" not in catalog["rows"][1]


def test_catalog_uses_matching_month_when_listing_is_unavailable():
    service = fugle_live.FugleLiveTQuoteService("", "202611")
    catalog = build_catalog(service, {
        "futures": [{"futures_id": "TXFK6", "close": 45200}],
        "options": [{"options_id": s} for s in ("TXO47000K6", "TXO47000W6", "TXO47000L6", "TX147000K6")],
    })
    assert not catalog["has_listing"]
    assert catalog["center"] == 45200
    assert len(catalog["rows"]) == 1
    assert catalog["rows"][0]["call"]["symbol"] == "TXO47000K6"
    assert catalog["rows"][0]["put"]["symbol"] == "TXO47000W6"


def test_focus_uses_only_requested_far_symbols_and_releases_connection(monkeypatch):
    service = fugle_live.FugleLiveTQuoteService("test", "202611")
    service.state.update(future_price=45256, listed_symbols=["TXO45200K6", "TXO47000K6", "TXO47000W6"],
                         selected_symbols=["TXO45200K6"])
    sent = []

    class MockSocket:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def send(self, message):
            sent.append(json.loads(message))

        async def recv(self):
            return json.dumps({"event": "authenticated" if len(sent) == 1 else "error"})

    monkeypatch.setattr(preview_quotes.websockets, "connect", lambda *args, **kwargs: MockSocket())
    results = []
    asyncio.run(preview_quotes.stream_focus(service, "202611", {45200, 47000, 99999}, results.append))
    assert {m["data"]["channel"] for m in sent if m["event"] == "subscribe"} == {"books", "aggregates"}
    assert all(m["data"]["symbols"] == ["TXO47000K6", "TXO47000W6"] for m in sent[1:])
    assert results == [{"status": "snapshot"}]
    assert not preview_quotes._focus_slot.locked()
