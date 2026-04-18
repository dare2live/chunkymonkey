"""共享 TDX 实时快照查询客户端。"""

from __future__ import annotations

import logging
from typing import Iterable

import pandas as pd

from services.tdx_source import call_tdx_quotes_with_retry


logger = logging.getLogger("cm-api")
_QUOTE_BATCH_SIZE = 80


def _normalize_stock_code(value: object) -> str | None:
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    if len(digits) < 6:
        return None
    return digits[-6:].zfill(6)


def _optional_float(value):
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except Exception:
        pass
    try:
        return float(value)
    except Exception:
        return None


def _normalize_quote_frame(frame: pd.DataFrame, *, source: str) -> dict[str, dict]:
    if frame is None or frame.empty:
        return {}

    results: dict[str, dict] = {}
    for _, row in frame.iterrows():
        code = _normalize_stock_code(row.get("code"))
        if not code:
            continue
        price = _optional_float(row.get("price"))
        last_close = _optional_float(row.get("last_close"))
        open_price = _optional_float(row.get("open"))
        high = _optional_float(row.get("high"))
        low = _optional_float(row.get("low"))
        volume = _optional_float(row.get("volume"))
        if volume is None:
            volume = _optional_float(row.get("vol"))
        results[code] = {
            "code": code,
            "price": price if price is not None else last_close,
            "last_close": last_close,
            "open": open_price,
            "high": high,
            "low": low,
            "volume": volume,
            "amount": _optional_float(row.get("amount")),
            "servertime": str(row.get("servertime") or "").strip() or None,
            "source": source,
        }
    return results


def _dedupe_codes(codes: Iterable[str]) -> list[str]:
    ordered: list[str] = []
    seen: set[str] = set()
    for code in codes:
        normalized = _normalize_stock_code(code)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        ordered.append(normalized)
    return ordered


def fetch_stock_spot_batch(codes: Iterable[str], batch_size: int = _QUOTE_BATCH_SIZE) -> dict[str, dict]:
    """批量抓取股票实时快照，优先使用共享 tdxhub 行情入口。"""
    unique_codes = _dedupe_codes(codes)
    if not unique_codes:
        return {}
    results: dict[str, dict] = {}

    for start in range(0, len(unique_codes), batch_size):
        pending = unique_codes[start:start + batch_size]
        batch_results: dict[str, dict] = {}

        try:
            frame, source = call_tdx_quotes_with_retry(
                lambda client: client.quotes(symbol=pending),
                action_name=f"quotes[{len(pending)}]",
            )
        except ImportError:
            logger.warning("[实时报价] tdxhub/mootdx 未安装，跳过批量 quotes")
            return {}
        except Exception as exc:
            logger.warning(f"[实时报价] 批量 quotes 失败: {exc}")
            continue

        normalized = _normalize_quote_frame(frame, source=source)
        for code in pending:
            item = normalized.get(code)
            if not item:
                continue
            batch_results[code] = item

        if pending:
            missing = [code for code in pending if code not in batch_results]
            if missing:
                logger.warning(f"[实时报价] 未获取到 {len(missing)} 只股票快照: {', '.join(missing[:10])}")
        results.update(batch_results)

    return results