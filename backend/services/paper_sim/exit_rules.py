"""Paper Sim v2 — 退出规则.

对一只 open position + 当日行情, 判断是否触发清仓 + 触发原因.

跟 portfolio_backtest.py 语义对齐 (确保两个回测器口径一致):

  1. stop hit       close ≤ entry × (1 + optimal_stop_pct)
                    optimal_stop_pct 在 mart 表里是**负数** (例 -0.05)
  2. target hit     high ≥ entry × (1 + optimal_target_pct) 且 NOT armed
                    target hit 是 "arm trailing" 信号, 不直接卖, 让 trailing 锁住涨幅
  3. trailing hit   armed=True 且 close ≤ high_since_arm × (1 - optimal_trailing_pct)
                    optimal_trailing_pct 在 mart 表里是**正数** (回撤幅度, 例 0.05 = -5%)
  4. hp expired     days_held ≥ optimal_hp
  5. stage_det      open stage ≤ 2, 今日 stage = 4, 且 current_close < entry (亏损时)

trailing_armed + high_since_arm 是跨日状态, 由 driver 在 fact_paper_sim_position
里跟踪 (新增字段).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class ExitInputs:
    """所有评估退出需要的输入. 单一字段表, 便于单测注入."""
    stock_code: str
    entry_price: float
    entry_stage: Optional[str]      # 买入时 technical_stage (字符串 e.g. '2', '4')
    optimal_hp: int
    optimal_stop_pct: Optional[float]      # 例 -0.05 (mart 表里负数)
    optimal_target_pct: Optional[float]    # 例 +0.20 (正数)
    optimal_trailing_pct: Optional[float]  # 例 +0.05 (mart 表里正数, 表示"回撤 5%")
    days_held: int
    current_close: float
    current_high: float              # 当日 high — target arm 判定用
    peak_since_entry: float          # max(close OR high) 自 trailing armed 以来
    trailing_armed: bool             # 已 arm trailing? (target hit 后变 True)
    today_stage: Optional[str]       # 今日 technical_stage (取自 fact_signal_context)
    min_forced_hp: int = 0           # Path A 2026-05-15: hp_expired 最小天数 (anti-churn)
                                     # 0 = 关闭, ≥1 = hp_expired 触发 days_held >= max(optimal_hp, min_forced_hp)
                                     # stop_hit / trailing / stage_det 不受此限 (真实风险退出永远允许)


@dataclass(frozen=True)
class ExitDecision:
    should_exit: bool
    reason: str = ""
    exit_price: float = 0.0
    new_trailing_armed: bool = False     # driver 拿这个去更新 fact_paper_sim_position
    new_peak: Optional[float] = None     # 同上, 跟踪 high_since_arm


def evaluate_exit(inp: ExitInputs) -> ExitDecision:
    """按优先级判断 5 个触发. 第一个命中就返回, 短路.

    Trailing 语义: target hit = arm trailing 信号 (不卖); arm 后 close ≤ peak × (1 - trailing_pct)
    才真卖. 这跟 portfolio_backtest.py 一致, 能锁住涨幅.
    """
    if inp.entry_price <= 0 or inp.current_close <= 0:
        return ExitDecision(False, reason="invalid_price")

    # 1. stop loss — 最优先 (止损永远优先)
    if inp.optimal_stop_pct is not None:
        stop_level = inp.entry_price * (1 + inp.optimal_stop_pct)
        if inp.current_close <= stop_level:
            return ExitDecision(True, reason="stop_hit", exit_price=inp.current_close)

    # 2. target — 不直接卖, 而是 arm trailing
    new_armed = inp.trailing_armed
    if (not inp.trailing_armed
            and inp.optimal_target_pct is not None
            and inp.current_high >= inp.entry_price * (1 + inp.optimal_target_pct)):
        new_armed = True

    # 3. trailing — armed 后才检
    if new_armed and inp.optimal_trailing_pct is not None:
        # peak_since_entry 是 max(high) 自 arm 起; mart 表里 trailing_pct 是正数 (回撤幅度)
        peak = max(inp.peak_since_entry, inp.current_high)
        trail_level = peak * (1 - inp.optimal_trailing_pct)
        if inp.current_close <= trail_level:
            return ExitDecision(True, reason="trailing_hit", exit_price=inp.current_close,
                                  new_trailing_armed=True, new_peak=peak)
        # 未触发, 更新 peak (跨日传给 driver)
        return ExitDecision(False, new_trailing_armed=True, new_peak=peak)

    # 4. hp 到期 (Path A 2026-05-15: 强制 min_forced_hp 防 churning)
    effective_hp = max(inp.optimal_hp, inp.min_forced_hp) if inp.optimal_hp > 0 else 0
    if effective_hp > 0 and inp.days_held >= effective_hp:
        return ExitDecision(True, reason="hp_expired", exit_price=inp.current_close,
                              new_trailing_armed=new_armed)

    # 5. stage 恶化 (买入时 ≤2 强信号, 今天 =4 + 当前亏损)
    if (inp.entry_stage in ("1", "1.5", "2")
            and inp.today_stage == "4"
            and inp.current_close < inp.entry_price):
        return ExitDecision(True, reason="stage_deterioration", exit_price=inp.current_close,
                              new_trailing_armed=new_armed)

    return ExitDecision(False, new_trailing_armed=new_armed, new_peak=inp.peak_since_entry)
