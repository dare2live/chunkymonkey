import ast
import json
from pathlib import Path

import pytest

from services import analytics, duck_adapter
from services.duck_adapter import connect
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
    allowed = {
        "services/analytics.py",
        "services/duck_adapter.py",
        "scripts/backtest_etf_strategies.py",
        "scripts/build_alpha158_duck.py",
        "scripts/build_etf_sector_rotation.py",
        "scripts/build_formula_signals_history.py",
        "scripts/build_stage_formula_fitness.py",
        "scripts/migrate_holders_to_tdxhub.py",
        "scripts/build_signal_context.py",
        "scripts/analyze_macd_feature_buckets.py",
        "scripts/build_stock_formula_optuna.py",
        "scripts/optuna_per_stock_macd.py",
        "scripts/validate_sentiment_ic.py",
        "scripts/validate_exclusion_rules.py",
        "scripts/build_stock_formula_optuna_v2.py",
        "scripts/optimize_per_stock_strategy.py",
        "scripts/optimize_per_stock_stage_strategy.py",
        "scripts/portfolio_backtest.py",
    }
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
