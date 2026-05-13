"""Phase γ D2 — 最新 K 线: latest_close + chg_pct。

来源: market.duckdb v_price_kline_qfq (前复权)
用法: build_picture_daily.py 一次性拉所有股票 last 2 天, 内存计算 chg_pct

为何不直接读 stock.db?
  - market.duckdb 是 K 线主源 (smartmoney.duckdb 只引用), v3 用前复权
  - 用 ATTACH 'market.duckdb' AS market 时, 直接 query market.v_price_kline_qfq
"""
from __future__ import annotations

from typing import Any


def compute_chg_pct(today_close: float | None, prev_close: float | None) -> float | None:
    """涨跌幅 = (today - prev) / prev (小数, 0.012 = +1.2%)。"""
    if today_close is None or prev_close is None or prev_close <= 0:
        return None
    return (today_close - prev_close) / prev_close


def derive_kline_latest(
    *,
    today_close: float | None,
    prev_close: float | None,
) -> dict[str, float | None]:
    """聚合输出 mart_stock_picture_daily 用的字段。"""
    return {
        "latest_close": today_close,
        "chg_pct": compute_chg_pct(today_close, prev_close),
    }
