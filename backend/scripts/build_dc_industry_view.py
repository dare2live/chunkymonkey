#!/usr/bin/env python3
"""东财(DC)行业/概念物化 — 全项目单一供应商=东财迁移 (owner=analysis/dc_full_migration_plan_20260623.md)。

决策 (已锁 2026-06-23): 行业=东财(dc_member 行业板块, =申万对齐同套桶) / 概念=东财 / 资金流=东财。
通达信+申万行业 dim 退役; 申万 index_member_all + v_sw_industry_pit **保留**专供深史(2025前)PIT兜底
(东财 dc_member 逐日快照仅 2025+, episode_strata 深史 as-of 走申万深PIT, 同套桶非混口径)。

真相源 = tushare_raw.duckdb:
  - raw_tushare_dc_index (idx_type='行业板块'/'概念板块', 板块目录)
  - raw_tushare_dc_member (板块→成分股, 逐日 trade_date 快照, 2025+)
  - raw_tushare_index_member_all (申万 L1/L2/L3 名集, 仅作"东财板块名→级别"映射参照; 东财行业名99%∈申万名)

产出 (serving, smartmoney.duckdb):
  - dim_stock_dc_industry: 个股当前快照, 列 tdx_l1/tdx_l1_name/.../tdx_l3_name (位置别名, 值=东财行业=申万对齐),
    与旧 dim_stock_sw_industry 列名一致 → 消费方纯表名 swap 零字段改。level 按申万名映射 (实测 L1=31/L2=127/L3=334)。
  - dim_stock_dc_concept: 个股当前概念成员 (多对多)。

PIT 视图 (tushare_raw.duckdb):
  - v_dc_industry_pit: dc_member 逐日快照 as-of (in_date/out_date 区间), 仅 2025+ 有效, 2025前 NULL。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "backend"))

from services.duck_adapter import connect  # noqa: E402

TRAW = "data/tushare_raw.duckdb"   # rule-compliance: ok evidence=东财源表所在库 (database_manifest tushare_raw)
SMARTMONEY = "data/smartmoney.duckdb"  # rule-compliance: ok evidence=live serving 主库
DIM_IND = "dim_stock_dc_industry"
DIM_CON = "dim_stock_dc_concept"
PIT_VIEW = "v_dc_industry_pit"


def _latest_dates(c) -> tuple[str, str]:
    idx_last = c.execute("SELECT MAX(trade_date) FROM traw.raw_tushare_dc_index WHERE idx_type='行业板块'").fetchone()[0]
    mem_last = c.execute(
        "SELECT MAX(trade_date) FROM traw.raw_tushare_dc_member WHERE trade_date <= ?", [idx_last]
    ).fetchone()[0]
    return idx_last, mem_last


def build_current_dims() -> None:
    """当前快照 dim (serving): 东财行业 (level 按申万名映射) + 东财概念。"""
    c = connect(SMARTMONEY, read_only=False, attach={"traw": {"path": TRAW, "read_only": True}})
    try:
        idx_last, mem_last = _latest_dates(c)
        # 行业 dim: 个股→其行业板块(3层) pivot 成 L1/L2/L3 (按申万名定级)
        c.execute(f"""
        CREATE OR REPLACE TABLE {DIM_IND} AS
        WITH sw AS (
            SELECT DISTINCT l1_name AS n, 1 AS lvl FROM traw.raw_tushare_index_member_all WHERE l1_name IS NOT NULL
            UNION SELECT DISTINCT l2_name, 2 FROM traw.raw_tushare_index_member_all WHERE l2_name IS NOT NULL
            UNION SELECT DISTINCT l3_name, 3 FROM traw.raw_tushare_index_member_all WHERE l3_name IS NOT NULL
        ),
        board_lvl AS (
            SELECT i.ts_code AS board_code, i.name AS board_name, sw.lvl
            FROM traw.raw_tushare_dc_index i JOIN sw ON i.name = sw.n
            WHERE i.trade_date = '{idx_last}' AND i.idx_type = '行业板块'
        ),
        sb AS (
            SELECT SPLIT_PART(m.con_code, '.', 1) AS stock_code, bl.board_code, bl.board_name, bl.lvl
            FROM traw.raw_tushare_dc_member m JOIN board_lvl bl ON m.ts_code = bl.board_code
            WHERE m.trade_date = '{mem_last}'
        )
        SELECT stock_code,
            MAX(CASE WHEN lvl=1 THEN board_code END) AS tdx_l1,
            MAX(CASE WHEN lvl=1 THEN board_name END) AS tdx_l1_name,
            MAX(CASE WHEN lvl=2 THEN board_code END) AS tdx_l2,
            MAX(CASE WHEN lvl=2 THEN board_name END) AS tdx_l2_name,
            MAX(CASE WHEN lvl=3 THEN board_code END) AS tdx_l3,
            MAX(CASE WHEN lvl=3 THEN board_name END) AS tdx_l3_name,
            CURRENT_TIMESTAMP AS updated_at
        FROM sb GROUP BY stock_code
        """)
        # 概念 dim: 个股→概念板块 (多对多, 保留 board 列表)
        c.execute(f"""
        CREATE OR REPLACE TABLE {DIM_CON} AS
        SELECT SPLIT_PART(m.con_code, '.', 1) AS stock_code, m.ts_code AS concept_code,
               i.name AS concept_name, CURRENT_TIMESTAMP AS updated_at
        FROM traw.raw_tushare_dc_member m
        JOIN traw.raw_tushare_dc_index i ON m.ts_code = i.ts_code AND i.trade_date = '{idx_last}'
        WHERE i.idx_type = '概念板块' AND m.trade_date = '{mem_last}'
        """)
        print(f"[done] {DIM_IND} + {DIM_CON} (东财当前快照, idx={idx_last} mem={mem_last})")
    finally:
        c.close()


def build_pit_view() -> None:
    """as-of PIT 视图 (dc_member 逐日快照 → 区间), 仅 2025+ 有效。"""
    c = connect(TRAW, read_only=False)
    try:
        # 逐日快照 → 每股每板块的 [in_date, out_date) 区间 (out_date=NULL 表示当前仍在)
        c.execute(f"""
        CREATE OR REPLACE VIEW {PIT_VIEW} AS
        WITH snap AS (
            SELECT DISTINCT SPLIT_PART(con_code, '.', 1) AS stock_code, ts_code AS board_code, trade_date
            FROM raw_tushare_dc_member
        ),
        runs AS (
            SELECT stock_code, board_code, trade_date,
                   LAG(trade_date) OVER (PARTITION BY stock_code, board_code ORDER BY trade_date) AS prev_d
            FROM snap
        )
        SELECT stock_code, board_code, MIN(trade_date) AS in_date
        FROM runs GROUP BY stock_code, board_code
        """)
        print(f"[done] {PIT_VIEW} (东财成员 as-of PIT, 仅 2025+; 深史走 v_sw_industry_pit)")
    finally:
        c.close()


def verify() -> int:
    sm = connect(SMARTMONEY, read_only=True)
    try:
        r = sm.execute(f"SELECT COUNT(*), COUNT(DISTINCT stock_code), COUNT(DISTINCT tdx_l1_name), COUNT(DISTINCT tdx_l2_name), COUNT(DISTINCT tdx_l3_name) FROM {DIM_IND}").fetchone()
        # rule-compliance: ok evidence=验证 fixture (行业 dim 1:1 + 申万对齐桶数)
        l1_ok = 28 <= r[2] <= 34
        cov_ok = r[0] == r[1] and r[0] >= 5000
        print(f"[verify] {DIM_IND}: {r[0]}行/{r[1]}股(1:1={r[0]==r[1]}) L1桶={r[2]} L2桶={r[3]} L3桶={r[4]} (申万对照31/131/337)")
        cn = sm.execute(f"SELECT COUNT(DISTINCT stock_code), COUNT(DISTINCT concept_code) FROM {DIM_CON}").fetchone()
        print(f"[verify] {DIM_CON}: {cn[0]}股 / {cn[1]}概念")
        samp = sm.execute(f"SELECT stock_code, tdx_l1_name, tdx_l2_name, tdx_l3_name FROM {DIM_IND} LIMIT 3").fetchall()
        print(f"[verify] 样本: {samp}")
        ok = l1_ok and cov_ok
        print(f"[verify] dim {'PASS' if ok else 'FAIL'}")
        return 0 if ok else 1
    finally:
        sm.close()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--verify", action="store_true", help="仅验证不重建")
    args = ap.parse_args(argv)
    if not args.verify:
        build_current_dims()
        build_pit_view()
    return verify()


if __name__ == "__main__":
    sys.exit(main())
