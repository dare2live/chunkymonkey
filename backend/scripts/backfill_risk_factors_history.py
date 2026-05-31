"""Phase ψ.β.1 — fact_risk_factors PIT 历史 backfill.

⚠ 用户要求 (Rule 9.1 真金白银): paper_sim 选股要用 ensemble 5 alpha, 但当前
   fact_risk_factors 只 15 天 (2026-04-02 → 2026-05-13). 无法历史回测.

⚠ 此脚本: 算每股每交易日 trailing 30/60/120 天 metrics, 写入 800 天历史
   (2023-01-01 → latest_closed). 严格 PIT — 每行 (stock × date) 只用 ≤ date 的 K 线.

输出表 fact_risk_factors 字段 (跟现有 services/risk_factors.py 一致):
  stock_code, calc_date,
  vol_30d/60d/120d, max_dd_60d/120d, sharpe_30d/60d, skew_60d, kurt_60d,
  mom_30d/120d, n_bars, ingested_at

usage:
  PYTHONPATH=backend python backend/scripts/backfill_risk_factors_history.py
  PYTHONPATH=backend python backend/scripts/backfill_risk_factors_history.py --start 2023-01-01 --end 2026-05-12
"""
from __future__ import annotations

import argparse
import logging
import math
import time
from pathlib import Path

import duckdb

from services.data_governance import validate_rows_before_insert


log = logging.getLogger("backfill_risk_factors_history")
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s",
                    datefmt="%H:%M:%S")


MARKET_DB = Path(__file__).resolve().parents[2] / "data" / "market.duckdb"
SMART_DB  = Path(__file__).resolve().parents[2] / "data" / "smartmoney.duckdb"

TRADING_DAYS_PER_YEAR = 252


