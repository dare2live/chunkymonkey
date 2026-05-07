"""
tdx_source.py - shared tdxhub runtime helpers.

The Python import path remains `tdxhub` for compatibility, but the project
expects that package to be provided by the dare2live/tdxhub fork.
"""

import logging
import os
import sys
import threading
import time
from pathlib import Path
from typing import Optional


logger = logging.getLogger("cm-api")
_TDX_TIMEOUT_SECONDS = float(os.environ.get("CM_TDX_TIMEOUT_SECONDS", "1.5"))
_TDX_DEFAULT_MAX_ATTEMPTS = 8
_QUOTES_IDLE_TTL_SECONDS = 300
_MOOTDX_CIRCUIT_BREAKER_SECONDS = 300
_TDX_SERVER_FAILURE_COOLDOWN_SECONDS = 120
_TDX_SERVER_TIMEOUT_COOLDOWN_SECONDS = 300
_NON_RETRYABLE_OPERATION_ERROR_TYPES = {"NotImplementedError"}

DEFAULT_TDX_SERVERS: tuple[tuple[str, int], ...] = (
    ("110.41.147.114", 7709),
    ("124.70.199.56", 7709),
    ("121.36.225.169", 7709),
    ("123.60.70.228", 7709),
    ("116.205.163.254", 7709),
)


def workspace_tdxhub_path() -> Path | None:
    """Return the sibling tdxhub fork path when this checkout has one."""

    stock_root = Path(__file__).resolve().parents[3]
    candidate = stock_root / "tdxhub"
    if (candidate / "tdxhub" / "__init__.py").exists():
        return candidate
    return None


def _module_is_loaded_from_path(module_name: str, root: Path) -> bool:
    module = sys.modules.get(module_name)
    if module is None:
        return False
    module_file = getattr(module, "__file__", None)
    if not module_file:
        return False
    try:
        Path(module_file).resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def ensure_workspace_tdxhub_path() -> Path | None:
    """Prefer the local tdxhub fork before importing any tdxhub module."""

    local_path = workspace_tdxhub_path()
    if local_path is None:
        return None

    text = str(local_path)
    if sys.path[:1] != [text]:
        sys.path[:] = [item for item in sys.path if item != text]
        sys.path.insert(0, text)

    if "tdxhub" in sys.modules and not _module_is_loaded_from_path("tdxhub", local_path):
        for name in list(sys.modules):
            if name == "tdxhub" or name.startswith("tdxhub."):
                del sys.modules[name]
    return local_path


ensure_workspace_tdxhub_path()


_tdxhub_runtime_state_lock = threading.Lock()
_tdxhub_runtime_state: dict[str, object] = {
    "until": 0.0,
    "summary": "",
    "attempts": [],
}


def tdxhub_circuit_open() -> bool:
    with _tdxhub_runtime_state_lock:
        return time.time() < float(_tdxhub_runtime_state.get("until") or 0.0)


def get_tdxhub_unavailable_state() -> dict[str, object]:
    with _tdxhub_runtime_state_lock:
        return {
            "until": float(_tdxhub_runtime_state.get("until") or 0.0),
            "summary": str(_tdxhub_runtime_state.get("summary") or ""),
            "attempts": list(_tdxhub_runtime_state.get("attempts") or []),
        }


def mark_tdxhub_unavailable(
    summary: str,
    attempts: list[dict],
    *,
    cooldown_seconds: int = _MOOTDX_CIRCUIT_BREAKER_SECONDS,
) -> None:
    with _tdxhub_runtime_state_lock:
        _tdxhub_runtime_state["until"] = time.time() + cooldown_seconds
        _tdxhub_runtime_state["summary"] = summary
        _tdxhub_runtime_state["attempts"] = list(attempts)


def clear_tdxhub_unavailable() -> None:
    with _tdxhub_runtime_state_lock:
        _tdxhub_runtime_state["until"] = 0.0
        _tdxhub_runtime_state["summary"] = ""
        _tdxhub_runtime_state["attempts"] = []


