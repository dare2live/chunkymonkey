"""单例 registry — 聚合 source, 按 priority 解析 capability fallback chain."""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Optional

from .base import BaseDataSource, Capability, Health, _get_pending


logger = logging.getLogger("data_sources.registry")


class Registry:
    """单例注册中心."""

    def __init__(self):
        self._sources: dict[str, BaseDataSource] = {}
        self._lock = threading.Lock()
        self._discover_pending()

    def _discover_pending(self):
        """加载 _PENDING_REGISTRATIONS 里所有 @register_source 的 class."""
        for cls in _get_pending():
            if cls.name in self._sources:
                continue  # 重复注册, skip
            try:
                instance = cls()
                self._sources[cls.name] = instance
                logger.info(
                    f"[registry] 注册 source: {cls.name} (priority={cls.priority}, "
                    f"caps={len(instance.capabilities)})"
                )
            except Exception as exc:
                logger.warning(f"[registry] 注册 {cls.name} 失败: {exc}")

    # ------------------------------------------------------------------
    # 列表
    # ------------------------------------------------------------------
    def list_sources(self) -> list[BaseDataSource]:
        """按 priority 升序返回所有注册的 source."""
        return sorted(self._sources.values(), key=lambda s: s.priority)

    def get_source(self, name: str) -> Optional[BaseDataSource]:
        return self._sources.get(name)

    def all_capabilities(self) -> dict[str, list[BaseDataSource]]:
        """返回 capability_name → 能提供该能力的 source 列表 (按 priority).

        UI 展示数据 → 源映射用此函数.
        """
        idx: dict[str, list[BaseDataSource]] = {}
        for src in self.list_sources():
            for cap in src.capabilities:
                idx.setdefault(cap.name, []).append(src)
        # 每条按 priority 排
        for cap_name, sources in idx.items():
            idx[cap_name] = sorted(sources, key=lambda s: s.priority)
        return idx

    # ------------------------------------------------------------------
    # 解析 + 调用
    # ------------------------------------------------------------------
    def resolve(
        self,
        capability: str,
        *,
        prefer_source: Optional[str] = None,
        **kwargs,
    ) -> tuple[Any, str]:
        """按 priority 顺序尝试每个 source.fetch(capability, **kwargs).

        返回: (data, source_name) — 成功的源.
        全部失败抛 RuntimeError.

        prefer_source: 可选, 强制只走某个源 (调试用).
        """
        candidates: list[BaseDataSource] = []
        if prefer_source:
            src = self._sources.get(prefer_source)
            if src is None:
                raise RuntimeError(f"未注册的源: {prefer_source}")
            if not src.has_capability(capability):
                raise RuntimeError(
                    f"源 {prefer_source} 不提供 capability '{capability}'"
                )
            candidates = [src]
        else:
            for src in self.list_sources():
                if src.has_capability(capability):
                    candidates.append(src)

        if not candidates:
            raise RuntimeError(f"没有源提供 capability '{capability}'")

        last_exc: Optional[Exception] = None
        for src in candidates:
            t0 = time.time()
            try:
                data = src.fetch(capability, **kwargs)
                latency_ms = (time.time() - t0) * 1000
                src._record_call(latency_ms, ok=True)
                return data, src.name
            except NotImplementedError:
                # capability 声明但未实现, 直接跳下一个
                continue
            except Exception as exc:
                latency_ms = (time.time() - t0) * 1000
                src._record_call(latency_ms, ok=False)
                last_exc = exc
                logger.warning(
                    f"[registry] {src.name}.fetch({capability}) 失败 "
                    f"({type(exc).__name__}), 尝试下一个源"
                )

        raise RuntimeError(
            f"所有 {len(candidates)} 个源都失败了 (capability={capability}): "
            f"{type(last_exc).__name__}: {last_exc}"
        ) from last_exc

    # ------------------------------------------------------------------
    # 健康
    # ------------------------------------------------------------------
    def healthcheck_all(self) -> dict[str, Health]:
        """同步逐个调用所有 source 的 healthcheck().

        每个超时容忍 5s, 失败时返回 Health(state='down').
        """
        out: dict[str, Health] = {}
        for src in self.list_sources():
            try:
                h = src.healthcheck()
                if h is None:
                    h = Health(state="unknown", notes="healthcheck 返回 None")
            except Exception as exc:
                h = Health(state="down", notes=f"{type(exc).__name__}: {exc}")
            h.last_check_ts = time.time()
            src._health = h
            out[src.name] = h
        return out

    def get_cached_health(self, name: str) -> Health:
        src = self._sources.get(name)
        return src._health if src else Health(state="unknown", notes="未注册")


# ---------------------------------------------------------------------------
# 单例 + 公共 API
# ---------------------------------------------------------------------------

_singleton: Optional[Registry] = None
_singleton_lock = threading.Lock()


def get_registry() -> Registry:
    global _singleton
    if _singleton is None:
        with _singleton_lock:
            if _singleton is None:
                _singleton = Registry()
    return _singleton


def resolve(capability: str, *, prefer_source: Optional[str] = None, **kwargs):
    return get_registry().resolve(capability, prefer_source=prefer_source, **kwargs)


def list_sources() -> list[BaseDataSource]:
    return get_registry().list_sources()


def healthcheck_all() -> dict[str, Health]:
    return get_registry().healthcheck_all()
