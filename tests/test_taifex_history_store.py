from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from tools.taifex_history_store import (
    build_store,
    historical_tquote_payload,
    latest_fallback_payload,
    load_contracts,
    load_option_bars,
    load_option_chain,
    rpt_files,
    value_positions,
)


pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("pyarrow") is None,
    reason="pyarrow is required for Parquet store tests.",
)


def write_sample_rpt(raw_dir: Path) -> Path:
    path = raw_dir / "OptionsDaily_2026_06_18.rpt"
    lines = [
        " 成交日期,          商品代號,        履約價格,                                                      到期月份(週別),        買賣權別,      成交時間,          成交價格,         成交數量(B or S),     開盤集合競價 ",
        "---------- ---- ------- ---- ----------------------------------------------------- ---- ------- ---- ----- ---- --------- ---- -------- ---- --------- ",
        "20260617   ,    TXO ,    22600                                                 ,    202607  ,    C     ,    154501  ,    205      ,    2,     ",
        "20260618   ,    TXO ,    22600                                                 ,    202607  ,    C     ,    090001  ,    210      ,    3,     ",
        "20260618   ,    TXO ,    22600                                                 ,    202607  ,    C     ,    090055  ,    212      ,    1,     ",
        "20260618   ,    TXO ,    22600                                                 ,    202607  ,    P     ,    090100  ,    190      ,    4,     ",
        "20260618   ,    TXO ,    22700                                                 ,    202607W1,    C     ,    090200  ,    180      ,    9,     ",
        "20260618   ,    CBO ,    17.5                                                  ,    202607  ,    P     ,    094315  ,    .08      ,    1,     ",
    ]
    raw_dir.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="cp950")
    return path


def test_builds_parquet_store_and_query_payloads(tmp_path):
    raw_dir = tmp_path / "raw"
    store_dir = tmp_path / "store"
    write_sample_rpt(raw_dir)

    files = rpt_files(raw_dir, year=2026)
    result = build_store(files, store_dir=store_dir, with_minute_bars=True)

    assert result["file_count"] == 1
    assert result["trade_count"] == 4
    assert result["daily_series_rows"] == 2
    assert result["minute_bar_rows"] == 3
    assert (store_dir / "contracts.parquet").exists()
    assert (store_dir / "contract_strikes.parquet").exists()
    assert (store_dir / "manifest.json").exists()

    contracts = load_contracts(store_dir)
    assert len(contracts) == 1
    assert contracts[0]["contract_month"] == "202607"
    assert contracts[0]["available_strikes"] == [22600]
    assert contracts[0]["total_volume"] == 10

    chain = load_option_chain(store_dir, contract_month="202607", trading_date="2026-06-18")
    assert len(chain) == 2
    call = chain[chain["cp"] == "C"].iloc[0]
    assert call["open"] == 205
    assert call["high"] == 212
    assert call["low"] == 205
    assert call["last"] == 212
    assert call["volume"] == 6
    assert call["first_trade_at"] == "2026-06-17T15:45:01"
    assert call["last_trade_at"] == "2026-06-18T09:00:55"

    tquote = historical_tquote_payload(store_dir, contract_month="202607", trading_date="2026-06-18")
    assert tquote["source"]["type"] == "taifex_rebuild"
    assert tquote["rows"][0]["call"]["bid"] is None
    assert tquote["rows"][0]["call"]["mark_source"] == "taifex_rebuild_last"
    assert tquote["rows"][0]["put"]["last"] == 190

    fallback = latest_fallback_payload(store_dir, contract_month="202607")
    assert fallback["stale"] is True
    assert fallback["status"] == "stale_fallback"

    valuation = value_positions(
        store_dir,
        [{"strike": 22600, "cp": "C", "side": "long", "qty": 2, "entry_price": 200}],
        contract_month="202607",
        trading_date="2026-06-18",
    )
    assert valuation["positions"][0]["mark"]["source"] == "taifex_rebuild_last"
    assert valuation["positions"][0]["mark"]["price"] == 212
    assert valuation["positions"][0]["pnl"]["unrealized_twd"] == 1200

    minute_bars = load_option_bars(
        store_dir,
        contract_month="202607",
        trading_date="2026-06-18",
        strike=22600,
        cp="C",
        freq="1m",
    )
    assert [bar["minute"] for bar in minute_bars] == [
        "2026-06-17T15:45:00",
        "2026-06-18T09:00:00",
    ]
