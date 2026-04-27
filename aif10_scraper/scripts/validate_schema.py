"""验证 schema/ 下所有 .sql 都能在 DuckDB 中正确 CREATE TABLE.

每条 DDL 都在内存 DuckDB 实例里执行一次, 失败列出.

Usage:
    python scripts/validate_schema.py
"""
from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SCHEMA_DIR = ROOT / "schema"


def main() -> int:
    try:
        import duckdb
    except ImportError:
        print("⚠ 需要 duckdb: pip install duckdb")
        return 2

    files = sorted(SCHEMA_DIR.glob("*.sql"))
    if not files:
        print(f"schema/ 目录下没有 .sql 文件")
        return 1

    print(f"=== 验证 {len(files)} 个 DDL 文件 ===")
    failures: list[tuple[str, str]] = []

    for f in files:
        sql = f.read_text(encoding="utf-8")
        con = duckdb.connect(":memory:")
        try:
            con.execute(sql)
        except Exception as exc:
            failures.append((f.name, str(exc)[:200]))
            print(f"  ✗ {f.name}: {str(exc)[:120]}")
            con.close()
            continue
        # 验证表确实存在
        tables = con.execute("SHOW TABLES").fetchall()
        if not tables:
            failures.append((f.name, "DDL 执行成功但没有表"))
            print(f"  ✗ {f.name}: 无表")
        con.close()

    print()
    print(f"=== 结果 ===")
    print(f"  成功: {len(files) - len(failures)}/{len(files)}")
    if failures:
        print(f"  失败: {len(failures)}")
        return 1
    print(f"  全部 ✓")
    return 0


if __name__ == "__main__":
    sys.exit(main())
