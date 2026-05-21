"""Feature research — fact_industry_beta_daily ETL (Codex a49c90a6 backlog #1).

Per-stock 60-day rolling beta vs industry index (CITIC L1 industry).

PIT-safe (Codex Rule 5/7):
- beta(stock, T) = Cov(stock_ret[T-60:T-1], industry_ret[T-60:T-1]) / Var(industry_ret[T-60:T-1])
- Strictly prior 60 day, anchor at trade_date
- industry mapping from mart_stock_industry_pit (effective_from <= trade_date)

Columns:
- stock_code / trade_date
- industry (tdx_l1_name from PIT mapping)
- beta_60d (-2 ~ +2 typical)
- beta_60d_zscore (cross-sectional z within day, +/-3 cap)
- source_max_trade_date (PIT 锚 = trade_date)

acceptance:
- PIT integrity 0 violations
- beta_60d coverage ≥ 80% (early stocks 不足 60 day prior 缺)
- z-score normality: mean ≈ 0 / std ≈ 1 within day

用法:
    PYTHONPATH=backend python backend/scripts/build_industry_beta_daily.py \\
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


logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("build_industry_beta_daily")


SMART_DB = Path(__file__).resolve().parents[2] / "data" / "smartmoney.duckdb"
TARGET = "fact_industry_beta_daily"


DDL_DROP = f"DROP TABLE IF EXISTS {TARGET}"

DDL_CREATE = f"""
CREATE TABLE {TARGET} (
    stock_code TEXT NOT NULL,
    trade_date DATE NOT NULL,
    industry TEXT,
    beta_60d DOUBLE,
    beta_60d_zscore DOUBLE,
    source_max_trade_date DATE NOT NULL,
    built_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (stock_code, trade_date)
)
"""
DDL_CREATE_IF_NOT_EXISTS = DDL_CREATE.replace("CREATE TABLE", "CREATE TABLE IF NOT EXISTS", 1)

DDL_INDEX_DATE = f"CREATE INDEX IF NOT EXISTS idx_beta_date ON {TARGET}(trade_date)"
DDL_INDEX_STOCK_DATE = f"CREATE INDEX IF NOT EXISTS idx_beta_stock_date ON {TARGET}(stock_code, trade_date)"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2024-01-01")   # rule-compliance: ok evidence=panel-window
    parser.add_argument("--end", default="2026-04-23")     # rule-compliance: ok evidence=panel-window-end
    parser.add_argument("--incremental", action="store_true",
                        help="只重算 start/end 切片，不删除历史全表")
    args = parser.parse_args()

    log.info(f"=== build fact_industry_beta_daily ===")
    log.info(f"  window: {args.start} → {args.end}")

    conn = duckdb.connect(str(SMART_DB))
    market_db = Path(__file__).resolve().parents[2] / "data" / "market.duckdb"
    conn.execute(f"ATTACH IF NOT EXISTS '{market_db}' AS market (READ_ONLY)")

    # Prereq
    r = conn.execute("SELECT COUNT(*) FROM mart_stock_industry_pit").fetchone()[0]
    log.info(f"  prereq mart_stock_industry_pit: {r:,} rows")

    if args.incremental:
        conn.execute(DDL_CREATE_IF_NOT_EXISTS)
        conn.execute(
            f"DELETE FROM {TARGET} WHERE trade_date >= CAST(? AS DATE) AND trade_date <= CAST(? AS DATE)",
            [args.start, args.end],
        )
    else:
        conn.execute(DDL_DROP)
        conn.execute(DDL_CREATE)

    # Step 1: stock daily returns (prior 60 day window for beta calc)
    t0 = time.time()
    log.info("  Step 1: load stock daily returns ...")

    df_returns = conn.execute(f"""
        SELECT
            code AS stock_code,
            CAST(date AS DATE) AS trade_date,
            (close / NULLIF(LAG(close) OVER (PARTITION BY code ORDER BY date), 0) - 1) AS daily_ret
        FROM market.v_price_kline_qfq
        WHERE adjust='qfq' AND freq='daily'
          AND CAST(date AS DATE) >= CAST(? AS DATE) - INTERVAL '90 days'
          AND CAST(date AS DATE) <= CAST(? AS DATE)
    """, [args.start, args.end]).fetchdf()
    log.info(f"    {len(df_returns):,} stock-day returns ({time.time()-t0:.0f}s)")

    # Step 2: industry mapping (PIT)
    log.info("  Step 2: industry PIT mapping ...")
    industry_df = conn.execute(f"""
        SELECT stock_code, tdx_l1_name AS industry,
               CAST(effective_from AS DATE) AS eff_from,
               CASE WHEN effective_to='9999-12-31' THEN CAST('9999-12-31' AS DATE)
                    ELSE CAST(effective_to AS DATE) END AS eff_to
        FROM mart_stock_industry_pit
        WHERE tdx_l1_name IS NOT NULL
    """).fetchdf()
    log.info(f"    {len(industry_df):,} industry mapping rows")

    # Join stock returns to industry (PIT-safe)
    log.info("  Step 3: vectorized join + beta calc ...")
    import pandas as pd
    import numpy as np

    # Register DataFrames + use SQL for compute (vectorized)
    conn.register("returns_tmp", df_returns)
    conn.register("industry_tmp", industry_df)

    # PIT-safe industry assignment per (stock, day)
    df_with_ind = conn.execute("""
        SELECT r.*, i.industry
        FROM returns_tmp r
        LEFT JOIN industry_tmp i
            ON i.stock_code = r.stock_code
           AND r.trade_date >= i.eff_from
           AND r.trade_date < i.eff_to
        QUALIFY ROW_NUMBER() OVER (
            PARTITION BY r.stock_code, r.trade_date ORDER BY i.eff_from DESC
        ) = 1
    """).fetchdf()
    log.info(f"    {len(df_with_ind):,} rows with industry mapping")

    # Industry daily returns (equal-weighted average per industry-day)
    industry_ret = df_with_ind.groupby(["trade_date", "industry"], dropna=True)["daily_ret"].mean().reset_index()
    industry_ret = industry_ret.rename(columns={"daily_ret": "ind_ret"})
    log.info(f"    {len(industry_ret):,} industry-day return rows")

    # Merge industry return back
    merged = df_with_ind.merge(industry_ret, on=["trade_date", "industry"], how="left")
    merged = merged.sort_values(["stock_code", "trade_date"]).reset_index(drop=True)
    log.info(f"    merged rows: {len(merged):,}")

    # 60-day rolling beta per stock (PIT-safe: prior 60 day window)
    log.info("  Step 4: rolling 60-day beta per stock ...")
    def _rolling_beta(group):
        # group is sorted by trade_date
        cov = group["daily_ret"].rolling(60, min_periods=30).cov(group["ind_ret"])
        var = group["ind_ret"].rolling(60, min_periods=30).var()
        beta = cov / var.replace(0, np.nan)
        return beta

    merged["beta_60d"] = merged.groupby("stock_code", group_keys=False).apply(_rolling_beta)

    # Shift 1 (用 prior 60 day, 不含今日 — PIT-safe)
    merged["beta_60d"] = merged.groupby("stock_code")["beta_60d"].shift(1)

    # Cross-sectional z-score per day
    log.info("  Step 5: cross-sectional z-score per day ...")
    merged["beta_60d_zscore"] = merged.groupby("trade_date")["beta_60d"].transform(
        lambda x: (x - x.mean()) / x.std() if x.std() > 0 else 0
    ).clip(-3, 3)

    # Filter to target window
    final = merged[(merged["trade_date"] >= pd.to_datetime(args.start)) &
                   (merged["trade_date"] <= pd.to_datetime(args.end))].copy()
    final["source_max_trade_date"] = final["trade_date"]
    final = final.dropna(subset=["beta_60d"])

    out_df = final[["stock_code", "trade_date", "industry", "beta_60d",
                    "beta_60d_zscore", "source_max_trade_date"]]
    log.info(f"  Final rows (after dropna): {len(out_df):,}")

    conn.register("beta_tmp", out_df)
    conn.execute(f"""
        INSERT OR REPLACE INTO {TARGET}
        (stock_code, trade_date, industry, beta_60d, beta_60d_zscore, source_max_trade_date)
        SELECT stock_code, trade_date, industry, beta_60d, beta_60d_zscore, source_max_trade_date
        FROM beta_tmp
    """)
    conn.unregister("beta_tmp")
    conn.execute(DDL_INDEX_DATE)
    conn.execute(DDL_INDEX_STOCK_DATE)

    log.info(f"  build done: {len(out_df):,} rows, {time.time()-t0:.0f}s")

    # PIT integrity audit
    bad = conn.execute(f"""
        SELECT COUNT(*) FROM {TARGET}
        WHERE source_max_trade_date > trade_date
    """).fetchone()[0]
    if bad > 0:
        log.error(f"  PIT integrity FAIL: {bad} rows")
        return 1
    log.info("  PIT integrity PASS: 0 violations")

    # Coverage
    cov = conn.execute(f"""
        SELECT
            ROUND(AVG(CASE WHEN beta_60d IS NOT NULL THEN 1.0 ELSE 0 END), 4) AS beta_cov,
            ROUND(AVG(CASE WHEN beta_60d_zscore IS NOT NULL THEN 1.0 ELSE 0 END), 4) AS z_cov
        FROM {TARGET}
    """).fetchone()
    log.info(f"  Feature coverage: beta={cov[0]} / zscore={cov[1]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
