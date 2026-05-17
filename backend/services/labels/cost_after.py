"""P0a cost-after forward returns — ML ranking training label.

⚠ PLAN_V3 v3.2 P0a: 训练 label 必须扣真实交易成本 + 含不可成交 mask,
    否则模型见到的是理想化收益, 实盘必然达不到 backtest 数字 (Rule 9.1 真金白银).

入场 / 退出: T+1 VWAP (用户决策 2026-05-14):
    - signal_date = t (公告/信号生成日)
    - entry_date = t+1 (T+1 交易日, A 股不允许 T+0)
    - exit_date_5d = t+1 + 5 个交易日 (= 持有 5 trading day → VWAP exit)
    - exit_date_10d / 20d / 60d / 90d 同理

往返成本组成 (paper_sim_config.yaml::tx_cost):
    买入: commission + slippage + transfer_fee (SH 0.001%, SZ 0)
    卖出: commission + slippage + stamp_duty (0.05%) + transfer_fee
    round_trip = 2 × commission + 2 × slippage + stamp_duty + 2 × transfer_fee
               ≈ 0.0005 + 0.002 + 0.0005 + 0.00002 ≈ 0.003 (0.3%)

不可成交 mask=True 条件:
    - T+1 entry: 停牌 (无 K 线 / volume=0) OR T+1 open 触涨跌停 (≥9.8% 普通股 / 19.8% 创业板/科创板 / 4.95% ST)
    - forward N 日 exit: 同上, exit_date 不可成交 → 顺延 N+1 / N+2 直到能成交, 仍不行则 label_Nd=NULL.
    实施: 调用方传 unable_to_trade_mask 已计算好 (此模块只接 vwap 输入).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from services.paper_sim.config import TxCostConfig


def compute_round_trip_cost_pct(tx: TxCostConfig) -> float:
    """单次完整往返 (买 + 卖) 真实成本 % (occurring on principal).

    Codex C-B 2026-05-15: 接入完整 A 股成本结构 (印花/佣金/过户/规费/证管/滑点).
    大单 surcharge (adv20 触发) 不在 label 阶段计入 (label 是 universe-level baseline,
    不假设 order size). paper_sim runtime 才按实际 ADV20 + 仓位计.

    Returns:
        round_trip_pct ≈ 0.0027 (0.27%, 不含大单 surcharge).
    """
    return (
        2 * tx.commission_pct                # 佣金双边
        + tx.stamp_duty_sell_pct             # 印花税卖单边
        + 2 * tx.transfer_fee_pct            # 过户费双边 (2023+ 沪深统一)
        + 2 * tx.exchange_fee_pct            # 交易所规费双边
        + 2 * tx.regulatory_fee_pct          # 证管费双边
        + 2 * tx.slippage_pct                # 基础滑点双边
    )


@dataclass(frozen=True)
class ForwardCostAfterResult:
    """5/10/20/60/90 horizon forward cost-after net returns.

    None 表示该 horizon 不可成交 / 数据缺失.
    """
    fwd_cost_after_5d: Optional[float]
    fwd_cost_after_10d: Optional[float]
    fwd_cost_after_20d: Optional[float]
    fwd_cost_after_60d: Optional[float]
    fwd_cost_after_90d: Optional[float]
    round_trip_cost_pct: float


def compute_forward_cost_after_returns(
    entry_vwap: Optional[float],
    exit_vwap_5d: Optional[float],
    exit_vwap_10d: Optional[float],
    exit_vwap_20d: Optional[float],
    exit_vwap_60d: Optional[float] = None,
    exit_vwap_90d: Optional[float] = None,
    *,
    tx: TxCostConfig,
    entry_unable: bool = False,
    exit_5d_unable: bool = False,
    exit_10d_unable: bool = False,
    exit_20d_unable: bool = False,
    exit_60d_unable: bool = False,
    exit_90d_unable: bool = False,
) -> ForwardCostAfterResult:
    """P0a label: T+1 VWAP 入场后 5/10/20/60/90 日 VWAP 退出, 扣往返 tx_cost.

    Args:
        entry_vwap: T+1 VWAP (停牌/涨跌停 → 调用方传 None 或设 entry_unable=True).
        exit_vwap_5d/10d/20d/60d/90d: 对应 trading-calendar offset 后的 VWAP.
        tx: paper_sim_config tx_cost 配置.
        entry_unable: True 表示 T+1 entry mask, 所有 horizon label=None.
        exit_*_unable: True 表示该 horizon exit mask, 仅该 horizon label=None.

    Returns:
        ForwardCostAfterResult: 多 horizon cost-after net 收益, 不可成交 → None.

    Formula:
        gross_return_N = exit_vwap_N / entry_vwap - 1
        net_return_N = gross_return_N - round_trip_cost_pct
        (假设 cost 按本金线性扣, 简化, 不算复利; 散户 5 仓位场景误差 < 0.005%.)
    """
    round_trip = compute_round_trip_cost_pct(tx)

    def _label(exit_vwap: Optional[float], exit_unable: bool) -> Optional[float]:
        if entry_unable or exit_unable:
            return None
        if entry_vwap is None or exit_vwap is None:
            return None
        if entry_vwap <= 0 or exit_vwap <= 0:
            return None
        gross = exit_vwap / entry_vwap - 1.0
        return gross - round_trip

    return ForwardCostAfterResult(
        fwd_cost_after_5d=_label(exit_vwap_5d, exit_5d_unable),
        fwd_cost_after_10d=_label(exit_vwap_10d, exit_10d_unable),
        fwd_cost_after_20d=_label(exit_vwap_20d, exit_20d_unable),
        fwd_cost_after_60d=_label(exit_vwap_60d, exit_60d_unable),
        fwd_cost_after_90d=_label(exit_vwap_90d, exit_90d_unable),
        round_trip_cost_pct=round_trip,
    )
