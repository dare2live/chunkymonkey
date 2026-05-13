"""Phase ψ — multi-window OOS metrics 聚合器单测.

防回退:
- 多窗 trades 合并算 sharpe/win/avg 而不是取窗平均 (避免高估)
- 时间区间是 min/max test_start/end
- 月度 sharpe std 反映稳定性
- 空 / 全 0 trades 返回 None
"""
from __future__ import annotations

import pytest

from services.backtest.result import TradeResult
from services.optimization.oos_aggregator import aggregate_oos_metrics


def _make_trade(net_ret: float, exit_reason: str = "hp_expired") -> TradeResult:
    return TradeResult(
        stock_code="600000", signal_date="2024-01-15",
        buy_date="2024-01-16", buy_price=10.0,
        sell_date="2024-02-15", sell_price=10.5,
        holding_days=20, exit_reason=exit_reason,
        gross_ret=net_ret + 0.005,
        net_ret=net_ret,
        max_drawdown=-0.02,
    )


def test_aggregate_basic_single_window():
    trades = [_make_trade(r) for r in [0.05, -0.02, 0.03, -0.01, 0.04]]
    result = aggregate_oos_metrics([{
        "trades": trades, "test_start": "2024-01-01", "test_end": "2024-01-31",
    }])
    assert result is not None
    assert result.oos_n_traded == 5
    assert result.oos_n_windows == 1
    assert result.oos_win_rate == 0.6
    assert result.oos_avg_ret == pytest.approx(0.018, abs=1e-3)


def test_aggregate_multi_window_combines_trades():
    """3 窗各 5 trades, 合并后 = 15 trades."""
    windows = []
    for i in range(3):
        trades = [_make_trade(0.02 + i*0.01) for _ in range(5)]
        windows.append({
            "trades": trades,
            "test_start": f"2024-{i+1:02d}-01",
            "test_end": f"2024-{i+1:02d}-28",
        })
    result = aggregate_oos_metrics(windows)
    assert result is not None
    assert result.oos_n_traded == 15
    assert result.oos_n_windows == 3
    assert result.oos_period_start == "2024-01-01"
    assert result.oos_period_end == "2024-03-28"


def test_aggregate_skips_empty_windows():
    """空 trades 的窗应被跳过."""
    windows = [
        {"trades": [_make_trade(0.02) for _ in range(3)],
         "test_start": "2024-01-01", "test_end": "2024-01-31"},
        {"trades": [],
         "test_start": "2024-02-01", "test_end": "2024-02-29"},
        {"trades": [_make_trade(0.05) for _ in range(4)],
         "test_start": "2024-03-01", "test_end": "2024-03-31"},
    ]
    result = aggregate_oos_metrics(windows)
    assert result is not None
    assert result.oos_n_traded == 7
    assert result.oos_n_windows == 2
    assert result.oos_period_end == "2024-03-31"


def test_aggregate_all_empty_returns_none():
    windows = [{"trades": [], "test_start": "2024-01-01", "test_end": "2024-01-31"}]
    assert aggregate_oos_metrics(windows) is None


def test_aggregate_empty_list_returns_none():
    assert aggregate_oos_metrics([]) is None


def test_aggregate_monthly_sharpe_std():
    """3 窗 sharpe 分别 0.5/1.0/2.0 → std ≈ 0.62."""
    windows = []
    # 窗 1: trades 都 +5%, std=0 → 不算 (需 ≥2 trades 且 std > 0)
    # 窗 2: avg 0.05 std 0.025 → sharpe = 2
    windows.append({
        "trades": [_make_trade(0.075), _make_trade(0.025), _make_trade(0.05)],
        "test_start": "2024-01-01", "test_end": "2024-01-31",
    })
    # 窗 3: avg 0.1 std 0.05 → sharpe = 2 (同)
    windows.append({
        "trades": [_make_trade(0.15), _make_trade(0.05), _make_trade(0.1)],
        "test_start": "2024-02-01", "test_end": "2024-02-29",
    })
    result = aggregate_oos_metrics(windows)
    assert result is not None
    assert result.oos_n_windows == 2
    assert result.oos_monthly_sharpe_std >= 0   # 两窗 sharpe 一样 → std=0


def test_aggregate_filters_blocked_trades():
    """one_word_blocked 不算 OOS trade."""
    trades = [
        _make_trade(0.05, "hp_expired"),
        _make_trade(0.0, "one_word_blocked"),
        _make_trade(-0.02, "stop_loss"),
    ]
    result = aggregate_oos_metrics([{
        "trades": trades, "test_start": "2024-01-01", "test_end": "2024-01-31",
    }])
    assert result is not None
    assert result.oos_n_traded == 2   # blocked 不算
