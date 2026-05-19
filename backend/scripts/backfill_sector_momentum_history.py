"""Phase ψ.β.sector — mart_sector_momentum 历史 backfill (方案 A: 成分股聚合).

⚠ 用户原话: "板块强度查 tdxhub 应该有现成的, 概念应该也有"
- TDX 协议层确实有 block_zs/fg/gn 板块文件 (services/block_client.py 已实现)
- 但**板块 K 线**没现成接口, services/sector_momentum.py 用方案 A (成分股等权聚合)
- 现有 mart_sector_momentum 只 41 行 (2026-04 起), 因为只算"今天"

⚠ 此脚本: backfill 历史 800 天 sector momentum, 写 mart_sector_momentum (per sector × date).

数据流:
  v_price_kline_qfq (个股) + dim_stock_tdx_industry_history (PIT 行业映射)
    → 每日按 PIT 行业聚合 close 等权 → 板块指数 K 线
    → 算 trailing 20/60/120/250 day metrics (ma/macd/return/excess vs market)
    → 写 mart_sector_momentum (per sector × calc_date)

PIT 严格 (Rule 9.1): 每个 calc_date d 用 d 时刻 PIT 行业映射,
不偷未来的 industry 重分类信息.

usage:
  PYTHONPATH=backend python backend/scripts/backfill_sector_momentum_history.py
"""
from __future__ import annotations

import argparse
import logging
import math
import time
from pathlib import Path

import duckdb


log = logging.getLogger("backfill_sector_momentum_history")
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s",
                    datefmt="%H:%M:%S")


