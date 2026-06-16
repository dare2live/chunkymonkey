"""信号生命周期 episode 引擎 (G1, owner=docs/conditional_alpha_program.md §2 L4)。

公式【买入信号→持有→卖出信号/时间上限】= 一个 episode (持仓段)。每 episode 记含成本净收益 +
入场时的 segment (形态×阶段 = technical_stage, 分层 = MACD 零轴) → 按 (公式×segment cell) 刻画
"哪个形态阶段真赚钱" (用户方法论第 4 步: 先找公式买→卖阶段, 再找其特征)。

红线 (PIT / 执行 / 出场口径):
- PIT: 信号在 close[i] 确认, 入场 T+1 open (i+1); stage[i]/dif[i] 只用 bars[:i] (上游分类器已保证)。
- 执行 (防 R2 幻觉): 入场日一字涨停 = 买不进 → 跳过该信号 (buyable-only); 出场 T+1 open; 含非对称成本 (卖方印花)。
- 出场口径 (用户 2026-06-16 铁律: 不存在全市场统一卖出信号): 优先【公式自身卖出信号】(如 MACD 死叉);
  无信号则 hold_cap 时间上限兜底。持仓周期不是全局固定, 是每 cell 从 episode 实测出来的。
- 同股持仓不重叠: 出场后下一次买入信号才再入场。
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path

import numpy as np
import yaml

_CONFIG = Path(__file__).resolve().parents[1] / "config" / "backtest_execution.yaml"


@dataclass(frozen=True)
class Episode:
    stock_code: str
    entry_date: str
    exit_date: str
    hold_days: int
    gross_return: float
    net_return: float          # 含非对称往返成本
    entry_stage: str           # technical_stage: 1底部/1.5突破/2上升/3顶部/4下跌/unknown
    entry_zero_axis: str       # 'above' (MACD DIF>=0) / 'below'
    exit_reason: str           # 'sell_signal' / 'hold_cap' / 'data_end'


def load_exec_cfg(path: Path | None = None) -> dict:
    return yaml.safe_load((path or _CONFIG).read_text(encoding="utf-8"))


def round_trip_cost(cfg: dict) -> float:
    """A股非对称往返成本率 (买卖各一次, 卖方加印花)。镜像 backtest_execution.yaml cost 节。"""
    c = cfg["cost"]
    one_side = (c["commission_pct"] + c["slippage_pct"] + c["transfer_fee_pct"]
                + c["exchange_fee_pct"] + c["regulatory_fee_pct"])
    return one_side * 2 + c["stamp_duty_sell_pct"]   # 印花仅卖方 (非对称核心)


def limit_pct(code: str, cfg: dict) -> float:
    """板块涨跌停幅度 (按代码前缀)。"""
    by_prefix = cfg["limit_board"]["by_prefix"]
    for prefix in sorted(by_prefix, key=len, reverse=True):   # 长前缀优先 (68 先于 6)
        if code.startswith(prefix):
            return float(by_prefix[prefix])
    return 0.10


def _is_one_line_up(open_p: float, high_p: float, low_p: float, prev_close: float,
                    lp: float, tol: float) -> bool:
    """一字涨停 (open==high==low 且涨幅达板幅*tol) = T+1 买不进。"""
    if prev_close <= 0 or open_p <= 0:
        return False
    return high_p == low_p and (open_p / prev_close - 1.0) >= lp * tol


def build_episodes(
    stock_code: str,
    dates: np.ndarray,
    opens: np.ndarray,
    highs: np.ndarray,
    lows: np.ndarray,
    closes: np.ndarray,
    buy_mask: np.ndarray,
    sell_mask: np.ndarray,
    stages: np.ndarray,
    dif: np.ndarray,
    *,
    cfg: dict,
    hold_cap: int = 60,           # rule-compliance: ok evidence=持仓上限兜底 60 交易日 (~3月波段; 真实持仓由卖出信号决定)
    start_date: str = "2020-01-01",   # rule-compliance: ok evidence=2020 起 KPI OOS 窗 (goal.md North-Star)
) -> list[Episode]:
    """对单股构建 episode 列表 (买→卖/止时, 同股不重叠)。"""
    n = len(closes)
    rt_cost = round_trip_cost(cfg)
    lp = limit_pct(stock_code, cfg)
    tol = float(cfg["limit_board"]["detect_tol"])
    out: list[Episode] = []
    i = 0
    while i < n - 1:
        if not buy_mask[i] or str(dates[i]) < start_date:
            i += 1
            continue
        ei = i + 1                                  # 入场 T+1
        if _is_one_line_up(opens[ei], highs[ei], lows[ei], closes[i], lp, tol):
            i += 1                                  # 一字板买不进, 跳过该信号 (buyable-only)
            continue
        entry_open = opens[ei]
        if entry_open <= 0:
            i += 1
            continue
        # 出场: 入场后第一个卖出信号 → T+1 出; 否则 hold_cap 兜底
        cap_idx = min(ei + hold_cap, n - 1)
        exit_idx, reason = cap_idx, ("hold_cap" if ei + hold_cap < n else "data_end")
        for j in range(ei, cap_idx + 1):
            if sell_mask[j]:
                exit_idx = min(j + 1, n - 1)
                reason = "sell_signal"
                break
        exit_open = opens[exit_idx]
        if exit_open <= 0:
            i = exit_idx + 1
            continue
        gross = exit_open / entry_open - 1.0
        out.append(Episode(
            stock_code=stock_code,
            entry_date=str(dates[ei]),
            exit_date=str(dates[exit_idx]),
            hold_days=int(exit_idx - ei),
            gross_return=float(gross),
            net_return=float(gross - rt_cost),
            entry_stage=str(stages[i]) if i < len(stages) else "unknown",
            entry_zero_axis="above" if dif[i] >= 0 else "below",
            exit_reason=reason,
        ))
        i = exit_idx + 1                            # 同股持仓不重叠
    return out


def aggregate_by_cell(episodes: list[Episode], *, by: tuple[str, ...] = ("entry_stage", "entry_zero_axis")) -> dict:
    """按 segment cell 聚合 episode 统计 (含成本)。返回 {cell_key: stats}。"""
    cells: dict[tuple, list[Episode]] = {}
    for ep in episodes:
        key = tuple(getattr(ep, b) for b in by)
        cells.setdefault(key, []).append(ep)
    cells[("__ALL__",)] = list(episodes)             # 无条件对照
    result = {}
    for key, eps in cells.items():
        rets = np.array([e.net_return for e in eps], dtype=float)
        holds = np.array([e.hold_days for e in eps], dtype=float)
        wins = rets[rets > 0]
        losses = rets[rets <= 0]
        mean_hold = float(holds.mean()) if len(holds) else 0.0
        mean_net = float(rets.mean()) if len(rets) else 0.0
        # 年化: 单 episode 平均净收益按平均持仓天数年化 (252 交易日)
        annual = float((1.0 + mean_net) ** (252.0 / mean_hold) - 1.0) if mean_hold > 0 and (1.0 + mean_net) > 0 else None
        result["|".join(map(str, key))] = {
            "n_episodes": len(eps),
            "win_rate": float(len(wins) / len(rets)) if len(rets) else None,
            "mean_net_return": mean_net,
            "median_net_return": float(np.median(rets)) if len(rets) else None,
            "payoff_ratio": float(wins.mean() / abs(losses.mean())) if len(wins) and len(losses) else None,
            "expectancy": mean_net,
            "mean_hold_days": mean_hold,
            "annualized_per_episode": annual,
        }
    return result
