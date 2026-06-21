from __future__ import annotations

import csv
from itertools import islice
from pathlib import Path

import pytest

from tests.taifex_rpt_history_rebuild import (
    CONTRACT_SUMMARY_COLUMNS,
    DAILY_SERIES_COLUMNS,
    file_trading_date,
    iter_option_trades,
    rebuild_daily_series,
    rpt_files,
    summarize_contracts,
    write_csv,
)


RAW_DIR = Path(__file__).resolve().parents[1] / "data" / "raw" / "taifex_rpt"


def require_2026_rpt_files() -> list[Path]:
    files = rpt_files(RAW_DIR, year=2026)
    if not files:
        pytest.skip("TAIFEX raw RPT files are local ignored data and are not available here.")
    return files


def test_2026_options_daily_rpt_files_are_available_and_ordered():
    files = require_2026_rpt_files()
    dates = [file_trading_date(path) for path in files]

    assert len(files) >= 100
    assert dates == sorted(dates)
    assert dates[0] == "2026-01-02"
    assert dates[-1] >= "2026-06-18"


def test_rpt_parser_decodes_cp950_trade_rows_and_cross_day_session_date():
    files = require_2026_rpt_files()
    latest = files[-1]
    trades = list(islice(iter_option_trades(latest, product="TXO"), 1000))

    assert trades
    assert {trade.product for trade in trades} == {"TXO"}
    assert all(trade.trading_date == file_trading_date(latest) for trade in trades)
    assert all(trade.cp in {"C", "P"} for trade in trades)
    assert all(trade.price > 0 for trade in trades)
    assert all(trade.volume > 0 for trade in trades)
    assert any(trade.trade_calendar_date != trade.trading_date for trade in trades)


def test_rebuilds_daily_txo_monthly_ohlcv_history_from_one_rpt(tmp_path):
    files = require_2026_rpt_files()
    latest = files[-1]
    rows, stats = rebuild_daily_series([latest], product="TXO", monthly_only=True)

    assert stats.file_count == 1
    assert stats.trade_count > 0
    assert stats.series_count == len(rows)
    assert rows
    assert {row["product"] for row in rows} == {"TXO"}
    assert {row["contract_kind"] for row in rows} == {"monthly"}
    assert {row["trading_date"] for row in rows} == {file_trading_date(latest)}
    assert all(row["open"] and row["high"] and row["low"] and row["close"] for row in rows)
    assert all(int(row["volume"]) > 0 for row in rows)
    assert all(int(row["trade_count"]) > 0 for row in rows)

    daily_path = tmp_path / "txo_monthly_daily_series.csv"
    write_csv(daily_path, rows, DAILY_SERIES_COLUMNS)
    with daily_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        assert reader.fieldnames == DAILY_SERIES_COLUMNS
        assert next(reader)["product"] == "TXO"

    contracts = summarize_contracts(rows)
    contracts_path = tmp_path / "txo_monthly_contracts.csv"
    write_csv(contracts_path, contracts, CONTRACT_SUMMARY_COLUMNS)
    with contracts_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        assert reader.fieldnames == CONTRACT_SUMMARY_COLUMNS
        assert next(reader)["contract_kind"] == "monthly"
