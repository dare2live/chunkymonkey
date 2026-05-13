"""Phase π.2 — 动态现金管理.

⚠ STRONG_BUY 数 < 阈值时, 不强制满仓, 现金占比动态提升.
⚠ 用户原话: "短期增值不缩水" — 没机会就空仓.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CashConfig:
    """动态现金管理参数."""
    full_invest_strong_buy: int = 5    # ≥ 5 个 STRONG_BUY → 满仓 (现金 10%)
    half_cash_strong_buy:   int = 2    # 2-4 个 → 30% 现金
    high_cash_strong_buy:   int = 1    # 1 个 → 60% 现金
    no_signal_cash:        float = 0.95  # 0 个 → 95% 现金 (空仓但留 5% 试错)


def dynamic_cash_pct(n_strong_buy: int, n_buy: int, config: CashConfig = CashConfig()) -> float:
    """据 STRONG_BUY / BUY 数量决定现金占比.

    Logic:
        STRONG_BUY ≥ 5: 10% 现金 (积极满仓)
        STRONG_BUY ≥ 2: 30% 现金 (中性)
        STRONG_BUY ≥ 1: 60% 现金 (谨慎)
        STRONG_BUY = 0 but BUY ≥ 10: 50% 现金 (无强信号, 弱信号多则部分仓)
        STRONG_BUY = 0 and BUY < 10: 95% 现金 (空仓)
    """
    if n_strong_buy >= config.full_invest_strong_buy:
        return 0.10
    if n_strong_buy >= config.half_cash_strong_buy:
        return 0.30
    if n_strong_buy >= config.high_cash_strong_buy:
        return 0.60
    if n_buy >= 10:
        return 0.50
    return config.no_signal_cash
