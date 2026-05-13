"""Phase ζ — per-stock × variant Optuna 寻优 orchestrator (单一职责).

⚠ 输入: 单股单 variant 的 signals + K 线
⚠ 输出: OptimalStrategy (best hp/stop/target/trailing + metrics)

不读 DB, 不写 DB — entry script 负责 I/O.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from services.backtest.objective import make_objective, MIN_TRADED_SIGNALS
from services.backtest.realistic_engine import Bar, backtest_signals
from services.backtest.result import BacktestSummary
from services.backtest.search_space import DEFAULT_SEARCH_SPACE, SearchSpace
from services.backtest.strategy_defaults import StrategyDefaults
from services.trading_config import EXECUTION_MODEL


@dataclass(frozen=True)
class OptimalStrategy:
    """单股单 variant 寻优结果 (Phase η++++++ 含 K 线形态阈值 + 多目标 metrics)."""
    stock_code: str
    formula_id: str
    formula_variant: str
    n_signals_input: int
    optuna_n_trials: int

    # 5 维策略超参
    optimal_hp: int
    optimal_stop_pct: float
    optimal_target_pct: float
    optimal_trailing_pct: float
    optimal_buy_offset: int

    # Phase η++++++: 4 维 K 线形态过滤
    optimal_body_ratio_min: float
    optimal_lower_shadow_min: float
    optimal_close_position_min: float
    optimal_volume_relative_min: float

    # 在最佳超参下的 BacktestSummary
    summary: BacktestSummary

    # Phase η++++++: 多目标 metrics
    optimal_calmar: float
    optimal_sortino: float
    optimal_pain_index: float
    optimal_ulcer_index: float
    optimal_tail_risk: float
    optimal_stability: float

    # composite 目标函数最优值
    score: float


def optimize_stock_strategy(
    stock_code: str,
    formula_id: str,
    formula_variant: str,
    signals: list[dict],
    bars_by_stock: dict[str, list[Bar]],
    n_trials: int = 100,
    search_space: SearchSpace = DEFAULT_SEARCH_SPACE,
    optuna_seed: int = 42,
) -> Optional[OptimalStrategy]:
    """Optuna TPE 寻优 (hp, stop, target, trailing).

    Returns:
        OptimalStrategy 或 None (信号过少 / Optuna 失败)
    """
    if len(signals) < MIN_TRADED_SIGNALS:
        return None

    import optuna
    optuna.logging.set_verbosity(optuna.logging.ERROR)

    objective_fn = make_objective(signals, bars_by_stock, search_space)
    study = optuna.create_study(
        direction="maximize",
        sampler=optuna.samplers.TPESampler(seed=optuna_seed),
    )
    try:
        study.optimize(objective_fn, n_trials=n_trials, show_progress_bar=False)
    except Exception:
        return None
    if not study.trials or study.best_value < -1e8:
        return None

    bp = study.best_params
    best_hp = bp["hp"]
    best_stop = bp["stop_pct"]
    best_target = bp["target_pct"]
    best_trail = bp["trailing_pct"]
    best_buy_offset = bp.get("buy_offset", 1)
    # Phase η++++++ K 线形态阈值
    best_body  = bp.get("body_ratio_min", 0.0)
    best_lower = bp.get("lower_shadow_min", 0.0)
    best_close = bp.get("close_position_min", 0.0)
    best_vol   = bp.get("volume_relative_min", 0.0)

    # 在最佳参数下重做一次完整流程拿 summary + objectives
    # 1. K 线过滤
    from services.candle_pattern.evaluator import score_pattern_match
    from services.candle_pattern.features import compute_features_from_bars
    filtered = []
    for s in signals:
        bars = bars_by_stock.get(s["stock_code"])
        if not bars:
            continue
        sig_i = -1
        for i, b in enumerate(bars):
            if b.date == s["signal_date"]:
                sig_i = i
                break
        if sig_i < 20:
            continue
        feat = compute_features_from_bars(bars, sig_i)
        if score_pattern_match(feat,
                               body_ratio_min=best_body,
                               lower_shadow_min=best_lower,
                               close_position_min=best_close,
                               volume_relative_min=best_vol) >= 1.0:
            filtered.append(s)
    if len(filtered) < MIN_TRADED_SIGNALS:
        return None

    summary = backtest_signals(
        signals=filtered, bars_by_stock=bars_by_stock,
        stop_pct=best_stop, target_pct=best_target, trailing_pct=best_trail,
        hp_target=best_hp, execution=EXECUTION_MODEL,
        buy_offset=best_buy_offset,
    )
    if summary.n_traded < MIN_TRADED_SIGNALS:
        return None

    # 拿 trades 算 objectives
    from services.backtest.realistic_engine import simulate_trade
    from services.optimization.objectives import compute_all_objectives
    trades = []
    for s in filtered:
        bars = bars_by_stock.get(s["stock_code"])
        if not bars: continue
        t = simulate_trade(
            stock_code=s["stock_code"], signal_date=s["signal_date"], bars=bars,
            stop_pct=best_stop, target_pct=best_target,
            trailing_pct=best_trail, hp_target=best_hp,
            execution=EXECUTION_MODEL, buy_offset=best_buy_offset,
        )
        if t: trades.append(t)
    obj = compute_all_objectives(trades)
    if obj is None:
        return None

    return OptimalStrategy(
        stock_code=stock_code,
        formula_id=formula_id,
        formula_variant=formula_variant,
        n_signals_input=len(signals),
        optuna_n_trials=n_trials,
        optimal_hp=best_hp,
        optimal_stop_pct=best_stop,
        optimal_target_pct=best_target,
        optimal_trailing_pct=best_trail,
        optimal_buy_offset=best_buy_offset,
        optimal_body_ratio_min=best_body,
        optimal_lower_shadow_min=best_lower,
        optimal_close_position_min=best_close,
        optimal_volume_relative_min=best_vol,
        summary=summary,
        optimal_calmar=obj.calmar,
        optimal_sortino=obj.sortino,
        optimal_pain_index=obj.pain_index,
        optimal_ulcer_index=obj.ulcer_index,
        optimal_tail_risk=obj.tail_risk,
        optimal_stability=obj.stability,
        score=float(study.best_value),
    )
