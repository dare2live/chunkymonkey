#!/usr/bin/env python3
"""Build feature drift root-cause evidence from model stability trials."""
from __future__ import annotations

import argparse
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
import json
import math
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.db import get_conn  # noqa: E402
from services.pipeline_manifest import git_commit_sha, record_pipeline_run, utc_now_iso  # noqa: E402
from services.schema_versions import record_actual_version  # noqa: E402


OUTPUT_TABLES = [
    "mart_feature_drift_root_cause",
    "mart_feature_drift_root_cause_summary",
]

DDL = """
CREATE TABLE IF NOT EXISTS mart_feature_drift_root_cause (
    run_id TEXT NOT NULL,
    source_run_id TEXT NOT NULL,
    trial_number INTEGER,
    model_family TEXT,
    scope TEXT NOT NULL,
    fold_id INTEGER,
    period_start TEXT,
    period_end TEXT,
    feature_name TEXT NOT NULL,
    psi_value DOUBLE NOT NULL,
    psi_threshold DOUBLE NOT NULL,
    severity TEXT NOT NULL,
    status TEXT,
    objective_value DOUBLE,
    built_at TEXT NOT NULL,
    PRIMARY KEY (run_id, source_run_id, trial_number, scope, fold_id, feature_name)
);
CREATE INDEX IF NOT EXISTS idx_feature_drift_root_cause_source
    ON mart_feature_drift_root_cause(run_id, source_run_id, feature_name);
CREATE INDEX IF NOT EXISTS idx_feature_drift_root_cause_severity
    ON mart_feature_drift_root_cause(run_id, severity, psi_value);

CREATE TABLE IF NOT EXISTS mart_feature_drift_root_cause_summary (
    run_id TEXT NOT NULL,
    source_run_id TEXT NOT NULL,
    feature_name TEXT NOT NULL,
    model_family TEXT,
    offender_count INTEGER NOT NULL,
    severe_count INTEGER NOT NULL,
    avg_psi DOUBLE,
    max_psi DOUBLE,
    scopes_json TEXT NOT NULL,
    fold_ids_json TEXT NOT NULL,
    recommendation TEXT NOT NULL,
    built_at TEXT NOT NULL,
    PRIMARY KEY (run_id, source_run_id, feature_name)
);
CREATE INDEX IF NOT EXISTS idx_feature_drift_root_cause_summary_max
    ON mart_feature_drift_root_cause_summary(run_id, max_psi DESC);
"""


@dataclass
class DriftRow:
    source_run_id: str
    trial_number: int | None
    model_family: str | None
    scope: str
    fold_id: int | None
    period_start: str | None
    period_end: str | None
    feature_name: str
    psi_value: float
    psi_threshold: float
    severity: str
    status: str | None
    objective_value: float | None


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


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _safe_json(value: str | None) -> Any:
    if not value:
        return None
    try:
        return json.loads(value)
    except Exception:
        return None


def _finite_float(value: Any) -> float | None:
    try:
        out = float(value)
    except Exception:
        return None
    return out if math.isfinite(out) else None


def _severity(psi: float, threshold: float) -> str:
    if psi >= max(threshold * 2.0, 0.50):
        return "severe"
    if psi >= threshold:
        return "offender"
    return "monitor"


def _recommendation(max_psi: float | None, offender_count: int, severe_count: int, threshold: float) -> str:
    max_psi = float(max_psi or 0.0)
    if severe_count >= 2 or max_psi >= max(threshold * 3.0, 0.75):
        return "exclude_or_transform_before_next_large_study"
    if severe_count == 1 or offender_count >= 3:
        return "winsorize_bucket_or_regime_split"
    if offender_count > 0:
        return "monitor_with_gate_warning"
    return "keep"


def _iter_trial_rows(conn: Any, source_run_ids: list[str] | None = None) -> Iterable[Any]:
    params: list[Any] = []
    where = ""
    if source_run_ids:
        where = "WHERE run_id IN (" + ",".join("?" for _ in source_run_ids) + ")"
        params = list(source_run_ids)
    return conn.execute(
        f"""
        SELECT run_id, trial_number, model_family, objective_value, status,
               fold_metrics_json
          FROM mart_model_stability_search_trial
          {where}
         ORDER BY run_id, trial_number
        """,
        params,
    ).fetchall()


