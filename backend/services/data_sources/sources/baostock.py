"""Baostock adapter — 免费 A 股源, 自研二进制协议 over 裸 TCP (不是 HTTP API)。

pip 包 ``baostock==0.9.3`` (钉死版本, 见 backend/requirements.txt 该行上方注释 —— 下面
四个坑全是版本相关行为, 升级可能悄悄改掉)。2026-08-30 对着 PyPI 上锁定版本的 sdist
(baostock-0.9.3.tar.gz) 逐文件核实, 不是道听途说:
  - 协议实现: ``baostock/util/socketutil.py`` (``send_msg``) +
    ``baostock/data/resultset.py`` (``ResultData``) —— 手写消息头 + crc32 + zlib。
  - 全局单例: ``SocketUtil`` 类级单例 (``instance`` 类属性) + ``baostock/common/context.py``
    模块级全局 ``default_socket``, 均无锁。

四个实测坑, 本 adapter 的职责就是把它们封死:

1. **无 socket 超时**: ``SocketUtil.connect()`` / ``get_default_socket()``
   (util/socketutil.py) 里创建的裸 ``socket.socket()`` 全程从未调用 ``settimeout()``,
   网络卡死会让整个进程永久挂起。
   对策 (已实测可行): 在 ``bs.login()`` **之前** ``socket.setdefaulttimeout(N)`` ——
   baostock 的 socket 在 login 时创建 (``SocketUtil().connect()``), 会继承这个进程级
   默认值; login 后立刻恢复原值, ``try/finally`` 保证不污染其它源。见
   ``_login_with_bounded_timeout``。

2. **``next()`` 静默截断**: ``ResultData.next()`` (data/resultset.py) 在翻页请求的
   ``receive_data`` 为 ``None``/空串时直接 ``return False``，且**不改
   ``self.error_code``**。常见写法 ``while rs.error_code=='0' and rs.next():`` 会中途
   安静停止、误以为拿完了, 没有任何报错信号 (error_code 还停在上一页成功时的 ``'0'``)。
   对策: ``_drain_rows`` 取完后做完整性校验 —— 结束时 ``error_code`` 仍须为
   ``BSERR_SUCCESS``；若调用方经 ``fetch_raw`` 传了 ``expected_row_count``，再比对条数
   (这是唯一能抓住"receive_data 为空但 error_code 未变"这种静默截断的手段)。

3. **并发不安全**: 全局单例 socket 且无锁, 多线程并发 send/recv 会互相污染字节流。
   对策: ``BaostockSource`` 记录首次调用的 thread id, 后续不同 thread 调用直接抛
   ``BaostockConcurrencyError``。**本 adapter 只能单进程串行使用**, 不要在它外面加线程池。

4. **``get_data()`` 在 pandas>=2.0 崩溃**: ``ResultData.get_data()`` 内部用了
   ``df.append(temp_df, ignore_index=True)`` —— ``DataFrame.append`` 已在 pandas 2.0
   移除。对策: 本 adapter **绝不调用 ``rs.get_data()``**, 一律 ``_drain_rows`` 手动
   ``while rs.next(): rows.append(rs.get_row_data())``，再用 ``rs.fields`` 自己组装
   ``list[dict]``。

``BaostockSource.fetch_raw`` 是 sync_runner 的调用约定入口, 与 ``sources/fuyao.py`` /
``sources/tushare.py`` 同型: ``fetch_raw(api, **params) -> list[dict]``。

**范围声明**: 本文件只提供接入能力 (source adapter), **不注册任何
``sync_registry.yaml`` 数据域** —— 域注册 (含 trade_cal 的 formal boundary 考量)
是另一刀的事。
"""
from __future__ import annotations

import socket
import threading
from typing import Any

ALIAS = "baostock"

# 官方从未公布 socket 超时建议值; 这里镜像 sync_registry.yaml `sources.baostock.
# fetch_timeout_seconds` 当前配的 15 (仅作构造函数默认值, 不做唯一 owner —— 真正接入
# sync_runner 的域注册环节会把 registry 值显式传进构造函数)。
DEFAULT_TIMEOUT_SECONDS = 15.0


