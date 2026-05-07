"""Global data quality gates for zero-silent-missing policy."""
from __future__ import annotations

import json
import re
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from services.data_deletion import ensure_data_deletion_tables
from services.data_processing_monitor import ensure_data_processing_monitor_tables
from services.feature_registry import load_feature_registry
from services.pipeline_manifest import git_commit_sha, record_pipeline_run, utc_now_iso
from services.pipeline_performance_policy import load_pipeline_performance_policy
from services.pricing_policy import load_pricing_label_policy, record_pricing_label_policy
from services.recommendation_universe import (
    explain_universe_exclusions,
    load_recommendation_universe_policy,
)
from services.schema_versions import record_actual_version
from services.utils import latest_completed_trade_date


REPO = Path(__file__).resolve().parent.parent.parent
WORKSPACE_ROOT = REPO.parent
DATA_DIR = REPO / "data"
MARKET_DB = DATA_DIR / "market.duckdb"
DELETE_POLICY = "verified_direct_delete_no_archive"

DDL = """
CREATE TABLE IF NOT EXISTS mart_global_data_quality_gate (
    gate_run_id TEXT PRIMARY KEY,
    policy_id TEXT NOT NULL,
    policy_hash TEXT NOT NULL,
    gate_scope TEXT NOT NULL,
    gate_status TEXT NOT NULL,
    blockers_json TEXT,
    warnings_json TEXT,
    evidence_json TEXT,
    started_at TEXT NOT NULL,
    ended_at TEXT NOT NULL,
    duration_s DOUBLE
);

CREATE TABLE IF NOT EXISTS mart_global_data_quality_detail (
    gate_run_id TEXT NOT NULL,
    domain TEXT NOT NULL,
    table_name TEXT,
    column_name TEXT,
    check_name TEXT NOT NULL,
    status TEXT NOT NULL,
    severity TEXT NOT NULL,
    row_count BIGINT,
    violation_count BIGINT,
    reason TEXT,
    examples_json TEXT,
    built_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_global_dq_detail_run
    ON mart_global_data_quality_detail(gate_run_id);

CREATE TABLE IF NOT EXISTS mart_feature_null_policy (
    policy_key TEXT PRIMARY KEY,
    table_name TEXT NOT NULL,
    column_pattern TEXT NOT NULL,
    match_type TEXT NOT NULL,
    null_class TEXT NOT NULL,
    null_reason TEXT NOT NULL,
    source_family TEXT,
    train_blocking BOOLEAN NOT NULL,
    production_allowed BOOLEAN NOT NULL,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    notes TEXT,
    built_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_feature_null_policy_lookup
    ON mart_feature_null_policy(table_name, enabled);

CREATE TABLE IF NOT EXISTS mart_candidate_feature_set_contract (
    feature_set_id TEXT NOT NULL,
    feature_name TEXT NOT NULL,
    required BOOLEAN NOT NULL DEFAULT TRUE,
    min_coverage_pct DOUBLE NOT NULL DEFAULT 95.0,
    null_policy TEXT NOT NULL DEFAULT 'allow_contractual_sparse_nulls',
    contract_source TEXT NOT NULL,
    built_at TEXT NOT NULL,
    PRIMARY KEY (feature_set_id, feature_name, contract_source)
);
CREATE INDEX IF NOT EXISTS idx_candidate_feature_contract_set
    ON mart_candidate_feature_set_contract(feature_set_id);

CREATE TABLE IF NOT EXISTS mart_feature_availability_contract (
    feature_name TEXT PRIMARY KEY,
    feature_group TEXT NOT NULL,
    feature_role TEXT NOT NULL,
    availability_cadence TEXT NOT NULL,
    panel_density TEXT NOT NULL,
    expected_update_frequency TEXT NOT NULL,
    null_policy TEXT NOT NULL,
    coverage_universe TEXT NOT NULL,
    model_input BOOLEAN NOT NULL,
    production_ready BOOLEAN NOT NULL,
    enabled BOOLEAN NOT NULL,
    frontend_visible BOOLEAN NOT NULL,
    pit_release_lag_days INTEGER NOT NULL DEFAULT 0,
    source_tables_json TEXT,
    required_capabilities_json TEXT,
    notes TEXT,
    built_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_feature_availability_contract_role
    ON mart_feature_availability_contract(feature_role, availability_cadence, panel_density);
"""

FEATURE_PANEL_KEY_CANDIDATES = ("feature_set_id", "stock_code", "date")
FOLLOW_LABEL_PREFIX = "follow_net_return_"
LEGACY_LABEL_PREFIXES = ("forward_ret_", "return_", "label_")
INVALID_FEATURE_SET_IDS = {"", "none", "null", "nan"}

_PROD_ROLLING_NULL_COLUMNS = {
    "ret_1d",
    "ret_5d",
    "ret_20d",
    "ret_60d",
    "vol_z20d",
    "vol_ratio_5_20",
    "vol_std_5d",
    "vol_std_20d",
    "range_pos_20",
    "range_pos_60",
    "momentum_diff",
    "amount_chg_5d",
    "ret_20d_rank",
    "ret_60d_rank",
    "vol_z20d_rank",
    "amount_chg_5d_rank",
}

_PROD_SOURCE_GAP_NULL_COLUMNS = {
    "rz_balance",
    "rz_chg_5d_pct",
    "rz_balance_rank",
    "rz_chg_5d_pct_rank",
    "rz_balance_to_amount20",
    "shareholder_count_qoq",
    "inst_count_qoq",
    "fund_count_qoq",
    "qfii_count_qoq",
    "yjyg_lower_pct",
    "yjyg_upper_pct",
    "roe",
    "eps_basic",
}


def _default_feature_null_policies(built_at: str) -> list[dict[str, Any]]:
    try:
        production_inputs = set(load_feature_registry().model_input_columns())
    except Exception:
        production_inputs = set()
    policies: list[dict[str, Any]] = [
        {
            "policy_key": "prod_forward_ret_diagnostic_tail",
            "table_name": "fact_feature_panel",
            "column_pattern": "forward_ret_%",
            "match_type": "like",
            "null_class": "diagnostic_alpha_label_future_immature",
            "null_reason": (
                "legacy forward_ret labels are diagnostic only; tail nulls are "
                "future immature horizon windows and are excluded from champion training"
            ),
            "source_family": "diagnostic_label",
            "train_blocking": False,
            "production_allowed": False,
            "notes": "primary training labels are follow_net_return_*",
            "built_at": built_at,
        },
        {
            "policy_key": "candidate_forward_ret_diagnostic_or_contract",
            "table_name": "fact_feature_panel_candidate",
            "column_pattern": "forward_ret_%",
            "match_type": "like",
            "null_class": "diagnostic_alpha_label_or_candidate_contract_gap",
            "null_reason": (
                "candidate forward_ret labels are diagnostic and must be judged by "
                "candidate feature_set_id contract before production use"
            ),
            "source_family": "diagnostic_label",
            "train_blocking": False,
            "production_allowed": False,
            "notes": "primary training labels are follow_net_return_*",
            "built_at": built_at,
        },
        {
            "policy_key": "prod_industry_relative_dependency_gap",
            "table_name": "fact_feature_panel",
            "column_pattern": "%_tdx_l1_rel",
            "match_type": "like",
            "null_class": "industry_relative_dependency_gap",
            "null_reason": (
                "industry-relative feature is null when the base feature or TDX L1 "
                "industry bucket is unavailable; this remains train-blocking until "
                "base-feature and industry coverage are repaired or rows are excluded"
            ),
            "source_family": "industry_relative_transform",
            "train_blocking": True,
            "production_allowed": False,
            "notes": "classification only; not a pass",
            "built_at": built_at,
        },
        {
            "policy_key": "prod_hs300_regime_benchmark_gap",
            "table_name": "fact_feature_panel",
            "column_pattern": "hs300_ret_%",
            "match_type": "like",
            "null_class": "benchmark_kline_coverage_gap",
            "null_reason": (
                "benchmark/regime features are null because HS300 proxy K-line "
                "coverage is behind the feature-panel dates; benchmark fetch or "
                "regime fallback must be fixed before production use"
            ),
            "source_family": "benchmark_regime",
            "train_blocking": True,
            "production_allowed": False,
            "notes": "classification only; not a pass",
            "built_at": built_at,
        },
        {
            "policy_key": "prod_regime_flag_benchmark_gap",
            "table_name": "fact_feature_panel",
            "column_pattern": "regime_flag",
            "match_type": "exact",
            "null_class": "benchmark_kline_coverage_gap",
            "null_reason": (
                "regime_flag is null when HS300 proxy K-line is unavailable for "
                "the signal date; this remains train-blocking"
            ),
            "source_family": "benchmark_regime",
            "train_blocking": True,
            "production_allowed": False,
            "notes": "classification only; not a pass",
            "built_at": built_at,
        },
        {
            "policy_key": "candidate_panel_feature_set_contract_gap",
            "table_name": "fact_feature_panel_candidate",
            "column_pattern": "%",
            "match_type": "like",
            "null_class": "candidate_feature_set_contract_gap",
            "null_reason": (
                "candidate panel mixes multiple feature families in one table; "
                "nulls must be validated per feature_set_id contract before the "
                "column can enter production research"
            ),
            "source_family": "candidate_feature_panel",
            "train_blocking": True,
            "production_allowed": False,
            "notes": "broad conservative classifier until per-feature_set contracts are implemented",
            "built_at": built_at,
        },
    ]
    for column in sorted(_PROD_ROLLING_NULL_COLUMNS):
        policies.append(
            {
                "policy_key": f"prod_rolling_warmup_{column}",
                "table_name": "fact_feature_panel",
                "column_pattern": column,
                "match_type": "exact",
                "null_class": "rolling_history_warmup_or_dependency_gap",
                "null_reason": (
                    "rolling/trailing feature is null for first available stock rows "
                    "or when its base window dependency is unavailable; rows must be "
                    "excluded or imputed inside the train fold"
                ),
                "source_family": "price_volume_rolling",
                "train_blocking": False,
                "production_allowed": True,
                "notes": "allowed as classified warmup nulls, not silent missing data",
                "built_at": built_at,
            }
        )
    for column in sorted(_PROD_SOURCE_GAP_NULL_COLUMNS):
        train_blocking = column in production_inputs
        policies.append(
            {
                "policy_key": f"prod_source_gap_{column}",
                "table_name": "fact_feature_panel",
                "column_pattern": column,
                "match_type": "exact",
                "null_class": (
                    "source_coverage_gap_requires_backfill"
                    if train_blocking
                    else "source_coverage_gap_excluded_from_production_training"
                ),
                "null_reason": (
                    "source-derived institution, financing, holder, forecast, or "
                    "fundamental feature is not available for all stock-date rows; "
                    + (
                        "root cause must be backfilled, excluded, or marked with a "
                        "source-availability contract before training"
                        if train_blocking
                        else "feature is explicitly excluded from production training "
                        "until source coverage/backfill is complete"
                    )
                ),
                "source_family": "source_coverage",
                "train_blocking": train_blocking,
                "production_allowed": False,
                "notes": (
                    "classification only; not a pass"
                    if train_blocking
                    else "not production_allowed; pass only because feature registry excludes it from production inputs"
                ),
                "built_at": built_at,
            }
        )
    return policies


def ensure_global_data_quality_tables(conn: Any) -> None:
    if hasattr(conn, "executescript"):
        conn.executescript(DDL)
    else:
        for stmt in DDL.split(";"):
            stmt = stmt.strip()
            if stmt:
                conn.execute(stmt)
    ensure_data_processing_monitor_tables(conn)
    ensure_data_deletion_tables(conn)
    _seed_default_feature_null_policies(conn)
    _seed_feature_availability_contracts(conn)
    _seed_candidate_feature_set_contracts(conn)


