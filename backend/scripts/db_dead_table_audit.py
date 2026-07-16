#!/usr/bin/env python3
"""死表审计 + 清理工具 (owner=docs/engineering_governance.md §6/§10).

保守规则: 只有 **0 行 AND 0 引用** (backend/ 非测试代码无任一 SELECT/JOIN/INSERT/CREATE 该表名) 才判死表可删。
→ "空但有 writer" 的表 (如 ablation 输出未跑) 被引用检查保护, 不误删。

dry-run 默认 (只列候选); --execute 才 DROP (事务 + CHECKPOINT 回收盘)。
非空 0-ref 历史表不在本工具自动范围 (需 EXPORT parquet 备份, 走 --include-nonempty 显式 + 另行 EXPORT)。

用法:
  python backend/scripts/db_dead_table_audit.py --db smartmoney            # dry-run
  python backend/scripts/db_dead_table_audit.py --db smartmoney --execute  # DROP 0行0引用死表
"""
from __future__ import annotations

import argparse
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


def _zero_row_tables(conn) -> list[str]:
    tabs = [r[0] for r in conn.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema='main'"
    ).fetchall()]
    out = []
    for t in tabs:
        try:
            if conn.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0] == 0:
                out.append(t)
        except Exception: pass  # 跳过单表异常 (审计尽力而为)
    return out


def _load_code_corpus() -> str:
    """backend/ 非测试 .py 全文拼成一个语料 (一次性), 供表名引用查 (无 PATH 依赖)。"""
    parts = []
    for p in (REPO / "backend").rglob("*.py"):
        sp = str(p)
        if "/tests/" in sp or "/fixtures/" in sp:
            continue
        try:
            parts.append(p.read_text(encoding="utf-8", errors="ignore"))
        except Exception: pass  # 跳过单表异常 (审计尽力而为)
    return "\n".join(parts)


def _is_referenced(table: str, corpus: str) -> bool:
    """非测试代码里有无该表名字面引用 (读/写/DDL)。有=不可删 (保守)。"""
    return table in corpus


def run(alias: str, execute: bool) -> int:
    db = _db_path(alias)
    conn = duck_connect(str(db), read_only=not execute)
    try:
        zero = _zero_row_tables(conn)
        corpus = _load_code_corpus()
        dead = [t for t in zero if not _is_referenced(t, corpus)]
        protected = [t for t in zero if t not in dead]

        print(f"=== 死表审计 db={alias} ({db.relative_to(REPO)}) ===")
        print(f"  0 行表: {len(zero)} | 死表 (0行+0引用): {len(dead)} | 受保护 (空但有引用/writer): {len(protected)}")
        print(f"\n  死表候选 (可 DROP):")
        for t in dead:
            print(f"    DEAD  {t}")
        if protected:
            print(f"\n  受保护 (空但代码有引用, 不删):")
            for t in protected[:20]:
                print(f"    KEEP  {t}")

        if not execute:
            print(f"\n  DRY-RUN: {len(dead)} 张死表可删。--execute 执行 DROP (事务+CHECKPOINT)。")
            return 0
        if not dead:
            print("\n  无死表可删。")
            return 0

        conn.execute("BEGIN TRANSACTION")
        try:
            for t in dead:
                conn.execute(f'DROP TABLE IF EXISTS "{t}"')
            conn.execute("COMMIT")
        except BaseException:
            try: conn.execute("ROLLBACK")  # best-effort; 原异常下方 raise
            except Exception: pass
            raise
        conn.execute("CHECKPOINT")
        # 验证: 死表已不在
        remain = [t for t in dead if conn.execute(
            "SELECT count(*) FROM information_schema.tables WHERE table_name=?", [t]
        ).fetchone()[0] > 0]
        if remain:
            print(f"\nFAIL: {len(remain)} 张未删除: {remain}", file=sys.stderr)
            return 5
        print(f"\n  已 DROP {len(dead)} 张死表 + CHECKPOINT。验证: 0 残留。")
    finally:
        conn.close()
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default="smartmoney", help="database_manifest 库 alias")
    ap.add_argument("--execute", action="store_true", help="真删 (默认 dry-run)")
    args = ap.parse_args()
    sys.exit(run(args.db, args.execute))


if __name__ == "__main__":
    main()
