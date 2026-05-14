"""Phase ψ — Optuna 中央配置加载器 (单一职责).

⚠ 唯一从 backend/config/optuna_config.yaml 读 Optuna 全部 hyperparam 的地方.
⚠ Rule 7 (CLAUDE.md): 任何业务代码读 governance / search_space / walk_forward /
   composite / constraints / execution 参数, 必须走此处, **不许** 在脚本内硬编码.

设计 (跟 paper_sim/config.py 同款):
  - frozen dataclass 防意外改 hyperparam
  - load_optuna_config(override=...) 单测可注入
  - _validate 校验关键字段范围 / 权重和 = 1.0
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "optuna_config.yaml"


# ─────────────────────────────────────────────────────────────────────
# Frozen dataclasses (一一对应 yaml 各段)
# ─────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class GovernanceConfig:
    min_n_trials: int
    max_n_trials: int
    require_sampler_seed: bool
    default_optuna_seed: int
    require_walk_forward: bool
    min_train_signals: int
    min_test_signals: int
    max_realistic_sharpe: float
    max_realistic_win_rate: float
    max_realistic_avg_ret: float
    min_realistic_avg_ret: float


@dataclass(frozen=True)
class ExpandingMonthlyConfig:
    min_train_months: int
    forward_months: int
    min_total_months: int


@dataclass(frozen=True)
class HoldoutConfig:
    train_ratio: float
    min_train_signals: int
    min_test_signals: int


@dataclass(frozen=True)
class ExpandingNwindowsConfig:
    n_windows: int
    min_train_signals: int
    min_test_signals: int


@dataclass(frozen=True)
class WalkForwardConfig:
    default_mode: str                # 'expanding_monthly' / 'holdout' / 'expanding' / 'none'
    expanding_monthly: ExpandingMonthlyConfig
    holdout: HoldoutConfig
    expanding: ExpandingNwindowsConfig


@dataclass(frozen=True)
class RangeConfig:
    lo: float
    hi: float


@dataclass(frozen=True)
class StrategySearchSpace:
    hp_choices: tuple
    stop_pct: RangeConfig
    target_pct: RangeConfig
    trailing_pct: RangeConfig
    buy_offset_choices: tuple


@dataclass(frozen=True)
class CandlePatternSearchSpace:
    body_ratio_min: RangeConfig
    lower_shadow_min: RangeConfig
    close_position_min: RangeConfig
    volume_relative_min: RangeConfig


@dataclass(frozen=True)
class SearchSpaceConfig:
    strategy: StrategySearchSpace
    candle_pattern: CandlePatternSearchSpace


@dataclass(frozen=True)
class CompositeConfig:
    calmar_w: float
    sortino_w: float
    sharpe_w: float
    stability_w: float
    pain_w: float
    ulcer_w: float
    tail_w: float


@dataclass(frozen=True)
class ConstraintsConfig:
    max_acceptable_drawdown: float
    worst_single_loss: float
    max_loss_streak: int
    min_traded: int


@dataclass(frozen=True)
class ExecutionConfig:
    n_trials: int
    n_workers: int
    sample_min: int


@dataclass(frozen=True)
class OutputConfig:
    stage_optimal_table: str
    cross_stage_optimal_table: str
    governance_log_table: str


@dataclass(frozen=True)
class DeflatedSharpeConfig:
    """Phase ψ.γ.discipline — 跨 study 多重测试治理 (Bailey & López de Prado 2014)."""
    enabled: bool
    min_p_value: float
    default_sharpe_variance: float
    cumulative_trials_log_table: str


@dataclass(frozen=True)
class OptunaConfig:
    """整体配置. 通过 load_optuna_config() 拿."""
    governance: GovernanceConfig
    walk_forward: WalkForwardConfig
    search_space: SearchSpaceConfig
    composite: CompositeConfig
    constraints: ConstraintsConfig
    execution: ExecutionConfig
    output: OutputConfig
    deflated_sharpe: DeflatedSharpeConfig


# ─────────────────────────────────────────────────────────────────────
# Load + validate
# ─────────────────────────────────────────────────────────────────────


def _to_range(d: dict) -> RangeConfig:
    return RangeConfig(lo=float(d["lo"]), hi=float(d["hi"]))


def _validate(cfg: OptunaConfig) -> None:
    """关键字段范围校验, 不通过 raise (Rule 5: defense > silent bypass)."""
    g = cfg.governance
    assert 0 < g.min_n_trials <= g.max_n_trials, \
        f"governance: min_n_trials={g.min_n_trials} max={g.max_n_trials} 顺序错"
    assert g.max_realistic_sharpe > 0, "max_realistic_sharpe 必须 > 0"
    assert 0 < g.max_realistic_win_rate <= 1.0, \
        f"max_realistic_win_rate 必须 (0, 1], 实 {g.max_realistic_win_rate}"
    assert g.min_realistic_avg_ret < 0 < g.max_realistic_avg_ret, \
        f"avg_ret 区间错: [{g.min_realistic_avg_ret}, {g.max_realistic_avg_ret}]"
    assert g.min_train_signals >= 1
    assert g.min_test_signals >= 1

    wf = cfg.walk_forward
    assert wf.default_mode in {"expanding_monthly", "expanding", "holdout", "none"}, \
        f"未知 walk_forward.default_mode: {wf.default_mode}"
    assert 0 < wf.holdout.train_ratio < 1.0
    assert wf.expanding_monthly.min_train_months >= 1
    assert wf.expanding_monthly.forward_months >= 1

    ss = cfg.search_space
    assert len(ss.strategy.hp_choices) >= 2, "hp 至少 2 档"
    assert ss.strategy.stop_pct.lo < ss.strategy.stop_pct.hi <= 0, "stop_pct 必须负"
    assert 0 < ss.strategy.target_pct.lo < ss.strategy.target_pct.hi, "target_pct 必须正"
    assert 0 < ss.strategy.trailing_pct.lo < ss.strategy.trailing_pct.hi
    assert len(ss.strategy.buy_offset_choices) >= 1

    c = cfg.composite
    w_sum = (c.calmar_w + c.sortino_w + c.sharpe_w + c.stability_w
             + c.pain_w + c.ulcer_w + c.tail_w)
    assert abs(w_sum - 1.0) <= 0.001, \
        f"composite 权重和必须 = 1.0, 实 {w_sum:.4f}"

    cons = cfg.constraints
    assert cons.max_acceptable_drawdown < 0
    assert cons.worst_single_loss < cons.max_acceptable_drawdown, \
        "worst_single_loss 应比 avg max_dd 更严 (更负)"
    assert cons.max_loss_streak >= 1
    assert cons.min_traded >= 1

    ex = cfg.execution
    assert ex.n_trials >= g.min_n_trials, \
        f"execution.n_trials={ex.n_trials} < governance.min_n_trials={g.min_n_trials}"
    assert ex.n_trials <= g.max_n_trials
    assert ex.n_workers >= 1

    out = cfg.output
    assert out.stage_optimal_table, "output.stage_optimal_table 必须非空"
    assert out.governance_log_table, "output.governance_log_table 必须非空"

    ds = cfg.deflated_sharpe
    assert 0.0 < ds.min_p_value < 1.0, \
        f"deflated_sharpe.min_p_value 必须 (0, 1), 实 {ds.min_p_value}"
    assert ds.default_sharpe_variance > 0, \
        f"deflated_sharpe.default_sharpe_variance 必须 > 0, 实 {ds.default_sharpe_variance}"
    assert ds.cumulative_trials_log_table, \
        "deflated_sharpe.cumulative_trials_log_table 必须非空"


def load_optuna_config(
    path: Path | None = None,
    override: dict | None = None,
) -> OptunaConfig:
    """加载 + 校验. 单测可注入 override.

    Args:
        path:     默认 backend/config/optuna_config.yaml
        override: 单测注入 (浅合并)
    """
    p = path or _CONFIG_PATH
    with open(p, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    if override:
        # 递归深合并 (单测 / 调试用), 避免浅合并丢掉嵌套字段
        def _deep_merge(base: dict, ov: dict) -> dict:
            out = dict(base)
            for k, v in ov.items():
                if isinstance(v, dict) and isinstance(out.get(k), dict):
                    out[k] = _deep_merge(out[k], v)
                else:
                    out[k] = v
            return out
        raw = _deep_merge(raw, override)

    cfg = OptunaConfig(
        governance=GovernanceConfig(**raw["governance"]),
        walk_forward=WalkForwardConfig(
            default_mode=raw["walk_forward"]["default_mode"],
            expanding_monthly=ExpandingMonthlyConfig(**raw["walk_forward"]["expanding_monthly"]),
            holdout=HoldoutConfig(**raw["walk_forward"]["holdout"]),
            expanding=ExpandingNwindowsConfig(**raw["walk_forward"]["expanding"]),
        ),
        search_space=SearchSpaceConfig(
            strategy=StrategySearchSpace(
                hp_choices=tuple(raw["search_space"]["strategy"]["hp_choices"]),
                stop_pct=_to_range(raw["search_space"]["strategy"]["stop_pct"]),
                target_pct=_to_range(raw["search_space"]["strategy"]["target_pct"]),
                trailing_pct=_to_range(raw["search_space"]["strategy"]["trailing_pct"]),
                buy_offset_choices=tuple(raw["search_space"]["strategy"]["buy_offset_choices"]),
            ),
            candle_pattern=CandlePatternSearchSpace(
                body_ratio_min=_to_range(raw["search_space"]["candle_pattern"]["body_ratio_min"]),
                lower_shadow_min=_to_range(raw["search_space"]["candle_pattern"]["lower_shadow_min"]),
                close_position_min=_to_range(raw["search_space"]["candle_pattern"]["close_position_min"]),
                volume_relative_min=_to_range(raw["search_space"]["candle_pattern"]["volume_relative_min"]),
            ),
        ),
        composite=CompositeConfig(**raw["composite"]),
        constraints=ConstraintsConfig(**raw["constraints"]),
        execution=ExecutionConfig(**raw["execution"]),
        output=OutputConfig(**raw["output"]),
        deflated_sharpe=DeflatedSharpeConfig(**raw.get("deflated_sharpe", {
            "enabled": False,
            "min_p_value": 0.95,
            "default_sharpe_variance": 1.0,
            "cumulative_trials_log_table": "fact_optuna_cumulative_trials",
        })),
    )
    _validate(cfg)
    return cfg


# 模块级单例 cache (避免每次 load yaml)
_CACHED_CONFIG: OptunaConfig | None = None


def get_optuna_config() -> OptunaConfig:
    """单例 — 业务代码用这个, 不要直接 load_optuna_config (除非要 reload)."""
    global _CACHED_CONFIG
    if _CACHED_CONFIG is None:
        _CACHED_CONFIG = load_optuna_config()
    return _CACHED_CONFIG


def reload_optuna_config() -> OptunaConfig:
    """单测 / 配置改动后强制 reload."""
    global _CACHED_CONFIG
    _CACHED_CONFIG = None
    return get_optuna_config()