def parse_tdx_server_string(value: str) -> Optional[tuple[str, int]]:
    parts = str(value or "").strip().split(":")
    if len(parts) != 2:
        return None
    try:
        return parts[0], int(parts[1])
    except ValueError:
        return None


def _load_hq_hosts() -> tuple[tuple[str, int], ...]:
    ensure_workspace_tdxhub_path()
    try:
        from tdxhub.consts import HQ_HOSTS as hosts

        return tuple((host, port) for _name, host, port in hosts)
    except ImportError:
        logger.warning("[tdxhub] 无法导入 tdxhub.consts.HQ_HOSTS，使用内置后备服务器列表")
        return DEFAULT_TDX_SERVERS


def iter_tdx_servers() -> tuple[tuple[str, int], ...]:
    custom_raw = [item.strip() for item in os.environ.get("CM_TDX_SERVERS", "").split(",") if item.strip()]
    custom = [parse_tdx_server_string(item) for item in custom_raw]
    ordered: list[tuple[str, int]] = []
    seen: set[tuple[str, int]] = set()

    for server in [item for item in custom if item is not None] + list(_load_hq_hosts()):
        if server in seen:
            continue
        seen.add(server)
        ordered.append(server)
    return tuple(ordered)


def get_tdx_quotes_class():
    ensure_workspace_tdxhub_path()
    try:
        from tdxhub.quotes import Quotes

        return Quotes
    except ImportError:
        return None


def get_tdx_affair_class():
    ensure_workspace_tdxhub_path()
    try:
        from tdxhub.affair import Affair

        return Affair
    except ImportError:
        return None


_quotes_pool_guard = threading.Lock()
_quotes_pool: dict[tuple[str, int], dict[str, object]] = {}
_server_cursor_guard = threading.Lock()
_server_cursor = 0
_server_health_guard = threading.Lock()
_server_health: dict[tuple[str, int], dict[str, object]] = {}


def _close_quietly(client) -> None:
    if client is None:
        return
    try:
        client.close()
    except Exception:
        pass


def _prune_idle_quotes_clients(now: float) -> None:
    with _quotes_pool_guard:
        items = list(_quotes_pool.items())
    for server, state in items:
        last_used = float(state.get("last_used") or 0.0)
        if now - last_used < _QUOTES_IDLE_TTL_SECONDS:
            continue
        lock = state["lock"]
        if not lock.acquire(blocking=False):
            continue
        try:
            client = state.get("client")
            if client is None:
                continue
            _close_quietly(client)
            state["client"] = None
            state["last_used"] = now
        finally:
            lock.release()


def _get_quotes_pool_state(server: tuple[str, int]) -> dict[str, object]:
    now = time.monotonic()
    _prune_idle_quotes_clients(now)
    with _quotes_pool_guard:
        state = _quotes_pool.get(server)
        if state is None:
            state = {
                "client": None,
                "lock": threading.Lock(),
                "last_used": now,
            }
            _quotes_pool[server] = state
        return state


def reset_tdx_quotes_pool() -> None:
    global _server_cursor
    with _quotes_pool_guard:
        items = list(_quotes_pool.items())
        _quotes_pool.clear()
    with _server_cursor_guard:
        _server_cursor = 0
    with _server_health_guard:
        _server_health.clear()
    for _server, state in items:
        _close_quietly(state.get("client"))


def _get_server_health_snapshot() -> dict[tuple[str, int], dict[str, object]]:
    with _server_health_guard:
        return {server: dict(state) for server, state in _server_health.items()}


def _should_cooldown_server(error_type: Optional[str]) -> bool:
    text = str(error_type or "").lower()
    return any(token in text for token in (
        "timeout", "connect", "recv", "reset", "brokenpipe", "oserror",
    ))


def _mark_tdx_server_success(server: tuple[str, int]) -> None:
    now = time.monotonic()
    with _server_health_guard:
        state = _server_health.setdefault(server, {})
        state["last_success_at"] = now
        state["unavailable_until"] = 0.0
        state["last_error_type"] = ""


