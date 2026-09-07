import sys
import time
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from services import db
from services import duck_adapter
from services.db import get_enabled_modules
from services.database_manifest import get_database_manifest
from services.duck_adapter import connect as duck_connect
from services.schema_layer_filter import keep_stmt


def test_default_db_path_comes_from_database_manifest():
    manifest = get_database_manifest()

    assert db.DB_PATH == manifest.path_for("smartmoney")
    assert db.DB_DIR == manifest.path_for("smartmoney").parent


def test_get_enabled_modules():
    # 内存 DuckDB, 模拟 app_settings 配置
    conn = duck_connect(":memory:")
    conn.execute("CREATE TABLE app_settings (key TEXT, value TEXT, updated_at TEXT)")
    conn.execute("INSERT INTO app_settings VALUES ('module_qlib_enabled', '1', '2026')")
    conn.execute("INSERT INTO app_settings VALUES ('module_akquant_enabled', '0', '2026')")
    conn.execute("INSERT INTO app_settings VALUES ('module_etf_enabled', '0', '2026')")

    modules = get_enabled_modules(conn)
    assert modules["qlib"] is True
    assert modules["akquant"] is False
    assert modules["etf"] is False

    conn.close()


def test_init_db_sets_module_defaults_without_legacy_migration_marker():
    original_dir = db.DB_DIR
    original_path = db.DB_PATH

    with TemporaryDirectory() as tmpdir:
        db.DB_DIR = Path(tmpdir)
        db.DB_PATH = db.DB_DIR / "smartmoney.duckdb"
        try:
            db.init_db()

            conn = duck_connect(str(db.DB_PATH))
            try:
                rows = conn.execute(
                    "SELECT key, value FROM app_settings WHERE key IN ("
                    "'module_akquant_enabled'"
                    ")"
                ).fetchall()
                settings = {row[0]: row[1] for row in rows}

                assert settings["module_akquant_enabled"] == "0"
            finally:
                conn.close()
        finally:
            db.DB_DIR = original_dir
            db.DB_PATH = original_path


def test_duck_connect_retries_file_lock_conflict(monkeypatch, tmp_path):
    calls = []
    real_connect = duck_adapter.duckdb.connect

    def fake_connect(db_path, read_only=False):
        calls.append((db_path, read_only))
        if len(calls) == 1:
            raise duck_adapter.duckdb.IOException(
                "IO Error: Could not set lock on file \"fixture.duckdb\": Conflicting lock"
            )
        return real_connect(":memory:", read_only=read_only)

    monkeypatch.setattr(duck_adapter.duckdb, "connect", fake_connect)
    monkeypatch.setattr(duck_adapter.time, "sleep", lambda _seconds: None)

    conn = duck_connect(str(tmp_path / "retry.duckdb"), timeout=1)
    try:
        assert len(calls) == 2
    finally:
        conn.close()


def test_keep_stmt_strips_leading_comment_before_target_check():
    """keep_stmt 必须剥离前导 SQL 注释再判 target — 否则 filter_schema_sql 按 ; split 时,
    无分号注释会粘到下一条语句, segment 以 -- 开头致 CREATE/ALTER target 识别失败 (target=None),
    退役表语句被误判 keep 而执行。(2026-06-28 实证: fact_institution_event 退役注释粘
    fact_setup_snapshot 索引, 致 init_db 在不存在的退役表上 CREATE INDEX 报错)。"""
    keep = {"live_tbl"}
    wiped = {"wiped_tbl"}
    # 注释粘连退役表索引 → 必须过滤 (False)
    assert keep_stmt("-- retire comment line\nCREATE INDEX idx_x ON wiped_tbl(c)", keep, wiped) is False
    # 注释粘连活层表索引 → 保留 (True)
    assert keep_stmt("-- comment\nCREATE INDEX idx_y ON live_tbl(c)", keep, wiped) is True
    # 纯注释 segment → 不执行 (False)
    assert keep_stmt("-- pure comment only", keep, wiped) is False
    # 无注释退役表索引 (baseline 路径) → 过滤 (False)
    assert keep_stmt("CREATE INDEX idx_z ON wiped_tbl(c)", keep, wiped) is False


