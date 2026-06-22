"""per-entity driver 注册 (防 god-module: 加 entity = 加 driver, 不改分发器本体)。

简单 entity (code + asof≤t + clean 模式) 共用 generic driver; 复杂 entity (ann_date 版本锁/
跨季 diff 等) 注册专属 driver。当前全走 generic。
"""
from __future__ import annotations

from ..spec import EntitySpec
from .generic import GenericDriver

_GENERIC = GenericDriver()
_REGISTRY: dict[str, object] = {}   # entity_name -> driver (专属); 缺省 generic


def get_driver(spec: EntitySpec):
    return _REGISTRY.get(spec.name, _GENERIC)
