"""单测: filters.py (涨跌停 / 停牌)"""
from __future__ import annotations

import pytest


class TestInferBoard:
    def test_main_default(self):
        from services.trading_config.filters import infer_board
        assert infer_board("600000") == "main"
        assert infer_board("000001") == "main"

    def test_chinext(self):
        from services.trading_config.filters import infer_board
        assert infer_board("300033") == "chinext"
        assert infer_board("301099") == "chinext"

    def test_star(self):
        from services.trading_config.filters import infer_board
        assert infer_board("688001") == "star"

    def test_bj(self):
        from services.trading_config.filters import infer_board
        assert infer_board("430000") == "bj"


class TestLimitDetection:
    def test_main_limit_up(self):
        """主板 10% 涨停."""
        from services.trading_config.filters import LimitBoardConfig, is_limit_up
        cfg = LimitBoardConfig()
        # 10 → 11 = +10% 涨停
        assert is_limit_up(11.0, 10.0, "main", cfg) is True

    def test_main_not_limit_up_at_9pct(self):
        from services.trading_config.filters import LimitBoardConfig, is_limit_up
        cfg = LimitBoardConfig()
        assert is_limit_up(10.9, 10.0, "main", cfg) is False

    def test_chinext_limit_up_at_20pct(self):
        """创业板 20%."""
        from services.trading_config.filters import LimitBoardConfig, is_limit_up
        cfg = LimitBoardConfig()
        assert is_limit_up(12.0, 10.0, "chinext", cfg) is True
        assert is_limit_up(11.0, 10.0, "chinext", cfg) is False  # 只 10%, 没到 20

    def test_one_word_limit_up(self):
        """OHLC 全相等 + 达到涨停."""
        from services.trading_config.filters import LimitBoardConfig, is_one_word_limit_up
        cfg = LimitBoardConfig()
        assert is_one_word_limit_up(
            today_open=11.0, today_high=11.0, today_low=11.0, today_close=11.0,
            prev_close=10.0, board="main", config=cfg,
        ) is True

    def test_one_word_limit_up_high_diff_false(self):
        """OHLC 不全相等 → 不是一字板."""
        from services.trading_config.filters import LimitBoardConfig, is_one_word_limit_up
        cfg = LimitBoardConfig()
        assert is_one_word_limit_up(
            today_open=11.0, today_high=11.0, today_low=10.5, today_close=11.0,
            prev_close=10.0, board="main", config=cfg,
        ) is False

    def test_zero_prev_close_safe(self):
        from services.trading_config.filters import LimitBoardConfig, is_limit_up
        assert is_limit_up(11.0, 0, "main", LimitBoardConfig()) is False


class TestSuspended:
    def test_zero_volume(self):
        from services.trading_config.filters import is_suspended
        assert is_suspended(0) is True
        assert is_suspended(None) is True

    def test_normal(self):
        from services.trading_config.filters import is_suspended
        assert is_suspended(1000) is False
