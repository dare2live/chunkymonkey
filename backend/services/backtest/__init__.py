"""Phase ε.3 — 回测引擎 package.

⚠ 唯一对外接口:

    from services.backtest.realistic_engine import simulate_trade, backtest_signals
    from services.backtest.result import TradeResult, BacktestSummary

逻辑职责:
  - simulate_trade(signal, kline, ...) → 单笔交易完整出场模拟
  - backtest_signals(signals, klines, ...) → 批量聚合 metrics
  - 所有定价/止损/到期/滑点/成本/涨跌停 全部 走 services.trading_config
"""
from services.backtest.realistic_engine import backtest_signals, simulate_trade
from services.backtest.result import BacktestSummary, ExitReason, TradeResult

__all__ = [
    "simulate_trade", "backtest_signals",
    "TradeResult", "BacktestSummary", "ExitReason",
]