def ensure_table(conn) -> None:
    """跟 services/risk_factors.py:ensure_table 同 schema."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS fact_risk_factors (
            stock_code   TEXT NOT NULL,
            calc_date    TEXT NOT NULL,
            vol_30d      DOUBLE,
            vol_60d      DOUBLE,
            vol_120d     DOUBLE,
            max_dd_60d   DOUBLE,
            max_dd_120d  DOUBLE,
            sharpe_30d   DOUBLE,
            sharpe_60d   DOUBLE,
            skew_60d     DOUBLE,
            kurt_60d     DOUBLE,
            mom_30d      DOUBLE,
            mom_120d     DOUBLE,
            n_bars       INTEGER,
            ingested_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (stock_code, calc_date)
        );
        CREATE INDEX IF NOT EXISTS idx_rf_date ON fact_risk_factors(calc_date);
    """)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2023-01-01")
    parser.add_argument("--end", default=None,
                        help="默认 K 线 max(date)")
    parser.add_argument("--min-bars", type=int, default=30,
                        help="每日每股至少 N 个 trailing K 线才算 (默认 30)")
    args = parser.parse_args()

    t0 = time.time()
    sqrt_year = math.sqrt(TRADING_DAYS_PER_YEAR)

    # 1. 加载全市场 K 线 (含已退市 — 无生存者偏差)
    mkt = duckdb.connect(str(MARKET_DB), read_only=True)
    end_date = args.end
    if end_date is None:
        end_date = mkt.execute(
            "SELECT MAX(date) FROM v_price_kline_qfq WHERE adjust='qfq' AND freq='daily'"
        ).fetchone()[0]
    log.info(f"=== Phase ψ.β.1 fact_risk_factors PIT backfill ===")
    log.info(f"  range: {args.start} → {end_date}")
    log.info(f"  min_bars: {args.min_bars}")

    # 计算前需要预 lookback 120 天 (用于 trailing 计算)
    from datetime import date, timedelta
    start_dt = date.fromisoformat(args.start)
    pre_start = (start_dt - timedelta(days=200)).isoformat()
    log.info(f"  lookback start (含 trailing 缓冲): {pre_start}")

    log.info("加载 K 线 (close + date, qfq daily) ...")
    t1 = time.time()
    mkt.execute(f"""
        CREATE OR REPLACE TEMP TABLE __k AS
        SELECT code AS stock_code, CAST(date AS VARCHAR) AS date,
               CAST(close AS DOUBLE) AS close
          FROM v_price_kline_qfq
         WHERE adjust='qfq' AND freq='daily'
           AND close IS NOT NULL AND close > 0
           AND date >= ? AND date <= ?
    """, [pre_start, end_date])
    n_kline = mkt.execute("SELECT COUNT(*) FROM __k").fetchone()[0]
    n_stocks = mkt.execute("SELECT COUNT(DISTINCT stock_code) FROM __k").fetchone()[0]
    log.info(f"  K 线: {n_kline:,} 行 / {n_stocks:,} 股 ({time.time()-t1:.1f}s)")

    # 2. 算 trailing metrics — 用 SQL 窗口函数 (一次性算全期 PIT)
    log.info("算 trailing 30/60/120 天 metrics (SQL 窗口) ...")
    t2 = time.time()

    # 用 ROWS BETWEEN N PRECEDING AND CURRENT ROW 算 trailing 窗口
    # (注: SQL 窗口 ROWS 计的是按 ORDER BY 排序后的行数, 不是日历天数, 但对 daily K 线一致)
    metrics_query = f"""
        WITH ret_series AS (
            SELECT stock_code, date, close,
                   CASE WHEN LAG(close) OVER (PARTITION BY stock_code ORDER BY date) > 0
                        THEN LN(close / LAG(close) OVER (PARTITION BY stock_code ORDER BY date))
                        ELSE NULL END AS ret
              FROM __k
        ),
        rolling AS (
            SELECT stock_code, date, close, ret,
                   -- 30 day window
                   STDDEV_SAMP(ret) OVER w30 * {sqrt_year} AS vol_30d,
                   AVG(ret)         OVER w30 * {TRADING_DAYS_PER_YEAR}
                       / NULLIF(STDDEV_SAMP(ret) OVER w30 * {sqrt_year}, 0) AS sharpe_30d,
                   -- 60 day window
                   STDDEV_SAMP(ret) OVER w60 * {sqrt_year} AS vol_60d,
                   AVG(ret)         OVER w60 * {TRADING_DAYS_PER_YEAR}
                       / NULLIF(STDDEV_SAMP(ret) OVER w60 * {sqrt_year}, 0) AS sharpe_60d,
                   SKEWNESS(ret)    OVER w60 AS skew_60d,
                   KURTOSIS(ret)    OVER w60 AS kurt_60d,
                   -- 120 day window
                   STDDEV_SAMP(ret) OVER w120 * {sqrt_year} AS vol_120d,
                   -- close[-30] / close[-120] 历史价格 (用 LAG)
                   LAG(close, 30)  OVER (PARTITION BY stock_code ORDER BY date) AS close_30d_ago,
                   LAG(close, 120) OVER (PARTITION BY stock_code ORDER BY date) AS close_120d_ago,
                   -- max_dd 60/120: trailing max - current close / trailing max
                   MAX(close) OVER w60  AS peak_60d,
                   MAX(close) OVER w120 AS peak_120d,
                   -- n_bars: 当前行之前累积的 K 线数 (含当日)
                   COUNT(*) OVER (PARTITION BY stock_code ORDER BY date
                                  ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS n_bars
              FROM ret_series
            WINDOW
              w30  AS (PARTITION BY stock_code ORDER BY date ROWS BETWEEN 29  PRECEDING AND CURRENT ROW),
              w60  AS (PARTITION BY stock_code ORDER BY date ROWS BETWEEN 59  PRECEDING AND CURRENT ROW),
              w120 AS (PARTITION BY stock_code ORDER BY date ROWS BETWEEN 119 PRECEDING AND CURRENT ROW)
        )
        SELECT stock_code, date AS calc_date,
               vol_30d, vol_60d, vol_120d,
               CASE WHEN peak_60d > 0  THEN (peak_60d  - close) / peak_60d  END AS max_dd_60d,
               CASE WHEN peak_120d > 0 THEN (peak_120d - close) / peak_120d END AS max_dd_120d,
               sharpe_30d, sharpe_60d,
               skew_60d, kurt_60d,
               CASE WHEN close_30d_ago  > 0 THEN close / close_30d_ago  - 1 END AS mom_30d,
               CASE WHEN close_120d_ago > 0 THEN close / close_120d_ago - 1 END AS mom_120d,
               n_bars
          FROM rolling
         WHERE date >= ? AND date <= ?
           AND n_bars >= ?
         ORDER BY stock_code, date
    """
    rows = mkt.execute(metrics_query, [args.start, end_date, args.min_bars]).fetchall()
    mkt.close()
    log.info(f"  metrics: {len(rows):,} 行 ({time.time()-t2:.1f}s)")

    # 3. 写库
    log.info("写 fact_risk_factors ...")
    t3 = time.time()

    # Phase ψ.γ.dict.2 — 字典 runtime enforce (Rule 5/6/7/9.5)
    INSERT_COLUMNS = [
        "stock_code", "calc_date",
        "vol_30d", "vol_60d", "vol_120d",
        "max_dd_60d", "max_dd_120d",
        "sharpe_30d", "sharpe_60d",
        "skew_60d", "kurt_60d",
        "mom_30d", "mom_120d",
        "n_bars",
    ]
    # skip_missing_table=True — 字典当前覆盖 vol_30d/vol_60d/vol_120d/sharpe_60d/mom_30d/mom_120d
    # (Phase ψ.β.1 字段子集); skew/kurt/max_dd_*/sharpe_30d/n_bars 字典未列, 不强校验.
    # 重点: pk/pit-key (stock_code/calc_date) + 已收录字段的 outlier_cap (vol_60d≤2.0, sharpe_60d∈[-10,10]).
    validate_rows_before_insert(
        rows, INSERT_COLUMNS, "fact_risk_factors",
        max_violation_rate=0.005,    # 5% 容忍 (历史数据 trailing 早期 NaN 等)
    )

    smart = duckdb.connect(str(SMART_DB))
    try:
        ensure_table(smart)
        # 增量替换 (相同 PK INSERT OR REPLACE)
        smart.execute("BEGIN TRANSACTION")
        try:
            # 先 DELETE 本次区间, 避免 INSERT OR REPLACE 太慢
            smart.execute(
                "DELETE FROM fact_risk_factors WHERE calc_date >= ? AND calc_date <= ?",
                [args.start, end_date]
            )
            smart.executemany(
                """INSERT INTO fact_risk_factors
                   (stock_code, calc_date, vol_30d, vol_60d, vol_120d,
                    max_dd_60d, max_dd_120d, sharpe_30d, sharpe_60d,
                    skew_60d, kurt_60d, mom_30d, mom_120d, n_bars)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                rows,
            )
            smart.execute("COMMIT")
        except BaseException:
            try: smart.execute("ROLLBACK")
            except Exception: pass
            raise

        log.info(f"  写入 {len(rows):,} 行 ({time.time()-t3:.1f}s)")

        # 4. 报告
        n_dates = smart.execute(
            "SELECT COUNT(DISTINCT calc_date) FROM fact_risk_factors WHERE calc_date BETWEEN ? AND ?",
            [args.start, end_date]
        ).fetchone()[0]
        n_stocks_out = smart.execute(
            "SELECT COUNT(DISTINCT stock_code) FROM fact_risk_factors WHERE calc_date BETWEEN ? AND ?",
            [args.start, end_date]
        ).fetchone()[0]
        log.info(f"=== 完成 — n_dates={n_dates} n_stocks={n_stocks_out:,} "
                 f"rows={len(rows):,} ({time.time()-t0:.0f}s) ===")
    finally:
        smart.close()


if __name__ == "__main__":
    main()
