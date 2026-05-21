from __future__ import annotations

from pathlib import Path
from datetime import date

import duckdb

from scripts.import_phase5_remote_predictions import import_model_predictions, import_model_predictions_from_parquet


DDL = """
CREATE TABLE {table} (
    stock_code VARCHAR,
    signal_date DATE,
    score DOUBLE,
    model_id VARCHAR
)
"""


def _create_prediction_tables(db_path: Path) -> None:
    conn = duckdb.connect(str(db_path))
    try:
        for table in ("mart_p0b_lambdamart_v6_predictions", "mart_p0b_oos_predictions"):
            conn.execute(DDL.format(table=table))
    finally:
        conn.close()


def test_import_model_predictions_replaces_one_model_id(tmp_path: Path):
    local_db = tmp_path / "local.duckdb"
    remote_db = tmp_path / "remote.duckdb"
    _create_prediction_tables(local_db)
    _create_prediction_tables(remote_db)

    local = duckdb.connect(str(local_db))
    try:
        for table in ("mart_p0b_lambdamart_v6_predictions", "mart_p0b_oos_predictions"):
            local.execute(f"INSERT INTO {table} VALUES ('000001', '2026-01-01', 1.0, 'target')")
            local.execute(f"INSERT INTO {table} VALUES ('000002', '2026-01-01', 2.0, 'other')")
    finally:
        local.close()

    remote = duckdb.connect(str(remote_db))
    try:
        for table in ("mart_p0b_lambdamart_v6_predictions", "mart_p0b_oos_predictions"):
            remote.execute(f"INSERT INTO {table} VALUES ('000003', '2026-01-02', 3.0, 'target')")
            remote.execute(f"INSERT INTO {table} VALUES ('000004', '2026-01-02', 4.0, 'target')")
    finally:
        remote.close()

    result = import_model_predictions(
        local_db=str(local_db),
        remote_db=str(remote_db),
        model_id="target",
    )

    assert result["tables"]["mart_p0b_oos_predictions"]["remote_rows"] == 2
    conn = duckdb.connect(str(local_db), read_only=True)
    try:
        for table in ("mart_p0b_lambdamart_v6_predictions", "mart_p0b_oos_predictions"):
            assert conn.execute(f"SELECT COUNT(*) FROM {table} WHERE model_id='target'").fetchone()[0] == 2
            assert conn.execute(f"SELECT COUNT(*) FROM {table} WHERE model_id='other'").fetchone()[0] == 1
    finally:
        conn.close()


def test_import_model_predictions_dry_run_does_not_write(tmp_path: Path):
    local_db = tmp_path / "local.duckdb"
    remote_db = tmp_path / "remote.duckdb"
    _create_prediction_tables(local_db)
    _create_prediction_tables(remote_db)

    remote = duckdb.connect(str(remote_db))
    try:
        remote.execute("INSERT INTO mart_p0b_oos_predictions VALUES ('000003', '2026-01-02', 3.0, 'target')")
        remote.execute("INSERT INTO mart_p0b_lambdamart_v6_predictions VALUES ('000003', '2026-01-02', 3.0, 'target')")
    finally:
        remote.close()

    result = import_model_predictions(
        local_db=str(local_db),
        remote_db=str(remote_db),
        model_id="target",
        dry_run=True,
    )

    assert result["tables"]["mart_p0b_oos_predictions"]["status"] == "dry_run"
    conn = duckdb.connect(str(local_db), read_only=True)
    try:
        assert conn.execute("SELECT COUNT(*) FROM mart_p0b_oos_predictions").fetchone()[0] == 0
    finally:
        conn.close()


