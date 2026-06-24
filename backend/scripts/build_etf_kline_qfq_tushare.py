"""建 ETF 前复权 K线 (M2: ETF K线源 mootdx/tx → tushare 单源, §4.3) + vs raw fund_daily 覆盖核证。

owner=analysis/m2_etf_tushare_migration_20260625.md。mirror backend/scripts/build_price_kline_qfq_tushare.py。
缘起: 旧 etf.duckdb.etf_price_kline 源 mootdx(96.3%)+tx, 实弹证 mootdx ETF分红未复权bug(除息日不调)+陈旧+glitch。
M2 Stage E (2026-06-25): 旧 etf_price_kline 已物删, ETF K线统一 tushare 单源 (本表)。
从 tushare raw_tushare_fund_daily + raw_tushare_fund_adj (2019+, 已 universe_filter 场内15/51/56/58) 建前复权 ETF K线。

前复权 (qfq rebased to latest, 同 A股约定): qfq = fund_daily.close × fund_adj.adj_factor / adj_factor_latest_per_code。
  收益 = qfq[t]/qfq[t-1] = 含分红总收益 (PIT: adj 除权日即知)。
单位 (实测对齐): volume=fund_daily.vol(手, 不转), amount=fund_daily.amount×1000(千元→元)。
验证: vs raw fund_daily (tushare 第一手源) 覆盖核证 (码全覆盖); 复权口径正确性由单测守 (合成纯分红除息), 非对 mootdx。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "backend"))

from services.duck_adapter import connect  # noqa: E402

ETF_DB = "data/etf.duckdb"  # rule-compliance: ok evidence=ETF K线库(消费方etf_engine/snapshot/mining同库读, 一次性build脚本)
TUSHARE_DB = str(REPO / "data" / "tushare_raw.duckdb")  # rule-compliance: ok evidence=tushare raw源库 ATTACH read-only, 一次性build脚本
TARGET = "etf_price_kline_qfq_tushare"
START = "20190101"  # rule-compliance: ok evidence=raw_tushare_fund_daily 实测起点 2019-01-02


def build(conn, *, attach: bool = True) -> int:
    """建 qfq 表。attach=True 接真 tushare_raw 库; attach=False 假设 tr 已 attach (单测注合成数据)。"""
    if attach:
        conn.execute(f"ATTACH IF NOT EXISTS '{TUSHARE_DB}' AS tr (READ_ONLY)")
    conn.execute(f"DROP TABLE IF EXISTS {TARGET}")
    conn.execute(f"""
        CREATE TABLE {TARGET} AS
        WITH latest AS (
            SELECT ts_code, adj_factor AS f_latest FROM (
                SELECT ts_code, adj_factor,
                       ROW_NUMBER() OVER (PARTITION BY ts_code ORDER BY trade_date DESC) rn
                FROM tr.raw_tushare_fund_adj) WHERE rn = 1
        )
        SELECT
            substr(d.ts_code, 1, 6) AS code,
            substr(d.trade_date,1,4)||'-'||substr(d.trade_date,5,2)||'-'||substr(d.trade_date,7,2) AS date,
            'daily' AS freq,
            'qfq'   AS adjust,
            d.open  * a.adj_factor / l.f_latest AS open,
            d.high  * a.adj_factor / l.f_latest AS high,
            d.low   * a.adj_factor / l.f_latest AS low,
            d.close * a.adj_factor / l.f_latest AS close,
            d.vol            AS volume,   -- 手 (实测对齐 etf_price_kline mootdx 1.00x, 不×100)
            d.amount * 1000.0 AS amount,  -- 千元 -> 元 (对齐 etf_price_kline mootdx)
            'tushare' AS source
        FROM tr.raw_tushare_fund_daily d
        JOIN tr.raw_tushare_fund_adj a ON d.ts_code = a.ts_code AND d.trade_date = a.trade_date
        JOIN latest l ON d.ts_code = l.ts_code
        WHERE d.trade_date >= '{START}' AND d.close > 0 AND a.adj_factor > 0 AND l.f_latest > 0
    """)
    n = conn.execute(f"SELECT count(*) FROM {TARGET}").fetchone()[0]
    conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{TARGET}_cd ON {TARGET}(code, date)")
    return n


def coverage_check(conn) -> dict:
    """覆盖核证 vs raw fund_daily (tushare_raw 第一手源, M2 Stage E mootdx 已物删故不再对 mootdx)。
    qfq 应覆盖全部 fund_daily∩fund_adj (close>0&adj>0, 2019+) 的码; 报 fund_daily 有但 qfq 无的码 (应=0)。
    复权口径正确性由单测 test_build_etf_kline_qfq_tushare (合成纯分红除息) 守, 非靠 mootdx 对账。"""
    conn.execute(f"ATTACH IF NOT EXISTS '{TUSHARE_DB}' AS tr (READ_ONLY)")
    row = conn.execute(f"""
        SELECT
          (SELECT count(*) FROM {TARGET}) AS qfq_rows,
          (SELECT count(DISTINCT code) FROM {TARGET}) AS qfq_codes,
          (SELECT count(DISTINCT substr(d.ts_code,1,6)) FROM tr.raw_tushare_fund_daily d
             WHERE d.trade_date >= '{START}' AND d.close > 0
               AND NOT EXISTS (SELECT 1 FROM {TARGET} q WHERE q.code = substr(d.ts_code,1,6))) AS missing_codes
    """).fetchone()
    return {"qfq_rows": row[0], "qfq_codes": row[1], "missing_codes": row[2]}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check-only", action="store_true")  # rule-compliance: ok evidence=只覆盖核证不重建
    args = ap.parse_args(argv)
    conn = connect(ETF_DB, read_only=False)
    try:
        if not args.check_only:
            n = build(conn)
            r = conn.execute(f"SELECT min(date),max(date),count(DISTINCT code) FROM {TARGET}").fetchone()
            print(f"[build] {TARGET}: {n:,} 行 | {r[0]}~{r[1]} | {r[2]} ETF", flush=True)
        cc = coverage_check(conn)
        conn.execute("CHECKPOINT")
    finally:
        conn.close()
    print(f"[覆盖核证] vs raw fund_daily (tushare第一手源): qfq {cc['qfq_rows']:,}行/{cc['qfq_codes']}码; "
          f"fund_daily有但qfq无的码={cc['missing_codes']}")
    ok = cc["qfq_rows"] > 0 and cc["missing_codes"] == 0
    print(f"[verdict] {'PASS qfq覆盖全部fund_daily码 (复权正确性见单测)' if ok else 'REVIEW 有码缺失, 查 fund_daily/fund_adj 同步'}")
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