def _mark_tdx_server_failure(server: tuple[str, int], error_type: Optional[str]) -> None:
    now = time.monotonic()
    with _server_health_guard:
        state = _server_health.setdefault(server, {})
        state["last_failure_at"] = now
        state["last_error_type"] = str(error_type or "")
        if _should_cooldown_server(error_type):
            cooldown = (
                _TDX_SERVER_TIMEOUT_COOLDOWN_SECONDS
                if "timeout" in str(error_type or "").lower()
                else _TDX_SERVER_FAILURE_COOLDOWN_SECONDS
            )
            state["unavailable_until"] = max(
                float(state.get("unavailable_until") or 0.0),
                now + cooldown,
            )


def _iter_tdx_servers_for_request(*, prefer_last_success: bool = True) -> tuple[tuple[str, int], ...]:
    servers = iter_tdx_servers()
    if len(servers) <= 1:
        return servers

    global _server_cursor
    with _server_cursor_guard:
        start = _server_cursor % len(servers)
        _server_cursor += 1
    rotated = servers[start:] + servers[:start]
    snapshot = _get_server_health_snapshot()
    now = time.monotonic()
    ready = []
    cooling = []
    for server in rotated:
        state = snapshot.get(server, {})
        unavailable_until = float(state.get("unavailable_until") or 0.0)
        if unavailable_until > now:
            cooling.append((server, unavailable_until))
        else:
            ready.append(server)

    if prefer_last_success and len(ready) > 1:
        preferred = max(ready, key=lambda item: float(snapshot.get(item, {}).get("last_success_at") or 0.0))
        if float(snapshot.get(preferred, {}).get("last_success_at") or 0.0) > 0:
            ready = [preferred] + [server for server in ready if server != preferred]

    cooling.sort(key=lambda item: item[1])
    return tuple(ready + [server for server, _until in cooling])


def _len_or_none(value) -> Optional[int]:
    try:
        return len(value)
    except Exception:
        return None


def _default_tdx_max_attempts() -> Optional[int]:
    """Bound a single TDX request so a cold failure never scans every server."""

    raw = os.environ.get("CM_TDX_MAX_ATTEMPTS")
    if raw is None or str(raw).strip() == "":
        return _TDX_DEFAULT_MAX_ATTEMPTS
    try:
        value = int(str(raw).strip())
    except ValueError:
        logger.warning("[tdxhub] invalid CM_TDX_MAX_ATTEMPTS=%r, using %s", raw, _TDX_DEFAULT_MAX_ATTEMPTS)
        return _TDX_DEFAULT_MAX_ATTEMPTS
    if value <= 0:
        return None
    return value


def _build_attempt(
    server: tuple[str, int],
    *,
    started_at: float,
    ok: bool,
    error_type: Optional[str] = None,
    error: Optional[str] = None,
    result=None,
    lock_wait_s: float | None = None,
    connect_elapsed_s: float | None = None,
    operation_elapsed_s: float | None = None,
    pooled_client: bool | None = None,
) -> dict[str, object]:
    attempt: dict[str, object] = {
        "server": server,
        "ok": ok,
        "elapsed_sec": round(time.monotonic() - started_at, 3),
    }
    if lock_wait_s is not None:
        attempt["lock_wait_sec"] = round(float(lock_wait_s), 3)
    if connect_elapsed_s is not None:
        attempt["connect_elapsed_sec"] = round(float(connect_elapsed_s), 3)
    if operation_elapsed_s is not None:
        attempt["operation_elapsed_sec"] = round(float(operation_elapsed_s), 3)
    if pooled_client is not None:
        attempt["pooled_client"] = bool(pooled_client)
    if ok:
        rows = _len_or_none(result)
        if rows is not None:
            attempt["rows"] = rows
        return attempt
    attempt["error_type"] = error_type or "error"
    attempt["error"] = error or error_type or "error"
    return attempt


def _error_type_from_exception(exc: Exception) -> str:
    if isinstance(exc, ValueError) and str(exc):
        return str(exc)
    return type(exc).__name__