# ---------------------------------------------------------------------------
# api 名 -> baostock 模块属性名 (query_* 函数) 映射表。映射表与查表逻辑分开
# (本项目现行做法, 不写 if-elif 链): 新增已实测可用的 api 只加一行, 不碰 fetch_raw。
# 当前二者刚好同名 (直接用 baostock 官方函数名做 api 值), 但仍用显式表而非
# getattr(bs, api) 直查 —— 这张表本身就是"已实测可用"的白名单, 未列入的名字
# fail-closed 报未知 api, 不会意外透传到 baostock 任意属性 (含私有/非 query_* 属性)。
# ---------------------------------------------------------------------------
API_FUNCTION_NAMES: dict[str, str] = {
    "query_trade_dates": "query_trade_dates",
    "query_history_k_data_plus": "query_history_k_data_plus",
    "query_stock_basic": "query_stock_basic",
    "query_adjust_factor": "query_adjust_factor",
    "query_dividend_data": "query_dividend_data",
    "query_stock_industry": "query_stock_industry",
    "query_all_stock": "query_all_stock",
    "query_daily_history_k_AStock": "query_daily_history_k_AStock",
    "query_daily_adjust_factor": "query_daily_adjust_factor",
}


# ---------------------------------------------------------------------------
# 错误码原文核对: baostock 0.9.3 baostock/common/contants.py 的 BSERR_* 常量
# (2026-08-30 下载 PyPI 钉版 sdist 逐条核实, 非凭空编)。按官方码语义分四档:
#   caller_error       调用方错误 (参数/日期/代码不合法) — 修参数, 不可重试
#   transient_network   网络瞬时 (连接/收发失败或超时) — 可退避重试
#   account_permission  账号权限 (登录/权限/黑名单) — 需人工; 尤其黑名单绝不能当
#                        网络错误重试 (重试只会在服务端反复留下命中记录)
#   client_parse        客户端本地解析/内部错误 — 非服务端语义, 修客户端逻辑
# ---------------------------------------------------------------------------
BSERR_SUCCESS = "0"

# 调用方错误
BSERR_INPARAM_EMPTY = "10004005"
BSERR_PARAM_ERR = "10004006"
BSERR_START_DATE_ERR = "10004007"
BSERR_END_DATE_ERR = "10004008"
BSERR_START_BIGTHAN_END = "10004009"
BSERR_DATE_ERR = "10004010"
BSERR_CODE_INVALIED = "10004011"
BSERR_INDICATOR_INVALIED = "10004012"
BSERR_BEYOND_DATE_SUPPORT = "10004013"
BSERR_MIXED_CODES_MARKET = "10004014"
BSERR_NO_SUPPORT_CODES_MARKET = "10004015"
BSERR_ORDER_TO_UPPER_LIMIT = "10004016"
BSERR_NO_SUPPORT_ORDERINFO = "10004017"
BSERR_INDICATOR_REPEAT = "10004018"
BSERR_MESSAGE_ERROR = "10004019"
BSERR_MESSAGE_CODE_ERROR = "10004020"

# 网络瞬时
BSERR_SOCKET_ERR = "10002001"
BSERR_CONNECT_FAIL = "10002002"
BSERR_CONNECT_TIMEOUT = "10002003"
BSERR_RECVCONNECTION_CLOSED = "10002004"
BSERR_SENDSOCK_FAIL = "10002005"
BSERR_SENDSOCK_TIMEOUT = "10002006"
BSERR_RECVSOCK_FAIL = "10002007"
BSERR_RECVSOCK_TIMEOUT = "10002008"

# 账号权限
BSERR_NO_LOGIN = "10001001"
BSERR_USERNAMEORPASSWORD_ERR = "10001002"
BSERR_GETUSERINFO_FAIL = "10001003"
BSERR_CLIENT_VESION_EXPIRE = "10001004"
BSERR_LOGIN_COUNT_LIMIT = "10001005"
BSERR_ACCESS_INSUFFICIENCE = "10001006"
BSERR_NEED_ACTIVATE = "10001007"
BSERR_USERNAME_EMPTY = "10001008"
BSERR_PASSWORD_EMPTY = "10001009"
BSERR_LOGOUT_FAIL = "10001010"
BSERR_BLACKLIST_USER = "10001011"

# 客户端解析异常
BSERR_PARSE_DATA_ERR = "10004001"
BSERR_UNGZIP_DATA_FAIL = "10004002"
BSERR_UNKNOWN_ERR = "10004003"
BSERR_OUTOF_BOUNDS = "10004004"
BSERR_SYSTEM_ERROR = "10005001"

CALLER_ERROR = "caller_error"
TRANSIENT_NETWORK = "transient_network"
ACCOUNT_PERMISSION = "account_permission"
CLIENT_PARSE = "client_parse"

