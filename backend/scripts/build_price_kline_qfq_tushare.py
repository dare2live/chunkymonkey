"""建 tushare 前复权 K线 (消费链切换: tushare 转正主源, §4.3) + 与 tdxhub 重叠期对账。

owner=CLAUDE §4.3 (tushare 主源转正) + sync_registry 注 "消费链切换是独立大手术须 review"。
缘起: 回测读的 v_price_kline_qfq 建自 tdxhub 备援源只到 2022; 而 raw_tushare_daily+adj_factor 已在库 2019+。
本脚本从 tushare raw 建前复权 K线 (2019+) 作回测新主源, load_kline 切过去 -> 回测可跨 2020+ 多 regime。

前复权 (qfq rebased to latest, 与 tdxhub 同约定): qfq = raw × adj_factor / adj_factor_latest_per_stock。
  返回 (收益) = qfq[t]/qfq[t-1] = (raw×f)[t]/(raw×f)[t-1] = 含分红总收益 (PIT: f[t] 除权日即知)。
  单位对齐 tdxhub: volume 手×100=股, amount 千元×1000=元 (capacity 诊断口径一致)。
验证 (§11 重大改动 -> 对账): 与 tdxhub v_price_kline_qfq 在 2022+ 重叠期逐日**收益**对账 max_rel_diff (期望≈0);
  收益匹配 = 两源同一标的同一前复权 (rebase 常数在收益里抵消), 切换安全。不匹配先查不 repoint。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "backend"))

from services.duck_adapter import connect  # noqa: E402

MARKET_DB = "data/market.duckdb"  # rule-compliance: ok evidence=回测K线库 (与 experiment_l0_baseline _db('market') 同库, 一次性 build 脚本)
TUSHARE_DB = str(REPO / "data" / "tushare_raw.duckdb")  # rule-compliance: ok evidence=tushare raw 源库 (ATTACH read-only, 一次性 build 脚本)
TARGET = "price_kline_qfq_tushare"
START = "20190101"  # rule-compliance: ok evidence=raw_tushare_daily 实测起点 2019-01-02 (全量回测窗起点)


def build(conn) -> int:
    conn.execute(f"ATTACH IF NOT EXISTS '{TUSHARE_DB}' AS tr (READ_ONLY)")
    conn.execute(f"DROP TABLE IF EXISTS {TARGET}")
    conn.execute(f"""
        CREATE TABLE {TARGET} AS
        WITH latest AS (
            SELECT ts_code, adj_factor AS f_latest FROM (
                SELECT ts_code, adj_factor,
                       ROW_NUMBER() OVER (PARTITION BY ts_code ORDER BY trade_date DESC) rn
                FROM tr.raw_tushare_adj_factor) WHERE rn = 1
        )
        SELECT
            substr(d.ts_code, 1, 6) AS code,
            substr(d.trade_date,1,4)||'-'||substr(d.trade_date,5,2)||'-'||substr(d.trade_date,7,2) AS date,
            d.open  * a.adj_factor / l.f_latest AS open,
            d.high  * a.adj_factor / l.f_latest AS high,
            d.low   * a.adj_factor / l.f_latest AS low,
            d.close * a.adj_factor / l.f_latest AS close,
            d.vol * 100.0 AS volume,      -- 手 -> 股 (对齐 tdxhub)
            d.amount * 1000.0 AS amount   -- 千元 -> 元 (对齐 tdxhub)
        FROM tr.raw_tushare_daily d
        JOIN tr.raw_tushare_adj_factor a ON d.ts_code = a.ts_code AND d.trade_date = a.trade_date
        JOIN latest l ON d.ts_code = l.ts_code
        WHERE d.trade_date >= '{START}' AND d.close > 0 AND a.adj_factor > 0 AND l.f_latest > 0
    """)
    n = conn.execute(f"SELECT count(*) FROM {TARGET}").fetchone()[0]
    conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{TARGET}_cd ON {TARGET}(code, date)")
    return n


def cross_check(conn) -> dict:
    """与 tdxhub v_price_kline_qfq 重叠期 (2022+) 逐日收益对账 (rebase 常数在收益里抵消)。"""
    row = conn.execute(f"""
        WITH ts AS (
            SELECT code, date, close,
                   close / LAG(close) OVER (PARTITION BY code ORDER BY date) - 1 AS ret
            FROM {TARGET} WHERE date >= '2022-01-04'
        ), tx AS (
            SELECT code, date, close,
                   close / LAG(close) OVER (PARTITION BY code ORDER BY date) - 1 AS ret
            FROM v_price_kline_qfq WHERE date >= '2022-01-04' AND adjust='qfq'
        )
        SELECT count(*) AS n,
               max(abs(ts.ret - tx.ret)) AS max_abs_diff,
               avg(abs(ts.ret - tx.ret)) AS avg_abs_diff,
               sum(CASE WHEN abs(ts.ret - tx.ret) > 0.005 THEN 1 ELSE 0 END) AS n_diff_gt50bp
        FROM ts JOIN tx ON ts.code = tx.code AND ts.date = tx.date
        WHERE ts.ret IS NOT NULL AND tx.ret IS NOT NULL
    """).fetchone()
    return {"n_overlap": row[0], "max_abs_ret_diff": row[1], "avg_abs_ret_diff": row[2], "n_diff_gt_50bp": row[3]}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check-only", action="store_true")  # rule-compliance: ok evidence=只对账不重建
    args = ap.parse_args(argv)
    conn = connect(MARKET_DB, read_only=False)
    try:
        if not args.check_only:
            n = build(conn)
            r = conn.execute(f"SELECT min(date),max(date),count(DISTINCT code) FROM {TARGET}").fetchone()
            print(f"[build] {TARGET}: {n:,} 行 | {r[0]}~{r[1]} | {r[2]} 股", flush=True)
        cc = cross_check(conn)
        conn.execute("CHECKPOINT")
    finally:
        conn.close()
    print(f"[对账] vs tdxhub v_price_kline_qfq 重叠期收益: n={cc['n_overlap']:,} "
          f"max_abs_diff={cc['max_abs_ret_diff']:.2e} avg={cc['avg_abs_ret_diff']:.2e} >50bp={cc['n_diff_gt_50bp']:,}")
    ok = cc["max_abs_ret_diff"] is not None and cc["max_abs_ret_diff"] < 0.02 and cc["n_diff_gt_50bp"] < cc["n_overlap"] * 0.01
    print(f"[verdict] {'PASS 收益对账一致, 可 repoint load_kline' if ok else 'REVIEW 收益差异偏大, 先查再 repoint'}")
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
