"""data_sources 包: 统一数据源 registry + capability 调度.

设计目标:
- 调用方走 resolve('kline_daily', code='600519') 而不是直接 import client
- 优先级: tdxhub > 妙想/aif10 > akshare (兜底)
- 每个 source 自我注册, 不动 updater.py 也能加新源
"""
from .base import BaseDataSource, Capability, Health, register_source, SourceState
from .registry import (
    Registry,
    get_registry,
    resolve,
    list_sources,
    healthcheck_all,
)

# 模块加载时自动注册内置 source
# sources/aif10.py 2026-07-07 整文件退役物删: P0.3 时期设计的注册框架从未真正被启用
# (sync_registry.yaml 47 域全是 source: tushare, 无任何域声明 source: aif10; 实际 aif10 数据管线
# holders_aif10/qfii_client/org_holding_aif10 全部直接 import aif10_scraper, 零个走本框架
# resolve() 路径), 见 analysis/aif10_capability_client_retirement_20260707.md
from .sources import tushare  # noqa: F401  # tdxhub/akshare 源退役 2026-06-28 重建(tushare唯一+aif10)

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
