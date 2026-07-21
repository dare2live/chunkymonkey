#!/usr/bin/env python3
"""申万行业 **深史 PIT as-of 视图** builder (owner=docs/MASTER_TOPLEVEL_DESIGN.md §6.2)。

本脚本只建 SW namespace 的 v_sw_industry_pit，供明确声明 SW taxonomy 的消费者使用。
它不是 DC namespace 的深史兜底；两者只能通过显式、版本化 crosswalk 比较。
> 当前快照 dim_stock_sw_industry 已物删 (孤儿); 其重建路径 = 本视图 WHERE out_date IS NULL, 不再维护独立 dim。

真相源 = tushare_raw.raw_tushare_index_member_all (S1 已补 is_new='N' 历史区间, out_date 填)。
奥卡姆: 不物化中间 mart, 一个薄视图暴露 PIT 区间; 消费侧按决策日 t 做 as-of 查询:
  WHERE in_date<=t AND (out_date IS NULL OR out_date>t) ORDER BY in_date DESC LIMIT 1 (取当时活跃归属)。
归一: ts_code '000592.SZ' → stock_code '000592' (6位, 与 market K线一致, 实测);
  out_date 原 INTEGER → VARCHAR (与 in_date VARCHAR 'YYYYMMDD' 同型, 字典序=时序, as-of 字符串比较安全)。
单库 (tushare_raw 内, 与源表同库) 避跨库 attach 脆弱性。验证 (--verify): 000007 as-of 2018=综合 / 2026=商贸零售。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "backend"))

from services.duck_adapter import connect  # noqa: E402

DB = "data/tushare_raw.duckdb"  # rule-compliance: ok evidence=申万源表 raw_tushare_index_member_all 所在库 (database_manifest tushare_raw)
VIEW = "v_sw_industry_pit"
SRC = "raw_tushare_index_member_all"

DDL = f"""
CREATE OR REPLACE VIEW {VIEW} AS
SELECT SPLIT_PART(ts_code, '.', 1) AS stock_code, ts_code, name,
       l1_code, l1_name, l2_code, l2_name, l3_code, l3_name,
       in_date, CAST(out_date AS VARCHAR) AS out_date, is_new
FROM {SRC}
"""


def build() -> None:
    c = connect(DB, read_only=False)
    try:
        c.execute(DDL)
    finally:
        c.close()
    print(f"[done] {VIEW} (CREATE OR REPLACE, 源 {SRC}; SW namespace PIT only)")


def verify() -> int:
    c = connect(DB, read_only=True)
    try:
        n = c.execute(f"SELECT count(*), count(DISTINCT stock_code) FROM {VIEW}").fetchone()
        cur = c.execute(f"SELECT count(DISTINCT stock_code) FROM {VIEW} WHERE out_date IS NULL").fetchone()[0]

        def asof(code: str, t: str, col: str = "l1_name"):
            r = c.execute(
                f"SELECT {col} FROM {VIEW} WHERE stock_code=? AND in_date<=? "
                f"AND (out_date IS NULL OR out_date>?) ORDER BY in_date DESC LIMIT 1",
                (code, t, t)).fetchone()
            return r[0] if r else None

        # rule-compliance: ok evidence=验证 fixture (000007 实测换6次行业的股, 2018/2026 spot-check as-of 正确性)
        a18, a26 = asof("000007", "20180101"), asof("000007", "20260101")
        print(f"[verify] {VIEW}: {n[0]}行/{n[1]}股, 当前成分(out_date NULL) {cur}股")
        print(f"[verify] 000007 as-of 2018={a18} / 2026={a26}")
        ok = a18 == "综合" and a26 == "商贸零售" and cur >= 5000
        print(f"[verify] view {'PASS' if ok else 'FAIL'} (深史 PIT as-of 正确性 + 当前覆盖)")
    finally:
        c.close()
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--verify", action="store_true", help="仅验证不重建")
    args = ap.parse_args(argv)
    if not args.verify:
        build()
    return verify()


if __name__ == "__main__":
    sys.exit(main())
