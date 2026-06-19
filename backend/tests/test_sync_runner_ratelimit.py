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
    monkeypatch.setattr(sr, "_RATE_LIMITER", None)
    monkeypatch.setattr(sr, "_RATE_LIMITER_INIT", False)
    spec = {"rate_limit": {"per_interface_per_min": 120, "total_per_min": 200, "max_concurrency": 2}}
    rl = sr._get_rate_limiter(spec)
    assert rl is not None and rl.per_api == 120 and rl.total == 200


def test_get_rate_limiter_none_when_unconfigured(monkeypatch):
    monkeypatch.setattr(sr, "_RATE_LIMITER", None)
    monkeypatch.setattr(sr, "_RATE_LIMITER_INIT", False)
    assert sr._get_rate_limiter({}) is None, "未配置 rate_limit = 不节流 (向后兼容)"
