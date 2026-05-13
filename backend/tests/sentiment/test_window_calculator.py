"""单测: window_calculator.py — 窗口聚合."""
from __future__ import annotations

import pytest


class TestComputeSurveyWindows:
    def test_empty_events(self):
        from services.sentiment.window_calculator import compute_survey_windows
        assert compute_survey_windows("A", []) == []

    def test_single_event(self):
        from services.sentiment.window_calculator import compute_survey_windows
        r = compute_survey_windows("A", [("2026-05-01", 5)])
        assert len(r) == 1
        assert r[0].count_30d == 1
        assert r[0].count_60d == 1
        assert r[0].inst_30d == 5
        assert r[0].inst_60d == 5

    def test_window_aging(self):
        """旧事件应在窗口外被淘汰."""
        from services.sentiment.window_calculator import compute_survey_windows
        events = [
            ("2026-01-01", 3),  # 距 5-01 已 120 日, 全部窗口外
            ("2026-04-15", 4),  # 距 5-01 = 16 日, 30d 内
            ("2026-03-15", 2),  # 距 5-01 = 47 日, 30d 外 60d 内
        ]
        events.sort()
        r = compute_survey_windows("A", events)
        # 在最后一个 event_date (2026-04-15) 上的窗口
        last = next(x for x in r if x.as_of_date == "2026-04-15")
        # 30d 内: 4-15, 3-15 距 2026-04-15 = 31 日, 不在 30d 内
        # 等等, 30 days, 即 [2026-03-17, 2026-04-15]
        # 3-15 = 03-15 不在 [03-17, 04-15] 内
        assert last.count_30d == 1  # 只 4-15 自己
        assert last.count_60d == 2  # 4-15 + 3-15

    def test_monotonic_30d_le_60d(self):
        from services.sentiment.window_calculator import compute_survey_windows
        events = [("2026-04-01", 3), ("2026-04-15", 5), ("2026-04-30", 2)]
        events.sort()
        r = compute_survey_windows("A", events)
        for w in r:
            assert w.count_30d <= w.count_60d
            assert w.inst_30d <= w.inst_60d


class TestDailyGridFromEvents:
    def test_grid_filters_empty(self):
        """无 events 的网格点应被跳过."""
        from services.sentiment.window_calculator import daily_grid_from_events
        out = daily_grid_from_events(
            stock_code="A", events=[("2026-05-01", 3)],
            grid_start="2026-04-01", grid_end="2026-04-30"
        )
        # 4-30 之前都没事件, 全 0, 应跳过
        assert out == []

    def test_grid_carry_with_aging(self):
        """事件后的网格日应 carry, 但 60 日后自动出窗."""
        from services.sentiment.window_calculator import daily_grid_from_events
        out = daily_grid_from_events(
            stock_code="A", events=[("2026-03-01", 5)],
            grid_start="2026-03-01", grid_end="2026-06-30"
        )
        dates = [r.as_of_date for r in out]
        # 3-01 到 4-29 (60 日窗口) 内都应有快照
        assert "2026-03-01" in dates
        assert "2026-04-29" in dates
        # 4-30 已超 60 日 (5+59=64 自然日窗口)
        # 4-30 = 60 日后 (3-02 起算), 应该已出窗
        # 我们用 long_days=60 默认, [4-30 - 59, 4-30] = [3-02, 4-30], 3-01 不在内 → 0 行
        assert "2026-04-30" not in dates

    def test_grid_with_trading_filter(self):
        from services.sentiment.window_calculator import daily_grid_from_events
        out = daily_grid_from_events(
            stock_code="A", events=[("2026-05-01", 3)],
            grid_start="2026-05-01", grid_end="2026-05-05",
            trading_dates=["2026-05-04", "2026-05-05"],  # 只这两个交易日
        )
        dates = [r.as_of_date for r in out]
        assert "2026-05-01" not in dates  # 非交易日
        assert "2026-05-04" in dates
        assert "2026-05-05" in dates
