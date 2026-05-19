"""Phase ψ.β.3 — fact_capital_flow_pit_daily PIT 资金流 alpha backfill.

⚠ Rule 9.4 数据失败先承认换方向: fact_institution_event 只 1 年 (2025-04 起),
   不能做 800 天 backfill. 改用可 PIT 的资金流数据:
   - fact_lhb_event (龙虎榜, 跨 2023-01 → 2026-04, 52K 行)
   - fact_executive_trade_event (高管增减持, 30+ 年, 68K 行)
   - fact_holder_count_period (股东户数, 季度 PIT)

⚠ PIT 严格: 每个 (stock, trade_date) 只用 trade_date 之前已发生 / 已公告的事件.

输出表 fact_capital_flow_pit_daily:
  PK: (stock_code, trade_date)
  字段: lhb_count_30d / lhb_net_buy_pct_30d / lhb_inst_buy_30d / exec_buy_60d /
        exec_sell_60d / exec_net_signal / holder_count_change_q_pct ...

usage:
  PYTHONPATH=backend python backend/scripts/backfill_capital_flow_pit.py
"""
from __future__ import annotations

import argparse
import logging
import time
from pathlib import Path

import duckdb


log = logging.getLogger("backfill_capital_flow_pit")
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s",
                    datefmt="%H:%M:%S")


MARKET_DB = Path(__file__).resolve().parents[2] / "data" / "market.duckdb"
SMART_DB  = Path(__file__).resolve().parents[2] / "data" / "smartmoney.duckdb"


