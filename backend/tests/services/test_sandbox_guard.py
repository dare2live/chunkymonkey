"""沙盒边界守卫单测 (2026-06-17, 红→绿: guard 挡 rw 开主库)。"""
from __future__ import annotations

import duckdb
import pytest

from services import sandbox_guard
from services.database_manifest import get_database_manifest


@pytest.fixture(autouse=True)
def _restore_guard():
    yield
    sandbox_guard.disable_sandbox_guard()


def _market_path() -> str:
    return str(get_database_manifest().path_for("market"))


def test_guard_blocks_rw_open_of_main_db():
    """guard ON 后 read_write 打开主库 → SandboxBoundaryError (核心硬门)。"""
    sandbox_guard.enable_sandbox_guard()
    with pytest.raises(sandbox_guard.SandboxBoundaryError):
        duckdb.connect(_market_path())  # 默认 read_write
    with pytest.raises(sandbox_guard.SandboxBoundaryError):
        duckdb.connect(_market_path(), read_only=False)


def test_guard_allows_read_only_open_of_main_db():
    """read_only 打开主库 → 放行 (探索读主库正路)。"""
    sandbox_guard.enable_sandbox_guard()
    con = duckdb.connect(_market_path(), read_only=True)
    try:
        n = con.execute("SELECT COUNT(*) FROM price_kline_qfq_tushare").fetchone()[0]
        assert n > 0
    finally:
        con.close()


def test_read_only_main_helper_works_and_is_read_only():
    sandbox_guard.enable_sandbox_guard()
    con = sandbox_guard.read_only_main("market")
    try:
        con.execute("SELECT 1").fetchone()
        with pytest.raises(Exception):
            con.execute("CREATE TABLE _should_fail_ro (x INT)")  # read_only 连接禁写
    finally:
        con.close()


def test_guard_off_by_default_no_side_effect():
    """未 enable 时主流程不受影响 (guard opt-in)。"""
    # disable (fixture 也会), 确认 in-memory rw 正常
    sandbox_guard.disable_sandbox_guard()
    con = duckdb.connect(":memory:")
    try:
        con.execute("CREATE TABLE t (x INT)")
        con.execute("INSERT INTO t VALUES (1)")
        assert con.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 1
    finally:
        con.close()


def test_sandbox_scratch_is_writable_and_under_sandbox(tmp_path):
    sandbox_guard.enable_sandbox_guard()
    con = sandbox_guard.sandbox_scratch("_unittest_probe")
    try:
        con.execute("CREATE TABLE t (x INT)")
        con.execute("INSERT INTO t VALUES (1),(2)")
        assert con.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 2
    finally:
        con.close()
    # 清理 probe scratch
    import shutil
    from services.sandbox_guard import _REPO_ROOT
    shutil.rmtree(_REPO_ROOT / "sandbox" / "_unittest_probe", ignore_errors=True)
