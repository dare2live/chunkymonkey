import sys
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

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
