#!/usr/bin/env python3
"""Materialize executable follow-return labels on feature panels.

The legacy ``forward_ret_*`` labels are diagnostic close-to-close labels. This
script writes the policy-backed ``follow_net_return_*`` labels from the user's
signal-day executable follow entry VWAP to the horizon exit VWAP.
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.analytics import duck_connection  # noqa: E402
from services.market_db import canonical_kline_daily_qfq_sql  # noqa: E402
from services.pipeline_manifest import git_commit_sha, record_pipeline_run, utc_now_iso  # noqa: E402
from services.pricing_policy import load_pricing_label_policy  # noqa: E402
from services.schema_versions import record_actual_version  # noqa: E402


REPO = Path(__file__).resolve().parent.parent.parent
DEFAULT_FEATURE_TABLES = ["fact_feature_panel", "fact_feature_panel_candidate"]
DEFAULT_HORIZONS = [5, 10, 20, 60, 90]

DDL = """
CREATE TABLE IF NOT EXISTS mart_follow_return_label_build (
    run_id TEXT NOT NULL,
    feature_table TEXT NOT NULL,
    policy_id TEXT NOT NULL,
    policy_hash TEXT NOT NULL,
    event_calc_version TEXT NOT NULL,
    price_adjustment TEXT NOT NULL,
    transaction_cost_bps DOUBLE NOT NULL,
    horizons_json TEXT NOT NULL,
    labels_json TEXT NOT NULL,
    row_count BIGINT,
    label_non_null_json TEXT,
    label_coverage_json TEXT,
    min_date TEXT,
    max_date TEXT,
    built_at TEXT NOT NULL,
    PRIMARY KEY (run_id, feature_table)
);
CREATE INDEX IF NOT EXISTS idx_follow_label_build_table
    ON mart_follow_return_label_build(feature_table, built_at);

CREATE TABLE IF NOT EXISTS mart_follow_return_label_quality (
    run_id TEXT NOT NULL,
    feature_table TEXT NOT NULL,
    label_name TEXT NOT NULL,
    horizon_days INTEGER NOT NULL,
    policy_id TEXT NOT NULL,
    policy_hash TEXT NOT NULL,
    event_calc_version TEXT NOT NULL,
    row_count BIGINT NOT NULL,
    non_null_count BIGINT NOT NULL,
    null_count BIGINT NOT NULL,
    immature_null_count BIGINT NOT NULL,
    mature_null_count BIGINT NOT NULL,
    missing_signal_kline_count BIGINT NOT NULL,
    missing_entry_price_count BIGINT NOT NULL,
    missing_exit_price_count BIGINT NOT NULL,
    unclassified_null_count BIGINT NOT NULL,
    min_date TEXT,
    max_date TEXT,
    stock_max_date_min TEXT,
    stock_max_date_max TEXT,
    global_market_max_date TEXT,
    built_at TEXT NOT NULL,
    PRIMARY KEY (run_id, feature_table, label_name)
);
CREATE INDEX IF NOT EXISTS idx_follow_label_quality_table
    ON mart_follow_return_label_quality(feature_table, built_at);
"""


def _execute_script(conn: Any, sql: str) -> None:
    if hasattr(conn, "executescript"):
        conn.executescript(sql)
        return
    for stmt in sql.split(";"):
        stmt = stmt.strip()
        if stmt:
            conn.execute(stmt)


def _quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _quote_relation(name: str) -> str:
    return ".".join(_quote_ident(part) for part in name.split("."))


def _row_value(row: Any, key: str, index: int) -> Any:
    if row is None:
        return None
    try:
        return row[key]
    except Exception:
        return row[index]


def _table_exists(conn: Any, table_name: str) -> bool:
    relation = table_name.split(".")
    if len(relation) == 2:
        schema, table = relation
        row = conn.execute(
            """
            SELECT 1
              FROM information_schema.tables
             WHERE (table_schema = ? OR table_catalog = ?)
               AND table_name = ?
             LIMIT 1
            """,
            (schema, schema, table),
        ).fetchone()
    else:
        row = conn.execute(
            """
            SELECT 1
              FROM information_schema.tables
             WHERE table_name = ?
             LIMIT 1
            """,
            (table_name,),
        ).fetchone()
    return row is not None


def _table_columns(conn: Any, table_name: str) -> set[str]:
    if not _table_exists(conn, table_name):
        return set()
    relation = table_name.split(".")
    if len(relation) == 2:
        schema, table = relation
        rows = conn.execute(
            """
            SELECT column_name
              FROM information_schema.columns
             WHERE (table_schema = ? OR table_catalog = ?)
               AND table_name = ?
            """,
            (schema, schema, table),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT column_name
              FROM information_schema.columns
             WHERE table_name = ?
            """,
            (table_name,),
        ).fetchall()
    return {str(_row_value(row, "column_name", 0)) for row in rows}


