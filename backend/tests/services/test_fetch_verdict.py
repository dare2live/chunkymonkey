"""fetch_verdict 分层判据 — 每源双向 (已知信号→正确档 / 未知信号→UNKNOWN)。

刀3 的核心判据是: **结构化信号优先于中文文案**。这些测试全部用合成异常,
不联网、不依赖任何供应商在线状态。
"""

from __future__ import annotations

import pytest

from services.data_sources.fetch_verdict import (
    FailureKind,
    classify_failure,
    is_hard_stop,
    should_retry_same_channel,
)


# ── baostock: 那个真陷阱 ──────────────────────────────────────────────────


def _bs_exc(code: str):
    from services.data_sources.sources.baostock import BaostockSessionError

    return BaostockSessionError(f"baostock error {code}", code=code)


def test_baostock_blacklist_is_a_hard_wall():
    assert classify_failure(_bs_exc("10001011"), source="baostock") is FailureKind.HARD_WALL


def test_baostock_not_logged_in_is_transient_not_a_wall():
    """10001001 与 10001011 在 baostock 自己的分类里同属 ACCOUNT_PERMISSION 一档。

    整档搬成 HARD_WALL 会把"会话掉线"变成"永久熄火" —— 本测试就是钉死这条不能整档搬。
    """
    kind = classify_failure(_bs_exc("10001001"), source="baostock")
    assert kind is FailureKind.TRANSIENT
    assert not is_hard_stop(kind)


@pytest.mark.parametrize("code", ["10001005", "10001010"])
def test_baostock_other_session_codes_are_transient(code):
    assert classify_failure(_bs_exc(code), source="baostock") is FailureKind.TRANSIENT


def test_baostock_real_permission_failure_is_a_hard_wall():
    # 10001002 = 用户名或密码错误 —— 重试无用, 且不是会话可恢复的那几个
    assert classify_failure(_bs_exc("10001002"), source="baostock") is FailureKind.HARD_WALL


def test_baostock_network_code_is_transient():
    # 10002007 = recv socket timeout (TRANSIENT_NETWORK 档)
    assert classify_failure(_bs_exc("10002007"), source="baostock") is FailureKind.TRANSIENT


def test_baostock_caller_error_is_structural():
    # 10004006 = 参数错误 —— 同样的请求重试多少次都一样
    assert classify_failure(_bs_exc("10004006"), source="baostock") is FailureKind.STRUCTURAL


def test_baostock_unknown_code_is_unknown_not_a_guess():
    kind = classify_failure(_bs_exc("99999999"), source="baostock")
    assert kind is FailureKind.UNKNOWN
    assert should_retry_same_channel(kind), "UNKNOWN 必须仍走有界重试 (与改动前行为一致)"


# ── fuyao: 业务码不该靠字符串匹配 ─────────────────────────────────────────


def _fy_exc(*, http=None, code=None, message="fuyao error"):
    from services.data_sources.sources.fuyao import FuyaoRestError

    return FuyaoRestError(message, http=http, code=code)


def test_fuyao_ratelimit_code_4001_is_transient_via_code_not_text():
    """4001 此前**只**存在于 sync_runner 的中文文案表里 (`"code=4001" in msg`)。

    这里用一个消息里**不含** "4001" 字样的异常, 证明判定走的是 .code 属性而不是文案。
    """
    exc = _fy_exc(code=4001, message="上游繁忙")
    assert "4001" not in str(exc)
    assert classify_failure(exc, source="fuyao") is FailureKind.TRANSIENT


@pytest.mark.parametrize("http", [401, 403])
def test_fuyao_auth_http_is_hard_wall(http):
    assert classify_failure(_fy_exc(http=http), source="fuyao") is FailureKind.HARD_WALL


def test_fuyao_404_is_structural_not_transient():
    kind = classify_failure(_fy_exc(http=404), source="fuyao")
    assert kind is FailureKind.STRUCTURAL
    assert not should_retry_same_channel(kind)


def test_fuyao_not_ready_is_transient():
    # 3002 = 数据未就绪, 稍后确实会有
    assert classify_failure(_fy_exc(code=3002), source="fuyao") is FailureKind.TRANSIENT


def test_fuyao_missing_fields_is_structural():
    from services.data_sources.sources.fuyao import FuyaoMissingFieldsError

    kind = classify_failure(FuyaoMissingFieldsError("缺列"), source="fuyao")
    assert kind is FailureKind.STRUCTURAL


def test_fuyao_unrecognised_is_unknown():
    assert classify_failure(_fy_exc(message="???"), source="fuyao") is FailureKind.UNKNOWN


# ── miaoxiang ─────────────────────────────────────────────────────────────


def test_miaoxiang_missing_field_is_structural():
    from services.data_sources.sources.miaoxiang import MiaoxiangMissingFieldError

    assert (
        classify_failure(MiaoxiangMissingFieldError("缺 SECUCODE"), source="miaoxiang")
        is FailureKind.STRUCTURAL
    )


