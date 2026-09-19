"""一次性迁移: canonical_top10_float_holders_period 的主键加 notice_date。

# 为什么

notice_date 是 PARTITION_FIELD 却不在 GRAIN 里, 而它取自供应商**可变**的 UPDATE_DATE。
供应商一改披露日, 同一粒度就搬了分区, 分区内 DELETE 够不到旧行, 主键撞车 ——
而日更是**整日批**, 一只股撞车 = 整天回滚。实测已静默丢失两整天:
  20260818 landing 1,460 行 -> canonical 615 行 / 50 只股 (应 146), 持续 17 天
  20260828 landing 7,356 行 -> canonical **0 行** (半年报高峰 516 只), 持续 10 天
两个批次 rejection_code 都是 None, 静静躺在 LANDED。详见 holders_top10_schema.GRAIN 头注。

# 它做什么 / 不做什么

**一行不删, 一行不改, 只换主键。** 旧版本行留着 —— 它们是「当时可知」不是「记错」:
供应商是 SCD-1(只留最新态, 实测 staging 144,397 个粒度里 0 个有两个 UPDATE_DATE),
我们的 landing 是全世界唯一一份「那天那个榜单长什么样」的记录, 删掉 = 历史消失(红线 1)。

# 验收判据 = 红线 4 的机器证明

迁移后逐个分区重算 accepted 指针的 (row_count, content_hash), 必须与库里存的**逐字相等**。
相等 = 派生面没有因为换主键而改变一个字节。不等 = 事务回滚, 什么都没发生。

幂等: PK 已是新的则直接返回 (no-op)。

用法: PYTHONPATH=backend python backend/scripts/migrate_holders_top10_pk.py [--db PATH] [--dry-run]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.db import get_conn  # noqa: E402
from services.data_sources.accepted_schema import ACCEPTED_TABLE  # noqa: E402
from services.data_sources.holders_top10_acceptance import (  # noqa: E402
    _canonical_column_sql,
    partition_pointer_stats,
)
from services.data_sources.holders_top10_schema import (  # noqa: E402
    CANONICAL_TABLE,
    DATASET_ID,
    SCHEMA_CONTRACT,
)

TMP = f"{CANONICAL_TABLE}__pkmigrate"


def _current_pk(conn) -> str:
    rows = conn.execute(
        "SELECT constraint_text FROM duckdb_constraints() "
        "WHERE table_name = ? AND constraint_type = 'PRIMARY KEY'",
        [CANONICAL_TABLE],
    ).fetchall()
    return str(rows[0][0]).strip() if rows else "(none)"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="")
    ap.add_argument("--dry-run", action="store_true", help="只报当前 PK 与目标 PK, 不改")
    args = ap.parse_args()
    if args.db:
        import services.db as _db

        t = Path(args.db).resolve()
        _db.DB_PATH, _db.DB_DIR = t, t.parent

    want = f"PRIMARY KEY({', '.join(SCHEMA_CONTRACT['primary_key'])})"
    conn = get_conn()
    try:
        have = _current_pk(conn)
        print(f"[pk-migrate] 当前: {have}")
        print(f"[pk-migrate] 目标: {want}")
        if have.replace(" ", "") == want.replace(" ", ""):
            print("[pk-migrate] 已是目标主键, no-op")
            return 0
        if args.dry_run:
            print("[pk-migrate] --dry-run, 未改动")
            return 0

        # 基准 = **迁移前现算**的每分区 (row_count, content_hash), 不是库里存的指针。
        #
        # 2026-09-08: 第一版判据写的是「迁移后重算 == 库里存量指针」, 在备份副本上跑出
        # 4 个分区不等 (20190629 / 20190710 / 20200610 / 20230617, 行数都相等只有 hash 不同)。
        # 查下来它们**迁移前就对不上** —— 在未迁移的备份上重算同样是这 4 个不等,
        # 且四者最后一次接受都是 2026-07-24, 远早于 2026-09-07 加 partition_pointer_stats,
        # 存量 hash 是旧公式(按批次而非按分区)算的。那是一条独立的历史遗留, 不是本次迁移造成的。
        #
        # 迁移的职责是「什么都别改变」, 所以正确的证明是**前后现算相等**, 它把本次改动的影响
        # 与历史遗留隔离开。存量指针的 4 个不等另行报出, 不阻断 —— 但也不假装没有。
        parts = [
            r[0]
            for r in conn.execute(
                f"SELECT DISTINCT partition_value FROM {ACCEPTED_TABLE} "
                "WHERE dataset_id = ? ORDER BY partition_value",
                [DATASET_ID],
            ).fetchall()
        ]
        pointers = [(p_, *partition_pointer_stats(conn, p_)) for p_ in parts]
        stored = {
            r[0]: (r[1], r[2])
            for r in conn.execute(
                f"SELECT partition_value, row_count, content_hash FROM {ACCEPTED_TABLE} "
                "WHERE dataset_id = ?",
                [DATASET_ID],
            ).fetchall()
        }
        pre_existing = [
            p_ for p_, rc, ch in pointers if stored.get(p_) != (rc, ch)
        ]
        if pre_existing:
            print(
                f"[pk-migrate] 注意: {len(pre_existing)} 个分区的**存量指针**迁移前就与现算不等 "
                f"(与本次无关, 见脚本注释): {pre_existing[:6]}"
            )
        before = conn.execute(f"SELECT COUNT(*) FROM {CANONICAL_TABLE}").fetchone()[0]
        print(f"[pk-migrate] 迁移前 {before:,} 行, {len(pointers):,} 个分区指针")

        cols_sql = ",\n            ".join(
            _canonical_column_sql(f) for f in SCHEMA_CONTRACT["fields"]
        )
        pk_sql = ", ".join(SCHEMA_CONTRACT["primary_key"])
        conn.execute("BEGIN TRANSACTION")
        try:
            conn.execute(f"DROP TABLE IF EXISTS {TMP}")
            conn.execute(f"CREATE TABLE {TMP} (\n            {cols_sql},\n"
                         f"            PRIMARY KEY ({pk_sql})\n        )")
            # **具名列**, 不用 SELECT *: 生产表的物理列序是「原 21 列 + ALTER 追加的
            # holder_code/is_holder_org(在末尾)」, 而契约列序把这两列放在 holder_name 之后。
            # SELECT * 按位置对齐会把 notice_date 灌进 is_exit_row 那一列
            # (实测报 Conversion Error: '20260829' -> BOOL, 幸亏类型不兼容才炸出来 ——
            #  若两列类型恰好兼容, 这就是一次静默的列错位)。
            names = ", ".join(f'"{f["name"]}"' for f in SCHEMA_CONTRACT["fields"])
            conn.execute(
                f"INSERT INTO {TMP} ({names}) SELECT {names} FROM {CANONICAL_TABLE}"
            )
            moved = conn.execute(f"SELECT COUNT(*) FROM {TMP}").fetchone()[0]
            if moved != before:
                raise RuntimeError(f"行数不等: {before:,} -> {moved:,}")
            conn.execute(f"DROP TABLE {CANONICAL_TABLE}")
            conn.execute(f"ALTER TABLE {TMP} RENAME TO {CANONICAL_TABLE}")

            # 红线 4 的机器证明: 派生面一个字节都没变。
            bad = []
            for part, row_count, content_hash in pointers:
                rc, ch = partition_pointer_stats(conn, part)
                if rc != row_count or ch != content_hash:
                    bad.append((part, row_count, rc, content_hash[:12], ch[:12]))
            if bad:
                raise RuntimeError(
                    f"{len(bad)} 个分区的现算结果被迁移改变了, 例: {bad[:3]}"
                )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise

        after = conn.execute(f"SELECT COUNT(*) FROM {CANONICAL_TABLE}").fetchone()[0]
        print(f"[pk-migrate] 迁移后 {after:,} 行, PK = {_current_pk(conn)}")
        print(f"[pk-migrate] {len(pointers):,} 个分区指针 hash 重算**全部相等**")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
