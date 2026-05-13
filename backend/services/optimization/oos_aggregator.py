"""Phase ψ — Multi-window OOS metrics 聚合器 (单一职责).

⚠ R1 标准 (expanding_monthly) 跑出多个窗 OOS 后, 用此处聚合成"统一 OOS metric"入库.
⚠ 业务表存 1 行 / (stock × formula × stage) — 不存 N 行多窗. 多窗 metrics 必须聚合.

聚合原则:
  - oos_sharpe: 把所有窗的 OOS trades 合并算 sharpe (不是窗的 mean sharpe, 那会高估)
  - oos_win_rate: 全部 OOS trades 的 win_rate
  - oos_avg_ret: 全部 OOS trades 的 avg ret
  - oos_n_traded: 全部 OOS trades 数
  - oos_period_start / end: 最早 / 最晚的 OOS 窗
  - oos_n_windows: 多少个窗贡献了 OOS
  - oos_monthly_sharpe_std: 月度 sharpe std (越小越稳, 用于 robustness)
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np

from services.backtest.result import BacktestSummary, TradeResult


@dataclass(frozen=True)
class OOSAggregateResult:
    """多窗 OOS 聚合后的统一 metrics."""
    oos_sharpe:       float
    oos_win_rate:     float
    oos_avg_ret:      float
    oos_n_traded:     int
    oos_period_start: str
    oos_period_end:   str
    oos_n_windows:    int
    oos_monthly_sharpe_std: float


def aggregate_oos_metrics(
    window_results: list[dict],
) -> Optional[OOSAggregateResult]:
    """聚合多窗 OOS 结果.

    Args:
        window_results: list of dicts, 每个 dict 代表一个 OOS 窗:
            {
                "trades": list[TradeResult],   # 该窗 OOS 实测的 trades
                "test_start": str,
                "test_end": str,
            }

    Returns:
        OOSAggregateResult 或 None (所有窗都 0 trades).
    """
    if not window_results:
        return None

    # 1. 合并所有 trades
    all_trades: list[TradeResult] = []
    valid_windows: list[dict] = []
    window_sharpes: list[float] = []
    for w in window_results:
        ts = w.get("trades") or []
        if not ts:
            continue
        traded = [t for t in ts if t.exit_reason != "one_word_blocked"]
        if not traded:
            continue
        all_trades.extend(traded)
        valid_windows.append(w)
        # 该窗内 sharpe (用于算月度 sharpe std)
        rets = np.array([t.net_ret for t in traded])
        if len(rets) >= 2 and rets.std() > 0:
            window_sharpes.append(float(rets.mean() / rets.std()))

    if not all_trades:
        return None

    # 2. 算合并 sharpe / win / avg
    rets = np.array([t.net_ret for t in all_trades])
    n = len(all_trades)
    avg = float(rets.mean())
    std = float(rets.std()) if n >= 2 else 0.0
    sharpe = float(avg / std) if std > 0 else 0.0
    win = float((rets > 0).mean())

    # 3. 时间区间
    start = min(w["test_start"] for w in valid_windows)
    end   = max(w["test_end"] for w in valid_windows)

    # 4. 月度 sharpe std (稳定性指标)
    if len(window_sharpes) >= 2:
        monthly_sharpe_std = float(np.std(window_sharpes))
    else:
        monthly_sharpe_std = 0.0

    return OOSAggregateResult(
        oos_sharpe=sharpe,
        oos_win_rate=win,
        oos_avg_ret=avg,
        oos_n_traded=n,
        oos_period_start=start,
        oos_period_end=end,
        oos_n_windows=len(valid_windows),
        oos_monthly_sharpe_std=monthly_sharpe_std,
    )


def summary_from_trades(trades: list[TradeResult]) -> BacktestSummary:
    """从 trades list 直接构造 BacktestSummary (绕过 backtest_signals).

    给 oos_aggregator 用 — 多窗 OOS 跑完拿到合并 trades, 不需要重 backtest.
    """
    from services.backtest.realistic_engine import aggregate
    return aggregate(trades, n_signals=len(trades))
