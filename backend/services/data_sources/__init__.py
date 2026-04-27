"""data_sources 包: 统一数据源 registry + capability 调度.

设计目标:
- 调用方走 resolve('kline_daily', code='600519') 而不是直接 import client
- 优先级: tdxhub > 妙想 > em_datacenter > akshare (兜底)
- 每个 source 自我注册, 不动 updater.py 也能加新源

详见 docs/architecture-redesign-2026-04.md.
"""
from .base import BaseDataSource, Capability, Health, register_source, SourceState
from .registry import (
    Registry,
    get_registry,
    resolve,
    list_sources,
    healthcheck_all,
)

# 模块加载时自动注册 4 个内置 source
from .sources import tdxhub, aif10, akshare  # noqa: F401

__all__ = [
    "BaseDataSource",
    "Capability",
    "Health",
    "SourceState",
    "register_source",
    "Registry",
    "get_registry",
    "resolve",
    "list_sources",
    "healthcheck_all",
]