def test_miaoxiang_truncation_is_structural():
    from services.data_sources.sources.miaoxiang import MiaoxiangTruncationError

    assert (
        classify_failure(MiaoxiangTruncationError("截断"), source="miaoxiang")
        is FailureKind.STRUCTURAL
    )


def test_miaoxiang_network_is_transient():
    assert classify_failure(TimeoutError("timed out"), source="miaoxiang") is FailureKind.TRANSIENT


def test_miaoxiang_generic_source_error_is_unknown():
    from services.data_sources.sources.miaoxiang import MiaoxiangSourceError

    assert (
        classify_failure(MiaoxiangSourceError("上游返回异常"), source="miaoxiang")
        is FailureKind.UNKNOWN
    )


# ── tdxhub: 没有状态码, 只能靠异常类 —— 这就是判据必须分层的理由 ──────────


def test_tdxhub_transport_error_is_transient():
    assert (
        classify_failure(ConnectionResetError("connection reset by peer"), source="tdxhub")
        is FailureKind.TRANSIENT
    )


def test_tdxhub_non_transport_error_is_unknown():
    assert classify_failure(ValueError("bad frame layout"), source="tdxhub") is FailureKind.UNKNOWN


# ── tushare: 文案是它唯一的信号, 只有授权到期可结构化判定 ─────────────────


def test_tushare_authorization_error_is_split_by_reason_not_by_class():
    """一个异常类装了 6 种 reason, 处置**不同** —— 按类整档搬是本模块要根治的病。

    尤其 auth_probe_unavailable: 它是"探针跑不起来 (非权限原因)"的兜底 = 网络问题。
    把它判成硬停, 一次抖动就会熄灭整条水管。
    """
    from services.data_sources.sources.tushare import TuShareAuthorizationError

    expired = classify_failure(TuShareAuthorizationError("auth_expired"), source="tushare")
    assert expired is FailureKind.AUTH_EXPIRED
    assert is_hard_stop(expired), "真到期必须停链"

    denied = classify_failure(TuShareAuthorizationError("auth_denied"), source="tushare")
    assert is_hard_stop(denied), "权限被拒重试无用"

    probe = classify_failure(
        TuShareAuthorizationError("auth_probe_unavailable"), source="tushare"
    )
    assert probe is FailureKind.TRANSIENT
    assert not is_hard_stop(probe), "探针不可用 = 网络问题, 不是墙"


def test_tushare_every_declared_reason_has_a_verdict():
    """reason 集合将来新增一个值时, 这条会红 —— 防止新 reason 静默落进 UNKNOWN。"""
    from services.data_sources.sources.tushare import (
        AUTH_FAILURE_REASONS,
        TuShareAuthorizationError,
    )

    unmapped = [
        r
        for r in sorted(AUTH_FAILURE_REASONS)
        if classify_failure(TuShareAuthorizationError(r), source="tushare")
        is FailureKind.UNKNOWN
    ]
    assert not unmapped, f"这些 reason 没有裁决, 会静默走有界重试: {unmapped}"


def test_tushare_chinese_prose_falls_through_to_unknown():
    """tushare 的中文散文**不在本层判定** —— 交回 sync_runner 的文案表 (第③层)。

    本测试锁住这个分层边界: 本层对它返 UNKNOWN, 而不是自己也去做子串匹配。
    """
    assert (
        classify_failure(RuntimeError("今日请求已达上限, 请明天再试"), source="tushare")
        is FailureKind.UNKNOWN
    )


# ── 边界 ──────────────────────────────────────────────────────────────────


def test_local_derive_sources_are_never_classified():
    """本地推导源不出网, 失败是本地代码问题, 不该被当成『换个水管试试』的理由。"""
    for src in ("calendar_rule", "stock_st_derive", "does_not_exist"):
        assert classify_failure(RuntimeError("boom"), source=src) is FailureKind.UNKNOWN


def test_classifier_never_raises_even_on_hostile_input():
    """分类器自身不得成为新的故障点。"""

    class Hostile(Exception):
        @property
        def code(self):  # noqa: ANN201
            raise RuntimeError("属性访问就炸")

    assert classify_failure(Hostile(), source="baostock") is FailureKind.UNKNOWN


def test_structural_is_the_only_kind_that_forbids_same_channel_retry():
    assert not should_retry_same_channel(FailureKind.STRUCTURAL)
    assert should_retry_same_channel(FailureKind.TRANSIENT)
    assert should_retry_same_channel(FailureKind.UNKNOWN)
    # 墙类根本不该走到"要不要重试"这一步, 但真问到也是否
    assert not should_retry_same_channel(FailureKind.HARD_WALL)
    assert not should_retry_same_channel(FailureKind.AUTH_EXPIRED)
