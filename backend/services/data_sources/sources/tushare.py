"""TuShare source adapter — 唯一存活的采集入口 (sync_runner.fetch_raw 专用).

代理 = tinyshare (2026-06-17 切, 旧 jiaoch.site 反刷量墙弃用)。授权码进 .env (TUSHARE_TOKEN 等)。

限流 (tinyshare 代理, 用户 2026-06-17/19):
  - 单接口 120 次/分钟
  - 多接口合计 200 次/分钟
  - 并发上限 2
强制方式 = **配置驱动主动节流** (no-hardcode): 限额声明在 backend/config/sync_registry.yaml
  defaults.rate_limit (per_interface_per_min / total_per_min / max_concurrency); sync_runner._RateLimiter
  读 config 在每次 adapter.fetch_raw 前滑窗节流 (撞墙前先睡)。瞬态限流措辞退避 (sync_runner
  _is_transient_ratelimit -> transient_backoff) 作兜底; 真·当日/账户级墙 (_is_quota_wall) 才停链。
  改限额只动 yaml, 不动代码。

2026-07-07 精简收口: 原多源 registry 框架 (base.py/registry.py, fallback-chain/priority/
capability清单/健康检查) 唯一消费方(旧 updater UI, /api/data_sources/* 路由) 已随 2026-06-24
重建物删, 整套 fallback 机制 0 消费方 (与 sources/aif10.py 同款问题, 已随之删除)。本类原 fetch()
(capability式, 供 registry.resolve() 用) + healthcheck() (供 registry.healthcheck_all() 用)
一并删除 (0 调用方); 只留 sync_runner 实际调用的 fetch_raw()。sync_runner._adapter() 已改直接
实例化本类, 不再经过已删除的 registry.get_source()。
"""
from __future__ import annotations

import os
from typing import Any


TOKEN_ENV_VARS = ("TUSHARE_TOKEN", "TUSHARE_PRO_TOKEN", "TS_TOKEN")


def _env_token() -> str:
    for name in TOKEN_ENV_VARS:
        token = os.environ.get(name, "").strip()
        if token:
            return token
    raise RuntimeError(
        "tinyshare 授权码 missing; set one of TUSHARE_TOKEN, TUSHARE_PRO_TOKEN, TS_TOKEN"
    )


def _pro_api(token: str):
    # 2026-06-17 切 tinyshare 代理 (旧 jiaoch.site 反刷量墙封锁; tinyshare 自带网关, 无需 _DataApi__http_url monkeypatch)。
    # tinyshare 是 tushare 兼容的代理包: import tinyshare as ts; ts.set_token(授权码); ts.pro_api()。
    import tinyshare as ts

    ts.set_token(token)
    return ts.pro_api()


def _compact_params(**params: Any) -> dict[str, Any]:
    return {key: value for key, value in params.items() if value not in (None, "")}


def _to_records(data: Any) -> list[dict[str, Any]]:
    if data is None:
        return []
    to_dict = getattr(data, "to_dict", None)
    if callable(to_dict):
        records = to_dict("records")
        return [dict(row) for row in records]
    if isinstance(data, list):
        return [dict(row) for row in data if isinstance(row, dict)]
    if isinstance(data, dict):
        rows = data.get("data")
        if isinstance(rows, list):
            return [dict(row) for row in rows if isinstance(row, dict)]
        return [dict(data)]
    return []


class TuShareSource:
    """sync_runner 专用 tushare 适配器 (精简后, 无多源 fallback 机制)。"""

    name = "tushare"

    def fetch_raw(self, api_name: str, **params) -> list[dict[str, Any]]:
        """sync_runner 专用通用入口: 按 api 名直调, 返回 api 字段镜像 records.

        与已删除的 fetch(capability) 的历史边界: capability 是策略/probe 面 (带字段归一化),
        fetch_raw 是 sync 面 (raw 镜像不加工, 加工归特征层 — 架构稿 §3.3)。
        仅 sync_registry.yaml 驱动的 sync_runner 允许调用。
        """
        token = _env_token()
        try:
            pro = _pro_api(token)
        except ImportError as exc:
            raise RuntimeError(f"tinyshare package not installed: {exc}") from exc
        fn = getattr(pro, api_name, None)
        if fn is None:
            return _to_records(pro.query(api_name, **_compact_params(**params)))
        return _to_records(fn(**_compact_params(**params)))
