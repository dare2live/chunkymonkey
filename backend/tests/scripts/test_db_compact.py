"""db_compact 保真缩盘工具测试 — 守 DETACH-src 回归 + 保真 (DDL/PK/索引/视图/行数)。

核心回归: 验证前必须 DETACH src, 否则 information_schema/duckdb_constraints/duckdb_indexes
跨 attach 库双计 (新+旧=2x) → 对账假失败 return 5。本测试断言 run() return 0 即守住该回归。
"""
from __future__ import annotations

import importlib
from pathlib import Path

import pytest

import sys

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "backend"))

from services.duck_adapter import connect as duck_connect  # noqa: E402

db_compact = importlib.import_module("backend.scripts.db_compact") if False else None
# 脚本以路径方式导入 (backend/scripts 非包)
import importlib.util  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "db_compact", REPO / "backend" / "scripts" / "db_compact.py"
)
db_compact = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(db_compact)


def _build_src(path: Path) -> tuple[int, int]:
    """建迷你源库: 2 表 (一带 PK), 1 索引, 1 视图。返回 (a 行数, b 行数)。"""
    c = duck_connect(str(path), read_only=False)
    try:
        c.execute("CREATE TABLE a (id INTEGER PRIMARY KEY, v DOUBLE)")
        c.execute("INSERT INTO a SELECT i, i*1.5 FROM range(100) t(i)")
        c.execute("CREATE TABLE b (k VARCHAR, n INTEGER)")
        c.execute("INSERT INTO b SELECT 'x'||i, i FROM range(50) t(i)")
        c.execute("CREATE INDEX idx_b_k ON b(k)")
        c.execute("CREATE VIEW v_ab AS SELECT a.id, b.n FROM a JOIN b ON a.id = b.n")
        c.execute("CHECKPOINT")
    finally:
        c.close()
    return 100, 50


def test_compact_preserves_structure_and_swaps(tmp_path, monkeypatch):
    src = tmp_path / "testdb.duckdb"
    na, nb = _build_src(src)

    # 让 _db_path 把 alias 映射到我们的临时源库
    monkeypatch.setattr(db_compact, "_db_path", lambda alias: src)

    rc = db_compact.run("testdb", execute=True)
    assert rc == 0, "缩盘 run 应 return 0 (return 5 = DETACH-src 回归致对账双计假失败)"

    # 换名后: src 路径 = 缩后库, bak 存在
    bak = src.with_name("testdb_precompact_bak.duckdb")
    assert bak.exists(), "旧库应留 _precompact_bak"
    assert src.exists(), "缩后库应换名回原路径"

    # 保真核对: 表/视图/约束/索引/行数 全保留
    c = duck_connect(str(src), read_only=True)
    try:
        tabs = c.execute(
            "SELECT count(*) FROM information_schema.tables WHERE table_schema='main' AND table_type='BASE TABLE'"
        ).fetchone()[0]
        views = c.execute(
            "SELECT count(*) FROM information_schema.tables WHERE table_schema='main' AND table_type='VIEW'"
        ).fetchone()[0]
        cons = c.execute("SELECT count(*) FROM duckdb_constraints()").fetchone()[0]
        idx = c.execute("SELECT count(*) FROM duckdb_indexes()").fetchone()[0]
        ra = c.execute('SELECT count(*) FROM a').fetchone()[0]
        rb = c.execute('SELECT count(*) FROM b').fetchone()[0]
        rv = c.execute('SELECT count(*) FROM v_ab').fetchone()[0]
    finally:
        c.close()

    assert tabs == 2, f"表数应保留 2, got {tabs}"
    assert views == 1, f"视图应重建保留 1, got {views}"
    assert cons >= 1, f"PK 约束应保留 (>=1), got {cons}"
    assert idx >= 1, f"索引应重建保留 (>=1), got {idx}"
    assert (ra, rb) == (na, nb), f"行数应全等, got a={ra} b={rb}"
    assert rv > 0, "视图应可查 (重建成功)"


def test_compact_dry_run_no_swap(tmp_path, monkeypatch):
    src = tmp_path / "testdb2.duckdb"
    _build_src(src)
    monkeypatch.setattr(db_compact, "_db_path", lambda alias: src)

    rc = db_compact.run("testdb2", execute=False)
    assert rc == 0
    # dry-run 不产生新库/不换名
    assert not src.with_name("testdb2_compact.duckdb").exists()
    assert not src.with_name("testdb2_precompact_bak.duckdb").exists()
