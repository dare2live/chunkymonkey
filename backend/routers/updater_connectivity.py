"""Connectivity probes used by the updater router."""

from __future__ import annotations

import asyncio
import time
from datetime import datetime

import httpx


CONNECTIVITY_TARGETS = {
    "holdings_source": "http://gw.tdx.com.cn:7708/",
}

CONNECTIVITY_LABELS = {
    "holdings_source": "股东源",
    "kline_source": "K线源",
}

CONNECTIVITY_CACHE_TTL_SECONDS = 300
_connectivity_cache = {
    "checked_at": 0.0,
    "data": None,
}


async def _compute_connectivity() -> dict:
    results = {}

    async def _check_holdings():
        try:
            async with httpx.AsyncClient(timeout=5.0, trust_env=False) as client:
                resp = await client.get(CONNECTIVITY_TARGETS["holdings_source"])
                ok = resp.status_code < 500
                return {
                    "holdings_source": ok,
                    "holdings_source_detail": f"HTTP {resp.status_code}" if ok else None,
                }
        except Exception:
            return {"holdings_source": False}

    async def _check_kline():
        from services.akshare_client import test_kline_availability

        try:
            probe = await asyncio.wait_for(test_kline_availability(), timeout=20)
            payload = {"kline_source": bool(probe.get("available"))}
            payload["kline_source_degraded"] = bool(
                probe.get("available")
                and (probe.get("effective_source") or "") != "tdxhub"
            )
            if probe.get("detail"):
                payload["kline_source_detail"] = probe.get("detail")
            payload["kline_source_meta"] = probe
            return payload
        except Exception:
            return {
                "kline_source": False,
                "kline_source_degraded": False,
                "kline_source_detail": "probe timeout",
                "kline_source_meta": {
                    "available": False,
                    "detail": "probe timeout",
                },
            }

    # 通达信(tdx)行业连通性探测已删 (2026-06-23 §4.3 行业切东财 dim_stock_dc_industry, tushare 不需探活外部 tdx 服务器)
    parts = await asyncio.gather(_check_holdings(), _check_kline())
    for part in parts:
        results.update(part)

    unreachable = [
        label
        for key, label in CONNECTIVITY_LABELS.items()
        if not results.get(key)
    ]
    degraded = []
    if results.get("kline_source_degraded"):
        degraded.append(f"K线源已降级（{results.get('kline_source_detail') or 'fallback'}）")

    if not unreachable and not degraded:
        results["message"] = "所有数据源正常"
    elif not unreachable:
        results["message"] = "；".join(degraded)
    else:
        parts = [f"{'、'.join(unreachable)}不可用，建议切换至手机热点"]
        parts.extend(degraded)
        results["message"] = "；".join(parts)
    return results


async def check_connectivity(force: bool = False) -> dict:
    """Probe data-source connectivity, with a short in-process cache."""

    now = time.time()
    cached = _connectivity_cache.get("data")
    checked_at = float(_connectivity_cache.get("checked_at") or 0.0)
    if not force and cached and (now - checked_at) < CONNECTIVITY_CACHE_TTL_SECONDS:
        results = dict(cached)
        results["cached"] = True
        results["checked_at"] = datetime.fromtimestamp(checked_at).isoformat()
        results["cache_age_seconds"] = int(now - checked_at)
        return results

    results = await _compute_connectivity()
    _connectivity_cache["data"] = dict(results)
    _connectivity_cache["checked_at"] = now
    results["cached"] = False
    results["checked_at"] = datetime.fromtimestamp(now).isoformat()
    results["cache_age_seconds"] = 0
    return results


def get_cached_connectivity() -> dict:
    now = time.time()
    cached = _connectivity_cache.get("data")
    checked_at = float(_connectivity_cache.get("checked_at") or 0.0)
    if cached:
        results = dict(cached)
        results["cached"] = True
        results["checked_at"] = datetime.fromtimestamp(checked_at).isoformat() if checked_at else None
        results["cache_age_seconds"] = int(now - checked_at) if checked_at else None
        return results
    return {
        "holdings_source": None,
        "kline_source": None,
        "message": "尚未执行连通性探测",
        "cached": True,
        "pending": True,
        "checked_at": None,
        "cache_age_seconds": None,
    }
