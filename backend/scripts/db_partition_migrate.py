#!/usr/bin/env python3
"""DB 多库分区迁移引擎 (D1+, owner=analysis/db_management_design_20260614.md).

按 backend/config/db_partition_tiers.yaml 把指定 tier 的表从 smartmoney.duckdb **保真**迁到 tier 库。
保真 = 逐表取原 DDL (含 PRIMARY KEY) + INSERT SELECT + 重建索引 —— 不用 CREATE TABLE AS SELECT
(那会无条件丢 PK, 06-12 COPY FROM DATABASE 同型教训 315→1)。

铁律 (平滑过渡):
  - dry-run 默认 (只 preflight + 打印计划 + 原子簇完整性检查); --execute 才真迁。
  - **绝不 DROP 源表** (本脚本只做 D1a 非破坏迁移; 源表 DROP = D1c 另走 --drop-source 且需先验证 + 写入方 repoint)。
  - 迁移后逐表验证: 行数 + 内容 EXCEPT 双向 == 0 + DDL PK 保真 + 约束数 + 索引数, 任一不齐 → FAIL (不 DROP 源)。
  - 原子写簇 (config) 不许拆: 迁移集合若拆开任一簇 → preflight FAIL (关联性检查)。

用法:
  python backend/scripts/db_partition_migrate.py --tier experiment            # dry-run (默认)
  python backend/scripts/db_partition_migrate.py --tier experiment --execute  # 真迁 + 验证 (不 DROP 源)
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "backend"))

from services.duck_adapter import connect as duck_connect  # noqa: E402

CONFIG = REPO / "backend" / "config" / "db_partition_tiers.yaml"
MANIFEST = REPO / "backend" / "config" / "database_manifest.yaml"


def _manifest_path(alias: str) -> Path:
    """DB 路径走 database_manifest.yaml (唯一真相源), 不在代码 hardcode。"""
    with open(MANIFEST, encoding="utf-8") as f:
        m = yaml.safe_load(f)
    return REPO / m["databases"][alias]["path"]


SOURCE_DB = _manifest_path("smartmoney")


def load_cfg() -> dict:
    with open(CONFIG, encoding="utf-8") as f:
        return yaml.safe_load(f)


def check_atomic_cluster_integrity(migrate_set: set[str], clusters: list[dict]) -> list[str]:
    """关联性检查: 迁移集合若拆开任一原子写簇 → 返回违规说明 (空=通过)。"""
    errs = []
    for cl in clusters or []:
        members = set(cl.get("tables", []))
        inside = members & migrate_set
        if inside and inside != members:
            outside = members - migrate_set
            errs.append(
                f"原子簇 '{cl['name']}' 被拆: 迁移集含 {sorted(inside)} 但漏 {sorted(outside)} "
                f"(同事务写不可跨文件 → 必须整簇同迁或整簇不迁; 该簇 tier={cl.get('tier')})"
            )
    return errs


def _table_ddl(conn, db_alias: str, table: str) -> str | None:
    row = conn.execute(
        "SELECT sql FROM duckdb_tables() WHERE database_name=? AND table_name=?",
        [db_alias, table],
    ).fetchone()
    return row[0] if row else None


def _baseline(conn, db_alias: str, table: str) -> dict:
    n = conn.execute(f'SELECT COUNT(*) FROM {db_alias}."{table}"').fetchone()[0]
    ncons = conn.execute(
        "SELECT COUNT(*) FROM duckdb_constraints() WHERE database_name=? AND table_name=?",
        [db_alias, table],
    ).fetchone()[0]
    nidx = conn.execute(
        "SELECT COUNT(*) FROM duckdb_indexes() WHERE database_name=? AND table_name=?",
        [db_alias, table],
    ).fetchone()[0]
    ddl = _table_ddl(conn, db_alias, table) or ""
    return {"rows": n, "constraints": ncons, "indexes": nidx, "has_pk": "PRIMARY KEY" in ddl, "ddl": ddl}


def run(tier: str, execute: bool) -> int:
    cfg = load_cfg()
    tier_cfg = cfg["tiers"].get(tier)
    if not tier_cfg:
        print(f"FAIL: tier '{tier}' 不在 config", file=sys.stderr)
        return 2
    tables = tier_cfg.get("tables") or []
    target_db = REPO / tier_cfg["target_db"]
    if not tables:
        print(f"FAIL: tier '{tier}' 表清单为空 (待补)", file=sys.stderr)
        return 2

    print(f"=== DB 分区迁移 tier={tier} → {target_db.relative_to(REPO)} ===")
    print(f"  模式: {'EXECUTE (真迁)' if execute else 'DRY-RUN (只 preflight)'}")
    print(f"  表数: {len(tables)}")

    # --- preflight: 关联性检查 (原子簇不许拆) ---
    errs = check_atomic_cluster_integrity(set(tables), cfg.get("atomic_write_clusters"))
    if errs:
        print("FAIL: 关联性检查 — 原子写簇被拆:", file=sys.stderr)
        for e in errs:
            print(f"  - {e}", file=sys.stderr)
        return 3
    print("  关联性检查: PASS (无原子簇被拆)")

    # --- preflight: 源 baseline (read-only) ---
    src = duck_connect(str(SOURCE_DB), read_only=True)
    try:
        base = {t: _baseline(src, "smartmoney", t) for t in tables}
    finally:
        src.close()
    total_rows = sum(b["rows"] for b in base.values())
    n_pk = sum(1 for b in base.values() if b["has_pk"])
    print(f"  源 baseline: {total_rows:,} 行, {n_pk}/{len(tables)} 表带 PK")

    if not execute:
        print("\n  [DRY-RUN] 计划迁移 (前 5 表预览):")
        for t in tables[:5]:
            b = base[t]
            print(f"    {t:46s} rows={b['rows']:>9,} pk={b['has_pk']} cons={b['constraints']} idx={b['indexes']}")
        if target_db.exists():
            print(f"\n  注意: {target_db.relative_to(REPO)} 已存在 — --execute 前需确认是否覆盖。")
        print("\n  DRY-RUN 完成。真迁加 --execute (仍不 DROP 源表)。")
        return 0

    # --- EXECUTE: 保真迁移 ---
    if target_db.exists():
        print(f"FAIL: {target_db} 已存在; 拒绝覆盖 (手动确认后删除或改名)。", file=sys.stderr)
        return 4
    tgt = duck_connect(str(target_db), read_only=False)
    try:
        tgt.execute(f"ATTACH '{SOURCE_DB}' AS src (READ_ONLY)")
        for i, t in enumerate(tables, 1):
            ddl = base[t]["ddl"]
            if ddl:
                tgt.execute(ddl)  # 原 DDL 含 PK → 保真建表 (非 CREATE AS SELECT)
                tgt.execute(f'INSERT INTO "{t}" SELECT * FROM src."{t}"')
            elif base[t]["constraints"] == 0:
                # duckdb_tables().sql=NULL (CTAS/pandas 建表常见) 且零约束 → CTAS fallback 保真 (无 PK/约束可丢)
                tgt.execute(f'CREATE TABLE "{t}" AS SELECT * FROM src."{t}"')
            else:
                raise RuntimeError(
                    f"表 {t} sql=NULL 但有 {base[t]['constraints']} 约束 — CTAS 会丢约束, 需手动 DDL 重建 (当前 0 例)"
                )
            # 重建索引 (非约束自带索引): 从源 duckdb_indexes 取 sql 重放
            for (isql,) in tgt.execute(
                "SELECT sql FROM duckdb_indexes() WHERE database_name='src' AND table_name=? AND sql IS NOT NULL",
                [t],
            ).fetchall():
                if isql:
                    tgt.execute(isql)
            if i % 5 == 0:
                tgt.execute("CHECKPOINT")
        tgt.execute("CHECKPOINT")

        # --- 迁移后验证 (任一 FAIL → 不 DROP 源, 返回非零) ---
        print("\n  验证 (源 vs 目标):")
        fails = []
        for t in tables:
            sb = base[t]
            tb = _baseline(tgt, tier, t) if False else None  # target 是默认 catalog, 用空 alias 查
            # 目标表在默认 catalog: 直接查
            n = tgt.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
            ncons = tgt.execute(
                "SELECT COUNT(*) FROM duckdb_constraints() WHERE database_name=? AND table_name=?",
                [target_db.stem, t],
            ).fetchone()[0]
            nidx = tgt.execute(
                "SELECT COUNT(*) FROM duckdb_indexes() WHERE database_name=? AND table_name=?",
                [target_db.stem, t],
            ).fetchone()[0]
            tddl = _table_ddl(tgt, target_db.stem, t) or ""
            # 内容: 双向 EXCEPT == 0
            d1 = tgt.execute(f'SELECT COUNT(*) FROM (SELECT * FROM src."{t}" EXCEPT SELECT * FROM "{t}")').fetchone()[0]
            d2 = tgt.execute(f'SELECT COUNT(*) FROM (SELECT * FROM "{t}" EXCEPT SELECT * FROM src."{t}")').fetchone()[0]
            ok = (n == sb["rows"] and d1 == 0 and d2 == 0
                  and ncons == sb["constraints"] and nidx == sb["indexes"]
                  and ("PRIMARY KEY" in tddl) == sb["has_pk"])
            mark = "PASS" if ok else "FAIL"
            if not ok:
                fails.append(t)
            print(f"    [{mark}] {t:46s} rows {sb['rows']}->{n} except({d1},{d2}) "
                  f"cons {sb['constraints']}->{ncons} idx {sb['indexes']}->{nidx} pk {sb['has_pk']}->{'PRIMARY KEY' in tddl}")
        if fails:
            print(f"\nFAIL: {len(fails)} 表验证不齐 — 源表保留未动, 删除 {target_db.name} 后排查: {fails}", file=sys.stderr)
            return 5
        print(f"\n  全部 {len(tables)} 表保真验证 PASS。源表**未 DROP** (D1a 非破坏)。")
        print("  下一步 (人工确认后): 写入方 repoint 到新库 + 读取方 ATTACH → 验证 live → 才 --drop-source (D1c)。")
    finally:
        tgt.close()
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tier", required=True, choices=["experiment", "feature", "source"])
    ap.add_argument("--execute", action="store_true", help="真迁 (默认 dry-run); 仍不 DROP 源表")
    args = ap.parse_args()
    sys.exit(run(args.tier, args.execute))


if __name__ == "__main__":
    main()
