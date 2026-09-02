"""取数失败的**结构化**判据 — P1 channels 刀3 (2026-09-02)。

## 为什么需要这一层

在此之前, "这次失败是墙还是抖动" 完全由**中文文案子串匹配**决定
(``sync_runner._HARD_WALL_MARKERS`` / ``_TRANSIENT_RATELIMIT_MARKERS``)。两个已实证的问题:

1. **抽象已经错位**: 扶摇的业务错误**码** ``4001`` 被塞进字符串表里靠 ``"code=4001" in msg``
   匹配。错误码和错误文案是两种东西, 现在共用一个匹配机制; 每接一个新源就往表里加措辞,
   表会越来越长且没人知道哪条还有效。
2. **收窄修复留下了对称假阴**: 表曾经过宽, 把瞬态的"并发请求过多"误判成当日墙,
   导致 advrecv backfill 两次 0 行停链 (见 sync_runner 该表上方的事故注释)。修法是**收窄**;
   但收窄同时造出镜像缺陷 —— 新供应商用一句表里没有的措辞说"你今天被封了",
   ``_is_quota_wall`` 返 False → 当瞬态无限退避 → 越戳越深。
   (这是 [[feedback-warn-only-degrades-to-warn-nothing]] 的镜像: 豁免收窄同样会漏。)

channels 的 typed demotion 里 "这条通道用不了" 的判定精度**完全等于**这两个函数的精度。
判错任一方向都贵: 错降级 = 白用质量更差的备源并污染血缘; 错不降级 = 主源真死了却在死循环重试,
备源形同虚设。

## 分层判据 (有结构化信号优先用结构化信号)

    ① 供应商业务错误码 / 异常自带属性   ← 本模块
    ② Python 异常类 (超时/连接错)        ← 本模块
    ③ 中文文案子串表                     ← 留在 sync_runner, 仅当 ①② 给出 UNKNOWN 时兜底

**必然是分层而不是一刀替换**: tdxhub 走裸 TCP 行情协议, 没有 HTTP 状态码也没有业务错误码,
那条路只能靠异常类; tushare 经 tinyshare 网关只吐中文散文, 文案是它**唯一**的信号 ——
对那条水管, 文案表不是技术债, 是仅有的证据。

## UNKNOWN 是诚实的缺省, 不是兜底垃圾桶

分不清就返 UNKNOWN, **不要猜**。UNKNOWN 走既有的有界重试 (max_attempts), 行为与今天一致,
且高发本身是可观测信号 ("该给这个源补 classify 了")。把分不清的东西硬归成 STRUCTURAL
会让本可恢复的失败直接弃疗; 硬归成 HARD_WALL 会让一次抖动熄灭整条水管。
"""

from __future__ import annotations

from enum import Enum
from typing import Any


class FailureKind(str, Enum):
    """取数失败的语义分档。值即字符串, 可直接进日志/证据字段。"""

    HARD_WALL = "hard_wall"
    """账户 / 当日 / IP 级封锁。重试只会加重判定 → 该源本进程熄火, 立刻换通道。"""

    AUTH_EXPIRED = "auth_expired"
    """授权到期。处置同 HARD_WALL, 但语义单列 —— 日落门与证据链要能区分
    "被封了" 和 "到期了"。"""

    TRANSIENT = "transient"
    """限流 / 网络抖动 / 会话掉线。退避重试有意义 (会话类失败需重建会话再试)。"""

    STRUCTURAL = "structural"
    """缺字段 / 端点不存在 / 参数被拒。同样的请求重试多少次都一样 → 不重试, 直接换通道。"""

    UNKNOWN = "unknown"
    """判不出。走既有有界重试; 高发 = 该补这个源的 classify 了。"""


# ── baostock ──────────────────────────────────────────────────────────────
#
# 注意一个真陷阱: baostock 自己的 ACCOUNT_PERMISSION 一档里**同时装着**
#   10001011 BLACKLIST_USER  —— 真墙, 永不可重试
#   10001001 NO_LOGIN        —— 会话掉线, 重建即可
#   10001005 LOGIN_COUNT_LIMIT / 10001010 LOGOUT_FAIL —— 同为会话级, 可恢复
# 整档映射成 HARD_WALL 会把可恢复的掉线变成永久熄火, 正是本模块要根治的那类过宽映射。
# 故这一档**按码细分**, 不按类整档搬。

