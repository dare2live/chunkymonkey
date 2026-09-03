#!/usr/bin/env python3
"""申万行业 **深史 PIT as-of 视图** builder (owner=本文件)。

本脚本只建 SW namespace 的 v_sw_industry_pit，供明确声明 SW taxonomy 的消费者使用。
它不是 DC namespace 的深史兜底；两者只能通过显式、版本化 crosswalk 比较。
> 当前快照 dim_stock_sw_industry 已物删 (孤儿); 其重建路径 = 本视图 WHERE out_date IS NULL, 不再维护独立 dim。

真相源 = tushare_raw.raw_tushare_index_member_all (S1 已补 is_new='N' 历史区间, out_date 填)。
奥卡姆: 不物化中间 mart, 一个薄视图暴露 PIT 区间; 消费侧按决策日 t 做 as-of 查询:
  WHERE in_date<=t AND (out_date IS NULL OR out_date>t)
归一: ts_code '000592.SZ' → stock_code '000592' (6位, 与 market K线一致, 实测);
  out_date 原 INTEGER → VARCHAR (与 in_date VARCHAR 'YYYYMMDD' 同型, 字典序=时序, as-of 字符串比较安全)。

PIT 互斥 (2026-07-22 Tier0B): 申万同级 L1 互斥。vendor raw 在重分类时偶发不闭合旧
out_date，或同 in_date 双 L1 并存（退市油企污染 传媒 L3）。视图对每个 stock 按
(in_date ASC, built_at ASC, l3_code) 序合成 effective out_date = 下一成员 in_date 与
raw out_date 的较早者，并丢弃 out_date<=in_date 的零长度败者行——保证任意 as-of 日
至多一个活跃 L1，fail-closed，不靠 segments/institution_profile 消费方各自 LIMIT。

单库 (tushare_raw 内, 与源表同库) 避跨库 attach 脆弱性。验证 (--verify): 000007 as-of
2018=综合 / 2026=商贸零售；002310 重分类闭合；四票无 open-L1 双计。
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

# Effective out_date closes unclosed predecessor when a later membership starts, and
# on same-in_date dual L1 the older built_at row is zero-length (filtered out).
DDL = f"""
CREATE OR REPLACE VIEW {VIEW} AS
WITH src AS (
  SELECT SPLIT_PART(ts_code, '.', 1) AS stock_code, ts_code, name,
         l1_code, l1_name, l2_code, l2_name, l3_code, l3_name,
         CAST(in_date AS VARCHAR) AS in_date,
         CAST(out_date AS VARCHAR) AS out_date_raw,
         is_new,
         built_at
  FROM {SRC}
),
ordered AS (
  SELECT *,
         LEAD(in_date) OVER (
           PARTITION BY stock_code
           ORDER BY in_date ASC,
                    built_at ASC NULLS FIRST,
                    l3_code ASC
         ) AS next_in_date
  FROM src
),
effective AS (
  SELECT stock_code, ts_code, name,
         l1_code, l1_name, l2_code, l2_name, l3_code, l3_name,
         in_date,
         CASE
           WHEN next_in_date IS NOT NULL
                AND (out_date_raw IS NULL OR out_date_raw > next_in_date)
             THEN next_in_date
           ELSE out_date_raw
         END AS out_date,
         is_new
  FROM ordered
)
SELECT stock_code, ts_code, name,
       l1_code, l1_name, l2_code, l2_name, l3_code, l3_name,
       in_date, out_date, is_new
FROM effective
WHERE out_date IS NULL OR out_date > in_date
"""


def build() -> None:
    c = connect(DB, read_only=False)
    try:
        c.execute(DDL)
    finally:
        c.close()
    print(f"[done] {VIEW} (CREATE OR REPLACE, 源 {SRC}; SW namespace PIT + L1 exclusivity)")


def _asof(c, code: str, t: str, col: str = "l1_name"):
    r = c.execute(
        f"SELECT {col} FROM {VIEW} WHERE stock_code=? AND in_date<=? "
        f"AND (out_date IS NULL OR out_date>?) ORDER BY in_date DESC LIMIT 1",
        (code, t, t)).fetchone()
    return r[0] if r else None


def _open_l1_dup_stocks(c) -> list[str]:
    rows = c.execute(f"""
        SELECT stock_code FROM {VIEW}
        WHERE out_date IS NULL
        GROUP BY stock_code
        HAVING count(DISTINCT l1_code) > 1
        ORDER BY stock_code
    """).fetchall()
    return [r[0] for r in rows]


def _active_l1_count(c, code: str, t: str) -> int:
    return c.execute(
        f"SELECT count(DISTINCT l1_code) FROM {VIEW} "
        f"WHERE stock_code=? AND in_date<=? AND (out_date IS NULL OR out_date>?)",
        (code, t, t),
    ).fetchone()[0]


def verify() -> int:
    c = connect(DB, read_only=True)
    try:
        n = c.execute(f"SELECT count(*), count(DISTINCT stock_code) FROM {VIEW}").fetchone()
        cur = c.execute(
            f"SELECT count(DISTINCT stock_code) FROM {VIEW} WHERE out_date IS NULL"
        ).fetchone()[0]

        a18, a26 = _asof(c, "000007", "20180101"), _asof(c, "000007", "20260101")  # rule-compliance: ok evidence=000007 PIT fixture
        print(f"[verify] {VIEW}: {n[0]}行/{n[1]}股, 当前成分(out_date NULL) {cur}股")
        print(f"[verify] 000007 as-of 2018={a18} / 2026={a26}")

        # rule-compliance: ok evidence=live census 002310 reclass 建筑装饰→公用事业@20260701
        a0630 = _asof(c, "002310", "20260630")  # rule-compliance: ok evidence=live 002310
        a0701 = _asof(c, "002310", "20260701")  # rule-compliance: ok evidence=live 002310
        a0722 = _asof(c, "002310", "20260722")  # rule-compliance: ok evidence=live 002310
        n0701 = _active_l1_count(c, "002310", "20260701")  # rule-compliance: ok evidence=live 002310
        n0722 = _active_l1_count(c, "002310", "20260722")  # rule-compliance: ok evidence=live 002310
        print(f"[verify] 002310 as-of 20260630={a0630} / 20260701={a0701} "
              f"/ 20260722={a0722} (active_l1@0701={n0701}, @0722={n0722})")

        # rule-compliance: ok evidence=live census same-in_date dual L1 退市油企
        dual_ok = True
        for code in ("000406", "000817", "000956"):  # rule-compliance: ok evidence=live dual-L1 census
            n_act = _active_l1_count(c, code, "20260722")
            name = _asof(c, code, "20260722")
            print(f"[verify] {code} as-of 20260722={name} active_l1={n_act}")
            if n_act != 1 or name != "石油石化":
                dual_ok = False

        dups = _open_l1_dup_stocks(c)
        print(f"[verify] open multi-L1 stocks: {dups or 'none'}")

        ok = (
            a18 == "综合"
            and a26 == "商贸零售"
            and cur >= 5000
            and a0630 == "建筑装饰"
            and a0701 == "公用事业"
            and a0722 == "公用事业"
            and n0701 == 1
            and n0722 == 1
            and dual_ok
            and not dups
        )
        print(f"[verify] view {'PASS' if ok else 'FAIL'} "
              f"(深史 PIT as-of + L1 exclusivity + 当前覆盖)")
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