def _ensure_tables(conn: Any) -> None:
    _execute_script(conn, DDL)


def _ensure_label_columns(conn: Any, feature_table: str, labels: list[str]) -> None:
    columns = _table_columns(conn, feature_table)
    if not columns:
        raise RuntimeError(f"feature table missing: {feature_table}")
    if "stock_code" not in columns or "date" not in columns:
        raise RuntimeError(f"feature table must include stock_code and date: {feature_table}")
    relation = _quote_relation(feature_table)
    for label in labels:
        conn.execute(f"ALTER TABLE {relation} ADD COLUMN IF NOT EXISTS {_quote_ident(label)} DOUBLE")


def _parse_horizons(value: str | None) -> list[int]:
    if not value:
        return DEFAULT_HORIZONS[:]
    horizons: list[int] = []
    for item in value.split(","):
        item = item.strip().lower().replace("d", "")
        if not item:
            continue
        horizon = int(item)
        if horizon <= 0:
            raise ValueError(f"horizon must be positive: {horizon}")
        horizons.append(horizon)
    return sorted(set(horizons))


def labels_for_horizons(horizons: list[int]) -> list[str]:
    return [f"follow_net_return_{horizon}d" for horizon in horizons]


def _emit_progress(enabled: bool, message: str) -> None:
    if enabled:
        print(message, file=sys.stderr, flush=True)


@contextmanager
def _heartbeat(enabled: bool, *, label: str, every_s: int = 30):
    if not enabled:
        yield
        return
    stop = threading.Event()
    started = time.perf_counter()

    def _run() -> None:
        while not stop.wait(every_s):
            elapsed = time.perf_counter() - started
            _emit_progress(True, f"[follow-label] heartbeat {label}: elapsed={elapsed:.1f}s")

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join(timeout=1)