_ERROR_CODE_CLASS: dict[str, str] = {
    # 调用方错误
    BSERR_INPARAM_EMPTY: CALLER_ERROR,
    BSERR_PARAM_ERR: CALLER_ERROR,
    BSERR_START_DATE_ERR: CALLER_ERROR,
    BSERR_END_DATE_ERR: CALLER_ERROR,
    BSERR_START_BIGTHAN_END: CALLER_ERROR,
    BSERR_DATE_ERR: CALLER_ERROR,
    BSERR_CODE_INVALIED: CALLER_ERROR,
    BSERR_INDICATOR_INVALIED: CALLER_ERROR,
    BSERR_BEYOND_DATE_SUPPORT: CALLER_ERROR,
    BSERR_MIXED_CODES_MARKET: CALLER_ERROR,
    BSERR_NO_SUPPORT_CODES_MARKET: CALLER_ERROR,
    BSERR_ORDER_TO_UPPER_LIMIT: CALLER_ERROR,
    BSERR_NO_SUPPORT_ORDERINFO: CALLER_ERROR,
    BSERR_INDICATOR_REPEAT: CALLER_ERROR,
    BSERR_MESSAGE_ERROR: CALLER_ERROR,
    BSERR_MESSAGE_CODE_ERROR: CALLER_ERROR,
    # 网络瞬时
    BSERR_SOCKET_ERR: TRANSIENT_NETWORK,
    BSERR_CONNECT_FAIL: TRANSIENT_NETWORK,
    BSERR_CONNECT_TIMEOUT: TRANSIENT_NETWORK,
    BSERR_RECVCONNECTION_CLOSED: TRANSIENT_NETWORK,
    BSERR_SENDSOCK_FAIL: TRANSIENT_NETWORK,
    BSERR_SENDSOCK_TIMEOUT: TRANSIENT_NETWORK,
    BSERR_RECVSOCK_FAIL: TRANSIENT_NETWORK,
    BSERR_RECVSOCK_TIMEOUT: TRANSIENT_NETWORK,
    # 账号权限
    BSERR_NO_LOGIN: ACCOUNT_PERMISSION,
    BSERR_USERNAMEORPASSWORD_ERR: ACCOUNT_PERMISSION,
    BSERR_GETUSERINFO_FAIL: ACCOUNT_PERMISSION,
    BSERR_CLIENT_VESION_EXPIRE: ACCOUNT_PERMISSION,
    BSERR_LOGIN_COUNT_LIMIT: ACCOUNT_PERMISSION,
    BSERR_ACCESS_INSUFFICIENCE: ACCOUNT_PERMISSION,
    BSERR_NEED_ACTIVATE: ACCOUNT_PERMISSION,
    BSERR_USERNAME_EMPTY: ACCOUNT_PERMISSION,
    BSERR_PASSWORD_EMPTY: ACCOUNT_PERMISSION,
    BSERR_LOGOUT_FAIL: ACCOUNT_PERMISSION,
    BSERR_BLACKLIST_USER: ACCOUNT_PERMISSION,
    # 客户端解析异常
    BSERR_PARSE_DATA_ERR: CLIENT_PARSE,
    BSERR_UNGZIP_DATA_FAIL: CLIENT_PARSE,
    BSERR_UNKNOWN_ERR: CLIENT_PARSE,
    BSERR_OUTOF_BOUNDS: CLIENT_PARSE,
    BSERR_SYSTEM_ERROR: CLIENT_PARSE,
}


def classify_baostock_failure(exc_or_code: Any) -> str:
    """按上表四档归类。接受裸错误码字符串 / 带 ``.code`` 属性的本模块异常 / 任意异常。

    未知错误码或未识别的异常类型一律 fail-safe 归 ``client_parse`` (不可当"可重试的
    网络问题"处理) —— 唯一的例外是 Python 标准网络异常 (``TimeoutError`` /
    ``ConnectionError`` / ``OSError``)，它们才归 ``transient_network``。
    """
    code: str | None
    if isinstance(exc_or_code, str):
        code = exc_or_code
    else:
        raw = getattr(exc_or_code, "code", None)
        code = str(raw) if raw is not None else None
    if code is not None and code in _ERROR_CODE_CLASS:
        return _ERROR_CODE_CLASS[code]
    if isinstance(exc_or_code, BaseException):
        if isinstance(exc_or_code, (TimeoutError, ConnectionError, OSError)):
            return TRANSIENT_NETWORK
        return CLIENT_PARSE
    return CLIENT_PARSE


