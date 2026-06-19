"""TuShare source adapter for token-backed, no-persist capability probes.

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
"""
from __future__ import annotations

import os
import time
from typing import Any

from ..base import BaseDataSource, Capability, Health, register_source


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


def _as_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _amount_delta(row: dict[str, Any], buy_key: str, sell_key: str) -> float | None:
    buy = _as_float(row.get(buy_key))
    sell = _as_float(row.get(sell_key))
    if buy is None or sell is None:
        return None
    return buy - sell


def _first_number(row: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = _as_float(row.get(key))
        if value is not None:
            return value
    return None


def _net_amount(
    row: dict[str, Any],
    *,
    direct_keys: tuple[str, ...],
    buy_key: str,
    sell_key: str,
) -> float | None:
    direct = _first_number(row, *direct_keys)
    if direct is not None:
        return direct
    delta = _amount_delta(row, buy_key, sell_key)
    if delta is not None:
        return delta
    return _first_number(row, buy_key)


def _add_normalized_order_flow_columns(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for raw in rows:
        row = dict(raw)
        super_large = _net_amount(
            row,
            direct_keys=("super_large_net_amount", "net_elg_amount"),
            buy_key="buy_elg_amount",
            sell_key="sell_elg_amount",
        )
        large = _net_amount(
            row,
            direct_keys=("large_net_amount", "net_lg_amount"),
            buy_key="buy_lg_amount",
            sell_key="sell_lg_amount",
        )
        medium = _net_amount(
            row,
            direct_keys=("medium_net_amount", "net_md_amount"),
            buy_key="buy_md_amount",
            sell_key="sell_md_amount",
        )
        small = _net_amount(
            row,
            direct_keys=("small_net_amount", "net_sm_amount"),
            buy_key="buy_sm_amount",
            sell_key="sell_sm_amount",
        )

        main = _first_number(row, "main_net_amount", "net_mf_amount")
        if main is None and super_large is not None and large is not None:
            main = super_large + large

        if main is not None:
            row["main_net_amount"] = main
        if super_large is not None:
            row["super_large_net_amount"] = super_large
        if large is not None:
            row["large_net_amount"] = large
        if medium is not None:
            row["medium_net_amount"] = medium
        if small is not None:
            row["small_net_amount"] = small
        normalized.append(row)
    return normalized


@register_source
class TuShareSource(BaseDataSource):
    name = "tushare"
    display_name = "TuShare Pro"
    priority = 30
    repo_url = "https://tushare.pro"

    @property
    def capabilities(self) -> list[Capability]:
        return [
            Capability(
                "moneyflow",
                description="沪深 A 股个股资金流向",
                freshness="daily",
                fields=[
                    "trade_date",
                    "ts_code",
                    "main_net_amount",
                    "super_large_net_amount",
                    "large_net_amount",
                    "medium_net_amount",
                    "small_net_amount",
                ],
                notes="TuShare pro.moneyflow; 2000 points; no-persist need_027 probe candidate",
            ),
            Capability(
                "moneyflow_dc",
                description="东方财富个股资金流向",
                freshness="daily",
                notes="TuShare pro.moneyflow_dc; 5000 points; starts at 2023-09-11",
            ),
            Capability(
                "moneyflow_ths",
                description="同花顺个股资金流向",
                freshness="daily",
                notes="TuShare pro.moneyflow_ths; 6000 points; cross-source check candidate",
            ),
        ]

    def fetch(self, capability: str, **kwargs) -> Any:
        token = _env_token()
        try:
            pro = _pro_api(token)
        except ImportError as exc:
            raise RuntimeError(f"tinyshare package not installed: {exc}") from exc
        params = _compact_params(
            ts_code=kwargs.get("ts_code"),
            trade_date=kwargs.get("trade_date"),
            start_date=kwargs.get("start_date"),
            end_date=kwargs.get("end_date"),
        )

        if capability == "moneyflow":
            rows = _to_records(pro.moneyflow(**params))
            return _add_normalized_order_flow_columns(rows)
        if capability == "moneyflow_dc":
            rows = _to_records(pro.moneyflow_dc(**params))
            return _add_normalized_order_flow_columns(rows)
        if capability == "moneyflow_ths":
            rows = _to_records(pro.moneyflow_ths(**params))
            return _add_normalized_order_flow_columns(rows)

        raise NotImplementedError(f"tushare does not implement capability '{capability}'")

    def fetch_raw(self, api_name: str, **params) -> list[dict[str, Any]]:
        """sync_runner 专用通用入口: 按 api 名直调, 返回 api 字段镜像 records.

        与 fetch(capability) 的边界: capability 是策略/probe 面 (带字段归一化),
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

    def healthcheck(self) -> Health:
        try:
            _env_token()
        except RuntimeError as exc:
            return Health(state="unknown", last_check_ts=time.time(), notes=str(exc))
        try:
            import tinyshare as ts  # noqa: F401
        except ImportError as exc:
            return Health(state="down", last_check_ts=time.time(), notes=f"tinyshare not installed: {exc}")
        return Health(
            state="ok",
            last_check_ts=time.time(),
            notes="授权码 present; tinyshare 代理; live API not called by healthcheck",
        )