def test_import_model_predictions_can_mirror_lambdamart_to_oos(tmp_path: Path):
    local_db = tmp_path / "local.duckdb"
    remote_db = tmp_path / "remote.duckdb"
    _create_prediction_tables(local_db)
    _create_prediction_tables(remote_db)

    remote = duckdb.connect(str(remote_db))
    try:
        remote.execute(
            "INSERT INTO mart_p0b_lambdamart_v6_predictions VALUES ('000003', '2026-01-02', 3.0, 'target')"
        )
    finally:
        remote.close()

    result = import_model_predictions(
        local_db=str(local_db),
        remote_db=str(remote_db),
        model_id="target",
        mirror_lambdamart_to_oos=True,
    )

    assert result["tables"]["mart_p0b_lambdamart_v6_predictions"]["local_after"] == 1
    assert result["tables"]["mart_p0b_oos_predictions"]["local_after"] == 0
    assert result["mirror_lambdamart_to_oos"]["target_after"] == 1


def test_import_model_predictions_allows_remote_column_reordering(tmp_path: Path):
    local_db = tmp_path / "local.duckdb"
    remote_db = tmp_path / "remote.duckdb"
    _create_prediction_tables(local_db)
    conn = duckdb.connect(str(remote_db))
    try:
        conn.execute(
            """
            CREATE TABLE mart_p0b_lambdamart_v6_predictions (
                model_id VARCHAR,
                score DOUBLE,
                signal_date DATE,
                stock_code VARCHAR
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE mart_p0b_oos_predictions (
                model_id VARCHAR,
                score DOUBLE,
                signal_date DATE,
                stock_code VARCHAR
            )
            """
        )
        conn.execute(
            "INSERT INTO mart_p0b_lambdamart_v6_predictions VALUES ('target', 3.0, '2026-01-02', '000003')"
        )
    finally:
        conn.close()

    result = import_model_predictions(
        local_db=str(local_db),
        remote_db=str(remote_db),
        model_id="target",
    )

    assert result["tables"]["mart_p0b_lambdamart_v6_predictions"]["local_after"] == 1
    local = duckdb.connect(str(local_db), read_only=True)
    try:
        row = local.execute(
            "SELECT stock_code, signal_date, score, model_id FROM mart_p0b_lambdamart_v6_predictions"
        ).fetchone()
        assert row == ("000003", date(2026, 1, 2), 3.0, "target")
    finally:
        local.close()


def test_import_model_predictions_from_parquet(tmp_path: Path):
    local_db = tmp_path / "local.duckdb"
    parquet_dir = tmp_path / "parquet"
    parquet_dir.mkdir()
    _create_prediction_tables(local_db)
    source_db = tmp_path / "source.duckdb"

    conn = duckdb.connect(str(source_db))
    try:
        conn.execute(DDL.format(table="mart_p0b_lambdamart_v6_predictions"))
        conn.execute(
            "INSERT INTO mart_p0b_lambdamart_v6_predictions VALUES "
            "('000003', '2026-01-02', 3.0, 'target'), "
            "('000004', '2026-01-02', 4.0, 'other')"
        )
        conn.execute(
            f"COPY mart_p0b_lambdamart_v6_predictions TO '{parquet_dir / 'mart_p0b_lambdamart_v6_predictions.parquet'}' "
            "(FORMAT PARQUET)"
        )
    finally:
        conn.close()

    result = import_model_predictions_from_parquet(
        local_db=str(local_db),
        parquet_dir=str(parquet_dir),
        model_id="target",
    )

    assert result["source"] == "parquet"
    assert result["tables"]["mart_p0b_lambdamart_v6_predictions"]["remote_rows"] == 1
    assert result["tables"]["mart_p0b_lambdamart_v6_predictions"]["local_after"] == 1
    assert result["tables"]["mart_p0b_oos_predictions"]["status"] == "missing_remote"
    local = duckdb.connect(str(local_db), read_only=True)
    try:
        row = local.execute(
            "SELECT stock_code, signal_date, score, model_id FROM mart_p0b_lambdamart_v6_predictions"
        ).fetchone()
        assert row == ("000003", date(2026, 1, 2), 3.0, "target")
    finally:
        local.close()
