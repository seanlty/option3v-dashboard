"""Build and query a Parquet TAIFEX option history store.

The store is intentionally tool-side demo infrastructure.  It converts TAIFEX
OptionsDaily RPT trade prints into directly queryable Parquet tables for:

* contract availability summaries
* TXO option-chain daily OHLCV
* historical last-price position valuation
* stale fallback T-quote payloads
* option trade daily/minute bars
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RAW_DIR = ROOT / "data" / "raw" / "taifex_rpt"
DEFAULT_STORE_DIR = ROOT / "data" / "processed" / "taifex_history_parquet"
SCHEMA_VERSION = "0.1.0"
POINT_VALUE_TXO = 50
RPT_FILE_RE = re.compile(r"OptionsDaily_(\d{4})_(\d{2})_(\d{2})\.rpt$")
MONTHLY_CONTRACT_RE = re.compile(r"^\d{6}$")

DAILY_SERIES_COLUMNS = [
    "trading_date",
    "product",
    "contract_month",
    "contract_kind",
    "strike",
    "strike_text",
    "cp",
    "open",
    "high",
    "low",
    "close",
    "last",
    "volume",
    "trade_count",
    "first_trade_at",
    "last_trade_at",
    "source_file_count",
    "source_files",
    "updated_at",
]
MINUTE_BAR_COLUMNS = [
    "trading_date",
    "session",
    "minute",
    "product",
    "contract_month",
    "contract_kind",
    "strike",
    "strike_text",
    "cp",
    "open",
    "high",
    "low",
    "close",
    "last",
    "volume",
    "trade_count",
    "first_trade_at",
    "last_trade_at",
    "source_file_count",
    "source_files",
    "updated_at",
]
CONTRACT_COLUMNS = [
    "product",
    "contract_month",
    "contract_kind",
    "first_trading_date",
    "last_trading_date",
    "trading_days",
    "strike_count",
    "option_series",
    "strike_min",
    "strike_max",
    "available_strikes_json",
    "total_volume",
    "total_trade_count",
    "updated_at",
]
CONTRACT_STRIKE_COLUMNS = [
    "product",
    "contract_month",
    "contract_kind",
    "strike",
    "strike_text",
    "first_trading_date",
    "last_trading_date",
    "call_volume",
    "put_volume",
    "total_volume",
    "total_trade_count",
    "updated_at",
]


@dataclass(frozen=True)
class OptionTrade:
    trading_date: str
    trade_calendar_date: str
    product: str
    strike: float
    strike_text: str
    contract_month: str
    contract_kind: str
    cp: str
    trade_time: str
    trade_at: str
    session: str
    price: float
    volume: int
    opening_auction: str
    source_file: str


@dataclass
class SeriesAccumulator:
    trading_date: str
    product: str
    contract_month: str
    contract_kind: str
    strike: float
    strike_text: str
    cp: str
    session: str | None = None
    minute: str | None = None
    open: float | None = None
    high: float | None = None
    low: float | None = None
    close: float | None = None
    volume: int = 0
    trade_count: int = 0
    first_trade_at: str = ""
    last_trade_at: str = ""
    source_files: set[str] | None = None

    def update(self, trade: OptionTrade) -> None:
        if self.source_files is None:
            self.source_files = set()
        if self.open is None or trade.trade_at < self.first_trade_at:
            self.open = trade.price
            self.first_trade_at = trade.trade_at
        if self.close is None or trade.trade_at >= self.last_trade_at:
            self.close = trade.price
            self.last_trade_at = trade.trade_at
        self.high = trade.price if self.high is None else max(self.high, trade.price)
        self.low = trade.price if self.low is None else min(self.low, trade.price)
        self.volume += trade.volume
        self.trade_count += 1
        self.source_files.add(trade.source_file)

    def to_daily_row(self, updated_at: str) -> dict[str, Any]:
        return {
            "trading_date": self.trading_date,
            "product": self.product,
            "contract_month": self.contract_month,
            "contract_kind": self.contract_kind,
            "strike": self.strike,
            "strike_text": self.strike_text,
            "cp": self.cp,
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "last": self.close,
            "volume": self.volume,
            "trade_count": self.trade_count,
            "first_trade_at": self.first_trade_at,
            "last_trade_at": self.last_trade_at,
            "source_file_count": len(self.source_files or set()),
            "source_files": ";".join(sorted(self.source_files or set())),
            "updated_at": updated_at,
        }

    def to_minute_row(self, updated_at: str) -> dict[str, Any]:
        return {
            "trading_date": self.trading_date,
            "session": self.session or "",
            "minute": self.minute or "",
            "product": self.product,
            "contract_month": self.contract_month,
            "contract_kind": self.contract_kind,
            "strike": self.strike,
            "strike_text": self.strike_text,
            "cp": self.cp,
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "last": self.close,
            "volume": self.volume,
            "trade_count": self.trade_count,
            "first_trade_at": self.first_trade_at,
            "last_trade_at": self.last_trade_at,
            "source_file_count": len(self.source_files or set()),
            "source_files": ";".join(sorted(self.source_files or set())),
            "updated_at": updated_at,
        }


@dataclass
class BuildStats:
    file_count: int = 0
    trade_count: int = 0
    daily_series_rows: int = 0
    minute_bar_rows: int = 0
    first_trading_date: str = ""
    last_trading_date: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "file_count": self.file_count,
            "trade_count": self.trade_count,
            "daily_series_rows": self.daily_series_rows,
            "minute_bar_rows": self.minute_bar_rows,
            "first_trading_date": self.first_trading_date,
            "last_trading_date": self.last_trading_date,
        }


def ensure_parquet_engine() -> None:
    if importlib.util.find_spec("pyarrow") is None:
        raise RuntimeError("pyarrow is required for Parquet history store support.")


def now_utc_text() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def file_trading_date(path: Path) -> str:
    match = RPT_FILE_RE.match(path.name)
    if not match:
        raise ValueError(f"Unexpected RPT file name: {path.name}")
    year, month, day = match.groups()
    return f"{year}-{month}-{day}"


def rpt_files(
    raw_dir: Path = DEFAULT_RAW_DIR,
    *,
    year: int = 2026,
    start_date: str | None = None,
    through_date: str | None = None,
) -> list[Path]:
    selected = []
    for path in sorted(raw_dir.glob(f"OptionsDaily_{year}_*.rpt")):
        trading_date = file_trading_date(path)
        if start_date and trading_date < start_date:
            continue
        if through_date and trading_date > through_date:
            continue
        selected.append(path)
    return selected


def iter_rpt_trades(
    path: Path,
    *,
    product: str | None = "TXO",
    monthly_only: bool = True,
    contract_month: str | None = None,
) -> Iterable[OptionTrade]:
    trading_date = file_trading_date(path)
    product_filter = product.upper() if product else None
    with path.open("r", encoding="cp950", newline="") as handle:
        for line in handle:
            row = [cell.strip() for cell in line.rstrip("\r\n").split(",")]
            if should_skip_row(row):
                continue
            row_product = row[1].upper() if len(row) > 1 else ""
            row_contract = row[3].upper() if len(row) > 3 else ""
            if product_filter and row_product != product_filter:
                continue
            if monthly_only and not is_monthly_contract(row_contract):
                continue
            if contract_month and row_contract != contract_month:
                continue
            yield parse_rpt_trade(row, trading_date=trading_date, source_file=path.name)


def parse_rpt_trade(row: list[str], *, trading_date: str, source_file: str) -> OptionTrade:
    if len(row) < 8:
        raise ValueError(f"Expected at least 8 columns in {source_file}: {row!r}")
    trade_calendar_date = normalize_yyyymmdd(row[0])
    trade_time = normalize_hhmmss(row[5])
    strike_text = normalize_strike(row[2])
    contract = row[3].upper()
    cp = row[4].upper()
    if cp not in {"C", "P"}:
        raise ValueError(f"Unexpected call/put value in {source_file}: {cp!r}")
    return OptionTrade(
        trading_date=trading_date,
        trade_calendar_date=trade_calendar_date,
        product=row[1].upper(),
        strike=strike_to_float(strike_text),
        strike_text=strike_text,
        contract_month=contract,
        contract_kind=contract_kind(contract),
        cp=cp,
        trade_time=trade_time,
        trade_at=f"{trade_calendar_date}T{trade_time}",
        session=session_for(trading_date, trade_calendar_date, trade_time),
        price=parse_float(row[6], label="price", source_file=source_file),
        volume=parse_int(row[7], label="volume", source_file=source_file),
        opening_auction=row[8] if len(row) > 8 else "",
        source_file=source_file,
    )


def should_skip_row(row: list[str]) -> bool:
    if not row:
        return True
    first_cell = row[0].strip()
    return not first_cell or first_cell == "成交日期" or first_cell.startswith("-")


def rebuild_frames(
    files: Iterable[Path],
    *,
    product: str | None = "TXO",
    monthly_only: bool = True,
    contract_month: str | None = None,
    with_minute_bars: bool = False,
    updated_at: str | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, BuildStats]:
    updated_at = updated_at or now_utc_text()
    selected_files = list(files)
    stats = BuildStats(file_count=len(selected_files))
    if selected_files:
        stats.first_trading_date = file_trading_date(selected_files[0])
        stats.last_trading_date = file_trading_date(selected_files[-1])

    daily: dict[tuple[str, str, str, str, str], SeriesAccumulator] = {}
    minute: dict[tuple[str, str, str, str, str, str], SeriesAccumulator] = {}
    for path in selected_files:
        for trade in iter_rpt_trades(
            path,
            product=product,
            monthly_only=monthly_only,
            contract_month=contract_month,
        ):
            stats.trade_count += 1
            daily_key = (
                trade.trading_date,
                trade.product,
                trade.contract_month,
                trade.strike_text,
                trade.cp,
            )
            if daily_key not in daily:
                daily[daily_key] = SeriesAccumulator(
                    trading_date=trade.trading_date,
                    product=trade.product,
                    contract_month=trade.contract_month,
                    contract_kind=trade.contract_kind,
                    strike=trade.strike,
                    strike_text=trade.strike_text,
                    cp=trade.cp,
                )
            daily[daily_key].update(trade)

            if with_minute_bars:
                minute_at = trade.trade_at[:16] + ":00"
                minute_key = (
                    trade.trading_date,
                    minute_at,
                    trade.product,
                    trade.contract_month,
                    trade.strike_text,
                    trade.cp,
                )
                if minute_key not in minute:
                    minute[minute_key] = SeriesAccumulator(
                        trading_date=trade.trading_date,
                        session=trade.session,
                        minute=minute_at,
                        product=trade.product,
                        contract_month=trade.contract_month,
                        contract_kind=trade.contract_kind,
                        strike=trade.strike,
                        strike_text=trade.strike_text,
                        cp=trade.cp,
                    )
                minute[minute_key].update(trade)

    daily_rows = [item.to_daily_row(updated_at) for item in daily.values()]
    daily_df = pd.DataFrame(daily_rows, columns=DAILY_SERIES_COLUMNS)
    if not daily_df.empty:
        daily_df = normalize_daily_frame(daily_df)
    minute_rows = [item.to_minute_row(updated_at) for item in minute.values()]
    minute_df = pd.DataFrame(minute_rows, columns=MINUTE_BAR_COLUMNS)
    if not minute_df.empty:
        minute_df = normalize_minute_frame(minute_df)
    stats.daily_series_rows = len(daily_df)
    stats.minute_bar_rows = len(minute_df)
    return daily_df, minute_df, stats


def build_store(
    files: Iterable[Path],
    *,
    store_dir: Path = DEFAULT_STORE_DIR,
    product: str | None = "TXO",
    monthly_only: bool = True,
    contract_month: str | None = None,
    with_minute_bars: bool = False,
) -> dict[str, Any]:
    ensure_parquet_engine()
    selected_files = list(files)
    if not selected_files:
        raise ValueError("No RPT files selected.")
    daily_df, minute_df, stats = rebuild_frames(
        selected_files,
        product=product,
        monthly_only=monthly_only,
        contract_month=contract_month,
        with_minute_bars=with_minute_bars,
    )
    replace_dates = sorted(daily_df["trading_date"].unique().tolist()) if not daily_df.empty else []
    write_partitioned_table(
        store_dir,
        "option_daily_series",
        daily_df,
        partition_dates=replace_dates,
        key_columns=["trading_date", "product", "contract_month", "strike_text", "cp"],
        sort_columns=["trading_date", "product", "contract_month", "strike", "cp"],
    )
    if with_minute_bars and not minute_df.empty:
        write_partitioned_table(
            store_dir,
            "option_minute_bars",
            minute_df,
            partition_dates=replace_dates,
            key_columns=["trading_date", "minute", "product", "contract_month", "strike_text", "cp"],
            sort_columns=["trading_date", "minute", "product", "contract_month", "strike", "cp"],
        )
    contracts_df, strikes_df = rebuild_contract_tables(store_dir)
    write_root_table(store_dir, "contracts", contracts_df)
    write_root_table(store_dir, "contract_strikes", strikes_df)
    manifest = write_manifest(
        store_dir,
        selected_files,
        stats,
        with_minute_bars=with_minute_bars,
        contracts_rows=len(contracts_df),
        contract_strikes_rows=len(strikes_df),
    )
    return {
        **stats.to_dict(),
        "store_dir": str(store_dir),
        "contracts_rows": len(contracts_df),
        "contract_strikes_rows": len(strikes_df),
        "manifest": manifest,
    }


def normalize_daily_frame(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    for column in ["strike", "open", "high", "low", "close", "last"]:
        df[column] = df[column].astype("float64")
    for column in ["volume", "trade_count", "source_file_count"]:
        df[column] = df[column].astype("int64")
    return df.sort_values(["trading_date", "product", "contract_month", "strike", "cp"]).reset_index(drop=True)


def normalize_minute_frame(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    for column in ["strike", "open", "high", "low", "close", "last"]:
        df[column] = df[column].astype("float64")
    for column in ["volume", "trade_count", "source_file_count"]:
        df[column] = df[column].astype("int64")
    return df.sort_values(["trading_date", "minute", "product", "contract_month", "strike", "cp"]).reset_index(drop=True)


def write_partitioned_table(
    store_dir: Path,
    table: str,
    df: pd.DataFrame,
    *,
    partition_dates: list[str],
    key_columns: list[str],
    sort_columns: list[str],
) -> None:
    if df.empty:
        return
    table_dir = store_dir / table
    table_dir.mkdir(parents=True, exist_ok=True)
    for yyyy_mm, group in df.groupby(df["trading_date"].str.slice(0, 7)):
        year, month = yyyy_mm.split("-")
        partition_dir = table_dir / f"year={year}" / f"month={month}"
        path = partition_dir / "data.parquet"
        partition_dir.mkdir(parents=True, exist_ok=True)
        if path.exists():
            existing = pd.read_parquet(path)
            existing = existing[~existing["trading_date"].isin(partition_dates)]
            combined = pd.concat([existing, group], ignore_index=True)
        else:
            combined = group.copy()
        combined = combined.drop_duplicates(subset=key_columns, keep="last")
        combined = combined.sort_values(sort_columns).reset_index(drop=True)
        combined.to_parquet(path, index=False)


def write_root_table(store_dir: Path, table: str, df: pd.DataFrame) -> None:
    store_dir.mkdir(parents=True, exist_ok=True)
    df.to_parquet(store_dir / f"{table}.parquet", index=False)


def read_table(store_dir: Path, table: str) -> pd.DataFrame:
    root_path = store_dir / f"{table}.parquet"
    if root_path.exists():
        return pd.read_parquet(root_path)
    table_dir = store_dir / table
    paths = sorted(table_dir.glob("year=*/month=*/data.parquet"))
    if not paths:
        return pd.DataFrame()
    return pd.concat([pd.read_parquet(path) for path in paths], ignore_index=True)


def read_table_for_date(store_dir: Path, table: str, trading_date: str) -> pd.DataFrame:
    year, month = trading_date[:4], trading_date[5:7]
    path = store_dir / table / f"year={year}" / f"month={month}" / "data.parquet"
    if not path.exists():
        return pd.DataFrame()
    df = pd.read_parquet(path)
    return df[df["trading_date"] == trading_date].reset_index(drop=True)


def rebuild_contract_tables(store_dir: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    daily = read_table(store_dir, "option_daily_series")
    updated_at = now_utc_text()
    if daily.empty:
        return pd.DataFrame(columns=CONTRACT_COLUMNS), pd.DataFrame(columns=CONTRACT_STRIKE_COLUMNS)

    contract_rows = []
    group_cols = ["product", "contract_month", "contract_kind"]
    for key, group in daily.groupby(group_cols, sort=True):
        product, contract_month, kind = key
        strikes = sort_strike_texts(group["strike_text"].unique().tolist())
        contract_rows.append({
            "product": product,
            "contract_month": contract_month,
            "contract_kind": kind,
            "first_trading_date": group["trading_date"].min(),
            "last_trading_date": group["trading_date"].max(),
            "trading_days": int(group["trading_date"].nunique()),
            "strike_count": len(strikes),
            "option_series": int(group[["strike_text", "cp"]].drop_duplicates().shape[0]),
            "strike_min": float(group["strike"].min()),
            "strike_max": float(group["strike"].max()),
            "available_strikes_json": json.dumps(strikes, ensure_ascii=False),
            "total_volume": int(group["volume"].sum()),
            "total_trade_count": int(group["trade_count"].sum()),
            "updated_at": updated_at,
        })

    strike_rows = []
    for key, group in daily.groupby(["product", "contract_month", "contract_kind", "strike_text", "strike"], sort=True):
        product, contract_month, kind, strike_text, strike = key
        call_volume = int(group.loc[group["cp"] == "C", "volume"].sum())
        put_volume = int(group.loc[group["cp"] == "P", "volume"].sum())
        strike_rows.append({
            "product": product,
            "contract_month": contract_month,
            "contract_kind": kind,
            "strike": float(strike),
            "strike_text": strike_text,
            "first_trading_date": group["trading_date"].min(),
            "last_trading_date": group["trading_date"].max(),
            "call_volume": call_volume,
            "put_volume": put_volume,
            "total_volume": int(group["volume"].sum()),
            "total_trade_count": int(group["trade_count"].sum()),
            "updated_at": updated_at,
        })

    contracts = pd.DataFrame(contract_rows, columns=CONTRACT_COLUMNS)
    contracts = contracts.sort_values(["product", "contract_month"]).reset_index(drop=True)
    strikes = pd.DataFrame(strike_rows, columns=CONTRACT_STRIKE_COLUMNS)
    strikes = strikes.sort_values(["product", "contract_month", "strike"]).reset_index(drop=True)
    return contracts, strikes


def write_manifest(
    store_dir: Path,
    files: list[Path],
    stats: BuildStats,
    *,
    with_minute_bars: bool,
    contracts_rows: int,
    contract_strikes_rows: int,
) -> dict[str, Any]:
    manifest = {
        "schema": "taifex_history_parquet_store",
        "schema_version": SCHEMA_VERSION,
        "generated_at": now_utc_text(),
        "source": {
            "provider": "TAIFEX",
            "raw_type": "OptionsDaily RPT",
            "encoding": "cp950",
            "source_file_count": len(files),
            "source_files": [path.name for path in files],
        },
        "tables": {
            "contracts": {"path": "contracts.parquet", "rows": contracts_rows},
            "contract_strikes": {"path": "contract_strikes.parquet", "rows": contract_strikes_rows},
            "option_daily_series": {"path": "option_daily_series/year=*/month=*/data.parquet", "rows": stats.daily_series_rows},
            "option_minute_bars": {
                "path": "option_minute_bars/year=*/month=*/data.parquet",
                "rows": stats.minute_bar_rows,
                "enabled": with_minute_bars,
            },
        },
        "stats": stats.to_dict(),
        "limitations": [
            "RPT files are trade prints, not order books.",
            "bid/ask, bid_size, and ask_size cannot be reconstructed from this source alone.",
            "historical valuation uses last/close with mark_source=taifex_rebuild_last.",
        ],
    }
    store_dir.mkdir(parents=True, exist_ok=True)
    (store_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def load_contracts(store_dir: Path, *, product: str | None = "TXO") -> list[dict[str, Any]]:
    df = read_table(store_dir, "contracts")
    if df.empty:
        return []
    if product:
        df = df[df["product"] == product.upper()]
    records = df.sort_values(["product", "contract_month"]).to_dict("records")
    for record in records:
        record["available_strikes"] = json.loads(record.pop("available_strikes_json"))
    return records


def load_option_chain(store_dir: Path, *, contract_month: str, trading_date: str) -> pd.DataFrame:
    df = read_table_for_date(store_dir, "option_daily_series", trading_date)
    if df.empty:
        return df
    return df[(df["contract_month"] == contract_month) & (df["product"] == "TXO")].reset_index(drop=True)


def historical_tquote_payload(
    store_dir: Path,
    *,
    contract_month: str,
    trading_date: str,
    stale: bool = False,
    status: str = "ok",
) -> dict[str, Any]:
    chain = load_option_chain(store_dir, contract_month=contract_month, trading_date=trading_date)
    rows = option_chain_records(chain)
    snapshot_at = latest_time_from_chain(chain)
    return {
        "schema": "quote_snapshot",
        "schema_version": SCHEMA_VERSION,
        "exchange": "TAIFEX",
        "product": "TXO",
        "contract_month": contract_month,
        "trading_date": trading_date,
        "session": "closed" if stale else "historical",
        "snapshot_at": snapshot_at,
        "status": status if rows else "empty",
        "stale": stale,
        "source": {"type": "taifex_rebuild", "provider": "TAIFEX OptionsDaily RPT"},
        "rows": rows,
        "metadata": {
            "row_count": len(rows),
            "mark_source": "taifex_rebuild_last",
            "limitations": ["trade-only rebuild", "bid/ask unavailable"],
        },
    }


def latest_fallback_payload(
    store_dir: Path,
    *,
    contract_month: str | None = None,
    before_date: str | None = None,
) -> dict[str, Any]:
    daily = read_table(store_dir, "option_daily_series")
    if daily.empty:
        return {
            "schema": "quote_snapshot",
            "schema_version": SCHEMA_VERSION,
            "status": "empty",
            "stale": True,
            "source": {"type": "taifex_rebuild", "provider": "TAIFEX OptionsDaily RPT"},
            "rows": [],
        }
    if before_date:
        daily = daily[daily["trading_date"] <= before_date]
    if contract_month:
        daily = daily[daily["contract_month"] == contract_month]
    if daily.empty:
        return {
            "schema": "quote_snapshot",
            "schema_version": SCHEMA_VERSION,
            "status": "empty",
            "stale": True,
            "source": {"type": "taifex_rebuild", "provider": "TAIFEX OptionsDaily RPT"},
            "rows": [],
        }
    trading_date = str(daily["trading_date"].max())
    day = daily[daily["trading_date"] == trading_date]
    if not contract_month:
        volumes = day.groupby("contract_month")["volume"].sum().sort_values(ascending=False)
        contract_month = str(volumes.index[0])
    return historical_tquote_payload(
        store_dir,
        contract_month=contract_month,
        trading_date=trading_date,
        stale=True,
        status="stale_fallback",
    )


def option_chain_records(chain: pd.DataFrame) -> list[dict[str, Any]]:
    if chain.empty:
        return []
    output = []
    for strike_text, group in chain.groupby("strike_text", sort=False):
        strike = float(group["strike"].iloc[0])
        row = {"strike": format_strike_value(strike, strike_text), "call": None, "put": None}
        for _, item in group.iterrows():
            leg = daily_row_to_leg(item)
            if item["cp"] == "C":
                row["call"] = leg
            elif item["cp"] == "P":
                row["put"] = leg
        output.append(row)
    output.sort(key=lambda row: strike_to_float(str(row["strike"])))
    return output


def daily_row_to_leg(row: pd.Series) -> dict[str, Any]:
    option_type = "call" if row["cp"] == "C" else "put"
    return {
        "type": option_type,
        "cp": row["cp"],
        "strike": format_strike_value(float(row["strike"]), str(row["strike_text"])),
        "bid": None,
        "ask": None,
        "bid_size": None,
        "ask_size": None,
        "open": float(row["open"]),
        "high": float(row["high"]),
        "low": float(row["low"]),
        "close": float(row["close"]),
        "last": float(row["last"]),
        "volume": int(row["volume"]),
        "trade_count": int(row["trade_count"]),
        "first_trade_at": row["first_trade_at"],
        "last_trade_at": row["last_trade_at"],
        "mark_source": "taifex_rebuild_last",
        "quality": {
            "status": "trade_only",
            "stale": True,
            "warnings": ["bid/ask unavailable from TAIFEX OptionsDaily RPT"],
        },
    }


def value_positions(
    store_dir: Path,
    positions: list[dict[str, Any]],
    *,
    contract_month: str,
    trading_date: str,
    stale: bool = True,
) -> dict[str, Any]:
    chain = load_option_chain(store_dir, contract_month=contract_month, trading_date=trading_date)
    lookup = {
        (str(row["strike_text"]), str(row["cp"])): row
        for _, row in chain.iterrows()
    }
    results = []
    total_pnl = 0.0
    missing = 0
    for index, position in enumerate(positions, start=1):
        strike_text = normalize_strike(str(position["strike"]))
        cp = normalize_cp(position.get("cp") or position.get("option_type"))
        side = normalize_side(str(position.get("side", "long")))
        qty = int(position.get("qty", 1))
        multiplier = int(position.get("multiplier", POINT_VALUE_TXO))
        entry_price = float(position.get("entry_price", 0))
        row = lookup.get((strike_text, cp))
        mark_price = float(row["last"]) if row is not None else None
        mark_at = str(row["last_trade_at"]) if row is not None else ""
        sign = 1 if side == "long" else -1
        pnl_points = (mark_price - entry_price) * sign if mark_price is not None else None
        pnl_twd = pnl_points * qty * multiplier if pnl_points is not None else None
        if pnl_twd is None:
            missing += 1
        else:
            total_pnl += pnl_twd
        results.append({
            "position_id": position.get("position_id") or f"demo-{index:03d}",
            "book": position.get("book", "demo"),
            "product": "TXO",
            "contract_month": contract_month,
            "strike": format_strike_value(strike_to_float(strike_text), strike_text),
            "cp": cp,
            "option_type": "call" if cp == "C" else "put",
            "side": side,
            "qty": qty,
            "entry_price": entry_price,
            "multiplier": multiplier,
            "mark": {
                "price": mark_price,
                "source": "taifex_rebuild_last" if mark_price is not None else "missing",
                "at": mark_at,
                "stale": stale,
            },
            "pnl": {
                "points": round(pnl_points, 4) if pnl_points is not None else None,
                "unrealized_twd": round(pnl_twd, 2) if pnl_twd is not None else None,
            },
            "quality": {
                "status": "ok" if mark_price is not None else "missing_mark",
                "warnings": ["uses historical last/close, not bid/ask mid"] if mark_price is not None else ["missing historical mark"],
            },
        })
    return {
        "schema": "position_valuation",
        "schema_version": SCHEMA_VERSION,
        "as_of": latest_time_from_chain(chain),
        "product": "TXO",
        "contract_month": contract_month,
        "trading_date": trading_date,
        "source": {"type": "taifex_rebuild", "mark_source": "taifex_rebuild_last"},
        "positions": results,
        "totals": {
            "unrealized_twd": round(total_pnl, 2),
            "position_count": len(results),
            "missing_position_count": missing,
        },
        "quality": {
            "status": "ok" if missing == 0 else "partial",
            "stale": stale,
        },
    }


def load_option_bars(
    store_dir: Path,
    *,
    contract_month: str,
    trading_date: str,
    strike: str | float,
    cp: str,
    freq: str = "1m",
) -> list[dict[str, Any]]:
    table = "option_daily_series" if freq == "1d" else "option_minute_bars"
    df = read_table_for_date(store_dir, table, trading_date)
    if df.empty:
        return []
    strike_text = normalize_strike(str(strike))
    filtered = df[
        (df["contract_month"] == contract_month)
        & (df["strike_text"] == strike_text)
        & (df["cp"] == normalize_cp(cp))
    ].copy()
    if filtered.empty:
        return []
    sort_cols = ["minute"] if freq != "1d" and "minute" in filtered.columns else ["trading_date"]
    filtered = filtered.sort_values(sort_cols)
    return filtered.to_dict("records")


def latest_time_from_chain(chain: pd.DataFrame) -> str:
    if chain.empty:
        return ""
    values = [value for value in chain["last_trade_at"].tolist() if value]
    return max(values) if values else ""


def normalize_yyyymmdd(value: str) -> str:
    digits = value.strip()
    if not re.fullmatch(r"\d{8}", digits):
        raise ValueError(f"Unexpected date value: {value!r}")
    return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"


def normalize_hhmmss(value: str) -> str:
    digits = value.strip().zfill(6)
    if not re.fullmatch(r"\d{6}", digits):
        raise ValueError(f"Unexpected time value: {value!r}")
    return f"{digits[:2]}:{digits[2:4]}:{digits[4:6]}"


def normalize_strike(value: str) -> str:
    text = value.strip()
    if "." not in text:
        return text
    return text.rstrip("0").rstrip(".")


def strike_to_float(value: str) -> float:
    return float(value)


def format_strike_value(strike: float, strike_text: str | None = None) -> int | float:
    if strike_text and "." not in strike_text and strike.is_integer():
        return int(strike)
    return int(strike) if strike.is_integer() else strike


def sort_strike_texts(values: list[str]) -> list[int | float]:
    return [format_strike_value(strike_to_float(value), value) for value in sorted(values, key=strike_to_float)]


def parse_float(value: str, *, label: str, source_file: str) -> float:
    try:
        return float(value.strip())
    except ValueError as exc:
        raise ValueError(f"Unexpected {label} value in {source_file}: {value!r}") from exc


def parse_int(value: str, *, label: str, source_file: str) -> int:
    try:
        return int(value.strip())
    except ValueError as exc:
        raise ValueError(f"Unexpected {label} value in {source_file}: {value!r}") from exc


def is_monthly_contract(contract: str) -> bool:
    return bool(MONTHLY_CONTRACT_RE.fullmatch(contract.strip()))


def contract_kind(contract: str) -> str:
    return "monthly" if is_monthly_contract(contract) else "weekly"


def session_for(trading_date: str, trade_calendar_date: str, trade_time: str) -> str:
    if trade_calendar_date != trading_date:
        return "night"
    hour = int(trade_time[:2])
    return "night" if hour >= 15 else "day"


def normalize_cp(value: Any) -> str:
    text = str(value or "").upper()
    if text in {"C", "CALL"}:
        return "C"
    if text in {"P", "PUT"}:
        return "P"
    raise ValueError(f"Unknown option type: {value!r}")


def normalize_side(value: str) -> str:
    text = value.lower()
    if text in {"long", "buy", "b"}:
        return "long"
    if text in {"short", "sell", "s"}:
        return "short"
    raise ValueError(f"Unknown side: {value!r}")


def finite_json(value: Any) -> Any:
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    if isinstance(value, dict):
        return {key: finite_json(item) for key, item in value.items()}
    if isinstance(value, list):
        return [finite_json(item) for item in value]
    return value


def print_json(payload: Any) -> None:
    print(json.dumps(finite_json(payload), ensure_ascii=False, indent=2))


def sample_positions_from_chain(store_dir: Path, *, contract_month: str, trading_date: str) -> list[dict[str, Any]]:
    chain = load_option_chain(store_dir, contract_month=contract_month, trading_date=trading_date)
    if chain.empty:
        return []
    top = chain.sort_values("volume", ascending=False).head(2)
    positions = []
    for index, (_, row) in enumerate(top.iterrows(), start=1):
        entry_price = max(0.5, float(row["last"]) - (5 * index))
        positions.append({
            "position_id": f"sample-{index:03d}",
            "book": "demo",
            "strike": row["strike_text"],
            "cp": row["cp"],
            "side": "long" if index == 1 else "short",
            "qty": index,
            "entry_price": entry_price,
            "multiplier": POINT_VALUE_TXO,
        })
    return positions


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    build = subparsers.add_parser("build", help="Build or update the Parquet store from RPT files.")
    build.add_argument("--raw-dir", type=Path, default=DEFAULT_RAW_DIR)
    build.add_argument("--store-dir", type=Path, default=DEFAULT_STORE_DIR)
    build.add_argument("--year", type=int, default=2026)
    build.add_argument("--start-date")
    build.add_argument("--through-date")
    build.add_argument("--product", default="TXO")
    build.add_argument("--contract-month")
    build.add_argument("--monthly-only", action=argparse.BooleanOptionalAction, default=True)
    build.add_argument("--with-minute-bars", action="store_true")
    build.add_argument("--limit-files", type=int)

    contracts = subparsers.add_parser("contracts", help="Print /api/history/contracts demo payload.")
    contracts.add_argument("--store-dir", type=Path, default=DEFAULT_STORE_DIR)
    contracts.add_argument("--product", default="TXO")

    chain = subparsers.add_parser("chain", help="Print historical option-chain T-quote payload.")
    chain.add_argument("--store-dir", type=Path, default=DEFAULT_STORE_DIR)
    chain.add_argument("--contract-month", required=True)
    chain.add_argument("--trading-date", required=True)

    fallback = subparsers.add_parser("fallback", help="Print latest stale fallback T-quote payload.")
    fallback.add_argument("--store-dir", type=Path, default=DEFAULT_STORE_DIR)
    fallback.add_argument("--contract-month")
    fallback.add_argument("--before-date")

    positions = subparsers.add_parser("positions", help="Print historical position valuation payload.")
    positions.add_argument("--store-dir", type=Path, default=DEFAULT_STORE_DIR)
    positions.add_argument("--contract-month", required=True)
    positions.add_argument("--trading-date", required=True)
    positions.add_argument("--positions-json")

    bars = subparsers.add_parser("bars", help="Print option daily or minute bars.")
    bars.add_argument("--store-dir", type=Path, default=DEFAULT_STORE_DIR)
    bars.add_argument("--contract-month", required=True)
    bars.add_argument("--trading-date", required=True)
    bars.add_argument("--strike", required=True)
    bars.add_argument("--cp", required=True)
    bars.add_argument("--freq", choices=["1m", "1d"], default="1m")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "build":
        files = rpt_files(
            args.raw_dir,
            year=args.year,
            start_date=args.start_date,
            through_date=args.through_date,
        )
        if args.limit_files:
            files = files[: args.limit_files]
        result = build_store(
            files,
            store_dir=args.store_dir,
            product=args.product,
            monthly_only=args.monthly_only,
            contract_month=args.contract_month,
            with_minute_bars=args.with_minute_bars,
        )
        print_json(result)
        return 0
    if args.command == "contracts":
        print_json({"contracts": load_contracts(args.store_dir, product=args.product)})
        return 0
    if args.command == "chain":
        print_json(historical_tquote_payload(args.store_dir, contract_month=args.contract_month, trading_date=args.trading_date))
        return 0
    if args.command == "fallback":
        print_json(latest_fallback_payload(args.store_dir, contract_month=args.contract_month, before_date=args.before_date))
        return 0
    if args.command == "positions":
        if args.positions_json:
            positions = json.loads(args.positions_json)
        else:
            positions = sample_positions_from_chain(
                args.store_dir,
                contract_month=args.contract_month,
                trading_date=args.trading_date,
            )
        print_json(value_positions(args.store_dir, positions, contract_month=args.contract_month, trading_date=args.trading_date))
        return 0
    if args.command == "bars":
        print_json({
            "bars": load_option_bars(
                args.store_dir,
                contract_month=args.contract_month,
                trading_date=args.trading_date,
                strike=args.strike,
                cp=args.cp,
                freq=args.freq,
            )
        })
        return 0
    raise SystemExit(f"Unhandled command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
