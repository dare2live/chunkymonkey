"""Phase δ D1 — exits.py 单测。"""
from __future__ import annotations

import pytest

from services.paper_engine.exits import (
    LIMIT_THRESHOLD,
    evaluate_exit,
    is_limit_down_day,
    is_limit_up_day,
)


class TestLimitGate:
    def test_limit_up(self):
        assert is_limit_up_day(open_p=11.0, prev_close=10.0) is True
        assert is_limit_up_day(open_p=10.5, prev_close=10.0) is False

    def test_limit_down(self):
        assert is_limit_down_day(open_p=9.0, prev_close=10.0) is True
        assert is_limit_down_day(open_p=9.5, prev_close=10.0) is False

    def test_none_prev_close(self):
        assert is_limit_up_day(open_p=10.0, prev_close=None) is False


class TestEvaluateExit:
    def test_hold_when_no_trigger(self):
        r = evaluate_exit(
            holding_days=10, expected_horizon=20,
            exit_stop_price=90.0, exit_target_1_price=110.0,
            today_open=100, today_high=102, today_low=99, today_close=101,
        )
        assert r["action"] == "hold"
        assert r["reason"] is None

    def test_stop_hit_at_low(self):
        # 今日 low=88 < stop=90 → 触发止损 (用 stop_price fill)
        r = evaluate_exit(
            holding_days=10, expected_horizon=20,
            exit_stop_price=90.0, exit_target_1_price=110.0,
            today_open=95, today_high=96, today_low=88, today_close=89,
        )
        assert r["action"] == "exit"
        assert r["reason"] == "stop"
        assert r["fill_price"] == 90.0

    def test_target_hit_at_high(self):
        # high=112 ≥ target=110 → 止盈
        r = evaluate_exit(
            holding_days=10, expected_horizon=20,
            exit_stop_price=90.0, exit_target_1_price=110.0,
            today_open=108, today_high=112, today_low=107, today_close=111,
        )
        assert r["action"] == "exit"
        assert r["reason"] == "target_1"
        assert r["fill_price"] == 110.0

    def test_horizon_expiry(self):
        r = evaluate_exit(
            holding_days=21, expected_horizon=20,
            exit_stop_price=90.0, exit_target_1_price=110.0,
            today_open=100, today_high=102, today_low=99, today_close=101,
        )
        assert r["action"] == "exit"
        assert r["reason"] == "horizon"
        assert r["fill_price"] == 101  # today_close

    def test_priority_stop_over_target(self):
        # 同日穿透两端 (波动很大): 止损优先 (保守)
        r = evaluate_exit(
            holding_days=10, expected_horizon=20,
            exit_stop_price=90.0, exit_target_1_price=110.0,
            today_open=100, today_high=112, today_low=88, today_close=100,
        )
        assert r["action"] == "exit"
        assert r["reason"] == "stop"

    def test_limit_up_blocks_target(self):
        # 一字涨停 → 目标不可达 (待 D+1)
        r = evaluate_exit(
            holding_days=10, expected_horizon=20,
            exit_stop_price=90.0, exit_target_1_price=110.0,
            today_open=111, today_high=111, today_low=111, today_close=111,
            prev_close=100.0,
        )
        assert r["action"] == "hold"
        assert r["reason"] == "blocked_limit"

    def test_limit_down_forces_stop_at_close(self):
        # 一字跌停 → stop_price 不可达, 用 close fill (gap 损失)
        r = evaluate_exit(
            holding_days=10, expected_horizon=20,
            exit_stop_price=95.0, exit_target_1_price=110.0,
            today_open=90, today_high=90, today_low=90, today_close=90,
            prev_close=100.0,
        )
        assert r["action"] == "exit"
        assert r["reason"] == "stop"
        assert r["fill_price"] == 90  # today_close, 非 stop_price 95

    def test_halted_stock(self):
        # 停牌: 无 open/close
        r = evaluate_exit(
            holding_days=10, expected_horizon=20,
            exit_stop_price=90.0, exit_target_1_price=110.0,
            today_open=None, today_high=None, today_low=None, today_close=None,
        )
        assert r["action"] == "hold"
        assert r["reason"] == "halted"

    def test_horizon_blocked_by_limit_down(self):
        # horizon 到期但跌停, blocked (next day 再试)
        r = evaluate_exit(
            holding_days=21, expected_horizon=20,
            exit_stop_price=70.0, exit_target_1_price=110.0,
            today_open=90, today_high=90, today_low=90, today_close=90,
            prev_close=100.0,
        )
        assert r["action"] == "hold"
        assert r["reason"] == "blocked_limit"

    def test_no_stop_or_target_only_horizon(self):
        # 没设 stop/target, 只看 horizon
        r = evaluate_exit(
            holding_days=21, expected_horizon=20,
            exit_stop_price=None, exit_target_1_price=None,
            today_open=100, today_high=102, today_low=99, today_close=101,
        )
        assert r["action"] == "exit"
        assert r["reason"] == "horizon"
