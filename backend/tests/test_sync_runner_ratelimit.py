"""tinyshare 主动节流 _RateLimiter 单测 (2026-06-19, 用户: 限流规则进 config + 流程强制).

验: 单接口/全接口 每分钟滑窗上限触发主动睡眠 (撞墙前先睡); under-limit 不睡; config 驱动。
用假时钟避免真等 60s。
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from services.data_sources import sync_runner as sr  # noqa: E402


def _fake_clock(monkeypatch):
    clock = [1000.0]
    sleeps: list[float] = []
    monkeypatch.setattr(sr.time, "time", lambda: clock[0])

    def fake_sleep(s):
        sleeps.append(s)
        clock[0] += s  # 推进时钟, 让滑窗 evict

    monkeypatch.setattr(sr.time, "sleep", fake_sleep)
    return clock, sleeps


def test_under_limit_no_throttle(monkeypatch):
    _clock, sleeps = _fake_clock(monkeypatch)
    rl = sr._RateLimiter(per_interface_per_min=10, total_per_min=20)
    for _ in range(5):
        rl.acquire("daily")
    assert sleeps == [], "under-limit 不应节流"


def test_per_interface_limit_throttles(monkeypatch):
    _clock, sleeps = _fake_clock(monkeypatch)
    rl = sr._RateLimiter(per_interface_per_min=2, total_per_min=100)
    rl.acquire("stk_factor_pro")   # 1
    rl.acquire("stk_factor_pro")   # 2
    rl.acquire("stk_factor_pro")   # 3 → 超单接口 2/分 → 主动睡到窗口 evict
    assert sleeps, "第3次超单接口上限应主动节流睡眠"
    assert sleeps[0] >= 60.0, "应睡满 ~60s 滑窗"


def test_total_limit_throttles_across_interfaces(monkeypatch):
    _clock, sleeps = _fake_clock(monkeypatch)
    rl = sr._RateLimiter(per_interface_per_min=100, total_per_min=2)
    rl.acquire("daily")            # 1 (全接口)
    rl.acquire("moneyflow")        # 2 (全接口)
    rl.acquire("share_float")      # 3 → 超全接口 2/分 (跨接口合计) → 主动睡
    assert sleeps, "第3次跨接口超全接口合计上限应主动节流"


def test_get_rate_limiter_from_config(monkeypatch):
    # 重置单例 (避免其他测试污染)
    monkeypatch.setattr(sr, "_RATE_LIMITERS", {})
    spec = {"rate_limit": {"per_interface_per_min": 120, "total_per_min": 200, "max_concurrency": 2}}
    rl = sr._get_rate_limiter(spec)
    assert rl is not None and rl.per_api == 120 and rl.total == 200


def test_get_rate_limiter_none_when_unconfigured(monkeypatch):
    monkeypatch.setattr(sr, "_RATE_LIMITERS", {})
    assert sr._get_rate_limiter({}) is None, "未配置 rate_limit = 不节流 (向后兼容)"

def test_rate_limiter_reused_within_same_source(monkeypatch):
    """同一 source 必须复用同一实例 —— 每次新建等于滑动窗口状态清零, 节流失效。"""
    monkeypatch.setattr(sr, "_RATE_LIMITERS", {})
    cfg = {"per_interface_per_min": 120, "total_per_min": 200}
    first = sr._get_rate_limiter({"source": "tushare", "rate_limit": cfg, "api": "x"})
    second = sr._get_rate_limiter({"source": "tushare", "rate_limit": cfg, "api": "y"})
    assert first is second


def test_rate_limiter_isolated_across_sources(monkeypatch):
    """不同 source 各自持有独立实例与独立配额。"""
    monkeypatch.setattr(sr, "_RATE_LIMITERS", {})
    tu = sr._get_rate_limiter(
        {"source": "tushare", "rate_limit": {"per_interface_per_min": 120, "total_per_min": 200}, "api": "x"}
    )
    fy = sr._get_rate_limiter(
        {"source": "fuyao", "rate_limit": {"per_interface_per_min": 5, "total_per_min": 5}, "api": "z"}
    )
    assert tu is not fy
    assert tu.per_api == 120 and fy.per_api == 5


def test_first_source_does_not_lock_config_for_later_sources(monkeypatch):
    """回归: 旧实现是全局单例, 第一个跑到的 domain 会锁死配额, 后来的 source 拿不到自己的。"""
    monkeypatch.setattr(sr, "_RATE_LIMITERS", {})
    sr._get_rate_limiter(
        {"source": "fuyao", "rate_limit": {"per_interface_per_min": 5, "total_per_min": 5}, "api": "z"}
    )
    tu = sr._get_rate_limiter(
        {"source": "tushare", "rate_limit": {"per_interface_per_min": 120, "total_per_min": 200}, "api": "x"}
    )
    assert tu.per_api == 120


def test_unconfigured_source_caches_none(monkeypatch):
    """未配 rate_limit 的 source 返回 None, 且该 None 被缓存(不必每次重解析 spec)。"""
    monkeypatch.setattr(sr, "_RATE_LIMITERS", {})
    assert sr._get_rate_limiter({"source": "baostock", "api": "w"}) is None
    assert sr._get_rate_limiter({"source": "baostock", "api": "w2"}) is None
    assert sr._RATE_LIMITERS["baostock"] is None


def test_transient_ratelimit_markers_cover_fuyao_and_tushare():
    """瞬态限流判定需同时认得 TuShare 中文措辞与扶摇业务错误码 4001, 且不误判其它错误。"""
    assert sr._is_transient_ratelimit("code=4001 message=rate limited")
    assert sr._is_transient_ratelimit("抱歉，您每分钟最多访问该接口120次，请稍后重试")
    assert sr._is_transient_ratelimit("并发请求过多")
    assert not sr._is_transient_ratelimit("code=3002 message=data not ready")
    assert not sr._is_transient_ratelimit("code=1002 message=invalid parameter format")
    assert not sr._is_transient_ratelimit("http 404 not found")