class BaostockImportError(RuntimeError):
    """``baostock`` package not importable — see backend/requirements.txt (pinned baostock==0.9.3)."""


class BaostockSessionError(RuntimeError):
    """``bs.login()``/``bs.logout()`` did not return ``error_code == BSERR_SUCCESS``."""

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        self.code = code


class BaostockQueryError(RuntimeError):
    """A ``query_*`` call returned ``error_code != BSERR_SUCCESS`` after login succeeded."""

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        self.code = code


class BaostockIntegrityError(RuntimeError):
    """Pagination ended in a state that cannot be trusted (next() truncation / count mismatch)."""


class BaostockConcurrencyError(RuntimeError):
    """baostock's client is a process-global singleton socket with no locking; single-thread only."""


def _login_with_bounded_timeout(bs: Any, *, timeout_seconds: float) -> Any:
    """坑 1 的封堵点: login 前设 socket 默认超时, login 后 try/finally 恢复。

    独立成模块级函数 (不内嵌进方法) 方便测试直接监视
    ``socket.setdefaulttimeout``/``socket.getdefaulttimeout`` 的调用序列, 不需要真连网。
    """
    if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)) \
            or timeout_seconds <= 0:
        raise ValueError("baostock timeout_seconds must be a positive number")
    previous = socket.getdefaulttimeout()
    socket.setdefaulttimeout(float(timeout_seconds))
    try:
        return bs.login()
    finally:
        socket.setdefaulttimeout(previous)


def _drain_rows(
    result: Any, *, expected_row_count: int | None = None
) -> list[dict[str, Any]]:
    """坑 2 + 坑 4 的封堵点: 手动翻页取行 (绝不调用 ``rs.get_data()``), 取完做完整性校验。

    ``rs.get_data()`` 内部用 ``DataFrame.append`` (pandas>=2.0 已删除, 坑 4) —— 本函数
    一律 ``while rs.next(): rows.append(rs.get_row_data())``，用 ``rs.fields`` 自己组装
    ``list[dict]``。

    完整性校验 (坑 2, ``ResultData.next()`` 静默截断实测): 翻页请求失败时 ``next()``
    直接 ``return False`` 且不改 ``self.error_code`` —— 常见写法只信 ``error_code=='0'``
    会被这种情况骗过 (error_code 还停在上一页成功时的值)。取完后:
      1. 结束时 ``error_code`` 仍须为 ``BSERR_SUCCESS`` —— 能抓住"翻页请求明确返回
         错误码"这一支路 (``next()`` 在这种情况下**会**更新 ``error_code``)。
      2. 若调用方传了 ``expected_row_count``，比对条数 —— 这是唯一能抓住
         "receive_data 为空但 error_code 未变"这一支路的手段 (纯计数, 不猜测)。
    """
    fields = [str(f) for f in (getattr(result, "fields", None) or [])]
    rows: list[dict[str, Any]] = []
    while result.next():
        raw_row = result.get_row_data()
        rows.append(dict(zip(fields, raw_row)))
    final_code = str(getattr(result, "error_code", None))
    if final_code != BSERR_SUCCESS:
        raise BaostockIntegrityError(
            f"baostock pagination ended with error_code={final_code!r} "
            f"error_msg={getattr(result, 'error_msg', '')!r} rows_so_far={len(rows)} "
            "— do not trust partial rows",
        )
    if expected_row_count is not None and len(rows) != expected_row_count:
        raise BaostockIntegrityError(
            f"baostock row count mismatch: got {len(rows)} rows, expected "
            f"{expected_row_count} — possible silent next() truncation (ResultData.next() "
            "can return False without changing error_code when a page request's "
            "receive_data comes back empty)",
        )
    return rows


