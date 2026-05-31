import sys
from pathlib import Path
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.duck_adapter import connect as duck_connect
from main import app
from routers import updater, updater_reset


client = TestClient(app)


DERIVED_RESET_TABLES = [table_name for _, table_name in updater_reset.DERIVED_RESET_TABLES]


INDUSTRY_RESET_TABLES = [
    "fact_setup_snapshot",
    "mart_current_relationship",
    "mart_institution_profile",
    "mart_institution_industry_stat",
    "mart_stock_trend",
    "mart_sector_momentum",
    "dim_stock_industry_context_latest",
    "dim_stock_quality_latest",
    "dim_stock_stage_latest",
    "dim_stock_turtle_latest",
    "fact_stock_archetype",
    "dim_stock_archetype_latest",
    "step_status",
]


PRESERVED_TABLES = [
    "fact_institution_event",
]


class _ConnStub:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


def _prepare_test_db(db_path: Path) -> None:
    conn = duck_connect(str(db_path))
    try:
        for table_name in INDUSTRY_RESET_TABLES + PRESERVED_TABLES:
            conn.execute(f"CREATE TABLE {table_name} (id INTEGER)")
            conn.execute(f"INSERT INTO {table_name} (id) VALUES (1)")
        conn.commit()
    finally:
        conn.close()


def test_build_reset_derived_response_closes_conn(monkeypatch):
    conn = _ConnStub()

    def get_conn(timeout: int = 0):
        assert timeout == 120
        return conn

    def build_payload(active_conn):
        assert active_conn is conn
        return {"ok": True, "counts": {"profiles": 0}}

    monkeypatch.setattr(updater_reset, "build_reset_derived_payload", build_payload)

    payload = updater_reset.build_reset_derived_response(get_conn)

    assert payload == {"ok": True, "counts": {"profiles": 0}}
    assert conn.closed is True


def test_build_reset_industry_response_closes_conn(monkeypatch):
    conn = _ConnStub()

    def get_conn(timeout: int = 0):
        assert timeout == 120
        return conn

    def build_payload(active_conn):
        assert active_conn is conn
        return {"ok": True, "counts": {"industry_stat": 0}}

    monkeypatch.setattr(updater_reset, "build_reset_industry_payload", build_payload)

    payload = updater_reset.build_reset_industry_response(get_conn)

    assert payload == {"ok": True, "counts": {"industry_stat": 0}}
    assert conn.closed is True


def _conn_factory(db_path: Path):
    def _open_conn(timeout: int = 120):
        return duck_connect(str(db_path), timeout=timeout)

    return _open_conn


def test_reset_tables_reports_missing_tables(tmp_path):
    db_path = tmp_path / "reset-missing.duckdb"
    conn = duck_connect(str(db_path))
    try:
        conn.execute("CREATE TABLE existing_table (id INTEGER)")
        conn.execute("INSERT INTO existing_table (id) VALUES (1), (2)")
        conn.commit()

        counts, missing_tables = updater._reset_tables(
            conn,
            [
                ("existing", "existing_table"),
                ("missing", "missing_table"),
            ],
        )

        assert counts == {"existing": 2, "missing": 0}
        assert missing_tables == ["missing_table"]
        remaining = conn.execute("SELECT COUNT(*) FROM existing_table").fetchone()[0]
        assert remaining == 0
    finally:
        conn.close()


def test_reset_derived_clears_derived_tables(tmp_path, monkeypatch):
    db_path = tmp_path / "derived-reset.duckdb"
    conn = duck_connect(str(db_path))
    try:
        for table_name in DERIVED_RESET_TABLES + ["inst_holdings"]:
            conn.execute(f"CREATE TABLE {table_name} (id INTEGER)")
            conn.execute(f"INSERT INTO {table_name} (id) VALUES (1)")
        conn.commit()
    finally:
        conn.close()

    monkeypatch.setattr("routers.updater.get_conn", _conn_factory(db_path))

    response = client.post("/api/inst/update/reset-derived")
    assert response.status_code == 200

    payload = response.json()
    assert payload["ok"] is True
    assert payload["counts"]["profiles"] == 1
    assert payload["missing_tables"] == []

    conn = duck_connect(str(db_path))
    try:
        for table_name in DERIVED_RESET_TABLES:
            remaining = conn.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0]
            assert remaining == 0, table_name

        preserved = conn.execute("SELECT COUNT(*) FROM inst_holdings").fetchone()[0]
        assert preserved == 1
    finally:
        conn.close()


def test_reset_industry_derived_clears_industry_dependent_tables(tmp_path, monkeypatch):
    db_path = tmp_path / "industry-reset.duckdb"
    _prepare_test_db(db_path)

    monkeypatch.setattr("routers.updater.get_conn", _conn_factory(db_path))
    monkeypatch.setattr("routers.updater._is_running", False)

    response = client.post("/api/inst/update/reset-industry-derived?restart_smart=false")
    assert response.status_code == 200

    payload = response.json()
    assert payload["ok"] is True
    assert payload["counts"]["profiles"] == 1
    assert payload["missing_tables"] == []

    conn = duck_connect(str(db_path))
    try:
        for table_name in INDUSTRY_RESET_TABLES:
            remaining = conn.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0]
            assert remaining == 0, table_name

        for table_name in PRESERVED_TABLES:
            remaining = conn.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0]
            assert remaining == 1, table_name
    finally:
        conn.close()


def test_reset_industry_derived_can_chain_to_smart_update(tmp_path, monkeypatch):
    db_path = tmp_path / "industry-reset-smart.duckdb"
    _prepare_test_db(db_path)

    monkeypatch.setattr("routers.updater.get_conn", _conn_factory(db_path))
    monkeypatch.setattr("routers.updater._is_running", False)
    smart_update = AsyncMock(return_value={"ok": True, "steps": 3, "step_ids": ["sync_industry", "build_current_rel", "build_trends"]})
    monkeypatch.setattr("routers.updater.smart_update", smart_update)

    response = client.post("/api/inst/update/reset-industry-derived")
    assert response.status_code == 200

    payload = response.json()
    assert payload["ok"] is True
    assert "并启动智能更新" in payload["message"]
    assert payload["smart_update"]["step_ids"] == ["sync_industry", "build_current_rel", "build_trends"]
    smart_update.assert_awaited_once()
