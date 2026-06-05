"""Local quote cache server for the option dashboard."""

from __future__ import annotations

import argparse
import json
import threading
from datetime import datetime, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import requests


ROOT = Path(__file__).resolve().parents[1]
FINMIND_API_BASE = "https://api.finmindtrade.com/api/v4"
QUOTE_REFRESH_SECONDS = 30


def load_env_token() -> str:
    env_path = ROOT / ".env"
    if not env_path.exists():
        return ""
    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        if key.strip() == "FINMIND_TOKEN":
            return value.strip().strip("'\"")
    return ""


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
            futures = self._fetch_snapshot("taiwan_futures_snapshot", {"data_id": "TXF"})
            options = self._fetch_snapshot("taiwan_options_snapshot", {"data_id": "TXO"})
            index, index_error = self._fetch_optional_snapshot("taiwan_stock_tick_snapshot", {"data_id": "001"})
            payload = {
                "ok": True,
                "updated_at": utc_now(),
                "refresh_interval_seconds": self.refresh_seconds,
                "futures": futures,
                "options": options,
                "index": index,
                "error": index_error,
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


QUOTE_CACHE = QuoteCache(load_env_token())


class DashboardHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler API.
        parsed = urlparse(self.path)
        if parsed.path == "/api/latest-quotes":
            query = parse_qs(parsed.query)
            force = query.get("force", ["0"])[0] == "1"
            payload = QUOTE_CACHE.refresh() if force or not QUOTE_CACHE.snapshot().get("updated_at") else QUOTE_CACHE.snapshot()
            self._send_json(payload)
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


def run_server(host: str, port: int) -> None:
    QUOTE_CACHE.start()
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
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--smoke", action="store_true", help="Run a lightweight smoke check and exit.")
    args = parser.parse_args(argv)
    if args.smoke:
        print("quant-assistant project is ready.")
        return
    run_server(args.host, args.port)


if __name__ == "__main__":
    main()