def _build_daily_label_table(conn: Any, *, horizons: list[int], transaction_cost_bps: float) -> None:
    cost_rate = float(transaction_cost_bps) / 10000.0
    lead_exprs = [
        "entry_price AS __follow_entry_price"
    ]
    label_exprs = []
    for horizon in horizons:
        offset = horizon
        label = f"follow_net_return_{horizon}d"
        exit_col = f"__follow_exit_price_{horizon}d"
        lead_exprs.append(f"LEAD(exit_price, {offset}) OVER w AS {_quote_ident(exit_col)}")
        label_exprs.append(
            f"""
            CASE
              WHEN __follow_entry_price > 0
               AND {_quote_ident(exit_col)} > 0
              THEN {_quote_ident(exit_col)} / NULLIF(__follow_entry_price, 0)
                   - 1 - {cost_rate:.12f}
              ELSE NULL
            END AS {_quote_ident(label)}
            """
        )
    kline_sql = canonical_kline_daily_qfq_sql()
    conn.execute("DROP TABLE IF EXISTS __follow_return_label_daily")
    conn.execute(
        f"""
        CREATE TEMP TABLE __follow_return_label_daily AS
        WITH raw_px AS (
            SELECT code AS stock_code,
                   CAST(date AS TEXT) AS date,
                   CAST(open AS DOUBLE) AS open,
                   CAST(close AS DOUBLE) AS close,
                   CASE
                     WHEN amount IS NOT NULL AND volume IS NOT NULL
                      AND amount > 0 AND volume > 0
                     THEN CAST(amount AS DOUBLE) / CAST(volume AS DOUBLE)
                     ELSE NULL
                   END AS raw_vwap
              FROM ({kline_sql}) k
        ),
        px AS (
            SELECT stock_code, date,
                   CASE
                     WHEN raw_vwap IS NOT NULL AND close > 0
                      AND raw_vwap / close BETWEEN 0.5 AND 1.5
                     THEN raw_vwap
                     WHEN raw_vwap IS NOT NULL AND close > 0
                      AND (raw_vwap / 100.0) / close BETWEEN 0.5 AND 1.5
                     THEN raw_vwap / 100.0
                     WHEN open > 0
                     THEN open
                     ELSE NULL
                   END AS entry_price,
                   CASE
                     WHEN raw_vwap IS NOT NULL AND close > 0
                      AND raw_vwap / close BETWEEN 0.5 AND 1.5
                     THEN raw_vwap
                     WHEN raw_vwap IS NOT NULL AND close > 0
                      AND (raw_vwap / 100.0) / close BETWEEN 0.5 AND 1.5
                     THEN raw_vwap / 100.0
                     WHEN close > 0
                     THEN close
                     ELSE NULL
                   END AS exit_price
              FROM raw_px
        ),
        indexed_px AS (
            SELECT stock_code, date, entry_price, exit_price,
                   ROW_NUMBER() OVER (PARTITION BY stock_code ORDER BY date) AS __rn,
                   COUNT(*) OVER (PARTITION BY stock_code) AS __max_rn,
                   MAX(date) OVER (PARTITION BY stock_code) AS __stock_max_date,
                   MAX(date) OVER () AS __global_market_max_date
              FROM px
        ),
        lead_px AS (
            SELECT stock_code, date, entry_price, exit_price,
                   __rn, __max_rn, __stock_max_date, __global_market_max_date,
                   {", ".join(lead_exprs)}
              FROM indexed_px
            WINDOW w AS (PARTITION BY stock_code ORDER BY date)
        )
        SELECT stock_code, date,
               __rn, __max_rn, __stock_max_date, __global_market_max_date,
               __follow_entry_price,
               {", ".join(_quote_ident(f"__follow_exit_price_{horizon}d") for horizon in horizons)},
               {", ".join(label_exprs)}
          FROM lead_px
        """
    )


def _where_clause(start_date: str | None, end_date: str | None, *, alias: str = "t") -> str:
    filters: list[str] = []
    if start_date:
        filters.append(f"{alias}.date >= ?")
    if end_date:
        filters.append(f"{alias}.date <= ?")
    return (" AND " + " AND ".join(filters)) if filters else ""


def _date_params(start_date: str | None, end_date: str | None) -> list[str]:
    params: list[str] = []
    if start_date:
        params.append(start_date)
    if end_date:
        params.append(end_date)
    return params


def _update_feature_table(
    conn: Any,
    *,
    feature_table: str,
    labels: list[str],
    start_date: str | None,
    end_date: str | None,
    progress: bool,
) -> dict[str, Any]:
    _ensure_label_columns(conn, feature_table, labels)
    relation = _quote_relation(feature_table)
    clear_sql = ", ".join(
        f"{_quote_ident(label)} = NULL"
        for label in labels
    )
    clear_started = time.perf_counter()
    _emit_progress(progress, f"[follow-label] clear labels: {feature_table}")
    with _heartbeat(progress, label=f"clear {feature_table}"):
        conn.execute(
            f"""
            UPDATE {relation} AS t
               SET {clear_sql}
             WHERE 1=1 {_where_clause(start_date, end_date)}
            """,
            _date_params(start_date, end_date),
        )
    clear_seconds = round(time.perf_counter() - clear_started, 3)
    set_sql = ", ".join(
        f"{_quote_ident(label)} = l.{_quote_ident(label)}"
        for label in labels
    )
    update_started = time.perf_counter()
    _emit_progress(progress, f"[follow-label] set labels from daily table: {feature_table}")
    with _heartbeat(progress, label=f"set {feature_table}"):
        conn.execute(
            f"""
            UPDATE {relation} AS t
               SET {set_sql}
              FROM __follow_return_label_daily AS l
             WHERE t.stock_code = l.stock_code
               AND CAST(t.date AS TEXT) = l.date
               {_where_clause(start_date, end_date)}
            """,
            _date_params(start_date, end_date),
        )
    set_seconds = round(time.perf_counter() - update_started, 3)
    counts_sql = ", ".join(
        f"COUNT({_quote_ident(label)}) AS {_quote_ident(label)}"
        for label in labels
    )
    row = conn.execute(
        f"""
        SELECT COUNT(*) AS row_count,
               MIN(CAST(date AS TEXT)) AS min_date,
               MAX(CAST(date AS TEXT)) AS max_date,
               {counts_sql}
          FROM {relation} AS t
         WHERE 1=1 {_where_clause(start_date, end_date)}
        """,
        _date_params(start_date, end_date),
    ).fetchone()
    row_count = int(_row_value(row, "row_count", 0) or 0)
    label_non_null = {
        label: int(_row_value(row, label, 3 + idx) or 0)
        for idx, label in enumerate(labels)
    }
    label_coverage = {
        label: (label_non_null[label] / row_count if row_count else 0.0)
        for label in labels
    }
    return {
        "feature_table": feature_table,
        "row_count": row_count,
        "min_date": _row_value(row, "min_date", 1),
        "max_date": _row_value(row, "max_date", 2),
        "label_non_null": label_non_null,
        "label_coverage": label_coverage,
        "clear_seconds": clear_seconds,
        "set_seconds": set_seconds,
    }


