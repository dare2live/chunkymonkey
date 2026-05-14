"""Phase η++++++ — Optuna 目标函数 (单一职责).

⚠ Phase η++++++ 重构: 单 sharpe → 多目标 composite (含 Calmar/Sortino/Pain/Ulcer/Tail/Stability).
⚠ Phase η++++++ 加 K 线形态过滤维度 (4 个新超参).
⚠ Phase η++++++ 加硬约束 (max_dd ≤ -25% / worst ≥ -30% / streak ≤ 5 / min_traded ≥ 5).
⚠ 改目标函数 → 改 composite.py; 改硬约束 → 改 constraints.py.
"""
from __future__ import annotations

import math
from typing import Callable, Optional

from services.backtest.realistic_engine import Bar, backtest_signals
from services.backtest.result import BacktestSummary
from services.backtest.search_space import SearchSpace
from services.candle_pattern.evaluator import score_pattern_match
from services.candle_pattern.features import compute_features_from_bars
from services.optimization.composite import composite_score, DEFAULT_OBJECTIVE_WEIGHTS
from services.optimization.constraints import passes_hard_constraints, DEFAULT_CONSTRAINTS
from services.optimization.objectives import compute_all_objectives
from services.trading_config import EXECUTION_MODEL


SCORE_FAILURE = -1e9
MIN_TRADED_SIGNALS = DEFAULT_CONSTRAINTS.min_traded   # 与硬约束对齐


def make_objective(
    signals: list[dict],
    bars_by_stock: dict[str, list[Bar]],
    search_space: SearchSpace,
) -> Callable:
    """Phase η++++++ Optuna 目标函数:
       9 维超参 = 5 (hp/stop/target/trailing/buy_offset) + 4 (K 线形态阈值)
       → backtest → 多目标 composite + 硬约束.
    """
    def objective(trial):
        # 1. 采样 5 维 strategy 参数
        strat, hp, buy_offset = search_space.sample(trial)

        # 2. 采样 4 维 K 线形态过滤参数
        from services.candle_pattern.search_space import DEFAULT_PATTERN_SEARCH_SPACE
        pattern = DEFAULT_PATTERN_SEARCH_SPACE.sample(trial)

        # 3. 过滤 signals — 仅保留通过 K 线形态过滤的 signal
        # Phase ψ.β.perf: 用 _idx (含 dict cache) 替代 linear search
        from services.backtest.realistic_engine import _idx as _bar_idx
        from services.candle_pattern.features import compute_features_from_bars

        filtered = []
        for s in signals:
            bars = bars_by_stock.get(s["stock_code"])
            if not bars:
                continue
            sig_i = _bar_idx(bars, s["signal_date"])
            if sig_i < 20:
                continue
            feat = compute_features_from_bars(bars, sig_i)
            if score_pattern_match(feat, **pattern) >= 1.0:
                filtered.append(s)

        if len(filtered) < DEFAULT_CONSTRAINTS.min_traded:
            return SCORE_FAILURE

        # 4. 回测 — Phase ψ.β.perf: 一次性拿 summary + trades, 不再重跑 simulate_trade
        from services.backtest.realistic_engine import backtest_signals_with_trades
        summary, trades = backtest_signals_with_trades(
            signals=filtered,
            bars_by_stock=bars_by_stock,
            stop_pct=strat.stop_pct,
            target_pct=strat.target_pct,
            trailing_pct=strat.trailing_pct,
            hp_target=hp,
            execution=EXECUTION_MODEL,
            buy_offset=buy_offset,
        )

        # 6. 硬约束检查
        ok, fail_reason = passes_hard_constraints(trades)
        if not ok:
            trial.set_user_attr("fail_reason", fail_reason)
            return SCORE_FAILURE

        # 7. 多目标 composite
        obj = compute_all_objectives(trades)
        if obj is None:
            return SCORE_FAILURE
        score = composite_score(obj)

        # 8. 记录 user_attrs 方便事后查
        trial.set_user_attr("n_traded", obj.n_traded)
        trial.set_user_attr("calmar", obj.calmar)
        trial.set_user_attr("sortino", obj.sortino)
        trial.set_user_attr("sharpe", obj.sharpe)
        trial.set_user_attr("pain_index", obj.pain_index)
        trial.set_user_attr("ulcer", obj.ulcer_index)
        trial.set_user_attr("tail_risk", obj.tail_risk)
        trial.set_user_attr("stability", obj.stability)
        trial.set_user_attr("win_rate", summary.win_rate)
        trial.set_user_attr("avg_ret", summary.avg_ret)
        trial.set_user_attr("avg_max_dd", summary.avg_max_dd)
        return score
    return objective


# Keep old compute_score for back-compat with tests
def compute_score(summary: BacktestSummary, min_n: int = 5) -> float:
    """⚠ DEPRECATED — 仅供老 test 用. 新代码用 composite_score."""
    if summary.n_traded < min_n: return SCORE_FAILURE
    if summary.std_ret <= 0:     return SCORE_FAILURE
    sample_weight = min(1.0, summary.n_traded / (min_n * 2.0))
    return summary.sharpe * math.log(1 + summary.n_traded) * sample_weight