DDL = """
CREATE TABLE IF NOT EXISTS fact_capital_flow_pit_daily (
    stock_code               TEXT NOT NULL,
    trade_date               TEXT NOT NULL,
    -- 龙虎榜 (近 30/90 日 trailing, trade_date PIT)
    lhb_count_30d            INTEGER,
    lhb_net_buy_pct_30d      DOUBLE,
    lhb_inst_buy_30d         INTEGER,
    lhb_count_90d            INTEGER,
    lhb_inst_buy_90d         INTEGER,
    -- 高管 (近 60 日 trailing, notice_date PIT)
    exec_buy_60d             INTEGER,
    exec_sell_60d            INTEGER,
    exec_buy_pct_60d         DOUBLE,
    exec_sell_pct_60d        DOUBLE,
    exec_net_signal          DOUBLE,
    -- 股东户数 (季度 PIT, source_available_date 严格)
    holder_count_change_q_pct DOUBLE,
    holder_count_q_report_date TEXT,
    -- 元
    built_at                 TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (stock_code, trade_date)
);
CREATE INDEX IF NOT EXISTS idx_cfpit_date ON fact_capital_flow_pit_daily(trade_date);
CREATE INDEX IF NOT EXISTS idx_cfpit_stock ON fact_capital_flow_pit_daily(stock_code);
"""


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2023-01-03")
    parser.add_argument("--end", default=None)
    args = parser.parse_args()

    t0 = time.time()
    # Codex review 2026-05-19 P1: end_date clamp 到 latest_completed_trade_date 防止盘中污染
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
    # clamp end_date to calendar last_closed (CLAUDE.md Rule 3 反例 defense)
    if end_date and str(end_date) > cal_max:
        log.warning(f"  end_date {end_date} > cal_max {cal_max}, clamped to {cal_max}")
        end_date = cal_max
    log.info(f"=== Phase ψ.β.3 fact_capital_flow_pit_daily backfill ===")
    log.info(f"  range: {args.start} → {end_date} (cal_max={cal_max})")

    # 1. 拉龙虎榜事件 (含 trade_date, net_buy_pct, is_inst_net_buy)
    log.info("加载 fact_lhb_event (龙虎榜) ...")
    mkt.execute("""
        CREATE OR REPLACE TEMP TABLE __lhb AS
        SELECT stock_code, trade_date,
               net_buy_pct,           -- 元 / 流通市值 比率 (%)
               is_inst_net_buy        -- 0/1: 机构席位是否净买
          FROM sm.fact_lhb_event
         WHERE trade_date >= '2022-10-01'   -- 缓冲: 提早 3 月给 trailing 90d
           AND trade_date <= ?
    """, [end_date])
    r = mkt.execute("SELECT COUNT(*), COUNT(DISTINCT stock_code) FROM __lhb").fetchone()
    log.info(f"  lhb: {r[0]:,} 行 / {r[1]:,} 股")

    # 2. 拉高管增减持 (notice_date format yyyymmdd? 让我看一下)
    log.info("加载 fact_executive_trade_event (高管) ...")
    # 看 notice_date 格式 (raw_gpcw_detail 是 yymmdd, executive 可能不同)
    sample = mkt.execute("SELECT notice_date FROM sm.fact_executive_trade_event LIMIT 1").fetchone()
    log.info(f"  notice_date sample: {sample}")
    # notice_date 是 'YYYY-MM-DD' 字符串吗? 看实测
    notice_format = "yyyy_mm_dd"  # 假设
    if sample and sample[0] and isinstance(sample[0], str) and "-" in sample[0]:
        notice_format = "yyyy_mm_dd"
    else:
        notice_format = "unknown"
    log.info(f"  notice_date 推断格式: {notice_format}")

    mkt.execute("""
        CREATE OR REPLACE TEMP TABLE __exec AS
        SELECT stock_code, notice_date,
               direction,
               COALESCE(total_change_pct_total, 0) AS change_pct
          FROM sm.fact_executive_trade_event
         WHERE notice_date >= '2022-10-01'
           AND notice_date <= ?
           AND direction IN ('buy', 'sell')
    """, [end_date])
    r = mkt.execute("SELECT COUNT(*) FROM __exec").fetchone()
    log.info(f"  exec: {r[0]:,} 行")

    # 3. 股东户数 PIT (季度, source_available_date 是可用日)
    log.info("加载 fact_holder_count_period (股东户数, 季度 PIT) ...")
    mkt.execute("""
        CREATE OR REPLACE TEMP TABLE __holder AS
        SELECT stock_code, report_date,
               -- 实际可用日: source_available_date (若有), 否则 report_date + 45 天默认
               COALESCE(source_available_date,
                        strftime(strptime(report_date, '%Y-%m-%d') + INTERVAL 45 DAY,
                                 '%Y-%m-%d')) AS available_date,
               holder_count_change_pct
          FROM sm.fact_holder_count_period
         WHERE report_date >= '2022-01-01'
           AND holder_count_change_pct IS NOT NULL
           -- Outlier 过滤: holder_count_change_pct 极端值 (脏数据) 排除
           --   合理范围 [-90, 90]% (季度增减 90% 已是极端值上限)
           AND ABS(holder_count_change_pct) <= 90
    """)
    r = mkt.execute("SELECT COUNT(*) FROM __holder").fetchone()
    log.info(f"  holder: {r[0]:,} 行")

    # 4. K 线 trade_dates
    log.info("加载 K 线 dates ...")
    mkt.execute(f"""
        CREATE OR REPLACE TEMP TABLE __dates AS
        SELECT DISTINCT CAST(date AS VARCHAR) AS trade_date, code AS stock_code
          FROM v_price_kline_qfq
         WHERE adjust='qfq' AND freq='daily'
           AND date >= ? AND date <= ? AND close > 0
    """, [args.start, end_date])
    r = mkt.execute("SELECT COUNT(*), COUNT(DISTINCT stock_code), COUNT(DISTINCT trade_date) FROM __dates").fetchone()
    log.info(f"  K 线: {r[0]:,} (stock, date) / {r[1]:,} 股 / {r[2]:,} 日")

    # 5. 一次性 SQL 算所有 PIT 因子 — 用 LATERAL 子查询 / ASOF
    log.info("算 PIT 资金流 alpha ...")
    t_alpha = time.time()
    # LHB trailing 窗口
    rows = mkt.execute("""
        WITH lhb_pit AS (
            SELECT d.stock_code, d.trade_date,
                   COUNT(*) FILTER (WHERE l.trade_date BETWEEN
                       strftime(strptime(d.trade_date, '%Y-%m-%d') - INTERVAL 30 DAY, '%Y-%m-%d')
                       AND d.trade_date) AS lhb_count_30d,
                   SUM(l.net_buy_pct) FILTER (WHERE l.trade_date BETWEEN
                       strftime(strptime(d.trade_date, '%Y-%m-%d') - INTERVAL 30 DAY, '%Y-%m-%d')
                       AND d.trade_date) AS lhb_net_buy_pct_30d,
                   SUM(CAST(l.is_inst_net_buy AS INT)) FILTER (WHERE l.trade_date BETWEEN
                       strftime(strptime(d.trade_date, '%Y-%m-%d') - INTERVAL 30 DAY, '%Y-%m-%d')
                       AND d.trade_date) AS lhb_inst_buy_30d,
                   COUNT(*) FILTER (WHERE l.trade_date BETWEEN
                       strftime(strptime(d.trade_date, '%Y-%m-%d') - INTERVAL 90 DAY, '%Y-%m-%d')
                       AND d.trade_date) AS lhb_count_90d,
                   SUM(CAST(l.is_inst_net_buy AS INT)) FILTER (WHERE l.trade_date BETWEEN
                       strftime(strptime(d.trade_date, '%Y-%m-%d') - INTERVAL 90 DAY, '%Y-%m-%d')
                       AND d.trade_date) AS lhb_inst_buy_90d
              FROM __dates d
              LEFT JOIN __lhb l
                ON l.stock_code = d.stock_code
               AND l.trade_date <= d.trade_date
               AND l.trade_date >= strftime(strptime(d.trade_date, '%Y-%m-%d') - INTERVAL 91 DAY, '%Y-%m-%d')
             GROUP BY d.stock_code, d.trade_date
        ),
        exec_pit AS (
            SELECT d.stock_code, d.trade_date,
                   COUNT(*) FILTER (WHERE e.direction='buy') AS exec_buy_60d,
                   COUNT(*) FILTER (WHERE e.direction='sell') AS exec_sell_60d,
                   COALESCE(SUM(e.change_pct) FILTER (WHERE e.direction='buy'), 0) AS exec_buy_pct_60d,
                   COALESCE(SUM(e.change_pct) FILTER (WHERE e.direction='sell'), 0) AS exec_sell_pct_60d
              FROM __dates d
              LEFT JOIN __exec e
                ON e.stock_code = d.stock_code
               AND e.notice_date <= d.trade_date
               AND e.notice_date >= strftime(strptime(d.trade_date, '%Y-%m-%d') - INTERVAL 60 DAY, '%Y-%m-%d')
             GROUP BY d.stock_code, d.trade_date
        ),
        holder_pit AS (
            SELECT d.stock_code, d.trade_date,
                   MAX(h.report_date) AS holder_count_q_report_date,
                   ANY_VALUE(h.holder_count_change_pct ORDER BY h.available_date DESC) AS holder_count_change_q_pct
              FROM __dates d
              LEFT JOIN __holder h
                ON h.stock_code = d.stock_code
               AND h.available_date <= d.trade_date
             GROUP BY d.stock_code, d.trade_date
        )
        SELECT l.stock_code, l.trade_date,
               COALESCE(l.lhb_count_30d, 0) AS lhb_count_30d,
               l.lhb_net_buy_pct_30d,
               COALESCE(l.lhb_inst_buy_30d, 0) AS lhb_inst_buy_30d,
               COALESCE(l.lhb_count_90d, 0) AS lhb_count_90d,
               COALESCE(l.lhb_inst_buy_90d, 0) AS lhb_inst_buy_90d,
               COALESCE(e.exec_buy_60d, 0) AS exec_buy_60d,
               COALESCE(e.exec_sell_60d, 0) AS exec_sell_60d,
               e.exec_buy_pct_60d, e.exec_sell_pct_60d,
               CASE WHEN COALESCE(e.exec_buy_60d, 0) + COALESCE(e.exec_sell_60d, 0) > 0
                    THEN (COALESCE(e.exec_buy_60d, 0) - COALESCE(e.exec_sell_60d, 0))::DOUBLE
                       / (COALESCE(e.exec_buy_60d, 0) + COALESCE(e.exec_sell_60d, 0))
                    ELSE NULL END AS exec_net_signal,
               h.holder_count_change_q_pct,
               h.holder_count_q_report_date
          FROM lhb_pit l
          LEFT JOIN exec_pit   e ON e.stock_code = l.stock_code AND e.trade_date = l.trade_date
          LEFT JOIN holder_pit h ON h.stock_code = l.stock_code AND h.trade_date = l.trade_date
         WHERE l.lhb_count_30d > 0 OR e.exec_buy_60d > 0 OR e.exec_sell_60d > 0
            OR h.holder_count_change_q_pct IS NOT NULL
    """).fetchall()
    mkt.close()
    log.info(f"  PIT 因子: {len(rows):,} 行 ({time.time()-t_alpha:.1f}s)")

    # 6. 写库
    t_write = time.time()
    smart = duckdb.connect(str(SMART_DB))
    try:
        for stmt in DDL.strip().split(";"):
            s = stmt.strip()
            if s: smart.execute(s)
        smart.execute("BEGIN TRANSACTION")
        try:
            smart.execute(
                "DELETE FROM fact_capital_flow_pit_daily WHERE trade_date >= ? AND trade_date <= ?",
                [args.start, end_date]
            )
            smart.executemany(
                """INSERT INTO fact_capital_flow_pit_daily
                   (stock_code, trade_date,
                    lhb_count_30d, lhb_net_buy_pct_30d, lhb_inst_buy_30d,
                    lhb_count_90d, lhb_inst_buy_90d,
                    exec_buy_60d, exec_sell_60d,
                    exec_buy_pct_60d, exec_sell_pct_60d, exec_net_signal,
                    holder_count_change_q_pct, holder_count_q_report_date)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                rows,
            )
            smart.execute("COMMIT")
        except BaseException:
            try: smart.execute("ROLLBACK")
            except Exception: pass
            raise
        log.info(f"  写入 {len(rows):,} 行 ({time.time()-t_write:.1f}s)")
        r = smart.execute("""SELECT COUNT(*), COUNT(DISTINCT stock_code), COUNT(DISTINCT trade_date),
                                    MIN(trade_date), MAX(trade_date)
                               FROM fact_capital_flow_pit_daily""").fetchone()
        log.info(f"=== 完成 — rows={r[0]:,} stocks={r[1]:,} dates={r[2]} "
                 f"range: {r[3]} → {r[4]} ({time.time()-t0:.0f}s) ===")
    finally:
        smart.close()


if __name__ == "__main__":
    main()
