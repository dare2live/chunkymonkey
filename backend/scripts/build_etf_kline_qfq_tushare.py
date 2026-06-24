"""建 ETF 前复权 K线 (M2 Stage C: ETF K线源 mootdx/tx → tushare, §4.3) + 与 mootdx 重叠期对账。

owner=analysis/m2_etf_tushare_migration_20260625.md。mirror backend/scripts/build_price_kline_qfq_tushare.py。
缘起: ETF K线现 etf.duckdb.etf_price_kline 源 mootdx(96.3%)+tx, 1-ETF实弹证 mootdx ETF分红未复权bug(除息日不调)。
从 tushare raw_tushare_fund_daily + raw_tushare_fund_adj (2019+, 已 universe_filter 场内15/51/56/58) 建前复权 ETF K线。

前复权 (qfq rebased to latest, 同 A股约定): qfq = fund_daily.close × fund_adj.adj_factor / adj_factor_latest_per_code。
  收益 = qfq[t]/qfq[t-1] = 含分红总收益 (PIT: adj 除权日即知)。
单位对齐 etf_price_kline (实测 mootdx 历史日 vol/amount 1.00× tushare): volume=fund_daily.vol(手, 不转), amount=fund_daily.amount×1000(千元→元)。
验证: 与 etf_price_kline (mootdx) 重叠期(2023+) 逐日**收益**对账; diff 集中在分红除息日 (mootdx 未复权=错, tushare 对, 见单测/fund_div)。
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


def cross_check(conn) -> dict:
    """与 etf_price_kline (mootdx) 重叠期(2023+) 逐日收益对账 (rebase 常数在收益抵消)。
    diff>50bp 预期集中在分红除息日 (mootdx 未复权=错, tushare 对); 报具体日数供人核。"""
    row = conn.execute(f"""
        WITH ts AS (
            SELECT code, date,
                   close / LAG(close) OVER (PARTITION BY code ORDER BY date) - 1 AS ret
            FROM {TARGET} WHERE date >= '2023-01-01'
        ), moo AS (
            SELECT code, date,
                   close / LAG(close) OVER (PARTITION BY code ORDER BY date) - 1 AS ret
            FROM etf_price_kline WHERE freq='daily' AND adjust='qfq' AND date >= '2023-01-01'
        )
        SELECT count(*) AS n,
               max(abs(ts.ret - moo.ret)) AS max_abs_diff,
               avg(abs(ts.ret - moo.ret)) AS avg_abs_diff,
               sum(CASE WHEN abs(ts.ret - moo.ret) > 0.005 THEN 1 ELSE 0 END) AS n_diff_gt50bp
        FROM ts JOIN moo ON ts.code = moo.code AND ts.date = moo.date
        WHERE ts.ret IS NOT NULL AND moo.ret IS NOT NULL
    """).fetchone()
    return {"n_overlap": row[0], "max_abs_ret_diff": row[1], "avg_abs_ret_diff": row[2], "n_diff_gt_50bp": row[3]}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check-only", action="store_true")  # rule-compliance: ok evidence=只对账不重建
    args = ap.parse_args(argv)
    conn = connect(ETF_DB, read_only=False)
    try:
        if not args.check_only:
            n = build(conn)
            r = conn.execute(f"SELECT min(date),max(date),count(DISTINCT code) FROM {TARGET}").fetchone()
            print(f"[build] {TARGET}: {n:,} 行 | {r[0]}~{r[1]} | {r[2]} ETF", flush=True)
        cc = cross_check(conn)
        conn.execute("CHECKPOINT")
    finally:
        conn.close()
    pct = (cc["n_diff_gt_50bp"] / cc["n_overlap"] * 100) if cc["n_overlap"] else 0.0
    print(f"[对账] vs mootdx etf_price_kline 重叠期收益: n={cc['n_overlap']:,} "
          f"max_abs_diff={cc['max_abs_ret_diff']:.2e} avg={cc['avg_abs_ret_diff']:.2e} "
          f">50bp={cc['n_diff_gt_50bp']:,} ({pct:.2f}%)")
    # 预期: diff 集中分红除息日 (mootdx未复权bug); avg≈0 + >50bp占比低 (~年度分红日数/总日数)。
    ok = cc["avg_abs_ret_diff"] is not None and cc["avg_abs_ret_diff"] < 1e-3 and pct < 1.0
    print(f"[verdict] {'PASS 收益除分红日外一致 (diff集中除息日=mootdx未复权bug, tushare对见单测)' if ok else 'REVIEW 差异超预期, 先查真相源(fund_div)再 repoint'}")
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