def _iter_summary_rows(conn: Any, source_run_ids: list[str] | None = None) -> Iterable[Any]:
    params: list[Any] = []
    where = ""
    if source_run_ids:
        where = "WHERE run_id IN (" + ",".join("?" for _ in source_run_ids) + ")"
        params = list(source_run_ids)
    return conn.execute(
        f"""
        SELECT run_id, best_trial_number, config_json
          FROM mart_model_stability_search_summary
          {where}
         ORDER BY run_id
        """,
        params,
    ).fetchall()


def collect_drift_rows(
    conn: Any,
    *,
    source_run_ids: list[str] | None = None,
    psi_threshold: float = 0.25,
    include_monitor: bool = False,
) -> list[DriftRow]:
    rows: list[DriftRow] = []
    threshold = float(psi_threshold)
    for trial in _iter_trial_rows(conn, source_run_ids):
        fold_metrics = _safe_json(trial["fold_metrics_json"])
        if not isinstance(fold_metrics, list):
            continue
        for fold in fold_metrics:
            if not isinstance(fold, dict):
                continue
            by_feature = fold.get("feature_drift_psi_by_feature") or {}
            if not isinstance(by_feature, dict):
                continue
            for feature, raw_psi in by_feature.items():
                psi = _finite_float(raw_psi)
                if psi is None:
                    continue
                severity = _severity(psi, threshold)
                if severity == "monitor" and not include_monitor:
                    continue
                rows.append(
                    DriftRow(
                        source_run_id=str(trial["run_id"]),
                        trial_number=int(trial["trial_number"]) if trial["trial_number"] is not None else None,
                        model_family=trial["model_family"],
                        scope="walkforward_fold",
                        fold_id=int(fold["fold_id"]) if fold.get("fold_id") is not None else None,
                        period_start=fold.get("test_start"),
                        period_end=fold.get("test_end"),
                        feature_name=str(feature),
                        psi_value=psi,
                        psi_threshold=threshold,
                        severity=severity,
                        status=trial["status"],
                        objective_value=_finite_float(trial["objective_value"]),
                    )
                )
    for summary in _iter_summary_rows(conn, source_run_ids):
        config = _safe_json(summary["config_json"])
        if not isinstance(config, dict):
            continue
        best_metrics = config.get("best_metrics") or {}
        if not isinstance(best_metrics, dict):
            continue
        by_feature = best_metrics.get("holdout_feature_drift_psi_by_feature") or {}
        if not isinstance(by_feature, dict):
            continue
        model_family = config.get("model_family")
        for feature, raw_psi in by_feature.items():
            psi = _finite_float(raw_psi)
            if psi is None:
                continue
            severity = _severity(psi, threshold)
            if severity == "monitor" and not include_monitor:
                continue
            rows.append(
                DriftRow(
                    source_run_id=str(summary["run_id"]),
                    trial_number=int(summary["best_trial_number"]) if summary["best_trial_number"] is not None else None,
                    model_family=str(model_family) if model_family else None,
                    scope="holdout_best",
                    fold_id=None,
                    period_start=None,
                    period_end=None,
                    feature_name=str(feature),
                    psi_value=psi,
                    psi_threshold=threshold,
                    severity=severity,
                    status="best_trial",
                    objective_value=None,
                )
            )
    return rows


def summarize_drift_rows(rows: list[DriftRow], *, built_at: str) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[DriftRow]] = defaultdict(list)
    for row in rows:
        grouped[(row.source_run_id, row.feature_name)].append(row)
    out: list[dict[str, Any]] = []
    for (source_run_id, feature_name), items in sorted(grouped.items()):
        values = [item.psi_value for item in items]
        severe_count = sum(1 for item in items if item.severity == "severe")
        offender_count = len(items)
        threshold = items[0].psi_threshold if items else 0.25
        out.append(
            {
                "source_run_id": source_run_id,
                "feature_name": feature_name,
                "model_family": items[0].model_family,
                "offender_count": offender_count,
                "severe_count": severe_count,
                "avg_psi": sum(values) / len(values) if values else None,
                "max_psi": max(values) if values else None,
                "scopes": sorted({item.scope for item in items}),
                "fold_ids": sorted({item.fold_id for item in items if item.fold_id is not None}),
                "recommendation": _recommendation(max(values) if values else None, offender_count, severe_count, threshold),
                "built_at": built_at,
            }
        )
    return sorted(out, key=lambda item: (item["source_run_id"], -(item["max_psi"] or 0.0), item["feature_name"]))


