#!/usr/bin/env python3
"""Build the current feature catalog and PIT join plan."""
from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.audit_registry_feature_pit import _base_feature_for_transform, _pit_risk_level  # noqa: E402
from services.db import get_conn  # noqa: E402
from services.feature_registry import FeatureRegistry, FeatureSpec, load_feature_registry  # noqa: E402
from services.pipeline_manifest import git_commit_sha, record_pipeline_run, utc_now_iso  # noqa: E402
from services.schema_versions import record_actual_version  # noqa: E402


DEFAULT_FEATURE_TABLES = [
    "fact_feature_panel",
    "fact_feature_panel_candidate",
    "fact_feature_panel_tdx_keep_challenger",
    "mart_temporal_research_panel",
]

STRUCTURAL_COLUMNS = {
    "stock_code",
    "stock_name",
    "date",
    "signal_date",
    "built_at",
    "updated_at",
    "feature_set_id",
    "run_id",
    "regime_flag",
    "kline_source_name",
    "kline_source_tier",
    "kline_is_fallback",
}

DDL = """
CREATE TABLE IF NOT EXISTS mart_feature_catalog_current (
    run_id TEXT NOT NULL,
    feature_table TEXT NOT NULL,
    feature_name TEXT NOT NULL,
    data_type TEXT,
    ordinal_position INTEGER,
    grain TEXT,
    feature_family TEXT,
    registry_status TEXT NOT NULL,
    enabled BOOLEAN,
    production_ready BOOLEAN,
    candidate_only BOOLEAN,
    label BOOLEAN,
    model_input BOOLEAN,
    source_tables_json TEXT,
    required_capabilities_json TEXT,
    pit_release_lag_days INTEGER,
    pit_risk_level TEXT NOT NULL,
    table_exists BOOLEAN NOT NULL,
    total_rows BIGINT,
    non_null_rows BIGINT,
    coverage_pct DOUBLE,
    min_signal_date TEXT,
    max_signal_date TEXT,
    source_event_date_column TEXT,
    source_available_date_column TEXT,
    allowed_in_production_research BOOLEAN NOT NULL,
    built_at TEXT NOT NULL,
    PRIMARY KEY (run_id, feature_table, feature_name)
);

CREATE TABLE IF NOT EXISTS mart_feature_pit_join_plan (
    run_id TEXT NOT NULL,
    feature_table TEXT NOT NULL,
    feature_name TEXT NOT NULL,
    pit_risk_level TEXT NOT NULL,
    join_keys_json TEXT,
    signal_date_column TEXT,
    source_event_date_column TEXT,
    source_available_date_column TEXT,
    lag_policy_days INTEGER,
    join_policy TEXT NOT NULL,
    production_blocking BOOLEAN NOT NULL,
    notes TEXT,
    built_at TEXT NOT NULL,
    PRIMARY KEY (run_id, feature_table, feature_name)
);

CREATE TABLE IF NOT EXISTS mart_feature_exclusion_reason (
    run_id TEXT NOT NULL,
    feature_table TEXT NOT NULL,
    feature_name TEXT NOT NULL,
    reason_code TEXT NOT NULL,
    reason_detail TEXT,
    production_blocking BOOLEAN NOT NULL,
    built_at TEXT NOT NULL,
    PRIMARY KEY (run_id, feature_table, feature_name, reason_code)
);
"""


def _execute_script(conn: Any, sql: str) -> None:
    if hasattr(conn, "executescript"):
        conn.executescript(sql)
        return
    for stmt in sql.split(";"):
        stmt = stmt.strip()
        if stmt:
            conn.execute(stmt)


def ensure_tables(conn: Any) -> None:
    _execute_script(conn, DDL)


def _quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _table_exists(conn: Any, table_name: str) -> bool:
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


def _table_columns(conn: Any, table_name: str) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in conn.execute(
            """
            SELECT column_name, data_type, ordinal_position
              FROM information_schema.columns
             WHERE table_name = ?
             ORDER BY ordinal_position
            """,
            (table_name,),
        ).fetchall()
    ]


def _finite_pct(non_null: int, total: int) -> float:
    if total <= 0:
        return 0.0
    value = non_null * 100.0 / total
    return value if math.isfinite(value) else 0.0


