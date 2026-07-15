"""TuShare source adapter — 唯一存活的采集入口 + 账户授权事实探针。

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
一并删除 (0 调用方); 只留 sync_runner 实际调用的 fetch_raw() 与 pipeline 前置硬门调用的
authorization_status()。sync_runner._adapter() 已改直接实例化本类, 不再经过已删除的
registry.get_source()。
"""
from __future__ import annotations

import os
import signal
import socket
import threading
from contextlib import contextmanager
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo


TOKEN_ENV_VARS = ("TUSHARE_TOKEN", "TUSHARE_PRO_TOKEN", "TS_TOKEN")
_SHANGHAI = ZoneInfo("Asia/Shanghai")
_AUTH_TIME_FORMAT = "%Y/%m/%d %H:%M:%S"
AUTH_FAILURE_REASONS = frozenset({
    "missing_token",
    "package_missing",
    "auth_expired",
    "auth_denied",
    "auth_probe_unavailable",
    "auth_metadata_invalid",
})


class TuShareAuthorizationError(RuntimeError):
    """Sanitized fail-closed authorization error safe for logs and CLI output."""

    def __init__(self, reason: str):
        if reason not in AUTH_FAILURE_REASONS:
            raise ValueError(f"unknown authorization failure reason: {reason}")
        self.reason = reason
        super().__init__(f"tushare authorization blocked: {reason}")


def _now_shanghai() -> datetime:
    return datetime.now(_SHANGHAI)


def _is_tinyshare_permission_error(exc: BaseException) -> bool:
    try:
        import tinyshare
    except ImportError:
        return False
    return isinstance(exc, tinyshare.TinySharePermissionError)


def _parse_authorization_metadata(rows: list[dict[str, Any]]) -> dict[str, Any]:
    try:
        row = rows[0]
        opened_at = datetime.strptime(str(row["addDate"]), _AUTH_TIME_FORMAT).replace(tzinfo=_SHANGHAI)
        expires_at = datetime.strptime(str(row["limitDate"]), _AUTH_TIME_FORMAT).replace(tzinfo=_SHANGHAI)
        remaining_weeks = int(row["week"])
        if remaining_weeks < 0 or opened_at > expires_at:
            raise ValueError("inconsistent authorization metadata")
    except (IndexError, KeyError, TypeError, ValueError):
        raise TuShareAuthorizationError("auth_metadata_invalid") from None
    return {
        "opened_at": opened_at,
        "expires_at": expires_at,
        "remaining_weeks": remaining_weeks,
    }


def _env_token() -> str:
    for name in TOKEN_ENV_VARS:
        token = os.environ.get(name, "").strip()
        if token:
            return token
    raise TuShareAuthorizationError("missing_token")


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


@contextmanager
def _authorization_timeout(seconds: float):
    """Bound user() even when the HTTP client forgets to provide its own timeout."""
    previous_socket_timeout = socket.getdefaulttimeout()
    socket.setdefaulttimeout(seconds)
    alarm_supported = (
        hasattr(signal, "SIGALRM")
        and hasattr(signal, "setitimer")
        and threading.current_thread() is threading.main_thread()
    )
    previous_handler = None
    previous_timer = (0.0, 0.0)
    if alarm_supported:
        def _raise_timeout(_signum, _frame):
            raise TimeoutError("authorization probe timed out")

        previous_handler = signal.getsignal(signal.SIGALRM)
        signal.signal(signal.SIGALRM, _raise_timeout)
        previous_timer = signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        if alarm_supported:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, previous_handler)
            if previous_timer[0] > 0:
                signal.setitimer(signal.ITIMER_REAL, *previous_timer)
        socket.setdefaulttimeout(previous_socket_timeout)


def probe_authorization(source: Any, *, timeout_seconds: float) -> dict[str, Any]:
    """Run one sanitized authorization probe under a hard, config-owned deadline."""
    if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)) \
            or timeout_seconds <= 0:
        raise ValueError("authorization timeout_seconds must be positive")
    try:
        with _authorization_timeout(float(timeout_seconds)):
            return source.authorization_status()
    except TuShareAuthorizationError:
        raise
    except Exception:
        raise TuShareAuthorizationError("auth_probe_unavailable") from None


class TuShareSource:
    """sync_runner + pipeline auth gate 共用适配器 (无多源 fallback)。"""

    name = "tushare"

    def authorization_status(self) -> dict[str, Any]:
        """Probe the active account and return only sanitized authorization metadata."""
        client_error: str | None = None
        try:
            pro = _pro_api(_env_token())
        except TuShareAuthorizationError:
            raise
        except ImportError:
            client_error = "package_missing"
        except Exception as exc:
            client_error = (
                "auth_denied" if _is_tinyshare_permission_error(exc) else "auth_probe_unavailable"
            )
        if client_error is not None:
            raise TuShareAuthorizationError(client_error)

        probe_error: str | None = None
        try:
            rows = _to_records(pro.user())
        except Exception as exc:
            probe_error = (
                "auth_denied" if _is_tinyshare_permission_error(exc) else "auth_probe_unavailable"
            )
        if probe_error is not None:
            raise TuShareAuthorizationError(probe_error)

        status = _parse_authorization_metadata(rows)
        if status["expires_at"] <= _now_shanghai():
            raise TuShareAuthorizationError("auth_expired")
        return status

    def fetch_raw(self, api_name: str, **params) -> list[dict[str, Any]]:
        """sync_runner 专用通用入口: 按 api 名直调, 返回 api 字段镜像 records.

        与已删除的 fetch(capability) 的历史边界: capability 是策略/probe 面 (带字段归一化),
        fetch_raw 是 sync 面 (raw 镜像不加工, 加工归特征层 — 架构稿 §3.3)。
        仅 sync_registry.yaml 驱动的 sync_runner 允许调用。
        """
        token = _env_token()
        client_error: str | None = None
        try:
            pro = _pro_api(token)
        except ImportError:
            client_error = "package_missing"
        except Exception as exc:
            if _is_tinyshare_permission_error(exc):
                client_error = "auth_denied"
            else:
                raise
        if client_error is not None:
            raise TuShareAuthorizationError(client_error)

        try:
            fn = getattr(pro, api_name, None)
            if fn is None:
                return _to_records(pro.query(api_name, **_compact_params(**params)))
            return _to_records(fn(**_compact_params(**params)))
        except Exception as exc:
            if not _is_tinyshare_permission_error(exc):
                raise
        raise TuShareAuthorizationError("auth_denied")
