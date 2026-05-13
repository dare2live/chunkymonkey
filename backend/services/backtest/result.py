"""Phase ε.3 — 回测结果数据类 (frozen dataclass).

TradeResult: 单笔
BacktestSummary: 批量聚合
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

ExitReason = Literal["stop_loss", "trailing_stop", "target_hit", "hp_expired",
                     "one_word_blocked", "data_truncated"]


@dataclass(frozen=True)
class TradeResult:
    """单笔交易完整模拟结果."""
    stock_code: str
    signal_date: str        # T 日
    buy_date: str           # T+1
    buy_price: float        # 实际买入价 (含滑点)
    sell_date: str
    sell_price: float       # 实际卖出价 (含滑点)
    holding_days: int       # 实际持仓周期 (交易日)
    exit_reason: ExitReason
    gross_ret: float        # 不扣成本的毛收益
    net_ret: float          # 扣双边成本的净收益
    max_drawdown: float     # 持仓期间 (intraday low - buy) / buy 最低


@dataclass(frozen=True)
class BacktestSummary:
    """批量交易聚合 metrics. 这是替代旧 mart_stock_formula_optuna 的真实指标."""
    n_signals: int           # 触发的信号数
    n_traded: int            # 真实开仓数 (剔除一字板/数据缺失等)
    n_blocked: int           # 因 限制无法开仓的数量 (一字涨停)

    # 核心收益指标 (基于 net_ret)
    win_rate: float          # net_ret > 0 比例
    avg_ret: float           # mean
    median_ret: float        # 中位数 (抗极端值)
    std_ret: float
    sharpe: float            # mean / std (单位窗口)
    calmar: float            # mean / abs(median max_drawdown)

    # 真实持仓期相关
    avg_holding_days: float  # 实际平均持仓 (含提前止损/止盈, 可能 < target)
    avg_max_dd: float        # 平均 max_drawdown (真实)

    # 出场原因分布 (理解策略行为)
    n_exit_stop_loss: int
    n_exit_trailing: int
    n_exit_target_hit: int
    n_exit_hp_expired: int
    n_exit_one_word_blocked: int
    n_exit_data_truncated: int

    @property
    def exit_distribution(self) -> dict[str, float]:
        if self.n_traded == 0:
            return {}
        return {
            "stop_loss": self.n_exit_stop_loss / self.n_traded,
            "trailing_stop": self.n_exit_trailing / self.n_traded,
            "target_hit": self.n_exit_target_hit / self.n_traded,
            "hp_expired": self.n_exit_hp_expired / self.n_traded,
            "one_word_blocked": self.n_exit_one_word_blocked / self.n_traded,
            "data_truncated": self.n_exit_data_truncated / self.n_traded,
        }
