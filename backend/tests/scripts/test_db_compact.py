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

    # 换名后: src 路径 = 缩后库。cut_db_compaction (2026-09-19): drop_bak 默认改为
    # True, 校验通过 + 换名成功后自动删 bak (--keep-bak / drop_bak=False 才保留)。
    bak = src.with_name("testdb_precompact_bak.duckdb")
    assert not bak.exists(), "默认 drop_bak=True, 校验通过后 bak 不应残留"
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


def test_compact_keep_bak_true_preserves_bak(tmp_path, monkeypatch):
    """A7: drop_bak=False (CLI --keep-bak) -> 校验通过换名后 bak 保留。"""
    src = tmp_path / "testdb3.duckdb"
    _build_src(src)
    monkeypatch.setattr(db_compact, "_db_path", lambda alias: src)

    rc = db_compact.run("testdb3", execute=True, drop_bak=False)
    assert rc == 0

    bak = src.with_name("testdb3_precompact_bak.duckdb")
    assert bak.exists(), "drop_bak=False 应保留 _precompact_bak"
    assert src.exists()


class _MutateOnClose:
    """代理: 真实读连接 close() 时, 在源文件上追加一行——用来在"读基线"与"整库
    ATTACH-copy"两个阶段之间制造一次真实的行数不齐, 而不 monkeypatch run() 内部逻辑
    本身 (A7 第三种隔离用例要的是真验证失败, 不是伪造 ok 标志位)。
    """

    def __init__(self, conn, path: Path):
        self._conn = conn
        self._path = path

    def __getattr__(self, name):
        return getattr(self._conn, name)

    def close(self):
        self._conn.close()
        extra = duck_connect(str(self._path), read_only=False)
        try:
            extra.execute("INSERT INTO a VALUES (999999, 1.0)")
            extra.execute("CHECKPOINT")
        finally:
            extra.close()


def test_compact_validation_failure_no_rename_no_bak(tmp_path, monkeypatch):
    """A7: 校验不过 (基线读取后源库又多了一行, 行数对不齐) -> 不换名、不删任何东西。"""
    src = tmp_path / "testdb4.duckdb"
    _build_src(src)
    monkeypatch.setattr(db_compact, "_db_path", lambda alias: src)

    orig_connect = db_compact.duck_connect
    state = {"patched_first_call": False}

    def _connect(path, read_only=False):
        conn = orig_connect(path, read_only=read_only)
        if read_only and not state["patched_first_call"]:
            state["patched_first_call"] = True
            return _MutateOnClose(conn, Path(path))
        return conn

    monkeypatch.setattr(db_compact, "duck_connect", _connect)

    rc = db_compact.run("testdb4", execute=True)
    assert rc == 5, "基线读完后源库又多一行 -> ATTACH-copy 阶段行数对不齐, 应 return 5"

    bak = src.with_name("testdb4_precompact_bak.duckdb")
    assert not bak.exists(), "校验不过不该产生 bak"
    assert src.exists(), "校验不过不该换名, 原路径的 src 应仍在"
    new = src.with_name("testdb4_compact.duckdb")
    if new.exists():
        # run() 校验不过时按文档不清理 new (留给人排查), 不是本测试要断言的行为,
        # 只确认它不会被误换名成 src。
        assert new.resolve() != src.resolve()


def test_execute_uses_configured_min_free_disk_gb(tmp_path, monkeypatch):
    """磁盘余量门读 backend/config/db_compaction.yaml 的 min_free_disk_gb, 不硬编码。"""
    src = tmp_path / "testdb5.duckdb"
    _build_src(src)
    monkeypatch.setattr(db_compact, "_db_path", lambda alias: src)

    class _Cfg:
        min_free_disk_gb = 10_000_000.0  # 荒谬地高 -> 磁盘余量门必定触发

    monkeypatch.setattr(db_compact, "load_db_compaction_config", lambda: _Cfg())

    rc = db_compact.run("testdb5", execute=True)
    assert rc == 6, "磁盘余量门应读配置 min_free_disk_gb (硬编码 10 时本用例测不出来)"
