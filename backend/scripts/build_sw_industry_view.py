#!/usr/bin/env python3
"""S2: 申万行业 PIT as-of 视图 — 行业迁移 通达信→申万 (owner=analysis/industry_migration_tdx_to_sw_20260615.md)。

真相源 = tushare_raw.raw_tushare_index_member_all (S1 已补 is_new='N' 历史区间, out_date 填)。
奥卡姆: 不物化中间 mart, 一个薄视图暴露 PIT 区间; 消费侧按决策日 t 做 as-of 查询:
  WHERE in_date<=t AND (out_date IS NULL OR out_date>t) ORDER BY in_date DESC LIMIT 1 (取当时活跃归属)。
归一: ts_code '000592.SZ' → stock_code '000592' (6位, 与 smartmoney dim / market K线 一致, 实测);
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
SMARTMONEY = "data/smartmoney.duckdb"  # rule-compliance: ok evidence=live serving 主库 (S3 当前快照 dim 落此避跨库)
VIEW = "v_sw_industry_pit"
SRC = "raw_tushare_index_member_all"
DIM = "dim_stock_sw_industry"  # S3 当前申万快照 (serving); 列名 tdx_l* = 位置别名(level1/2/3), 值=申万SW2021, 见 owner doc

DDL = f"""
CREATE OR REPLACE VIEW {VIEW} AS
SELECT SPLIT_PART(ts_code, '.', 1) AS stock_code, ts_code,
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
    print(f"[done] {VIEW} (CREATE OR REPLACE, 源 {SRC})")


def build_current_dim() -> None:
    """S3: 当前申万快照 → smartmoney dim_stock_sw_industry, 供 live serving (避跨库, 消费者读 smartmoney 如常)。

    源 = v_sw_industry_pit WHERE out_date IS NULL (当前成分, 1股1行)。列名 tdx_l1/l2/l3(+name) 是**位置别名**
    (level-1/2/3 行业), 值已是申万 SW2021 — 保留列名仅为最小化消费方改动 (industry.py INDUSTRY_TABLE 切此表即可),
    源真相见 analysis/industry_migration_tdx_to_sw_20260615.md。as-of/PIT 走 v_sw_industry_pit (本快照只服务"当前")。
    """
    c = connect(SMARTMONEY, read_only=False, attach={"traw": {"path": DB, "read_only": True}})
    try:
        c.execute(f"""
        CREATE OR REPLACE TABLE {DIM} AS
        SELECT stock_code, l1_code AS tdx_l1, l1_name AS tdx_l1_name,
               l2_code AS tdx_l2, l2_name AS tdx_l2_name,
               l3_code AS tdx_l3, l3_name AS tdx_l3_name, CURRENT_TIMESTAMP AS updated_at
        FROM traw.{VIEW} WHERE out_date IS NULL
        """)
    finally:
        c.close()
    print(f"[done] {DIM} (smartmoney 当前申万快照 from {VIEW})")


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
        print(f"[verify] view {'PASS' if ok else 'FAIL'} (PIT as-of 正确性 + 当前覆盖)")
    finally:
        c.close()
    # serving dim (smartmoney 当前快照)
    sm = connect(SMARTMONEY, read_only=True)
    try:
        dn = sm.execute(f"SELECT count(*), count(DISTINCT stock_code) FROM {DIM}").fetchone()
        # rule-compliance: ok evidence=验证 fixture (000007 当前申万行业 spot-check)
        cur07 = sm.execute(f"SELECT tdx_l1_name FROM {DIM} WHERE stock_code='000007'").fetchone()
        dim_ok = dn[0] == dn[1] and dn[0] >= 5000 and cur07 and cur07[0] == "商贸零售"
        print(f"[verify] {DIM}: {dn[0]}行/{dn[1]}股(1:1), 000007={cur07[0] if cur07 else None}; dim {'PASS' if dim_ok else 'FAIL'}")
    finally:
        sm.close()
    return 0 if (ok and dim_ok) else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--verify", action="store_true", help="仅验证不重建")
    args = ap.parse_args(argv)
    if not args.verify:
        build()
        build_current_dim()
    return verify()


if __name__ == "__main__":
    sys.exit(main())
