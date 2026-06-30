"""Local quote cache server for the option dashboard."""

from __future__ import annotations

import argparse
import json
import os
import threading
import time
from datetime import datetime, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv

try:
    from .fugle_live import FugleLiveTQuoteService
    from .fugle_live import load_env_token as load_fugle_token
except ImportError:  # pragma: no cover - supports `python src/main.py`.
    from fugle_live import FugleLiveTQuoteService
    from fugle_live import load_env_token as load_fugle_token


ROOT = Path(__file__).resolve().parents[1]
FINMIND_API_BASE = "https://api.finmindtrade.com/api/v4"
FUGLE_STOCK_API_BASE = "https://api.fugle.tw/marketdata/v1.0/stock"
QUOTE_REFRESH_SECONDS = 30
FUTURES_DATA_IDS = ("TXF", "MXF", "MTX", "TMF")
TAIEX_DATA_ID = "TAIEX"
TAIEX_STOCK_ID = "TAIEX"
TAIEX_FUGLE_NAME = "發行量加權股價指數"
TAIPEI_TZ = ZoneInfo("Asia/Taipei")


def env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if not value:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def load_env_token() -> str:
    return load_env_value("FINMIND_TOKEN")


def load_env_value(target_key: str) -> str:
    if value := os.environ.get(target_key):
        return value
    load_dotenv(ROOT / ".env", override=False)
    return os.environ.get(target_key, "")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def taipei_today() -> str:
    return datetime.now(TAIPEI_TZ).date().isoformat()


def first_number(*values: Any) -> float | None:
    for value in values:
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            continue
        if parsed == parsed and parsed not in (float("inf"), float("-inf")):
            return parsed
    return None


def nested_value(payload: dict[str, Any], *path: str) -> Any:
    current: Any = payload
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def is_taiex_fugle_row(row: dict[str, Any]) -> bool:
    name = str(row.get("name") or row.get("stockName") or "").strip()
    symbol = str(row.get("symbol") or row.get("stock_id") or row.get("stockId") or "").upper().strip()
    return name == TAIEX_FUGLE_NAME or symbol in {TAIEX_STOCK_ID, "001", "IX0001"}


def fugle_row_date(row: dict[str, Any], fallback: str) -> str:
    for key in ("date", "tradeDate", "tradingDate", "lastUpdated", "last_updated", "time"):
        value = row.get(key)
        if value:
            normalized = normalize_date_text(value)
            if is_iso_date(normalized):
                return normalized
    return fallback


def normalize_fugle_taiex_row(row: dict[str, Any], trading_date: str) -> dict[str, Any]:
    close = first_number(
        row.get("closePrice"),
        row.get("lastPrice"),
        row.get("close"),
        nested_value(row, "lastTrade", "price"),
        row.get("referencePrice"),
    )
    open_price = first_number(row.get("openPrice"), row.get("open"), row.get("open_price"), close)
    high = first_number(row.get("highPrice"), row.get("high"), row.get("high_price"), close, open_price)
    low = first_number(row.get("lowPrice"), row.get("low"), row.get("low_price"), close, open_price)
    if close is None or open_price is None or high is None or low is None:
        raise RuntimeError("Fugle 加權指數 snapshot 缺少 OHLC 欄位。")
    if min(open_price, high, low, close) <= 0:
        raise RuntimeError("Fugle 加權指數 snapshot OHLC 不是有效正數。")
    high = max(high, open_price, close)
    low = min(low, open_price, close)
    return {
        "stock_id": TAIEX_STOCK_ID,
        "symbol": row.get("symbol") or TAIEX_STOCK_ID,
        "name": row.get("name") or TAIEX_FUGLE_NAME,
        "date": fugle_row_date(row, trading_date),
        "open": open_price,
        "high": high,
        "low": low,
        "close": close,
        "source": "fugle_stock_snapshot",
    }


