"""TDX ``xdxr`` corporate-action events. Not qfq, not daily factors, not OHLCV."""
from __future__ import annotations

from typing import Any

from services.data_sources.tdxhub_kline_recon import (
    bar_trade_date,
    bars_as_records,
    protocol_market,
    reject_tdx_adjust,
)

OHLCV_KEYS = frozenset({"open", "high", "low", "close", "vol", "volume", "amount"})
EVENT_FIELDS = (
    "category",
    "fenhong",
    "peigujia",
    "songzhuangu",
    "peigu",
    "suogu",
    "panqianliutong",
    "panhouliutong",
    "qianzongguben",
    "houzongguben",
    "fenshu",
    "xingquanjia",
)


def _require_quotes_api(client: Any, *, op: str) -> Any:
    if getattr(client, "protocol", None) == "mac":
        raise TypeError(f"{op} requires quotes_client(); never reuse mac_client")
    api = getattr(client, "client", None)
    if api is None or not hasattr(api, "get_xdxr_info"):
        raise TypeError(f"{op} requires quotes_client() TdxHq_API.get_xdxr_info")
    return api


def map_xdxr_events(
    raw: Any,
    ts_code: str,
    *,
    market: int,
    code: str,
) -> dict[str, Any]:
    events: list[dict[str, Any]] = []
    for item in bars_as_records(raw):
        event_date = bar_trade_date(item)
        event: dict[str, Any] = {
            "kind": "corporate_action_event",
            "event_date": event_date.isoformat() if event_date else None,
            "category_name": item.get("name") or item.get("category_name"),
        }
        for key in EVENT_FIELDS:
            if key in item:
                event[key] = item.get(key)
        events.append(event)
    return {
        "status": "ok" if events else "empty_recon",
        "kind": "corporate_action_events",
        "grain": "event",
        "is_qfq": False,
        "is_hfq": False,
        "is_daily_factor": False,
        "is_ohlcv": False,
        "ts_code": ts_code,
        "market": int(market),
        "code": str(code),
        "events": events,
    }


def fetch_xdxr(
    client: Any,
    ts_code: str,
    *,
    adjust: str | None = None,
) -> dict[str, Any]:
    reject_tdx_adjust(adjust)
    api = _require_quotes_api(client, op="xdxr")
    market, code = protocol_market(ts_code)
    raw = api.get_xdxr_info(market, code)
    return map_xdxr_events(raw, ts_code, market=market, code=code)


__all__ = [
    "EVENT_FIELDS",
    "OHLCV_KEYS",
    "fetch_xdxr",
    "map_xdxr_events",
]
