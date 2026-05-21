"""Backfill a unified strategy result registry from paper_sim KPI marts."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.db import get_conn
from services.duck_adapter import connect as duck_connect


REGISTRY_TABLE = "mart_strategy_result_registry"
PAPER_SIM_KPI_TABLE = "mart_paper_sim_kpi"
LAMBDAMART_COMPARE_TABLE = "mart_paper_sim_lambdamart_v6_kpi_compare"

REGISTRY_DDL = f"""
CREATE TABLE IF NOT EXISTS {REGISTRY_TABLE} (
    result_id           TEXT PRIMARY KEY,
    source_table        TEXT NOT NULL,
    source_pk           TEXT NOT NULL,
    result_type         TEXT NOT NULL,
    model_id            TEXT,
    sim_run_id          TEXT,
    comparison_id       TEXT,
    variant             TEXT,
    model_label         TEXT,
    period_start        TEXT,
    period_end          TEXT,
    annual_return       DOUBLE,
    max_dd              DOUBLE,
    sharpe              DOUBLE,
    monthly_win_rate    DOUBLE,
    rank_ic             DOUBLE,
    turnover            DOUBLE,
    leakage_flag        BOOLEAN NOT NULL DEFAULT FALSE,
    parent_result_id    TEXT,
    baseline_result_id  TEXT,
    sim_config_hash     TEXT,
    param_diff_json     TEXT,
    params_json         TEXT,
    lineage_url         TEXT,
    source_artifact_uri TEXT,
    production_status   TEXT NOT NULL DEFAULT 'unknown',
    decision            TEXT NOT NULL DEFAULT 'unknown',
    decision_reason     TEXT,
    evidence_json       TEXT NOT NULL DEFAULT '{{}}',
    built_at            TIMESTAMP,
    registered_at       TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_msrr_source
    ON {REGISTRY_TABLE}(source_table, source_pk);
CREATE INDEX IF NOT EXISTS idx_msrr_model
    ON {REGISTRY_TABLE}(model_id, sim_run_id, comparison_id);
"""

REGISTRY_SCHEMA_MIGRATIONS = [
    f"ALTER TABLE {REGISTRY_TABLE} ADD COLUMN IF NOT EXISTS parent_result_id TEXT",
    f"ALTER TABLE {REGISTRY_TABLE} ADD COLUMN IF NOT EXISTS baseline_result_id TEXT",
    f"ALTER TABLE {REGISTRY_TABLE} ADD COLUMN IF NOT EXISTS sim_config_hash TEXT",
    f"ALTER TABLE {REGISTRY_TABLE} ADD COLUMN IF NOT EXISTS param_diff_json TEXT",
    f"ALTER TABLE {REGISTRY_TABLE} ADD COLUMN IF NOT EXISTS params_json TEXT",
    f"ALTER TABLE {REGISTRY_TABLE} ADD COLUMN IF NOT EXISTS lineage_url TEXT",
    f"ALTER TABLE {REGISTRY_TABLE} ADD COLUMN IF NOT EXISTS source_artifact_uri TEXT",
]


def _utc_now_text() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _json_default(value: Any) -> str:
    return str(value)


def _stable_json(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=_json_default)


def _result_id(source_table: str, source_pk: str, result_type: str) -> str:
    payload = f"{source_table}\0{source_pk}\0{result_type}".encode("utf-8")
    return "strategy_result:" + hashlib.sha256(payload).hexdigest()[:32]


def _row_dict(row: Any) -> dict[str, Any]:
    return {key: row[key] for key in row.keys()}


def _query_dicts(conn: Any, sql: str) -> list[dict[str, Any]]:
    cursor = conn.execute(sql)
    rows = cursor.fetchall()
    if not rows:
        return []
    first = rows[0]
    if hasattr(first, "keys"):
        return [_row_dict(row) for row in rows]
    columns = [str(col[0]) for col in cursor.description]
    return [dict(zip(columns, row)) for row in rows]


def _first(row: dict[str, Any], names: list[str], default: Any = None) -> Any:
    for name in names:
        if name in row and row[name] is not None:
            return row[name]
    return default


def _float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _bool_or_false(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"1", "true", "yes", "y", "pass", "passed"}


def table_exists(conn: Any, table: str) -> bool:
    row = conn.execute(
        """
        SELECT 1
          FROM information_schema.tables
         WHERE table_schema = 'main' AND table_name = ?
         LIMIT 1
        """,
        [table],
    ).fetchone()
    return row is not None


def ensure_registry_table(conn: Any) -> None:
    if hasattr(conn, "executescript"):
        conn.executescript(REGISTRY_DDL)
    else:
        for statement in REGISTRY_DDL.split(";"):
            statement = statement.strip()
            if statement:
                conn.execute(statement)
    for statement in REGISTRY_SCHEMA_MIGRATIONS:
        conn.execute(statement)


def _production_status_from_kpi(row: dict[str, Any]) -> str:
    if "all_kpi_pass" in row and row["all_kpi_pass"] is not None:
        return "candidate_passed" if _bool_or_false(row["all_kpi_pass"]) else "blocked"
    if "user_criteria_pass" in row and row["user_criteria_pass"] is not None:
        return "candidate_passed" if _bool_or_false(row["user_criteria_pass"]) else "blocked"
    return "unknown"


def _decision_from_kpi(row: dict[str, Any]) -> tuple[str, str | None]:
    if "all_kpi_pass" in row and row["all_kpi_pass"] is not None:
        if _bool_or_false(row["all_kpi_pass"]):
            return "candidate_passed", "all_kpi_pass=true"
        return "blocked", "all_kpi_pass=false"
    if "user_criteria_pass" in row and row["user_criteria_pass"] is not None:
        if _bool_or_false(row["user_criteria_pass"]):
            return "candidate_passed", "user_criteria_pass=true"
        return "blocked", "user_criteria_pass=false"
    return "unknown", None


def _leakage_flag(row: dict[str, Any]) -> bool:
    return _bool_or_false(_first(row, ["leakage_flag", "has_leakage", "leakage_detected"], False))


def _paper_sim_registry_row(row: dict[str, Any], registered_at: str) -> dict[str, Any]:
    sim_run_id = str(row["sim_run_id"])
    variant = str(_first(row, ["variant", "model_label"], "unknown"))
    source_pk = sim_run_id
    parent_sim_run_id = row.get("parent_sim_run_id")
    parent_result_id = (
        _result_id(PAPER_SIM_KPI_TABLE, str(parent_sim_run_id), "paper_sim_kpi")
        if parent_sim_run_id is not None
        else None
    )
    evidence = {
        "n_days": row.get("n_days"),
        "all_kpi_pass": row.get("all_kpi_pass"),
        "user_criteria_pass": row.get("user_criteria_pass"),
        "anti_churn_pass": row.get("anti_churn_pass"),
        "robustness_pass": row.get("robustness_pass"),
        "sim_config_hash": row.get("sim_config_hash"),
        "parent_sim_run_id": row.get("parent_sim_run_id"),
        "param_diff_json": row.get("param_diff_json"),
        "lineage_url": row.get("lineage_url"),
    }
    return {
        "result_id": _result_id(PAPER_SIM_KPI_TABLE, source_pk, "paper_sim_kpi"),
        "source_table": PAPER_SIM_KPI_TABLE,
        "source_pk": source_pk,
        "result_type": "paper_sim_kpi",
        "model_id": _first(row, ["model_id"]),
        "sim_run_id": sim_run_id,
        "comparison_id": None,
        "variant": variant,
        "model_label": _first(row, ["model_label"]),
        "period_start": str(row.get("period_start")) if row.get("period_start") is not None else None,
        "period_end": str(row.get("period_end")) if row.get("period_end") is not None else None,
        "annual_return": _float_or_none(_first(row, ["annual_return", "ann_ret"])),
        "max_dd": _float_or_none(row.get("max_dd")),
        "sharpe": _float_or_none(row.get("sharpe")),
        "monthly_win_rate": _float_or_none(row.get("monthly_win_rate")),
        "rank_ic": _float_or_none(row.get("rank_ic")),
        "turnover": _float_or_none(_first(row, ["annual_turnover", "turnover", "avg_turnover"])),
        "leakage_flag": _leakage_flag(row),
        "parent_result_id": parent_result_id,
        "baseline_result_id": None,
        "sim_config_hash": row.get("sim_config_hash"),
        "param_diff_json": row.get("param_diff_json"),
        "params_json": row.get("config_snapshot"),
        "lineage_url": row.get("lineage_url"),
        "source_artifact_uri": None,
        "production_status": _production_status_from_kpi(row),
        "decision": _decision_from_kpi(row)[0],
        "decision_reason": _decision_from_kpi(row)[1],
        "evidence_json": _stable_json({k: v for k, v in evidence.items() if v is not None}),
        "built_at": row.get("built_at"),
        "registered_at": registered_at,
    }


def _compare_decision(row: dict[str, Any], baseline: dict[str, Any] | None) -> tuple[str, str, str | None]:
    model_label = str(row.get("model_label") or "")
    if "baseline" in model_label.lower():
        return "baseline_reference", "reference", None

    blockers: list[str] = []
    monthly_win_rate = _float_or_none(row.get("monthly_win_rate"))
    if monthly_win_rate is not None and monthly_win_rate < 0.55:
        blockers.append(f"monthly_win_rate {monthly_win_rate:.4f} < 0.55")
    if baseline is not None:
        for metric, label in [("ann_ret", "ann_ret"), ("sharpe", "sharpe")]:
            challenger_value = _float_or_none(row.get(metric))
            baseline_value = _float_or_none(baseline.get(metric))
            if challenger_value is not None and baseline_value is not None and challenger_value < baseline_value:
                blockers.append(f"{label} {challenger_value:.4f} < baseline {baseline_value:.4f}")
    if blockers:
        return "challenger_hold_reject", "hold_reject", "; ".join(blockers)
    return "challenger_review", "review", None


def _compare_registry_row(
    row: dict[str, Any],
    registered_at: str,
    *,
    baseline: dict[str, Any] | None = None,
) -> dict[str, Any]:
    comparison_id = str(row["comparison_id"])
    model_label = str(row["model_label"])
    source_pk = f"{comparison_id}:{model_label}"
    production_status, decision, decision_reason = _compare_decision(row, baseline)
    baseline_result_id = None
    if baseline is not None and baseline.get("model_label") is not None:
        baseline_source_pk = f"{comparison_id}:{baseline['model_label']}"
        baseline_result_id = _result_id(LAMBDAMART_COMPARE_TABLE, baseline_source_pk, "model_compare")
    evidence = {
        "prediction_table": row.get("prediction_table"),
        "rank_ic_n_dates": row.get("rank_ic_n_dates"),
        "source_kpi_built_at": row.get("source_kpi_built_at"),
        "baseline_model_label": baseline.get("model_label") if baseline is not None else None,
        "baseline_ann_ret": baseline.get("ann_ret") if baseline is not None else None,
        "baseline_sharpe": baseline.get("sharpe") if baseline is not None else None,
        "baseline_monthly_win_rate": baseline.get("monthly_win_rate") if baseline is not None else None,
    }
    return {
        "result_id": _result_id(LAMBDAMART_COMPARE_TABLE, source_pk, "model_compare"),
        "source_table": LAMBDAMART_COMPARE_TABLE,
        "source_pk": source_pk,
        "result_type": "model_compare",
        "model_id": _first(row, ["model_id"]),
        "sim_run_id": _first(row, ["sim_run_id"]),
        "comparison_id": comparison_id,
        "variant": None,
        "model_label": model_label,
        "period_start": str(row.get("period_start")) if row.get("period_start") is not None else None,
        "period_end": str(row.get("period_end")) if row.get("period_end") is not None else None,
        "annual_return": _float_or_none(_first(row, ["annual_return", "ann_ret"])),
        "max_dd": _float_or_none(row.get("max_dd")),
        "sharpe": _float_or_none(row.get("sharpe")),
        "monthly_win_rate": _float_or_none(row.get("monthly_win_rate")),
        "rank_ic": _float_or_none(row.get("rank_ic")),
        "turnover": _float_or_none(_first(row, ["annual_turnover", "turnover", "avg_turnover"])),
        "leakage_flag": _leakage_flag(row),
        "parent_result_id": None,
        "baseline_result_id": baseline_result_id,
        "sim_config_hash": None,
        "param_diff_json": None,
        "params_json": _stable_json(
            {
                "comparison_id": comparison_id,
                "model_id": row.get("model_id"),
                "model_label": model_label,
                "prediction_table": row.get("prediction_table"),
                "period_start": str(row.get("period_start")) if row.get("period_start") is not None else None,
                "period_end": str(row.get("period_end")) if row.get("period_end") is not None else None,
            }
        ),
        "lineage_url": None,
        "source_artifact_uri": row.get("prediction_table"),
        "production_status": production_status,
        "decision": decision,
        "decision_reason": decision_reason,
        "evidence_json": _stable_json({k: v for k, v in evidence.items() if v is not None}),
        "built_at": row.get("built_at"),
        "registered_at": registered_at,
    }


def collect_registry_rows(conn: Any, *, registered_at: str | None = None) -> list[dict[str, Any]]:
    registered_at = registered_at or _utc_now_text()
    rows: list[dict[str, Any]] = []
    if table_exists(conn, PAPER_SIM_KPI_TABLE):
        for source in _query_dicts(conn, f"SELECT * FROM {PAPER_SIM_KPI_TABLE} ORDER BY sim_run_id"):
            if source.get("sim_run_id") is not None:
                rows.append(_paper_sim_registry_row(source, registered_at))
    if table_exists(conn, LAMBDAMART_COMPARE_TABLE):
        compare_sources: list[dict[str, Any]] = []
        for source in _query_dicts(
            conn,
            f"SELECT * FROM {LAMBDAMART_COMPARE_TABLE} ORDER BY comparison_id, model_label"
        ):
            if source.get("comparison_id") is not None and source.get("model_label") is not None:
                compare_sources.append(source)
        baselines = {
            str(source["comparison_id"]): source
            for source in compare_sources
            if "baseline" in str(source.get("model_label") or "").lower()
        }
        for source in compare_sources:
            rows.append(
                _compare_registry_row(
                    source,
                    registered_at,
                    baseline=baselines.get(str(source["comparison_id"])),
                )
            )
    return rows


def _existing_registered_at_by_result_id(conn: Any, result_ids: list[str]) -> dict[str, Any]:
    if not result_ids or not table_exists(conn, REGISTRY_TABLE):
        return {}
    placeholders = ", ".join("?" for _ in result_ids)
    rows = conn.execute(
        f"""
        SELECT result_id, registered_at
          FROM {REGISTRY_TABLE}
         WHERE result_id IN ({placeholders})
        """,
        result_ids,
    ).fetchall()
    out: dict[str, Any] = {}
    for row in rows:
        if hasattr(row, "keys"):
            out[str(row["result_id"])] = row["registered_at"]
        else:
            out[str(row[0])] = row[1]
    return out


def upsert_registry_rows(conn: Any, rows: list[dict[str, Any]]) -> int:
    ensure_registry_table(conn)
    if not rows:
        conn.commit()
        return 0
    columns = [
        "result_id",
        "source_table",
        "source_pk",
        "result_type",
        "model_id",
        "sim_run_id",
        "comparison_id",
        "variant",
        "model_label",
        "period_start",
        "period_end",
        "annual_return",
        "max_dd",
        "sharpe",
        "monthly_win_rate",
        "rank_ic",
        "turnover",
        "leakage_flag",
        "parent_result_id",
        "baseline_result_id",
        "sim_config_hash",
        "param_diff_json",
        "params_json",
        "lineage_url",
        "source_artifact_uri",
        "production_status",
        "decision",
        "decision_reason",
        "evidence_json",
        "built_at",
        "registered_at",
    ]
    placeholders = ", ".join(["?"] * len(columns))
    result_ids = [str(row["result_id"]) for row in rows]
    existing_registered_at = _existing_registered_at_by_result_id(conn, result_ids)
    insert_rows = []
    for row in rows:
        result_id = str(row["result_id"])
        if result_id in existing_registered_at:
            row = {**row, "registered_at": existing_registered_at[result_id]}
        insert_rows.append([row.get(col) for col in columns])
    delete_placeholders = ", ".join("?" for _ in result_ids)
    conn.execute(
        f"DELETE FROM {REGISTRY_TABLE} WHERE result_id IN ({delete_placeholders})",
        result_ids,
    )
    conn.executemany(
        f"INSERT INTO {REGISTRY_TABLE} ({', '.join(columns)}) VALUES ({placeholders})",
        insert_rows,
    )
    conn.commit()
    return len(rows)


def backfill(conn: Any, *, dry_run: bool) -> dict[str, Any]:
    rows = collect_registry_rows(conn)
    if dry_run:
        return {
            "mode": "dry-run",
            "source_tables": [PAPER_SIM_KPI_TABLE, LAMBDAMART_COMPARE_TABLE],
            "candidate_rows": len(rows),
            "upserted_rows": 0,
            "rows": rows,
        }
    upserted = upsert_registry_rows(conn, rows)
    return {
        "mode": "apply",
        "source_tables": [PAPER_SIM_KPI_TABLE, LAMBDAMART_COMPARE_TABLE],
        "candidate_rows": len(rows),
        "upserted_rows": upserted,
    }


def _open_conn(db_path: str | None, *, dry_run: bool, timeout: int):
    if db_path:
        return duck_connect(db_path, timeout=timeout, read_only=dry_run)
    return get_conn(timeout=timeout)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-path", default=None, help="DuckDB path; defaults to data/smartmoney.duckdb")
    parser.add_argument("--dry-run", action="store_true", help="print JSON candidates without writing")
    parser.add_argument("--timeout", type=int, default=30)
    args = parser.parse_args()

    conn = _open_conn(args.db_path, dry_run=args.dry_run, timeout=args.timeout)
    try:
        result = backfill(conn, dry_run=args.dry_run)
    finally:
        conn.close()
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True, default=_json_default))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
