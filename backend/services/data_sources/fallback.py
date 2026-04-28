"""数据源故障 fallback 层 — P0.3 (2026-04-28).

设计:
- 主源 (aif10/miaoxiang) 故障时, 自动 fallback 到 akshare 库的等价接口
- 每次调用记录到 registry telemetry (call_count / fail_count / avg_latency_ms)
- client 内部 try/except 走 fallback, 不破坏现有 sync step 接口

Why not 让 updater 直接走 registry.resolve():
- 现有 sync step 已经有 ensure_tables / upsert / batch tracking 大量逻辑
- 改 sync step 走 resolve() 等于重写 client, 工程量爆炸
- 实务做法: client 内部加 try/except, 让 client 有韧性, registry 仍是元数据/UI 层

适用范围:
- lhb_daily: 妙想 RPT_DAILYBILLBOARD_DETAILSNEW → ak.stock_lhb_detail_em
- qfii_holding_quarterly: 妙想 RPT_DMSK_HOLDERS → ak.stock_gdfx_holding_detail_em
- institution_survey: 妙想 RPT_ORG_SURVEYNEW → ak.stock_jgdy_tj_em
- (market.py 十大流通 / market_signals 没有 ak 等价兜底, 容忍 cache stale)
"""
from __future__ import annotations

import logging
import time
from typing import Any, Callable

logger = logging.getLogger("cm-api.fallback")


def with_fallback(
    capability: str,
    primary_fn: Callable[[], Any],
    fallback_fn: Callable[[], Any] | None = None,
    *,
    primary_label: str = "primary",
    fallback_label: str = "fallback",
) -> tuple[Any, str]:
    """跑 primary, 失败时 fallback. 同时更新 registry telemetry.

    返回: (data, source_used) — source_used 是 'primary' / 'fallback' / '<exc>'.
    """
    # 找 source 实例 (基于 capability 的主源)
    try:
        from . import get_registry
        reg = get_registry()
        # 主源: 第一个声明该 capability 的 source (按 priority 排)
        primary_src = None
        fallback_src = None
        for src in reg.list_sources():
            if src.has_capability(capability):
                if primary_src is None:
                    primary_src = src
                elif fallback_src is None:
                    fallback_src = src
                    break
    except Exception:
        primary_src = fallback_src = None

    # 跑 primary
    t0 = time.time()
    try:
        data = primary_fn()
        latency_ms = (time.time() - t0) * 1000
        if primary_src is not None:
            primary_src._record_call(latency_ms, ok=True)
        return data, primary_label
    except Exception as exc:
        latency_ms = (time.time() - t0) * 1000
        if primary_src is not None:
            primary_src._record_call(latency_ms, ok=False)
        logger.warning(
            f"[fallback] {capability} 主源失败 ({type(exc).__name__}: {str(exc)[:120]}), "
            f"{'尝试 fallback' if fallback_fn else '无 fallback, 上抛'}"
        )
        if fallback_fn is None:
            raise

    # 跑 fallback
    t0 = time.time()
    try:
        data = fallback_fn()
        latency_ms = (time.time() - t0) * 1000
        if fallback_src is not None:
            fallback_src._record_call(latency_ms, ok=True)
        logger.info(f"[fallback] {capability}: fallback OK (latency {int(latency_ms)}ms)")
        return data, fallback_label
    except Exception as exc:
        latency_ms = (time.time() - t0) * 1000
        if fallback_src is not None:
            fallback_src._record_call(latency_ms, ok=False)
        logger.error(
            f"[fallback] {capability} 主+备全失败. fallback {type(exc).__name__}: {str(exc)[:120]}"
        )
        raise