def _seed_default_feature_null_policies(conn: Any) -> None:
    built_at = datetime.now(UTC).replace(tzinfo=None).isoformat(timespec="seconds")
    rows = [
        (
            policy["policy_key"],
            policy["table_name"],
            policy["column_pattern"],
            policy["match_type"],
            policy["null_class"],
            policy["null_reason"],
            policy.get("source_family"),
            bool(policy["train_blocking"]),
            bool(policy["production_allowed"]),
            True,
            policy.get("notes"),
            policy["built_at"],
        )
        for policy in _default_feature_null_policies(built_at)
    ]
    policy_keys = [row[0] for row in rows]
    if policy_keys:
        placeholders = ", ".join("?" for _ in policy_keys)
        conn.execute(
            f"DELETE FROM mart_feature_null_policy WHERE policy_key IN ({placeholders})",
            policy_keys,
        )
    conn.executemany(
        """
        INSERT INTO mart_feature_null_policy (
            policy_key, table_name, column_pattern, match_type,
            null_class, null_reason, source_family,
            train_blocking, production_allowed, enabled, notes, built_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )


def _seed_feature_availability_contracts(conn: Any) -> None:
    built_at = datetime.now(UTC).replace(tzinfo=None).isoformat(timespec="seconds")
    registry = load_feature_registry()
    rows = [
        (
            spec.name,
            spec.group,
            spec.feature_role,
            spec.availability_cadence,
            spec.panel_density,
            spec.expected_update_frequency,
            spec.null_policy,
            spec.coverage_universe,
            bool(spec.model_input),
            bool(spec.production_ready),
            bool(spec.enabled),
            bool(spec.frontend_visible),
            int(spec.pit_release_lag_days),
            json.dumps(list(spec.source_tables), ensure_ascii=False, sort_keys=True),
            json.dumps(list(spec.required_capabilities), ensure_ascii=False, sort_keys=True),
            spec.notes,
            built_at,
        )
        for spec in registry.features.values()
    ]
    conn.execute("DELETE FROM mart_feature_availability_contract")
    if rows:
        conn.executemany(
            """
            INSERT INTO mart_feature_availability_contract (
                feature_name, feature_group, feature_role,
                availability_cadence, panel_density,
                expected_update_frequency, null_policy, coverage_universe,
                model_input, production_ready, enabled, frontend_visible,
                pit_release_lag_days, source_tables_json,
                required_capabilities_json, notes, built_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )


def _json_list(value: Any) -> list[str]:
    if not value:
        return []
    try:
        loaded = json.loads(str(value))
    except Exception:
        return []
    if not isinstance(loaded, list):
        return []
    return [str(item) for item in loaded if str(item or "").strip()]


def _normal_feature_set_id(value: Any) -> str:
    feature_set_id = str(value or "").strip()
    return "" if feature_set_id.lower() in INVALID_FEATURE_SET_IDS else feature_set_id


def _feature_set_from_notes(notes: Any) -> str | None:
    text = str(notes or "")
    match = re.search(r"feature_set_id=([A-Za-z0-9_\\-]+)", text)
    return match.group(1) if match else None


def _current_candidate_feature_set_ids(conn: Any) -> set[str]:
    if not _table_exists(conn, "fact_feature_panel_candidate"):
        return set()
    columns = _table_columns(conn, "fact_feature_panel_candidate")
    if "feature_set_id" not in columns:
        return set()
    rows = conn.execute(
        """
        SELECT DISTINCT feature_set_id
          FROM fact_feature_panel_candidate
         WHERE feature_set_id IS NOT NULL
           AND TRIM(CAST(feature_set_id AS VARCHAR)) <> ''
        """
    ).fetchall()
    return {
        feature_set_id
        for row in rows
        if (feature_set_id := _normal_feature_set_id(_row_value(row, "feature_set_id", 0)))
    }


def _seed_candidate_feature_set_contracts(conn: Any) -> None:
    built_at = datetime.now(UTC).replace(tzinfo=None).isoformat(timespec="seconds")
    contracts: dict[tuple[str, str, str], tuple[Any, ...]] = {}
    active_feature_set_ids = _current_candidate_feature_set_ids(conn)

    if _table_exists(conn, "mart_model_selection_run"):
        rows = conn.execute(
            """
            SELECT feature_set_id, selected_features_json
              FROM mart_model_selection_run
             WHERE feature_set_id IS NOT NULL
               AND selected_features_json IS NOT NULL
             ORDER BY built_at
            """
        ).fetchall()
        for row in rows:
            feature_set_id = _normal_feature_set_id(_row_value(row, "feature_set_id", 0))
            if not feature_set_id or feature_set_id not in active_feature_set_ids:
                continue
            for feature in _json_list(_row_value(row, "selected_features_json", 1)):
                contracts[(feature_set_id, feature, "model_selection_selected")] = (
                    feature_set_id,
                    feature,
                    True,
                    95.0,
                    "allow_selected_feature_contract_nulls",
                    "model_selection_selected",
                    built_at,
                )

    if _table_exists(conn, "mart_multidim_model"):
        rows = conn.execute(
            """
            SELECT notes, feature_cols_json
              FROM mart_multidim_model
             WHERE feature_cols_json IS NOT NULL
             ORDER BY created_at
            """
        ).fetchall()
        for row in rows:
            feature_set_id = _normal_feature_set_id(_feature_set_from_notes(_row_value(row, "notes", 0)))
            if not feature_set_id or feature_set_id not in active_feature_set_ids:
                continue
            for feature in _json_list(_row_value(row, "feature_cols_json", 1)):
                contracts[(feature_set_id, feature, "multidim_model_features")] = (
                    feature_set_id,
                    feature,
                    True,
                    95.0,
                    "allow_model_feature_contract_nulls",
                    "multidim_model_features",
                    built_at,
                )

    if _table_exists(conn, "mart_feature_retention_decision"):
        rows = conn.execute(
            """
            SELECT feature_set_id, feature_name, coverage_pct
              FROM mart_feature_retention_decision
             WHERE decision = 'keep'
             ORDER BY built_at
            """
        ).fetchall()
        for row in rows:
            feature_set_id = _normal_feature_set_id(_row_value(row, "feature_set_id", 0))
            feature = str(_row_value(row, "feature_name", 1) or "").strip()
            if not feature_set_id or feature_set_id not in active_feature_set_ids or not feature:
                continue
            coverage = float(_row_value(row, "coverage_pct", 2) or 0.0)
            min_coverage = max(0.0, min(100.0, coverage - 0.5))
            contracts[(feature_set_id, feature, "retention_keep")] = (
                feature_set_id,
                feature,
                True,
                min_coverage,
                "allow_retention_coverage_contract_nulls",
                "retention_keep",
                built_at,
            )

    sources = ["model_selection_selected", "multidim_model_features", "retention_keep"]
    placeholders = ", ".join("?" for _ in sources)
    conn.execute(
        f"DELETE FROM mart_candidate_feature_set_contract WHERE contract_source IN ({placeholders})",
        sources,
    )
    if contracts:
        conn.executemany(
            """
            INSERT INTO mart_candidate_feature_set_contract (
                feature_set_id, feature_name, required, min_coverage_pct,
                null_policy, contract_source, built_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            list(contracts.values()),
        )


def _emit_progress(message: str) -> None:
    print(f"[global_dq] {utc_now_iso()} {message}", flush=True)


def _row_value(row: Any, key: str, index: int) -> Any:
    if row is None:
        return None
    try:
        return row[key]
    except Exception:
        return row[index]


def _quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _quote_table(name: str) -> str:
    return ".".join(_quote_ident(part) for part in name.split("."))


def _table_parts(table_name: str) -> tuple[str | None, str]:
    parts = table_name.split(".", 1)
    return (parts[0], parts[1]) if len(parts) == 2 else (None, table_name)


def _table_exists(conn: Any, table_name: str) -> bool:
    schema, table = _table_parts(table_name)
    try:
        conn.execute(f"SELECT 1 FROM {_quote_table(table_name)} LIMIT 0").fetchone()
        return True
    except Exception:
        pass
    if schema:
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
            (table,),
        ).fetchone()
    return row is not None


def _schema_exists(conn: Any, schema: str) -> bool:
    row = conn.execute(
        """
        SELECT 1
          FROM information_schema.schemata
         WHERE schema_name = ?
         LIMIT 1
        """,
        (schema,),
    ).fetchone()
    if row is not None:
        return True
    try:
        row = conn.execute(
            """
            SELECT 1
              FROM duckdb_databases()
             WHERE database_name = ?
             LIMIT 1
            """,
            (schema,),
        ).fetchone()
        return row is not None
    except Exception:
        return False


def attach_market_if_available(conn: Any) -> bool:
    if _schema_exists(conn, "market"):
        return True
    if not MARKET_DB.exists():
        return False
    try:
        conn.execute(f"ATTACH IF NOT EXISTS '{MARKET_DB}' AS market (READ_ONLY)")
        return _schema_exists(conn, "market")
    except Exception:
        return False


def _table_columns(conn: Any, table_name: str) -> dict[str, str]:
    schema, table = _table_parts(table_name)
    if not _table_exists(conn, table_name):
        return {}
    if schema:
        rows = conn.execute(
            """
            SELECT column_name, data_type
              FROM information_schema.columns
             WHERE (table_schema = ? OR table_catalog = ?)
               AND table_name = ?
             ORDER BY ordinal_position
            """,
            (schema, schema, table),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT column_name, data_type
              FROM information_schema.columns
             WHERE table_name = ?
             ORDER BY ordinal_position
            """,
            (table,),
        ).fetchall()
    return {str(_row_value(row, "column_name", 0)): str(_row_value(row, "data_type", 1)) for row in rows}


def _count_rows(conn: Any, table_name: str, where_sql: str | None = None) -> int:
    if not _table_exists(conn, table_name):
        return 0
    where = f" WHERE {where_sql}" if where_sql else ""
    row = conn.execute(f"SELECT COUNT(*) AS n FROM {_quote_table(table_name)}{where}").fetchone()
    return int(_row_value(row, "n", 0) or 0)


def _safe_json_load(value: Any) -> Any:
    if not value:
        return None
    try:
        return json.loads(value)
    except Exception:
        return None


def _detail(
    *,
    domain: str,
    check_name: str,
    status: str,
    severity: str = "blocker",
    table_name: str | None = None,
    column_name: str | None = None,
    row_count: int | None = None,
    violation_count: int | None = None,
    reason: str | None = None,
    examples: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "domain": domain,
        "table_name": table_name,
        "column_name": column_name,
        "check_name": check_name,
        "status": status,
        "severity": severity,
        "row_count": row_count,
        "violation_count": violation_count,
        "reason": reason,
        "examples": examples or [],
    }


def _sample_examples(
    conn: Any,
    table_name: str,
    *,
    where_sql: str,
    columns: list[str],
    limit: int,
) -> list[dict[str, Any]]:
    if limit <= 0:
        return []
    select_cols = ", ".join(_quote_ident(col) for col in columns)
    rows = conn.execute(
        f"""
        SELECT {select_cols}
          FROM {_quote_table(table_name)}
         WHERE {where_sql}
         LIMIT {int(limit)}
        """
    ).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        out.append({col: _row_value(row, col, idx) for idx, col in enumerate(columns)})
    return out


def _business_keys_for(columns: set[str]) -> list[str]:
    keys = [key for key in FEATURE_PANEL_KEY_CANDIDATES if key in columns]
    if "stock_code" in columns and "date" in columns and "feature_set_id" not in columns:
        keys = ["stock_code", "date"]
    return keys


def _is_label_column(column: str) -> bool:
    return column.startswith(FOLLOW_LABEL_PREFIX) or column.startswith(LEGACY_LABEL_PREFIXES)


def _industry_relative_base_column(column: str) -> str | None:
    suffix = "_tdx_l1_rel"
    if not column.endswith(suffix):
        return None
    base = column[: -len(suffix)]
    return base or None


def _latest_follow_label_quality(conn: Any, feature_table: str) -> dict[str, dict[str, Any]]:
    if not _table_exists(conn, "mart_follow_return_label_build") or not _table_exists(
        conn,
        "mart_follow_return_label_quality",
    ):
        return {}
    build = conn.execute(
        """
        SELECT run_id, built_at
          FROM mart_follow_return_label_build
         WHERE feature_table = ?
         ORDER BY built_at DESC
         LIMIT 1
        """,
        (feature_table,),
    ).fetchone()
    if not build:
        return {}
    run_id = _row_value(build, "run_id", 0)
    built_at = _row_value(build, "built_at", 1)
    rows = conn.execute(
        """
        SELECT label_name,
               row_count,
               non_null_count,
               null_count,
               immature_null_count,
               mature_null_count,
               missing_signal_kline_count,
               missing_entry_price_count,
               missing_exit_price_count,
               unclassified_null_count
          FROM mart_follow_return_label_quality
         WHERE feature_table = ?
           AND run_id = ?
        """,
        (feature_table, run_id),
    ).fetchall()
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        label = str(_row_value(row, "label_name", 0))
        out[label] = {
            "run_id": str(run_id),
            "built_at": built_at,
            "row_count": int(_row_value(row, "row_count", 1) or 0),
            "non_null_count": int(_row_value(row, "non_null_count", 2) or 0),
            "null_count": int(_row_value(row, "null_count", 3) or 0),
            "immature_null_count": int(_row_value(row, "immature_null_count", 4) or 0),
            "mature_null_count": int(_row_value(row, "mature_null_count", 5) or 0),
            "missing_signal_kline_count": int(_row_value(row, "missing_signal_kline_count", 6) or 0),
            "missing_entry_price_count": int(_row_value(row, "missing_entry_price_count", 7) or 0),
            "missing_exit_price_count": int(_row_value(row, "missing_exit_price_count", 8) or 0),
            "unclassified_null_count": int(_row_value(row, "unclassified_null_count", 9) or 0),
        }
    return out


def _clean_follow_label_quality(
    label_quality: dict[str, Any] | None,
    *,
    row_count: int,
    null_count: int,
    exact_current_table: bool,
) -> bool:
    if not label_quality:
        return False
    quality_row_count = int(label_quality.get("row_count") or 0)
    quality_null_count = int(label_quality.get("null_count") or 0)
    quality_non_null_count = int(label_quality.get("non_null_count") or 0)
    if exact_current_table:
        if quality_row_count != int(row_count) or quality_null_count != int(null_count):
            return False
        if quality_non_null_count != int(row_count) - int(null_count):
            return False
    else:
        if quality_row_count < int(row_count) or quality_null_count < int(null_count):
            return False
    return (
        quality_null_count == int(label_quality.get("immature_null_count") or 0)
        and int(label_quality.get("mature_null_count") or 0) == 0
        and int(label_quality.get("missing_signal_kline_count") or 0) == 0
        and int(label_quality.get("missing_entry_price_count") or 0) == 0
        and int(label_quality.get("missing_exit_price_count") or 0) == 0
        and int(label_quality.get("unclassified_null_count") or 0) == 0
    )


def _match_null_policy(conn: Any, table_name: str, column_name: str) -> dict[str, Any] | None:
    if not _table_exists(conn, "mart_feature_null_policy"):
        return None
    rows = conn.execute(
        """
        SELECT policy_key,
               table_name,
               column_pattern,
               match_type,
               null_class,
               null_reason,
               source_family,
               train_blocking,
               production_allowed,
               notes
          FROM mart_feature_null_policy
         WHERE enabled = TRUE
           AND table_name IN (?, '*')
         ORDER BY
               CASE WHEN table_name = ? THEN 0 ELSE 1 END,
               CASE match_type
                 WHEN 'exact' THEN 0
                 WHEN 'like' THEN 1
                 WHEN 'prefix' THEN 2
                 WHEN 'suffix' THEN 3
                 WHEN 'contains' THEN 4
                 ELSE 5
               END,
               LENGTH(column_pattern) DESC
        """,
        (table_name, table_name),
    ).fetchall()
    for row in rows:
        pattern = str(_row_value(row, "column_pattern", 2))
        match_type = str(_row_value(row, "match_type", 3)).lower()
        matched = False
        if match_type == "exact":
            matched = column_name == pattern
        elif match_type == "like":
            sql_pattern = pattern.replace("%", "*")
            # DuckDB LIKE rules are used in SQL, but fnmatch-style matching
            # keeps policy lookup cheap and connection-agnostic in Python.
            import fnmatch

            matched = fnmatch.fnmatchcase(column_name, sql_pattern)
        elif match_type == "prefix":
            matched = column_name.startswith(pattern)
        elif match_type == "suffix":
            matched = column_name.endswith(pattern)
        elif match_type == "contains":
            matched = pattern in column_name
        if not matched:
            continue
        return {
            "policy_key": _row_value(row, "policy_key", 0),
            "table_name": _row_value(row, "table_name", 1),
            "column_pattern": pattern,
            "match_type": match_type,
            "null_class": _row_value(row, "null_class", 4),
            "null_reason": _row_value(row, "null_reason", 5),
            "source_family": _row_value(row, "source_family", 6),
            "train_blocking": bool(_row_value(row, "train_blocking", 7)),
            "production_allowed": bool(_row_value(row, "production_allowed", 8)),
            "notes": _row_value(row, "notes", 9),
        }
    return None


def _base_feature_for_availability_contract(column_name: str) -> str | None:
    suffixes = (
        "_xs_bucket5",
        "_xs_rank",
        "_tdx_l1_rel",
    )
    for suffix in suffixes:
        if column_name.endswith(suffix):
            base = column_name[: -len(suffix)]
            return base or None
    if column_name.endswith("_rank"):
        base = column_name[: -len("_rank")]
        return base or None
    return None


def _min_history_rows_for_feature(column_name: str) -> int | None:
    base = _base_feature_for_availability_contract(column_name) or column_name
    patterns = (
        ("250", 250),
        ("120", 120),
        ("90", 90),
        ("60", 60),
        ("30", 30),
        ("20", 20),
        ("10", 10),
        ("5", 5),
        ("1", 1),
    )
    for token, rows in patterns:
        if token in base:
            return rows
    if base in {"momentum_diff"}:
        return 60
    return None


def _feature_availability_contract(conn: Any, column_name: str) -> dict[str, Any] | None:
    if not _table_exists(conn, "mart_feature_availability_contract"):
        return None
    candidates = [column_name]
    base = _base_feature_for_availability_contract(column_name)
    if base:
        candidates.append(base)
    placeholders = ", ".join("?" for _ in candidates)
    rows = conn.execute(
        f"""
        SELECT feature_name,
               feature_group,
               feature_role,
               availability_cadence,
               panel_density,
               expected_update_frequency,
               null_policy,
               coverage_universe,
               model_input,
               production_ready,
               enabled,
               frontend_visible,
               pit_release_lag_days,
               notes
          FROM mart_feature_availability_contract
         WHERE feature_name IN ({placeholders})
         ORDER BY CASE WHEN feature_name = ? THEN 0 ELSE 1 END
         LIMIT 1
        """,
        [*candidates, column_name],
    ).fetchone()
    if not rows:
        return None
    return {
        "feature_name": _row_value(rows, "feature_name", 0),
        "matched_from": "exact" if _row_value(rows, "feature_name", 0) == column_name else "base_feature",
        "feature_group": _row_value(rows, "feature_group", 1),
        "feature_role": _row_value(rows, "feature_role", 2),
        "availability_cadence": _row_value(rows, "availability_cadence", 3),
        "panel_density": _row_value(rows, "panel_density", 4),
        "expected_update_frequency": _row_value(rows, "expected_update_frequency", 5),
        "null_policy": _row_value(rows, "null_policy", 6),
        "coverage_universe": _row_value(rows, "coverage_universe", 7),
        "model_input": bool(_row_value(rows, "model_input", 8)),
        "production_ready": bool(_row_value(rows, "production_ready", 9)),
        "enabled": bool(_row_value(rows, "enabled", 10)),
        "frontend_visible": bool(_row_value(rows, "frontend_visible", 11)),
        "pit_release_lag_days": int(_row_value(rows, "pit_release_lag_days", 12) or 0),
        "notes": _row_value(rows, "notes", 13),
    }


def _null_temporal_scope(
    conn: Any,
    table_name: str,
    column_name: str,
    columns: dict[str, str],
    *,
    where_sql: str | None = None,
    params: list[Any] | None = None,
    min_history_rows: int | None = None,
) -> dict[str, Any]:
    if "stock_code" not in columns or "date" not in columns:
        return {"checked": False, "reason": "missing_stock_or_date_key"}
    params = params or []
    where = f"WHERE {where_sql}" if where_sql else ""
    null_where = f"WHERE {where_sql} AND t.{_quote_ident(column_name)} IS NULL" if where_sql else (
        f"WHERE t.{_quote_ident(column_name)} IS NULL"
    )
    min_history = int(min_history_rows or 0)
    row = conn.execute(
        f"""
        WITH stock_scope AS (
            SELECT stock_code,
                   COUNT(*) AS stock_rows,
                   MIN(CASE WHEN {_quote_ident(column_name)} IS NOT NULL THEN date ELSE NULL END) AS first_valid_date
              FROM {_quote_table(table_name)}
              {where}
             GROUP BY stock_code
        )
        SELECT COUNT(*) AS null_rows,
               SUM(CASE WHEN sc.first_valid_date IS NULL THEN 1 ELSE 0 END) AS never_valid_nulls,
               SUM(CASE WHEN sc.first_valid_date IS NULL AND sc.stock_rows > ? THEN 1 ELSE 0 END)
                   AS never_valid_sufficient_history_nulls,
               SUM(CASE WHEN sc.first_valid_date IS NULL AND sc.stock_rows <= ? THEN 1 ELSE 0 END)
                   AS never_valid_insufficient_history_nulls,
               SUM(CASE WHEN sc.first_valid_date IS NOT NULL AND t.date > sc.first_valid_date THEN 1 ELSE 0 END)
                   AS post_first_valid_nulls,
               SUM(CASE WHEN sc.first_valid_date IS NOT NULL AND t.date <= sc.first_valid_date THEN 1 ELSE 0 END)
                   AS pre_first_valid_nulls,
               MIN(t.date) AS min_null_date,
               MAX(t.date) AS max_null_date
          FROM {_quote_table(table_name)} t
          LEFT JOIN stock_scope sc USING (stock_code)
          {null_where}
        """,
        [*params, min_history, min_history, *params],
    ).fetchone()
    return {
        "checked": True,
        "null_rows": int(_row_value(row, "null_rows", 0) or 0),
        "never_valid_nulls": int(_row_value(row, "never_valid_nulls", 1) or 0),
        "never_valid_sufficient_history_nulls": int(
            _row_value(row, "never_valid_sufficient_history_nulls", 2) or 0
        ),
        "never_valid_insufficient_history_nulls": int(
            _row_value(row, "never_valid_insufficient_history_nulls", 3) or 0
        ),
        "post_first_valid_nulls": int(_row_value(row, "post_first_valid_nulls", 4) or 0),
        "pre_first_valid_nulls": int(_row_value(row, "pre_first_valid_nulls", 5) or 0),
        "min_null_date": _row_value(row, "min_null_date", 6),
        "max_null_date": _row_value(row, "max_null_date", 7),
        "min_history_rows": min_history,
    }


def _availability_null_outcome(
    conn: Any,
    table_name: str,
    column_name: str,
    columns: dict[str, str],
    contract: dict[str, Any],
    *,
    row_count: int,
    null_count: int,
    where_sql: str | None = None,
    params: list[Any] | None = None,
    context: str = "",
) -> dict[str, Any] | None:
    if null_count <= 0:
        return None
    null_policy = str(contract.get("null_policy") or "").lower()
    prefix = f"{context} " if context else ""
    if null_policy in {"no_null", "block_unclassified_null", "encode_no_event_as_zero_or_days_since"}:
        if null_policy == "encode_no_event_as_zero_or_days_since":
            reason = (
                f"{prefix}feature_role={contract['feature_role']} cadence={contract['availability_cadence']} "
                "is event-driven, but the daily panel must encode no-event as 0/count/days_since; "
                "NULL indicates event ETL or feature-fill failure"
            )
        else:
            reason = (
                f"{prefix}availability contract requires dense non-null values "
                f"(role={contract['feature_role']}, cadence={contract['availability_cadence']}, "
                f"density={contract['panel_density']})"
            )
        return {
            "status": "fail",
            "check_name": "availability_contract_null_rule_violation",
            "violation_count": null_count,
            "reason": reason,
            "bucket": "train_blocking",
        }
    if null_policy == "excluded_until_backfilled":
        status = "pass" if (not contract["model_input"] or not contract["production_ready"]) else "fail"
        return {
            "status": status,
            "check_name": "availability_contract_excluded_source_gap"
            if status == "pass"
            else "availability_contract_source_gap_blocking",
            "violation_count": 0 if status == "pass" else null_count,
            "reason": (
                f"{prefix}nulls are classified as source/backfill gaps by availability contract; "
                f"model_input={contract['model_input']} production_ready={contract['production_ready']}"
            ),
            "bucket": "classified" if status == "pass" else "train_blocking",
        }
    if null_policy in {"rolling_warmup_only", "asof_until_next_report", "base_dependency_only"}:
        scope = _null_temporal_scope(
            conn,
            table_name,
            column_name,
            columns,
            where_sql=where_sql,
            params=params,
            min_history_rows=_min_history_rows_for_feature(column_name),
        )
        if (
            scope.get("checked")
            and int(scope.get("post_first_valid_nulls") or 0) == 0
            and int(scope.get("never_valid_sufficient_history_nulls") or 0) == 0
        ):
            return {
                "status": "pass",
                "check_name": "availability_contract_classified_temporal_nulls",
                "violation_count": 0,
                "reason": (
                    f"{prefix}nulls are limited to {null_policy}; "
                    f"pre_first_valid_nulls={scope.get('pre_first_valid_nulls', 0)} "
                    f"never_valid_insufficient_history_nulls="
                    f"{scope.get('never_valid_insufficient_history_nulls', 0)}"
                ),
                "bucket": "classified",
            "scope": scope,
        }
        actionable_nulls = (
            int(scope.get("post_first_valid_nulls") or 0)
            + int(scope.get("never_valid_sufficient_history_nulls") or 0)
            if scope.get("checked")
            else null_count
        )
        return {
            "status": "fail",
            "check_name": "availability_contract_temporal_null_breach",
            "violation_count": actionable_nulls,
            "reason": (
                f"{prefix}{null_policy} permits only explainable pre-availability/warmup gaps; "
                f"scope={json.dumps(scope, ensure_ascii=False, sort_keys=True)}"
            ),
            "bucket": "train_blocking",
            "scope": scope,
        }
    return {
        "status": "fail",
        "check_name": "availability_contract_unknown_null_policy",
        "violation_count": null_count,
        "reason": f"{prefix}unknown null_policy={contract.get('null_policy')} in feature availability contract",
        "bucket": "train_blocking",
    }


def _append_outcome(
    detail: dict[str, Any],
    *,
    details: list[dict[str, Any]],
    blockers: list[str],
    warnings: list[str],
) -> None:
    details.append(detail)
    if detail["status"] != "pass":
        token = f"{detail['domain']}:{detail['check_name']}"
        if detail.get("table_name"):
            token += f":{detail['table_name']}"
        if detail.get("column_name"):
            token += f":{detail['column_name']}"
        if detail["severity"] == "warning":
            warnings.append(token)
        else:
            blockers.append(token)


def _check_calendar(conn: Any, details: list[dict[str, Any]], blockers: list[str], warnings: list[str]) -> dict[str, Any]:
    if not _table_exists(conn, "dim_trading_calendar"):
        item = _detail(
            domain="calendar",
            table_name="dim_trading_calendar",
            check_name="table_exists",
            status="fail",
            reason="dim_trading_calendar is required before every data fetch or feature build",
        )
        _append_outcome(item, details=details, blockers=blockers, warnings=warnings)
        return {"exists": False}

    row = conn.execute(
        """
        SELECT COUNT(*) AS rows,
               SUM(CASE WHEN trade_date IS NULL OR TRIM(CAST(trade_date AS VARCHAR)) = '' THEN 1 ELSE 0 END) AS null_dates,
               SUM(CASE WHEN is_trading = 1 THEN 1 ELSE 0 END) AS trading_days,
               MIN(trade_date) AS min_date,
               MAX(trade_date) AS max_date
          FROM dim_trading_calendar
        """
    ).fetchone()
    dup = conn.execute(
        """
        SELECT COUNT(*) AS n
          FROM (
            SELECT trade_date
              FROM dim_trading_calendar
             WHERE trade_date IS NOT NULL
             GROUP BY trade_date
            HAVING COUNT(*) > 1
          )
        """
    ).fetchone()
    latest = latest_completed_trade_date(conn)
    evidence = {
        "exists": True,
        "rows": int(_row_value(row, "rows", 0) or 0),
        "null_dates": int(_row_value(row, "null_dates", 1) or 0),
        "trading_days": int(_row_value(row, "trading_days", 2) or 0),
        "min_date": _row_value(row, "min_date", 3),
        "max_date": _row_value(row, "max_date", 4),
        "duplicate_dates": int(_row_value(dup, "n", 0) or 0),
        "latest_completed_trade_date": latest,
    }
    violations = evidence["null_dates"] + evidence["duplicate_dates"]
    if evidence["trading_days"] <= 0:
        violations += 1
    item = _detail(
        domain="calendar",
        table_name="dim_trading_calendar",
        check_name="preflight_integrity",
        status="pass" if violations == 0 else "fail",
        row_count=evidence["rows"],
        violation_count=violations,
        reason=None if violations == 0 else "calendar has null dates, duplicate dates, or no trading days",
    )
    _append_outcome(item, details=details, blockers=blockers, warnings=warnings)
    return evidence


def _check_duplicate_keys(
    conn: Any,
    table_name: str,
    keys: list[str],
    details: list[dict[str, Any]],
    blockers: list[str],
    warnings: list[str],
) -> dict[str, Any]:
    row_count = _count_rows(conn, table_name)
    if not keys:
        item = _detail(
            domain="business_key",
            table_name=table_name,
            check_name="duplicate_key",
            status="fail",
            row_count=row_count,
            violation_count=1,
            reason="no business key columns available for duplicate detection",
        )
        _append_outcome(item, details=details, blockers=blockers, warnings=warnings)
        return {"row_count": row_count, "duplicate_keys": None}

    key_sql = ", ".join(_quote_ident(key) for key in keys)
    row = conn.execute(
        f"""
        SELECT COUNT(*) AS n
          FROM (
            SELECT {key_sql}, COUNT(*) AS cnt
              FROM {_quote_table(table_name)}
             GROUP BY {key_sql}
            HAVING COUNT(*) > 1
          )
        """
    ).fetchone()
    duplicate_keys = int(_row_value(row, "n", 0) or 0)
    item = _detail(
        domain="business_key",
        table_name=table_name,
        check_name="duplicate_key",
        status="pass" if duplicate_keys == 0 else "fail",
        row_count=row_count,
        violation_count=duplicate_keys,
        reason=None if duplicate_keys == 0 else f"duplicate business keys: {','.join(keys)}",
    )
    _append_outcome(item, details=details, blockers=blockers, warnings=warnings)
    return {"row_count": row_count, "duplicate_keys": duplicate_keys, "keys": keys}


def _check_key_missing(
    conn: Any,
    table_name: str,
    keys: list[str],
    columns: dict[str, str],
    details: list[dict[str, Any]],
    blockers: list[str],
    warnings: list[str],
    *,
    example_limit: int,
) -> None:
    if not keys:
        return
    row_count = _count_rows(conn, table_name)
    for key in keys:
        if key not in columns:
            item = _detail(
                domain="required_column",
                table_name=table_name,
                column_name=key,
                check_name="column_exists",
                status="fail",
                row_count=row_count,
                violation_count=row_count,
                reason="required business key column is missing",
            )
            _append_outcome(item, details=details, blockers=blockers, warnings=warnings)
            continue
        where_sql = f"{_quote_ident(key)} IS NULL OR TRIM(CAST({_quote_ident(key)} AS VARCHAR)) = ''"
        missing = _count_rows(conn, table_name, where_sql=where_sql)
        examples = _sample_examples(conn, table_name, where_sql=where_sql, columns=keys, limit=example_limit)
        item = _detail(
            domain="required_column",
            table_name=table_name,
            column_name=key,
            check_name="missing_key_value",
            status="pass" if missing == 0 else "fail",
            row_count=row_count,
            violation_count=missing,
            reason=None if missing == 0 else "business key has null or blank values",
            examples=examples,
        )
        _append_outcome(item, details=details, blockers=blockers, warnings=warnings)


def _check_calendar_alignment(
    conn: Any,
    table_name: str,
    date_column: str,
    details: list[dict[str, Any]],
    blockers: list[str],
    warnings: list[str],
) -> dict[str, Any]:
    if not _table_exists(conn, "dim_trading_calendar"):
        return {"checked": False, "reason": "calendar_missing"}
    if date_column not in _table_columns(conn, table_name):
        return {"checked": False, "reason": "date_column_missing"}
    row_count = _count_rows(conn, table_name)
    row = conn.execute(
        f"""
        SELECT COUNT(*) AS n
          FROM {_quote_table(table_name)} t
          LEFT JOIN dim_trading_calendar cal
            ON cal.trade_date = t.{_quote_ident(date_column)}
           AND cal.is_trading = 1
         WHERE cal.trade_date IS NULL
        """
    ).fetchone()
    mismatches = int(_row_value(row, "n", 0) or 0)
    item = _detail(
        domain="calendar",
        table_name=table_name,
        column_name=date_column,
        check_name="trading_date_alignment",
        status="pass" if mismatches == 0 else "fail",
        row_count=row_count,
        violation_count=mismatches,
        reason=None if mismatches == 0 else "rows use dates absent from dim_trading_calendar trading days",
    )
    _append_outcome(item, details=details, blockers=blockers, warnings=warnings)
    return {"checked": True, "mismatches": mismatches}


def _null_counts(
    conn: Any,
    table_name: str,
    columns: list[str],
    *,
    chunk_size: int = 32,
) -> tuple[int, dict[str, int]]:
    if not columns:
        return _count_rows(conn, table_name), {}
    chunk_size = max(1, int(chunk_size))
    row_count = _count_rows(conn, table_name)
    counts: dict[str, int] = {}
    for start in range(0, len(columns), chunk_size):
        chunk = columns[start:start + chunk_size]
        chunk_started = time.perf_counter()
        _emit_progress(
            f"null_scan chunk table={table_name} columns={start + 1}-{start + len(chunk)}/{len(columns)}"
        )
        exprs = []
        for idx, column in enumerate(chunk):
            exprs.append(f"SUM(CASE WHEN {_quote_ident(column)} IS NULL THEN 1 ELSE 0 END) AS c{idx}")
        row = conn.execute(f"SELECT {', '.join(exprs)} FROM {_quote_table(table_name)}").fetchone()
        for idx, column in enumerate(chunk):
            counts[column] = int(_row_value(row, f"c{idx}", idx) or 0)
        _emit_progress(
            f"null_scan chunk_done table={table_name} "
            f"columns={start + 1}-{start + len(chunk)}/{len(columns)} "
            f"elapsed={time.perf_counter() - chunk_started:.3f}s"
        )
    return row_count, counts


def _check_feature_panel_nulls(
    conn: Any,
    table_name: str,
    columns: dict[str, str],
    details: list[dict[str, Any]],
    blockers: list[str],
    warnings: list[str],
) -> dict[str, Any]:
    scan_cols = [col for col in columns if col not in {"stock_code", "date", "feature_set_id"}]
    row_count, counts = _null_counts(conn, table_name, scan_cols)
    quality = _latest_follow_label_quality(conn, table_name)
    null_columns: dict[str, int] = {}
    allowed_immature_labels: dict[str, dict[str, Any]] = {}
    classified_nulls: dict[str, dict[str, Any]] = {}
    train_blocking_nulls: dict[str, dict[str, Any]] = {}
    for column, null_count in counts.items():
        if null_count <= 0:
            continue
        if column.startswith(FOLLOW_LABEL_PREFIX):
            label_quality = quality.get(column)
            if _clean_follow_label_quality(
                label_quality,
                row_count=row_count,
                null_count=null_count,
                exact_current_table=("feature_set_id" not in columns),
            ):
                allowed_immature_labels[column] = label_quality
                item = _detail(
                    domain="feature_panel_nulls",
                    table_name=table_name,
                    column_name=column,
                    check_name="classified_follow_label_nulls",
                    status="pass",
                    row_count=row_count,
                    violation_count=0,
                    reason="nulls are classified as future immature follow labels",
                )
                _append_outcome(item, details=details, blockers=blockers, warnings=warnings)
                continue
            item = _detail(
                domain="feature_panel_nulls",
                table_name=table_name,
                column_name=column,
                check_name="stale_or_inconsistent_follow_label_quality",
                status="fail",
                row_count=row_count,
                violation_count=null_count,
                reason=(
                    "follow label nulls require a current mart_follow_return_label_quality "
                    "record whose row_count/null_count exactly matches the scanned feature table "
                    "and whose nulls are fully classified as future immature"
                ),
            )
            _append_outcome(item, details=details, blockers=blockers, warnings=warnings)
            continue
        base_column = _industry_relative_base_column(column)
        if table_name == "fact_feature_panel" and base_column and base_column in columns:
            base_policy = _match_null_policy(conn, table_name, base_column)
            if base_policy and not base_policy["train_blocking"]:
                base_nonnull_nulls = int(
                    conn.execute(
                        f"""
                        SELECT SUM(
                            CASE
                              WHEN {_quote_ident(column)} IS NULL
                               AND {_quote_ident(base_column)} IS NOT NULL
                              THEN 1 ELSE 0
                            END
                        )
                          FROM {_quote_table(table_name)}
                        """
                    ).fetchone()[0]
                    or 0
                )
                if base_nonnull_nulls == 0:
                    classified_nulls[column] = {
                        "null_count": null_count,
                        "null_class": "derived_base_feature_null",
                        "policy_key": base_policy["policy_key"],
                        "source_family": "industry_relative_transform",
                        "production_allowed": base_policy["production_allowed"],
                        "base_column": base_column,
                    }
                    item = _detail(
                        domain="feature_panel_nulls",
                        table_name=table_name,
                        column_name=column,
                        check_name="classified_nulls",
                        status="pass",
                        row_count=row_count,
                        violation_count=0,
                        reason=(
                            "derived_base_feature_null: industry-relative value is null only "
                            f"where base feature {base_column} is null; base policy="
                            f"{base_policy['policy_key']}"
                        ),
                    )
                    _append_outcome(item, details=details, blockers=blockers, warnings=warnings)
                    continue
                train_blocking_nulls[column] = {
                    "null_count": null_count,
                    "null_class": "derived_feature_null_with_base_present",
                    "source_family": "industry_relative_transform",
                    "base_column": base_column,
                    "base_nonnull_nulls": base_nonnull_nulls,
                }
                item = _detail(
                    domain="feature_panel_nulls",
                    table_name=table_name,
                    column_name=column,
                    check_name="derived_feature_null_with_base_present",
                    status="fail",
                    row_count=row_count,
                    violation_count=base_nonnull_nulls,
                    reason=(
                        f"industry-relative value is null while base feature {base_column} "
                        "is present; investigate transform/grouping pipeline"
                    ),
                )
                _append_outcome(item, details=details, blockers=blockers, warnings=warnings)
                continue
        availability = _feature_availability_contract(conn, column)
        if availability and not column.startswith(LEGACY_LABEL_PREFIXES):
            outcome = _availability_null_outcome(
                conn,
                table_name,
                column,
                columns,
                availability,
                row_count=row_count,
                null_count=null_count,
            )
            if outcome:
                bucket = train_blocking_nulls if outcome["bucket"] == "train_blocking" else classified_nulls
                bucket[column] = {
                    "null_count": null_count,
                    "null_class": outcome["check_name"],
                    "feature_role": availability["feature_role"],
                    "availability_cadence": availability["availability_cadence"],
                    "panel_density": availability["panel_density"],
                    "null_policy": availability["null_policy"],
                    "scope": outcome.get("scope"),
                }
                item = _detail(
                    domain="feature_panel_nulls",
                    table_name=table_name,
                    column_name=column,
                    check_name=outcome["check_name"],
                    status=outcome["status"],
                    row_count=row_count,
                    violation_count=outcome["violation_count"],
                    reason=outcome["reason"],
                )
                _append_outcome(item, details=details, blockers=blockers, warnings=warnings)
                continue
        null_policy = _match_null_policy(conn, table_name, column)
        if null_policy:
            check_name = "classified_train_blocking_nulls" if null_policy["train_blocking"] else "classified_nulls"
            status = "fail" if null_policy["train_blocking"] else "pass"
            bucket = train_blocking_nulls if null_policy["train_blocking"] else classified_nulls
            bucket[column] = {
                "null_count": null_count,
                "null_class": null_policy["null_class"],
                "policy_key": null_policy["policy_key"],
                "source_family": null_policy["source_family"],
                "production_allowed": null_policy["production_allowed"],
            }
            item = _detail(
                domain="feature_panel_nulls",
                table_name=table_name,
                column_name=column,
                check_name=check_name,
                status=status,
                row_count=row_count,
                violation_count=0 if not null_policy["train_blocking"] else null_count,
                reason=(
                    f"{null_policy['null_class']}: {null_policy['null_reason']} "
                    f"(policy={null_policy['policy_key']})"
                ),
            )
            _append_outcome(item, details=details, blockers=blockers, warnings=warnings)
            continue
        null_columns[column] = null_count
        severity = "blocker" if not _is_label_column(column) else "blocker"
        item = _detail(
            domain="feature_panel_nulls",
            table_name=table_name,
            column_name=column,
            check_name="unclassified_nulls",
            status="fail",
            severity=severity,
            row_count=row_count,
            violation_count=null_count,
            reason="nulls are not classified by an explicit quality table or source registry reason",
        )
        _append_outcome(item, details=details, blockers=blockers, warnings=warnings)
    return {
        "row_count": row_count,
        "scanned_columns": len(scan_cols),
        "unclassified_null_columns": null_columns,
        "classified_null_columns": classified_nulls,
        "classified_train_blocking_null_columns": train_blocking_nulls,
        "allowed_immature_follow_labels": allowed_immature_labels,
    }


def _is_candidate_market_derived_feature(column: str) -> bool:
    base = column
    for suffix in ("_xs_bucket5", "_xs_rank", "_tdx_l1_rel"):
        if base.endswith(suffix):
            base = base[: -len(suffix)]
            break
    market_prefixes = (
        "ret_",
        "vol_",
        "ma_",
        "ma_ratio_",
        "range_pos_",
        "amount_",
        "turnover_",
        "beta_",
    )
    market_exact = {
        "klen",
        "vol_std_5d",
        "vol_std_20d",
        "vol_z20d",
        "vol_ratio_5_20",
        "momentum_diff",
    }
    return base in market_exact or any(base.startswith(prefix) for prefix in market_prefixes)


def _candidate_contracts_by_set(
    conn: Any,
    feature_set_ids: list[str],
) -> dict[str, dict[str, dict[str, Any]]]:
    if not feature_set_ids or not _table_exists(conn, "mart_candidate_feature_set_contract"):
        return {}
    placeholders = ", ".join("?" for _ in feature_set_ids)
    rows = conn.execute(
        f"""
        SELECT feature_set_id,
               feature_name,
               required,
               min_coverage_pct,
               null_policy,
               contract_source
          FROM mart_candidate_feature_set_contract
         WHERE required = TRUE
           AND feature_set_id IN ({placeholders})
        """,
        feature_set_ids,
    ).fetchall()
    contracts: dict[str, dict[str, dict[str, Any]]] = {}
    for row in rows:
        feature_set_id = _normal_feature_set_id(_row_value(row, "feature_set_id", 0))
        feature = str(_row_value(row, "feature_name", 1) or "").strip()
        if not feature_set_id or not feature:
            continue
        feature_contracts = contracts.setdefault(feature_set_id, {})
        contract = feature_contracts.setdefault(
            feature,
            {
                "feature_name": feature,
                "required": False,
                "min_coverage_pct": 0.0,
                "null_policies": set(),
                "contract_sources": set(),
            },
        )
        contract["required"] = contract["required"] or bool(_row_value(row, "required", 2))
        contract["min_coverage_pct"] = max(
            float(contract["min_coverage_pct"]),
            float(_row_value(row, "min_coverage_pct", 3) or 0.0),
        )
        null_policy = str(_row_value(row, "null_policy", 4) or "").strip()
        contract_source = str(_row_value(row, "contract_source", 5) or "").strip()
        if null_policy:
            contract["null_policies"].add(null_policy)
        if contract_source:
            contract["contract_sources"].add(contract_source)
    for feature_contracts in contracts.values():
        for contract in feature_contracts.values():
            contract["null_policies"] = sorted(contract["null_policies"])
            contract["contract_sources"] = sorted(contract["contract_sources"])
    return contracts


def _candidate_contract_null_counts(
    conn: Any,
    table_name: str,
    *,
    feature_set_ids: list[str],
    columns: list[str],
    chunk_size: int = 24,
) -> dict[str, dict[str, int]]:
    if not feature_set_ids or not columns:
        return {}
    placeholders = ", ".join("?" for _ in feature_set_ids)
    counts: dict[str, dict[str, int]] = {feature_set_id: {} for feature_set_id in feature_set_ids}
    for start in range(0, len(columns), max(1, int(chunk_size))):
        chunk = columns[start:start + chunk_size]
        chunk_started = time.perf_counter()
        _emit_progress(
            "candidate_contract_null_scan chunk "
            f"table={table_name} columns={start + 1}-{start + len(chunk)}/{len(columns)} "
            f"feature_sets={len(feature_set_ids)}"
        )
        exprs = [
            f"SUM(CASE WHEN {_quote_ident(column)} IS NULL THEN 1 ELSE 0 END) AS c{idx}"
            for idx, column in enumerate(chunk)
        ]
        rows = conn.execute(
            f"""
            SELECT feature_set_id, {", ".join(exprs)}
              FROM {_quote_table(table_name)}
             WHERE feature_set_id IN ({placeholders})
             GROUP BY feature_set_id
            """,
            feature_set_ids,
        ).fetchall()
        for row in rows:
            feature_set_id = _normal_feature_set_id(_row_value(row, "feature_set_id", 0))
            if not feature_set_id:
                continue
            target = counts.setdefault(feature_set_id, {})
            for idx, column in enumerate(chunk):
                target[column] = int(_row_value(row, f"c{idx}", idx + 1) or 0)
        _emit_progress(
            "candidate_contract_null_scan chunk_done "
            f"table={table_name} columns={start + 1}-{start + len(chunk)}/{len(columns)} "
            f"elapsed={time.perf_counter() - chunk_started:.3f}s"
        )
    return counts


def _check_candidate_feature_panel_nulls(
    conn: Any,
    table_name: str,
    columns: dict[str, str],
    details: list[dict[str, Any]],
    blockers: list[str],
    warnings: list[str],
) -> dict[str, Any]:
    feature_set_rows = conn.execute(
        f"""
        SELECT feature_set_id, COUNT(*) AS row_count
          FROM {_quote_table(table_name)}
         GROUP BY feature_set_id
         ORDER BY row_count DESC
        """
    ).fetchall()
    feature_sets: list[dict[str, Any]] = []
    row_count = 0
    for row in feature_set_rows:
        raw_feature_set_id = _row_value(row, "feature_set_id", 0)
        feature_set_id = _normal_feature_set_id(raw_feature_set_id)
        set_rows = int(_row_value(row, "row_count", 1) or 0)
        row_count += set_rows
        if not feature_set_id:
            item = _detail(
                domain="candidate_feature_contract",
                table_name=table_name,
                column_name="feature_set_id",
                check_name="invalid_feature_set_id",
                status="fail",
                row_count=set_rows,
                violation_count=set_rows,
                reason=f"candidate rows have invalid feature_set_id={raw_feature_set_id!r}",
            )
            _append_outcome(item, details=details, blockers=blockers, warnings=warnings)
            continue
        feature_sets.append({"feature_set_id": feature_set_id, "row_count": set_rows})

    feature_set_ids = [item["feature_set_id"] for item in feature_sets]
    contracts = _candidate_contracts_by_set(conn, feature_set_ids)
    contracted_feature_columns = sorted(
        {
            feature
            for feature_set_id in feature_set_ids
            for feature in contracts.get(feature_set_id, {})
            if feature in columns
        }
    )
    null_counts = _candidate_contract_null_counts(
        conn,
        table_name,
        feature_set_ids=[feature_set_id for feature_set_id in feature_set_ids if contracts.get(feature_set_id)],
        columns=contracted_feature_columns,
    )
    quality = _latest_follow_label_quality(conn, table_name)
    uncontracted_sets: dict[str, int] = {}
    missing_contract_features: dict[str, list[str]] = {}
    checked_features = 0
    contract_null_features: dict[str, dict[str, Any]] = {}
    train_blocking_null_features: dict[str, dict[str, Any]] = {}

    for feature_set in feature_sets:
        feature_set_id = feature_set["feature_set_id"]
        set_rows = int(feature_set["row_count"])
        feature_contracts = contracts.get(feature_set_id) or {}
        if not feature_contracts:
            uncontracted_sets[feature_set_id] = set_rows
            item = _detail(
                domain="candidate_feature_contract",
                table_name=table_name,
                column_name="feature_set_id",
                check_name="candidate_feature_set_contract_missing",
                status="fail",
                severity="warning",
                row_count=set_rows,
                violation_count=set_rows,
                reason=(
                    f"feature_set_id={feature_set_id} has no per-feature quality contract; "
                    "historical/uncontracted candidate rows are not trainable until selected "
                    "features, coverage thresholds, and null root-cause policies are recorded"
                ),
            )
            _append_outcome(item, details=details, blockers=blockers, warnings=warnings)
            continue

        for feature, contract in sorted(feature_contracts.items()):
            checked_features += 1
            if feature not in columns:
                missing_contract_features.setdefault(feature_set_id, []).append(feature)
                item = _detail(
                    domain="candidate_feature_contract",
                    table_name=table_name,
                    column_name=feature,
                    check_name="candidate_contract_feature_missing",
                    status="fail",
                    row_count=set_rows,
                    violation_count=set_rows,
                    reason=(
                        f"feature_set_id={feature_set_id} contract references missing column; "
                        f"sources={','.join(contract['contract_sources'])}"
                    ),
                )
                _append_outcome(item, details=details, blockers=blockers, warnings=warnings)
                continue

            null_count = int(null_counts.get(feature_set_id, {}).get(feature, 0))
            coverage_pct = 100.0 if set_rows <= 0 else ((set_rows - null_count) * 100.0 / set_rows)
            min_coverage = float(contract["min_coverage_pct"])
            availability = _feature_availability_contract(conn, feature)
            if null_count > 0 and availability and not feature.startswith(LEGACY_LABEL_PREFIXES):
                outcome = _availability_null_outcome(
                    conn,
                    table_name,
                    feature,
                    columns,
                    availability,
                    row_count=set_rows,
                    null_count=null_count,
                    where_sql="feature_set_id = ?",
                    params=[feature_set_id],
                    context=f"feature_set_id={feature_set_id}",
                )
                if outcome:
                    target = (
                        train_blocking_null_features
                        if outcome["bucket"] == "train_blocking"
                        else contract_null_features
                    )
                    target[f"{feature_set_id}:{feature}"] = {
                        "null_count": null_count,
                        "coverage_pct": coverage_pct,
                        "min_coverage_pct": min_coverage,
                        "root_cause": outcome["check_name"],
                        "contract_sources": contract["contract_sources"],
                        "availability_contract": {
                            "feature_role": availability["feature_role"],
                            "availability_cadence": availability["availability_cadence"],
                            "panel_density": availability["panel_density"],
                            "null_policy": availability["null_policy"],
                        },
                        "scope": outcome.get("scope"),
                    }
                    item = _detail(
                        domain="candidate_feature_contract",
                        table_name=table_name,
                        column_name=feature,
                        check_name=outcome["check_name"],
                        status=outcome["status"],
                        row_count=set_rows,
                        violation_count=outcome["violation_count"],
                        reason=outcome["reason"],
                    )
                    _append_outcome(item, details=details, blockers=blockers, warnings=warnings)
                    continue

            if null_count > 0 and coverage_pct + 1e-9 < min_coverage:
                item = _detail(
                    domain="candidate_feature_contract",
                    table_name=table_name,
                    column_name=feature,
                    check_name="candidate_contract_coverage_below_reference",
                    status="fail",
                    severity="warning",
                    row_count=set_rows,
                    violation_count=null_count,
                    reason=(
                        f"feature_set_id={feature_set_id} coverage={coverage_pct:.4f}% "
                        f"is below reference {min_coverage:.4f}%; availability/null policy "
                        "determines whether this is model-blocking"
                    ),
                )
                _append_outcome(item, details=details, blockers=blockers, warnings=warnings)

            if null_count <= 0:
                item = _detail(
                    domain="candidate_feature_contract",
                    table_name=table_name,
                    column_name=feature,
                    check_name="candidate_contract_no_nulls",
                    status="pass",
                    row_count=set_rows,
                    violation_count=0,
                    reason=(
                        f"feature_set_id={feature_set_id} contracted feature has full coverage; "
                        f"sources={','.join(contract['contract_sources'])}"
                    ),
                )
                _append_outcome(item, details=details, blockers=blockers, warnings=warnings)
                continue

            if feature.startswith(FOLLOW_LABEL_PREFIX):
                label_quality = quality.get(feature)
                if _clean_follow_label_quality(
                    label_quality,
                    row_count=set_rows,
                    null_count=null_count,
                    exact_current_table=False,
                ):
                    contract_null_features[f"{feature_set_id}:{feature}"] = {
                        "null_count": null_count,
                        "coverage_pct": coverage_pct,
                        "root_cause": "future_immature_follow_label",
                    }
                    item = _detail(
                        domain="candidate_feature_contract",
                        table_name=table_name,
                        column_name=feature,
                        check_name="classified_candidate_follow_label_nulls",
                        status="pass",
                        row_count=set_rows,
                        violation_count=0,
                        reason=(
                            f"feature_set_id={feature_set_id} nulls are classified as "
                            "future immature follow labels"
                        ),
                    )
                    _append_outcome(item, details=details, blockers=blockers, warnings=warnings)
                    continue

            if _is_candidate_market_derived_feature(feature):
                scope = _null_temporal_scope(
                    conn,
                    table_name,
                    feature,
                    columns,
                    where_sql="feature_set_id = ?",
                    params=[feature_set_id],
                    min_history_rows=_min_history_rows_for_feature(feature),
                )
                temporal_ok = (
                    scope.get("checked")
                    and int(scope.get("post_first_valid_nulls") or 0) == 0
                    and int(scope.get("never_valid_sufficient_history_nulls") or 0) == 0
                )
                actionable_nulls = (
                    int(scope.get("post_first_valid_nulls") or 0)
                    + int(scope.get("never_valid_sufficient_history_nulls") or 0)
                    if scope.get("checked")
                    else null_count
                )
                target = contract_null_features if temporal_ok else train_blocking_null_features
                target[f"{feature_set_id}:{feature}"] = {
                    "null_count": null_count,
                    "coverage_pct": coverage_pct,
                    "min_coverage_pct": min_coverage,
                    "root_cause": (
                        "rolling_history_warmup_or_kline_dependency_gap"
                        if temporal_ok
                        else "market_derived_post_warmup_gap_requires_investigation"
                    ),
                    "contract_sources": contract["contract_sources"],
                    "scope": scope,
                }
                item = _detail(
                    domain="candidate_feature_contract",
                    table_name=table_name,
                    column_name=feature,
                    check_name=(
                        "classified_candidate_derived_nulls"
                        if temporal_ok
                        else "candidate_contract_temporal_null_breach"
                    ),
                    status="pass" if temporal_ok else "fail",
                    row_count=set_rows,
                    violation_count=0 if temporal_ok else actionable_nulls,
                    reason=(
                        f"feature_set_id={feature_set_id} null_count={null_count} "
                        f"coverage={coverage_pct:.4f}% scope="
                        f"{json.dumps(scope, ensure_ascii=False, sort_keys=True)}"
                    ),
                )
                _append_outcome(item, details=details, blockers=blockers, warnings=warnings)
                continue

            train_blocking_null_features[f"{feature_set_id}:{feature}"] = {
                "null_count": null_count,
                "coverage_pct": coverage_pct,
                "min_coverage_pct": min_coverage,
                "root_cause": "unclassified_source_fetch_parse_or_storage_gap",
                "contract_sources": contract["contract_sources"],
            }
            item = _detail(
                domain="candidate_feature_contract",
                table_name=table_name,
                column_name=feature,
                check_name="candidate_contract_unclassified_nulls",
                status="fail",
                row_count=set_rows,
                violation_count=null_count,
                reason=(
                    f"feature_set_id={feature_set_id} contracted source feature has "
                    f"{null_count} null rows without a recorded root cause; investigate "
                    "data source coverage, network fetch, parser, and storage lineage "
                    "before this feature can train"
                ),
            )
            _append_outcome(item, details=details, blockers=blockers, warnings=warnings)

    return {
        "row_count": row_count,
        "feature_sets": feature_sets,
        "contracted_feature_sets": sorted(contracts),
        "uncontracted_feature_sets": uncontracted_sets,
        "contracted_features_checked": checked_features,
        "contracted_feature_columns_scanned": len(contracted_feature_columns),
        "missing_contract_features": missing_contract_features,
        "classified_contract_null_features": contract_null_features,
        "train_blocking_contract_null_features": train_blocking_null_features,
    }


def _check_feature_table(
    conn: Any,
    table_name: str,
    details: list[dict[str, Any]],
    blockers: list[str],
    warnings: list[str],
    *,
    example_limit: int,
    strict_nulls: bool,
) -> dict[str, Any]:
    if not _table_exists(conn, table_name):
        item = _detail(
            domain="table",
            table_name=table_name,
            check_name="table_exists",
            status="fail",
            reason="required feature table is missing",
        )
        _append_outcome(item, details=details, blockers=blockers, warnings=warnings)
        return {"exists": False}
    columns = _table_columns(conn, table_name)
    keys = _business_keys_for(set(columns))
    evidence: dict[str, Any] = {"exists": True, "columns": len(columns), "keys": keys}
    _check_key_missing(
        conn,
        table_name,
        keys,
        columns,
        details,
        blockers,
        warnings,
        example_limit=example_limit,
    )
    evidence["duplicates"] = _check_duplicate_keys(conn, table_name, keys, details, blockers, warnings)
    if "date" in columns:
        evidence["calendar_alignment"] = _check_calendar_alignment(
            conn,
            table_name,
            "date",
            details,
            blockers,
            warnings,
        )
    if strict_nulls:
        if table_name == "fact_feature_panel_candidate" and "feature_set_id" in columns:
            evidence["null_scan"] = _check_candidate_feature_panel_nulls(
                conn,
                table_name,
                columns,
                details,
                blockers,
                warnings,
            )
        else:
            evidence["null_scan"] = _check_feature_panel_nulls(conn, table_name, columns, details, blockers, warnings)
    return evidence


def _check_feature_table_kline_alignment(
    conn: Any,
    table_name: str,
    details: list[dict[str, Any]],
    blockers: list[str],
    warnings: list[str],
    *,
    example_limit: int,
) -> dict[str, Any]:
    columns = _table_columns(conn, table_name)
    required = {"stock_code", "date", "close"}
    if not required <= set(columns):
        return {"checked": False, "reason": "feature table does not expose stock_code/date/close"}
    attached = attach_market_if_available(conn)
    kline_table = "market.v_price_kline_qfq"
    if not attached or not _table_exists(conn, kline_table):
        item = _detail(
            domain="feature_panel_kline",
            table_name=table_name,
            check_name="canonical_kline_available",
            status="fail",
            reason="feature panel K-line alignment requires canonical TDXHub K-line view",
        )
        _append_outcome(item, details=details, blockers=blockers, warnings=warnings)
        return {"checked": False, "market_attached": attached}

    relation = _quote_table(table_name)
    row_count = _count_rows(conn, table_name)
    panel_source_name_expr = "p.kline_source_name" if "kline_source_name" in columns else "NULL"
    panel_source_tier_expr = "p.kline_source_tier" if "kline_source_tier" in columns else "NULL"
    panel_is_fallback_expr = "p.kline_is_fallback" if "kline_is_fallback" in columns else "NULL"
    join_sql = f"""
        FROM {relation} p
        LEFT JOIN {_quote_table(kline_table)} k
          ON k.code = p.stock_code
         AND k.date = p.date
         AND k.freq = 'daily'
         AND k.adjust = 'qfq'
    """
    checks = {
        "missing_canonical_kline": "k.code IS NULL",
        "close_mismatch_with_canonical_kline": (
            "k.code IS NOT NULL AND (p.close IS NULL OR ABS(CAST(p.close AS DOUBLE) - CAST(k.close AS DOUBLE)) > 1e-4)"
        ),
        "canonical_kline_fallback_used": (
            "k.code IS NOT NULL AND (COALESCE(k.is_fallback, FALSE) OR COALESCE(k.source_tier, 99) <> 1)"
        ),
    }
    if {"kline_source_tier", "kline_is_fallback"} <= set(columns):
        checks["panel_kline_lineage_stale"] = (
            "k.code IS NOT NULL AND ("
            "COALESCE(p.kline_is_fallback, FALSE) <> COALESCE(k.is_fallback, FALSE) "
            "OR COALESCE(p.kline_source_tier, 99) <> COALESCE(k.source_tier, 99)"
            ")"
        )

    evidence: dict[str, Any] = {"checked": True, "row_count": row_count}
    for check_name, predicate in checks.items():
        violation_count = int(
            _row_value(
                conn.execute(f"SELECT COUNT(*) AS n {join_sql} WHERE {predicate}").fetchone(),
                "n",
                0,
            )
            or 0
        )
        examples: list[dict[str, Any]] = []
        if violation_count:
            rows = conn.execute(
                f"""
                SELECT p.stock_code,
                       p.date,
                       p.close AS panel_close,
                       {panel_source_name_expr} AS panel_source_name,
                       {panel_source_tier_expr} AS panel_source_tier,
                       {panel_is_fallback_expr} AS panel_is_fallback,
                       k.close AS canonical_close,
                       k.source_name AS canonical_source_name,
                       k.source_tier AS canonical_source_tier,
                       k.is_fallback AS canonical_is_fallback
                  {join_sql}
                 WHERE {predicate}
                 ORDER BY p.date DESC, p.stock_code
                 LIMIT ?
                """,
                (example_limit,),
            ).fetchall()
            examples = [
                {
                    "stock_code": _row_value(row, "stock_code", 0),
                    "date": _row_value(row, "date", 1),
                    "panel_close": _row_value(row, "panel_close", 2),
                    "panel_source_name": _row_value(row, "panel_source_name", 3),
                    "panel_source_tier": _row_value(row, "panel_source_tier", 4),
                    "panel_is_fallback": _row_value(row, "panel_is_fallback", 5),
                    "canonical_close": _row_value(row, "canonical_close", 6),
                    "canonical_source_name": _row_value(row, "canonical_source_name", 7),
                    "canonical_source_tier": _row_value(row, "canonical_source_tier", 8),
                    "canonical_is_fallback": _row_value(row, "canonical_is_fallback", 9),
                }
                for row in rows
            ]
        item = _detail(
            domain="feature_panel_kline",
            table_name=table_name,
            column_name="close",
            check_name=check_name,
            status="pass" if violation_count == 0 else "fail",
            row_count=row_count,
            violation_count=violation_count,
            reason=(
                None
                if violation_count == 0
                else "feature panel price and K-line lineage must match canonical primary TDXHub K-line"
            ),
            examples=examples,
        )
        _append_outcome(item, details=details, blockers=blockers, warnings=warnings)
        evidence[check_name] = violation_count
    return evidence


def _check_market_kline(
    conn: Any,
    details: list[dict[str, Any]],
    blockers: list[str],
    warnings: list[str],
) -> dict[str, Any]:
    attached = attach_market_if_available(conn)
    table_name = "market.v_price_kline_qfq"
    if not attached or not _table_exists(conn, table_name):
        item = _detail(
            domain="kline",
            table_name=table_name,
            check_name="canonical_kline_exists",
            status="fail",
            reason="tdxhub primary canonical K-line view must be available",
        )
        _append_outcome(item, details=details, blockers=blockers, warnings=warnings)
        return {"exists": False, "market_attached": attached}
    where = "freq = 'daily' AND adjust = 'qfq'"
    row_count = _count_rows(conn, table_name, where_sql=where)
    row = conn.execute(
        f"""
        SELECT
            SUM(CASE WHEN code IS NULL OR TRIM(CAST(code AS VARCHAR)) = '' THEN 1 ELSE 0 END) AS missing_code,
            SUM(CASE WHEN date IS NULL OR TRIM(CAST(date AS VARCHAR)) = '' THEN 1 ELSE 0 END) AS missing_date,
            SUM(CASE WHEN open IS NULL OR open <= 0 THEN 1 ELSE 0 END) AS invalid_open,
            SUM(CASE WHEN close IS NULL OR close <= 0 THEN 1 ELSE 0 END) AS invalid_close,
            SUM(CASE WHEN volume IS NULL OR volume < 1e-6 THEN 1 ELSE 0 END) AS invalid_volume,
            SUM(CASE WHEN amount IS NULL OR amount < 1e-6 THEN 1 ELSE 0 END) AS invalid_amount
          FROM {_quote_table(table_name)}
         WHERE {where}
        """
    ).fetchone()
    checks = {
        "missing_code": int(_row_value(row, "missing_code", 0) or 0),
        "missing_date": int(_row_value(row, "missing_date", 1) or 0),
        "invalid_open": int(_row_value(row, "invalid_open", 2) or 0),
        "invalid_close": int(_row_value(row, "invalid_close", 3) or 0),
        "invalid_volume": int(_row_value(row, "invalid_volume", 4) or 0),
        "invalid_amount": int(_row_value(row, "invalid_amount", 5) or 0),
    }
    for check_name, violations in checks.items():
        item = _detail(
            domain="kline",
            table_name=table_name,
            check_name=check_name,
            status="pass" if violations == 0 else "fail",
            row_count=row_count,
            violation_count=violations,
            reason=None if violations == 0 else "canonical K-line has invalid or missing core values",
        )
        _append_outcome(item, details=details, blockers=blockers, warnings=warnings)
    _check_duplicate_keys(conn, table_name, ["code", "date", "freq", "adjust"], details, blockers, warnings)
    return {"exists": True, "rows": row_count, **checks}


def _check_institution_events(
    conn: Any,
    details: list[dict[str, Any]],
    blockers: list[str],
    warnings: list[str],
) -> dict[str, Any]:
    table_name = "fact_institution_event"
    if not _table_exists(conn, table_name):
        item = _detail(
            domain="institution_event",
            table_name=table_name,
            check_name="table_exists",
            status="fail",
            reason="institution events are required for stock selection",
        )
        _append_outcome(item, details=details, blockers=blockers, warnings=warnings)
        return {"exists": False}
    columns = _table_columns(conn, table_name)
    row_count = _count_rows(conn, table_name)
    evidence: dict[str, Any] = {"exists": True, "rows": row_count}
    for column in ("stock_code", "notice_date"):
        if column not in columns:
            item = _detail(
                domain="institution_event",
                table_name=table_name,
                column_name=column,
                check_name="required_column_exists",
                status="fail",
                row_count=row_count,
                violation_count=row_count,
                reason="required event column is missing",
            )
            _append_outcome(item, details=details, blockers=blockers, warnings=warnings)
            continue
        where = f"{_quote_ident(column)} IS NULL OR TRIM(CAST({_quote_ident(column)} AS VARCHAR)) = ''"
        missing = _count_rows(conn, table_name, where_sql=where)
        item = _detail(
            domain="institution_event",
            table_name=table_name,
            column_name=column,
            check_name="required_value_missing",
            status="pass" if missing == 0 else "fail",
            row_count=row_count,
            violation_count=missing,
            reason=None if missing == 0 else "institution event required value is missing",
        )
        _append_outcome(item, details=details, blockers=blockers, warnings=warnings)
        evidence[f"{column}_missing"] = missing
    if "notice_date" in columns:
        notice_iso = (
            "CASE "
            "WHEN length(CAST(notice_date AS VARCHAR)) = 8 AND instr(CAST(notice_date AS VARCHAR), '-') = 0 "
            "THEN substr(CAST(notice_date AS VARCHAR),1,4) || '-' || "
            "substr(CAST(notice_date AS VARCHAR),5,2) || '-' || "
            "substr(CAST(notice_date AS VARCHAR),7,2) "
            "ELSE CAST(notice_date AS VARCHAR) END"
        )
        future_where = f"TRY_CAST(({notice_iso}) AS DATE) > CURRENT_DATE"
        future_count = _count_rows(conn, table_name, where_sql=future_where)
        future_bounds = conn.execute(
            f"""
            SELECT MIN({notice_iso}) AS min_future_notice_date,
                   MAX({notice_iso}) AS max_future_notice_date
              FROM {_quote_table(table_name)}
             WHERE {future_where}
            """
        ).fetchone()
        future_by_source: list[dict[str, Any]] = []
        source_notice_future_count = 0
        observed_source_future_count = 0
        if "notice_date_source" in columns:
            future_by_source = [
                dict(row)
                for row in conn.execute(
                    f"""
                    SELECT COALESCE(NULLIF(notice_date_source, ''), 'unknown') AS notice_date_source,
                           COUNT(*) AS rows
                      FROM {_quote_table(table_name)}
                     WHERE {future_where}
                     GROUP BY COALESCE(NULLIF(notice_date_source, ''), 'unknown')
                     ORDER BY rows DESC
                    """
                ).fetchall()
            ]
            source_notice_future_count = int(
                next(
                    (
                        row["rows"]
                        for row in future_by_source
                        if row.get("notice_date_source") == "source_notice"
                    ),
                    0,
                )
                or 0
            )
            observed_source_future_count = sum(
                int(row["rows"] or 0)
                for row in future_by_source
                if row.get("notice_date_source") in {"source_notice", "page_update_date"}
            )
        example_columns = [
            column
            for column in (
                "institution_id",
                "stock_code",
                "stock_name",
                "report_date",
                "notice_date",
                "notice_date_source",
                "source_notice_date",
                "availability_deadline",
                "event_type",
            )
            if column in columns
        ]
        severity = "blocker" if observed_source_future_count else "warning"
        item = _detail(
            domain="institution_event",
            table_name=table_name,
            column_name="notice_date",
            check_name="future_notice_date",
            status="pass" if future_count == 0 else "fail",
            severity=severity,
            row_count=row_count,
            violation_count=future_count,
            reason=None
            if future_count == 0
            else (
                "future observed-source notice rows indicate upstream date corruption"
                if observed_source_future_count
                else "future notice_date rows are excluded from live signals; regulatory_deadline rows are plannable fallback dates, not true source disclosure dates"
            ),
            examples=_sample_examples(
                conn,
                table_name,
                where_sql=future_where,
                columns=example_columns,
                limit=5,
            )
            if future_count
            else [],
        )
        _append_outcome(item, details=details, blockers=blockers, warnings=warnings)
        evidence["future_notice_date_rows"] = future_count
        if future_by_source:
            evidence["future_notice_by_source"] = future_by_source
            evidence["future_source_notice_rows"] = source_notice_future_count
            evidence["future_observed_source_rows"] = observed_source_future_count
        evidence["min_future_notice_date"] = (
            str(future_bounds["min_future_notice_date"])
            if future_bounds and future_bounds["min_future_notice_date"] is not None
            else None
        )
        evidence["max_future_notice_date"] = (
            str(future_bounds["max_future_notice_date"])
            if future_bounds and future_bounds["max_future_notice_date"] is not None
            else None
        )
    if {"price_entry", "price_entry_status"} <= set(columns):
        where = """
            (price_entry IS NULL OR price_entry <= 0)
            AND COALESCE(price_entry_status, '') NOT IN ('future_signal_waiting')
        """
        missing = _count_rows(conn, table_name, where_sql=where)
        item = _detail(
            domain="institution_event",
            table_name=table_name,
            column_name="price_entry",
            check_name="unclassified_missing_entry_price",
            status="pass" if missing == 0 else "fail",
            row_count=row_count,
            violation_count=missing,
            reason=None if missing == 0 else "missing entry prices must be future_signal_waiting or fixed",
        )
        _append_outcome(item, details=details, blockers=blockers, warnings=warnings)
        evidence["unclassified_missing_entry_price"] = missing
    return evidence


def _compact_date_expr(column_name: str) -> str:
    quoted = _quote_ident(column_name)
    return (
        "substr("
        f"regexp_replace(COALESCE(CAST({quoted} AS VARCHAR), ''), '[^0-9]', '', 'g'), "
        "1, 8)"
    )


def _iso_date_expr(compact_expr: str) -> str:
    return (
        f"substr({compact_expr},1,4) || '-' || "
        f"substr({compact_expr},5,2) || '-' || "
        f"substr({compact_expr},7,2)"
    )


def _check_holder_availability(
    conn: Any,
    details: list[dict[str, Any]],
    blockers: list[str],
    warnings: list[str],
    *,
    example_limit: int = 5,
) -> dict[str, Any]:
    table_name = "fact_top10_holder_period"
    if not _table_exists(conn, table_name):
        return {"exists": False}

    columns = set(_table_columns(conn, table_name))
    row_count = _count_rows(conn, table_name)
    evidence: dict[str, Any] = {"exists": True, "rows": row_count}

    required = {"report_date", "page_update_date", "availability_source"}
    if required <= columns:
        page_norm = _compact_date_expr("page_update_date")
        report_norm = _compact_date_expr("report_date")
        page_before_report_where = f"""
            length({page_norm}) = 8
            AND length({report_norm}) = 8
            AND {page_norm} < {report_norm}
        """
        unsafe_page_update_where = f"""
            {page_before_report_where}
            AND COALESCE(NULLIF(availability_source, ''), 'unknown') = 'page_update_date'
        """
        page_before_report_rows = _count_rows(
            conn,
            table_name,
            where_sql=page_before_report_where,
        )
        unsafe_page_update_rows = _count_rows(
            conn,
            table_name,
            where_sql=unsafe_page_update_where,
        )
        example_columns = [
            column
            for column in (
                "stock_code",
                "stock_name",
                "report_date",
                "page_update_date",
                "notice_date",
                "availability_source",
                "raw_hash",
                "fetched_at",
            )
            if column in columns
        ]
        item = _detail(
            domain="holder_availability",
            table_name=table_name,
            column_name="page_update_date",
            check_name="page_update_before_report_used_as_availability",
            status="pass" if unsafe_page_update_rows == 0 else "fail",
            row_count=row_count,
            violation_count=unsafe_page_update_rows,
            reason=(
                None
                if unsafe_page_update_rows == 0 and page_before_report_rows == 0
                else (
                    "F10 page_update_date is before report_date and must not be used as PIT availability"
                    if unsafe_page_update_rows
                    else "F10 page_update_date/report_date conflicts exist but are not used as page_update_date availability"
                )
            ),
            examples=_sample_examples(
                conn,
                table_name,
                where_sql=unsafe_page_update_where if unsafe_page_update_rows else page_before_report_where,
                columns=example_columns,
                limit=example_limit,
            )
            if page_before_report_rows
            else [],
        )
        _append_outcome(item, details=details, blockers=blockers, warnings=warnings)
        evidence["page_update_before_report_rows"] = page_before_report_rows
        evidence["unsafe_page_update_availability_rows"] = unsafe_page_update_rows

    fetched_required = {"report_date", "notice_date", "fetched_at", "availability_source"}
    if fetched_required <= columns:
        report_norm = _compact_date_expr("report_date")
        fetched_norm = _compact_date_expr("fetched_at")
        notice_norm = _compact_date_expr("notice_date")
        fetched_iso = _iso_date_expr(fetched_norm)
        notice_iso = _iso_date_expr(notice_norm)
        invalid_fetched_where = f"""
            COALESCE(NULLIF(availability_source, ''), 'unknown') = 'fetched_at_observed'
            AND (
                length({report_norm}) != 8
                OR length({fetched_norm}) != 8
                OR length({notice_norm}) != 8
                OR {fetched_norm} < {report_norm}
                OR TRY_CAST({fetched_iso} AS DATE) > CURRENT_DATE
                OR TRY_CAST({notice_iso} AS DATE) > CURRENT_DATE
            )
        """
        invalid_fetched_rows = _count_rows(
            conn,
            table_name,
            where_sql=invalid_fetched_where,
        )
        example_columns = [
            column
            for column in (
                "stock_code",
                "stock_name",
                "report_date",
                "notice_date",
                "page_update_date",
                "availability_source",
                "raw_hash",
                "fetched_at",
            )
            if column in columns
        ]
        item = _detail(
            domain="holder_availability",
            table_name=table_name,
            column_name="fetched_at",
            check_name="invalid_fetched_at_observed_availability",
            status="pass" if invalid_fetched_rows == 0 else "fail",
            row_count=row_count,
            violation_count=invalid_fetched_rows,
            reason=None
            if invalid_fetched_rows == 0
            else "fetched_at_observed must be observed on/after report_date and not in the future",
            examples=_sample_examples(
                conn,
                table_name,
                where_sql=invalid_fetched_where,
                columns=example_columns,
                limit=example_limit,
            )
            if invalid_fetched_rows
            else [],
        )
        _append_outcome(item, details=details, blockers=blockers, warnings=warnings)
        evidence["invalid_fetched_at_observed_rows"] = invalid_fetched_rows

    return evidence


F10_SOURCE_AVAILABILITY_TABLES = (
    ("fact_holder_count_period", "report_date"),
    ("fact_common_major_holder_stock", "report_date"),
    ("fact_fund_holding_tdx_f10", "report_date"),
    ("fact_shareholder_trade_tdx_b", "change_date"),
)


def _check_tdx_f10_source_availability(
    conn: Any,
    details: list[dict[str, Any]],
    blockers: list[str],
    warnings: list[str],
    *,
    example_limit: int = 5,
) -> dict[str, Any]:
    evidence: dict[str, Any] = {"tables": {}}
    for table_name, fact_date_column in F10_SOURCE_AVAILABILITY_TABLES:
        if not _table_exists(conn, table_name):
            evidence["tables"][table_name] = {"exists": False}
            continue
        columns = set(_table_columns(conn, table_name))
        row_count = _count_rows(conn, table_name)
        table_evidence: dict[str, Any] = {"exists": True, "rows": row_count}
        evidence["tables"][table_name] = table_evidence
        required = {fact_date_column, "source_available_date", "source_date_quality"}
        if not required <= columns:
            table_evidence["missing_columns"] = sorted(required - columns)
            continue

        fact_norm = _compact_date_expr(fact_date_column)
        available_norm = _compact_date_expr("source_available_date")
        available_iso = _iso_date_expr(available_norm)
        missing_available_where = """
            source_available_date IS NULL
            OR CAST(source_available_date AS VARCHAR) = ''
        """
        available_before_fact_where = f"""
            length({fact_norm}) = 8
            AND length({available_norm}) = 8
            AND {available_norm} < {fact_norm}
        """
        future_available_where = f"""
            length({available_norm}) = 8
            AND TRY_CAST({available_iso} AS DATE) > CURRENT_DATE
        """
        example_columns = [
            column
            for column in (
                "stock_code",
                "stock_name",
                fact_date_column,
                "source_available_date",
                "source_notice_date",
                "source_date_quality",
                "page_update_date",
                "raw_hash",
                "fetched_at",
            )
            if column in columns
        ]
        for check_name, column_name, where_sql, reason in (
            (
                "missing_source_available_date",
                "source_available_date",
                missing_available_where,
                "TDX/F10 parsed rows must carry a source availability date before model eligibility",
            ),
            (
                "source_available_before_fact_date",
                "source_available_date",
                available_before_fact_where,
                "TDX/F10 source availability cannot be earlier than the fact period/event date",
            ),
            (
                "future_source_available_date",
                "source_available_date",
                future_available_where,
                "TDX/F10 source availability cannot be in the future",
            ),
        ):
            violation_count = _count_rows(conn, table_name, where_sql=where_sql)
            item = _detail(
                domain="tdx_f10_source_availability",
                table_name=table_name,
                column_name=column_name,
                check_name=check_name,
                status="pass" if violation_count == 0 else "fail",
                row_count=row_count,
                violation_count=violation_count,
                reason=None if violation_count == 0 else reason,
                examples=_sample_examples(
                    conn,
                    table_name,
                    where_sql=where_sql,
                    columns=example_columns,
                    limit=example_limit,
                )
                if violation_count
                else [],
            )
            _append_outcome(item, details=details, blockers=blockers, warnings=warnings)
            table_evidence[check_name] = violation_count

    plan_table = "fact_shareholder_plan_tdx_f10"
    if _table_exists(conn, plan_table):
        columns = set(_table_columns(conn, plan_table))
        row_count = _count_rows(conn, plan_table)
        plan_evidence: dict[str, Any] = {"exists": True, "rows": row_count}
        evidence["tables"][plan_table] = plan_evidence
        required = {"source_notice_date", "source_available_date", "source_date_quality"}
        if required <= columns:
            notice_norm = _compact_date_expr("source_notice_date")
            available_norm = _compact_date_expr("source_available_date")
            notice_iso = _iso_date_expr(notice_norm)
            available_iso = _iso_date_expr(available_norm)
            allowed_quality = (
                "parsed_latest_announce_date",
                "parsed_first_announce_date",
                "page_update_date_fallback",
                "missing_source_date",
            )
            invalid_quality_values = ", ".join(f"'{value}'" for value in allowed_quality)
            checks = [
                (
                    "invalid_source_date_quality",
                    "source_date_quality",
                    f"""
                    source_date_quality IS NULL
                    OR source_date_quality NOT IN ({invalid_quality_values})
                    """,
                    "shareholder-plan source date quality must identify announcement/page-update provenance",
                ),
                (
                    "missing_parsed_source_notice_date",
                    "source_notice_date",
                    """
                    source_date_quality LIKE 'parsed_%'
                    AND (
                        source_notice_date IS NULL
                        OR CAST(source_notice_date AS VARCHAR) = ''
                    )
                    """,
                    "parsed shareholder-plan rows must retain the source notice date",
                ),
                (
                    "future_source_notice_date",
                    "source_notice_date",
                    f"""
                    length({notice_norm}) = 8
                    AND TRY_CAST({notice_iso} AS DATE) > CURRENT_DATE
                    """,
                    "TDX/F10 parsed source notice date cannot be in the future",
                ),
                (
                    "future_source_available_date",
                    "source_available_date",
                    f"""
                    length({available_norm}) = 8
                    AND TRY_CAST({available_iso} AS DATE) > CURRENT_DATE
                    """,
                    "TDX/F10 source availability cannot be in the future",
                ),
            ]
            plan_window_columns = [column for column in ("start_date", "end_date") if column in columns]
            announcement_columns = [
                column
                for column in (
                    "announce_date",
                    "latest_announce_date",
                    "first_announce_date",
                    "page_update_date",
                )
                if column in columns
            ]
            if plan_window_columns:
                window_notice_terms = [
                    f"length({_compact_date_expr(column)}) = 8 AND {notice_norm} = {_compact_date_expr(column)}"
                    for column in plan_window_columns
                ]
                window_available_terms = [
                    f"length({_compact_date_expr(column)}) = 8 AND {available_norm} = {_compact_date_expr(column)}"
                    for column in plan_window_columns
                ]
                notice_announcement_terms = [
                    f"length({_compact_date_expr(column)}) = 8 AND {notice_norm} = {_compact_date_expr(column)}"
                    for column in announcement_columns
                ]
                available_announcement_terms = [
                    f"length({_compact_date_expr(column)}) = 8 AND {available_norm} = {_compact_date_expr(column)}"
                    for column in announcement_columns
                ]
                notice_announcement_match = (
                    " OR ".join(notice_announcement_terms)
                    if notice_announcement_terms
                    else "FALSE"
                )
                available_announcement_match = (
                    " OR ".join(available_announcement_terms)
                    if available_announcement_terms
                    else "FALSE"
                )
                checks.append(
                    (
                        "plan_window_used_as_source_date",
                        "source_available_date",
                        f"""
                        (
                            length({notice_norm}) = 8
                            AND ({' OR '.join(window_notice_terms)})
                            AND NOT ({notice_announcement_match})
                        )
                        OR (
                            length({available_norm}) = 8
                            AND ({' OR '.join(window_available_terms)})
                            AND NOT ({available_announcement_match})
                        )
                        """,
                        "shareholder-plan start/end dates are plan windows, not source availability",
                    )
                )
            example_columns = [
                column
                for column in (
                    "stock_code",
                    "stock_name",
                    "source_notice_date",
                    "source_available_date",
                    "source_date_quality",
                    "announce_date",
                    "latest_announce_date",
                    "first_announce_date",
                    "start_date",
                    "end_date",
                    "page_update_date",
                    "raw_hash",
                    "fetched_at",
                )
                if column in columns
            ]
            for check_name, column_name, where_sql, reason in checks:
                violation_count = _count_rows(conn, plan_table, where_sql=where_sql)
                item = _detail(
                    domain="tdx_f10_source_availability",
                    table_name=plan_table,
                    column_name=column_name,
                    check_name=check_name,
                    status="pass" if violation_count == 0 else "fail",
                    row_count=row_count,
                    violation_count=violation_count,
                    reason=None if violation_count == 0 else reason,
                    examples=_sample_examples(
                        conn,
                        plan_table,
                        where_sql=where_sql,
                        columns=example_columns,
                        limit=example_limit,
                    )
                    if violation_count
                    else [],
                )
                _append_outcome(item, details=details, blockers=blockers, warnings=warnings)
                plan_evidence[check_name] = violation_count
        else:
            plan_evidence["missing_columns"] = sorted(required - columns)
    return evidence


def _has_stage_timing(perf: Any) -> bool:
    if not isinstance(perf, dict):
        return False
    keys = {"steps", "stage_timings", "stage_timing", "timing", "timings", "profile", "step_durations"}
    return any(key in perf for key in keys)


def _check_pipeline_performance(
    conn: Any,
    details: list[dict[str, Any]],
    blockers: list[str],
    warnings: list[str],
    *,
    policy_sections: dict[str, Any],
    recent_limit: int,
) -> dict[str, Any]:
    if not _table_exists(conn, "mart_pipeline_run_manifest"):
        item = _detail(
            domain="pipeline_performance",
            table_name="mart_pipeline_run_manifest",
            check_name="manifest_exists",
            status="fail",
            reason="pipeline manifest is required to investigate slow fetch/build jobs",
        )
        _append_outcome(item, details=details, blockers=blockers, warnings=warnings)
        return {"exists": False}
    perf_policy = load_pipeline_performance_policy().to_dict()
    if not perf_policy.get("pipeline_duration_budgets_s"):
        perf_policy = policy_sections.get("performance_policy") or {}
    progress_s = float(perf_policy.get("progress_heartbeat_required_after_s") or 30)
    default_budget = float(perf_policy.get("default_pipeline_duration_budget_s") or 600)
    budgets = perf_policy.get("pipeline_duration_budgets_s") or {}
    tracked = sorted(str(name) for name in budgets)
    if not tracked:
        return {"exists": True, "tracked_pipelines": []}
    placeholders = ", ".join("?" for _ in tracked)
    rows = conn.execute(
        f"""
        SELECT run_id, pipeline_name, status, duration_s, perf_summary_json
          FROM mart_pipeline_run_manifest
         WHERE duration_s IS NOT NULL
           AND pipeline_name IN ({placeholders})
         ORDER BY COALESCE(started_at, created_at) DESC
         LIMIT {int(recent_limit)}
        """,
        tracked,
    ).fetchall()
    slow_without_timing = 0
    over_budget = 0
    for row in rows:
        run_id = str(_row_value(row, "run_id", 0))
        pipeline_name = str(_row_value(row, "pipeline_name", 1))
        duration = float(_row_value(row, "duration_s", 3) or 0.0)
        perf = _safe_json_load(_row_value(row, "perf_summary_json", 4))
        if duration >= progress_s and not _has_stage_timing(perf):
            slow_without_timing += 1
            item = _detail(
                domain="pipeline_performance",
                table_name="mart_pipeline_run_manifest",
                column_name=pipeline_name,
                check_name="slow_run_has_stage_timing",
                status="fail",
                row_count=len(rows),
                violation_count=1,
                reason=f"run {run_id} took {duration:.1f}s without stage timing/progress evidence",
                examples=[{"run_id": run_id, "pipeline_name": pipeline_name, "duration_s": duration}],
            )
            _append_outcome(item, details=details, blockers=blockers, warnings=warnings)
        budget = float(budgets.get(pipeline_name) or default_budget)
        if duration > budget:
            over_budget += 1
            item = _detail(
                domain="pipeline_performance",
                table_name="mart_pipeline_run_manifest",
                column_name=pipeline_name,
                check_name="duration_budget",
                status="fail",
                row_count=len(rows),
                violation_count=1,
                reason=f"run {run_id} took {duration:.1f}s over budget {budget:.1f}s",
                examples=[{"run_id": run_id, "pipeline_name": pipeline_name, "duration_s": duration, "budget_s": budget}],
            )
            _append_outcome(item, details=details, blockers=blockers, warnings=warnings)
    if not rows:
        item = _detail(
            domain="pipeline_performance",
            table_name="mart_pipeline_run_manifest",
            check_name="tracked_run_exists",
            status="pass",
            severity="warning",
            reason="no tracked slow-pipeline rows found",
        )
        details.append(item)
    return {
        "exists": True,
        "tracked_pipelines": tracked,
        "rows_checked": len(rows),
        "slow_without_stage_timing": slow_without_timing,
        "over_budget": over_budget,
    }


def _model_status_map(conn: Any) -> dict[str, str]:
    if not _table_exists(conn, "mart_model_lifecycle"):
        return {}
    columns = _table_columns(conn, "mart_model_lifecycle")
    if not {"model_id", "status"}.issubset(columns):
        return {}
    rows = conn.execute(
        """
        SELECT model_id, status
          FROM mart_model_lifecycle
         WHERE model_id IS NOT NULL
           AND model_id <> ''
        """
    ).fetchall()
    return {str(_row_value(row, "model_id", 0)): str(_row_value(row, "status", 1) or "").lower() for row in rows}


def _check_recommendation_outputs(
    conn: Any,
    details: list[dict[str, Any]],
    blockers: list[str],
    warnings: list[str],
) -> dict[str, Any]:
    lifecycle_status = _model_status_map(conn)
    champion_ids = {model_id for model_id, status in lifecycle_status.items() if status == "champion"}
    checked_tables: list[str] = []
    invalid_rows_total = 0
    multi_primary_dates_total = 0
    non_investable_rows_total = 0
    universe_policy = load_recommendation_universe_policy()
    evidence: dict[str, Any] = {
        "exists": False,
        "champion_model_ids": sorted(champion_ids),
        "universe_policy_id": universe_policy.policy_id,
        "tables": {},
    }
    table_specs = (
        ("mart_daily_recommendation", True, True),
        ("mart_daily_topk_view_cache", True, True),
        ("mart_daily_recommendation_risk", True, False),
    )
    for table_name, has_primary_semantics, has_run_mode_semantics in table_specs:
        if not _table_exists(conn, table_name):
            continue
        columns = _table_columns(conn, table_name)
        if "model_id" not in columns:
            continue
        evidence["exists"] = True
        checked_tables.append(table_name)
        champion_filter_parts = []
        if has_primary_semantics and "is_primary" in columns:
            champion_filter_parts.append("COALESCE(is_primary, FALSE) = TRUE")
        if has_run_mode_semantics and "run_mode" in columns:
            champion_filter_parts.append("lower(COALESCE(run_mode, '')) = 'champion'")
        if not champion_filter_parts:
            continue
        champion_filter = " OR ".join(champion_filter_parts)
        status_case = (
            "CASE "
            + " ".join(
                f"WHEN model_id = ? THEN ?" for _ in lifecycle_status
            )
            + " ELSE NULL END"
            if lifecycle_status
            else "NULL"
        )
        status_params: list[Any] = []
        for model_id, status in lifecycle_status.items():
            status_params.extend([model_id, status])
        row = conn.execute(
            f"""
            SELECT COUNT(*) AS n
              FROM {_quote_ident(table_name)}
             WHERE ({champion_filter})
               AND COALESCE(({status_case}), 'missing') <> 'champion'
            """,
            status_params,
        ).fetchone()
        invalid_rows = int(_row_value(row, "n", 0) or 0)
        invalid_rows_total += invalid_rows
        examples = []
        if invalid_rows:
            examples = [
                dict(row)
                for row in conn.execute(
                    f"""
                    SELECT model_id,
                           COUNT(*) AS row_count,
                           MIN({_quote_ident('snapshot_date')}) AS min_snapshot_date,
                           MAX({_quote_ident('snapshot_date')}) AS max_snapshot_date
                      FROM {_quote_ident(table_name)}
                     WHERE ({champion_filter})
                       AND COALESCE(({status_case}), 'missing') <> 'champion'
                     GROUP BY model_id
                     ORDER BY row_count DESC, model_id
                     LIMIT 10
                    """,
                    status_params,
                ).fetchall()
            ]
        item = _detail(
            domain="recommendation_output",
            table_name=table_name,
            column_name="model_id",
            check_name="primary_outputs_use_lifecycle_champion",
            status="pass" if invalid_rows == 0 else "fail",
            row_count=_count_rows(conn, table_name),
            violation_count=invalid_rows,
            reason=(
                None
                if invalid_rows == 0
                else "primary/champion recommendation outputs must reference the lifecycle champion only"
            ),
            examples=examples,
        )
        _append_outcome(item, details=details, blockers=blockers, warnings=warnings)

        multi_primary_dates = 0
        if "snapshot_date" in columns and "is_primary" in columns:
            row = conn.execute(
                f"""
                SELECT COUNT(*) AS n
                  FROM (
                        SELECT snapshot_date
                          FROM {_quote_ident(table_name)}
                         WHERE COALESCE(is_primary, FALSE) = TRUE
                         GROUP BY snapshot_date
                        HAVING COUNT(DISTINCT model_id) > 1
                       )
                """
            ).fetchone()
            multi_primary_dates = int(_row_value(row, "n", 0) or 0)
            multi_primary_dates_total += multi_primary_dates
            item = _detail(
                domain="recommendation_output",
                table_name=table_name,
                column_name="snapshot_date",
                check_name="single_primary_model_per_snapshot",
                status="pass" if multi_primary_dates == 0 else "fail",
                row_count=_count_rows(conn, table_name),
                violation_count=multi_primary_dates,
                reason=None if multi_primary_dates == 0 else "each snapshot_date may have only one primary model",
            )
            _append_outcome(item, details=details, blockers=blockers, warnings=warnings)
        non_investable_rows = 0
        if (
            table_name in {"mart_daily_recommendation", "mart_daily_topk_view_cache"}
            and "stock_code" in columns
            and _table_exists(conn, "dim_active_a_stock")
        ):
            rows = conn.execute(
                f"""
                SELECT stock_code
                  FROM {_quote_ident(table_name)}
                 WHERE ({champion_filter})
                   AND stock_code IS NOT NULL
                   AND TRIM(CAST(stock_code AS VARCHAR)) <> ''
                """
            ).fetchall()
            stock_codes = [str(_row_value(row, "stock_code", 0)) for row in rows]
            exclusions = explain_universe_exclusions(conn, stock_codes, policy=universe_policy)
            non_investable_rows = sum(1 for code in stock_codes if code in exclusions)
            non_investable_rows_total += non_investable_rows
            examples = [
                {"stock_code": code, "reason": reason}
                for code, reason in list(exclusions.items())[:10]
            ]
            item = _detail(
                domain="recommendation_output",
                table_name=table_name,
                column_name="stock_code",
                check_name="primary_outputs_use_investable_universe",
                status="pass" if non_investable_rows == 0 else "fail",
                row_count=_count_rows(conn, table_name),
                violation_count=non_investable_rows,
                reason=(
                    None
                    if non_investable_rows == 0
                    else f"primary/champion recommendations must satisfy {universe_policy.policy_id}"
                ),
                examples=examples,
            )
            _append_outcome(item, details=details, blockers=blockers, warnings=warnings)
        evidence["tables"][table_name] = {
            "row_count": _count_rows(conn, table_name),
            "invalid_primary_or_champion_rows": invalid_rows,
            "multi_primary_dates": multi_primary_dates,
            "non_investable_primary_or_champion_rows": non_investable_rows,
        }
    if not checked_tables:
        details.append(_detail(
            domain="recommendation_output",
            table_name="mart_daily_recommendation",
            check_name="recommendation_tables_present",
            status="pass",
            severity="warning",
            reason="recommendation output tables are absent in this environment",
        ))
    evidence["checked_tables"] = checked_tables
    evidence["invalid_primary_or_champion_rows"] = invalid_rows_total
    evidence["multi_primary_dates"] = multi_primary_dates_total
    evidence["non_investable_primary_or_champion_rows"] = non_investable_rows_total
    return evidence


def _model_feature_contract_violations(feature_cols_raw: Any) -> list[dict[str, Any]]:
    try:
        feature_cols = json.loads(feature_cols_raw or "[]")
    except Exception:
        return [{"feature_name": "__feature_cols_json__", "reason": "invalid_json"}]
    if not isinstance(feature_cols, list):
        return [{"feature_name": "__feature_cols_json__", "reason": "not_list"}]
    registry = load_feature_registry()
    excluded = set(registry.model_input_excluded)
    violations: list[dict[str, Any]] = []
    for feature_name_raw in feature_cols:
        feature_name = str(feature_name_raw)
        if feature_name in {"regime_up", "regime_flat", "regime_down"}:
            continue
        spec = registry.features.get(feature_name)
        reason = None
        if feature_name in excluded:
            reason = "model_input_excluded"
        elif spec is None:
            reason = "missing_feature_registry_contract"
        elif spec.label:
            reason = "label_column"
        elif not spec.enabled:
            reason = "feature_disabled"
        elif not spec.model_input:
            reason = "not_model_input"
        elif not spec.production_ready:
            reason = "not_production_ready"
        elif str(spec.null_policy) == "excluded_until_backfilled":
            reason = "excluded_until_backfilled"
        if reason:
            violations.append(
                {
                    "feature_name": feature_name,
                    "reason": reason,
                    "feature_group": getattr(spec, "group", None),
                    "feature_role": getattr(spec, "feature_role", None),
                    "null_policy": getattr(spec, "null_policy", None),
                }
            )
    return violations


def _check_model_feature_contracts(
    conn: Any,
    details: list[dict[str, Any]],
    blockers: list[str],
    warnings: list[str],
    *,
    current_policy_hash: str,
) -> dict[str, Any]:
    if not _table_exists(conn, "mart_multidim_model"):
        details.append(_detail(
            domain="model_feature_contract",
            table_name="mart_multidim_model",
            check_name="model_table_present",
            status="pass",
            severity="warning",
            reason="mart_multidim_model is absent in this environment",
        ))
        return {"exists": False}
    columns = _table_columns(conn, "mart_multidim_model")
    if not {"model_id", "feature_cols_json", "pricing_policy_hash"}.issubset(columns):
        item = _detail(
            domain="model_feature_contract",
            table_name="mart_multidim_model",
            check_name="model_feature_contract_columns",
            status="fail",
            reason="current-policy model feature contract check requires model_id, feature_cols_json, pricing_policy_hash",
        )
        _append_outcome(item, details=details, blockers=blockers, warnings=warnings)
        return {"exists": True, "checked_models": 0, "violating_models": 0}
    rows = conn.execute(
        """
        SELECT model_id, feature_cols_json, pricing_policy_hash
          FROM mart_multidim_model
         WHERE pricing_policy_hash = ?
        """,
        (current_policy_hash,),
    ).fetchall()
    examples: list[dict[str, Any]] = []
    violating = 0
    bad_feature_count = 0
    for row in rows:
        violations = _model_feature_contract_violations(_row_value(row, "feature_cols_json", 1))
        if not violations:
            continue
        violating += 1
        bad_feature_count += len(violations)
        examples.append(
            {
                "model_id": _row_value(row, "model_id", 0),
                "violations": violations[:20],
            }
        )
    item = _detail(
        domain="model_feature_contract",
        table_name="mart_multidim_model",
        column_name="feature_cols_json",
        check_name="current_policy_models_use_allowed_features",
        status="pass" if violating == 0 else "fail",
        row_count=len(rows),
        violation_count=bad_feature_count,
        reason=None if violating == 0 else "current pricing-policy models must use production-ready model-input features only",
        examples=examples[:10],
    )
    _append_outcome(item, details=details, blockers=blockers, warnings=warnings)
    return {
        "exists": True,
        "checked_models": len(rows),
        "violating_models": violating,
        "bad_feature_count": bad_feature_count,
        "examples": examples[:10],
    }


def _check_model_lifecycle_integrity(
    conn: Any,
    details: list[dict[str, Any]],
    blockers: list[str],
    warnings: list[str],
) -> dict[str, Any]:
    if not _table_exists(conn, "mart_model_lifecycle") or not _table_exists(conn, "mart_multidim_model"):
        details.append(_detail(
            domain="model_lifecycle",
            table_name="mart_model_lifecycle",
            check_name="lifecycle_model_row_integrity",
            status="pass",
            severity="warning",
            reason="model lifecycle/model tables are not both present in this environment",
        ))
        return {"exists": False}
    lifecycle_cols = _table_columns(conn, "mart_model_lifecycle")
    model_cols = _table_columns(conn, "mart_multidim_model")
    if "model_id" not in lifecycle_cols or "model_id" not in model_cols:
        item = _detail(
            domain="model_lifecycle",
            table_name="mart_model_lifecycle",
            check_name="lifecycle_model_row_integrity",
            status="fail",
            reason="model lifecycle integrity requires model_id columns",
        )
        _append_outcome(item, details=details, blockers=blockers, warnings=warnings)
        return {"exists": True, "missing_model_rows": 0, "missing_lifecycle_rows": 0, "dangling_promoted_from": 0}
    missing_model_rows = conn.execute(
        """
        SELECT l.model_id
          FROM mart_model_lifecycle l
          LEFT JOIN mart_multidim_model m
            ON l.model_id = m.model_id
         WHERE m.model_id IS NULL
         ORDER BY l.model_id
         LIMIT 20
        """
    ).fetchall()
    missing_lifecycle_rows = conn.execute(
        """
        SELECT m.model_id
          FROM mart_multidim_model m
          LEFT JOIN mart_model_lifecycle l
            ON m.model_id = l.model_id
         WHERE l.model_id IS NULL
         ORDER BY m.model_id
         LIMIT 20
        """
    ).fetchall()
    dangling_promoted_from: list[Any] = []
    if "promoted_from" in lifecycle_cols:
        dangling_promoted_from = conn.execute(
            """
            SELECT l.model_id, l.promoted_from
              FROM mart_model_lifecycle l
              LEFT JOIN mart_model_lifecycle p
                ON l.promoted_from = p.model_id
             WHERE l.promoted_from IS NOT NULL
               AND l.promoted_from <> ''
               AND p.model_id IS NULL
             ORDER BY l.model_id
             LIMIT 20
            """
        ).fetchall()
    violation_count = len(missing_model_rows) + len(missing_lifecycle_rows) + len(dangling_promoted_from)
    examples = [
        *(
            {"type": "lifecycle_without_model_row", "model_id": _row_value(row, "model_id", 0)}
            for row in missing_model_rows
        ),
        *(
            {"type": "model_without_lifecycle_row", "model_id": _row_value(row, "model_id", 0)}
            for row in missing_lifecycle_rows
        ),
        *(
            {
                "type": "dangling_promoted_from",
                "model_id": _row_value(row, "model_id", 0),
                "promoted_from": _row_value(row, "promoted_from", 1),
            }
            for row in dangling_promoted_from
        ),
    ]
    item = _detail(
        domain="model_lifecycle",
        table_name="mart_model_lifecycle",
        check_name="lifecycle_model_row_integrity",
        status="pass" if violation_count == 0 else "fail",
        row_count=violation_count,
        violation_count=violation_count,
        reason=(
            None
            if violation_count == 0
            else "model lifecycle rows must not retain dangling model or predecessor references after direct-delete cleanup"
        ),
        examples=examples,
    )
    _append_outcome(item, details=details, blockers=blockers, warnings=warnings)
    return {
        "exists": True,
        "missing_model_rows": len(missing_model_rows),
        "missing_lifecycle_rows": len(missing_lifecycle_rows),
        "dangling_promoted_from": len(dangling_promoted_from),
        "examples": examples,
    }


FORBIDDEN_CLEANUP_DIR_NAMES = {
    ".git",
    ".hg",
    ".svn",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    ".venv",
    "venv",
    "node_modules",
}
FORBIDDEN_CLEANUP_MARKERS = ("archive", "backup", "quarantine", "归档", "备份", "隔离")
FORBIDDEN_CLEANUP_SUFFIXES = (".bak", ".backup", ".orig")
FORBIDDEN_CLEANUP_TABLE_SUFFIXES = ("_bak", "_backup", "_orig")


def _is_forbidden_cleanup_name(name: str, *, table_name: bool = False) -> bool:
    lowered = name.lower()
    if any(marker in lowered for marker in FORBIDDEN_CLEANUP_MARKERS):
        return True
    suffixes = FORBIDDEN_CLEANUP_TABLE_SUFFIXES if table_name else FORBIDDEN_CLEANUP_SUFFIXES
    if any(lowered.endswith(suffix) for suffix in suffixes):
        return True
    return (not table_name) and lowered.endswith("~")


def _is_forbidden_cleanup_artifact(path: Path) -> bool:
    return _is_forbidden_cleanup_name(path.name, table_name=False)


def _find_forbidden_cleanup_tables(conn: Any) -> list[str]:
    try:
        table_names = [
            str(_row_value(row, "table_name", 0))
            for row in conn.execute(
                """
                SELECT table_name
                  FROM information_schema.tables
                 ORDER BY table_name
                """
            ).fetchall()
        ]
    except Exception:
        return []
    return sorted(
        name
        for name in table_names
        if _is_forbidden_cleanup_name(name, table_name=True)
    )


def _find_forbidden_cleanup_artifacts(root: Path, *, max_depth: int | None = None) -> list[str]:
    if not root.exists():
        return []
    forbidden: list[str] = []
    stack: list[tuple[Path, int]] = [(root, 0)]
    while stack:
        path, depth = stack.pop()
        if max_depth is not None and depth > max_depth:
            continue
        try:
            children = list(path.iterdir())
        except OSError:
            continue
        for child in children:
            if child.name in FORBIDDEN_CLEANUP_DIR_NAMES:
                continue
            if _is_forbidden_cleanup_artifact(child):
                forbidden.append(str(child))
                continue
            if child.is_symlink():
                continue
            if child.is_dir() and (max_depth is None or depth + 1 <= max_depth):
                stack.append((child, depth + 1))
    return sorted(forbidden)


def _check_cleanup_policy(
    conn: Any,
    details: list[dict[str, Any]],
    blockers: list[str],
    warnings: list[str],
) -> dict[str, Any]:
    forbidden_tables = _find_forbidden_cleanup_tables(conn)
    forbidden_artifacts = _find_forbidden_cleanup_artifacts(WORKSPACE_ROOT)
    forbidden_dirs = [path for path in forbidden_artifacts if Path(path).is_dir()]
    forbidden_files = [path for path in forbidden_artifacts if Path(path).is_file()]
    violation_count = len(forbidden_tables) + len(forbidden_artifacts)
    item = _detail(
        domain="cleanup_policy",
        table_name="mart_data_deletion_record",
        check_name="direct_delete_no_archive",
        status="pass" if violation_count == 0 else "fail",
        row_count=violation_count,
        violation_count=violation_count,
        reason=(
            None
            if violation_count == 0
            else "obsolete data cleanup must delete verified stale artifacts directly; archive/backup/quarantine artifacts are globally forbidden"
        ),
        examples=[
            *({"kind": "duckdb_forbidden_cleanup_table", "name": table} for table in forbidden_tables[:10]),
            *({"kind": "filesystem_cleanup_artifact", "path": path} for path in forbidden_artifacts[:10]),
        ],
    )
    _append_outcome(item, details=details, blockers=blockers, warnings=warnings)
    return {
        "delete_policy": DELETE_POLICY,
        "cleanup_artifact_scan_root": str(WORKSPACE_ROOT),
        "forbidden_table_count": len(forbidden_tables),
        "backup_table_count": len([name for name in forbidden_tables if "backup" in name.lower()]),
        "forbidden_dir_count": len(forbidden_dirs),
        "forbidden_file_count": len(forbidden_files),
        "forbidden_artifact_count": len(forbidden_artifacts),
        "examples": item.get("examples", []),
    }


def _processing_monitor_tables(conn: Any, *, include_market: bool) -> list[tuple[str, str | None]]:
    tables: list[tuple[str, str | None]] = []
    if _table_exists(conn, "mart_data_processing_tool_run"):
        tables.append(("mart_data_processing_tool_run", "mart_data_processing_tool_issue"))
    if (
        include_market
        and attach_market_if_available(conn)
        and _table_exists(conn, "market.mart_data_processing_tool_run")
    ):
        issue_table = "market.mart_data_processing_tool_issue" if _table_exists(
            conn,
            "market.mart_data_processing_tool_issue",
        ) else None
        tables.append(("market.mart_data_processing_tool_run", issue_table))
    return tables


def _check_data_processing_monitor(
    conn: Any,
    details: list[dict[str, Any]],
    blockers: list[str],
    warnings: list[str],
    *,
    recent_limit: int,
    include_market: bool,
) -> dict[str, Any]:
    tables = _processing_monitor_tables(conn, include_market=include_market)
    if not tables:
        item = _detail(
            domain="data_processing_monitor",
            table_name="mart_data_processing_tool_run",
            check_name="monitor_exists",
            status="fail",
            reason="data cleaning and processing tools must write auditable run evidence",
        )
        _append_outcome(item, details=details, blockers=blockers, warnings=warnings)
        return {"exists": False}

    total_rows = 0
    rejected_runs = 0
    unclassified_rejected_runs = 0
    issue_rows = 0
    checked_tables = [table for table, _ in tables]
    for run_table, issue_table in tables:
        row = conn.execute(
            f"SELECT COUNT(*) AS n FROM {_quote_table(run_table)}"
        ).fetchone()
        total_rows += int(_row_value(row, "n", 0) or 0)
        recent = conn.execute(
            f"""
            SELECT run_id, tool_name, source_name, rejected_rows, reason_counts_json, duration_s
              FROM {_quote_table(run_table)}
             ORDER BY ended_at DESC
             LIMIT {int(recent_limit)}
            """
        ).fetchall()
        for item_row in recent:
            rejected = int(_row_value(item_row, "rejected_rows", 3) or 0)
            if rejected <= 0:
                continue
            rejected_runs += 1
            reason_counts = _safe_json_load(_row_value(item_row, "reason_counts_json", 4))
            if not isinstance(reason_counts, dict) or not reason_counts:
                unclassified_rejected_runs += 1
                item = _detail(
                    domain="data_processing_monitor",
                    table_name=run_table,
                    column_name=str(_row_value(item_row, "tool_name", 1)),
                    check_name="rejected_rows_have_reason",
                    status="fail",
                    row_count=len(recent),
                    violation_count=1,
                    reason="rejected rows must carry machine-readable rejection reason counts",
                    examples=[
                        {
                            "run_id": _row_value(item_row, "run_id", 0),
                            "source_name": _row_value(item_row, "source_name", 2),
                            "rejected_rows": rejected,
                        }
                    ],
                )
                _append_outcome(item, details=details, blockers=blockers, warnings=warnings)
        if issue_table:
            row = conn.execute(
                f"SELECT COUNT(*) AS n FROM {_quote_table(issue_table)}"
            ).fetchone()
            issue_rows += int(_row_value(row, "n", 0) or 0)

    details.append(_detail(
        domain="data_processing_monitor",
        table_name=",".join(checked_tables),
        check_name="monitor_summary",
        status="pass",
        severity="warning",
        row_count=total_rows,
        violation_count=rejected_runs,
        reason=(
            "processing monitor is available; rejected rows are acceptable only "
            "when reason_counts_json and issue samples explain them"
        ),
        examples=[
            {
                "checked_tables": checked_tables,
                "rejected_runs_checked": rejected_runs,
                "unclassified_rejected_runs": unclassified_rejected_runs,
                "issue_rows": issue_rows,
            }
        ],
    ))
    if total_rows == 0:
        item = _detail(
            domain="data_processing_monitor",
            table_name=",".join(checked_tables),
            check_name="recent_tool_runs_exist",
            status="fail",
            severity="warning",
            reason="no processing tool run rows found yet; future writes must populate this monitor",
        )
        _append_outcome(item, details=details, blockers=blockers, warnings=warnings)
    return {
        "exists": True,
        "tables": checked_tables,
        "rows": total_rows,
        "rejected_runs_checked": rejected_runs,
        "unclassified_rejected_runs": unclassified_rejected_runs,
        "issue_rows": issue_rows,
    }


def _persist_details(conn: Any, *, gate_run_id: str, details: list[dict[str, Any]], built_at: str) -> None:
    conn.execute("DELETE FROM mart_global_data_quality_detail WHERE gate_run_id = ?", (gate_run_id,))
    rows = [
        (
            gate_run_id,
            item["domain"],
            item.get("table_name"),
            item.get("column_name"),
            item["check_name"],
            item["status"],
            item["severity"],
            item.get("row_count"),
            item.get("violation_count"),
            item.get("reason"),
            json.dumps(item.get("examples") or [], ensure_ascii=False, sort_keys=True),
            built_at,
        )
        for item in details
    ]
    conn.executemany(
        """
        INSERT INTO mart_global_data_quality_detail (
            gate_run_id, domain, table_name, column_name, check_name,
            status, severity, row_count, violation_count, reason,
            examples_json, built_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )


def record_global_data_quality_gate(
    conn: Any,
    *,
    gate_run_id: str | None = None,
    gate_scope: str = "model_training",
    feature_tables: list[str] | None = None,
    include_market: bool = True,
    include_institution_events: bool = True,
    include_pipeline_performance: bool = True,
    strict_feature_nulls: bool = True,
    recent_pipeline_limit: int = 200,
    example_limit: int = 5,
) -> dict[str, Any]:
    policy = load_pricing_label_policy()
    ensure_global_data_quality_tables(conn)
    record_pricing_label_policy(conn, policy)
    started_at = utc_now_iso()
    started = time.perf_counter()
    built_at = datetime.now(UTC).replace(tzinfo=None).isoformat(timespec="seconds")
    gate_run_id = gate_run_id or f"global_data_quality_{gate_scope}_{built_at.replace(':', '').replace('-', '')}"
    feature_tables = feature_tables or ["fact_feature_panel", "fact_feature_panel_candidate"]
    details: list[dict[str, Any]] = []
    blockers: list[str] = []
    warnings: list[str] = []
    stage_timings: dict[str, float] = {}
    _emit_progress(
        f"start scope={gate_scope} feature_tables={','.join(feature_tables)} "
        f"strict_feature_nulls={strict_feature_nulls}"
    )

    evidence: dict[str, Any] = {"feature_tables": {}, "stage_timings": stage_timings}
    stage_started = time.perf_counter()
    _emit_progress("calendar_preflight start")
    evidence["calendar"] = _check_calendar(conn, details, blockers, warnings)
    stage_timings["calendar_preflight_s"] = round(time.perf_counter() - stage_started, 3)
    _emit_progress(f"calendar_preflight done elapsed={stage_timings['calendar_preflight_s']:.3f}s")

    for table_name in feature_tables:
        stage_started = time.perf_counter()
        _emit_progress(f"feature_table_scan start table={table_name}")
        evidence["feature_tables"][table_name] = _check_feature_table(
            conn,
            table_name,
            details,
            blockers,
            warnings,
            example_limit=example_limit,
            strict_nulls=strict_feature_nulls,
        )
        if include_market:
            evidence["feature_tables"][table_name]["kline_alignment"] = _check_feature_table_kline_alignment(
                conn,
                table_name,
                details,
                blockers,
                warnings,
                example_limit=example_limit,
            )
        stage_timings[f"feature_table_scan:{table_name}_s"] = round(time.perf_counter() - stage_started, 3)
        _emit_progress(
            f"feature_table_scan done table={table_name} "
            f"elapsed={stage_timings[f'feature_table_scan:{table_name}_s']:.3f}s"
        )
    if include_market:
        stage_started = time.perf_counter()
        _emit_progress("kline_scan start")
        evidence["kline"] = _check_market_kline(conn, details, blockers, warnings)
        stage_timings["kline_scan_s"] = round(time.perf_counter() - stage_started, 3)
        _emit_progress(f"kline_scan done elapsed={stage_timings['kline_scan_s']:.3f}s")
    if include_institution_events:
        stage_started = time.perf_counter()
        _emit_progress("institution_event_scan start")
        evidence["institution_events"] = _check_institution_events(conn, details, blockers, warnings)
        stage_timings["institution_event_scan_s"] = round(time.perf_counter() - stage_started, 3)
        _emit_progress(f"institution_event_scan done elapsed={stage_timings['institution_event_scan_s']:.3f}s")
    stage_started = time.perf_counter()
    _emit_progress("holder_availability_scan start")
    evidence["holder_availability"] = _check_holder_availability(
        conn,
        details,
        blockers,
        warnings,
        example_limit=example_limit,
    )
    stage_timings["holder_availability_scan_s"] = round(time.perf_counter() - stage_started, 3)
    _emit_progress(
        f"holder_availability_scan done elapsed={stage_timings['holder_availability_scan_s']:.3f}s"
    )
    stage_started = time.perf_counter()
    _emit_progress("tdx_f10_source_availability_scan start")
    evidence["tdx_f10_source_availability"] = _check_tdx_f10_source_availability(
        conn,
        details,
        blockers,
        warnings,
        example_limit=example_limit,
    )
    stage_timings["tdx_f10_source_availability_scan_s"] = round(time.perf_counter() - stage_started, 3)
    _emit_progress(
        "tdx_f10_source_availability_scan done "
        f"elapsed={stage_timings['tdx_f10_source_availability_scan_s']:.3f}s"
    )
    stage_started = time.perf_counter()
    _emit_progress("recommendation_output_scan start")
    evidence["recommendation_outputs"] = _check_recommendation_outputs(conn, details, blockers, warnings)
    stage_timings["recommendation_output_scan_s"] = round(time.perf_counter() - stage_started, 3)
    _emit_progress(
        f"recommendation_output_scan done elapsed={stage_timings['recommendation_output_scan_s']:.3f}s"
    )
    stage_started = time.perf_counter()
    _emit_progress("model_feature_contract_scan start")
    evidence["model_feature_contract"] = _check_model_feature_contracts(
        conn,
        details,
        blockers,
        warnings,
        current_policy_hash=policy.policy_hash(),
    )
    stage_timings["model_feature_contract_scan_s"] = round(time.perf_counter() - stage_started, 3)
    _emit_progress(
        f"model_feature_contract_scan done elapsed={stage_timings['model_feature_contract_scan_s']:.3f}s"
    )
    stage_started = time.perf_counter()
    _emit_progress("model_lifecycle_scan start")
    evidence["model_lifecycle"] = _check_model_lifecycle_integrity(conn, details, blockers, warnings)
    stage_timings["model_lifecycle_scan_s"] = round(time.perf_counter() - stage_started, 3)
    _emit_progress(
        f"model_lifecycle_scan done elapsed={stage_timings['model_lifecycle_scan_s']:.3f}s"
    )
    stage_started = time.perf_counter()
    _emit_progress("cleanup_policy_scan start")
    evidence["cleanup_policy"] = _check_cleanup_policy(conn, details, blockers, warnings)
    stage_timings["cleanup_policy_scan_s"] = round(time.perf_counter() - stage_started, 3)
    _emit_progress(
        f"cleanup_policy_scan done elapsed={stage_timings['cleanup_policy_scan_s']:.3f}s"
    )
    if include_pipeline_performance:
        stage_started = time.perf_counter()
        _emit_progress("pipeline_performance_scan start")
        evidence["pipeline_performance"] = _check_pipeline_performance(
            conn,
            details,
            blockers,
            warnings,
            policy_sections=policy.definition_sections,
            recent_limit=recent_pipeline_limit,
        )
        stage_timings["pipeline_performance_scan_s"] = round(time.perf_counter() - stage_started, 3)
        _emit_progress(
            f"pipeline_performance_scan done elapsed={stage_timings['pipeline_performance_scan_s']:.3f}s"
        )
    stage_started = time.perf_counter()
    _emit_progress("data_processing_monitor_scan start")
    evidence["data_processing_monitor"] = _check_data_processing_monitor(
        conn,
        details,
        blockers,
        warnings,
        recent_limit=recent_pipeline_limit,
        include_market=include_market,
    )
    stage_timings["data_processing_monitor_scan_s"] = round(time.perf_counter() - stage_started, 3)
    _emit_progress(
        f"data_processing_monitor_scan done "
        f"elapsed={stage_timings['data_processing_monitor_scan_s']:.3f}s"
    )

    blockers = sorted(set(blockers))
    warnings = sorted(set(warnings))
    gate_status = "pass" if not blockers else "blocked"
    ended_at = utc_now_iso()
    duration_s = time.perf_counter() - started
    stage_timings["total_before_persist_s"] = round(duration_s, 3)
    stage_started = time.perf_counter()
    _emit_progress(f"persist start status={gate_status} blockers={len(blockers)} warnings={len(warnings)}")
    _persist_details(conn, gate_run_id=gate_run_id, details=details, built_at=built_at)
    conn.execute(
        """
        INSERT OR REPLACE INTO mart_global_data_quality_gate (
            gate_run_id, policy_id, policy_hash, gate_scope, gate_status,
            blockers_json, warnings_json, evidence_json,
            started_at, ended_at, duration_s
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            gate_run_id,
            policy.policy_id,
            policy.policy_hash(),
            gate_scope,
            gate_status,
            json.dumps(blockers, ensure_ascii=False, sort_keys=True),
            json.dumps(warnings, ensure_ascii=False, sort_keys=True),
            json.dumps(evidence, ensure_ascii=False, sort_keys=True),
            started_at,
            ended_at,
            duration_s,
        ),
    )
    record_actual_version(conn, "mart_global_data_quality_gate")
    record_actual_version(conn, "mart_global_data_quality_detail")
    record_actual_version(conn, "mart_feature_null_policy")
    record_actual_version(conn, "mart_candidate_feature_set_contract")
    record_actual_version(conn, "mart_feature_availability_contract")
    record_actual_version(conn, "mart_data_processing_tool_run")
    record_actual_version(conn, "mart_data_processing_tool_issue")
    record_actual_version(conn, "mart_data_deletion_record")
    record_pipeline_run(
        conn,
        run_id=gate_run_id,
        pipeline_name="validate_global_data_quality",
        status=gate_status,
        started_at=started_at,
        ended_at=ended_at,
        duration_s=duration_s,
        commit_sha=git_commit_sha(REPO),
        input_tables=[
            "dim_trading_calendar",
            *feature_tables,
            "mart_pipeline_run_manifest",
            "mart_data_processing_tool_run",
            "mart_data_processing_tool_issue",
        ],
        output_tables=[
            "mart_global_data_quality_gate",
            "mart_global_data_quality_detail",
            "mart_feature_null_policy",
            "mart_candidate_feature_set_contract",
            "mart_feature_availability_contract",
            "mart_data_processing_tool_run",
            "mart_data_processing_tool_issue",
        ],
        gate_result=gate_status,
        blockers=blockers,
        perf_summary={
            "detail_count": len(details),
            "blocker_count": len(blockers),
            "warning_count": len(warnings),
            "strict_feature_nulls": strict_feature_nulls,
            "stage_timings": stage_timings,
        },
    )
    try:
        conn.commit()
    except Exception:
        pass
    stage_timings["persist_s"] = round(time.perf_counter() - stage_started, 3)
    _emit_progress(f"done status={gate_status} elapsed={duration_s:.3f}s")
    return {
        "gate_run_id": gate_run_id,
        "policy_id": policy.policy_id,
        "policy_hash": policy.policy_hash(),
        "gate_scope": gate_scope,
        "gate_status": gate_status,
        "blockers": blockers,
        "warnings": warnings,
        "evidence": evidence,
        "duration_s": round(duration_s, 3),
    }
