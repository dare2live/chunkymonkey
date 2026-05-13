"""Phase η++++++ — 8 个独立 objective metric (单一职责, 纯函数).

⚠ 每个 metric 都是纯函数, 接收一组 TradeResult, 返回 float (越大越好统一约定).
⚠ 改 metric 公式 → 改这一处.

业界投资学约定:
  - 稳定收益最大化 = 高 mean_ret / 低 max_drawdown / 低 std / 短回撤期
  - 不只是 Sharpe (单笔 ret/std), 还要看路径痛苦度 (pain_index/ulcer)
  - 单笔最大亏损 (tail_risk) 是用户真正的"心脏病"
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

import numpy as np

from services.backtest.result import TradeResult


@dataclass(frozen=True)
class ObjectiveValues:
    """8 个 objective 一次性算完."""
    sharpe:        float       # 单笔 ret 均值 / 单笔 ret 标准差
    calmar:        float       # 单笔 ret 均值 / abs(平均 max_drawdown)
    sortino:       float       # 单笔 ret 均值 / 下行波动 std (只算 ret<0 的)
    pain_index:    float       # 持仓期累积 dd 时长 × 深度 (= avg(|dd|))
    ulcer_index:   float       # sqrt(mean(dd²)), 路径痛苦度
    tail_risk:     float       # 最差 5% 单笔亏损均值 (CVaR_5)
    stability:     float       # 1 - std(rolling win_rate) (winrate 一致性)
    n_traded:      int         # 实际开仓数 (供 log(1+n) 加权)


def _safe_div(a: float, b: float, default: float = 0.0) -> float:
    if b is None or abs(b) < 1e-9:
        return default
    return a / b


def compute_all_objectives(trades: list[TradeResult]) -> Optional[ObjectiveValues]:
    """单一入口: 一组 trades → 8 个 objectives."""
    if not trades:
        return None
    traded = [t for t in trades if t.exit_reason != "one_word_blocked"]
    if not traded:
        return None

    rets = np.array([t.net_ret for t in traded])
    dds  = np.array([t.max_drawdown for t in traded])  # 都 ≤ 0

    n = len(traded)
    mean_ret = float(rets.mean())
    std_ret  = float(rets.std())

    # 1. Sharpe (单笔)
    sharpe = _safe_div(mean_ret, std_ret)

    # 2. Calmar = mean_ret / abs(mean max_dd)
    abs_avg_dd = float(np.abs(dds.mean()))
    calmar = _safe_div(mean_ret, abs_avg_dd) if abs_avg_dd > 0.005 else 0.0

    # 3. Sortino = mean_ret / 下行 std (只算 ret<0)
    downside = rets[rets < 0]
    downside_std = float(downside.std()) if len(downside) > 0 else 0.0
    sortino = _safe_div(mean_ret, downside_std) if downside_std > 0 else (sharpe * 1.5)
    # 全胜或几乎全胜时 sortino → 极大, 不用 INF, 用 sharpe × 1.5 上限

    # 4. Pain Index = mean(|dd|)
    pain_index = float(np.abs(dds).mean())

    # 5. Ulcer Index = sqrt(mean(dd²))
    ulcer_index = float(np.sqrt((dds ** 2).mean()))

    # 6. Tail Risk (CVaR_5) = 最差 5% 单笔 ret 均值
    if n >= 20:
        sorted_rets = np.sort(rets)
        n_tail = max(1, int(np.ceil(n * 0.05)))
        tail_risk = float(sorted_rets[:n_tail].mean())   # 最差几笔 (负数, 越接近 0 越好)
    else:
        # 小样本: 用最差一笔
        tail_risk = float(rets.min())

    # 7. Stability (winrate 一致性) — rolling 5 笔 win_rate 的 std, 1-std (越大越稳)
    if n >= 10:
        wins = (rets > 0).astype(float)
        rolling_win = np.array([wins[max(0, i-4):i+1].mean() for i in range(4, n)])
        stab = 1.0 - float(rolling_win.std())
        stability = max(0.0, stab)
    else:
        stability = 0.5  # 中性

    return ObjectiveValues(
        sharpe=sharpe,
        calmar=calmar,
        sortino=sortino,
        pain_index=pain_index,
        ulcer_index=ulcer_index,
        tail_risk=tail_risk,
        stability=stability,
        n_traded=n,
    )