def persist_drift_root_cause(
    conn: Any,
    *,
    run_id: str,
    rows: list[DriftRow],
    summaries: list[dict[str, Any]],
    built_at: str,
) -> dict[str, Any]:
    ensure_tables(conn)
    conn.execute("DELETE FROM mart_feature_drift_root_cause WHERE run_id = ?", (run_id,))
    conn.execute("DELETE FROM mart_feature_drift_root_cause_summary WHERE run_id = ?", (run_id,))
    conn.executemany(
        """
        INSERT INTO mart_feature_drift_root_cause (
            run_id, source_run_id, trial_number, model_family, scope, fold_id,
            period_start, period_end, feature_name, psi_value, psi_threshold,
            severity, status, objective_value, built_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                run_id,
                row.source_run_id,
                row.trial_number,
                row.model_family,
                row.scope,
                int(row.fold_id) if row.fold_id is not None else -1,
                row.period_start,
                row.period_end,
                row.feature_name,
                row.psi_value,
                row.psi_threshold,
                row.severity,
                row.status,
                row.objective_value,
                built_at,
            )
            for row in rows
        ],
    )
    conn.executemany(
        """
        INSERT INTO mart_feature_drift_root_cause_summary (
            run_id, source_run_id, feature_name, model_family, offender_count,
            severe_count, avg_psi, max_psi, scopes_json, fold_ids_json,
            recommendation, built_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                run_id,
                item["source_run_id"],
                item["feature_name"],
                item["model_family"],
                item["offender_count"],
                item["severe_count"],
                item["avg_psi"],
                item["max_psi"],
                _json(item["scopes"]),
                _json(item["fold_ids"]),
                item["recommendation"],
                built_at,
            )
            for item in summaries
        ],
    )
    record_actual_version(conn, "mart_feature_drift_root_cause")
    record_actual_version(conn, "mart_feature_drift_root_cause_summary")
    conn.commit()
    return {
        "detail_rows": len(rows),
        "summary_rows": len(summaries),
        "source_runs": len({row.source_run_id for row in rows}),
        "severe_rows": sum(1 for row in rows if row.severity == "severe"),
    }


def build_feature_drift_root_cause(
    conn: Any,
    *,
    run_id: str | None = None,
    source_run_ids: list[str] | None = None,
    psi_threshold: float = 0.25,
    include_monitor: bool = False,
) -> dict[str, Any]:
    started_at = utc_now_iso()
    started = time.perf_counter()
    run_id = run_id or f"feature_drift_root_cause_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"
    built_at = datetime.utcnow().isoformat(timespec="seconds")
    rows = collect_drift_rows(
        conn,
        source_run_ids=source_run_ids,
        psi_threshold=psi_threshold,
        include_monitor=include_monitor,
    )
    summaries = summarize_drift_rows(rows, built_at=built_at)
    result = persist_drift_root_cause(
        conn,
        run_id=run_id,
        rows=rows,
        summaries=summaries,
        built_at=built_at,
    )
    top_features = [
        {
            "source_run_id": item["source_run_id"],
            "feature_name": item["feature_name"],
            "max_psi": item["max_psi"],
            "offender_count": item["offender_count"],
            "recommendation": item["recommendation"],
        }
        for item in sorted(summaries, key=lambda item: (-(item["max_psi"] or 0.0), -item["offender_count"]))[:20]
    ]
    duration_s = time.perf_counter() - started
    record_pipeline_run(
        conn,
        run_id=run_id,
        pipeline_name="build_feature_drift_root_cause",
        status="success",
        started_at=started_at,
        ended_at=utc_now_iso(),
        duration_s=duration_s,
        commit_sha=git_commit_sha(Path(__file__).resolve().parent.parent.parent),
        input_tables=["mart_model_stability_search_trial", "mart_model_stability_search_summary"],
        output_tables=OUTPUT_TABLES,
        perf_summary={
            **result,
            "psi_threshold": float(psi_threshold),
            "include_monitor": bool(include_monitor),
            "source_run_ids": source_run_ids or "all",
            "top_features": top_features,
            "duration_s": duration_s,
        },
    )
    conn.commit()
    return {
        "run_id": run_id,
        **result,
        "top_features": top_features,
    }


def _parse_csv(value: str | None) -> list[str] | None:
    if not value:
        return None
    out = [item.strip() for item in value.split(",") if item.strip()]
    return out or None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--source-run-ids", default=None)
    parser.add_argument("--psi-threshold", type=float, default=0.25)
    parser.add_argument("--include-monitor", action="store_true")
    args = parser.parse_args()
    with get_conn() as conn:
        result = build_feature_drift_root_cause(
            conn,
            run_id=args.run_id,
            source_run_ids=_parse_csv(args.source_run_ids),
            psi_threshold=args.psi_threshold,
            include_monitor=args.include_monitor,
        )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
