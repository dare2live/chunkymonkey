"""Phase ψ.β.2 — fact_financial_pit_daily PIT 财务/估值/质量 alpha backfill.

⚠ Rule 9.1 真金白银: aif10 估值表无 PIT 时序 (audit 已证), 改用
  raw_gpcw_detail (含 report_announce_date 公告日) + fact_financial_derived (已计算 yoy
  等 derived 字段) + v_price_kline_qfq (close).

⚠ PIT 严格: paper_sim 在 t 查 (stock, t) 行 → JOIN announce_date <= t.
  避免财报"报告期已结束但还没公告"的 leakage (Q1 报告 03-31, 实际 4 月底才公告).

输出表 fact_financial_pit_daily:
  PK: (stock_code, trade_date)
  字段:
    - report_date / announce_date  PIT key
    - 估值: pe_ttm / pb / ps_ttm
    - 质量: roe_q / revenue_yoy / profit_yoy / gross_margin / net_margin / ocf_to_profit / debt_ratio
    - 持股结构: inst_holding_pct / qfii_pct / fund_pct

usage:
  PYTHONPATH=backend python backend/scripts/backfill_financial_pit.py
"""
from __future__ import annotations

import argparse
import logging
import time
from pathlib import Path

import duckdb


log = logging.getLogger("backfill_financial_pit")
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s",
                    datefmt="%H:%M:%S")


MARKET_DB = Path(__file__).resolve().parents[2] / "data" / "market.duckdb"
SMART_DB  = Path(__file__).resolve().parents[2] / "data" / "smartmoney.duckdb"