def call_tdx_quotes_with_retry(
    operation,
    *,
    action_name: str = "quotes",
    collect_attempts: bool = False,
    max_attempts: Optional[int] = None,
    connect_timeout: Optional[float] = None,
    prefer_last_success: bool = True,
):
    """Run a Quotes operation with server retry and pooled client reuse."""
    Quotes = get_tdx_quotes_class()
    if Quotes is None:
        raise ImportError("tdxhub 未安装，无法执行 Quotes 调用")

    attempts: list[str] = []
    attempt_details: list[dict[str, object]] = []
    attempt_count = 0
    timeout = float(connect_timeout if connect_timeout is not None else _TDX_TIMEOUT_SECONDS)
    effective_max_attempts = max_attempts if max_attempts is not None else _default_tdx_max_attempts()
    for server in _iter_tdx_servers_for_request(prefer_last_success=prefer_last_success):
        if effective_max_attempts is not None and attempt_count >= effective_max_attempts:
            break
        attempt_count += 1
        state = _get_quotes_pool_state(server)
        lock = state["lock"]
        started_at = time.monotonic()
        lock_started_at = time.monotonic()
        with lock:
            lock_wait_s = time.monotonic() - lock_started_at
            client = state.get("client")
            pooled_client = client is not None
            connect_elapsed_s = 0.0
            if client is None:
                connect_started_at = time.monotonic()
                try:
                    client = Quotes.factory(
                        market="std",
                        multithread=False,
                        heartbeat=False,
                        server=server,
                        timeout=timeout,
                    )
                    connect_elapsed_s = time.monotonic() - connect_started_at
                    state["client"] = client
                except Exception as exc:
                    connect_elapsed_s = time.monotonic() - connect_started_at
                    error_type = _error_type_from_exception(exc)
                    attempts.append(f"{server[0]}:{server[1]}:{error_type}")
                    attempt_details.append(
                        _build_attempt(
                            server,
                            started_at=started_at,
                            ok=False,
                            error_type=error_type,
                            error=str(exc),
                            lock_wait_s=lock_wait_s,
                            connect_elapsed_s=connect_elapsed_s,
                            operation_elapsed_s=0.0,
                            pooled_client=False,
                        )
                    )
                    _mark_tdx_server_failure(server, error_type)
                    logger.debug(f"[tdxhub] {action_name} 建连失败 {server}: {exc}")
                    if error_type in _NON_RETRYABLE_OPERATION_ERROR_TYPES:
                        break
                    continue

            try:
                operation_started_at = time.monotonic()
                result = operation(client)
                operation_elapsed_s = time.monotonic() - operation_started_at
                state["last_used"] = time.monotonic()
                _mark_tdx_server_success(server)
                attempt_details.append(
                    _build_attempt(
                        server,
                        started_at=started_at,
                        ok=True,
                        result=result,
                        lock_wait_s=lock_wait_s,
                        connect_elapsed_s=connect_elapsed_s,
                        operation_elapsed_s=operation_elapsed_s,
                        pooled_client=pooled_client,
                    )
                )
                payload = (result, f"tdxhub_{server[0]}:{server[1]}")
                if collect_attempts:
                    return payload + (attempt_details,)
                return payload
            except Exception as exc:
                operation_elapsed_s = time.monotonic() - operation_started_at
                error_type = _error_type_from_exception(exc)
                attempts.append(f"{server[0]}:{server[1]}:{error_type}")
                attempt_details.append(
                    _build_attempt(
                        server,
                        started_at=started_at,
                        ok=False,
                        error_type=error_type,
                        error=str(exc),
                        lock_wait_s=lock_wait_s,
                        connect_elapsed_s=connect_elapsed_s,
                        operation_elapsed_s=operation_elapsed_s,
                        pooled_client=pooled_client,
                    )
                )
                _mark_tdx_server_failure(server, error_type)
                logger.debug(f"[tdxhub] {action_name} 调用失败 {server}: {exc}")
                _close_quietly(client)
                state["client"] = None
                state["last_used"] = time.monotonic()
                if error_type in _NON_RETRYABLE_OPERATION_ERROR_TYPES:
                    break

    error = RuntimeError(f"{action_name} unavailable: " + ", ".join(attempts[:5]))
    if collect_attempts:
        setattr(error, "tdx_attempts", attempt_details)
    raise error