# ── 只读审计连接 (2026-09-07 加) ──────────────────────────────────────────────
#
# 背景: 全仓 6 个 check_/audit 脚本各自写 duck_connect(path, read_only=True), 都没传
# timeout, 于是全都继承写路径的 30 秒重试截止。写者持锁时 (日更/回填) 它们每个库干等
# 30 秒才失败 —— 结论与等 1 秒时一模一样。实测 moth assert 33.2s 里 30.2s 是单条
# data-layer-integrity 断言, 而它 user CPU 只有 2.4s。改用共享入口后 30.21s -> 3.13s。


def test_audit_lock_timeout_defaults_and_env_override(monkeypatch):
    from services import duck_adapter as da

    monkeypatch.delenv(da.AUDIT_LOCK_TIMEOUT_ENV, raising=False)
    assert da.audit_lock_timeout() == da.DEFAULT_AUDIT_LOCK_TIMEOUT

    monkeypatch.setenv(da.AUDIT_LOCK_TIMEOUT_ENV, "7")
    assert da.audit_lock_timeout() == 7

    # 空串按未设置处理, 不当成 0 (0 会让审计连接完全不重试)。
    monkeypatch.setenv(da.AUDIT_LOCK_TIMEOUT_ENV, "  ")
    assert da.audit_lock_timeout() == da.DEFAULT_AUDIT_LOCK_TIMEOUT


def test_audit_lock_timeout_rejects_garbage(monkeypatch):
    """坏值必须报错, 不许静默退回默认 —— 那会让「我设了但没生效」无法察觉。"""
    from services import duck_adapter as da

    monkeypatch.setenv(da.AUDIT_LOCK_TIMEOUT_ENV, "abc")
    with pytest.raises(RuntimeError, match=da.AUDIT_LOCK_TIMEOUT_ENV):
        da.audit_lock_timeout()

    monkeypatch.setenv(da.AUDIT_LOCK_TIMEOUT_ENV, "-1")
    with pytest.raises(RuntimeError, match=da.AUDIT_LOCK_TIMEOUT_ENV):
        da.audit_lock_timeout()


def test_audit_connect_passes_audit_timeout_and_read_only_through(monkeypatch):
    """audit_connect 必须把 audit_lock_timeout() 与 read_only=True 传给 connect。

    2026-09-07 —— 本测试的第一版是「真开一个写连接再 audit_connect, 断言等待 < 10 秒」,
    它**抓不到**要抓的东西: 把 audit_connect 里的 timeout 硬编码回 30, 测试照样绿,
    且只跑了 0.05 秒。原因是同进程再开同一个文件抛的是
    ConnectionException("Can't open a connection to same database file with a
    different configuration") —— 立刻失败, 压根没进锁重试路径,
    而 pytest.raises(Exception) 宽到任何异常都算通过。

    真锁竞争要另起进程 (test_check_lineage_catalog_drift.py 的 _rw_lock_holder 那样),
    那是集成测试的成本。这里改成直接验参数透传, 判据窄而准。
    """
    from services import duck_adapter as da

    seen = {}

    def fake_connect(db_path, timeout=30, read_only=False, attach=None):
        seen.update(db_path=db_path, timeout=timeout, read_only=read_only, attach=attach)
        return object()

    monkeypatch.setattr(da, "connect", fake_connect)
    monkeypatch.setenv(da.AUDIT_LOCK_TIMEOUT_ENV, "4")

    da.audit_connect("/tmp/whatever.duckdb")

    assert seen["read_only"] is True, "审计连接必须只读 (项目规则 6)"
    assert seen["timeout"] == 4, (
        f"审计连接的锁等待是 {seen['timeout']}, 没走 audit_lock_timeout() —— "
        "写路径的 30 秒会让日更期间每个库白等 30 秒"
    )
