"""Phase ε.3 — 真实回测引擎 (核心).

⚠ 取代旧 build_stock_formula_optuna.py 的 "持有到期 + 无止损" 简化逻辑.
⚠ 单笔模拟逐日推进, 触发 stop/trailing/target/hp 中任一即出场.
⚠ 一字涨停 → 延迟买入 (next 交易日重试, 重试 1 次仍涨停 → 放弃)
⚠ 所有定价 / 滑点 / 成本 / 涨跌停 → 走 services.trading_config

输入 (per signal):
  signal_date, stock_code, K 线序列 (date, open, high, low, close, volume, amount),
  stop_pct (例如 -0.05 表示 stop_price = buy × 0.95),
  target_pct (例如 +0.15),
  trailing_pct (例如 0.02),
  hp_target (持仓周期, 交易日).

输出: TradeResult (单笔) 或 BacktestSummary (批量聚合)
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

import numpy as np

from services.backtest.filters import cap_net_ret
from services.backtest.result import BacktestSummary, ExitReason, TradeResult
from services.trading_config import EXECUTION_MODEL, ExecutionModel
from services.trading_config.buy_pricing import compute_buy_price
from services.trading_config.filters import (
    infer_board, is_one_word_limit_up, is_one_word_limit_down, is_suspended,
)
from services.trading_config.sell_pricing import compute_sell_price
from services.trading_config.slippage import apply_costs_to_return


@dataclass(frozen=True)
class Bar:
    """单日 K 线 (轻量)."""
    date: str
    open: float
    high: float
    low: float
    close: float
    volume: float
    amount: float


# Phase ψ.β.perf: bar date → idx cache (per bars list, by id())
# 性能 hotspot: simulate_trade 每次调用 _idx O(N) linear search.
# Optuna 100 trials × N_signals × K-line bars = O(1e11) ops 在大跑批里.
# 用 id(bars) 做 key 缓存 {date: idx} — fork worker 内 bars_by_stock 共享内存,
# id 稳定不变, 缓存命中率近 100%.
# 防御: cache size 上限 (LRU evict), 防内存爆.
_BAR_DATE_IDX_CACHE: dict[int, dict[str, int]] = {}
_BAR_DATE_IDX_CACHE_MAX = 10_000   # 10K stocks 大约够全市场


def _idx(bars: list[Bar], date: str) -> int:
    """日期 → bars 列表索引. O(1) dict 查找 (含 cache build)."""
    key = id(bars)
    cache = _BAR_DATE_IDX_CACHE.get(key)
    if cache is None:
        if len(_BAR_DATE_IDX_CACHE) >= _BAR_DATE_IDX_CACHE_MAX:
            # 简单 evict: 随机删一个 (Python 3.7+ dict 保序, 删第一个即最老)
            _BAR_DATE_IDX_CACHE.pop(next(iter(_BAR_DATE_IDX_CACHE)), None)
        cache = {b.date: i for i, b in enumerate(bars)}
        _BAR_DATE_IDX_CACHE[key] = cache
    return cache.get(date, -1)


def simulate_trade(
    stock_code: str,
    signal_date: str,
    bars: list[Bar],
    stop_pct: float,        # 例 -0.05 表示 buy × (1+stop_pct) = buy × 0.95
    target_pct: float,      # 例 +0.15
    trailing_pct: float,    # 例 0.02 (回撤幅度, 正值)
    hp_target: int,         # 持仓 N 个交易日
    execution: ExecutionModel = EXECUTION_MODEL,
    buy_offset: int = 1,    # T+N 买入 (默认 T+1; 验证延迟买入假说时 ≥2)
) -> Optional[TradeResult]:
    """模拟单笔交易完整出场.

    Args:
        buy_offset: 信号日 + N 个交易日尝试买入 (默认 1 = T+1).
                    一字涨停时最多再延迟 1 个交易日重试.

    Returns:
        TradeResult, 或 None (信号日无效 / K 线不足)
    """
    n = len(bars)
    sig_i = _idx(bars, signal_date)
    if sig_i < 0 or sig_i + buy_offset >= n:
        return None  # 信号日不在 K 线中, 或 T+offset 缺数据

    # ─── 1) 尝试 T+buy_offset 买入 (一字涨停延迟 1 次) ───
    board = infer_board(stock_code)
    buy_i = -1
    for attempt in (sig_i + buy_offset, sig_i + buy_offset + 1):
        if attempt >= n:
            break
        bar = bars[attempt]
        prev = bars[attempt - 1]
        if is_suspended(bar.volume):
            continue
        if (execution.limit_board.reject_buy_on_limit_up_one_word
            and is_one_word_limit_up(bar.open, bar.high, bar.low, bar.close,
                                     prev.close, board, execution.limit_board)):
            continue
        buy_i = attempt
        break

    if buy_i < 0:
        # 全部一字涨停 / 停牌 → 无法开仓
        intended_buy_i = sig_i + buy_offset
        return TradeResult(
            stock_code=stock_code, signal_date=signal_date,
            buy_date=bars[intended_buy_i].date if intended_buy_i < n else "",
            buy_price=0.0,
            sell_date="", sell_price=0.0,
            holding_days=0, exit_reason="one_word_blocked",
            gross_ret=0.0, net_ret=0.0, max_drawdown=0.0,
        )

    # 算买入价 (走 trading_config)
    bb = bars[buy_i]
    buy_price = compute_buy_price(
        signal_close=bars[sig_i].close,
        next_open=bb.open, next_amount=bb.amount, next_volume=bb.volume,
        config=execution.buy_pricing,
    )
    if buy_price is None or buy_price <= 0:
        return None

    # ─── 2) 算止损/止盈线 ───
    stop_price   = buy_price * (1.0 + stop_pct)
    target_price = buy_price * (1.0 + target_pct)

    # ─── 3) 逐日推进, 触发 stop/trailing/target/hp ───
    high_since_buy = buy_price
    trailing_armed = False
    max_dd = 0.0   # 持仓期最低 (intraday) 相对 buy 的回撤 (≤ 0)

    for offset in range(1, n - buy_i):
        idx = buy_i + offset
        b = bars[idx]
        prev_b = bars[idx - 1]
        if is_suspended(b.volume):
            continue

        # 更新 max_dd (intraday low)
        cur_dd = (b.low - buy_price) / buy_price
        if cur_dd < max_dd:
            max_dd = cur_dd

        # 优先级 1: stop_loss (intraday low ≤ stop_price)
        if b.low <= stop_price:
            sp = compute_sell_price(
                "stop_loss",
                stop_price=stop_price,
                today_open=b.open, today_high=b.high, today_low=b.low, today_close=b.close,
                config=execution.sell_pricing,
            )
            return _build_result(stock_code, signal_date, bb.date, buy_price,
                                 b.date, sp, offset, "stop_loss", max_dd, execution)

        # 优先级 2: target_hit (intraday high ≥ target_price → arm trailing)
        if not trailing_armed and b.high >= target_price:
            trailing_armed = True

        # 优先级 3: trailing (arm 后, close 较 high_since 回撤 ≥ trailing_pct → 卖)
        if trailing_armed:
            if b.high > high_since_buy:
                high_since_buy = b.high
            dd_from_high = (b.close - high_since_buy) / high_since_buy
            if dd_from_high <= -trailing_pct:
                sp = compute_sell_price(
                    "trailing_stop",
                    today_open=b.open, today_high=b.high, today_low=b.low, today_close=b.close,
                    config=execution.sell_pricing,
                )
                return _build_result(stock_code, signal_date, bb.date, buy_price,
                                     b.date, sp, offset, "trailing_stop", max_dd, execution)

        # 优先级 4: hp_expired
        if offset >= hp_target:
            sp = compute_sell_price(
                "hp_expired",
                today_open=b.open, today_high=b.high, today_low=b.low, today_close=b.close,
                config=execution.sell_pricing,
            )
            return _build_result(stock_code, signal_date, bb.date, buy_price,
                                 b.date, sp, offset, "hp_expired", max_dd, execution)

    # K 线提前耗尽 (尾部数据不足)
    return _build_result(stock_code, signal_date, bb.date, buy_price,
                         bars[-1].date, bars[-1].close, n - buy_i - 1,
                         "data_truncated", max_dd, execution)


def _build_result(stock_code: str, signal_date: str, buy_date: str, buy_price: float,
                  sell_date: str, sell_price: Optional[float], holding_days: int,
                  reason: ExitReason, max_dd: float, execution: ExecutionModel) -> TradeResult:
    if sell_price is None or buy_price <= 0:
        gross = 0.0
    else:
        gross = (sell_price - buy_price) / buy_price
    net = apply_costs_to_return(gross, execution.cost)
    # Phase ζ: 单笔极值 cap, 防止复权 spike 污染聚合 metrics
    gross = cap_net_ret(gross)
    net = cap_net_ret(net)
    return TradeResult(
        stock_code=stock_code, signal_date=signal_date,
        buy_date=buy_date, buy_price=buy_price,
        sell_date=sell_date, sell_price=sell_price or 0.0,
        holding_days=holding_days, exit_reason=reason,
        gross_ret=gross, net_ret=net, max_drawdown=max_dd,
    )


# ─────────────────────────────────────────────────────────────────────
# 批量聚合
# ─────────────────────────────────────────────────────────────────────

def backtest_signals_with_trades(
    signals: list[dict],
    bars_by_stock: dict[str, list[Bar]],
    stop_pct: float,
    target_pct: float,
    trailing_pct: float,
    hp_target: int,
    execution: ExecutionModel = EXECUTION_MODEL,
    buy_offset: int = 1,
) -> tuple[BacktestSummary, list[TradeResult]]:
    """Phase ψ.β.perf: 同时返回 summary + trades, 避免调用方重新跑 simulate_trade."""
    trades: list[TradeResult] = []
    for sig in signals:
        bars = bars_by_stock.get(sig["stock_code"])
        if not bars:
            continue
        t = simulate_trade(
            stock_code=sig["stock_code"], signal_date=sig["signal_date"],
            bars=bars, stop_pct=stop_pct, target_pct=target_pct,
            trailing_pct=trailing_pct, hp_target=hp_target, execution=execution,
            buy_offset=buy_offset,
        )
        if t is not None:
            trades.append(t)
    return aggregate(trades, n_signals=len(signals)), trades


def backtest_signals(
    signals: list[dict],   # [{stock_code, signal_date}, ...]
    bars_by_stock: dict[str, list[Bar]],
    stop_pct: float,
    target_pct: float,
    trailing_pct: float,
    hp_target: int,
    execution: ExecutionModel = EXECUTION_MODEL,
    buy_offset: int = 1,
) -> BacktestSummary:
    """Legacy 兼容接口 — 内部走 backtest_signals_with_trades."""
    summary, _ = backtest_signals_with_trades(
        signals=signals, bars_by_stock=bars_by_stock,
        stop_pct=stop_pct, target_pct=target_pct, trailing_pct=trailing_pct,
        hp_target=hp_target, execution=execution, buy_offset=buy_offset,
    )
    return summary


def aggregate(trades: list[TradeResult], n_signals: int) -> BacktestSummary:
    """聚合 trades → BacktestSummary."""
    traded = [t for t in trades if t.exit_reason != "one_word_blocked"]
    n_blocked = len(trades) - len(traded)

    if not traded:
        return BacktestSummary(
            n_signals=n_signals, n_traded=0, n_blocked=n_blocked,
            win_rate=0.0, avg_ret=0.0, median_ret=0.0, std_ret=0.0,
            sharpe=0.0, calmar=0.0,
            avg_holding_days=0.0, avg_max_dd=0.0,
            n_exit_stop_loss=0, n_exit_trailing=0,
            n_exit_target_hit=0, n_exit_hp_expired=0,
            n_exit_one_word_blocked=n_blocked,
            n_exit_data_truncated=0,
        )

    rets = np.array([t.net_ret for t in traded])
    dds = np.array([t.max_drawdown for t in traded])
    hps = np.array([t.holding_days for t in traded])

    n = len(traded)
    win_rate = float((rets > 0).mean())
    avg_ret = float(rets.mean())
    median_ret = float(np.median(rets))
    std_ret = float(rets.std())
    sharpe = float(avg_ret / std_ret) if std_ret > 0 else 0.0
    avg_dd = float(dds.mean())
    calmar = float(avg_ret / abs(avg_dd)) if abs(avg_dd) > 0.001 else 0.0

    def _count(reason: str) -> int:
        return sum(1 for t in traded if t.exit_reason == reason)

    return BacktestSummary(
        n_signals=n_signals, n_traded=n, n_blocked=n_blocked,
        win_rate=win_rate, avg_ret=avg_ret, median_ret=median_ret, std_ret=std_ret,
        sharpe=sharpe, calmar=calmar,
        avg_holding_days=float(hps.mean()),
        avg_max_dd=avg_dd,
        n_exit_stop_loss=_count("stop_loss"),
        n_exit_trailing=_count("trailing_stop"),
        n_exit_target_hit=_count("target_hit"),
        n_exit_hp_expired=_count("hp_expired"),
        n_exit_one_word_blocked=n_blocked,
        n_exit_data_truncated=_count("data_truncated"),
    )
