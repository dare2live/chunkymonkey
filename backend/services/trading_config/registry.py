"""Phase ε.1 — 全局执行模型实例 (一处声明).

⚠ 改参数: 改本文件的 DEFAULT_EXECUTION_MODEL.
⚠ 不要在业务代码里自建 ExecutionModel — 永远用这里的全局实例.

成本单一真相源 (2026-06-11 收敛, 体检 MEDIUM):
    交易成本 (佣金/印花/过户/规费/证管/滑点) 唯一定义在
    backend/config/paper_sim_config.yaml::tx_cost. 本文件不再 hardcode bps,
    而是用 _cost_from_paper_sim() 从该 yaml 派生 TradingCostConfig.
    旧 bug: registry sell_stamp_duty_bps=10 (= 印花 0.10%) vs paper_sim
    stamp_duty_sell_pct=0.0005 (= 0.05%, 2023-08-28 印花减半后真值) 两处冲突,
    且 registry round-trip (25.4 bps) 漏算交易所规费 + 证管费, 低估真实成本.
    收敛后 registry.round_trip_cost_pct() == labels.cost_after.compute_round_trip_cost_pct().
"""
from __future__ import annotations

from services.trading_config.buy_pricing import BuyPricingConfig
from services.trading_config.execution_model import ExecutionModel
from services.trading_config.filters import LimitBoardConfig
from services.trading_config.horizon import HorizonUnit
from services.trading_config.sell_pricing import SellPricingConfig
from services.trading_config.slippage import TradingCostConfig


def _cost_from_paper_sim() -> TradingCostConfig:
    """从 paper_sim_config.yaml::tx_cost 派生 bps-based TradingCostConfig (单一真相源).

    映射保证 round_trip 总成本与 labels.cost_after.compute_round_trip_cost_pct 完全一致
    (= 2×commission + stamp + 2×transfer + 2×exchange + 2×regulatory + 2×slippage).
    大单 surcharge (large_order_*) 是 runtime ADV20 触发量, 不入 universe-level baseline,
    与 cost_after 一致不计.

    buckets 映射 (pct → bps, ×10000):
      commission → buy/sell commission (双边)
      transfer + exchange + regulatory → buy/sell transfer bucket (均匀分双边)
      slippage → buy/sell impact bucket (双边)
      stamp_duty_sell → sell stamp (单边, A 股仅卖收印花)
    """
    # 延迟 import 避免模块加载期硬依赖 (paper_sim.config 不 import trading_config, 无环).
    from services.paper_sim.config import load_config

    tx = load_config().tx_cost
    bps = 10000.0
    # 把双边均摊费 (transfer/exchange/regulatory) 折进每侧 transfer bucket
    per_side_other = (tx.transfer_fee_pct + tx.exchange_fee_pct + tx.regulatory_fee_pct) * bps
    return TradingCostConfig(
        buy_commission_bps=tx.commission_pct * bps,
        buy_transfer_bps=per_side_other,
        buy_impact_bps=tx.slippage_pct * bps,
        sell_commission_bps=tx.commission_pct * bps,
        sell_transfer_bps=per_side_other,
        sell_stamp_duty_bps=tx.stamp_duty_sell_pct * bps,  # 5.0 bps (2023-08 减半)
        sell_impact_bps=tx.slippage_pct * bps,
    )


# ─────────────────────────────────────────────────────────────────────
# 全局默认执行模型 — 改这里全局生效
# ─────────────────────────────────────────────────────────────────────

DEFAULT_EXECUTION_MODEL = ExecutionModel(
    version="ε.1-2026-05-12",

    # 买入: T+1 VWAP (= amount / volume×100), 不加额外滑点
    # 选 vwap 而非 open: 因为 open 受当日跳空影响, VWAP 更代表实际平均成交价
    buy_pricing=BuyPricingConfig(
        mode="vwap",
        slippage_pct=0.0,
    ),

    # 卖出执行
    sell_pricing=SellPricingConfig(
        # 止损: 触发价 - 0.3% (保守, 实际可能跌穿)
        stop_loss_mode="trigger_with_slippage",
        stop_loss_slippage_pct=-0.003,
        # 止盈: target_price 直接成交 (保守, 实际常涨更多)
        target_hit_mode="target_price",
        # Trailing: 当日 close
        trailing_mode="close",
        # 持仓到期: 收盘卖
        hp_expiry_mode="close",
    ),

    # 交易成本: 从 paper_sim_config.yaml::tx_cost 派生 (单一真相源, 见模块 docstring)
    cost=_cost_from_paper_sim(),

    # 涨跌停过滤
    limit_board=LimitBoardConfig(
        reject_buy_on_limit_up_one_word=True,
        allow_sell_through_limit_down=True,
    ),

    # 持仓周期单位: 交易日
    horizon_unit=HorizonUnit.TRADING_DAYS,
)


def get_execution_model() -> ExecutionModel:
    """全局唯一获取入口 (便于测试 monkey patch)."""
    return DEFAULT_EXECUTION_MODEL


def assert_cost_single_source(tol: float = 1e-9) -> None:
    """断言 registry 成本 == labels.cost_after 成本 (防两处成本真相源再分裂).

    两条成本路径必须永远相等:
      - registry: DEFAULT_EXECUTION_MODEL.cost.round_trip_cost_pct() (bps 派生)
      - canonical: labels.cost_after.compute_round_trip_cost_pct(paper_sim tx_cost)
    任一处偏离 (e.g. 有人改回 registry hardcode / 改 paper_sim yaml 没同步) → raise.
    """
    from services.labels.cost_after import compute_round_trip_cost_pct
    from services.paper_sim.config import load_config

    registry_rt = DEFAULT_EXECUTION_MODEL.cost.round_trip_cost_pct()
    canonical_rt = compute_round_trip_cost_pct(load_config().tx_cost)
    if abs(registry_rt - canonical_rt) > tol:
        raise AssertionError(
            "成本真相源分裂! registry round_trip="
            f"{registry_rt:.8f} != paper_sim/cost_after round_trip={canonical_rt:.8f}. "
            "成本只能定义在 paper_sim_config.yaml::tx_cost; "
            "registry 必须用 _cost_from_paper_sim() 派生, 不允许 hardcode bps."
        )