class QuoteCache:
    def __init__(self, token: str, fugle_token: str = "", refresh_seconds: int = QUOTE_REFRESH_SECONDS) -> None:
        self.token = token
        self.fugle_token = fugle_token
        self.refresh_seconds = refresh_seconds
        self.lock = threading.Lock()
        self.refresh_lock = threading.Lock()
        self.payload: dict[str, Any] = {
            "ok": False,
            "updated_at": "",
            "refresh_interval_seconds": refresh_seconds,
            "futures": [],
            "options": [],
            "index": [],
            "error": "尚未更新即時報價。",
        }
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._run, name="quote-cache-refresh", daemon=True)

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        self.thread.join(timeout=2)

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return dict(self.payload)

    def refresh(self) -> dict[str, Any]:
        if not self.refresh_lock.acquire(blocking=False):
            return self.snapshot()
        try:
            futures, futures_error = self._fetch_futures_snapshots()
            options = self._fetch_snapshot("taiwan_options_snapshot", {"data_id": "TXO"})
            index, index_error = self._fetch_index_snapshot()
            error = "；".join(filter(None, [futures_error, index_error]))
            payload = {
                "ok": True,
                "updated_at": utc_now(),
                "refresh_interval_seconds": self.refresh_seconds,
                "futures": futures,
                "options": options,
                "index": index,
                "error": error,
            }
        except Exception as error:  # noqa: BLE001 - keep the cache endpoint resilient.
            previous = self.snapshot()
            payload = {
                **previous,
                "ok": bool(previous.get("futures") and previous.get("options")),
                "error": sanitize_error(error),
            }
        finally:
            self.refresh_lock.release()

        with self.lock:
            self.payload = payload
            return dict(self.payload)

    def _run(self) -> None:
        while not self.stop_event.is_set():
            self.refresh()
            self.stop_event.wait(self.refresh_seconds)

    def _fetch_snapshot(self, endpoint: str, params: dict[str, str]) -> list[dict[str, Any]]:
        return self._fetch_api(endpoint, params)

    def _fetch_data(self, dataset: str, params: dict[str, str]) -> list[dict[str, Any]]:
        return self._fetch_api("data", {"dataset": dataset, **params})

    def _fetch_api(self, endpoint: str, params: dict[str, str]) -> list[dict[str, Any]]:
        url = f"{FINMIND_API_BASE}/{endpoint}"
        request_params = dict(params)
        headers = {}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"

        response = requests.get(url, params=request_params, headers=headers, timeout=15)
        response.raise_for_status()
        payload = response.json()
        status = payload.get("status")
        if status and status != 200:
            raise RuntimeError(payload.get("msg") or f"FinMind status {status}")
        data = payload.get("data") or []
        if not isinstance(data, list):
            raise RuntimeError(f"{endpoint} 回傳格式不是 list。")
        return data

    def _fetch_fugle_stock_api(self, path: str, params: dict[str, str] | None = None) -> dict[str, Any]:
        if not self.fugle_token:
            raise RuntimeError("Fugle API token is not configured.")
        response = requests.get(
            f"{FUGLE_STOCK_API_BASE}/{path.lstrip('/')}",
            params=params or {},
            headers={"X-API-KEY": self.fugle_token},
            timeout=15,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise RuntimeError("Fugle stock snapshot 回傳格式不是 dict。")
        return payload

    def _fetch_fugle_taiex_snapshot(self, trading_date: str = "") -> list[dict[str, Any]]:
        payload = self._fetch_fugle_stock_api("snapshot/quotes/TSE")
        data = payload.get("data") or []
        if not isinstance(data, list):
            raise RuntimeError("Fugle stock snapshot data 回傳格式不是 list。")
        for row in data:
            if not isinstance(row, dict):
                continue
            if is_taiex_fugle_row(row):
                return [normalize_fugle_taiex_row(row, trading_date or taipei_today())]
        raise RuntimeError("Fugle stock snapshot 查無發行量加權股價指數。")

    def _fetch_fugle_taiex_candle(self, trading_date: str) -> dict[str, Any] | None:
        rows = self._fetch_fugle_taiex_snapshot(trading_date)
        return rows[0] if rows else None

    def _fetch_index_snapshot(self) -> tuple[list[dict[str, Any]], str]:
        fugle_error = ""
        try:
            rows = self._fetch_fugle_taiex_snapshot()
            if rows:
                return rows, ""
        except Exception as error:  # noqa: BLE001 - fall back to the older FinMind tick snapshot.
            fugle_error = f"Fugle 加權指數 snapshot: {sanitize_error(error)}"

        finmind_rows, finmind_error = self._fetch_optional_snapshot("taiwan_stock_tick_snapshot", {"data_id": "001"})
        return finmind_rows, "；".join(filter(None, [fugle_error, finmind_error]))

    def fetch_index_candles(self, start_date: str, end_date: str) -> dict[str, Any]:
        trading_dates = self._fetch_data(
            "TaiwanStockTradingDate",
            {"start_date": start_date, "end_date": end_date},
        )
        daily_candles = self._fetch_data(
            "TaiwanStockPrice",
            {"data_id": TAIEX_DATA_ID, "start_date": start_date, "end_date": end_date},
        )
        latest_date = latest_trading_date(trading_dates, start_date, end_date)
        latest_rows: list[dict[str, Any]] = []
        latest_candle: dict[str, Any] | None = None
        latest_error = ""
        latest_source = "fugle_stock_snapshot"
        if latest_date:
            try:
                latest_candle = self._fetch_fugle_taiex_candle(latest_date)
            except Exception as error:  # noqa: BLE001 - historical OHLC can still render without intraday rows.
                latest_error = sanitize_error(error)

        return {
            "ok": True,
            "start_date": start_date,
            "end_date": end_date,
            "latest_date": latest_date,
            "trading_dates": trading_dates,
            "daily_candles": daily_candles,
            "latest_rows": latest_rows,
            "latest_candle": latest_candle,
            "latest_source": latest_source,
            "latest_error": latest_error,
        }

    def _fetch_futures_snapshots(self) -> tuple[list[dict[str, Any]], str]:
        rows: list[dict[str, Any]] = []
        warnings: list[str] = []
        try:
            all_rows = self._fetch_snapshot("taiwan_futures_snapshot", {"data_id": ""})
            if all_rows:
                return dedupe_futures_rows(all_rows), ""
        except Exception as error:  # noqa: BLE001 - fall back to targeted futures requests.
            pass
        for data_id in FUTURES_DATA_IDS:
            try:
                rows.extend(self._fetch_snapshot("taiwan_futures_snapshot", {"data_id": data_id}))
            except Exception as error:  # noqa: BLE001 - non-TXF products should not block the cache.
                if data_id == "TXF" and not rows:
                    raise
                warnings.append(f"{data_id}: {sanitize_error(error)}")
        return rows, "；".join(warnings)

    def _fetch_optional_snapshot(self, endpoint: str, params: dict[str, str]) -> tuple[list[dict[str, Any]], str]:
        try:
            return self._fetch_snapshot(endpoint, params), ""
        except Exception as error:  # noqa: BLE001 - index snapshot is useful but should not block options.
            previous = self.snapshot()
            cached = previous.get("index")
            return (cached if isinstance(cached, list) else []), f"{endpoint}: {sanitize_error(error)}"


def sanitize_error(error: Exception) -> str:
    text = str(error)
    if "token=" in text:
        text = text.split("token=", 1)[0] + "token=<hidden>"
    return text


def dedupe_futures_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: dict[str, dict[str, Any]] = {}
    for row in rows:
        futures_id = str(row.get("futures_id") or "")
        if futures_id:
            deduped[futures_id] = row
    return list(deduped.values())


def is_iso_date(value: str) -> bool:
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        return False
    return True


def normalize_date_text(value: Any) -> str:
    return str(value or "")[:10].replace("/", "-")


def latest_trading_date(rows: list[dict[str, Any]], start_date: str, end_date: str) -> str:
    dates = sorted(
        {
            date
            for row in rows
            if (date := normalize_date_text(row.get("date") or row.get("trading_date") or row.get("stock_date")))
            and start_date <= date <= end_date
        },
    )
    return dates[-1] if dates else ""


QUOTE_CACHE = QuoteCache(load_env_token(), fugle_token=load_fugle_token())
FUGLE_TQUOTE = FugleLiveTQuoteService(load_fugle_token())
ALLOW_FORCE_QUOTE_REFRESH = env_flag("ALLOW_FORCE_QUOTE_REFRESH")


class DashboardHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def end_headers(self) -> None:
        static_path = urlparse(self.path).path
        if static_path.endswith((".html", ".js", ".css")) or static_path == "/":
            self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler API.
        parsed = urlparse(self.path)
        if parsed.path == "/api/latest-quotes":
            query = parse_qs(parsed.query)
            force = ALLOW_FORCE_QUOTE_REFRESH and query.get("force", ["0"])[0] == "1"
            payload = QUOTE_CACHE.refresh() if force or not QUOTE_CACHE.snapshot().get("updated_at") else QUOTE_CACHE.snapshot()
            self._send_json(payload)
            return
        if parsed.path == "/api/fugle-tquote":
            self._send_json(FUGLE_TQUOTE.cached_legacy_snapshot())
            return
        if parsed.path == "/api/fugle-tquote-events":
            self._send_event_stream()
            return
        if parsed.path == "/api/live/tquote":
            self._send_json(FUGLE_TQUOTE.quote_snapshot())
            return
        if parsed.path == "/api/live/tquote-events":
            self._send_quote_snapshot_event_stream()
            return
        if parsed.path == "/api/live/futures-1m":
            self._send_json(FUGLE_TQUOTE.futures_1m_snapshot())
            return
        if parsed.path == "/api/live/futures-1m-events":
            self._send_futures_1m_event_stream()
            return
        if parsed.path == "/api/index-candles":
            query = parse_qs(parsed.query)
            start_date = query.get("start_date", [""])[0]
            end_date = query.get("end_date", [""])[0]
            if not is_iso_date(start_date) or not is_iso_date(end_date):
                self._send_json({"ok": False, "error": "start_date 與 end_date 必須是 YYYY-MM-DD。"})
                return
            try:
                self._send_json(QUOTE_CACHE.fetch_index_candles(start_date, end_date))
            except Exception as error:  # noqa: BLE001 - return a frontend-friendly JSON error.
                self._send_json({"ok": False, "error": sanitize_error(error)})
            return
        if parsed.path == "/.env":
            self.send_error(404)
            return
        super().do_GET()

    def _send_json(self, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_event_stream(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        while True:
            try:
                payload = json.dumps(FUGLE_TQUOTE.snapshot(), ensure_ascii=False)
                self.wfile.write(f"data: {payload}\n\n".encode("utf-8"))
                self.wfile.flush()
                time.sleep(1)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                break

    def _send_quote_snapshot_event_stream(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        while True:
            try:
                payload = json.dumps(FUGLE_TQUOTE.quote_snapshot(), ensure_ascii=False)
                self.wfile.write(f"data: {payload}\n\n".encode("utf-8"))
                self.wfile.flush()
                time.sleep(1)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                break

    def _send_futures_1m_event_stream(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        while True:
            try:
                payload = json.dumps(FUGLE_TQUOTE.futures_1m_snapshot(), ensure_ascii=False)
                self.wfile.write(f"data: {payload}\n\n".encode("utf-8"))
                self.wfile.flush()
                time.sleep(1)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                break


def run_server(host: str, port: int) -> None:
    QUOTE_CACHE.start()
    FUGLE_TQUOTE.start()
    server = ThreadingHTTPServer((host, port), DashboardHandler)
    print(f"option dashboard server running at http://{host}:{port}/")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        QUOTE_CACHE.stop()
        server.server_close()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Run the local option dashboard server.")
    parser.add_argument("--host", default=os.getenv("HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=env_int("PORT", 8765))
    parser.add_argument("--smoke", action="store_true", help="Run a lightweight smoke check and exit.")
    args = parser.parse_args(argv)
    if args.smoke:
        print("quant-assistant project is ready.")
        return
    run_server(args.host, args.port)


if __name__ == "__main__":
    main()
