import ast
import json
from pathlib import Path

import pytest

from services import analytics, duck_adapter
from services.duck_adapter import connect
from services.duckdb_connect_policy import DEFAULT_DUCKDB_CONNECT_POLICY
from services.pipeline_manifest import ensure_pipeline_manifest_schema, record_pipeline_run


pytestmark = pytest.mark.integration


def _create_duck_file(path: Path, ddl: str, rows_sql: str = "") -> None:
    conn = connect(str(path))
    try:
        conn.executescript(ddl)
        if rows_sql:
            conn.executescript(rows_sql)
    finally:
        conn.close()


def test_analytics_connection_attaches_market_and_etf_read_only(monkeypatch, tmp_path):
    smart_db = tmp_path / "smart.duckdb"
    market_db = tmp_path / "market.duckdb"
    etf_db = tmp_path / "etf.duckdb"
    _create_duck_file(
        smart_db,
        "CREATE TABLE smart_rows (id INTEGER);",
        "INSERT INTO smart_rows VALUES (1);",
    )
    _create_duck_file(
        market_db,
        "CREATE TABLE market_rows (id INTEGER);",
        "INSERT INTO market_rows VALUES (10);",
    )
    _create_duck_file(
        etf_db,
        "CREATE TABLE etf_rows (id INTEGER);",
        "INSERT INTO etf_rows VALUES (20);",
    )
    monkeypatch.setattr(analytics, "SMART_DB", str(smart_db))
    monkeypatch.setattr(analytics, "MARKET_DB", str(market_db))
    monkeypatch.setattr(analytics, "ETF_DB", str(etf_db))

    with analytics.duck_connection(writable=True) as con:
        con.execute("INSERT INTO smart_rows VALUES (2)")
        assert con.execute("SELECT SUM(id) FROM smart_rows").fetchone()[0] == 3
        assert con.execute("SELECT SUM(id) FROM market.market_rows").fetchone()[0] == 10
        assert con.execute("SELECT SUM(id) FROM etf.etf_rows").fetchone()[0] == 20

        with pytest.raises(Exception):
            con.execute("INSERT INTO market.market_rows VALUES (11)")
        with pytest.raises(Exception):
            con.execute("INSERT INTO etf.etf_rows VALUES (21)")


def test_analytics_two_read_only_connections_query_concurrently(monkeypatch, tmp_path):
    smart_db = tmp_path / "smart.duckdb"
    market_db = tmp_path / "market.duckdb"
    etf_db = tmp_path / "etf.duckdb"
    _create_duck_file(smart_db, "CREATE TABLE smart_rows (id INTEGER);", "INSERT INTO smart_rows VALUES (1);")
    _create_duck_file(market_db, "CREATE TABLE market_rows (id INTEGER);", "INSERT INTO market_rows VALUES (10);")
    _create_duck_file(etf_db, "CREATE TABLE etf_rows (id INTEGER);", "INSERT INTO etf_rows VALUES (20);")
    monkeypatch.setattr(analytics, "SMART_DB", str(smart_db))
    monkeypatch.setattr(analytics, "MARKET_DB", str(market_db))
    monkeypatch.setattr(analytics, "ETF_DB", str(etf_db))

    with analytics.duck_connection() as first, analytics.duck_connection() as second:
        assert first.execute("SELECT COUNT(*) FROM smart_rows").fetchone()[0] == 1
        assert second.execute("SELECT COUNT(*) FROM smart_rows").fetchone()[0] == 1
        assert first.execute("SELECT COUNT(*) FROM market.market_rows").fetchone()[0] == 1
        assert second.execute("SELECT COUNT(*) FROM etf.etf_rows").fetchone()[0] == 1


def test_analytics_missing_optional_attach_keeps_smart_connection_usable(monkeypatch, tmp_path):
    smart_db = tmp_path / "smart.duckdb"
    _create_duck_file(
        smart_db,
        "CREATE TABLE smart_rows (id INTEGER);",
        "INSERT INTO smart_rows VALUES (1);",
    )
    monkeypatch.setattr(analytics, "SMART_DB", str(smart_db))
    monkeypatch.setattr(analytics, "MARKET_DB", str(tmp_path / "missing_market.duckdb"))
    monkeypatch.setattr(analytics, "ETF_DB", str(tmp_path / "missing_etf.duckdb"))

    with analytics.duck_connection() as con:
        assert con.execute("SELECT COUNT(*) FROM smart_rows").fetchone()[0] == 1