_BAOSTOCK_BLACKLIST = "10001011"
_BAOSTOCK_SESSION_RECOVERABLE = frozenset({
    "10001001",  # NO_LOGIN — 未登录/会话被踢
    "10001005",  # LOGIN_COUNT_LIMIT — 登录数超限
    "10001010",  # LOGOUT_FAIL
})


def _classify_baostock(exc: BaseException) -> FailureKind:
    from services.data_sources.sources.baostock import (
        ACCOUNT_PERMISSION,
        CALLER_ERROR,
        TRANSIENT_NETWORK,
        classify_baostock_failure,
    )

    code = getattr(exc, "code", None)
    code = str(code) if code is not None else None
    if code == _BAOSTOCK_BLACKLIST:
        return FailureKind.HARD_WALL
    if code in _BAOSTOCK_SESSION_RECOVERABLE:
        return FailureKind.TRANSIENT

    klass = classify_baostock_failure(exc)
    if klass == TRANSIENT_NETWORK:
        return FailureKind.TRANSIENT
    if klass == CALLER_ERROR:
        return FailureKind.STRUCTURAL
    if klass == ACCOUNT_PERMISSION:
        # 会话可恢复的几个码上面已拦; 剩下的是密码错/未激活/权限不足 —— 重试无用。
        return FailureKind.HARD_WALL
    # CLIENT_PARSE 这一档把真正的解析错与 UNKNOWN_ERR / SYSTEM_ERROR 混在一起,
    # 粒度不足以做 STRUCTURAL 这种"别再试了"的判定 → 诚实返 UNKNOWN。
    return FailureKind.UNKNOWN


# ── fuyao ─────────────────────────────────────────────────────────────────

_FUYAO_CLASS_MAP = {
    "auth": FailureKind.HARD_WALL,           # 401/403/2002/2004 — 凭证问题, 重试无用
    "http_404": FailureKind.STRUCTURAL,      # 端点/路径不存在
    "product_mismatch": FailureKind.STRUCTURAL,
    "missing_fields": FailureKind.STRUCTURAL,
    "not_ready": FailureKind.TRANSIENT,      # 3002 数据未就绪 — 稍后确实会有
    "timeout": FailureKind.TRANSIENT,
    "connection_failure": FailureKind.TRANSIENT,
}


def _classify_fuyao(exc: BaseException) -> FailureKind:
    from services.data_sources.sources.fuyao import classify_fuyao_failure

    # 限流码 4001: 扶摇官方文档明示应指数退避重试。此前它**只**存在于 sync_runner 的
    # 中文文案表里 (`"code=4001" in msg`) —— 一个业务码靠字符串匹配, 是抽象错位的活样本。
    code = getattr(exc, "code", None)
    if code == 4001 or str(code) == "4001":
        return FailureKind.TRANSIENT

    return _FUYAO_CLASS_MAP.get(classify_fuyao_failure(exc), FailureKind.UNKNOWN)


# ── miaoxiang ─────────────────────────────────────────────────────────────


def _classify_miaoxiang(exc: BaseException) -> FailureKind:
    from services.data_sources.sources.miaoxiang import (
        MiaoxiangMissingFieldError,
        MiaoxiangTruncationError,
    )

    if isinstance(exc, (MiaoxiangMissingFieldError, MiaoxiangTruncationError)):
        return FailureKind.STRUCTURAL
    if isinstance(exc, (TimeoutError, ConnectionError, OSError)):
        return FailureKind.TRANSIENT
    # MiaoxiangSourceError 覆盖参数错与上游返回异常两种情况, 粒度不足以分档 → UNKNOWN。
    return FailureKind.UNKNOWN


# ── tdxhub ────────────────────────────────────────────────────────────────


