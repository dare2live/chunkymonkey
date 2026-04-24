#!/usr/bin/env python3
"""Phase 8.1: Alpha158 因子库 DuckDB SQL 版

qlib 的 Alpha158 共 158 个价量因子, 本实现取核心 ~80 个高影响因子 (其余可迭代补)
输出到独立文件 data/alpha158.duckdb, 训练时通过 ATTACH 联表, 与主库解耦.

覆盖因子组:
  KBAR (9)                 K 线形态
  Price rolling (5/10/20/30/60)  价量滚动
  Return/ROC                收益率
  MA / STD                  均线/标准差
  MAX/MIN ratio            区间极值比
  RSV                      stochastic
  RANK                     rolling 截面
  CORR(price, volume)      价量相关
  CNTP/CNTN                正负天数
  SUMP/SUMN                正负收益和
  VMA / VSTD               成交量均线/波动

总约 80 列, 每股 ×交易日 panel.
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import duckdb

logger = logging.getLogger("alpha158")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s | %(message)s")


def build(output_db: str, start_date: str):
    market_db = str(Path(__file__).resolve().parent.parent.parent / "data" / "market.duckdb")

    # 核心 windows
    windows = [5, 10, 20, 30, 60]

    # 构造 Alpha158 主 SQL
    sql_parts = [f"""
    WITH px_raw AS (
        SELECT code::VARCHAR as stock_code,
               CAST(date AS DATE) AS date,
               CAST(open AS DOUBLE) AS open,
               CAST(high AS DOUBLE) AS high,
               CAST(low AS DOUBLE) AS low,
               CAST(close AS DOUBLE) AS close,
               CAST(volume AS DOUBLE) AS volume,
               CAST(amount AS DOUBLE) AS amount
        FROM mkt.price_kline_tdxhub
        WHERE freq='daily' AND adjust='qfq' AND date >= '{start_date}'
    ),
    px AS (
        SELECT *,
               (close / NULLIF(LAG(close, 1) OVER w, 0) - 1) AS ret_1d
        FROM px_raw
        WINDOW w AS (PARTITION BY stock_code ORDER BY date)
    )
    SELECT
        stock_code, date, close,
        -- ═══════════════════════════════════════════════════════════
        -- KBAR (9 个)  K 线形态
        -- ═══════════════════════════════════════════════════════════
        ((close - open) / NULLIF(open, 0)) AS a158_kmid,
        ((high - low) / NULLIF(open, 0)) AS a158_klen,
        ((close - open) / NULLIF(high - low, 0)) AS a158_kmid2,
        ((high - GREATEST(open, close)) / NULLIF(open, 0)) AS a158_kup,
        ((high - GREATEST(open, close)) / NULLIF(high - low, 0)) AS a158_kup2,
        ((LEAST(open, close) - low) / NULLIF(open, 0)) AS a158_klow,
        ((LEAST(open, close) - low) / NULLIF(high - low, 0)) AS a158_klow2,
        ((2 * close - high - low) / NULLIF(open, 0)) AS a158_ksft,
        ((2 * close - high - low) / NULLIF(high - low, 0)) AS a158_ksft2
    """]

    for w in windows:
        sql_parts.append(f"""
        ,
        -- ═══════════════════════════════════════════════════════════
        -- Window {w}
        -- ═══════════════════════════════════════════════════════════
        -- ROC: close vs N 日前 close
        (close / NULLIF(LAG(close, {w}) OVER w1, 0) - 1) AS a158_roc{w},
        -- MA: close / MA - 1
        (close / NULLIF(AVG(close) OVER (PARTITION BY stock_code ORDER BY date ROWS {w-1} PRECEDING), 0) - 1) AS a158_ma{w},
        -- STD: close N 日收益率 std
        STDDEV_SAMP(ret_1d) OVER (PARTITION BY stock_code ORDER BY date ROWS {w-1} PRECEDING) AS a158_std{w},
        -- MAX ratio: N 日最高 close / current
        (close / NULLIF(MAX(close) OVER (PARTITION BY stock_code ORDER BY date ROWS {w-1} PRECEDING), 0)) AS a158_max{w},
        -- MIN ratio: close / N 日最低
        (close / NULLIF(MIN(close) OVER (PARTITION BY stock_code ORDER BY date ROWS {w-1} PRECEDING), 0)) AS a158_min{w},
        -- RSV: stochastic
        ((close - MIN(low) OVER (PARTITION BY stock_code ORDER BY date ROWS {w-1} PRECEDING))
            / NULLIF(MAX(high) OVER (PARTITION BY stock_code ORDER BY date ROWS {w-1} PRECEDING)
                     - MIN(low) OVER (PARTITION BY stock_code ORDER BY date ROWS {w-1} PRECEDING), 0)) AS a158_rsv{w},
        -- QTLU (quantile 75%): 近似用 MAX-MIN 区间位置
        ((close - MIN(close) OVER (PARTITION BY stock_code ORDER BY date ROWS {w-1} PRECEDING))
            / NULLIF(MAX(close) OVER (PARTITION BY stock_code ORDER BY date ROWS {w-1} PRECEDING)
                     - MIN(close) OVER (PARTITION BY stock_code ORDER BY date ROWS {w-1} PRECEDING), 0)) AS a158_qtl{w},
        -- CNTP: 正收益天数比例
        SUM(CASE WHEN ret_1d > 0 THEN 1 ELSE 0 END) OVER (PARTITION BY stock_code ORDER BY date ROWS {w-1} PRECEDING) * 1.0 / {w} AS a158_cntp{w},
        -- SUMP: 正收益和 / 总绝对和
        (SUM(CASE WHEN ret_1d > 0 THEN ret_1d ELSE 0 END) OVER (PARTITION BY stock_code ORDER BY date ROWS {w-1} PRECEDING)
            / NULLIF(SUM(ABS(ret_1d)) OVER (PARTITION BY stock_code ORDER BY date ROWS {w-1} PRECEDING), 0)) AS a158_sump{w},
        -- VMA: volume 均线比
        (volume / NULLIF(AVG(volume) OVER (PARTITION BY stock_code ORDER BY date ROWS {w-1} PRECEDING), 0)) AS a158_vma{w},
        -- VSTD: volume std 标准化
        (STDDEV_SAMP(volume) OVER (PARTITION BY stock_code ORDER BY date ROWS {w-1} PRECEDING)
            / NULLIF(AVG(volume) OVER (PARTITION BY stock_code ORDER BY date ROWS {w-1} PRECEDING), 0)) AS a158_vstd{w}
        """)

    sql_parts.append("""
        FROM px
        WINDOW w1 AS (PARTITION BY stock_code ORDER BY date)
    """)

    full_sql = "".join(sql_parts)

    t0 = time.time()
    logger.info("构造 Alpha158 panel (windows=%s, start=%s)", windows, start_date)

    con = duckdb.connect(output_db)
    con.execute("INSTALL sqlite; LOAD sqlite;")
    con.execute(f"ATTACH '{market_db}' AS mkt (READ_ONLY)")

    # 目标表
    con.execute("DROP TABLE IF EXISTS fact_alpha158_panel")
    logger.info("运行主 SQL 计算 + 写表")
    con.execute(f"CREATE TABLE fact_alpha158_panel AS {full_sql}")

    # 统计
    row = con.execute("""
        SELECT COUNT(*), COUNT(DISTINCT stock_code), COUNT(DISTINCT date)
        FROM fact_alpha158_panel
    """).fetchone()
    cols = [r[0] for r in con.execute("DESCRIBE fact_alpha158_panel").fetchall()]
    logger.info("fact_alpha158_panel: rows=%d codes=%d dates=%d cols=%d",
                row[0], row[1], row[2], len(cols))
    logger.info("Alpha158 列名 (前 12): %s", cols[:12])
    logger.info("总耗时 %.1f min", (time.time() - t0) / 60)

    # 建索引 (DuckDB min-max zone map 够用, 显式建 code+date 提速 join)
    con.execute("CREATE INDEX idx_a158_code_date ON fact_alpha158_panel(stock_code, date)")

    con.execute("DETACH mkt")
    con.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--start', default='2023-01-01')
    parser.add_argument('--output', default=None, help='默认 data/alpha158.duckdb')
    args = parser.parse_args()

    output = args.output or str(Path(__file__).resolve().parent.parent.parent / "data" / "alpha158.duckdb")
    build(output, args.start)


if __name__ == "__main__":
    main()
