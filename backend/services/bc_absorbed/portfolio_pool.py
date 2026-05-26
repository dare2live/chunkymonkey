"""Layer 3: 股票池管理 — max K 选择 + 仓位 + 止盈止损 + 替换.

独立模块, 独立调参. 输入 Layer 2 scored signals, 输出持仓变动.

设计与主项目 paper_sim 一致: max 5 stocks, 可不满仓.

用法:
    from portfolio_pool import PortfolioPool
    pool = PortfolioPool(config)
    actions = pool.step(date_idx, scored_signals, current_prices)
    # actions = [{'action': 'buy', 'code': '300616', 'score': 0.85}, ...]
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np


@dataclass
class Position:
    code: str
    entry_idx: int
    entry_price: float
    entry_score: float
    highest_since_entry: float
    formulas: list[str] = field(default_factory=list)


@dataclass
class PoolAction:
    action: str             # buy / sell / hold
    code: str
    date_idx: int
    reason: str
    price: float = 0.0
    score: float = 0.0
    pnl_pct: float = 0.0   # for sell actions


DEFAULT_CONFIG: dict[str, Any] = {
    "max_positions": 5,
    "score_threshold": 0.30,
    "replace_ratio": 1.3,
    "stop_loss_pct": 0.05,
    "trailing_stop_pct": 0.10,
    "ma_exit_period": 20,
    "max_same_sector": 2,
    "equal_weight": True,
}


class PortfolioPool:
    def __init__(self, config: dict[str, Any] | None = None):
        self.cfg = {**DEFAULT_CONFIG, **(config or {})}
        self.positions: dict[str, Position] = {}
        self.trade_log: list[PoolAction] = []

    @property
    def n_positions(self) -> int:
        return len(self.positions)

    @property
    def available_slots(self) -> int:
        return self.cfg["max_positions"] - self.n_positions

    def step(
        self,
        date_idx: int,
        scored_signals: list,
        close_prices: dict[str, float],
        next_open_prices: dict[str, float] | None = None,
        high_prices: dict[str, float] | None = None,
        ma_prices: dict[str, float] | None = None,
    ) -> list[PoolAction]:
        """Process one day: check exits at close, then entries at T+1 open when supplied."""
        actions: list[PoolAction] = []
        entry_prices = next_open_prices if next_open_prices is not None else close_prices
        entry_date_idx = date_idx + 1 if next_open_prices is not None else date_idx

        for code in list(self.positions.keys()):
            pos = self.positions[code]
            price = close_prices.get(code, 0)
            if price <= 0:
                continue
            pos.highest_since_entry = max(pos.highest_since_entry, high_prices.get(code, price) if high_prices else price)

            sell_reason = self._check_exit(pos, price, ma_prices.get(code) if ma_prices else None)
            if sell_reason:
                pnl = (price - pos.entry_price) / pos.entry_price if pos.entry_price > 0 else 0
                act = PoolAction("sell", code, date_idx, sell_reason, price=price, pnl_pct=round(pnl, 4))
                actions.append(act)
                self.trade_log.append(act)
                del self.positions[code]

        candidates = [s for s in scored_signals
                      if s.score >= self.cfg["score_threshold"]
                      and s.code not in self.positions
                      and s.code in entry_prices]
        candidates.sort(key=lambda s: -s.score)

        slots = self.available_slots
        if slots > 0:
            for sig in candidates[:slots]:
                price = entry_prices[sig.code]
                if price <= 0:
                    continue
                pos = Position(
                    code=sig.code,
                    entry_idx=entry_date_idx,
                    entry_price=price,
                    entry_score=sig.score,
                    highest_since_entry=price,
                    formulas=sig.formulas,
                )
                self.positions[sig.code] = pos
                act = PoolAction("buy", sig.code, entry_date_idx, "new_entry", price=price, score=sig.score)
                actions.append(act)
                self.trade_log.append(act)
                slots -= 1

        if candidates and self.available_slots <= 0:
            worst_code, worst_pos = min(self.positions.items(), key=lambda x: x[1].entry_score)
            best_candidate = candidates[0]
            if best_candidate.score > worst_pos.entry_score * self.cfg["replace_ratio"]:
                price = close_prices.get(worst_code, 0)
                pnl = (price - worst_pos.entry_price) / worst_pos.entry_price if worst_pos.entry_price > 0 else 0
                actions.append(PoolAction("sell", worst_code, date_idx, "replaced", price=price, pnl_pct=round(pnl, 4)))
                del self.positions[worst_code]
                new_price = entry_prices[best_candidate.code]
                self.positions[best_candidate.code] = Position(
                    code=best_candidate.code, entry_idx=entry_date_idx, entry_price=new_price,
                    entry_score=best_candidate.score, highest_since_entry=new_price,
                    formulas=best_candidate.formulas,
                )
                actions.append(PoolAction("buy", best_candidate.code, entry_date_idx, "replace_entry", price=new_price, score=best_candidate.score))

        return actions

    def _check_exit(self, pos: Position, price: float, ma_price: float | None) -> str:
        if pos.entry_price > 0 and (price - pos.entry_price) / pos.entry_price <= -self.cfg["stop_loss_pct"]:
            return "stop_loss"
        if pos.highest_since_entry > 0 and (price - pos.highest_since_entry) / pos.highest_since_entry <= -self.cfg["trailing_stop_pct"]:
            return "trailing_stop"
        if ma_price is not None and price < ma_price:
            return "below_ma"
        return ""

    def summary(self) -> dict[str, Any]:
        buys = [t for t in self.trade_log if t.action == "buy"]
        sells = [t for t in self.trade_log if t.action == "sell"]
        pnls = [t.pnl_pct for t in sells]
        return {
            "total_buys": len(buys),
            "total_sells": len(sells),
            "open_positions": len(self.positions),
            "win_rate": float(np.mean([p > 0 for p in pnls])) if pnls else 0,
            "avg_pnl": float(np.mean(pnls)) if pnls else 0,
            "max_dd": float(min(pnls)) if pnls else 0,
        }
