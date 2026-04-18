import sys
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services import db
from services.db import get_enabled_modules

def test_get_enabled_modules():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
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
        db.DB_PATH = db.DB_DIR / "smartmoney.db"
        try:
            db.init_db()

            conn = sqlite3.connect(str(db.DB_PATH))
            try:
                rows = conn.execute(
                    "SELECT key, value FROM app_settings WHERE key IN ("
                    "'module_qlib_enabled', 'module_etf_enabled', 'module_akquant_enabled', '_migration_qlib_default_v1'"
                    ")"
                ).fetchall()
                settings = {row[0]: row[1] for row in rows}

                assert settings["module_qlib_enabled"] == "1"
                assert settings["module_etf_enabled"] == "1"
                assert settings["module_akquant_enabled"] == "0"
                assert "_migration_qlib_default_v1" not in settings
            finally:
                conn.close()
        finally:
            db.DB_DIR = original_dir
            db.DB_PATH = original_path
