#!/usr/bin/env python3
"""DuckDB 整库保真紧缩 (DROP 后文件不缩 — 内部块需整库重写才回收盘)。

保真 = 逐表原 DDL (含 PK/约束) + INSERT + 重建索引 + 视图按定义重建 —— **绝不用 CREATE TABLE AS SELECT**
(CTAS 丢 PK = 06-12 db_split_execute 同型坑, 约束 315→1; 仅 NULL-sql 且零约束的表才 CTAS-fallback)。
ATTACH-copy 法 (无中间 parquet, peak=old+new), 不删生产库 (验证通过才换名, 旧库留 bak)。

dry-run 默认 (列计划 + 对账基线); --execute 才重写 + 验证 + 换名。

用法:
  python backend/scripts/db_compact.py --db smartmoney            # dry-run
  python backend/scripts/db_compact.py --db smartmoney --execute  # 重写紧缩 + 验证 + 换名 (旧库留 _precompact_bak)
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "backend"))

from services.duck_adapter import connect as duck_connect  # noqa: E402

MANIFEST = REPO / "backend" / "config" / "database_manifest.yaml"


def _db_path(alias: str) -> Path:
    m = yaml.safe_load(open(MANIFEST, encoding="utf-8"))
    return REPO / m["databases"][alias]["path"]


def _rel(p: Path) -> Path:
    """显示用相对路径; p 不在 repo 下 (如临时库) 时回退原路径, 不崩。"""
    try:
        return p.relative_to(REPO)
    except ValueError:
        return p


def run(alias: str, execute: bool, drop_bak: bool = False) -> int:
    src = _db_path(alias)
    # 派生兄弟文件名 (非 hardcode DB 路径; src 来自 database_manifest)
    new = src.with_name(src.stem + "_compact.duckdb")  # rule-compliance: ok evidence=derived from manifest src
    bak = src.with_name(src.stem + "_precompact_bak.duckdb")  # rule-compliance: ok evidence=derived from manifest src

    # baseline (read-only)
    s = duck_connect(str(src), read_only=True)
    try:
        tables = [r[0] for r in s.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema='main' AND table_type='BASE TABLE' ORDER BY table_name"
        ).fetchall()]
        views = [r[0] for r in s.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema='main' AND table_type='VIEW'"
        ).fetchall()]
        base_cons = s.execute("SELECT count(*) FROM duckdb_constraints()").fetchone()[0]
        base_idx = s.execute("SELECT count(*) FROM duckdb_indexes()").fetchone()[0]
        ddls = {r[0]: r[1] for r in s.execute("SELECT table_name, sql FROM duckdb_tables WHERE schema_name='main'").fetchall()}
        view_sql = {r[0]: r[1] for r in s.execute("SELECT view_name, sql FROM duckdb_views() WHERE schema_name='main'").fetchall()}
        base_rows = {t: s.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0] for t in tables}
        base_cons_t = {t: s.execute("SELECT count(*) FROM duckdb_constraints() WHERE table_name=?", [t]).fetchone()[0] for t in tables}
    finally:
        s.close()

    sz = src.stat().st_size / 1e9
    print(f"=== 整库保真紧缩 db={alias} ({_rel(src)}, {sz:.1f}G) ===")
    print(f"  对账基线: {len(tables)} 表 + {len(views)} 视图 / {base_cons} 约束 / {base_idx} 索引 / {sum(base_rows.values()):,} 行")
    free = shutil.disk_usage(REPO).free / 1e9
    print(f"  磁盘余量: {free:.0f}G (peak≈old+new≈{sz*2:.0f}G)")

    if not execute:
        print("  DRY-RUN: --execute 重写紧缩 + 验证 + 换名 (旧库留 bak；--drop-bak 换名后删 bak)。")
        return 0
    if free < 10:
        print(f"FAIL: 磁盘余量 {free:.0f}G < 10G, 拒绝紧缩", file=sys.stderr)
        return 6
    if new.exists():
        print(f"FAIL: {new.name} 已存在, 先删", file=sys.stderr)
        return 4

    t = duck_connect(str(new), read_only=False)
    try:
        t.execute(f"ATTACH '{src}' AS src (READ_ONLY)")
        for i, tab in enumerate(tables, 1):
            ddl = ddls.get(tab)
            ncons = base_cons_t[tab]
            if ddl:
                t.execute(ddl)
                t.execute(f'INSERT INTO "{tab}" SELECT * FROM src."{tab}"')
            elif ncons == 0:
                t.execute(f'CREATE TABLE "{tab}" AS SELECT * FROM src."{tab}"')  # NULL-sql 零约束安全
            else:
                raise RuntimeError(f"表 {tab} sql=NULL 但 {ncons} 约束 — 需手动 DDL")
            for (isql,) in t.execute(
                "SELECT sql FROM duckdb_indexes() WHERE database_name='src' AND table_name=? AND sql IS NOT NULL", [tab]
            ).fetchall():
                if isql:
                    t.execute(isql)
            if i % 20 == 0:
                t.execute("CHECKPOINT")
        # 视图按定义重建 (在表之后); 依赖容忍: 重试到不再有进展 (处理视图引用视图)
        pending = [v for v in views if view_sql.get(v)]
        while pending:
            progressed = False
            still = []
            for v in pending:
                try:
                    t.execute(view_sql[v])
                    progressed = True
                except Exception:  # 依赖的视图还没建, 下轮再试
                    still.append(v)
            pending = still
            if not progressed:  # 一轮零进展 = 真错 (非依赖序问题)
                raise RuntimeError(f"视图重建卡死, 无法创建: {pending}")
        t.execute("CHECKPOINT")
        t.execute("DETACH src")  # 关键: information_schema/duckdb_constraints/duckdb_indexes 跨所有 attach 库计数, 不 DETACH 会双倍计 src

        # 验证 (任一不齐 → 不换名)
        new_tables = t.execute("SELECT count(*) FROM information_schema.tables WHERE table_schema='main' AND table_type='BASE TABLE'").fetchone()[0]
        new_views = t.execute("SELECT count(*) FROM information_schema.tables WHERE table_schema='main' AND table_type='VIEW'").fetchone()[0]
        new_cons = t.execute("SELECT count(*) FROM duckdb_constraints()").fetchone()[0]
        new_idx = t.execute("SELECT count(*) FROM duckdb_indexes()").fetchone()[0]
        row_bad = [tab for tab in tables if t.execute(f'SELECT count(*) FROM "{tab}"').fetchone()[0] != base_rows[tab]]
        ok = (new_tables == len(tables) and new_views == len(views) and new_cons == base_cons
              and new_idx == base_idx and not row_bad)
        print(f"  验证: 表 {len(tables)}->{new_tables} 视图 {len(views)}->{new_views} 约束 {base_cons}->{new_cons} 索引 {base_idx}->{new_idx} 行不齐={len(row_bad)}")
        if not ok:
            print(f"FAIL: 对账不齐, 不换名; 删 {new.name} 排查. 行不齐表: {row_bad[:5]}", file=sys.stderr)
            return 5
    finally:
        t.close()

    # 换名 (旧库留 bak)
    src.rename(bak)
    new.rename(src)
    new_sz = src.stat().st_size / 1e9
    saved = sz - new_sz
    if drop_bak:
        bak.unlink()
        print(f"\n  紧缩完成: {sz:.1f}G → {new_sz:.1f}G (省 {saved:.1f}G). 已按 --drop-bak 删除 {bak.name}。")
    else:
        print(f"\n  紧缩完成: {sz:.1f}G → {new_sz:.1f}G (省 {saved:.1f}G). 旧库留 {bak.name} (验证 doctor 后可删)。")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default="smartmoney")
    ap.add_argument("--execute", action="store_true", help="真重写 (默认 dry-run)")
    ap.add_argument(
        "--drop-bak",
        action="store_true",
        help="换名并对账通过后删除 _precompact_bak（不可回滚）",
    )
    args = ap.parse_args()
    sys.exit(run(args.db, args.execute, drop_bak=args.drop_bak))


if __name__ == "__main__":
    main()
