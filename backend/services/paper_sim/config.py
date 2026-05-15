"""Paper Sim v2 — config 加载 + 校验.

设计原则:
- 加载 backend/config/paper_sim_config.yaml
- frozen dataclass, 防意外改 hyperparam
- 验证关键字段范围 (例 severe_threshold ∈ (0, 1])
- 支持 override (单测可注入 override dict)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "paper_sim_config.yaml"


@dataclass(frozen=True)
class PortfolioConfig:
    initial_cash: float
    max_positions: int
    cash_when_strong_buy_lt: int
    position_sizing: str
    min_cash_pct: float


@dataclass(frozen=True)
class SelectionConfig:
    mode: str                            # "production" | "backtest" | "ensemble" | "ml_score"
    candidate_source: str
    rank_by: str
    min_tier_to_buy: str
    min_tier_to_swap_in: str
    liquidity_min_amount_20d: float
    liquidity_max_price: float
    exclude_stage: list
    backtest_tier_thresholds: dict       # {strong_buy: {sharpe_min, win_rate_min, calmar_min}, buy: {...}}
    # Phase ψ.α: 公式白名单 (空 / None = 不限制, 列表 = 只用列表里的 formula_id)
    # 用于 ablation: momentum vs reversal vs combined 同 paper_sim 路径下切换
    formula_whitelist: tuple = ()
    # Phase ψ.β.4: ensemble 多 alpha 综合 配置
    ensemble_alphas: tuple = ()
    regime_gate: dict = field(default_factory=dict)
    default_holding: dict = field(default_factory=lambda: {
        "hp": 15, "stop_pct": -0.10, "target_pct": 0.20, "trailing_pct": 0.05
    })
    # Phase ψ.β.4.6: quality filter (sanity gate, 在 zscore 之前 filter universe)
    # 防止 ensemble 选高 vol 股 (短期 stop_hit 频繁) 和下跌趋势股 (stage=4)
    ensemble_quality_filters: dict = field(default_factory=lambda: {
        "max_vol_60d": 0.40,        # 60 日年化 std ≤ 40% (排除高波动)
        "min_amount_20d_yuan": 0,   # 流动性 (driver liquidity 已 filter, 这里冗余)
        "allowed_stages": ["1", "1.5", "2"],   # 仅底部 / 突破中 / 上升趋势
    })
    # Phase ψ.β.5 L2: vol-aware per-stock 参数 (从 fact_risk_factors.vol_60d 缩放 stop/target/trailing)
    # enabled=False (默认 off, 不影响现有 ensemble v3 跑批); 启用见 paper_sim_ensemble.yaml 注释
    vol_aware: dict = field(default_factory=dict)
    # Phase ψ.γ.2: per-stock × stage 接入 ensemble (用现有 mart_per_stock_stage_strategy_optimal
    # 24K 行 9 维 Optuna OOS 产物覆盖 default_holding). 优先级: per_stock_stage > vol_aware > default.
    per_stock_stage: dict = field(default_factory=dict)
    # PLAN_V3 v3.2 P0c: ML score mode (Option A) — selector ranking 用 mart_p0b_oos_predictions.
    # 只在 mode='ml_score' 生效. exit/swap 仍走 Optuna 9-dim 公式 (per_stock_stage).
    ml_score_model_id: str = "lgbm_baseline_v1"
    ml_score_max_candidates: int = 30
    ml_score_min_score: float | None = None
    # Codex 7-day plan Day 6: hybrid mode (sequential filter + rank-linear blend).
    # 只在 mode='hybrid' 生效.
    hybrid_model_id: str = "lgbm_baseline_v1"
    hybrid_w_ml: float = 0.20
    hybrid_max_candidates: int = 30
    hybrid_q60_min_stage: bool = True
    # Path A 2026-05-15: anti-churn min_forced_hp (Codex aa2d79d2 MAJOR #3 修).
    # hp_expired 强制 days_held >= max(optimal_hp, min_forced_hp). 0 = 关闭, 实测 15 把 turnover 从 22x→8x.
    # stop_hit / trailing / stage_det 不受此限 (真实风险退出永远允许).
    min_forced_hp: int = 0


@dataclass(frozen=True)
class ExitConfig:
    use_optuna_stop: bool
    use_optuna_target: bool
    use_optuna_trailing: bool
    use_optuna_hp: bool
    stage_deterioration_sell: bool


@dataclass(frozen=True)
class SwapConfig:
    enabled: bool
    severe_threshold: float
    candidate_must_close_gap: bool
    gap_buffer_pct: float
    min_holding_days_before_swap: int
    max_swaps_per_day: int


@dataclass(frozen=True)
class TxCostConfig:
    """A 股完整成本结构 (Codex aaedbc9d C-B 2026-05-15).

    2023+ 沪深统一 transfer_fee 双向, 不再区分 SH-only.
    大单 surcharge: base > large_order_adv_threshold_pct × ADV20 时触发 +large_order_surcharge_pct slippage.
    """
    commission_pct: float                       # 佣金 (单边), 通常 0.025%
    commission_min_cny: float                   # 佣金最低, 通常 5 CNY
    stamp_duty_sell_pct: float                  # 印花税 (sell only), 0.05% (2023.08 降)
    transfer_fee_pct: float                     # 过户费 (沪深双向), 0.001%
    exchange_fee_pct: float                     # 交易所规费 (双向), 0.00341%
    regulatory_fee_pct: float                   # 证管费 (双向), 0.002%
    slippage_pct: float                         # 基础滑点 (单边), 8 bps = 0.08%
    large_order_surcharge_pct: float            # 大单溢价 (单边), 15 bps = 0.15%
    large_order_adv_threshold_pct: float        # 大单阈值 (× ADV20), 3%


@dataclass(frozen=True)
class RiskConfig:
    daily_dd_warning_pct: float
    max_dd_hard_stop_pct: float
    hard_stop_freeze_days: int


@dataclass(frozen=True)
class ValidationConfig:
    user_criteria: dict
    anti_churn: dict
    robustness: dict
    ablation: dict
    sensitivity: dict
    reality_check: dict


@dataclass(frozen=True)
class DataConfig:
    optimal_table_primary: str
    optimal_table_fallback: str
    benchmark: str
    start_date: str


@dataclass(frozen=True)
class PaperSimConfig:
    portfolio: PortfolioConfig
    selection: SelectionConfig
    exit: ExitConfig
    swap: SwapConfig
    tx_cost: TxCostConfig
    risk: RiskConfig
    validation: ValidationConfig
    data: DataConfig


def _validate(cfg: PaperSimConfig) -> None:
    """字段范围校验. fail-fast 避免错配跑半天才发现."""
    p = cfg.portfolio
    assert p.initial_cash > 0, "initial_cash 必须 > 0"
    assert 1 <= p.max_positions <= 30, f"max_positions out of [1, 30]: {p.max_positions}"
    assert 0 <= p.min_cash_pct < 0.5, f"min_cash_pct out of [0, 0.5): {p.min_cash_pct}"
    assert p.position_sizing in {"wilson_kelly", "equal", "kelly"}, \
        f"unknown sizing: {p.position_sizing}"

    s = cfg.swap
    assert 0 < s.severe_threshold <= 1.0, f"severe_threshold out of (0, 1]: {s.severe_threshold}"
    assert 0 <= s.gap_buffer_pct < 0.1, f"gap_buffer_pct out of [0, 0.1): {s.gap_buffer_pct}"
    assert s.min_holding_days_before_swap >= 0
    assert 0 <= s.max_swaps_per_day <= 10

    t = cfg.tx_cost
    assert 0 < t.commission_pct < 0.01, f"commission_pct unrealistic: {t.commission_pct}"
    assert t.commission_min_cny >= 0, f"commission_min_cny negative: {t.commission_min_cny}"
    assert 0 < t.stamp_duty_sell_pct < 0.005, f"stamp_duty unrealistic: {t.stamp_duty_sell_pct}"
    assert 0 <= t.transfer_fee_pct < 0.001, f"transfer_fee unrealistic: {t.transfer_fee_pct}"
    assert 0 <= t.exchange_fee_pct < 0.001, f"exchange_fee unrealistic: {t.exchange_fee_pct}"
    assert 0 <= t.regulatory_fee_pct < 0.001, f"regulatory_fee unrealistic: {t.regulatory_fee_pct}"
    assert 0 < t.slippage_pct < 0.01, f"slippage unrealistic: {t.slippage_pct}"
    assert 0 <= t.large_order_surcharge_pct < 0.01, \
        f"large_order_surcharge unrealistic: {t.large_order_surcharge_pct}"
    assert 0 <= t.large_order_adv_threshold_pct < 1.0, \
        f"large_order_adv_threshold out of [0, 1): {t.large_order_adv_threshold_pct}"

    r = cfg.risk
    assert r.daily_dd_warning_pct < 0 and r.daily_dd_warning_pct > -0.2
    assert r.max_dd_hard_stop_pct < r.daily_dd_warning_pct  # hard stop 更严

    sel = cfg.selection
    # Codex C4 (a163ca58): 加 ml_score / hybrid (Day 6 + P0c modes)
    assert sel.mode in {"production", "backtest", "ensemble", "ml_score", "hybrid"}, \
        f"unknown selection.mode: {sel.mode}"
    assert sel.min_tier_to_buy in {"BUY", "STRONG_BUY", "WATCH"}
    assert sel.min_tier_to_swap_in in {"BUY", "STRONG_BUY"}
    if sel.mode == "ensemble":
        assert sel.ensemble_alphas, "ensemble mode requires non-empty ensemble_alphas"
        for a in sel.ensemble_alphas:
            assert all(k in a for k in ("name", "weight", "source_table", "source_col",
                                        "direction", "pit_key")), \
                   f"ensemble alpha missing required keys: {a}"
            assert a["direction"] in (1, -1, +1)


def load_config(path: Path | None = None, override: dict | None = None) -> PaperSimConfig:
    """加载并校验配置. override 单测注入用."""
    p = path or _CONFIG_PATH
    with open(p, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    if override:
        # 浅合并 (足够单测用 — 单测一般只改 1-2 key)
        for key, val in override.items():
            if isinstance(val, dict) and key in raw and isinstance(raw[key], dict):
                raw[key] = {**raw[key], **val}
            else:
                raw[key] = val

    cfg = PaperSimConfig(
        portfolio=PortfolioConfig(**raw["portfolio"]),
        selection=SelectionConfig(
            **{**raw["selection"],
               "formula_whitelist": tuple(raw["selection"].get("formula_whitelist") or ()),
               "exclude_stage": list(raw["selection"].get("exclude_stage") or []),
               # Phase ψ.β.4: ensemble alphas (tuple of dict — frozen dc 字段可为 tuple)
               "ensemble_alphas": tuple(raw["selection"].get("ensemble_alphas") or []),
               "regime_gate": raw["selection"].get("regime_gate", {}) or {},
               "default_holding": raw["selection"].get("default_holding", {}) or {
                   "hp": 15, "stop_pct": -0.10, "target_pct": 0.20, "trailing_pct": 0.05},
               "ensemble_quality_filters": raw["selection"].get("ensemble_quality_filters") or {
                   "max_vol_60d": 0.40, "min_amount_20d_yuan": 0,
                   "allowed_stages": ["1", "1.5", "2"]},
               "vol_aware":        raw["selection"].get("vol_aware",        {}) or {},
               "per_stock_stage":  raw["selection"].get("per_stock_stage",  {}) or {}}
        ),
        exit=ExitConfig(**raw["exit"]),
        swap=SwapConfig(**raw["swap"]),
        tx_cost=TxCostConfig(**raw["tx_cost"]),
        risk=RiskConfig(**raw["risk"]),
        validation=ValidationConfig(**raw["validation"]),
        data=DataConfig(**raw["data"]),
    )
    _validate(cfg)
    return cfg
