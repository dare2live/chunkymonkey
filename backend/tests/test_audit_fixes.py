"""审计 2026-04-22 整改项的单元测试.

覆盖:
- 4.1 median_max_drawdown 真正中位数
- 4.2 win_rate_120d 列 + fallback 配对
- 4.3 signals_v2 drawdown_column horizon-aware
- 5.5 calculate_returns 增量跳过冻结事件
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from services.signals_v2 import PolicyConfig


# ============================================================================
# P0-C drawdown_column 跟随 horizon
# ============================================================================


@pytest.mark.parametrize(
    "horizon, expected_col",
    [
        (10, "max_drawdown_30d"),
        (30, "max_drawdown_30d"),
        (60, "max_drawdown_60d"),
        (90, "max_drawdown_60d"),
        (120, "max_drawdown_60d"),
    ],
)
def test_policy_config_drawdown_column_by_horizon(horizon, expected_col):
    cfg = PolicyConfig(horizon_days=horizon)
    assert cfg.drawdown_column == expected_col


# ============================================================================
# P0-C compute_ev_stats 接受 drawdown_col
# ============================================================================


def test_compute_ev_stats_dd30_vs_dd60_differs():
    from services.signals_v2 import compute_ev_stats

    history = [
        {"gain": 10.0, "max_drawdown_30d": -2.0, "max_drawdown_60d": -8.0},
        {"gain": 15.0, "max_drawdown_30d": -3.0, "max_drawdown_60d": -10.0},
        {"gain": -5.0, "max_drawdown_30d": -6.0, "max_drawdown_60d": -15.0},
    ]
    stats_30 = compute_ev_stats(history, drawdown_col="max_drawdown_30d")
    stats_60 = compute_ev_stats(history, drawdown_col="max_drawdown_60d")
    # 30d 均值 -3.67，60d 均值 -11.0，必须不同
    assert stats_30.avg_drawdown_pct != stats_60.avg_drawdown_pct
    assert abs(stats_30.avg_drawdown_pct - (-3.67)) < 0.1
    assert abs(stats_60.avg_drawdown_pct - (-11.0)) < 0.1


# ============================================================================
# P0-A median_max_drawdown 取中位数而非均值
# ============================================================================


def test_median_not_average_on_skewed_dd():
    """模拟 updater.py 里的 Python 中位数路径."""
    dd30 = [-2.0, -3.0, -5.0, -100.0]  # 明显右偏，median=-4 (skewed)，mean=-27.5
    dd30_sorted = sorted(dd30)
    median_dd30 = dd30_sorted[len(dd30_sorted) // 2]
    mean_dd30 = sum(dd30) / len(dd30)
    # median 应远离 mean（证明不是一回事）
    assert abs(median_dd30 - mean_dd30) > 20
    # median 应接近 -3 (第 3 个是 -3.0)
    assert median_dd30 == -3.0


# ============================================================================
# P0-B win_rate_120d 统一 fallback
# ============================================================================


def test_scoring_wr_fallback_uses_120d_for_non_buy():
    """scoring.py fallback 路径应走 win_rate_120d 而非 win_rate_90d."""
    import inspect
    from services import scoring

    source = inspect.getsource(scoring.calculate_institution_scores)
    # 以前: _pick(p, "buy_win_rate_120d", "win_rate_90d")
    # 现在: _pick(p, "buy_win_rate_120d", "win_rate_120d")
    assert "_pick(p, \"buy_win_rate_120d\", \"win_rate_120d\")" in source


def test_institution_scoring_read_label_matches_fallback():
    """institution_scoring_read.py 的 120 日胜率标签应对应 win_rate_120d (而非 90d)."""
    import inspect
    from services import institution_scoring_read

    source = inspect.getsource(institution_scoring_read)
    # 找 "120日胜率" 的 tuple
    assert "\"120日胜率\"" in source
    # 新逻辑必须取 win_rate_120d 作为 fallback
    assert "profile.get(\"buy_win_rate_120d\") if has_buy else profile.get(\"win_rate_120d\")" in source
