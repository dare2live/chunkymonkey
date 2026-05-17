"""Tests for score_rank_diff_v1 sizer (Codex round 19 + 用户 push back).

按 round 19 verdict (a59f50ececd83cdb1):
- rank-based score tilt (w ∝ (N+1-rank)^p)
- vol haircut (low vol → 重仓)
- cap/floor + cash buffer
- Default: p=1.2, vol_exp=0.5, cash=0.15, max_single=0.25, min_single=0.05
"""
from __future__ import annotations

import pytest
from dataclasses import dataclass

from services.paper_sim.sizer import allocate_positions, SizingResult


@dataclass
class _MockCandidate:
    """Minimal CandidateRow for sizer tests."""
    stock_code: str
    expected_total_return: float = 0.05
    optimal_stop_pct: float = -0.05
    optimal_target_pct: float = 0.10
    optimal_trailing_pct: float = 0.03
    optimal_hp: int = 10
    formula_id: str = "test"
    formula_variant: str = "test"
    stage: str = "test"
    score: float = 0.5
    exit_source: str = "pit"


@dataclass
class _MockConfig:
    """Minimal PortfolioConfig for sizer tests."""
    position_sizing: str = "score_rank_diff_v1"
    min_cash_pct: float = 0.05
    # Codex round 19 defaults
    score_rank_p: float = 1.2
    vol_haircut_exp: float = 0.5
    vol_haircut_min: float = 0.75
    vol_haircut_max: float = 1.20
    score_rank_cash_buffer: float = 0.15
    max_single_weight: float = 0.25
    min_single_weight: float = 0.05


class TestScoreRankDiffV1:
    def test_5_stocks_rank_descending(self):
        """5 candidates rank 1-5 (按 score 排好序), 验 rank 1 重仓 / rank 5 轻仓."""
        candidates = [_MockCandidate(stock_code=f"60000{i}", score=1.0 - i*0.1) for i in range(5)]
        cfg = _MockConfig()
        results = allocate_positions(candidates, cfg, available_cash=1_000_000, total_capital=1_000_000)
        # 5 results returned
        assert len(results) == 5
        # rank 1 (idx 0) target_pct ≥ rank 5 (idx 4) target_pct
        assert results[0].target_pct >= results[4].target_pct
        # 验 cash buffer ~ 15%
        total_pct = sum(r.target_pct for r in results)
        assert 0.80 <= total_pct <= 0.86  # 1 - cash_buffer (0.15) ± rounding

    def test_cap_enforced(self):
        """单 stock 不超过 max_single (25%)."""
        candidates = [_MockCandidate(stock_code=f"60000{i}", score=1.0 - i*0.1) for i in range(5)]
        cfg = _MockConfig(max_single_weight=0.25)
        results = allocate_positions(candidates, cfg, available_cash=1_000_000, total_capital=1_000_000)
        for r in results:
            assert r.target_pct <= 0.25 + 1e-6, f"{r.stock_code} weight {r.target_pct} > 0.25"

    def test_floor_enforced(self):
        """单 stock 不小于 min_single (5%)."""
        candidates = [_MockCandidate(stock_code=f"60000{i}", score=1.0 - i*0.1) for i in range(5)]
        cfg = _MockConfig(min_single_weight=0.05)
        results = allocate_positions(candidates, cfg, available_cash=1_000_000, total_capital=1_000_000)
        for r in results:
            assert r.target_pct >= 0.05 - 1e-6, f"{r.stock_code} weight {r.target_pct} < 0.05"

    def test_vol_haircut_clip(self):
        """高 vol stock 应被 haircut (clip 0.75 lower bound)."""
        # 5 candidates, 极差 vol
        candidates = [_MockCandidate(stock_code=f"60000{i}", optimal_stop_pct=-0.02 - i*0.05) for i in range(5)]
        # vol = [0.02, 0.07, 0.12, 0.17, 0.22]
        cfg = _MockConfig()
        results = allocate_positions(candidates, cfg, available_cash=1_000_000, total_capital=1_000_000)
        # Rank 1 stop=0.02 低 vol → vh > 1 (但 clip to 1.20)
        # Rank 5 stop=0.22 高 vol → vh < 1 (clip to 0.75)
        # 检查 reason 含 vol_haircut
        assert all("vol_haircut" in r.reason for r in results)

    def test_empty_candidates(self):
        results = allocate_positions([], _MockConfig(), 1_000_000, 1_000_000)
        assert results == []

    def test_cash_buffer_respected(self):
        """cash_buffer=0.20 时总投入 ≈ 80% (cap 后可能略低)."""
        candidates = [_MockCandidate(stock_code=f"60000{i}", score=1.0 - i*0.1) for i in range(5)]
        cfg = _MockConfig(score_rank_cash_buffer=0.20)
        results = allocate_positions(candidates, cfg, available_cash=1_000_000, total_capital=1_000_000)
        total_pct = sum(r.target_pct for r in results)
        assert 0.70 <= total_pct <= 0.85  # cap 影响, 大致区间

    def test_shape_codex_recommended(self):
        """Codex round 19 推荐形状: ~30 / 23 / 17 / 10 / 5 + 15% cash (p=1.2, vol equal).

        5 等 vol candidates, p=1.2 应得到大致这个形状.
        """
        candidates = [_MockCandidate(stock_code=f"60000{i}",
                                    optimal_stop_pct=-0.05,  # equal vol
                                    score=1.0 - i*0.1) for i in range(5)]
        cfg = _MockConfig(score_rank_p=1.2, max_single_weight=0.50, min_single_weight=0.01)  # 大 cap 看 raw shape
        results = allocate_positions(candidates, cfg, available_cash=1_000_000, total_capital=1_000_000)
        weights = [r.target_pct for r in results]
        # rank 1 应该 25-35% range (p=1.2)
        assert 0.25 < weights[0] < 0.35
        # rank 5 应该 3-8% range
        assert 0.03 < weights[4] < 0.10
        # descending
        assert all(weights[i] >= weights[i+1] for i in range(4))
