"""Quote snapshot data contract and local latest-cache helpers."""

from __future__ import annotations

import json
import math
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_VERSION = "0.1.0"
POINT_VALUE_TXO = 50
DEFAULT_CACHE_DIR = ROOT / "data" / "processed" / "fugle_live_snapshot_cache"
DEFAULT_CACHE_TTL_SECONDS = 60


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def parse_iso(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def first_number(*values: Any) -> float | None:
    for value in values:
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(parsed):
            return parsed
    return None


def round_or_none(value: Any, digits: int) -> float | int | None:
    parsed = first_number(value)
    if parsed is None:
        return None
    if digits <= 0:
        return int(round(parsed))
    return round(parsed, digits)


def safe_deepcopy(payload: dict[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(payload, ensure_ascii=False))


class QuoteSnapshotStore:
    def __init__(
        self,
        cache_dir: Path | None = None,
        ttl_seconds: int = DEFAULT_CACHE_TTL_SECONDS,
        history_interval_seconds: int = 15,
        persist: bool = True,
    ) -> None:
        self.cache_dir = cache_dir or DEFAULT_CACHE_DIR
        self.ttl_seconds = ttl_seconds
        self.history_interval_seconds = history_interval_seconds
        self.persist = persist
        self.latest_snapshot: dict[str, Any] | None = None
        self.last_history_write_monotonic = 0.0
        self.last_history_snapshot_at = ""

    @property
    def latest_snapshot_path(self) -> Path:
        return self.cache_dir / "latest_quote_snapshot.json"

    @property
    def history_dir(self) -> Path:
        return self.cache_dir / "history"

    def write(self, snapshot: dict[str, Any]) -> None:
        if not snapshot_has_usable_quotes(snapshot):
            return
        self.latest_snapshot = safe_deepcopy(snapshot)
        if not self.persist:
            return
        self.latest_snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        write_json(self.latest_snapshot_path, snapshot)
        if not self.should_write_history(snapshot):
            return
        history_name = snapshot.get("snapshot_at", now_iso()).replace(":", "").replace("+", "_").replace("-", "")
        write_json(self.history_dir / f"{history_name}_quote_snapshot.json", snapshot)
        self.last_history_write_monotonic = time.monotonic()
        self.last_history_snapshot_at = str(snapshot.get("snapshot_at") or "")

    def read_latest(self, force_stale: bool = False, error: str = "") -> dict[str, Any] | None:
        if self.latest_snapshot:
            return mark_cached_snapshot(self.latest_snapshot, self.ttl_seconds, force_stale=force_stale, error=error)
        if not self.persist:
            return None
        if not self.latest_snapshot_path.exists():
            return None
        try:
            snapshot = read_json(self.latest_snapshot_path)
        except (OSError, json.JSONDecodeError):
            return None
        return mark_cached_snapshot(snapshot, self.ttl_seconds, force_stale=force_stale, error=error)

    def should_write_history(self, snapshot: dict[str, Any]) -> bool:
        snapshot_at = str(snapshot.get("snapshot_at") or "")
        if snapshot_at and snapshot_at == self.last_history_snapshot_at:
            return False
        return time.monotonic() - self.last_history_write_monotonic >= self.history_interval_seconds


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def cache_age_seconds(snapshot: dict[str, Any]) -> float | None:
    snapshot_dt = parse_iso(str(snapshot.get("snapshot_at") or ""))
    if not snapshot_dt:
        return None
    return (datetime.now(timezone.utc).astimezone() - snapshot_dt).total_seconds()


def mark_cached_snapshot(
    snapshot: dict[str, Any],
    ttl_seconds: int,
    force_stale: bool = False,
    error: str = "",
) -> dict[str, Any]:
    cached = safe_deepcopy(snapshot)
    age = cache_age_seconds(cached)
    stale = force_stale or (age is not None and age > ttl_seconds)
    cached["stale"] = bool(stale)
    cached["status"] = "stale_cache" if stale else cached.get("status", "ok")
    if error:
        cached["error"] = error
    source = dict(cached.get("source") or {})
    source["type"] = "fugle_cache" if stale else source.get("type", "fugle_live")
    source["cache_age_seconds"] = round_or_none(age, 3)
    source["cache_ttl_seconds"] = ttl_seconds
    cached["source"] = source
    for row in cached.get("rows") or []:
        for side in ("call", "put"):
            quality = ((row.get(side) or {}).get("quality") or {})
            if isinstance(quality, dict):
                quality["stale"] = bool(stale)
                quality["age_seconds"] = round_or_none(age, 3)
    return cached


def quote_snapshot_from_tquote_payload(
    payload: dict[str, Any],
    source_type: str,
    stale: bool = False,
    error: str = "",
) -> dict[str, Any]:
    snapshot_at = payload.get("last_aggregate_at") or payload.get("last_book_at") or payload.get("last_event_at") or now_iso()
    settlement_date = str(payload.get("settlement_date") or "")
    contract = str(payload.get("contract") or payload.get("contract_month") or "")
    session = "night" if payload.get("after_hours") else "day"
    rows = [adapt_row(row, stale=stale) for row in payload.get("rows") or []]
    vix = adapt_vix(payload.get("vix"))
    source = {
        "type": source_type,
        "provider": "fugle",
        "status": payload.get("status") or "",
    }
    if payload.get("event_counts"):
        source["event_counts"] = payload.get("event_counts")
    if payload.get("last_book_at"):
        source["last_book_at"] = payload.get("last_book_at")
    if payload.get("last_aggregate_at"):
        source["last_aggregate_at"] = payload.get("last_aggregate_at")
    return {
        "schema": "quote_snapshot",
        "schema_version": SCHEMA_VERSION,
        "snapshot_id": f"quote_snapshot:TXO:{contract}:{snapshot_at}",
        "exchange": "TAIFEX",
        "product": "TXO",
        "contract_month": contract,
        "settlement_date": settlement_date,
        "trading_date": trading_date_from_snapshot(snapshot_at),
        "session": session,
        "snapshot_at": snapshot_at,
        "received_at": now_iso(),
        "status": payload.get("status") or "ok",
        "stale": bool(stale),
        "error": error or payload.get("error") or "",
        "source": source,
        "underlying": {
            "product": "TXF",
            "symbol": payload.get("future_symbol") or "",
            "price": round_or_none(payload.get("future_price"), 6),
            "source": source_type,
            "updated_at": payload.get("last_aggregate_at") or payload.get("last_event_at") or snapshot_at,
        },
        "risk_model": {
            "model": "black76",
            "risk_free_rate": round_or_none(payload.get("risk_free_rate"), 8),
            "expiry_at": f"{settlement_date}T13:30:00+08:00" if settlement_date else "",
            "time_to_expiry_years": round_or_none(payload.get("time_to_expiry_years"), 10),
            "iv_basis": "mid_price",
        },
        "rows": rows,
        "vix": vix,
        "vix_series": adapt_vix_series(payload.get("vix_series") or []),
        "metadata": {
            "row_count": len(rows),
            "selected_symbols": payload.get("selected_symbols") or [],
            "selected_strikes": payload.get("selected_strikes") or [row.get("strike") for row in rows],
            "contract_multiplier": POINT_VALUE_TXO,
            "price_unit": "index_points",
            "iv_unit": "decimal",
            "vix_value_unit": "percent",
            "quote_greeks_unit": "per_one_option_before_multiplier",
        },
    }


def trading_date_from_snapshot(snapshot_at: str) -> str:
    parsed = parse_iso(str(snapshot_at or ""))
    return parsed.date().isoformat() if parsed else now_iso()[:10]


def adapt_row(row: dict[str, Any], stale: bool) -> dict[str, Any]:
    strike = row.get("strike")
    return {
        "strike": strike,
        "call": adapt_leg(row.get("call") or {}, "call", stale),
        "put": adapt_leg(row.get("put") or {}, "put", stale),
    }


def adapt_leg(leg: dict[str, Any], option_type: str, stale: bool) -> dict[str, Any]:
    bid = first_number(leg.get("bid"))
    ask = first_number(leg.get("ask"))
    last = first_number(leg.get("last"))
    mid = (bid + ask) / 2 if bid and ask and bid > 0 and ask > 0 else None
    warnings: list[str] = []
    if mid is None:
        warnings.append("bid/ask mid unavailable; IV and Greeks require both sides.")
    if last is None:
        warnings.append("last price unavailable.")
    status = "ok" if mid is not None or last is not None else "missing"
    bid_ask_state = "normal" if mid is not None else "last_only" if last is not None else "missing"
    return {
        "symbol": leg.get("symbol"),
        "type": option_type,
        "bid": round_or_none(bid, 6),
        "ask": round_or_none(ask, 6),
        "mid": round_or_none(mid, 6),
        "bid_size": round_or_none(leg.get("bid_size"), 0),
        "ask_size": round_or_none(leg.get("ask_size"), 0),
        "last": round_or_none(last, 6),
        "volume": round_or_none(leg.get("volume"), 0),
        "change": round_or_none(leg.get("change"), 6),
        "change_percent": round_or_none(leg.get("change_percent"), 8),
        "quote_at": leg.get("time"),
        "aggregate_at": leg.get("last_updated"),
        "bid_iv": round_or_none(leg.get("bid_iv"), 10),
        "ask_iv": round_or_none(leg.get("ask_iv"), 10),
        "mid_iv": round_or_none(leg.get("mid_iv"), 10),
        "delta": round_or_none(leg.get("delta"), 10),
        "gamma": round_or_none(leg.get("gamma"), 12),
        "theta": round_or_none(leg.get("theta"), 10),
        "vega": round_or_none(leg.get("vega"), 10),
        "greeks_source": "black76_mid_iv" if leg.get("mid_iv") else "missing_mid_iv",
        "quality": {
            "status": status,
            "bid_ask_state": bid_ask_state,
            "stale": bool(stale),
            "age_seconds": 0 if not stale else None,
            "warnings": warnings,
        },
    }


def adapt_vix(vix: dict[str, Any] | None) -> dict[str, Any] | None:
    if not vix:
        return None
    value_percent = first_number(vix.get("value_percent"), vix.get("value"))
    return {
        "value_decimal": round_or_none(value_percent / 100 if value_percent is not None else None, 10),
        "value_percent": round_or_none(value_percent, 6),
        "sample_count": vix.get("sample_count"),
        "call_count": vix.get("call_count"),
        "put_count": vix.get("put_count"),
        "method": vix.get("method"),
    }


def adapt_vix_series(series: list[dict[str, Any]]) -> list[dict[str, Any]]:
    points: list[dict[str, Any]] = []
    for point in series:
        value_percent = first_number(point.get("value_percent"), point.get("value"))
        if value_percent is None:
            continue
        points.append({
            "time": point.get("time") or now_iso(),
            "value_percent": round_or_none(value_percent, 6),
            "value": round_or_none(value_percent, 6),
            "sample_count": point.get("sample_count"),
            "call_count": point.get("call_count"),
            "put_count": point.get("put_count"),
        })
    return points


def legacy_tquote_from_quote_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    vix = snapshot.get("vix") or {}
    return {
        "status": snapshot.get("status"),
        "authenticated": False,
        "subscribed": False,
        "after_hours": snapshot.get("session") == "night",
        "contract": snapshot.get("contract_month"),
        "settlement_date": snapshot.get("settlement_date"),
        "future_symbol": (snapshot.get("underlying") or {}).get("symbol"),
        "future_price": (snapshot.get("underlying") or {}).get("price"),
        "risk_free_rate": (snapshot.get("risk_model") or {}).get("risk_free_rate"),
        "time_to_expiry_years": (snapshot.get("risk_model") or {}).get("time_to_expiry_years"),
        "selected_symbols": (snapshot.get("metadata") or {}).get("selected_symbols") or [],
        "selected_strikes": (snapshot.get("metadata") or {}).get("selected_strikes") or [],
        "event_counts": (snapshot.get("source") or {}).get("event_counts") or {},
        "last_event_at": snapshot.get("snapshot_at"),
        "last_book_at": (snapshot.get("source") or {}).get("last_book_at") or snapshot.get("snapshot_at"),
        "last_aggregate_at": (snapshot.get("source") or {}).get("last_aggregate_at") or snapshot.get("snapshot_at"),
        "vix": {
            "value": vix.get("value_percent"),
            "sample_count": vix.get("sample_count"),
            "call_count": vix.get("call_count"),
            "put_count": vix.get("put_count"),
            "method": vix.get("method"),
        } if vix else None,
        "vix_series": snapshot.get("vix_series") or [],
        "error": snapshot.get("error") or "",
        "rows": snapshot.get("rows") or [],
        "stale": snapshot.get("stale", False),
        "source": snapshot.get("source") or {},
    }


def snapshot_has_usable_quotes(snapshot: dict[str, Any]) -> bool:
    for row in snapshot.get("rows") or []:
        for side in ("call", "put"):
            leg = row.get(side) or {}
            if first_number(leg.get("mid"), leg.get("last"), leg.get("bid"), leg.get("ask")) is not None:
                return True
    return False
