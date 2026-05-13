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
    candidate_source: str
    rank_by: str
    min_tier_to_buy: str
    min_tier_to_swap_in: str
    liquidity_min_amount_20d: float
    liquidity_max_price: float
    exclude_stage: list


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
    commission_pct: float
    commission_min_cny: float
    stamp_duty_sell_pct: float
    transfer_fee_sh_pct: float
    slippage_pct: float


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
    assert 0 < t.stamp_duty_sell_pct < 0.005
    assert 0 < t.slippage_pct < 0.01

    r = cfg.risk
    assert r.daily_dd_warning_pct < 0 and r.daily_dd_warning_pct > -0.2
    assert r.max_dd_hard_stop_pct < r.daily_dd_warning_pct  # hard stop 更严

    sel = cfg.selection
    assert sel.min_tier_to_buy in {"BUY", "STRONG_BUY", "WATCH"}
    assert sel.min_tier_to_swap_in in {"BUY", "STRONG_BUY"}


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
        selection=SelectionConfig(**raw["selection"]),
        exit=ExitConfig(**raw["exit"]),
        swap=SwapConfig(**raw["swap"]),
        tx_cost=TxCostConfig(**raw["tx_cost"]),
        risk=RiskConfig(**raw["risk"]),
        validation=ValidationConfig(**raw["validation"]),
        data=DataConfig(**raw["data"]),
    )
    _validate(cfg)
    return cfg
