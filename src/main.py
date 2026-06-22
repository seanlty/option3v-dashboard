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

import requests
from dotenv import load_dotenv

try:
    from .fugle_live import FugleLiveTQuoteService
except ImportError:  # pragma: no cover - supports `python src/main.py`.
    from fugle_live import FugleLiveTQuoteService


ROOT = Path(__file__).resolve().parents[1]
FINMIND_API_BASE = "https://api.finmindtrade.com/api/v4"
QUOTE_REFRESH_SECONDS = 30
FUTURES_DATA_IDS = ("TXF", "MXF", "MTX", "TMF")
TAIEX_DATA_ID = "TAIEX"


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


class QuoteCache:
    def __init__(self, token: str, refresh_seconds: int = QUOTE_REFRESH_SECONDS) -> None:
        self.token = token
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
            index, index_error = self._fetch_optional_snapshot("taiwan_stock_tick_snapshot", {"data_id": "001"})
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
        latest_error = ""
        if latest_date:
            try:
                latest_rows = self._fetch_data("TaiwanVariousIndicators5Seconds", {"start_date": latest_date})
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


QUOTE_CACHE = QuoteCache(load_env_token())
FUGLE_TQUOTE = FugleLiveTQuoteService(load_env_value("FUGLE_TOKEN"))
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
