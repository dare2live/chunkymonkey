"""Live FormulaBase adapters for bc_absorbed challenger and bank formulas.

These adapters bridge a small verified slice of the bc_absorbed formula bank
into the shared FormulaBase registry so ``build_formula_signals_history`` can
populate them through the same live signal pipeline as the core formulas.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
import sys
from typing import Any

import numpy as np

from services.formula_engine.base import FormulaMetadata, FormulaSignal, register_formula


BC_ABSORBED_DIR = Path(__file__).resolve().parents[1] / "bc_absorbed"


@lru_cache(maxsize=1)
def _load_bc_absorbed_formula_engine() -> Any:
    """Import the legacy bc_absorbed formula engine on demand.

    The module still uses its historical top-level import layout (``formula_engine``,
    ``bank``), so we temporarily prepend the legacy directory to ``sys.path`` for the
    import and then remove it again. The imported module remains cached in
    ``sys.modules``.
    """

    path = str(BC_ABSORBED_DIR)
    inserted = False
    if path not in sys.path:
        sys.path.insert(0, path)
        inserted = True
    try:
        import formula_engine as bc_formula_engine  # type: ignore[import-not-found]

        return bc_formula_engine
    finally:
        if inserted and path in sys.path:
            sys.path.remove(path)


def _run_bc_absorbed_formula(
    formula_id: str,
    *,
    code: str,
    dates: np.ndarray,
    opens: np.ndarray,
    highs: np.ndarray,
    lows: np.ndarray,
    closes: np.ndarray,
    volumes: np.ndarray,
    amounts: np.ndarray,
    params: dict[str, Any] | None = None,
) -> tuple[np.ndarray, dict[str, Any]]:
    """Run a legacy bc_absorbed challenger formula and normalize its output."""

    bc_formula_engine = _load_bc_absorbed_formula_engine()
    result = bc_formula_engine.compute_formula_signals(  # type: ignore[attr-defined]
        formula_id,
        open_=opens,
        high=highs,
        low=lows,
        close=closes,
        volume=volumes,
        amount=amounts,
        params=params or {},
        stock_code=code,
        dates=dates,
    )
    entry = np.asarray(result.get("entry", np.zeros(len(closes), dtype=bool)), dtype=bool)
    if entry.shape != (len(closes),):
        raise ValueError(
            f"bc_absorbed formula {formula_id} returned entry shape {entry.shape}, expected {(len(closes),)}"
        )
    indicators = result.get("indicators")
    if not isinstance(indicators, dict):
        indicators = {}
    return entry, indicators


@dataclass(frozen=True)
class BankFormulaAdapter:
    metadata: FormulaMetadata
    default_strength: float = 0.5
    default_params: dict[str, Any] | None = None

    def compute_signals(
        self,
        code: str,
        dates: np.ndarray,
        opens: np.ndarray,
        highs: np.ndarray,
        lows: np.ndarray,
        closes: np.ndarray,
        volumes: np.ndarray,
        amounts: np.ndarray,
    ) -> list[FormulaSignal]:
        entry, indicators = _run_bc_absorbed_formula(
            self.metadata.formula_id,
            code=code,
            dates=dates,
            opens=opens,
            highs=highs,
            lows=lows,
            closes=closes,
            volumes=volumes,
            amounts=amounts,
            params=self.default_params,
        )
        if not entry.any():
            return []

        reason_codes: tuple[str, ...] = (f"bc_absorbed:{self.metadata.formula_id}",)
        skipped = indicators.get("skipped")
        if skipped:
            reason_codes = reason_codes + (f"skipped:{skipped}",)

        signals: list[FormulaSignal] = []
        for i in np.flatnonzero(entry):
            signals.append(
                FormulaSignal(
                    stock_code=code,
                    date=str(dates[i]),
                    formula_id=self.metadata.formula_id,
                    formula_variant=self.metadata.formula_id,
                    strength=self.default_strength,
                    state=None,
                    reason_codes=reason_codes,
                )
            )
        return signals


BANK_CHALLENGER_SPECS: tuple[tuple[str, str, str, str, int, float], ...] = (
    (
        "gs_raw_buy",
        "GS 原始买点",
        "GS",
        "原始GS买点 CROSS(X36, X3)，更敏感。",
        15,
        0.55,
    ),
    (
        "gs_pullback_confirm",
        "GS 回调确认",
        "GP",
        "GS买点叠加历史质量、卖出状态、均线多头和回撤约束。",
        15,
        0.55,
    ),
    (
        "ma_base_breakout",
        "均线筑底突破",
        "MB",
        "MA5长期低于MA90后突破并站稳MA145。",
        20,
        0.55,
    ),
    (
        "activity_breakout",
        "活跃度大牛突破",
        "AB",
        "K线活跃度 X15 向上突破大牛线。",
        5,
        0.50,
    ),
    (
        "volume_base_breakout",
        "巨量蓄势启动",
        "VB",
        "巨量后缩量横盘，再温和放量突破平台。",
        20,
        0.55,
    ),
)

BANK_EXTENSION_SPECS: tuple[tuple[str, str, str, str, int, float], ...] = (
    (
        "pullback_doji",
        "回调十字星",
        "PD",
        "放量大涨后凌厉缩量回调收十字星，次日买入。",
        10,
        0.56,
    ),
    (
        "weekly_macd_daily_macd_bull",
        "周日 MACD 同强",
        "WM",
        "周线与日线 MACD 同时转强，DIF > DEA > 0。",
        15,
        0.56,
    ),
    (
        "weekly_higher_low_daily_break",
        "周更高低突破",
        "WH",
        "周线形成 higher-low 后，日线突破前高。",
        12,
        0.55,
    ),
    (
        "monthly_uptrend_daily_pullback_buy",
        "月升日回踩买",
        "MU",
        "月线多头 + 日线回踩 MA20。",
        20,
        0.56,
    ),
    (
        "multi_tf_rsi_alignment",
        "多周期 RSI 对齐",
        "RS",
        "日线冷却区 + 周线 RSI 强势对齐。",
        10,
        0.52,
    ),
    (
        "weekly_breakout_daily_confirm",
        "周突破日确认",
        "WB",
        "周线突破 20 周高点后，日线确认。",
        15,
        0.55,
    ),
    (
        "monthly_stage2_daily_volume_confirm",
        "月升量能确认",
        "MV",
        "月线升势 + 日线放量确认。",
        15,
        0.55,
    ),
    (
        "weekly_dragon_daily_pullback",
        "周龙日回踩",
        "WD",
        "周线连续上升后，日线小回踩。",
        10,
        0.55,
    ),
)

BANK_EXTENSION_DEFAULT_PARAMS: dict[str, dict[str, Any]] = {
    "pullback_doji": {
        "breakout_limit_ratio": 0.55,
        "breakout_vol_ratio": 1.25,
        "pullback_min_days": 2,
        "pullback_max_days": 6,
        "pullback_vol_shrink": 0.80,
        "doji_body_ratio_max": 0.40,
        "doji_range_min": 0.0025,
        "buy_offset": 1,
        "signal_cooldown_days": 0,
    },
    "weekly_higher_low_daily_break": {
        "lookback": 4,
    },
    "weekly_breakout_daily_confirm": {
        "weekly_window": 15,
    },
    "weekly_dragon_daily_pullback": {
        "weekly_streak": 2,
    },
    "monthly_stage2_daily_volume_confirm": {
        "volume_mult": 1.5,
    },
}

BANK_HELD_BACK_EXTENSION_FORMULA_IDS: tuple[str, ...] = (
    "pullback_doji",
    "monthly_stage2_daily_volume_confirm",
)

BANK_LIVE_EXTENSION_FORMULA_IDS: tuple[str, ...] = tuple(
    formula_id
    for formula_id, *_ in BANK_EXTENSION_SPECS
    if formula_id not in BANK_HELD_BACK_EXTENSION_FORMULA_IDS
)


def _build_bank_registry(
    specs: tuple[tuple[str, str, str, str, int, float], ...],
    *,
    default_params_by_formula_id: dict[str, dict[str, Any]] | None = None,
) -> dict[str, BankFormulaAdapter]:
    registry: dict[str, BankFormulaAdapter] = {}
    for formula_id, name, tag, description, horizon_days, strength in specs:
        adapter = BankFormulaAdapter(
            metadata=FormulaMetadata(
                formula_id=formula_id,
                name=name,
                tag=tag,
                description=description,
                default_horizon_days=horizon_days,
            ),
            default_strength=strength,
            default_params=(default_params_by_formula_id or {}).get(formula_id),
        )
        registry[formula_id] = adapter
        register_formula(adapter)
    return registry


BANK_CHALLENGER_REGISTRY = _build_bank_registry(BANK_CHALLENGER_SPECS)
BANK_EXTENSION_REGISTRY = _build_bank_registry(
    BANK_EXTENSION_SPECS,
    default_params_by_formula_id=BANK_EXTENSION_DEFAULT_PARAMS,
)
BANK_LIVE_REGISTRY = {
    **BANK_CHALLENGER_REGISTRY,
    **{formula_id: BANK_EXTENSION_REGISTRY[formula_id] for formula_id in BANK_LIVE_EXTENSION_FORMULA_IDS},
}


__all__ = [
    "BANK_CHALLENGER_REGISTRY",
    "BANK_EXTENSION_REGISTRY",
    "BANK_HELD_BACK_EXTENSION_FORMULA_IDS",
    "BANK_LIVE_REGISTRY",
    "BANK_LIVE_EXTENSION_FORMULA_IDS",
    "BankFormulaAdapter",
]