DDL = """
CREATE TABLE IF NOT EXISTS fact_financial_pit_daily (
    stock_code        TEXT NOT NULL,
    trade_date        TEXT NOT NULL,
    -- PIT references
    report_date       TEXT,            -- 引用的财报报告期 (例 2024-03-31)
    announce_date     TEXT,            -- 公告日 PIT key (例 2024-04-25)
    -- 估值 (close × shares / TTM 指标)
    pe_ttm            DOUBLE,
    pb                DOUBLE,
    ps_ttm            DOUBLE,
    -- 质量 (来自 fact_financial_derived 已计算 + raw_gpcw_detail)
    roe_q             DOUBLE,          -- 单季 ROE (raw_gpcw_detail.roe)
    revenue_yoy       DOUBLE,
    profit_yoy        DOUBLE,
    gross_margin      DOUBLE,
    net_margin        DOUBLE,
    ocf_to_profit     DOUBLE,
    debt_ratio        DOUBLE,
    -- 持股结构 (机构集中度)
    inst_holding_pct  DOUBLE,
    qfii_pct          DOUBLE,
    fund_pct          DOUBLE,
    -- 元
    built_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (stock_code, trade_date)
);
CREATE INDEX IF NOT EXISTS idx_fpit_date  ON fact_financial_pit_daily(trade_date);
CREATE INDEX IF NOT EXISTS idx_fpit_stock ON fact_financial_pit_daily(stock_code);
"""


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2023-01-03")
    parser.add_argument("--end", default=None,
                        help="默认 K 线 max(date)")
    args = parser.parse_args()

    t0 = time.time()

    # 1. 准备 report_with_announce CTE (在 smart 上下文)
    mkt = duckdb.connect(str(MARKET_DB), read_only=True)
    mkt.execute(f"ATTACH '{SMART_DB}' AS sm (READ_ONLY)")
    end_date = args.end
    if end_date is None:
        end_date = mkt.execute(
            "SELECT MAX(date) FROM v_price_kline_qfq WHERE adjust='qfq' AND freq='daily'"
        ).fetchone()[0]
    log.info(f"=== Phase ψ.β.2 fact_financial_pit_daily backfill ===")
    log.info(f"  range: {args.start} → {end_date}")

    # 2. 把 raw_gpcw_detail + fact_financial_derived JOIN 后 拉到 market 的 temp 表
    #    (announce_date 转 'YYYY-MM-DD' 格式 与 K 线 date 字符串一致, 便于 ASOF)
    log.info("加载 报告 × 公告日 (raw_gpcw_detail + fact_financial_derived) ...")
    t1 = time.time()
    mkt.execute("""
        CREATE OR REPLACE TEMP TABLE __report AS
        WITH src AS (
            SELECT r.stock_code, r.report_date,
                   -- yymmdd (例 260425.0) → '2026-04-25'
                   '20' || LPAD(CAST(CAST(r.report_announce_date AS INTEGER) AS VARCHAR), 6, '0')
                   AS announce_yyyymmdd,
                   r.net_profit_ttm_wan, r.revenue_ttm_wan, r.ocf_ttm,
                   r.total_shares, r.nav_per_share,
                   r.roe AS roe_q,
                   r.inst_total_shares, r.qfii_shares, r.fund_shares,
                   r.holder_count,
                   d.revenue_yoy, d.profit_yoy,
                   d.gross_margin, d.net_margin,
                   d.ocf_to_profit, d.debt_ratio
              FROM sm.raw_gpcw_detail r
              LEFT JOIN sm.fact_financial_derived d
                ON d.stock_code = r.stock_code AND d.report_date = r.report_date
             WHERE r.report_announce_date IS NOT NULL
               AND r.total_shares > 0
        )
        SELECT stock_code, report_date,
               -- '20260425' → '2026-04-25'
               SUBSTR(announce_yyyymmdd, 1, 4) || '-'
               || SUBSTR(announce_yyyymmdd, 5, 2) || '-'
               || SUBSTR(announce_yyyymmdd, 7, 2) AS announce_date,
               net_profit_ttm_wan, revenue_ttm_wan, ocf_ttm,
               total_shares, nav_per_share, roe_q,
               inst_total_shares, qfii_shares, fund_shares, holder_count,
               revenue_yoy, profit_yoy, gross_margin, net_margin,
               ocf_to_profit, debt_ratio
          FROM src
         ORDER BY stock_code, announce_date
    """)
    n_reports = mkt.execute("SELECT COUNT(*) FROM __report").fetchone()[0]
    n_stocks_r = mkt.execute("SELECT COUNT(DISTINCT stock_code) FROM __report").fetchone()[0]
    log.info(f"  reports: {n_reports:,} 行 / {n_stocks_r:,} 股 ({time.time()-t1:.1f}s)")

    # 3. ASOF JOIN: 每股每日 拿最近 announce_date ≤ trade_date 的 report
    #    (DuckDB ASOF JOIN 支持: a >= b 的最大 b)
    log.info("ASOF JOIN K 线 × report (PIT) ...")
    t2 = time.time()
    mkt.execute(f"""
        CREATE OR REPLACE TEMP TABLE __pit AS
        SELECT k.code AS stock_code, CAST(k.date AS VARCHAR) AS trade_date, k.close,
               r.report_date, r.announce_date,
               r.net_profit_ttm_wan, r.revenue_ttm_wan, r.ocf_ttm,
               r.total_shares, r.nav_per_share, r.roe_q,
               r.inst_total_shares, r.qfii_shares, r.fund_shares,
               r.revenue_yoy, r.profit_yoy, r.gross_margin, r.net_margin,
               r.ocf_to_profit, r.debt_ratio
          FROM v_price_kline_qfq k
          ASOF LEFT JOIN __report r
            ON k.code = r.stock_code
           AND CAST(k.date AS VARCHAR) >= r.announce_date
         WHERE k.adjust='qfq' AND k.freq='daily'
           AND k.date >= ? AND k.date <= ?
           AND k.close > 0
    """, [args.start, end_date])
    n_pit = mkt.execute("SELECT COUNT(*) FROM __pit WHERE report_date IS NOT NULL").fetchone()[0]
    n_total = mkt.execute("SELECT COUNT(*) FROM __pit").fetchone()[0]
    log.info(f"  pit rows: {n_total:,}  with report: {n_pit:,} "
             f"(剔 {n_total-n_pit:,} 没历史财报) ({time.time()-t2:.1f}s)")

    # 4. 计算 PIT 因子 (PE/PB/PS + 质量 + 持股 pct), 写 fact_financial_pit_daily
    log.info("计算 PIT 因子 + 写库 ...")
    t3 = time.time()

    # 注: pe_ttm 用 close * total_shares / (net_profit_ttm_wan * 10000)
    #     wan = 万元 → 元的倍数. close in 元, total_shares in 股
    # pb = close / nav_per_share (nav_per_share 已经是 per-share book value)
    # ps_ttm 同 pe
    rows = mkt.execute("""
        SELECT stock_code, trade_date, report_date, announce_date,
               CASE WHEN net_profit_ttm_wan > 0 AND total_shares > 0
                    THEN (close * total_shares) / (net_profit_ttm_wan * 10000.0) END AS pe_ttm,
               CASE WHEN nav_per_share > 0 THEN close / nav_per_share END AS pb,
               CASE WHEN revenue_ttm_wan > 0 AND total_shares > 0
                    THEN (close * total_shares) / (revenue_ttm_wan * 10000.0) END AS ps_ttm,
               roe_q, revenue_yoy, profit_yoy, gross_margin, net_margin,
               ocf_to_profit, debt_ratio,
               CASE WHEN total_shares > 0 THEN inst_total_shares / total_shares END AS inst_pct,
               CASE WHEN total_shares > 0 THEN qfii_shares / total_shares END AS qfii_pct,
               CASE WHEN total_shares > 0 THEN fund_shares / total_shares END AS fund_pct
          FROM __pit
         WHERE report_date IS NOT NULL
    """).fetchall()
    mkt.close()
    log.info(f"  计算完成: {len(rows):,} 行 ({time.time()-t3:.1f}s)")

    # 写库
    smart = duckdb.connect(str(SMART_DB))
    try:
        for stmt in DDL.strip().split(";"):
            s = stmt.strip()
            if s:
                smart.execute(s)
        smart.execute("BEGIN TRANSACTION")
        try:
            smart.execute(
                "DELETE FROM fact_financial_pit_daily WHERE trade_date >= ? AND trade_date <= ?",
                [args.start, end_date]
            )
            smart.executemany(
                """INSERT INTO fact_financial_pit_daily
                   (stock_code, trade_date, report_date, announce_date,
                    pe_ttm, pb, ps_ttm,
                    roe_q, revenue_yoy, profit_yoy, gross_margin, net_margin,
                    ocf_to_profit, debt_ratio,
                    inst_holding_pct, qfii_pct, fund_pct)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                rows,
            )
            smart.execute("COMMIT")
        except BaseException:
            try: smart.execute("ROLLBACK")
            except Exception: pass
            raise

        log.info(f"  写入 {len(rows):,} 行 ({time.time()-t3:.1f}s 含计算)")

        # 5. 报告
        r = smart.execute("""
            SELECT COUNT(*), COUNT(DISTINCT stock_code), COUNT(DISTINCT trade_date),
                   MIN(trade_date), MAX(trade_date)
              FROM fact_financial_pit_daily
        """).fetchone()
        log.info(f"=== 完成 — rows={r[0]:,} stocks={r[1]:,} dates={r[2]} "
                 f"range: {r[3]} → {r[4]} ({time.time()-t0:.0f}s) ===")

        # 抽样验证
        sample = smart.execute("""
            SELECT stock_code, trade_date, report_date, announce_date,
                   ROUND(pe_ttm, 2) AS pe, ROUND(pb, 2) AS pb, ROUND(ps_ttm, 2) AS ps,
                   ROUND(roe_q, 2) AS roe_q, ROUND(revenue_yoy, 3) AS rev_yoy,
                   ROUND(inst_holding_pct, 3) AS inst_pct
              FROM fact_financial_pit_daily
             WHERE stock_code='000001'
               AND trade_date IN ('2023-06-15','2024-06-14','2025-06-13','2026-05-12')
             ORDER BY trade_date
        """).fetchall()
        print()
        print('=== 000001 跨年抽样 (PIT 验证) ===')
        for x in sample:
            print(f'  {x[1]}  report={x[2]}  announce={x[3]}  '
                  f'PE={x[4]} PB={x[5]} PS={x[6]} ROE_q={x[7]} rev_yoy={x[8]} '
                  f'inst%={x[9]}')
    finally:
        smart.close()


if __name__ == "__main__":
    main()
