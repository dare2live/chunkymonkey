"""data_sources base classes — 每个 source 自我描述 + 健康检查.

声明式 manifest 模式:
- Capability 是"业务数据需求"的标识 (kline_daily / top_holders 等)
- 每个 source 在 capabilities 里列自己能干的
- registry 按 priority 聚合 fallback chain
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Literal, Optional


SourceState = Literal["ok", "degraded", "down", "unknown"]
Freshness = Literal["t-0", "t-1", "daily", "weekly", "quarterly", "yearly", "static", "once"]


@dataclass
class Capability:
    """单个数据能力的元数据.

    name: 业务名 (kline_daily / top_free_holders 等), 全局唯一标识
    description: 一句话说明
    freshness: 更新粒度
    cost: low / medium / high (调用一次的耗时/资源)
    fields: 关键字段清单 (用于前端展示, 不必齐全)
    """
    name: str
    description: str = ""
    freshness: Freshness = "daily"
    cost: Literal["low", "medium", "high"] = "low"
    fields: list[str] = field(default_factory=list)
    notes: str = ""


@dataclass
class Health:
    """source 健康状态."""
    state: SourceState = "unknown"
    last_check_ts: Optional[float] = None
    last_success_ts: Optional[float] = None
    consecutive_failures: int = 0
    avg_latency_ms: Optional[float] = None
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "last_check_iso": (
                datetime.fromtimestamp(self.last_check_ts).isoformat()
                if self.last_check_ts else None
            ),
            "last_success_iso": (
                datetime.fromtimestamp(self.last_success_ts).isoformat()
                if self.last_success_ts else None
            ),
            "consecutive_failures": self.consecutive_failures,
            "avg_latency_ms": self.avg_latency_ms,
            "notes": self.notes,
        }


class BaseDataSource:
    """所有 source 继承此类.

    子类声明:
        name: 全局唯一标识 (tdxhub / aif10 / em_datacenter / akshare)
        display_name: UI 显示名
        priority: 数字越小优先级越高 (10/20/30/99)
        capabilities: list[Capability]

    子类实现:
        fetch(capability_name, **kwargs) -> Any
        healthcheck() -> Health
    """

    name: str = ""
    display_name: str = ""
    priority: int = 50
    repo_url: str = ""  # github URL (可选, UI 展示)

    @property
    def capabilities(self) -> list[Capability]:
        return []

    def has_capability(self, name: str) -> bool:
        return any(c.name == name for c in self.capabilities)

    def get_capability(self, name: str) -> Optional[Capability]:
        for c in self.capabilities:
            if c.name == name:
                return c
        return None

    def fetch(self, capability: str, **kwargs) -> Any:
        raise NotImplementedError(
            f"{self.__class__.__name__}.fetch({capability}) 未实现"
        )

    def healthcheck(self) -> Health:
        """子类必须实现. 返回当前健康状态.

        约定:
        - 不抛异常, 内部捕获并写到 Health.state = 'down'
        - 不阻塞超过 5s
        """
        return Health(state="unknown", notes="子类未实现 healthcheck")

    # ------------------------------------------------------------------
    # 内部状态: 健康 + telemetry (由 registry 维护, source 子类不必关心)
    # ------------------------------------------------------------------
    def __init__(self):
        self._health = Health()
        self._call_count = 0
        self._fail_count = 0
        self._latency_sum_ms = 0.0

    def _record_call(self, latency_ms: float, ok: bool):
        self._call_count += 1
        self._latency_sum_ms += latency_ms
        if not ok:
            self._fail_count += 1

    @property
    def telemetry(self) -> dict[str, Any]:
        return {
            "call_count": self._call_count,
            "fail_count": self._fail_count,
            "avg_latency_ms": (
                round(self._latency_sum_ms / self._call_count, 1)
                if self._call_count else None
            ),
        }


# ---------------------------------------------------------------------------
# 注册装饰器
# ---------------------------------------------------------------------------

_PENDING_REGISTRATIONS: list[type[BaseDataSource]] = []


def register_source(cls: type[BaseDataSource]) -> type[BaseDataSource]:
    """装饰器: 标记一个 source class 等待 registry 注册.

    用法:
        @register_source
        class TdxhubSource(BaseDataSource):
            ...

    实际注册在 registry 模块加载时统一处理.
    """
    if not issubclass(cls, BaseDataSource):
        raise TypeError(f"{cls.__name__} 必须继承 BaseDataSource")
    if not cls.name:
        raise ValueError(f"{cls.__name__}.name 不能为空")
    _PENDING_REGISTRATIONS.append(cls)
    return cls


def _get_pending() -> list[type[BaseDataSource]]:
    return list(_PENDING_REGISTRATIONS)