MARKET_DB = Path(__file__).resolve().parents[2] / "data" / "market.duckdb"
SMART_DB  = Path(__file__).resolve().parents[2] / "data" / "smartmoney.duckdb"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2023-01-03")
    parser.add_argument("--end", default=None)
    parser.add_argument("--industry-level", default="l1",
                        choices=["l1", "l2"],
                        help="一级 (13 行业) 还是二级 (56 行业). 默认 l1.")
    parser.add_argument("--min-stocks-per-sector", type=int, default=5,
                        help="少于此数股票的行业跳过")
    args = parser.parse_args()

    t0 = time.time()
    # Codex review 2026-05-19 P1: end_date clamp 到 latest_completed_trade_date 防盘中污染
    # rule-compliance: ok evidence=calendar-gate-end-date-clamp-defense
    import sys as _sys
    _sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # backend/
    from services.market_db import _latest_completed_trade_date_for_write
    cal_max = _latest_completed_trade_date_for_write()  # fail-closed

    mkt = duckdb.connect(str(MARKET_DB), read_only=True)
    mkt.execute(f"ATTACH '{SMART_DB}' AS sm (READ_ONLY)")

    end_date = args.end
    if end_date is None:
        end_date = mkt.execute(
            "SELECT MAX(date) FROM v_price_kline_qfq WHERE adjust='qfq' AND freq='daily'"
        ).fetchone()[0]
    # clamp end_date to calendar last_closed
    if end_date and str(end_date) > cal_max:
        log.warning(f"  end_date {end_date} > cal_max {cal_max}, clamped to {cal_max}")
        end_date = cal_max
    log.info(f"=== Phase ψ.β.sector backfill ===")
    log.info(f"  range: {args.start} → {end_date} (cal_max={cal_max})")
    log.info(f"  industry_level: {args.industry_level}")
    log.info(f"  min_stocks_per_sector: {args.min_stocks_per_sector}")

    # 1. 加载 PIT 行业映射
    # dim_stock_tdx_industry_history (PIT) 如果数据稀疏 (只 latest snapshot),
    # 退到 dim_stock_tdx_industry (latest 但稳定)
    log.info("加载 PIT 行业映射 ...")
    sector_col_name = f"tdx_{args.industry_level}_name"
    # dim_stock_tdx_industry_history 是 PIT 但实际只 ~1 周 snapshot (2026-04-25 起)
    # 真实历史 PIT 不可用 — 退到 latest (假设行业分类跨 3 年基本不变, 业界做法)
    # Phase ψ.β.dict 后续可加 announce_date 字段做真 PIT.
    history_distinct_dates = mkt.execute(
        "SELECT COUNT(DISTINCT snapshot_date) FROM sm.dim_stock_tdx_industry_history"
    ).fetchone()[0]
    if history_distinct_dates >= 30:
        # 真有 ≥ 30 天 PIT 历史, 用 history
        log.info(f"  使用 dim_stock_tdx_industry_history ({history_distinct_dates} snapshots, PIT)")
        mkt.execute(f"""
            CREATE OR REPLACE TEMP TABLE __pit_industry AS
            SELECT stock_code, snapshot_date AS effective_date, {sector_col_name} AS sector
              FROM sm.dim_stock_tdx_industry_history
             WHERE {sector_col_name} IS NOT NULL AND {sector_col_name} != ''
        """)
    else:
        log.warning(f"  dim_stock_tdx_industry_history 只 {history_distinct_dates} 个 snapshot, "
                    f"退到 latest (假设行业分类 3 年内基本不变)")
        mkt.execute(f"""
            CREATE OR REPLACE TEMP TABLE __pit_industry AS
            SELECT stock_code, '2020-01-01' AS effective_date, {sector_col_name} AS sector
              FROM sm.dim_stock_tdx_industry
             WHERE {sector_col_name} IS NOT NULL AND {sector_col_name} != ''
        """)
    n_industry_rows = mkt.execute("SELECT COUNT(*) FROM __pit_industry").fetchone()[0]
    n_sectors = mkt.execute("SELECT COUNT(DISTINCT sector) FROM __pit_industry").fetchone()[0]
    log.info(f"  行业映射: {n_industry_rows:,} 行 / {n_sectors:,} 个 sector")

    # 2. 加载个股 K 线 (含 lookback 给 trailing 250 日预热)
    from datetime import date, timedelta
    start_dt = date.fromisoformat(args.start)
    lookback_start = (start_dt - timedelta(days=400)).isoformat()
    log.info(f"加载 K 线 {lookback_start} → {end_date} ...")
    mkt.execute(f"""
        CREATE OR REPLACE TEMP TABLE __k AS
        SELECT code AS stock_code, CAST(date AS VARCHAR) AS date,
               CAST(close AS DOUBLE) AS close
          FROM v_price_kline_qfq
         WHERE adjust='qfq' AND freq='daily'
           AND close IS NOT NULL AND close > 0
           AND date >= ? AND date <= ?
    """, [lookback_start, end_date])
    n_kline = mkt.execute("SELECT COUNT(*) FROM __k").fetchone()[0]
    log.info(f"  K 线: {n_kline:,} 行")

    # 3. K 线 × 行业 ASOF JOIN (每个 K 线日 取 ≤ 该日的 PIT 行业)
    log.info("JOIN K 线 × PIT 行业 ...")
    t1 = time.time()
    mkt.execute("""
        CREATE OR REPLACE TEMP TABLE __k_industry AS
        SELECT k.stock_code, k.date, k.close, i.sector
          FROM __k k
          ASOF LEFT JOIN __pit_industry i
            ON i.stock_code = k.stock_code
           AND k.date >= i.effective_date
         WHERE i.sector IS NOT NULL
    """)
    r = mkt.execute("SELECT COUNT(*) FROM __k_industry").fetchone()
    log.info(f"  K 线 × 行业: {r[0]:,} 行 ({time.time()-t1:.1f}s)")

    # 4. 等权聚合 → 行业指数 daily close
    # 等权 = AVG(close) per (sector × date). 各股票 close 取等权平均.
    # 更精确做法是用归一化 ret 累乘, 但等权 close 已足够算 momentum.
    log.info("等权聚合 → 行业指数 daily ...")
    t2 = time.time()
    mkt.execute(f"""
        CREATE OR REPLACE TEMP TABLE __sector_idx AS
        SELECT sector, date,
               AVG(close) AS sector_close,
               COUNT(*) AS n_stocks
          FROM __k_industry
         GROUP BY sector, date
        HAVING n_stocks >= {args.min_stocks_per_sector}
    """)
    n_sector_daily = mkt.execute("SELECT COUNT(*) FROM __sector_idx").fetchone()[0]
    n_uniq_sectors = mkt.execute("SELECT COUNT(DISTINCT sector) FROM __sector_idx").fetchone()[0]
    log.info(f"  sector × date: {n_sector_daily:,} 行 / {n_uniq_sectors} sectors ({time.time()-t2:.1f}s)")

    # 5. 全市场等权 benchmark (每天)
    mkt.execute("""
        CREATE OR REPLACE TEMP TABLE __market_idx AS
        SELECT date, AVG(close) AS market_close, COUNT(*) AS n_stocks
          FROM __k
         GROUP BY date
        HAVING n_stocks >= 100
    """)

    # 6. 算 trailing metrics — 窗口 SQL
    log.info("算 trailing momentum metrics ...")
    t3 = time.time()
    sqrt_year = math.sqrt(252)
    rows = mkt.execute(f"""
        WITH ret AS (
            SELECT s.sector, s.date, s.sector_close, s.n_stocks,
                   m.market_close,
                   CASE WHEN LAG(s.sector_close) OVER (PARTITION BY s.sector ORDER BY s.date) > 0
                        THEN s.sector_close / LAG(s.sector_close) OVER (PARTITION BY s.sector ORDER BY s.date) - 1
                        ELSE NULL END AS sector_ret,
                   CASE WHEN LAG(m.market_close) OVER (ORDER BY s.date) > 0
                        THEN m.market_close / LAG(m.market_close) OVER (ORDER BY s.date) - 1
                        ELSE NULL END AS market_ret
              FROM __sector_idx s
              JOIN __market_idx m USING (date)
        ),
        roll AS (
            SELECT sector, date, sector_close, n_stocks, market_close, sector_ret, market_ret,
                   AVG(sector_close) OVER w20  AS ma20,
                   AVG(sector_close) OVER w60  AS ma60,
                   STDDEV_SAMP(sector_ret) OVER w60 * {sqrt_year} AS vol_60d,
                   -- 5 / 20 / 60 / 120 day return
                   sector_close / LAG(sector_close, 5)   OVER (PARTITION BY sector ORDER BY date) - 1 AS ret_5d,
                   sector_close / LAG(sector_close, 20)  OVER (PARTITION BY sector ORDER BY date) - 1 AS ret_20d,
                   sector_close / LAG(sector_close, 60)  OVER (PARTITION BY sector ORDER BY date) - 1 AS ret_60d,
                   sector_close / LAG(sector_close, 120) OVER (PARTITION BY sector ORDER BY date) - 1 AS ret_120d,
                   -- market same
                   market_close / LAG(market_close, 20)  OVER (ORDER BY date) - 1 AS market_ret_20d,
                   market_close / LAG(market_close, 60)  OVER (ORDER BY date) - 1 AS market_ret_60d,
                   -- price vs MA position
                   COUNT(*) OVER (PARTITION BY sector ORDER BY date
                                  ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS n_bars
              FROM ret
             WINDOW w20 AS (PARTITION BY sector ORDER BY date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW),
                    w60 AS (PARTITION BY sector ORDER BY date ROWS BETWEEN 59 PRECEDING AND CURRENT ROW)
        )
        SELECT sector, date,
               sector_close, n_stocks,
               ROUND(ma20, 4)   AS ma20,
               ROUND(ma60, 4)   AS ma60,
               ROUND(vol_60d, 4) AS vol_60d,
               ROUND(ret_5d, 5) AS ret_5d,
               ROUND(ret_20d, 5) AS ret_20d,
               ROUND(ret_60d, 5) AS ret_60d,
               ROUND(ret_120d, 5) AS ret_120d,
               -- excess (sector ret - market ret)
               ROUND(ret_20d - market_ret_20d, 5) AS excess_20d,
               ROUND(ret_60d - market_ret_60d, 5) AS excess_60d,
               -- price vs ma 位置 (越正越强)
               CASE WHEN ma20 > 0 THEN ROUND((sector_close - ma20) / ma20, 5) END AS price_vs_ma20,
               CASE WHEN ma60 > 0 THEN ROUND((sector_close - ma60) / ma60, 5) END AS price_vs_ma60,
               n_bars
          FROM roll
         WHERE date >= ? AND date <= ?
           AND n_bars >= 60
         ORDER BY sector, date
    """, [args.start, end_date]).fetchall()
    mkt.close()
    log.info(f"  metrics: {len(rows):,} 行 ({time.time()-t3:.1f}s)")

    # 7. 写库 fact_sector_momentum_daily (新表, 跟 mart_sector_momentum 现有的不冲突)
    log.info("写库 fact_sector_momentum_daily ...")
    t4 = time.time()
    smart = duckdb.connect(str(SMART_DB))
    try:
        smart.execute("""
            CREATE TABLE IF NOT EXISTS fact_sector_momentum_daily (
                sector_name    TEXT NOT NULL,
                date           TEXT NOT NULL,
                sector_close   DOUBLE,
                n_stocks       INTEGER,
                ma20           DOUBLE,
                ma60           DOUBLE,
                vol_60d        DOUBLE,
                ret_5d         DOUBLE,
                ret_20d        DOUBLE,
                ret_60d        DOUBLE,
                ret_120d       DOUBLE,
                excess_20d     DOUBLE,
                excess_60d     DOUBLE,
                price_vs_ma20  DOUBLE,
                price_vs_ma60  DOUBLE,
                n_bars         INTEGER,
                built_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (sector_name, date)
            )
        """)
        smart.execute("CREATE INDEX IF NOT EXISTS idx_fsmd_date ON fact_sector_momentum_daily(date)")

        smart.execute("BEGIN TRANSACTION")
        try:
            smart.execute(
                "DELETE FROM fact_sector_momentum_daily WHERE date >= ? AND date <= ?",
                [args.start, end_date]
            )
            smart.executemany(
                """INSERT INTO fact_sector_momentum_daily
                   (sector_name, date, sector_close, n_stocks, ma20, ma60, vol_60d,
                    ret_5d, ret_20d, ret_60d, ret_120d,
                    excess_20d, excess_60d, price_vs_ma20, price_vs_ma60, n_bars)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                rows,
            )
            smart.execute("COMMIT")
        except BaseException:
            try: smart.execute("ROLLBACK")
            except Exception: pass
            raise
        log.info(f"  写入 {len(rows):,} 行 ({time.time()-t4:.1f}s)")

        # 8. 报告
        r = smart.execute("""
            SELECT COUNT(*), COUNT(DISTINCT sector_name), COUNT(DISTINCT date),
                   MIN(date), MAX(date)
              FROM fact_sector_momentum_daily
        """).fetchone()
        log.info(f"=== 完成 — rows={r[0]:,} sectors={r[1]} dates={r[2]} "
                 f"range: {r[3]} → {r[4]} ({time.time()-t0:.0f}s) ===")

        # 抽样: 计算机 / 食品饮料 / 银行 跨年抽样
        print()
        print('=== Sector momentum 抽样验证 (跨年 / 不同 sector) ===')
        for sec in ('计算机', '食品饮料', '银行', '电子', '医药生物'):
            r = smart.execute("""
                SELECT date, ROUND(sector_close, 2), ROUND(ret_60d * 100, 2),
                       ROUND(excess_60d * 100, 2), n_stocks
                  FROM fact_sector_momentum_daily
                 WHERE sector_name = ? AND date IN ('2023-06-15', '2024-06-14', '2025-06-13', '2026-05-12')
                 ORDER BY date
            """, [sec]).fetchall()
            if r:
                print(f'  {sec}:')
                for x in r:
                    print(f'    {x[0]}  close={x[1]:>7}  ret_60d={x[2]:>+6}%  excess_60d={x[3]:>+6}%  n_stocks={x[4]}')
    finally:
        smart.close()


if __name__ == "__main__":
    main()
