"""Phase δ D1 — entries.py 单测。"""
from __future__ import annotations

import pytest

from services.paper_engine.entries import compute_shares, evaluate_entry


class TestEvaluateEntry:
    def test_limit_fill_within_range(self):
        # entry_target=102 在 [101, 103] 内 → 用 102
        r = evaluate_entry(
            entry_target_price=102.0,
            entry_max_price=105.0,
            today_open=101.5, today_high=103, today_low=101, today_close=102.5,
        )
        assert r["action"] == "enter"
        assert r["reason"] == "limit_filled"
        assert r["fill_price"] == 102.0

    def test_open_above_max_reject(self):
        # 跳空开盘 = 106 > entry_max=105 → reject
        r = evaluate_entry(
            entry_target_price=102.0,
            entry_max_price=105.0,
            today_open=106, today_high=108, today_low=105.5, today_close=107,
        )
        assert r["action"] == "reject"
        assert "gap_above_max" in r["reason"]

    def test_one_word_limit_up_reject(self):
        # 一字涨停 (open=high=low ≥ 9.7%) → reject
        r = evaluate_entry(
            entry_target_price=102.0,
            entry_max_price=110.0,
            today_open=110.0, today_high=110.0, today_low=110.0, today_close=110.0,
            prev_close=100.0,
        )
        assert r["action"] == "reject"
        assert "limit_up" in r["reason"]

    def test_halted_skip(self):
        r = evaluate_entry(
            entry_target_price=102.0,
            today_open=None, today_high=None, today_low=None, today_close=None,
        )
        assert r["action"] == "skip"
        assert r["reason"] == "halted"

    def test_no_target_uses_open(self):
        # entry_target=None → 用 today_open
        r = evaluate_entry(
            entry_target_price=None,
            entry_max_price=None,
            today_open=100.0, today_high=102, today_low=99, today_close=101,
        )
        assert r["action"] == "enter"
        assert r["reason"] == "open_fallback"
        assert r["fill_price"] == 100.0

    def test_target_below_low_uses_market_open(self):
        # entry_target=95 < low=99 (没触达 entry_target), 但 open=100 ≤ entry_max=105 → 用 open
        r = evaluate_entry(
            entry_target_price=95.0,
            entry_max_price=105.0,
            today_open=100, today_high=102, today_low=99, today_close=101,
        )
        assert r["action"] == "enter"
        assert r["reason"] == "market_open"
        assert r["fill_price"] == 100.0


class TestComputeShares:
    def test_basic_100_share_floor(self):
        # 5万 × 5% = 2500 元 / 25 元/股 = 100 股
        s = compute_shares(cash_available=50000, target_weight=0.05, fill_price=25.0)
        assert s == 100

    def test_not_enough_for_one_lot(self):
        # 1万 × 1% = 100 元 < 1 手 茅台 (1800 元/股 × 100 股)
        s = compute_shares(cash_available=10000, target_weight=0.01, fill_price=1800.0)
        assert s == 0

    def test_rounds_down_to_lot(self):
        # 10万 × 10% = 1万 / 33 = 303 股, floor to 300
        s = compute_shares(cash_available=100000, target_weight=0.10, fill_price=33.0)
        assert s == 300

    def test_negative_cash_returns_zero(self):
        s = compute_shares(cash_available=-100, target_weight=0.05, fill_price=10.0)
        assert s == 0

    def test_zero_price_returns_zero(self):
        s = compute_shares(cash_available=100000, target_weight=0.05, fill_price=0.0)
        assert s == 0
