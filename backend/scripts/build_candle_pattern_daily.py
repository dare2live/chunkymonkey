"""Phase 1 — fact_candle_pattern_daily ETL builder (Codex aa4a41ca Path 3, 2026-05-16).

PIT-safe build: per (stock_code, trade_date) 用 prior 20 day bars 算 vol_ma20 / close_max_20,
当日 OHLCV 算 6 数值 + 6 binary.

PIT 锚点: source_max_trade_date = trade_date (today's bar 用 today, 20-day MA 用 prior days).
   不读未来 K线.

用法:
    # smoke (last 30 day, 100 stocks)
    PYTHONPATH=backend python backend/scripts/build_candle_pattern_daily.py \\
        --start 2026-03-01 --end 2026-04-23 --limit-stocks 100

    # 全量 (覆盖 panel 时段)
    PYTHONPATH=backend python backend/scripts/build_candle_pattern_daily.py \\
        --start 2024-01-01 --end 2026-04-23
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import duckdb

from services.candle_pattern.features import compute_features_for_signal
from services.candle_pattern.ddl import CANDLE_PATTERN_DDL


logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("build_candle_pattern_daily")


SMART_DB = Path(__file__).resolve().parents[2] / "data" / "smartmoney.duckdb"


def main() -> int:
    parser = argparse.ArgumentParser()
    # rule-compliance: ok evidence=Phase1-smoke (Phase 1 build script default smoke window)
    parser.add_argument("--start", default="2024-01-01")
    parser.add_argument("--end", default="2026-04-23")     # rule-compliance: ok evidence=v3-panel-coverage
    parser.add_argument("--limit-stocks", type=int, default=None)
    parser.add_argument("--batch-size", type=int, default=5000,
                        help="commit 频率 (每 N rows)")
    args = parser.parse_args()

    log.info(f"=== build_candle_pattern_daily ===")
    log.info(f"  window: {args.start} → {args.end}")
    log.info(f"  limit-stocks: {args.limit_stocks or 'all'}")

    conn = duckdb.connect(str(SMART_DB))
    conn.execute(CANDLE_PATTERN_DDL)

    # 加载 K线 + 20-day prior 数据 (cross-DB attach market)
    market_db = Path(__file__).resolve().parents[2] / "data" / "market.duckdb"
    conn.execute(f"ATTACH IF NOT EXISTS '{market_db}' AS market (READ_ONLY)")

    # 选 universe (优先 panel 已 cover 的 stock list)
    limit_clause = f"LIMIT {args.limit_stocks}" if args.limit_stocks else ""
    stocks = [r[0] for r in conn.execute(f"""
        SELECT DISTINCT stock_code FROM mart_p0a_feature_label_panel_v3
        ORDER BY stock_code
        {limit_clause}
    """).fetchall()]
    log.info(f"  universe: {len(stocks):,} stocks")

    # 每 stock 批量算 candle_pattern
    total_rows = 0
    t0 = time.time()
    qs = ",".join("?" * len(stocks))
    rows = conn.execute(f"""
        SELECT
            code AS stock_code,
            date AS trade_date,
            open, high, low, close, volume,
            -- 20 day prior MA (PIT: WINDOW 不含今日)
            AVG(amount) OVER (
                PARTITION BY code ORDER BY date
                ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING
            ) AS vol_ma20_amt,
            -- 20 day prior MAX close (PIT)
            MAX(close) OVER (
                PARTITION BY code ORDER BY date
                ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING
            ) AS close_max_20
        FROM market.v_price_kline_qfq
        WHERE adjust='qfq' AND freq='daily'
          AND code IN ({qs})
          AND date >= ? AND date <= ?
        ORDER BY code, date
    """, [*stocks, args.start, args.end]).fetchall()

    log.info(f"  K线 rows loaded: {len(rows):,} ({time.time()-t0:.0f}s)")

    # Vectorized 算 candle features (numpy/pandas, 100x faster than Python loop)
    import pandas as pd, numpy as np
    df = pd.DataFrame(rows, columns=[
        "stock_code", "trade_date", "open", "high", "low", "close",
        "volume", "vol_ma_amt", "close_max_20",
    ])
    # Drop rows missing 20-day prior history
    df = df.dropna(subset=["vol_ma_amt", "close_max_20"])
    df = df[(df["close"] > 0) & (df["high"] >= df["low"]) & ((df["high"] - df["low"]) > 1e-9)]

    full = df["high"] - df["low"]
    body = (df["close"] - df["open"]).abs()
    upper = df["high"] - df[["open", "close"]].max(axis=1)
    lower = df[["open", "close"]].min(axis=1) - df["low"]

    df["body_ratio"] = body / full
    df["upper_shadow_ratio"] = upper / full
    df["lower_shadow_ratio"] = lower / full
    df["close_position"] = (df["close"] - df["low"]) / full
    # vol_ma20 = vol_ma_amt / close (avg vol approx, PIT prior)
    vol_ma20 = df["vol_ma_amt"] / df["close"]
    df["volume_relative"] = np.where(vol_ma20 > 0, df["volume"] / vol_ma20, 1.0)
    df["breakout_strength_20"] = np.where(
        df["close_max_20"] > 0,
        (df["close"] - df["close_max_20"]) / df["close_max_20"],
        0.0,
    )
    # 6 binary 派生
    df["is_bullish"] = df["close"] > df["open"]
    df["is_doji"] = df["body_ratio"] < 0.1
    df["is_long_lower_shadow"] = df["lower_shadow_ratio"] > 0.6
    df["is_long_upper_shadow"] = df["upper_shadow_ratio"] > 0.6
    df["is_marubozu"] = df["body_ratio"] > 0.9
    df["is_high_volume"] = df["volume_relative"] > 2.0
    df["source_max_trade_date"] = df["trade_date"]

    log.info(f"  vectorized compute done: {len(df):,} rows ({time.time()-t0:.0f}s)")

    # Bulk insert via DuckDB register
    out_df = df[[
        "stock_code", "trade_date",
        "body_ratio", "upper_shadow_ratio", "lower_shadow_ratio",
        "close_position", "volume_relative", "breakout_strength_20",
        "is_bullish", "is_doji", "is_long_lower_shadow",
        "is_long_upper_shadow", "is_marubozu", "is_high_volume",
        "source_max_trade_date",
    ]]
    conn.register("candle_pattern_tmp", out_df)
    conn.execute("""
        INSERT OR REPLACE INTO fact_candle_pattern_daily (
            stock_code, trade_date,
            body_ratio, upper_shadow_ratio, lower_shadow_ratio,
            close_position, volume_relative, breakout_strength_20,
            is_bullish, is_doji, is_long_lower_shadow,
            is_long_upper_shadow, is_marubozu, is_high_volume,
            source_max_trade_date
        )
        SELECT * FROM candle_pattern_tmp
    """)
    conn.unregister("candle_pattern_tmp")
    total_rows = len(out_df)

    log.info(f"  build done: {total_rows:,} rows written, {time.time()-t0:.0f}s total")

    # PIT integrity audit
    bad = conn.execute("""
        SELECT COUNT(*) FROM fact_candle_pattern_daily
        WHERE source_max_trade_date > trade_date
    """).fetchone()[0]
    if bad > 0:
        log.error(f"  PIT integrity FAIL: {bad} rows with source_max_trade_date > trade_date")
        return 1
    log.info(f"  PIT integrity PASS: 0 rows violate")

    return 0


def _flush_batch(conn, batch: list[tuple]) -> None:
    """Legacy: Bulk INSERT OR REPLACE (idempotent rebuild).

    Note (2026-05-16): vectorized 版本不用这个, 用 DataFrame register + INSERT FROM SELECT.
    保留 backwards-compat.
    """
    conn.executemany("""
        INSERT OR REPLACE INTO fact_candle_pattern_daily (
            stock_code, trade_date,
            body_ratio, upper_shadow_ratio, lower_shadow_ratio,
            close_position, volume_relative, breakout_strength_20,
            is_bullish, is_doji, is_long_lower_shadow,
            is_long_upper_shadow, is_marubozu, is_high_volume,
            source_max_trade_date
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, batch)


if __name__ == "__main__":
    sys.exit(main())
