"""HolderResolver fallback 链测试 (mock sources).

不依赖网络. 验证:
- tier=1 成功 → 不触发 fallback
- tier=1 返回 None → 走 tier=2
- tier=1 抛异常 → 走 tier=2
- 全部失败 → SourceExhausted
- stats 统计正确
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.holders_resolver import (  # noqa: E402
    HolderResolver,
    HolderSource,
    ResolverResult,
    SourceExhausted,
)


def _make_result(source: str, source_tier: int) -> ResolverResult:
    return ResolverResult(
        holders_df=pd.DataFrame([{
            "stock_code": "600519", "report_date": "20260331",
            "holder_set": "free", "holder_rank": 1,
            "holder_name": "X", "shares_approx": 1000,
        }]),
        periods_df=pd.DataFrame(),
        raw_text=None, raw_hash=None,
        page_update_date=None,
        server_or_endpoint=f"mock-{source}",
        source=source, source_tier=source_tier,
        fetched_at=datetime.utcnow().isoformat(timespec="seconds"),
    )


class _MockSource(HolderSource):
    def __init__(self, name: str, source_tier: int, *,
                 returns="ok", error: Exception | None = None) -> None:
        self.name = name
        self.source_tier = source_tier
        self._returns = returns
        self._error = error
        self.calls = 0

    def fetch(self, symbol, *, stock_name=""):
        self.calls += 1
        if self._error is not None:
            raise self._error
        if self._returns == "none":
            return None
        if self._returns == "empty":
            r = _make_result(self.name, self.source_tier)
            r.holders_df = pd.DataFrame()  # has_data() 返回 False
            return r
        return _make_result(self.name, self.source_tier)


def test_first_source_succeeds_no_fallback():
    s1 = _MockSource("s1", 1, returns="ok")
    s2 = _MockSource("s2", 2, returns="ok")
    r = HolderResolver([s1, s2])

    result = r.fetch("600519")
    assert result.source == "s1"
    assert result.source_tier == 1
    assert s1.calls == 1
    assert s2.calls == 0  # 第一层成功, 不进 fallback


def test_first_source_returns_none_falls_to_second():
    """tier=1 返回 None (不覆盖) → tier=2 接手."""

    s1 = _MockSource("s1", 1, returns="none")
    s2 = _MockSource("s2", 2, returns="ok")
    r = HolderResolver([s1, s2])

    result = r.fetch("600519")
    assert result.source == "s2"
    assert result.source_tier == 2
    assert s1.calls == 1
    assert s2.calls == 1


def test_first_source_returns_empty_falls_to_second():
    """tier=1 返回 ResolverResult 但 holders_df 是空 → 走 tier=2."""

    s1 = _MockSource("s1", 1, returns="empty")
    s2 = _MockSource("s2", 2, returns="ok")
    r = HolderResolver([s1, s2])

    result = r.fetch("600519")
    assert result.source == "s2"


def test_first_source_raises_falls_to_second():
    """tier=1 抛异常 (例如 117 服务器全失败) → tier=2 接手."""

    s1 = _MockSource("s1", 1, error=RuntimeError("all 117 TDX servers down"))
    s2 = _MockSource("s2", 2, returns="ok")
    r = HolderResolver([s1, s2])

    result = r.fetch("600519")
    assert result.source == "s2"


def test_all_sources_fail_raises_source_exhausted():
    s1 = _MockSource("s1", 1, error=RuntimeError("err1"))
    s2 = _MockSource("s2", 2, error=RuntimeError("err2"))
    r = HolderResolver([s1, s2])

    with pytest.raises(SourceExhausted) as exc_info:
        r.fetch("600519")
    assert exc_info.value.symbol == "600519"
    assert len(exc_info.value.errors) == 2


def test_all_sources_return_none_returns_none():
    """所有源都返回 None (无异常, 但都不覆盖) → resolver 返回 None."""

    s1 = _MockSource("s1", 1, returns="none")
    s2 = _MockSource("s2", 2, returns="none")
    r = HolderResolver([s1, s2])

    assert r.fetch("600519") is None


def test_sources_tried_in_tier_order_regardless_of_input_order():
    """sources 按 source_tier 排序, 不按 list 输入顺序."""

    # 故意倒序传入
    s2 = _MockSource("s2", 2, returns="ok")
    s1 = _MockSource("s1", 1, returns="ok")
    r = HolderResolver([s2, s1])

    result = r.fetch("600519")
    # tier=1 先试, 所以 s1 中签
    assert result.source_tier == 1
    assert s1.calls == 1
    assert s2.calls == 0


def test_stats_track_attempts_and_outcomes():
    s1 = _MockSource("s1", 1, returns="none")
    s2 = _MockSource("s2", 2, error=RuntimeError("boom"))
    s3 = _MockSource("s3", 3, returns="ok")
    r = HolderResolver([s1, s2, s3])

    r.fetch("600519")
    stats = r.stats()
    assert stats["s1_attempts"] == 1 and stats["s1_no_data"] == 1
    assert stats["s2_attempts"] == 1 and stats["s2_error"] == 1
    assert stats["s3_attempts"] == 1 and stats["s3_success"] == 1


def test_empty_sources_raises():
    with pytest.raises(ValueError):
        HolderResolver([])
