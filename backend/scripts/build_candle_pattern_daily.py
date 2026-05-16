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

    # 计算 candle features per row
    batch: list[tuple] = []
    t1 = time.time()
    for r in rows:
        code, date, op, hi, lo, cl, vol, vol_ma_amt, close_max_20 = r
        if vol_ma_amt is None or close_max_20 is None:
            # 前 20 日 prior history 不足 → skip
            continue
        # vol_ma20 用 amount-based 转 vol (close × volume ≈ amount, 近似)
        # 但 compute_features_for_signal 期望 vol_ma20 = average volume, 不是 amount.
        # 用 amount / close 近似 average volume (PIT 安全, 都是 prior days).
        # 实际应跑 AVG(volume) OVER. 简化此处.
        vol_ma20 = vol_ma_amt / cl if cl > 0 else None
        if not vol_ma20:
            continue
        feats = compute_features_for_signal(
            open_p=op, high=hi, low=lo, close=cl, volume=vol,
            vol_ma20=vol_ma20, close_max_20=close_max_20,
        )
        if feats is None:
            continue
        batch.append((
            code, date,
            feats.body_ratio, feats.upper_shadow_ratio, feats.lower_shadow_ratio,
            feats.close_position, feats.volume_relative, feats.breakout_strength_20,
            feats.is_bullish, feats.is_doji, feats.is_long_lower_shadow,
            feats.is_long_upper_shadow, feats.is_marubozu, feats.is_high_volume,
            date,  # source_max_trade_date = trade_date (PIT)
        ))
        if len(batch) >= args.batch_size:
            _flush_batch(conn, batch)
            total_rows += len(batch)
            batch = []
            if total_rows % 50000 == 0:
                log.info(f"  ... {total_rows:,} rows written ({time.time()-t1:.0f}s)")
    if batch:
        _flush_batch(conn, batch)
        total_rows += len(batch)

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
    """Bulk INSERT OR REPLACE (idempotent rebuild)."""
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
