"""P0a label generation — cost-after forward returns for ML ranking training.

PLAN_V3 v3.2 P0a: Label = T+1 VWAP 入场后, 未来 5/10/20 日 VWAP 退出, 扣往返
真实交易成本 (commission + stamp_duty + transfer_fee + slippage) 后净收益.
停牌 / 涨跌停 mask=True, label=NULL.

接入:
    from services.labels.cost_after import compute_forward_cost_after_returns
"""
from services.labels.cost_after import (
    compute_forward_cost_after_returns,
    compute_round_trip_cost_pct,
)

__all__ = [
    "compute_forward_cost_after_returns",
    "compute_round_trip_cost_pct",
]
