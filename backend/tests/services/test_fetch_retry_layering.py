"""_fetch_with_retry 的分层判据接线 (刀3 下半, 2026-09-02)。

改动前, "这次失败是墙还是抖动" 只由中文文案子串表决定, 留下一个对称假阴:
**新供应商用一句表里没有的措辞说"你被封了" → 当瞬态无限退避 → 越戳越深**。
本文件的第一条测试就是钉死这个洞。

背景: 改动前 `_fetch_with_retry` 在 CI 里几乎没有覆盖 —— 仅有的两处引用
(test_sync_runner_integrity.py / test_security_day_transport_modularity.py)
都落在 ci_pytest_surface.yaml 的 `ci_test_optional` 里 (2026-07-20 起 75 个文件被排除,
六周未提升)。决定停链与否的热路径没有 CI 覆盖, 本身就是该补的缺口。
"""

from __future__ import annotations

import pytest

from services.data_sources import sync_runner as sr


class _Boom:
    """按固定次数抛出同一个异常的假 adapter。"""

    def __init__(self, exc: BaseException) -> None:
        self.exc = exc
        self.calls = 0

    def fetch_raw(self, _api, **_params):
        self.calls += 1
        raise self.exc


def _spec(source: str, *, attempts: int = 3) -> dict:
    return {
        "domain": "probe",
        "api": "probe_api",
        "source": source,
        "retry": {
            "max_attempts": attempts,
            "backoff_seconds": [1, 2, 3],
            "transient_backoff_seconds": [60, 120, 180],
        },
    }


@pytest.fixture
def sleeps(monkeypatch):
    recorded: list[float] = []
    monkeypatch.setattr(sr.time, "sleep", recorded.append)
    monkeypatch.setattr(sr, "_get_rate_limiter", lambda _spec: None)
    return recorded


def _fuyao(*, http=None, code=None, message="上游异常"):
    from services.data_sources.sources.fuyao import FuyaoRestError

    return FuyaoRestError(message, http=http, code=code)


# ── 被堵上的假阴洞 ────────────────────────────────────────────────────────


def test_structured_wall_stops_the_chain_even_with_no_text_marker(sleeps):
    """403 = 凭证被拒 = 墙, 但消息里**一个中文墙措辞都没有**。

    改动前: `_is_quota_wall("Forbidden")` 为 False → 当瞬态退避重试 3 次。
    改动后: 结构化层按 http=403 判 HARD_WALL → 立即停链。
    """
    exc = _fuyao(http=403, message="Forbidden")
    assert not sr._is_quota_wall(str(exc)), "前提: 该消息不含任何文案表里的墙措辞"

    adapter = _Boom(exc)
    with pytest.raises(sr.QuotaExhaustedError):
        sr._fetch_with_retry(adapter, _spec("fuyao"), {"trade_date": "20260901"})
    assert adapter.calls == 1, "墙必须立即停链, 不消耗重试"


def test_structural_failure_does_not_burn_retries(sleeps):
    """404 端点不存在 —— 同样的请求重试多少次都一样。"""
    adapter = _Boom(_fuyao(http=404, message="Not Found"))
    got = sr._fetch_with_retry(adapter, _spec("fuyao"), {"trade_date": "20260901"})
    assert got is None
    assert adapter.calls == 1, "结构性失败早退, 不空烧 attempts"
    assert sleeps == [], "早退不应睡"


def test_transient_by_business_code_uses_long_backoff(sleeps):
    """扶摇限流码 4001 走**属性**判定, 消息里没有 'code=4001' 字样也照样识别。"""
    exc = _fuyao(code=4001, message="上游繁忙")
    assert not sr._is_transient_ratelimit(str(exc)), "前提: 文案表认不出它"

    adapter = _Boom(exc)
    got = sr._fetch_with_retry(adapter, _spec("fuyao"), {"trade_date": "20260901"})
    assert got is None
    assert adapter.calls == 3, "瞬态应当用满 attempts"
    assert sleeps == [60, 120], "瞬态限流必须用长退避 (等窗口恢复), 不是默认的 1/2 秒"


# ── tushare 行为逐位不变 (第③层文案表仍是它唯一的信号) ────────────────────


def test_tushare_wall_prose_still_stops_the_chain(sleeps):
    """tushare 经 tinyshare 网关只吐中文散文, 结构化层对它恒返 UNKNOWN。

    这条锁住"接线不得让旧路径失效": 文案表必须仍然生效。
    """
    adapter = _Boom(RuntimeError("今日请求已达上限, 请明天再试"))
    with pytest.raises(sr.QuotaExhaustedError):
        sr._fetch_with_retry(adapter, _spec("tushare"), {"trade_date": "20260901"})
    assert adapter.calls == 1


def test_tushare_transient_prose_still_uses_long_backoff(sleeps):
    """就是当年那次事故的原文 —— 必须判成瞬态而不是当日墙。"""
    adapter = _Boom(RuntimeError("并发请求过多, 请稍后重试"))
    got = sr._fetch_with_retry(adapter, _spec("tushare"), {"trade_date": "20260901"})
    assert got is None
    assert adapter.calls == 3, "瞬态绝不停链"
    assert sleeps == [60, 120]


# ── UNKNOWN 的缺省仍是有界重试 (与改动前一致) ─────────────────────────────


def test_unknown_failure_keeps_bounded_retry(sleeps):
    """判不出 → 不猜, 走原有的有界重试。

    这是精度不足时的安全缺省: 既不弃疗 (STRUCTURAL), 也不熄灭水管 (HARD_WALL)。
    """
    adapter = _Boom(RuntimeError("???"))
    got = sr._fetch_with_retry(adapter, _spec("tdxhub"), {"trade_date": "20260901"})
    assert got is None
    assert adapter.calls == 3
    assert sleeps == [1, 2], "UNKNOWN 用普通退避, 不升级成瞬态长退避"


def test_authorization_error_still_propagates_untouched(sleeps):
    """授权异常在通用处理之前就被 re-raise —— 接线不得改变这条。"""
    from services.data_sources.sources.tushare import TuShareAuthorizationError

    adapter = _Boom(TuShareAuthorizationError("auth_expired"))
    with pytest.raises(TuShareAuthorizationError):
        sr._fetch_with_retry(adapter, _spec("tushare"), {"trade_date": "20260901"})
    assert adapter.calls == 1
