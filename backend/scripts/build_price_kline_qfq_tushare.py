"""建 tushare 前复权 K线 (M2 清洗层唯一 builder; K线真相源, §4.3) + 重建后自完整性 sanity。

daily_update Step 2.96 每日 CREATE TABLE AS 全量重建 price_kline_qfq_tushare (market.duckdb),
v_price_kline_qfq 视图 FROM 本表 = serving/回测 K线唯一读面。
前复权 (qfq rebased to latest): qfq = raw × adj_factor / adj_factor_latest_per_stock。
  返回 (收益) = qfq[t]/qfq[t-1] = 含分红总收益 (PIT: f[t] 除权日即知)。
  单位: volume 手×100=股, amount 千元×1000=元 (2026-06-22 切主源时对齐旧 tdxhub 口径, 消费方按此约定)。
历史: 原版含 vs tdxhub 重叠期收益对账 (2026-06-22 切主源一次性核证, max_diff 0.03% PASS 后 repoint);
  tdxhub 链 2026-06 全退役后该对账退化为 self-join 永真式 → 2026-07-02 批7 改为自完整性 sanity。
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


# measured: 2026-07-02 实测基线 8,319,172 行 / 5,431 股; floor 留 ~10% 缓冲防日常波动误报
MIN_ROWS = 7_500_000
MIN_CODES = 5_000


def cross_check(conn) -> dict:
    """重建后自完整性 sanity (2026-07-02 批7 重写 — 死闸修复)。

    原版=vs tdxhub v_price_kline_qfq 重叠期收益对账 (切主源一次性核证); tdxhub 退役后
    视图 FROM 本表自身 → self-join 恒 diff=0 恒 PASS = 永真式死闸。改为真自检:
    行数/覆盖不缩水 + 无非法价格行 (视图 WHERE 会过滤坏行, 此处查源表侧防静默丢数)。
    """
    row = conn.execute(f"""
        SELECT count(*)                                        AS n_rows,
               count(DISTINCT code)                            AS n_codes,
               max(date)                                       AS max_date,
               sum(CASE WHEN close IS NULL OR close <= 0
                         OR high < low THEN 1 ELSE 0 END)      AS n_bad_price
        FROM {TARGET}
    """).fetchone()
    return {"n_rows": row[0], "n_codes": row[1], "max_date": row[2], "n_bad_price": row[3]}


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
    print(f"[sanity] {TARGET}: rows={cc['n_rows']:,} codes={cc['n_codes']:,} "
          f"max_date={cc['max_date']} bad_price={cc['n_bad_price']:,}")
    ok = cc["n_rows"] >= MIN_ROWS and cc["n_codes"] >= MIN_CODES and cc["n_bad_price"] == 0
    print(f"[verdict] {'PASS 自完整性检查通过' if ok else 'REVIEW 行数/覆盖缩水或含非法价格行, 先查再消费'}")
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
