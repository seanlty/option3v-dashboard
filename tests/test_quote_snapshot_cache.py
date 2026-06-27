from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

from src.fugle_live import (
    is_after_hours_now,
    is_night_preopen_now,
    mid_price,
    normalize_future_1m_candle,
    regular_session_state,
)
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


def test_quote_snapshot_store_can_cache_without_disk_writes(tmp_path: Path):
    snapshot = quote_snapshot_from_tquote_payload(sample_tquote_payload(), source_type="fugle_live")
    store = QuoteSnapshotStore(cache_dir=tmp_path, ttl_seconds=60, persist=False)

    store.write(snapshot)
    cached = store.read_latest()

    assert cached is not None
    assert cached["snapshot_id"] == snapshot["snapshot_id"]
    assert not (tmp_path / "latest_quote_snapshot.json").exists()


def test_mid_price_requires_both_bid_and_ask():
    assert mid_price(1600, 1640) == 1620
    assert mid_price(1600, None) is None
    assert mid_price(None, 1640) is None


def test_regular_session_state_labels_morning_session():
    tz = ZoneInfo("Asia/Taipei")
    payload = regular_session_state(datetime(2026, 6, 22, 9, 1, tzinfo=tz))
    assert payload["state"] == "regular"
    assert payload["session_date"] == "2026-06-22"


def test_after_hours_uses_taipei_time():
    tz = ZoneInfo("Asia/Taipei")

    assert not is_after_hours_now(datetime(2026, 6, 22, 9, 1, tzinfo=tz))
    assert is_after_hours_now(datetime(2026, 6, 22, 7, 30, tzinfo=tz))
    assert is_after_hours_now(datetime(2026, 6, 22, 15, 1, tzinfo=tz))


def test_night_preopen_is_between_six_and_regular_open():
    tz = ZoneInfo("Asia/Taipei")

    assert not is_night_preopen_now(datetime(2026, 6, 22, 5, 59, tzinfo=tz))
    assert is_night_preopen_now(datetime(2026, 6, 22, 6, 0, tzinfo=tz))
    assert is_night_preopen_now(datetime(2026, 6, 22, 8, 44, tzinfo=tz))
    assert not is_night_preopen_now(datetime(2026, 6, 22, 8, 45, tzinfo=tz))


def test_normalize_future_1m_candle_filters_regular_session():
    assert normalize_future_1m_candle({
        "date": "2026-06-22T08:44:00.000+08:00",
        "open": 1,
        "high": 1,
        "low": 1,
        "close": 1,
    }) is None

    candle = normalize_future_1m_candle({
        "date": "2026-06-22T08:45:00.000+08:00",
        "open": 23100,
        "high": 23150,
        "low": 23080,
        "close": 23120,
        "volume": 12,
        "average": 23110,
    })
    assert candle["session_date"] == "2026-06-22"
    assert candle["open"] == 23100
    assert candle["volume"] == 12