def _date_column(columns: set[str]) -> str | None:
    if "signal_date" in columns:
        return "signal_date"
    if "date" in columns:
        return "date"
    return None


def _feature_spec(registry: FeatureRegistry, feature: str) -> tuple[FeatureSpec | None, str, str | None]:
    spec = registry.features.get(feature)
    if spec:
        return spec, "registered", None
    base = _base_feature_for_transform(feature, registry)
    if base and base in registry.features:
        return registry.features[base], "transformed_from_registered", base
    if feature in STRUCTURAL_COLUMNS or feature.startswith("forward_ret_"):
        return None, "structural_or_label", None
    return None, "unknown", None


def _feature_family(feature: str, spec: FeatureSpec | None, registry_status: str) -> str:
    if spec:
        return spec.group
    if feature in STRUCTURAL_COLUMNS:
        return "structural"
    if feature.startswith("forward_ret_"):
        return "labels"
    return "unknown"


def _table_stats(
    conn: Any,
    table_name: str,
    *,
    date_col: str | None,
    features: list[str],
) -> tuple[int, str | None, str | None, dict[str, int]]:
    select_parts = ["COUNT(*) AS total_rows"]
    if date_col:
        select_parts.extend(
            [
                f"MIN(CAST({_quote_ident(date_col)} AS VARCHAR)) AS min_date",
                f"MAX(CAST({_quote_ident(date_col)} AS VARCHAR)) AS max_date",
            ]
        )
    select_parts.extend(
        f"COUNT({_quote_ident(feature)}) AS {_quote_ident(feature)}"
        for feature in features
    )
    row = conn.execute(
        f"SELECT {', '.join(select_parts)} FROM {_quote_ident(table_name)}"
    ).fetchone()
    if not row:
        return 0, None, None, {feature: 0 for feature in features}
    if hasattr(row, "keys"):
        total_rows = int(row["total_rows"] or 0)
        min_date = str(row["min_date"]) if date_col and row["min_date"] is not None else None
        max_date = str(row["max_date"]) if date_col and row["max_date"] is not None else None
        non_null_counts = {feature: int(row[feature] or 0) for feature in features}
        return total_rows, min_date, max_date, non_null_counts
    idx = 0
    total_rows = int(row[idx] or 0)
    idx += 1
    min_date = max_date = None
    if date_col:
        min_date = str(row[idx]) if row[idx] is not None else None
        idx += 1
        max_date = str(row[idx]) if row[idx] is not None else None
        idx += 1
    non_null_counts = {
        feature: int(row[idx + offset] or 0)
        for offset, feature in enumerate(features)
    }
    return total_rows, min_date, max_date, non_null_counts


def _join_plan(
    *,
    feature: str,
    spec: FeatureSpec | None,
    pit_risk_level: str,
    signal_date_column: str | None,
) -> dict[str, Any]:
    lag_days = int(spec.pit_release_lag_days) if spec else 0
    if pit_risk_level in {"not_applicable", "critical"}:
        source_event = None
        source_available = None
        policy = "blocked_or_not_applicable"
        notes = "not model-eligible" if pit_risk_level == "not_applicable" else "missing registry/source metadata"
    elif pit_risk_level == "low":
        source_event = signal_date_column
        source_available = signal_date_column
        policy = "same_day_or_trailing_market_data"
        notes = "trailing market-derived feature"
    elif lag_days > 0:
        source_event = "report_date"
        source_available = f"report_date_plus_{lag_days}d"
        policy = "asof_source_available_date"
        notes = "conservative regulatory lag until true notice date is available"
    else:
        source_event = spec.source_event_date_column if spec and spec.source_event_date_column else "source_event_date"
        source_available = (
            spec.source_available_date_column
            if spec and spec.source_available_date_column
            else "source_available_date"
        )
        policy = "asof_source_available_date"
        notes = "requires source-specific available date"
    return {
        "join_keys": ["stock_code", signal_date_column] if signal_date_column else ["stock_code"],
        "signal_date_column": signal_date_column,
        "source_event_date_column": source_event,
        "source_available_date_column": source_available,
        "lag_policy_days": lag_days,
        "join_policy": policy,
        "notes": notes,
    }


