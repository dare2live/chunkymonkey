import sys
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services import db
from services import duck_adapter
from services.db import get_enabled_modules
from services.database_manifest import get_database_manifest
from services.duck_adapter import connect as duck_connect


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
                    "'module_etf_enabled', 'module_akquant_enabled'"
                    ")"
                ).fetchall()
                settings = {row[0]: row[1] for row in rows}

                assert settings["module_etf_enabled"] == "1"
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