def _insert_quality_rows(
    conn: Any,
    *,
    run_id: str,
    feature_table: str,
    policy_id: str,
    policy_hash: str,
    event_calc_version: str,
    horizons: list[int],
    labels: list[str],
    start_date: str | None,
    end_date: str | None,
    built_at: str,
) -> list[dict[str, Any]]:
    relation = _quote_relation(feature_table)
    summaries: list[dict[str, Any]] = []
    for horizon, label in zip(horizons, labels, strict=True):
        offset = horizon
        exit_col = f"__follow_exit_price_{horizon}d"
        row = conn.execute(
            f"""
            WITH scoped AS (
                SELECT stock_code,
                       CAST(date AS TEXT) AS date,
                       {_quote_ident(label)} AS label_value
                  FROM {relation} AS t
                 WHERE 1=1 {_where_clause(start_date, end_date)}
            ),
            joined AS (
                SELECT s.stock_code,
                       s.date,
                       s.label_value,
                       l.__rn,
                       l.__max_rn,
                       l.__stock_max_date,
                       l.__global_market_max_date,
                       l.__follow_entry_price,
                       l.{_quote_ident(exit_col)} AS __follow_exit_price
                  FROM scoped AS s
                  LEFT JOIN __follow_return_label_daily AS l
                    ON s.stock_code = l.stock_code
                   AND s.date = l.date
            ),
            classified AS (
                SELECT *,
                       CASE
                         WHEN __rn IS NULL THEN 1 ELSE 0
                       END AS missing_signal_kline,
                       CASE
                         WHEN label_value IS NULL
                          AND __rn IS NOT NULL
                          AND __rn + {offset} > __max_rn THEN 1 ELSE 0
                       END AS immature_null,
                       CASE
                         WHEN label_value IS NULL
                          AND __rn IS NOT NULL
                          AND __rn + {offset} <= __max_rn
                          AND (__follow_entry_price IS NULL OR __follow_entry_price <= 0)
                         THEN 1 ELSE 0
                       END AS missing_entry_price,
                       CASE
                         WHEN label_value IS NULL
                          AND __rn IS NOT NULL
                          AND __rn + {offset} <= __max_rn
                          AND __follow_entry_price > 0
                          AND (__follow_exit_price IS NULL OR __follow_exit_price <= 0)
                         THEN 1 ELSE 0
                       END AS missing_exit_price
                  FROM joined
            ),
            aggregated AS (
                SELECT COUNT(*) AS row_count,
                       COUNT(label_value) AS non_null_count,
                       SUM(CASE WHEN label_value IS NULL THEN 1 ELSE 0 END) AS null_count,
                       SUM(immature_null) AS immature_null_count,
                       SUM(missing_signal_kline) AS missing_signal_kline_count,
                       SUM(missing_entry_price) AS missing_entry_price_count,
                       SUM(missing_exit_price) AS missing_exit_price_count,
                       MIN(date) AS min_date,
                       MAX(date) AS max_date,
                       MIN(__stock_max_date) AS stock_max_date_min,
                       MAX(__stock_max_date) AS stock_max_date_max,
                       MAX(__global_market_max_date) AS global_market_max_date
                  FROM classified
            )
            SELECT row_count,
                   non_null_count,
                   null_count,
                   immature_null_count,
                   null_count - immature_null_count AS mature_null_count,
                   missing_signal_kline_count,
                   missing_entry_price_count,
                   missing_exit_price_count,
                   null_count
                     - immature_null_count
                     - missing_signal_kline_count
                     - missing_entry_price_count
                     - missing_exit_price_count AS unclassified_null_count,
                   min_date,
                   max_date,
                   stock_max_date_min,
                   stock_max_date_max,
                   global_market_max_date
              FROM aggregated
            """,
            _date_params(start_date, end_date),
        ).fetchone()
        summary = {
            "label_name": label,
            "horizon_days": horizon,
            "row_count": int(_row_value(row, "row_count", 0) or 0),
            "non_null_count": int(_row_value(row, "non_null_count", 1) or 0),
            "null_count": int(_row_value(row, "null_count", 2) or 0),
            "immature_null_count": int(_row_value(row, "immature_null_count", 3) or 0),
            "mature_null_count": int(_row_value(row, "mature_null_count", 4) or 0),
            "missing_signal_kline_count": int(_row_value(row, "missing_signal_kline_count", 5) or 0),
            "missing_entry_price_count": int(_row_value(row, "missing_entry_price_count", 6) or 0),
            "missing_exit_price_count": int(_row_value(row, "missing_exit_price_count", 7) or 0),
            "unclassified_null_count": int(_row_value(row, "unclassified_null_count", 8) or 0),
            "min_date": _row_value(row, "min_date", 9),
            "max_date": _row_value(row, "max_date", 10),
            "stock_max_date_min": _row_value(row, "stock_max_date_min", 11),
            "stock_max_date_max": _row_value(row, "stock_max_date_max", 12),
            "global_market_max_date": _row_value(row, "global_market_max_date", 13),
        }
        conn.execute(
            """
            INSERT OR REPLACE INTO mart_follow_return_label_quality (
                run_id, feature_table, label_name, horizon_days,
                policy_id, policy_hash, event_calc_version,
                row_count, non_null_count, null_count,
                immature_null_count, mature_null_count,
                missing_signal_kline_count, missing_entry_price_count,
                missing_exit_price_count, unclassified_null_count,
                min_date, max_date, stock_max_date_min, stock_max_date_max,
                global_market_max_date, built_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                feature_table,
                label,
                horizon,
                policy_id,
                policy_hash,
                event_calc_version,
                summary["row_count"],
                summary["non_null_count"],
                summary["null_count"],
                summary["immature_null_count"],
                summary["mature_null_count"],
                summary["missing_signal_kline_count"],
                summary["missing_entry_price_count"],
                summary["missing_exit_price_count"],
                summary["unclassified_null_count"],
                summary["min_date"],
                summary["max_date"],
                summary["stock_max_date_min"],
                summary["stock_max_date_max"],
                summary["global_market_max_date"],
                built_at,
            ),
        )
        summaries.append(summary)
    return summaries


def materialize_follow_return_labels(
    conn: Any,
    *,
    feature_tables: list[str] | None = None,
    horizons: list[int] | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    run_id: str | None = None,
    progress: bool = False,
) -> dict[str, Any]:
    policy = load_pricing_label_policy()
    started_at = utc_now_iso()
    started = time.perf_counter()
    feature_tables = feature_tables or DEFAULT_FEATURE_TABLES[:]
    horizons = horizons or DEFAULT_HORIZONS[:]
    labels = labels_for_horizons(horizons)
    built_at = datetime.now(UTC).replace(tzinfo=None).isoformat(timespec="seconds")
    run_id = run_id or f"follow_return_label_build_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}"
    _ensure_tables(conn)
    stage_timing: dict[str, float] = {}
    stage_started = time.perf_counter()
    _emit_progress(progress, f"[follow-label] build daily labels: horizons={horizons}")
    _build_daily_label_table(conn, horizons=horizons, transaction_cost_bps=policy.transaction_cost_bps)
    stage_timing["build_daily_label_table_seconds"] = round(time.perf_counter() - stage_started, 3)

    table_summaries = []
    for feature_table in feature_tables:
        table_started = time.perf_counter()
        _emit_progress(progress, f"[follow-label] update table: {feature_table}")
        summary = _update_feature_table(
            conn,
            feature_table=feature_table,
            labels=labels,
            start_date=start_date,
            end_date=end_date,
            progress=progress,
        )
        summary["update_seconds"] = round(time.perf_counter() - table_started, 3)
        stage_timing[f"{feature_table}.clear_seconds"] = summary["clear_seconds"]
        stage_timing[f"{feature_table}.set_seconds"] = summary["set_seconds"]
        stage_timing[f"{feature_table}.update_seconds"] = summary["update_seconds"]
        table_summaries.append(summary)
        conn.execute(
            """
            INSERT OR REPLACE INTO mart_follow_return_label_build (
                run_id, feature_table, policy_id, policy_hash,
                event_calc_version, price_adjustment, transaction_cost_bps,
                horizons_json, labels_json, row_count, label_non_null_json,
                label_coverage_json, min_date, max_date, built_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                feature_table,
                policy.policy_id,
                policy.policy_hash(),
                policy.event_calc_version,
                policy.price_adjustment,
                policy.transaction_cost_bps,
                json.dumps(horizons, ensure_ascii=False),
                json.dumps(labels, ensure_ascii=False),
                summary["row_count"],
                json.dumps(summary["label_non_null"], ensure_ascii=False, sort_keys=True),
                json.dumps(summary["label_coverage"], ensure_ascii=False, sort_keys=True),
                summary["min_date"],
                summary["max_date"],
                built_at,
            ),
        )
        quality_started = time.perf_counter()
        _emit_progress(progress, f"[follow-label] audit null causes: {feature_table}")
        summary["quality"] = _insert_quality_rows(
            conn,
            run_id=run_id,
            feature_table=feature_table,
            policy_id=policy.policy_id,
            policy_hash=policy.policy_hash(),
            event_calc_version=policy.event_calc_version,
            horizons=horizons,
            labels=labels,
            start_date=start_date,
            end_date=end_date,
            built_at=built_at,
        )
        summary["quality_seconds"] = round(time.perf_counter() - quality_started, 3)
        stage_timing[f"{feature_table}.quality_seconds"] = summary["quality_seconds"]
        _emit_progress(
            progress,
            "[follow-label] done table: "
            f"{feature_table}, rows={summary['row_count']}, "
            f"update={summary['update_seconds']}s, quality={summary['quality_seconds']}s",
        )
        try:
            record_actual_version(conn, feature_table.rsplit(".", 1)[-1])
        except Exception:
            pass
    record_actual_version(conn, "mart_follow_return_label_build")
    record_actual_version(conn, "mart_follow_return_label_quality")
    ended_at = utc_now_iso()
    duration_s = time.perf_counter() - started
    stage_timing["total_seconds"] = round(duration_s, 3)
    record_pipeline_run(
        conn,
        run_id=run_id,
        pipeline_name="materialize_follow_return_labels",
        status="success",
        started_at=started_at,
        ended_at=ended_at,
        duration_s=duration_s,
        commit_sha=git_commit_sha(REPO),
        input_tables=["market.v_price_kline_qfq", *feature_tables],
        output_tables=[*feature_tables, "mart_follow_return_label_build", "mart_follow_return_label_quality"],
        label_name=",".join(labels),
        gate_result="pass",
        perf_summary={
            "stage_timings": stage_timing,
            "feature_tables": table_summaries,
            "horizons": horizons,
            "policy_hash": policy.policy_hash(),
        },
    )
    return {
        "run_id": run_id,
        "policy_id": policy.policy_id,
        "policy_hash": policy.policy_hash(),
        "event_calc_version": policy.event_calc_version,
        "transaction_cost_bps": policy.transaction_cost_bps,
        "horizons": horizons,
        "labels": labels,
        "feature_tables": table_summaries,
        "stage_timing": stage_timing,
        "built_at": built_at,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--feature-table", action="append", dest="feature_tables")
    parser.add_argument("--horizons", default="5,10,20,60,90")
    parser.add_argument("--start-date", default=None)
    parser.add_argument("--end-date", default=None)
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    with duck_connection(writable=True) as conn:
        result = materialize_follow_return_labels(
            conn,
            feature_tables=args.feature_tables,
            horizons=_parse_horizons(args.horizons),
            start_date=args.start_date,
            end_date=args.end_date,
            run_id=args.run_id,
            progress=not args.quiet,
        )
        conn.commit()
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