def _exclusion_reasons(
    *,
    feature: str,
    spec: FeatureSpec | None,
    registry_status: str,
    pit_risk_level: str,
    table_has_signal_date: bool,
    non_null_rows: int,
) -> list[tuple[str, str, bool]]:
    reasons: list[tuple[str, str, bool]] = []
    if feature in STRUCTURAL_COLUMNS:
        reasons.append(("structural_column", "not a model feature", False))
    if feature.startswith("forward_ret_") or (spec and spec.label):
        reasons.append(("label_column", "forward label is not an input feature", True))
    if spec and not spec.enabled:
        reasons.append(("disabled_registry_feature", "feature registry enabled=false", True))
    if spec and not spec.production_ready:
        reasons.append(("production_not_ready", "feature registry production_ready=false", True))
    if spec and spec.candidate_only:
        reasons.append(("candidate_only", "feature registry candidate_only=true", False))
    if registry_status == "unknown":
        reasons.append(("unknown_blocking", "feature is not registered and has no PIT metadata", True))
    if pit_risk_level == "critical":
        reasons.append(("critical_pit_risk", "critical PIT risk requires explicit metadata before production", True))
    if not table_has_signal_date:
        reasons.append(("missing_signal_date", "feature table has neither date nor signal_date", True))
    if non_null_rows == 0:
        reasons.append(("zero_coverage", "feature has zero non-null rows in this table", False))
    return reasons