class BaostockSource:
    """sync_runner 调用约定: ``fetch_raw(api, **params) -> list[dict]``。

    会话管理: 不每次 fetch 都 login (TCP 连接昂贵) —— 首次 fetch/显式 ``logout()`` 之间
    复用同一登录态。若某次查询返回 ``BSERR_NO_LOGIN`` (会话已断的信号), 重新 login 一次
    再重试一次该次查询; 仍失败则照常抛错 (不无限重试掩盖真故障)。

    **线程安全**: baostock 客户端是进程级全局单例 socket 且无锁 (坑 3, 见模块 docstring)。
    本类记录首次调用的 thread id, 后续不同 thread 调用直接抛 ``BaostockConcurrencyError``。
    只能单进程串行使用, 不要在它外面包线程池。
    """

    name = ALIAS

    def __init__(
        self,
        *,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        bs_module: Any | None = None,
    ) -> None:
        self._timeout_seconds = float(timeout_seconds)
        self._bs_module = bs_module  # 测试注入假对象; 生产环境首次用到时才真 import
        self._logged_in = False
        self._owner_thread_id: int | None = None

    def _check_single_thread(self) -> None:
        current = threading.get_ident()
        if self._owner_thread_id is None:
            self._owner_thread_id = current
            return
        if current != self._owner_thread_id:
            raise BaostockConcurrencyError(
                f"baostock adapter used from thread {current}, first used from "
                f"{self._owner_thread_id}; baostock's client is a process-global "
                "singleton socket with no locking (see module docstring 坑 3) — this "
                "adapter is single-process single-thread-only. Do not wrap it in a "
                "thread pool."
            )

    def _module(self) -> Any:
        if self._bs_module is None:
            try:
                import baostock as bs  # noqa: E402
            except ImportError as exc:
                raise BaostockImportError(
                    "baostock package not installed; pip install per "
                    "backend/requirements.txt (pinned baostock==0.9.3 — see the "
                    "comment above that line for why the pin matters)"
                ) from exc
            self._bs_module = bs
        return self._bs_module

    def _ensure_login(self) -> Any:
        bs = self._module()
        if self._logged_in:
            return bs
        result = _login_with_bounded_timeout(bs, timeout_seconds=self._timeout_seconds)
        code = str(getattr(result, "error_code", None))
        if code != BSERR_SUCCESS:
            raise BaostockSessionError(
                f"baostock login failed code={code} msg={getattr(result, 'error_msg', '')!r}",
                code=code,
            )
        self._logged_in = True
        return bs

    def logout(self) -> None:
        """显式登出释放服务端会话。不碰 socket 超时 (login 已 try/finally 恢复过)。"""
        self._check_single_thread()
        if not self._logged_in:
            return
        bs = self._module()
        try:
            bs.logout()
        finally:
            self._logged_in = False

    def fetch_raw(self, api: str, **params: Any) -> list[dict[str, Any]]:
        """按 api 名直调对应 ``query_*``，手动翻页取全部行，返回镜像字段的 ``list[dict]``。

        可选 ``expected_row_count`` (从 ``params`` 里取, 不透传给 baostock 查询函数):
        调用方若知道期望条数, 传入即可让 ``_drain_rows`` 做条数比对 (坑 2 的完整性兜底)。
        """
        name = str(api or "").strip()
        fn_name = API_FUNCTION_NAMES.get(name)
        if fn_name is None:
            raise KeyError(
                f"baostock: unknown api {api!r} (known: {sorted(API_FUNCTION_NAMES)})"
            )
        query_params = dict(params)
        expected_row_count = query_params.pop("expected_row_count", None)

        self._check_single_thread()
        bs = self._ensure_login()
        fn = getattr(bs, fn_name)
        result = fn(**query_params)
        code = str(getattr(result, "error_code", None))

        if code == BSERR_NO_LOGIN:
            # 会话已断的信号: 重新 login 一次再重试同一次查询一次 (不无限重试)。
            self._logged_in = False
            bs = self._ensure_login()
            fn = getattr(bs, fn_name)
            result = fn(**query_params)
            code = str(getattr(result, "error_code", None))

        if code != BSERR_SUCCESS:
            klass = classify_baostock_failure(code)
            raise BaostockQueryError(
                f"baostock api={name} failed code={code} class={klass} "
                f"msg={getattr(result, 'error_msg', '')!r}",
                code=code,
            )
        return _drain_rows(result, expected_row_count=expected_row_count)


__all__ = [
    "ACCOUNT_PERMISSION",
    "ALIAS",
    "API_FUNCTION_NAMES",
    "BSERR_BLACKLIST_USER",
    "BSERR_CONNECT_FAIL",
    "BSERR_CONNECT_TIMEOUT",
    "BSERR_NO_LOGIN",
    "BSERR_PARAM_ERR",
    "BSERR_PARSE_DATA_ERR",
    "BSERR_RECVSOCK_FAIL",
    "BSERR_SUCCESS",
    "BaostockConcurrencyError",
    "BaostockImportError",
    "BaostockIntegrityError",
    "BaostockQueryError",
    "BaostockSessionError",
    "BaostockSource",
    "CALLER_ERROR",
    "CLIENT_PARSE",
    "DEFAULT_TIMEOUT_SECONDS",
    "TRANSIENT_NETWORK",
    "classify_baostock_failure",
]
