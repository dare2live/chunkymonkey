from __future__ import annotations

import json

import duckdb

from scripts.backfill_paper_sim_cache_metadata import (
    LEGACY_HASH_NAMESPACE,
    backfill,
    canonical_json_text,
    legacy_snapshot_hash,
    param_diff_json,
)
from services.paper_sim.ddl import apply_schema_migration
from services.paper_sim.sim_cache import check_cache


class NonClosingConn:
    def __init__(self, conn):
        self._conn = conn

    def __getattr__(self, name):
        return getattr(self._conn, name)

    def close(self):
        return None


def _create_backfill_kpi_table(conn) -> None:
    conn.execute(
        """
        CREATE TABLE mart_paper_sim_kpi (
            sim_run_id VARCHAR PRIMARY KEY,
            variant VARCHAR,
            period_start VARCHAR,
            period_end VARCHAR,
            n_days INTEGER,
            config_snapshot VARCHAR,
            annual_return DOUBLE,
            built_at TIMESTAMP
        )
        """
    )
    apply_schema_migration(conn)


def _insert_kpi(
    conn,
    *,
    sim_run_id: str,
    max_positions: int,
    built_at: str,
    sim_config_hash: str | None = None,
) -> None:
    snapshot = json.dumps(
        {"portfolio": {"max_positions": max_positions}, "swap": {"enabled": True}}
    )
    conn.execute(
        """
        INSERT INTO mart_paper_sim_kpi
        (sim_run_id, variant, period_start, period_end, n_days, config_snapshot,
         annual_return, built_at, sim_config_hash)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            sim_run_id,
            "unit",
            "2025-01-02",
            "2026-05-19",
            330,
            snapshot,
            0.1,
            built_at,
            sim_config_hash,
        ],
    )


def test_canonical_json_text_stabilizes_key_order():
    a = '{"swap":{"enabled":true},"portfolio":{"max_positions":5}}'
    b = '{"portfolio":{"max_positions":5},"swap":{"enabled":true}}'

    assert canonical_json_text(a) == canonical_json_text(b)


def test_legacy_snapshot_hash_is_namespaced_and_deterministic():
    kwargs = {
        "config_snapshot": '{"portfolio":{"max_positions":5},"swap":{"enabled":true}}',
        "variant": "champion",
        "period_start": "2023-01-03",
        "period_end": "2026-05-19",
        "n_days": 820,
    }

    h1 = legacy_snapshot_hash(**kwargs)
    h2 = legacy_snapshot_hash(**kwargs)

    assert h1 == h2
    assert h1.startswith(f"{LEGACY_HASH_NAMESPACE}:")


def test_param_diff_json_reports_changed_top_level_sections():
    parent = {
        "portfolio": {"max_positions": 5, "min_cash_pct": 0.3},
        "swap": {"enabled": True, "max_swaps_per_day": 2},
    }
    current = {
        "portfolio": {"max_positions": 10, "min_cash_pct": 0.3},
        "swap": {"enabled": True, "max_swaps_per_day": 1},
    }

    diff = json.loads(param_diff_json(json.dumps(current), json.dumps(parent)) or "{}")

    assert diff == {
        "portfolio": {"max_positions": [5, 10]},
        "swap": {"max_swaps_per_day": [2, 1]},
    }


def test_param_diff_json_returns_none_for_no_change():
    snapshot = '{"portfolio":{"max_positions":5},"swap":{"enabled":true}}'

    assert param_diff_json(snapshot, snapshot) is None


def test_backfill_dry_run_does_not_write_memory_duckdb(monkeypatch):
    conn = duckdb.connect(":memory:")
    try:
        _create_backfill_kpi_table(conn)
        _insert_kpi(
            conn,
            sim_run_id="sim_existing",
            max_positions=3,
            built_at="2026-05-19 09:00:00",
            sim_config_hash="keep_hash",
        )
        _insert_kpi(
            conn,
            sim_run_id="sim_missing",
            max_positions=5,
            built_at="2026-05-20 09:00:00",
        )
        monkeypatch.setattr(
            "scripts.backfill_paper_sim_cache_metadata.get_conn",
            lambda: NonClosingConn(conn),
        )

        result = backfill(apply=False)

        rows = conn.execute(
            """
            SELECT sim_run_id, sim_config_hash
              FROM mart_paper_sim_kpi
             ORDER BY built_at
            """
        ).fetchall()
        assert result["mode"] == "dry-run"
        assert result["candidate_rows"] == 1
        assert result["updated_rows"] == 0
        assert rows == [("sim_existing", "keep_hash"), ("sim_missing", None)]
    finally:
        conn.close()


def test_backfill_apply_only_updates_rows_missing_hash(monkeypatch):
    conn = duckdb.connect(":memory:")
    try:
        _create_backfill_kpi_table(conn)
        _insert_kpi(
            conn,
            sim_run_id="sim_old_missing",
            max_positions=5,
            built_at="2026-05-19 09:00:00",
        )
        _insert_kpi(
            conn,
            sim_run_id="sim_existing",
            max_positions=7,
            built_at="2026-05-20 09:00:00",
            sim_config_hash="keep_hash",
        )
        _insert_kpi(
            conn,
            sim_run_id="sim_new_missing",
            max_positions=10,
            built_at="2026-05-21 09:00:00",
        )
        monkeypatch.setattr(
            "scripts.backfill_paper_sim_cache_metadata.get_conn",
            lambda: NonClosingConn(conn),
        )

        result = backfill(apply=True)

        rows = conn.execute(
            """
            SELECT sim_run_id, sim_config_hash, parent_sim_run_id, param_diff_json
              FROM mart_paper_sim_kpi
             ORDER BY built_at
            """
        ).fetchall()
        by_id = {row[0]: row for row in rows}
        assert result["mode"] == "apply"
        assert result["candidate_rows"] == 2
        assert result["updated_rows"] == 2
        assert by_id["sim_existing"][1:] == ("keep_hash", None, None)
        assert by_id["sim_old_missing"][1].startswith(f"{LEGACY_HASH_NAMESPACE}:")
        assert by_id["sim_old_missing"][2] is None
        assert by_id["sim_old_missing"][3] is None
        assert by_id["sim_new_missing"][1].startswith(f"{LEGACY_HASH_NAMESPACE}:")
        assert by_id["sim_new_missing"][2] == "sim_old_missing"
        assert json.loads(by_id["sim_new_missing"][3]) == {"portfolio": {"max_positions": [5, 10]}}
    finally:
        conn.close()


def test_check_cache_returns_latest_built_at_row_memory_duckdb():
    conn = duckdb.connect(":memory:")
    try:
        _create_backfill_kpi_table(conn)
        _insert_kpi(
            conn,
            sim_run_id="sim_old",
            max_positions=5,
            built_at="2026-05-19 09:00:00",
            sim_config_hash="same_hash",
        )
        _insert_kpi(
            conn,
            sim_run_id="sim_new",
            max_positions=10,
            built_at="2026-05-21 09:00:00",
            sim_config_hash="same_hash",
        )

        row = check_cache(conn, "same_hash")

        assert row is not None
        assert row["sim_run_id"] == "sim_new"
        assert str(row["built_at"]).startswith("2026-05-21")
    finally:
        conn.close()
