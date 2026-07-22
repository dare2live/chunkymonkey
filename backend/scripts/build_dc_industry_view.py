#!/usr/bin/env python3
"""Legacy DC namespace 当前快照物化器；目标契约见 MASTER Tier0B。

当前快照按 DC 自身 level 词汇映射，不再以 SW 名称猜层级。它仍缺版本化 membership
有效期和 evidence crosswalk，因此只能服务迁移期展示，不是 canonical classification PIT。

真相源 = tushare_raw.duckdb:
  - raw_tushare_dc_index (idx_type='行业板块'/'概念板块', 板块目录)
  - raw_tushare_dc_member (板块→成分股, 逐日 trade_date 快照, 2025+)

产出 (serving, smartmoney.duckdb):
  - dim_stock_dc_industry: legacy 个股当前快照，仍沿用 tdx_* 位置别名；待迁移。
  - dim_stock_dc_concept: 个股当前概念成员 (多对多)。

旧 `v_dc_industry_pit` writer 已退役：原实现只有 first-seen，没有 out_date/content_type，
不能继续生成假 PIT。现存 DB view 仅作待清理 residue，不得被新 consumer 使用。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "backend"))

from services.duck_adapter import connect  # noqa: E402
from services.taxonomy_config import (  # noqa: E402
    current_snapshot_quality_floor,
    source_index_type,
    source_level_map,
)

TRAW = "data/tushare_raw.duckdb"   # rule-compliance: ok evidence=东财源表所在库 (database_manifest tushare_raw)
SMARTMONEY = "data/smartmoney.duckdb"  # rule-compliance: ok evidence=live serving 主库
DIM_IND = "dim_stock_dc_industry"
DIM_CON = "dim_stock_dc_concept"
_DIM_IND_NEXT = f"{DIM_IND}__next"
_DIM_CON_NEXT = f"{DIM_CON}__next"


def _sql_str(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _dc_level_case(column: str = "i.level") -> str:
    mapping = source_level_map("dc_industry")
    clauses = " ".join(
        f"WHEN CAST({column} AS VARCHAR) = {_sql_str(source)} THEN {int(level[1:])}"
        for source, level in mapping.items()
    )
    return f"CASE {clauses} END"


def _latest_dates(c) -> tuple[str, str]:
    industry_type = source_index_type("dc_industry")
    concept_type = source_index_type("dc_concept")
    industry_last, concept_last = c.execute("""
        SELECT MAX(CASE WHEN idx_type = ? THEN trade_date END),
               MAX(CASE WHEN idx_type = ? THEN trade_date END)
        FROM traw.raw_tushare_dc_index
        WHERE idx_type IN (?, ?)
    """, [industry_type, concept_type, industry_type, concept_type]).fetchone()
    member_last = c.execute(
        "SELECT MAX(trade_date) FROM traw.raw_tushare_dc_member"
    ).fetchone()[0]
    frontier = (industry_last, concept_last, member_last)
    if any(value is None for value in frontier) or len(set(frontier)) != 1:
        raise RuntimeError(
            "DC current snapshot source frontier mismatch: "
            f"industry_index={industry_last} concept_index={concept_last} "
            f"member={member_last}"
        )
    return str(industry_last), str(member_last)


def build_current_dims() -> None:
    """Build and atomically publish the two validated DC current-snapshot dims."""
    c = connect(SMARTMONEY, read_only=False, attach={"traw": {"path": TRAW, "read_only": True}})
    transaction_open = False
    try:
        idx_last, mem_last = _latest_dates(c)
        if not idx_last or not mem_last:
            raise RuntimeError(
                f"DC current snapshot source frontier missing: idx={idx_last} mem={mem_last}"
            )
        # Internal residue is never accepted state. Remove it before the publish transaction so
        # a rollback cannot resurrect an older abandoned shadow.
        c.execute(f"DROP TABLE IF EXISTS {_DIM_IND_NEXT}")
        c.execute(f"DROP TABLE IF EXISTS {_DIM_CON_NEXT}")
        c.execute("BEGIN TRANSACTION")
        transaction_open = True

        # 行业 dim: 个股→其 DC 行业板块，按 DC 源 level 映射为 L1/L2/L3。
        level_case = _dc_level_case("i.level")
        industry_index_type = source_index_type("dc_industry")
        concept_index_type = source_index_type("dc_concept")
        c.execute(f"""
        CREATE TABLE {_DIM_IND_NEXT} AS
        WITH board_lvl AS (
            SELECT i.ts_code AS board_code, i.name AS board_name, {level_case} AS lvl
            FROM traw.raw_tushare_dc_index i
            WHERE i.trade_date = ? AND i.idx_type = ?
        ),
        sb AS (
            SELECT SPLIT_PART(m.con_code, '.', 1) AS stock_code, bl.board_code, bl.board_name, bl.lvl
            FROM traw.raw_tushare_dc_member m JOIN board_lvl bl ON m.ts_code = bl.board_code
            WHERE m.trade_date = ?
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
        """, [idx_last, industry_index_type, mem_last])
        # 概念 dim: 个股→概念板块 (多对多, 保留 board 列表)
        c.execute(f"""
        CREATE TABLE {_DIM_CON_NEXT} AS
        SELECT SPLIT_PART(m.con_code, '.', 1) AS stock_code, m.ts_code AS concept_code,
               i.name AS concept_name, CURRENT_TIMESTAMP AS updated_at
        FROM traw.raw_tushare_dc_member m
        JOIN traw.raw_tushare_dc_index i ON m.ts_code = i.ts_code AND i.trade_date = ?
        WHERE i.idx_type = ? AND m.trade_date = ?
        """, [idx_last, concept_index_type, mem_last])

        if not _validate_dims(
            c,
            industry_table=_DIM_IND_NEXT,
            concept_table=_DIM_CON_NEXT,
            idx_last=idx_last,
            mem_last=mem_last,
        ):
            raise RuntimeError(
                "DC current snapshot validation failed; accepted tables were not published"
            )

        c.execute(f"DROP TABLE IF EXISTS {DIM_CON}")
        c.execute(f"DROP TABLE IF EXISTS {DIM_IND}")
        c.execute(f"ALTER TABLE {_DIM_IND_NEXT} RENAME TO {DIM_IND}")
        c.execute(f"ALTER TABLE {_DIM_CON_NEXT} RENAME TO {DIM_CON}")
        c.execute(
            f"CREATE UNIQUE INDEX idx_dc_industry_stock ON {DIM_IND}(stock_code)"
        )
        c.execute(
            f"CREATE UNIQUE INDEX idx_dc_concept_grain "
            f"ON {DIM_CON}(stock_code, concept_code)"
        )
        c.execute("COMMIT")
        transaction_open = False
        print(f"[done] {DIM_IND} + {DIM_CON} (东财当前快照, idx={idx_last} mem={mem_last})")
        # CX-1: persist published source frontier for empty-increment skip guard.
        try:
            from services.pipeline.delta_manifest import write_dc_as_of

            write_dc_as_of(str(idx_last))
        except Exception as exc:  # noqa: BLE001 — marker is observational; publish already committed
            print(f"[warn] dc_industry_view_as_of write failed: {exc}")
    except BaseException:
        if transaction_open:
            try:
                c.execute("ROLLBACK")
            except Exception:
                pass
        raise
    finally:
        c.close()


def _validate_dims(
    sm,
    *,
    industry_table: str,
    concept_table: str,
    idx_last: str,
    mem_last: str,
) -> bool:
    """Validate one industry/concept table pair against the same raw source frontier."""
    level_case = _dc_level_case("i.level")
    industry_index_type = source_index_type("dc_industry")
    concept_index_type = source_index_type("dc_concept")
    industry = sm.execute(f"""
            WITH board_lvl AS (
                SELECT i.ts_code AS board_code, i.name AS board_name, {level_case} AS lvl
                FROM traw.raw_tushare_dc_index i
                WHERE i.trade_date = ? AND i.idx_type = ?
            ), sb AS (
                SELECT SPLIT_PART(m.con_code, '.', 1) AS stock_code,
                       bl.board_code, bl.board_name, bl.lvl
                FROM traw.raw_tushare_dc_member m
                JOIN board_lvl bl ON m.ts_code = bl.board_code
                WHERE m.trade_date = ?
            ), expected AS (
                SELECT stock_code,
                    MAX(CASE WHEN lvl=1 THEN board_code END) AS tdx_l1,
                    MAX(CASE WHEN lvl=1 THEN board_name END) AS tdx_l1_name,
                    MAX(CASE WHEN lvl=2 THEN board_code END) AS tdx_l2,
                    MAX(CASE WHEN lvl=2 THEN board_name END) AS tdx_l2_name,
                    MAX(CASE WHEN lvl=3 THEN board_code END) AS tdx_l3,
                    MAX(CASE WHEN lvl=3 THEN board_name END) AS tdx_l3_name
                FROM sb GROUP BY stock_code
            ), actual AS (
                SELECT stock_code, tdx_l1, tdx_l1_name, tdx_l2, tdx_l2_name,
                       tdx_l3, tdx_l3_name FROM {industry_table}
            )
            SELECT
                (SELECT COUNT(*) FROM expected),
                (SELECT COUNT(*) FROM actual),
                (SELECT COUNT(DISTINCT stock_code) FROM actual),
                (SELECT COUNT(DISTINCT tdx_l1) FROM expected),
                (SELECT COUNT(DISTINCT tdx_l2) FROM expected),
                (SELECT COUNT(DISTINCT tdx_l3) FROM expected),
                (SELECT COUNT(DISTINCT tdx_l1) FROM actual),
                (SELECT COUNT(DISTINCT tdx_l2) FROM actual),
                (SELECT COUNT(DISTINCT tdx_l3) FROM actual),
                (SELECT COUNT(*) FROM (
                    SELECT * FROM expected EXCEPT SELECT * FROM actual
                ) missing),
                (SELECT COUNT(*) FROM (
                    SELECT * FROM actual EXCEPT SELECT * FROM expected
                ) unexpected),
                (SELECT COUNT(*) FROM (
                    SELECT stock_code, lvl FROM sb
                    GROUP BY stock_code, lvl HAVING COUNT(DISTINCT board_code) > 1
                ) ambiguous),
                (SELECT COUNT(*) FROM actual WHERE stock_code IS NULL),
                (SELECT COUNT(*) FROM actual
                 WHERE (tdx_l1 IS NULL) IS DISTINCT FROM (tdx_l1_name IS NULL)
                    OR (tdx_l2 IS NULL) IS DISTINCT FROM (tdx_l2_name IS NULL)
                    OR (tdx_l3 IS NULL) IS DISTINCT FROM (tdx_l3_name IS NULL)),
                (SELECT COUNT(*) FROM board_lvl WHERE lvl IS NULL)
    """, [idx_last, industry_index_type, mem_last]).fetchone()
    industry_floor = current_snapshot_quality_floor("dc_industry")
    level_floor = industry_floor["min_nodes_by_level"]
    industry_ok = (
        int(industry[0]) > 0
        and int(industry[1]) == int(industry[0]) == int(industry[2])
        and tuple(int(industry[index]) for index in range(3, 6))
        == tuple(int(industry[index]) for index in range(6, 9))
        and int(industry[9]) == 0
        and int(industry[10]) == 0
        and int(industry[11]) == 0
        and int(industry[12]) == 0
        and int(industry[13]) == 0
        and int(industry[14]) == 0
        and int(industry[2]) >= int(industry_floor["min_stocks"])
        and all(
            int(industry[index]) >= int(level_floor[level])
            for index, level in zip(range(6, 9), ("L1", "L2", "L3"))
        )
    )
    print(
        f"[verify] {industry_table}: actual={industry[1]}行/{industry[2]}股 "
        f"expected={industry[0]}股; DC L1/L2/L3 actual="
        f"{industry[6]}/{industry[7]}/{industry[8]} expected="
        f"{industry[3]}/{industry[4]}/{industry[5]}; "
        f"missing={industry[9]} unexpected={industry[10]} ambiguous={industry[11]} "
        f"null_keys={industry[12]} bad_code_name_pairs={industry[13]} "
        f"unmapped_levels={industry[14]}; "
        f"floor=stocks>={industry_floor['min_stocks']} nodes>={level_floor}"
    )

    concept = sm.execute(f"""
            WITH expected AS (
                SELECT SPLIT_PART(m.con_code, '.', 1) AS stock_code,
                       m.ts_code AS concept_code, i.name AS concept_name
                FROM traw.raw_tushare_dc_member m
                JOIN traw.raw_tushare_dc_index i
                  ON m.ts_code = i.ts_code AND i.trade_date = ?
                WHERE i.idx_type = ? AND m.trade_date = ?
            ), actual AS (
                SELECT stock_code, concept_code, concept_name FROM {concept_table}
            )
            SELECT
                (SELECT COUNT(*) FROM expected),
                (SELECT COUNT(*) FROM actual),
                (SELECT COUNT(DISTINCT stock_code) FROM expected),
                (SELECT COUNT(DISTINCT stock_code) FROM actual),
                (SELECT COUNT(DISTINCT concept_code) FROM expected),
                (SELECT COUNT(DISTINCT concept_code) FROM actual),
                (SELECT COUNT(DISTINCT (stock_code, concept_code)) FROM expected),
                (SELECT COUNT(DISTINCT (stock_code, concept_code)) FROM actual),
                (SELECT COUNT(*) FROM (
                    SELECT * FROM expected EXCEPT SELECT * FROM actual
                ) missing),
                (SELECT COUNT(*) FROM (
                    SELECT * FROM actual EXCEPT SELECT * FROM expected
                ) unexpected),
                (SELECT COUNT(*) FROM actual
                 WHERE stock_code IS NULL OR concept_code IS NULL OR concept_name IS NULL)
    """, [idx_last, concept_index_type, mem_last]).fetchone()
    concept_floor = current_snapshot_quality_floor("dc_concept")
    concept_ok = (
        int(concept[0]) > 0
        and tuple(int(concept[index]) for index in range(1, 8, 2))
        == tuple(int(concept[index]) for index in range(0, 7, 2))
        and int(concept[0]) == int(concept[6]) == int(concept[7])
        and int(concept[8]) == 0
        and int(concept[9]) == 0
        and int(concept[10]) == 0
        and int(concept[1]) >= int(concept_floor["min_memberships"])
        and int(concept[3]) >= int(concept_floor["min_stocks"])
        and int(concept[5]) >= int(concept_floor["min_nodes"])
    )
    print(
        f"[verify] {concept_table}: actual={concept[1]}行/{concept[3]}股/{concept[5]}概念 "
        f"expected={concept[0]}行/{concept[2]}股/{concept[4]}概念; "
        f"missing={concept[8]} unexpected={concept[9]} null_keys={concept[10]}; floor="
        f"{concept_floor['min_memberships']}行/{concept_floor['min_stocks']}股/"
        f"{concept_floor['min_nodes']}概念"
    )
    samp = sm.execute(
        f"SELECT stock_code, tdx_l1_name, tdx_l2_name, tdx_l3_name "
        f"FROM {industry_table} LIMIT 3"
    ).fetchall()
    print(f"[verify] 样本: {samp}")
    ok = industry_ok and concept_ok
    print(f"[verify] dim {'PASS' if ok else 'FAIL'}")
    return ok


def verify() -> int:
    sm = connect(
        SMARTMONEY,
        read_only=True,
        attach={"traw": {"path": TRAW, "read_only": True}},
    )
    try:
        idx_last, mem_last = _latest_dates(sm)
        ok = _validate_dims(
            sm,
            industry_table=DIM_IND,
            concept_table=DIM_CON,
            idx_last=idx_last,
            mem_last=mem_last,
        )
        return 0 if ok else 1
    finally:
        sm.close()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--verify", action="store_true", help="仅验证不重建")
    args = ap.parse_args(argv)
    if not args.verify:
        build_current_dims()
    return verify()


if __name__ == "__main__":
    sys.exit(main())
