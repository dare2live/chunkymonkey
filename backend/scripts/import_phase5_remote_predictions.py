"""Import Phase 5 prediction rows from a remote DuckDB copy into local DB."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import duckdb

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


PREDICTION_TABLES = (
    "mart_p0b_lambdamart_v6_predictions",
    "mart_p0b_oos_predictions",
)


def table_exists(conn: duckdb.DuckDBPyConnection, table_name: str, *, schema: str = "main") -> bool:
    try:
        conn.execute(f"SELECT 1 FROM {qualified_name(table_name, schema=schema)} LIMIT 0")
    except Exception:
        return False
    return True


def table_columns(conn: duckdb.DuckDBPyConnection, table_name: str, *, schema: str = "main") -> list[str]:
    rows = conn.execute(f"DESCRIBE {qualified_name(table_name, schema=schema)}").fetchall()
    return [str(row[0]) for row in rows]


def import_model_predictions(
    *,
    local_db: str,
    remote_db: str,
    model_id: str,
    dry_run: bool = False,
    mirror_lambdamart_to_oos: bool = False,
) -> dict[str, Any]:
    if not Path(remote_db).exists():
        raise FileNotFoundError(remote_db)
    conn = duckdb.connect(local_db)
    try:
        conn.execute(f"ATTACH '{escape_sql_string(remote_db)}' AS remote_db (READ_ONLY)")
        results: dict[str, Any] = {"model_id": model_id, "dry_run": dry_run, "tables": {}}
        for table in PREDICTION_TABLES:
            table_result: dict[str, Any] = {}
            results["tables"][table] = table_result
            if not table_exists(conn, table):
                table_result["status"] = "missing_local"
                continue
            if not table_exists(conn, table, schema="remote_db"):
                table_result["status"] = "missing_remote"
                continue

            local_cols = table_columns(conn, table)
            remote_cols = table_columns(conn, table, schema="remote_db")
            missing_in_remote = [col for col in local_cols if col not in remote_cols]
            extra_in_remote = [col for col in remote_cols if col not in local_cols]
            if missing_in_remote:
                table_result["status"] = "schema_mismatch"
                table_result["local_cols"] = local_cols
                table_result["remote_cols"] = remote_cols
                table_result["missing_in_remote"] = missing_in_remote
                table_result["extra_in_remote"] = extra_in_remote
                continue
            select_sql = ", ".join(quote_identifier(col) for col in local_cols)

            remote_count = conn.execute(
                f"SELECT COUNT(*) FROM {qualified_name(table, schema='remote_db')} WHERE model_id = ?",
                [model_id],
            ).fetchone()[0]
            local_before = conn.execute(
                f"SELECT COUNT(*) FROM {qualified_name(table)} WHERE model_id = ?",
                [model_id],
            ).fetchone()[0]
            table_result.update(
                status="dry_run" if dry_run else "imported",
                remote_rows=int(remote_count),
                local_before=int(local_before),
            )
            if dry_run:
                continue
            cols_sql = ", ".join(quote_identifier(col) for col in local_cols)
            conn.execute("BEGIN")
            try:
                conn.execute(f"DELETE FROM {qualified_name(table)} WHERE model_id = ?", [model_id])
                conn.execute(
                    f"INSERT INTO {qualified_name(table)} ({cols_sql}) "
                    f"SELECT {select_sql} FROM {qualified_name(table, schema='remote_db')} WHERE model_id = ?",
                    [model_id],
                )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
            local_after = conn.execute(
                f"SELECT COUNT(*) FROM {qualified_name(table)} WHERE model_id = ?",
                [model_id],
            ).fetchone()[0]
            table_result["local_after"] = int(local_after)
        if mirror_lambdamart_to_oos:
            results["mirror_lambdamart_to_oos"] = mirror_lambdamart_rows_to_oos(
                conn,
                model_id=model_id,
                dry_run=dry_run,
            )
        return results
    finally:
        conn.close()


def import_model_predictions_from_parquet(
    *,
    local_db: str,
    parquet_dir: str,
    model_id: str,
    dry_run: bool = False,
    mirror_lambdamart_to_oos: bool = False,
) -> dict[str, Any]:
    """Import model-specific prediction parquet files without copying a full DuckDB."""

    source_dir = Path(parquet_dir)
    if not source_dir.exists():
        raise FileNotFoundError(parquet_dir)

    conn = duckdb.connect(local_db)
    try:
        results: dict[str, Any] = {
            "model_id": model_id,
            "dry_run": dry_run,
            "source": "parquet",
            "parquet_dir": str(source_dir),
            "tables": {},
        }
        for table in PREDICTION_TABLES:
            table_result: dict[str, Any] = {}
            results["tables"][table] = table_result
            if not table_exists(conn, table):
                table_result["status"] = "missing_local"
                continue
            parquet_source = resolve_prediction_parquet_source(source_dir, table)
            if parquet_source is None:
                table_result["status"] = "missing_remote"
                continue
            view_name = f"__remote_{table}"
            conn.execute(f"DROP VIEW IF EXISTS {quote_identifier(view_name)}")
            try:
                conn.execute(
                    f"CREATE TEMP VIEW {quote_identifier(view_name)} AS "
                    f"SELECT * FROM read_parquet('{escape_sql_string(parquet_source)}')"
                )
                table_result.update(
                    import_prediction_table_from_relation(
                        conn,
                        table=table,
                        relation_sql=quote_identifier(view_name),
                        model_id=model_id,
                        dry_run=dry_run,
                    )
                )
                table_result["parquet_source"] = parquet_source
            finally:
                conn.execute(f"DROP VIEW IF EXISTS {quote_identifier(view_name)}")
        if mirror_lambdamart_to_oos:
            results["mirror_lambdamart_to_oos"] = mirror_lambdamart_rows_to_oos(
                conn,
                model_id=model_id,
                dry_run=dry_run,
            )
        return results
    finally:
        conn.close()


def resolve_prediction_parquet_source(source_dir: Path, table: str) -> str | None:
    file_path = source_dir / f"{table}.parquet"
    if file_path.exists():
        return str(file_path)
    table_dir = source_dir / table
    if table_dir.is_dir() and any(table_dir.glob("*.parquet")):
        return str(table_dir / "*.parquet")
    return None


def import_prediction_table_from_relation(
    conn: duckdb.DuckDBPyConnection,
    *,
    table: str,
    relation_sql: str,
    model_id: str,
    dry_run: bool,
) -> dict[str, Any]:
    table_result: dict[str, Any] = {}
    local_cols = table_columns(conn, table)
    remote_cols = relation_columns(conn, relation_sql)
    missing_in_remote = [col for col in local_cols if col not in remote_cols]
    extra_in_remote = [col for col in remote_cols if col not in local_cols]
    if missing_in_remote:
        table_result["status"] = "schema_mismatch"
        table_result["local_cols"] = local_cols
        table_result["remote_cols"] = remote_cols
        table_result["missing_in_remote"] = missing_in_remote
        table_result["extra_in_remote"] = extra_in_remote
        return table_result

    remote_count = conn.execute(
        f"SELECT COUNT(*) FROM {relation_sql} WHERE model_id = ?",
        [model_id],
    ).fetchone()[0]
    local_before = conn.execute(
        f"SELECT COUNT(*) FROM {qualified_name(table)} WHERE model_id = ?",
        [model_id],
    ).fetchone()[0]
    table_result.update(
        status="dry_run" if dry_run else "imported",
        remote_rows=int(remote_count),
        local_before=int(local_before),
    )
    if dry_run:
        return table_result

    cols_sql = ", ".join(quote_identifier(col) for col in local_cols)
    select_sql = ", ".join(quote_identifier(col) for col in local_cols)
    conn.execute("BEGIN")
    try:
        conn.execute(f"DELETE FROM {qualified_name(table)} WHERE model_id = ?", [model_id])
        conn.execute(
            f"INSERT INTO {qualified_name(table)} ({cols_sql}) "
            f"SELECT {select_sql} FROM {relation_sql} WHERE model_id = ?",
            [model_id],
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    local_after = conn.execute(
        f"SELECT COUNT(*) FROM {qualified_name(table)} WHERE model_id = ?",
        [model_id],
    ).fetchone()[0]
    table_result["local_after"] = int(local_after)
    return table_result


def relation_columns(conn: duckdb.DuckDBPyConnection, relation_sql: str) -> list[str]:
    rows = conn.execute(f"DESCRIBE SELECT * FROM {relation_sql}").fetchall()
    return [str(row[0]) for row in rows]


def escape_sql_string(value: str) -> str:
    """Escape a string for DuckDB SQL literals where bind params are unsupported."""
    return value.replace("'", "''")


def quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def qualified_name(table_name: str, *, schema: str = "main") -> str:
    return f"{quote_identifier(schema)}.{quote_identifier(table_name)}"


def mirror_lambdamart_rows_to_oos(
    conn: duckdb.DuckDBPyConnection,
    *,
    model_id: str,
    dry_run: bool,
) -> dict[str, Any]:
    """Copy LambdaMART v6 rows into the legacy OOS table for post-retrain tools."""
    source = "mart_p0b_lambdamart_v6_predictions"
    target = "mart_p0b_oos_predictions"
    source_count = conn.execute(
        f"SELECT COUNT(*) FROM {qualified_name(source)} WHERE model_id = ?",
        [model_id],
    ).fetchone()[0]
    target_before = conn.execute(
        f"SELECT COUNT(*) FROM {qualified_name(target)} WHERE model_id = ?",
        [model_id],
    ).fetchone()[0]
    result = {
        "source_rows": int(source_count),
        "target_before": int(target_before),
        "status": "dry_run" if dry_run else "mirrored",
    }
    if dry_run:
        return result

    source_cols = table_columns(conn, source)
    target_cols = table_columns(conn, target)
    missing_in_source = [col for col in target_cols if col not in source_cols]
    extra_in_source = [col for col in source_cols if col not in target_cols]
    if missing_in_source:
        result["status"] = "schema_mismatch"
        result["source_cols"] = source_cols
        result["target_cols"] = target_cols
        result["missing_in_source"] = missing_in_source
        result["extra_in_source"] = extra_in_source
        return result
    target_cols_sql = ", ".join(quote_identifier(col) for col in target_cols)
    source_select_sql = ", ".join(quote_identifier(col) for col in target_cols)
    conn.execute("BEGIN")
    try:
        conn.execute(f"DELETE FROM {qualified_name(target)} WHERE model_id = ?", [model_id])
        conn.execute(
            f"INSERT INTO {qualified_name(target)} ({target_cols_sql}) "
            f"SELECT {source_select_sql} FROM {qualified_name(source)} WHERE model_id = ?",
            [model_id],
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    target_after = conn.execute(
        f"SELECT COUNT(*) FROM {qualified_name(target)} WHERE model_id = ?",
        [model_id],
    ).fetchone()[0]
    result["target_after"] = int(target_after)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--local-db", default="data/smartmoney.duckdb")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--remote-db")
    source.add_argument(
        "--remote-parquet-dir",
        help="directory containing mart_p0b_lambdamart_v6_predictions.parquet and optional mart_p0b_oos_predictions.parquet",
    )
    parser.add_argument("--model-id", required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--mirror-lambdamart-to-oos",
        action="store_true",
        help="copy imported LambdaMART v6 rows into mart_p0b_oos_predictions for legacy post-retrain tools",
    )
    args = parser.parse_args()
    if args.remote_parquet_dir:
        result = import_model_predictions_from_parquet(
            local_db=args.local_db,
            parquet_dir=args.remote_parquet_dir,
            model_id=args.model_id,
            dry_run=args.dry_run,
            mirror_lambdamart_to_oos=args.mirror_lambdamart_to_oos,
        )
    else:
        result = import_model_predictions(
            local_db=args.local_db,
            remote_db=args.remote_db,
            model_id=args.model_id,
            dry_run=args.dry_run,
            mirror_lambdamart_to_oos=args.mirror_lambdamart_to_oos,
        )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
