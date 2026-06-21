"""Test-side TAIFEX OptionsDaily RPT history rebuild utilities.

This module intentionally lives under tests so raw-file exploration can move
forward without changing production code.  The RPT files are trade prints, not
order books, so the rebuild output is OHLCV trade history rather than bid/ask
snapshots.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Iterator


ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = ROOT / "data" / "raw" / "taifex_rpt"
OUTPUT_DIR = ROOT / "data" / "processed" / "taifex_rpt_rebuild"
RPT_FILE_RE = re.compile(r"OptionsDaily_(\d{4})_(\d{2})_(\d{2})\.rpt$")
MONTHLY_CONTRACT_RE = re.compile(r"^\d{6}$")
DAILY_SERIES_COLUMNS = [
    "trading_date",
    "product",
    "contract_month",
    "contract_kind",
    "strike",
    "cp",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "trade_count",
    "first_trade_at",
    "last_trade_at",
    "source_file_count",
    "source_files",
]
CONTRACT_SUMMARY_COLUMNS = [
    "product",
    "contract_month",
    "contract_kind",
    "first_trading_date",
    "last_trading_date",
    "trading_days",
    "strikes",
    "option_series",
    "total_volume",
    "total_trade_count",
]


@dataclass(frozen=True)
class OptionTrade:
    trading_date: str
    trade_calendar_date: str
    product: str
    strike: str
    contract_month: str
    cp: str
    trade_time: str
    trade_at: str
    price: float
    volume: int
    opening_auction: str
    source_file: str


@dataclass
class DailySeries:
    trading_date: str
    product: str
    contract_month: str
    contract_kind: str
    strike: str
    cp: str
    open: float | None = None
    high: float | None = None
    low: float | None = None
    close: float | None = None
    volume: int = 0
    trade_count: int = 0
    first_trade_at: str = ""
    last_trade_at: str = ""
    source_files: set[str] = field(default_factory=set)

    def update(self, trade: OptionTrade) -> None:
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

    def to_row(self) -> dict[str, str | int]:
        return {
            "trading_date": self.trading_date,
            "product": self.product,
            "contract_month": self.contract_month,
            "contract_kind": self.contract_kind,
            "strike": self.strike,
            "cp": self.cp,
            "open": format_number(self.open),
            "high": format_number(self.high),
            "low": format_number(self.low),
            "close": format_number(self.close),
            "volume": self.volume,
            "trade_count": self.trade_count,
            "first_trade_at": self.first_trade_at,
            "last_trade_at": self.last_trade_at,
            "source_file_count": len(self.source_files),
            "source_files": ";".join(sorted(self.source_files)),
        }


@dataclass
class RebuildStats:
    file_count: int = 0
    trade_count: int = 0
    series_count: int = 0
    first_trading_date: str = ""
    last_trading_date: str = ""

    def to_dict(self) -> dict[str, int | str]:
        return {
            "file_count": self.file_count,
            "trade_count": self.trade_count,
            "series_count": self.series_count,
            "first_trading_date": self.first_trading_date,
            "last_trading_date": self.last_trading_date,
        }


def file_trading_date(path: Path) -> str:
    match = RPT_FILE_RE.match(path.name)
    if not match:
        raise ValueError(f"Unexpected RPT file name: {path.name}")
    year, month, day = match.groups()
    return f"{year}-{month}-{day}"


def rpt_files(
    raw_dir: Path = RAW_DIR,
    *,
    year: int = 2026,
    start_date: str | None = None,
    through_date: str | None = None,
) -> list[Path]:
    files = sorted(raw_dir.glob(f"OptionsDaily_{year}_*.rpt"))
    selected = []
    for path in files:
        trading_date = file_trading_date(path)
        if start_date and trading_date < start_date:
            continue
        if through_date and trading_date > through_date:
            continue
        selected.append(path)
    return selected


def iter_option_trades(
    path: Path,
    *,
    product: str | None = None,
    monthly_only: bool = False,
    contract_month: str | None = None,
) -> Iterator[OptionTrade]:
    trading_date = file_trading_date(path)
    product_filter = product.upper() if product else None
    with path.open("r", encoding="cp950", newline="") as handle:
        for line in handle:
            raw_row = [cell.strip() for cell in line.rstrip("\r\n").split(",")]
            if should_skip_row(raw_row):
                continue
            row_product = raw_row[1].upper() if len(raw_row) > 1 else ""
            row_contract = raw_row[3].upper() if len(raw_row) > 3 else ""
            if product_filter and row_product != product_filter:
                continue
            if monthly_only and not is_monthly_contract(row_contract):
                continue
            if contract_month and row_contract != contract_month:
                continue
            trade = parse_rpt_row(raw_row, trading_date=trading_date, source_file=path.name)
            if trade is None:
                continue
            yield trade


def parse_rpt_row(raw_row: list[str], *, trading_date: str, source_file: str) -> OptionTrade | None:
    if should_skip_row(raw_row):
        return None
    row = [cell.strip() for cell in raw_row]
    if len(row) < 8:
        raise ValueError(f"Expected at least 8 columns in {source_file}: {raw_row!r}")

    trade_calendar_date = normalize_yyyymmdd(row[0])
    trade_time = normalize_hhmmss(row[5])
    product = row[1].upper()
    contract = row[3].upper()
    cp = row[4].upper()
    if cp not in {"C", "P"}:
        raise ValueError(f"Unexpected call/put value in {source_file}: {cp!r}")
    price = parse_price(row[6], source_file=source_file)
    volume = parse_volume(row[7], source_file=source_file)
    return OptionTrade(
        trading_date=trading_date,
        trade_calendar_date=trade_calendar_date,
        product=product,
        strike=normalize_strike(row[2]),
        contract_month=contract,
        cp=cp,
        trade_time=trade_time,
        trade_at=f"{trade_calendar_date}T{trade_time}",
        price=price,
        volume=volume,
        opening_auction=row[8] if len(row) > 8 else "",
        source_file=source_file,
    )


def should_skip_row(row: list[str]) -> bool:
    if not row:
        return True
    first_cell = row[0].strip()
    return not first_cell or first_cell == "成交日期" or first_cell.startswith("-")


def rebuild_daily_series(
    files: Iterable[Path],
    *,
    product: str | None = "TXO",
    monthly_only: bool = True,
    contract_month: str | None = None,
) -> tuple[list[dict[str, str | int]], RebuildStats]:
    selected_files = list(files)
    stats = RebuildStats(file_count=len(selected_files))
    if selected_files:
        stats.first_trading_date = file_trading_date(selected_files[0])
        stats.last_trading_date = file_trading_date(selected_files[-1])

    series: dict[tuple[str, str, str, str, str], DailySeries] = {}
    for path in selected_files:
        for trade in iter_option_trades(
            path,
            product=product,
            monthly_only=monthly_only,
            contract_month=contract_month,
        ):
            stats.trade_count += 1
            key = (
                trade.trading_date,
                trade.product,
                trade.contract_month,
                trade.strike,
                trade.cp,
            )
            if key not in series:
                series[key] = DailySeries(
                    trading_date=trade.trading_date,
                    product=trade.product,
                    contract_month=trade.contract_month,
                    contract_kind=contract_kind(trade.contract_month),
                    strike=trade.strike,
                    cp=trade.cp,
                )
            series[key].update(trade)

    rows = [item.to_row() for item in series.values()]
    rows.sort(key=daily_series_sort_key)
    stats.series_count = len(rows)
    return rows, stats


def summarize_contracts(rows: Iterable[dict[str, str | int]]) -> list[dict[str, str | int]]:
    summaries: dict[tuple[str, str, str], dict[str, object]] = {}
    for row in rows:
        key = (
            str(row["product"]),
            str(row["contract_month"]),
            str(row["contract_kind"]),
        )
        summary = summaries.setdefault(
            key,
            {
                "product": key[0],
                "contract_month": key[1],
                "contract_kind": key[2],
                "trading_dates": set(),
                "strikes": set(),
                "series": set(),
                "total_volume": 0,
                "total_trade_count": 0,
            },
        )
        summary["trading_dates"].add(str(row["trading_date"]))  # type: ignore[union-attr]
        summary["strikes"].add(str(row["strike"]))  # type: ignore[union-attr]
        summary["series"].add((str(row["strike"]), str(row["cp"])))  # type: ignore[union-attr]
        summary["total_volume"] = int(summary["total_volume"]) + int(row["volume"])
        summary["total_trade_count"] = int(summary["total_trade_count"]) + int(row["trade_count"])

    output = []
    for summary in summaries.values():
        trading_dates = sorted(summary["trading_dates"])  # type: ignore[arg-type]
        output.append({
            "product": str(summary["product"]),
            "contract_month": str(summary["contract_month"]),
            "contract_kind": str(summary["contract_kind"]),
            "first_trading_date": trading_dates[0] if trading_dates else "",
            "last_trading_date": trading_dates[-1] if trading_dates else "",
            "trading_days": len(trading_dates),
            "strikes": len(summary["strikes"]),  # type: ignore[arg-type]
            "option_series": len(summary["series"]),  # type: ignore[arg-type]
            "total_volume": int(summary["total_volume"]),
            "total_trade_count": int(summary["total_trade_count"]),
        })
    output.sort(key=lambda row: (str(row["product"]), str(row["contract_month"])))
    return output


def write_csv(path: Path, rows: list[dict[str, str | int]], columns: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


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


def parse_price(value: str, *, source_file: str) -> float:
    try:
        return float(value.strip())
    except ValueError as exc:
        raise ValueError(f"Unexpected price value in {source_file}: {value!r}") from exc


def parse_volume(value: str, *, source_file: str) -> int:
    try:
        return int(value.strip())
    except ValueError as exc:
        raise ValueError(f"Unexpected volume value in {source_file}: {value!r}") from exc


def is_monthly_contract(contract: str) -> bool:
    return bool(MONTHLY_CONTRACT_RE.fullmatch(contract.strip()))


def contract_kind(contract: str) -> str:
    return "monthly" if is_monthly_contract(contract) else "weekly"


def format_number(value: float | None) -> str:
    if value is None:
        return ""
    return f"{value:.8f}".rstrip("0").rstrip(".")


def daily_series_sort_key(row: dict[str, str | int]) -> tuple[object, ...]:
    return (
        str(row["trading_date"]),
        str(row["product"]),
        str(row["contract_month"]),
        strike_sort_value(str(row["strike"])),
        str(row["cp"]),
    )


def strike_sort_value(value: str) -> tuple[int, float | str]:
    try:
        return (0, float(value))
    except ValueError:
        return (1, value)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-dir", type=Path, default=RAW_DIR)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    parser.add_argument("--year", type=int, default=2026)
    parser.add_argument("--start-date")
    parser.add_argument("--through-date")
    parser.add_argument("--product", default="TXO")
    parser.add_argument("--contract-month")
    parser.add_argument("--monthly-only", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--limit-files", type=int)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    files = rpt_files(
        args.raw_dir,
        year=args.year,
        start_date=args.start_date,
        through_date=args.through_date,
    )
    if args.limit_files:
        files = files[: args.limit_files]
    if not files:
        raise SystemExit(f"No OptionsDaily RPT files found in {args.raw_dir}")

    rows, stats = rebuild_daily_series(
        files,
        product=args.product,
        monthly_only=args.monthly_only,
        contract_month=args.contract_month,
    )
    contract_rows = summarize_contracts(rows)
    kind = "monthly" if args.monthly_only else "all"
    product = (args.product or "all").lower()
    suffix = args.contract_month or str(args.year)
    daily_path = args.output_dir / f"{product}_{kind}_daily_series_{suffix}.csv"
    contracts_path = args.output_dir / f"{product}_{kind}_contracts_{suffix}.csv"
    write_csv(daily_path, rows, DAILY_SERIES_COLUMNS)
    write_csv(contracts_path, contract_rows, CONTRACT_SUMMARY_COLUMNS)

    print(json.dumps({
        **stats.to_dict(),
        "daily_series_csv": str(daily_path),
        "contracts_csv": str(contracts_path),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
