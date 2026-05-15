"""Paper Sim — A 股完整交易成本模型 (Codex aaedbc9d C-B, 2026-05-15).

A 股 2024 后成本结构 (沪深双向统一):
  买入: 佣金 0.025% (min 5 CNY) + 过户费 0.001% + 交易所规费 0.00341% + 证管费 0.002%
        + 滑点 8 bps (基础) + 大单 surcharge 15 bps (>3% ADV20)
  卖出: 同买入项 + 印花税 0.05% (单边)
  → 往返净成本 ≈ 0.025%×2 + 0.001%×2 + 0.00341%×2 + 0.002%×2 + 0.05% + 8bps×2 ≈ 0.27% + 大单 surcharge

2023 沪深过户费统一 0.001% 双向 (此前仅 SH). 本模块不再区分 _is_sh_market.

设计:
  - 输入 base_amount = price × shares + 可选 adv20 (大单 surcharge 触发用 — base > adv20*3% → +15bps slippage)
  - 输出 TxResult: commission / stamp_duty / transfer_fee / exchange_fee / regulatory_fee
        + slippage_base / large_order_surcharge / total / effective_amount
  - buy_cost / sell_revenue 双 API, 内部共享 _compute_components.
"""
from __future__ import annotations

from dataclasses import dataclass

from services.paper_sim.config import TxCostConfig


@dataclass(frozen=True)
class TxResult:
    base_amount: float                # price × shares (不含税费)
    commission: float                 # 佣金 (min 5 CNY)
    stamp_duty: float                 # 印花税 (sell only)
    transfer_fee: float               # 过户费 (沪深双向 2023+)
    exchange_fee: float               # 交易所规费
    regulatory_fee: float             # 证管费
    slippage_base: float              # 基础滑点 8 bps
    large_order_surcharge: float      # 大单溢价 (> threshold × ADV20 触发)
    total_cost: float                 # 全部税费滑点总和
    effective_amount: float           # buy = base+total, sell = base-total


def _compute_components(
    cfg: TxCostConfig,
    price: float,
    shares: int,
    is_sell: bool,
    adv20: float | None = None,
) -> TxResult:
    """共享: 计算佣金 / 印花 / 过户 / 规费 / 证管 / 滑点 / 大单溢价 各项 + 总."""
    base = price * shares
    if base <= 0:
        return TxResult(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)

    commission = max(cfg.commission_min_cny, base * cfg.commission_pct)
    stamp = base * cfg.stamp_duty_sell_pct if is_sell else 0.0
    transfer = base * cfg.transfer_fee_pct
    exchange = base * cfg.exchange_fee_pct
    regulatory = base * cfg.regulatory_fee_pct
    slip_base = base * cfg.slippage_pct

    # 大单溢价: order > 3% ADV20 → 加 large_order_surcharge_pct (15 bps)
    surcharge = 0.0
    if adv20 is not None and adv20 > 0 and cfg.large_order_adv_threshold_pct > 0:
        if base > adv20 * cfg.large_order_adv_threshold_pct:
            surcharge = base * cfg.large_order_surcharge_pct

    total = commission + stamp + transfer + exchange + regulatory + slip_base + surcharge
    effective = base - total if is_sell else base + total

    return TxResult(
        base_amount=base,
        commission=commission,
        stamp_duty=stamp,
        transfer_fee=transfer,
        exchange_fee=exchange,
        regulatory_fee=regulatory,
        slippage_base=slip_base,
        large_order_surcharge=surcharge,
        total_cost=total,
        effective_amount=effective,
    )


def compute_buy_cost(
    cfg: TxCostConfig,
    price: float,
    shares: int,
    adv20: float | None = None,
) -> TxResult:
    """买入: shares × price → 实际花费 = base + 全部费用."""
    return _compute_components(cfg, price, shares, is_sell=False, adv20=adv20)


def compute_sell_revenue(
    cfg: TxCostConfig,
    price: float,
    shares: int,
    adv20: float | None = None,
) -> TxResult:
    """卖出: shares × price → 实际收到 = base - 全部费用."""
    return _compute_components(cfg, price, shares, is_sell=True, adv20=adv20)


def estimate_round_trip_pct(cfg: TxCostConfig) -> float:
    """估算往返成本占比 (无 ADV20 = 不含大单 surcharge). 用于 swap gap_buffer + 报告.

    简化: 1000 元 base (避开最低佣金 5 元尾部效应).
    """
    buy = compute_buy_cost(cfg, price=10.0, shares=100, adv20=None)
    sell = compute_sell_revenue(cfg, price=10.0, shares=100, adv20=None)
    return (buy.total_cost + sell.total_cost) / buy.base_amount