def build_feature_catalog_current(
    conn: Any,
    *,
    run_id: str | None = None,
    feature_tables: list[str] | None = None,
) -> dict[str, Any]:
    ensure_tables(conn)
    registry = load_feature_registry()
    run_id = run_id or f"feature_catalog_current_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}"
    started_at = utc_now_iso()
    built_at = datetime.now(UTC).replace(tzinfo=None).isoformat(timespec="seconds")
    feature_tables = feature_tables or DEFAULT_FEATURE_TABLES

    conn.execute("DELETE FROM mart_feature_catalog_current WHERE run_id = ?", (run_id,))
    conn.execute("DELETE FROM mart_feature_pit_join_plan WHERE run_id = ?", (run_id,))
    conn.execute("DELETE FROM mart_feature_exclusion_reason WHERE run_id = ?", (run_id,))

    catalog_rows = []
    join_rows = []
    exclusion_rows = []
    missing_tables = []
    scanned_tables = 0

    for table_name in feature_tables:
        exists = _table_exists(conn, table_name)
        if not exists:
            missing_tables.append(table_name)
            exclusion_rows.append(
                (
                    run_id,
                    table_name,
                    "*",
                    "table_missing",
                    "requested feature table does not exist",
                    True,
                    built_at,
                )
            )
            continue
        scanned_tables += 1
        columns = _table_columns(conn, table_name)
        column_names = {str(col["column_name"]) for col in columns}
        signal_date_column = _date_column(column_names)
        total_rows, min_signal_date, max_signal_date, non_null_counts = _table_stats(
            conn,
            table_name,
            date_col=signal_date_column,
            features=[str(col["column_name"]) for col in columns],
        )
        table_has_signal_date = signal_date_column is not None

        for col in columns:
            feature = str(col["column_name"])
            spec, registry_status, base_feature = _feature_spec(registry, feature)
            risk = _pit_risk_level(feature, spec, registry)
            family = _feature_family(feature, spec, registry_status)
            non_null_rows = non_null_counts.get(feature, 0)
            coverage_pct = _finite_pct(non_null_rows, total_rows)
            reasons = _exclusion_reasons(
                feature=feature,
                spec=spec,
                registry_status=registry_status,
                pit_risk_level=risk,
                table_has_signal_date=table_has_signal_date,
                non_null_rows=non_null_rows,
            )
            production_blocking = any(reason[2] for reason in reasons)
            model_input = bool(spec.model_input) if spec else False
            allowed = bool(
                table_has_signal_date
                and model_input
                and not production_blocking
                and risk not in {"critical", "not_applicable"}
                and non_null_rows > 0
            )
            plan = _join_plan(
                feature=feature,
                spec=spec,
                pit_risk_level=risk,
                signal_date_column=signal_date_column,
            )
            catalog_rows.append(
                (
                    run_id,
                    table_name,
                    feature,
                    col.get("data_type"),
                    int(col.get("ordinal_position") or 0),
                    "stock_day" if table_has_signal_date and "stock_code" in column_names else "unknown",
                    family,
                    registry_status if not base_feature else f"{registry_status}:{base_feature}",
                    bool(spec.enabled) if spec else None,
                    bool(spec.production_ready) if spec else None,
                    bool(spec.candidate_only) if spec else None,
                    bool(spec.label) if spec else feature.startswith("forward_ret_"),
                    model_input,
                    _json(list(spec.source_tables) if spec else []),
                    _json(list(spec.required_capabilities) if spec else []),
                    int(spec.pit_release_lag_days) if spec else 0,
                    risk,
                    True,
                    total_rows,
                    non_null_rows,
                    coverage_pct,
                    min_signal_date,
                    max_signal_date,
                    plan["source_event_date_column"],
                    plan["source_available_date_column"],
                    allowed,
                    built_at,
                )
            )
            join_rows.append(
                (
                    run_id,
                    table_name,
                    feature,
                    risk,
                    _json(plan["join_keys"]),
                    plan["signal_date_column"],
                    plan["source_event_date_column"],
                    plan["source_available_date_column"],
                    plan["lag_policy_days"],
                    plan["join_policy"],
                    bool(production_blocking),
                    plan["notes"],
                    built_at,
                )
            )
            for reason_code, detail, blocking in reasons:
                exclusion_rows.append((run_id, table_name, feature, reason_code, detail, bool(blocking), built_at))

    if catalog_rows:
        conn.executemany(
            """
            INSERT OR REPLACE INTO mart_feature_catalog_current (
                run_id, feature_table, feature_name, data_type, ordinal_position,
                grain, feature_family, registry_status, enabled, production_ready,
                candidate_only, label, model_input, source_tables_json,
                required_capabilities_json, pit_release_lag_days, pit_risk_level,
                table_exists, total_rows, non_null_rows, coverage_pct,
                min_signal_date, max_signal_date, source_event_date_column,
                source_available_date_column, allowed_in_production_research,
                built_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            catalog_rows,
        )
    if join_rows:
        conn.executemany(
            """
            INSERT OR REPLACE INTO mart_feature_pit_join_plan (
                run_id, feature_table, feature_name, pit_risk_level,
                join_keys_json, signal_date_column, source_event_date_column,
                source_available_date_column, lag_policy_days, join_policy,
                production_blocking, notes, built_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            join_rows,
        )
    if exclusion_rows:
        conn.executemany(
            """
            INSERT OR REPLACE INTO mart_feature_exclusion_reason (
                run_id, feature_table, feature_name, reason_code, reason_detail,
                production_blocking, built_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            exclusion_rows,
        )

    for table in ("mart_feature_catalog_current", "mart_feature_pit_join_plan", "mart_feature_exclusion_reason"):
        record_actual_version(conn, table)

    summary = {
        "run_id": run_id,
        "scanned_tables": scanned_tables,
        "missing_tables": missing_tables,
        "catalog_rows": len(catalog_rows),
        "join_plan_rows": len(join_rows),
        "exclusion_rows": len(exclusion_rows),
        "allowed_features": sum(1 for row in catalog_rows if row[-2]),
        "blocked_features": sum(1 for row in join_rows if row[-3]),
    }
    record_pipeline_run(
        conn,
        run_id=run_id,
        pipeline_name="build_feature_catalog_current",
        status="success",
        started_at=started_at,
        ended_at=utc_now_iso(),
        commit_sha=git_commit_sha(Path(__file__).resolve().parent.parent.parent),
        input_tables=feature_tables,
        output_tables=[
            "mart_feature_catalog_current",
            "mart_feature_pit_join_plan",
            "mart_feature_exclusion_reason",
        ],
        gate_result="pass",
        blockers=[],
        perf_summary=summary,
    )
    conn.commit()
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--feature-table", action="append", dest="feature_tables")
    args = parser.parse_args()
    with get_conn() as conn:
        result = build_feature_catalog_current(
            conn,
            run_id=args.run_id,
            feature_tables=args.feature_tables,
        )
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
