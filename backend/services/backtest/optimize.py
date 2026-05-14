"""Phase ζ + ψ — per-stock × variant Optuna 寻优 orchestrator (Config-driven, 单一职责).

⚠ Phase ψ: 强制 walk-forward / OOS 验证 (反 in-sample leakage).
⚠ Rule 7: 所有参数 (n_trials / seed / walk_forward_mode / train_ratio) 走
   backend/config/optuna_config.yaml, 不许 hardcode.

⚠ 输入: 单股单 variant 的 signals + K 线
⚠ 输出: OptimalStrategy (best hp/stop/target/trailing + in-sample 描述 + 多窗聚合 OOS metrics)

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
from services.optimization.config import OptunaConfig, get_optuna_config
from services.optimization.governance import enforce_pre_optimize
from services.optimization.oos_aggregator import aggregate_oos_metrics
from services.optimization.walk_forward import (
    WalkForwardMode, WalkForwardSplit,
    assert_no_temporal_leak, split_dispatch,
)
from services.trading_config import EXECUTION_MODEL


@dataclass(frozen=True)
class OptimalStrategy:
    """单股单 variant 寻优结果 (Phase ψ 含 R1 多窗聚合 OOS metrics)."""
    stock_code: str
    formula_id: str
    formula_variant: str
    n_signals_input: int
    optuna_n_trials: int

    # 5 维 strategy 超参 (Optuna 选出来的 best)
    optimal_hp: int
    optimal_stop_pct: float
    optimal_target_pct: float
    optimal_trailing_pct: float
    optimal_buy_offset: int

    # 4 维 K 线形态过滤超参
    optimal_body_ratio_min: float
    optimal_lower_shadow_min: float
    optimal_close_position_min: float
    optimal_volume_relative_min: float

    # ── in-sample (train 集) metrics — 仅描述, 业务代码不读 ──
    summary: BacktestSummary
    optimal_calmar: float
    optimal_sortino: float
    optimal_pain_index: float
    optimal_ulcer_index: float
    optimal_tail_risk: float
    optimal_stability: float
    score: float

    # ── Phase ψ OOS validation (业务真值, selector / ranker 只读这些) ──
    walk_forward_mode: str
    train_n_signals: int
    test_n_signals: int
    oos_sharpe: float
    oos_win_rate: float
    oos_avg_ret: float
    oos_n_traded: int
    oos_period_start: str
    oos_period_end: str
    oos_n_windows: int                 # expanding_monthly 多窗时 > 1
    oos_monthly_sharpe_std: float      # 月度 sharpe std (稳定性)


def optimize_stock_strategy(
    stock_code: str,
    formula_id: str,
    formula_variant: str,
    signals: list[dict],
    bars_by_stock: dict[str, list[Bar]],
    n_trials: Optional[int] = None,
    search_space: Optional[SearchSpace] = None,
    optuna_seed: Optional[int] = None,
    walk_forward_mode: Optional[WalkForwardMode] = None,
    cfg: Optional[OptunaConfig] = None,
) -> Optional[OptimalStrategy]:
    """Optuna TPE 寻优 + 强制 walk-forward / OOS 验证.

    ⚠ Rule 7: 默认全部参数走 cfg (yaml). 显式传 override 用于单测 / 调试.

    Args:
        n_trials:          默认 cfg.execution.n_trials
        search_space:      默认 DEFAULT_SEARCH_SPACE (cfg.search_space.strategy)
        optuna_seed:       默认 cfg.governance.default_optuna_seed
        walk_forward_mode: 默认 cfg.walk_forward.default_mode ('expanding_monthly' = R1)

    Returns:
        OptimalStrategy 或 None (信号过少 / Optuna 失败 / OOS 无样本)
    """
    cfg = cfg or get_optuna_config()
    n_trials = n_trials if n_trials is not None else cfg.execution.n_trials
    search_space = search_space or DEFAULT_SEARCH_SPACE
    optuna_seed = optuna_seed if optuna_seed is not None else cfg.governance.default_optuna_seed
    wf_mode = walk_forward_mode or cfg.walk_forward.default_mode

    if len(signals) < MIN_TRADED_SIGNALS:
        return None

    # ━━━ 1. 时序切分 (R1 expanding_monthly 多窗 / holdout 单窗) ━━━
    splits = split_dispatch(signals, mode=wf_mode, cfg=cfg)
    if not splits:
        return None
    for split in splits:
        assert_no_temporal_leak(split)

    # ━━━ 2. Optuna 在 FIRST 窗的 train 上调参 ━━━
    # R1 设计: 用第一个窗的 train 集 (=前 6 个月) 做参数选择, 然后用 best params
    # 在每个窗的 test 上 OOS 评估, 多窗 OOS 拼起来. 这样保证:
    #   - params 选择只用最早数据 (不偷看后面)
    #   - OOS 评估覆盖所有后续月份 (统计可靠)
    #
    # 备选实现: 每窗各自跑 Optuna (更严格但 8 worker × N 窗 × 100 trial 算力开销大),
    # 留作 R1-PLUS 后续优化.
    first_split = splits[0]
    train_signals = first_split.train

    enforce_pre_optimize(n_trials=n_trials, has_seed=True, cfg=cfg)

    import optuna
    optuna.logging.set_verbosity(optuna.logging.ERROR)

    objective_fn = make_objective(train_signals, bars_by_stock, search_space)
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
    best_body  = bp.get("body_ratio_min", 0.0)
    best_lower = bp.get("lower_shadow_min", 0.0)
    best_close = bp.get("close_position_min", 0.0)
    best_vol   = bp.get("volume_relative_min", 0.0)

    # ━━━ 3. in-sample summary (在 train 集上用 best params 跑一遍, 仅描述) ━━━
    in_sample_summary, in_sample_obj = _evaluate_at_params(
        signals=train_signals, bars_by_stock=bars_by_stock,
        best_stop=best_stop, best_target=best_target, best_trail=best_trail,
        best_hp=best_hp, best_buy_offset=best_buy_offset,
        best_body=best_body, best_lower=best_lower,
        best_close=best_close, best_vol=best_vol,
    )
    if in_sample_summary is None or in_sample_obj is None:
        return None

    # ━━━ 4. OOS 评估 (R1 核心: 在每个 split.test 上用 best params 跑, 多窗聚合) ━━━
    if wf_mode == "none":
        # in-sample 模式: 跳过 OOS, governance 会拒入业务表
        return _build_result_none(
            stock_code, formula_id, formula_variant, len(signals), n_trials,
            best_hp, best_stop, best_target, best_trail, best_buy_offset,
            best_body, best_lower, best_close, best_vol,
            in_sample_summary, in_sample_obj, study.best_value,
            first_split, wf_mode,
        )

    window_results: list[dict] = []
    total_test_n_signals = 0
    for split in splits:
        if not split.test:
            continue
        trades = _trades_at_params(
            signals=split.test, bars_by_stock=bars_by_stock,
            best_stop=best_stop, best_target=best_target, best_trail=best_trail,
            best_hp=best_hp, best_buy_offset=best_buy_offset,
            best_body=best_body, best_lower=best_lower,
            best_close=best_close, best_vol=best_vol,
        )
        if trades:
            window_results.append({
                "trades": trades,
                "test_start": split.test_start,
                "test_end": split.test_end,
            })
            total_test_n_signals += split.n_test

    if not window_results:
        return None

    agg = aggregate_oos_metrics(window_results)
    if agg is None:
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
        summary=in_sample_summary,
        optimal_calmar=in_sample_obj.calmar,
        optimal_sortino=in_sample_obj.sortino,
        optimal_pain_index=in_sample_obj.pain_index,
        optimal_ulcer_index=in_sample_obj.ulcer_index,
        optimal_tail_risk=in_sample_obj.tail_risk,
        optimal_stability=in_sample_obj.stability,
        score=float(study.best_value),
        walk_forward_mode=wf_mode,
        train_n_signals=first_split.n_train,
        test_n_signals=total_test_n_signals,
        oos_sharpe=agg.oos_sharpe,
        oos_win_rate=agg.oos_win_rate,
        oos_avg_ret=agg.oos_avg_ret,
        oos_n_traded=agg.oos_n_traded,
        oos_period_start=agg.oos_period_start,
        oos_period_end=agg.oos_period_end,
        oos_n_windows=agg.oos_n_windows,
        oos_monthly_sharpe_std=agg.oos_monthly_sharpe_std,
    )


# ─────────────────────────────────────────────────────────────────────
# Helper: 在指定 signals 上用 best params 跑 backtest_signals + objectives
# ─────────────────────────────────────────────────────────────────────


def _filter_signals_by_pattern(
    signals: list[dict],
    bars_by_stock: dict,
    best_body: float, best_lower: float,
    best_close: float, best_vol: float,
) -> list[dict]:
    """K 线形态过滤 (跟 objective.py 同款)."""
    from services.candle_pattern.evaluator import score_pattern_match
    from services.candle_pattern.features import compute_features_from_bars

    # Phase ψ.β.perf: 用 _idx (O(1) dict cache) 替代 linear search
    from services.backtest.realistic_engine import _idx as _bar_idx
    filtered = []
    for s in signals:
        bars = bars_by_stock.get(s["stock_code"])
        if not bars:
            continue
        sig_i = _bar_idx(bars, s["signal_date"])
        if sig_i < 20:
            continue
        feat = compute_features_from_bars(bars, sig_i)
        if feat is None:
            continue
        if score_pattern_match(feat,
                               body_ratio_min=best_body,
                               lower_shadow_min=best_lower,
                               close_position_min=best_close,
                               volume_relative_min=best_vol) >= 1.0:
            filtered.append(s)
    return filtered


def _trades_at_params(
    signals: list[dict],
    bars_by_stock: dict,
    best_stop: float, best_target: float, best_trail: float,
    best_hp: int, best_buy_offset: int,
    best_body: float, best_lower: float,
    best_close: float, best_vol: float,
) -> list:
    """单纯跑 trades, 不算 summary (给 OOS aggregator 用)."""
    from services.backtest.realistic_engine import simulate_trade
    filtered = _filter_signals_by_pattern(
        signals, bars_by_stock, best_body, best_lower, best_close, best_vol,
    )
    if not filtered:
        return []
    trades = []
    for s in filtered:
        bars = bars_by_stock.get(s["stock_code"])
        if not bars:
            continue
        t = simulate_trade(
            stock_code=s["stock_code"], signal_date=s["signal_date"], bars=bars,
            stop_pct=best_stop, target_pct=best_target,
            trailing_pct=best_trail, hp_target=best_hp,
            execution=EXECUTION_MODEL, buy_offset=best_buy_offset,
        )
        if t is not None:
            trades.append(t)
    return trades


def _evaluate_at_params(
    signals: list[dict],
    bars_by_stock: dict,
    best_stop: float, best_target: float, best_trail: float,
    best_hp: int, best_buy_offset: int,
    best_body: float, best_lower: float,
    best_close: float, best_vol: float,
):
    """跑 backtest + objectives. 返回 (BacktestSummary, ObjectiveValues) 或 (None, None)."""
    # Phase ψ.β.perf: 一次性拿 summary + trades, 避免 _trades_at_params 重复 simulate_trade
    from services.backtest.realistic_engine import backtest_signals_with_trades
    from services.optimization.objectives import compute_all_objectives

    filtered = _filter_signals_by_pattern(
        signals, bars_by_stock, best_body, best_lower, best_close, best_vol,
    )
    if len(filtered) < MIN_TRADED_SIGNALS:
        return None, None

    summary, trades = backtest_signals_with_trades(
        signals=filtered, bars_by_stock=bars_by_stock,
        stop_pct=best_stop, target_pct=best_target, trailing_pct=best_trail,
        hp_target=best_hp, execution=EXECUTION_MODEL,
        buy_offset=best_buy_offset,
    )
    if summary.n_traded < MIN_TRADED_SIGNALS:
        return None, None

    obj = compute_all_objectives(trades)
    if obj is None:
        return None, None
    return summary, obj


def _build_result_none(
    stock_code, formula_id, formula_variant, n_signals_input, n_trials,
    best_hp, best_stop, best_target, best_trail, best_buy_offset,
    best_body, best_lower, best_close, best_vol,
    in_sample_summary, in_sample_obj, score,
    split: WalkForwardSplit, wf_mode: str,
) -> OptimalStrategy:
    """none 模式: 填 in-sample, OOS 字段 0/空 (governance 拒入业务表)."""
    return OptimalStrategy(
        stock_code=stock_code,
        formula_id=formula_id,
        formula_variant=formula_variant,
        n_signals_input=n_signals_input,
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
        summary=in_sample_summary,
        optimal_calmar=in_sample_obj.calmar,
        optimal_sortino=in_sample_obj.sortino,
        optimal_pain_index=in_sample_obj.pain_index,
        optimal_ulcer_index=in_sample_obj.ulcer_index,
        optimal_tail_risk=in_sample_obj.tail_risk,
        optimal_stability=in_sample_obj.stability,
        score=float(score),
        walk_forward_mode=wf_mode,
        train_n_signals=split.n_train,
        test_n_signals=0,
        oos_sharpe=0.0, oos_win_rate=0.0, oos_avg_ret=0.0,
        oos_n_traded=0, oos_period_start="", oos_period_end="",
        oos_n_windows=0, oos_monthly_sharpe_std=0.0,
    )
