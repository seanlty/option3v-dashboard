import os

from src import fugle_live
from src import main as main_module


def test_load_env_value_prefers_os_environ(monkeypatch, tmp_path):
    (tmp_path / ".env").write_text("FINMIND_TOKEN=dotenv-token\n", encoding="utf-8")
    monkeypatch.setattr(main_module, "ROOT", tmp_path)
    monkeypatch.setenv("FINMIND_TOKEN", "env-token")

    assert main_module.load_env_value("FINMIND_TOKEN") == "env-token"


def test_load_env_value_falls_back_to_dotenv(monkeypatch, tmp_path):
    key = "OPTION_DASHBOARD_TEST_TOKEN"
    (tmp_path / ".env").write_text(f"{key}=dotenv-token\n", encoding="utf-8")
    monkeypatch.setattr(main_module, "ROOT", tmp_path)
    monkeypatch.delenv(key, raising=False)

    try:
        assert main_module.load_env_value(key) == "dotenv-token"
    finally:
        os.environ.pop(key, None)


def test_fugle_load_env_token_prefers_os_environ(monkeypatch, tmp_path):
    (tmp_path / ".env").write_text("FUGLE_TOKEN=dotenv-token\n", encoding="utf-8")
    monkeypatch.setattr(fugle_live, "ROOT", tmp_path)
    monkeypatch.setenv("FUGLE_TOKEN", "env-token")

    assert fugle_live.load_env_token() == "env-token"


def test_fugle_load_env_token_falls_back_to_dotenv_alias(monkeypatch, tmp_path):
    (tmp_path / ".env").write_text("FUGLE_MARKETDATA_API_KEY=dotenv-token\n", encoding="utf-8")
    monkeypatch.setattr(fugle_live, "ROOT", tmp_path)
    for key in ("FUGLE_TOKEN", "FUGLE_API_KEY", "FUGLE_MARKETDATA_API_KEY"):
        monkeypatch.delenv(key, raising=False)

    try:
        assert fugle_live.load_env_token() == "dotenv-token"
    finally:
        for key in ("FUGLE_TOKEN", "FUGLE_API_KEY", "FUGLE_MARKETDATA_API_KEY"):
            os.environ.pop(key, None)


def test_main_runs(capsys):
    main_module.main(["--smoke"])
    captured = capsys.readouterr()
    assert "quant-assistant project is ready." in captured.out


def test_fugle_service_auto_switches_to_afterhours(monkeypatch):
    calls = []

    def fake_universe(token, contract, strike_count, after_hours):
        calls.append(after_hours)
        suffix = "night" if after_hours else "day"
        return {
            "settlement_date": "2026-07-15",
            "future_symbol": "TXFG6",
            "future_price": 23000,
            "selected_strikes": [23000],
            "selected_symbols": [f"TXO23000{suffix}"],
            "symbol_meta": {
                f"TXO23000{suffix}": {
                    "symbol": f"TXO23000{suffix}",
                    "strike": 23000,
                    "side": "call",
                },
            },
        }

    monkeypatch.setattr(fugle_live, "is_after_hours_now", lambda: False)
    monkeypatch.setattr(fugle_live, "prepare_universe", fake_universe)

    service = fugle_live.FugleLiveTQuoteService("token", contract="202607")
    service.started = True
    service.state.update(
        after_hours=False,
        status="live",
        books={"old": {"bidPrice": 1}},
        aggregates={"old": {"lastPrice": 1}},
        vix_series=[{"time": "old", "value": 1}],
    )

    monkeypatch.setattr(fugle_live, "is_after_hours_now", lambda: True)
    service._refresh_session_if_needed()

    snapshot = service.state.snapshot()
    assert calls == [True]
    assert service.after_hours is True
    assert snapshot["after_hours"] is True
    assert snapshot["selected_symbols"] == ["TXO23000night"]
    assert service.state.books == {}
    assert service.state.aggregates == {}
    assert service.force_next_rest_probe is True


def test_fugle_service_fixed_regular_session_does_not_auto_switch(monkeypatch):
    monkeypatch.setattr(fugle_live, "is_after_hours_now", lambda: True)
    service = fugle_live.FugleLiveTQuoteService("token", contract="202607", after_hours=False)
    service.started = True

    service._refresh_session_if_needed()

    assert service.after_hours is False


def test_fugle_service_preopen_returns_last_night_snapshot(monkeypatch):
    def fail_prepare(*args, **kwargs):
        raise AssertionError("pre-open cache should be returned before preparing a new universe")

    monkeypatch.setattr(fugle_live, "is_night_preopen_now", lambda: True)
    monkeypatch.setattr(fugle_live, "prepare_universe", fail_prepare)
    service = fugle_live.FugleLiveTQuoteService("token", contract="202607")
    night_payload = {
        "status": "live",
        "after_hours": True,
        "contract": "202607",
        "settlement_date": "2026-07-15",
        "future_symbol": "TXFG6",
        "future_price": 23000,
        "risk_free_rate": 0.015,
        "time_to_expiry_years": 0.05,
        "selected_symbols": ["TXO23000G6"],
        "selected_strikes": [23000],
        "last_event_at": "2026-06-23T05:00:00+08:00",
        "last_book_at": "2026-06-23T05:00:00+08:00",
        "last_aggregate_at": "2026-06-23T05:00:00+08:00",
        "rows": [{
            "strike": 23000,
            "call": {
                "symbol": "TXO23000G6",
                "bid": 100,
                "ask": 110,
                "last": 105,
            },
        }],
    }
    service.snapshot_store.write(fugle_live.quote_snapshot_from_tquote_payload(night_payload, source_type="fugle_live"))

    snapshot = service.quote_snapshot()

    assert snapshot["session"] == "night"
    assert snapshot["stale"] is True
    assert snapshot["source"]["type"] == "fugle_cache"
    assert "08:45" in snapshot["error"]