def test_duck_adapter_records_lock_wait_after_retry(monkeypatch, tmp_path):
    calls = []
    real_connect = duck_adapter.duckdb.connect

    def fake_connect(db_path, read_only=False):
        calls.append((db_path, read_only))
        if len(calls) == 1:
            raise duck_adapter.duckdb.IOException(
                'IO Error: Could not set lock on file "fixture.duckdb": Conflicting lock'
            )
        return real_connect(":memory:", read_only=read_only)

    monkeypatch.setattr(duck_adapter.duckdb, "connect", fake_connect)
    monkeypatch.setattr(duck_adapter.time, "sleep", lambda _seconds: None)

    conn = connect(str(tmp_path / "retry.duckdb"), timeout=1)
    try:
        assert len(calls) == 2
        assert conn.duckdb_lock_wait_s >= 0.1
        assert conn.connect_mutex_wait_s >= 0.0
        assert conn.duckdb_connect_wait_s >= conn.duckdb_lock_wait_s
        assert conn.duckdb_connect_elapsed_s >= 0.0
    finally:
        conn.close()


def test_duck_adapter_attaches_path_specs_read_only_by_default(tmp_path):
    smart_db = tmp_path / "smart.duckdb"
    market_db = tmp_path / "market.duckdb"
    _create_duck_file(smart_db, "CREATE TABLE smart_rows (id INTEGER);")
    _create_duck_file(market_db, "CREATE TABLE market_rows (id INTEGER);")

    with connect(str(smart_db), read_only=False, attach={"market": str(market_db)}) as conn:
        conn.execute("INSERT INTO smart_rows VALUES (1)")
        assert conn.execute("SELECT COUNT(*) FROM smart_rows").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM market.market_rows").fetchone()[0] == 0

        with pytest.raises(Exception):
            conn.execute("INSERT INTO market.market_rows VALUES (10)")


def test_duck_adapter_allows_explicit_writable_attach(tmp_path):
    smart_db = tmp_path / "smart.duckdb"
    side_db = tmp_path / "side.duckdb"
    _create_duck_file(smart_db, "CREATE TABLE smart_rows (id INTEGER);")
    _create_duck_file(side_db, "CREATE TABLE side_rows (id INTEGER);")

    with connect(
        str(smart_db),
        read_only=False,
        attach={"side": {"path": str(side_db), "read_only": False}},
    ) as conn:
        conn.execute("INSERT INTO side.side_rows VALUES (10)")
        assert conn.execute("SELECT SUM(id) FROM side.side_rows").fetchone()[0] == 10


def test_duck_adapter_records_attach_wait_after_retry(monkeypatch):
    calls = []

    class FakeConn:
        def execute(self, sql):
            calls.append(sql)
            if len(calls) == 1:
                raise duck_adapter.duckdb.IOException(
                    'IO Error: Could not set lock on file "fixture.duckdb": Conflicting lock'
                )
            return self

    monkeypatch.setattr(duck_adapter.time, "sleep", lambda _seconds: None)

    waited = duck_adapter.attach_with_retry(
        FakeConn(),
        "sm",
        "fixture.duckdb",
        read_only=True,
        timeout=1,
    )

    assert len(calls) == 2
    assert waited >= 0.1


def test_pipeline_manifest_records_duckdb_wait_metrics():
    conn = connect(":memory:")
    try:
        ensure_pipeline_manifest_schema(conn)
        conn.duckdb_lock_wait_s = 0.25
        conn.connect_mutex_wait_s = 0.125
        conn.duckdb_connect_wait_s = 0.375
        conn.duckdb_connect_elapsed_s = 0.5

        record_pipeline_run(
            conn,
            run_id="duck_wait_contract",
            pipeline_name="unit_pipeline",
            status="success",
            perf_summary={"rows": 3},
        )

        row = conn.execute(
            "SELECT perf_summary_json FROM mart_pipeline_run_manifest WHERE run_id = 'duck_wait_contract'"
        ).fetchone()
        perf = json.loads(row["perf_summary_json"])

        assert perf["rows"] == 3
        assert perf["duckdb_lock_wait_s"] == 0.25
        assert perf["connect_mutex_wait_s"] == 0.125
        assert perf["duckdb_connect_wait_s"] == 0.375
        assert perf["duckdb_connect_elapsed_s"] == 0.5
    finally:
        conn.close()


def test_production_code_keeps_raw_duckdb_connect_calls_allowlisted():
    repo = Path(__file__).resolve().parents[2]
    allowed = set(DEFAULT_DUCKDB_CONNECT_POLICY.allowed_raw_connect_paths)
    found = set()

    for base in [repo / "services", repo / "scripts"]:
        for path in base.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                if (
                    isinstance(func, ast.Attribute)
                    and func.attr == "connect"
                    and isinstance(func.value, ast.Name)
                    and func.value.id == "duckdb"
                ):
                    found.add(path.relative_to(repo).as_posix())

    assert found == allowed
