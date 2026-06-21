from pathlib import Path

from src.fugle_live import mid_price
from src.quote_snapshot_cache import (
    QuoteSnapshotStore,
    mark_cached_snapshot,
    quote_snapshot_from_tquote_payload,
    snapshot_has_usable_quotes,
)


def sample_tquote_payload():
    return {
        "status": "live",
        "after_hours": True,
        "contract": "202607",
        "settlement_date": "2026-07-15",
        "future_symbol": "TXFG6",
        "future_price": 47565,
        "risk_free_rate": 0.015,
        "time_to_expiry_years": 0.071,
        "selected_symbols": ["TXO47500G6", "TXO47500S6"],
        "selected_strikes": [47500],
        "event_counts": {"snapshot": 1},
        "last_event_at": "2026-06-19T17:01:28+08:00",
        "last_book_at": "2026-06-19T17:01:28+08:00",
        "last_aggregate_at": "2026-06-19T17:01:28+08:00",
        "vix": {
            "value": 32.1,
            "sample_count": 8,
            "call_count": 4,
            "put_count": 4,
            "method": "demo",
        },
        "vix_series": [{"time": "2026-06-19T17:01:28+08:00", "value": 32.1}],
        "rows": [{
            "strike": 47500,
            "call": {
                "symbol": "TXO47500G6",
                "bid": 1600,
                "ask": 1640,
                "last": 1625,
                "volume": 120,
                "mid_iv": 0.32,
                "delta": 0.52,
                "gamma": 0.0002,
                "theta": -12,
                "vega": 25,
            },
            "put": {
                "symbol": "TXO47500S6",
                "bid": 1580,
                "ask": 1620,
                "last": 1605,
                "volume": 98,
                "mid_iv": 0.31,
                "delta": -0.48,
                "gamma": 0.0002,
                "theta": -11,
                "vega": 24,
            },
        }],
    }


def test_quote_snapshot_contract_shape_and_cache_stale_state(tmp_path: Path):
    snapshot = quote_snapshot_from_tquote_payload(sample_tquote_payload(), source_type="fugle_live")

    assert snapshot["schema"] == "quote_snapshot"
    assert snapshot["contract_month"] == "202607"
    assert snapshot["session"] == "night"
    assert snapshot["underlying"]["price"] == 47565
    assert snapshot["rows"][0]["call"]["mid"] == 1620
    assert snapshot["vix"]["value_percent"] == 32.1
    assert snapshot_has_usable_quotes(snapshot)

    store = QuoteSnapshotStore(cache_dir=tmp_path, ttl_seconds=0)
    store.write(snapshot)
    cached = store.read_latest()

    assert cached is not None
    assert cached["stale"] is True
    assert cached["status"] == "stale_cache"
    assert cached["source"]["type"] == "fugle_cache"
    assert cached["rows"][0]["call"]["quality"]["stale"] is True


def test_mid_price_requires_both_bid_and_ask():
    assert mid_price(1600, 1640) == 1620
    assert mid_price(1600, None) is None
    assert mid_price(None, 1640) is None
