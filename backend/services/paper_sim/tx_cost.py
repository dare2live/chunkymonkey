"""Paper Sim v2 — 真实交易成本.

A 股 2024 后成本结构:
  买入: 佣金 0.025% (最低 5 元) + 滑点 0.1%
  卖出: 佣金 0.025% (最低 5 元) + 印花税 0.05% + 沪市过户费 0.001% + 滑点 0.1%
  → 往返净成本 ≈ 0.05% × 2 + 0.05% + ~0% + 0.1% × 2 ≈ 0.35%

设计:
  - 输入 base_amount = price × shares
  - 输出 {commission, stamp_duty, transfer_fee, slippage, total, effective_price}
  - 单独 buy_cost / sell_revenue 两个 API, 内部共享逻辑
"""
from __future__ import annotations

from dataclasses import dataclass

from services.paper_sim.config import TxCostConfig


@dataclass(frozen=True)
class TxResult:
    base_amount: float                # price × shares (不含税费)
    commission: float
    stamp_duty: float
    transfer_fee: float
    slippage: float
    total_cost: float                 # 全部税费滑点
    effective_amount: float           # buy 是付出, sell 是收到 — 用 sign 由调用方控制


def _is_sh_market(stock_code: str) -> bool:
    """上交所代码: 600/601/603/605/688/689 开头. 深交所: 000/001/002/003/300."""
    if not stock_code:
        return False
    return stock_code.startswith(("6", "9"))   # 9 是 B 股, 简化按 6 系列识别 SH


def _compute_components(
    cfg: TxCostConfig,
    price: float,
    shares: int,
    is_sell: bool,
    stock_code: str,
) -> TxResult:
    """共享: 计算佣金 / 印花 / 过户 / 滑点 各项 + 总."""
    base = price * shares
    if base <= 0:
        return TxResult(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)

    commission = max(cfg.commission_min_cny, base * cfg.commission_pct)
    stamp = base * cfg.stamp_duty_sell_pct if is_sell else 0.0
    transfer = base * cfg.transfer_fee_sh_pct if _is_sh_market(stock_code) else 0.0
    slippage = base * cfg.slippage_pct

    total = commission + stamp + transfer + slippage
    # buy: effective 是"实际花了多少" = base + total
    # sell: effective 是"实际收到多少" = base - total
    effective = base - total if is_sell else base + total

    return TxResult(
        base_amount=base,
        commission=commission,
        stamp_duty=stamp,
        transfer_fee=transfer,
        slippage=slippage,
        total_cost=total,
        effective_amount=effective,
    )


def compute_buy_cost(
    cfg: TxCostConfig, price: float, shares: int, stock_code: str
) -> TxResult:
    """买入: 100 股 × price → 实际花费 = price×shares + 全部费用."""
    return _compute_components(cfg, price, shares, is_sell=False, stock_code=stock_code)


def compute_sell_revenue(
    cfg: TxCostConfig, price: float, shares: int, stock_code: str
) -> TxResult:
    """卖出: 100 股 × price → 实际收到 = price×shares - 全部费用."""
    return _compute_components(cfg, price, shares, is_sell=True, stock_code=stock_code)


def estimate_round_trip_pct(cfg: TxCostConfig, stock_code: str = "000001") -> float:
    """估算往返交易成本占比 (买 + 卖). 用于 swap gap_buffer 计算 + 报告.

    简化: 用 1000 元 base 估比例 (避开最低佣金 5 元的尾部效应).
    """
    buy = compute_buy_cost(cfg, price=10.0, shares=100, stock_code=stock_code)
    sell = compute_sell_revenue(cfg, price=10.0, shares=100, stock_code=stock_code)
    return (buy.total_cost + sell.total_cost) / buy.base_amount