def _classify_tdxhub(exc: BaseException) -> FailureKind:
    """通达信走裸 TCP 行情协议, **没有** HTTP 状态码也没有业务错误码 ——
    这条路只能靠异常类, 这正是判据必须分层而非一刀替换的原因。"""
    from services.data_sources.sources.tdxhub import is_hq_transport_error

    if is_hq_transport_error(exc):
        return FailureKind.TRANSIENT
    if isinstance(exc, (TimeoutError, ConnectionError, OSError)):
        return FailureKind.TRANSIENT
    return FailureKind.UNKNOWN


# ── tushare ───────────────────────────────────────────────────────────────


# TuShareAuthorizationError 是一个异常类装了 6 种 reason, 它们的正确处置**不同** ——
# 与 baostock ACCOUNT_PERMISSION 那个陷阱同型: 按类整档搬会把可恢复的失败判成永久熄火。
# 尤其 auth_probe_unavailable: 它是"探针本身跑不起来 (非权限原因)"的兜底
# (tushare.py:166/185/195, 即网络/超时), 判成硬停会让一次抖动熄灭整条水管。
_TUSHARE_REASON_KIND = {
    "auth_expired": FailureKind.AUTH_EXPIRED,       # 真到期
    "auth_denied": FailureKind.HARD_WALL,           # 权限被拒
    "missing_token": FailureKind.HARD_WALL,         # 配置缺失, 重试无用
    "package_missing": FailureKind.HARD_WALL,       # 依赖缺失, 重试无用
    "auth_metadata_invalid": FailureKind.HARD_WALL,  # 元数据不可解析
    "auth_probe_unavailable": FailureKind.TRANSIENT,  # 探针跑不起来 = 网络问题
}


def _classify_tushare(exc: BaseException) -> FailureKind:
    """tushare 经 tinyshare 网关只吐中文散文, 无状态码无业务码。
    唯一能结构化判定的是授权异常 (且必须**按 reason 细分**, 见上表);
    其余交回 sync_runner 的文案表 (第③层)。对这条水管, 文案不是技术债 —— 是仅有的信号。"""
    try:
        from services.data_sources.sources.tushare import TuShareAuthorizationError
    except Exception:  # noqa: BLE001 — 该模块导入失败不应连累分类
        return FailureKind.UNKNOWN
    if isinstance(exc, TuShareAuthorizationError):
        return _TUSHARE_REASON_KIND.get(
            getattr(exc, "reason", None), FailureKind.UNKNOWN
        )
    return FailureKind.UNKNOWN


_DISPATCH = {
    "baostock": _classify_baostock,
    "fuyao": _classify_fuyao,
    "miaoxiang": _classify_miaoxiang,
    "tdxhub": _classify_tdxhub,
    "tushare": _classify_tushare,
}


def classify_failure(exc: BaseException, *, source: str) -> FailureKind:
    """第①②层判据。判不出返 UNKNOWN, 由调用方决定是否走第③层文案表。

    对没有登记 mapper 的源 (如 calendar_rule / stock_st_derive 这类本地推导) 一律 UNKNOWN ——
    它们不出网, 失败是本地代码问题, 不该被当成"换个水管试试"的理由。
    """
    mapper = _DISPATCH.get(source)
    if mapper is None:
        return FailureKind.UNKNOWN
    try:
        return mapper(exc)
    except Exception:  # noqa: BLE001 — 分类器自身不得成为新的故障点
        return FailureKind.UNKNOWN


def is_hard_stop(kind: FailureKind) -> bool:
    """该档是否意味着"这条水管本进程别再用了"。"""
    return kind in (FailureKind.HARD_WALL, FailureKind.AUTH_EXPIRED)


def should_retry_same_channel(kind: FailureKind) -> bool:
    """该档是否值得在**同一条通道**上退避重试。

    STRUCTURAL 明确不值得 (同样的请求重试多少次都一样);
    UNKNOWN 值得 —— 有界重试是判不出时的安全缺省, 且与改动前行为一致。
    """
    return kind in (FailureKind.TRANSIENT, FailureKind.UNKNOWN)
