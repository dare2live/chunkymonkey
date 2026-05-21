#!/usr/bin/env python3
"""Build an Optuna-ready feature search space from association evidence."""
from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.db import get_conn  # noqa: E402
from services.pipeline_manifest import git_commit_sha, record_pipeline_run, utc_now_iso  # noqa: E402
from services.schema_versions import record_actual_version  # noqa: E402


DDL = """
CREATE TABLE IF NOT EXISTS mart_feature_search_space (
    run_id TEXT NOT NULL,
    source_association_run_id TEXT NOT NULL,
    panel_table TEXT NOT NULL,
    label_name TEXT NOT NULL,
    feature_name TEXT NOT NULL,
    feature_group TEXT,
    cluster_id TEXT,
    representative_feature TEXT,
    rank_ic DOUBLE,
    abs_rank_ic DOUBLE,
    rank_direction INTEGER,
    coverage_pct DOUBLE,
    fold_count INTEGER,
    sign_stability DOUBLE,
    fold_valid_count INTEGER,
    fold_same_direction_rate DOUBLE,
    fold_rank_ic_std DOUBLE,
    long_short_spread DOUBLE,
    selection_role TEXT NOT NULL,
    selection_reason TEXT,
    built_at TEXT NOT NULL,
    PRIMARY KEY (run_id, feature_name)
);
CREATE INDEX IF NOT EXISTS idx_feature_search_space_run_role
    ON mart_feature_search_space(run_id, selection_role);
ALTER TABLE mart_feature_search_space ADD COLUMN IF NOT EXISTS fold_valid_count INTEGER;
ALTER TABLE mart_feature_search_space ADD COLUMN IF NOT EXISTS fold_same_direction_rate DOUBLE;
ALTER TABLE mart_feature_search_space ADD COLUMN IF NOT EXISTS fold_rank_ic_std DOUBLE;

CREATE TABLE IF NOT EXISTS mart_feature_search_space_summary (
    run_id TEXT PRIMARY KEY,
    source_association_run_id TEXT NOT NULL,
    panel_table TEXT NOT NULL,
    label_name TEXT NOT NULL,
    selected_count INTEGER,
    excluded_count INTEGER,
    selected_features_json TEXT,
    rejected_features_json TEXT,
    group_counts_json TEXT,
    config_json TEXT,
    built_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS mart_model_selection_run (
    run_id TEXT PRIMARY KEY,
    feature_set_id TEXT NOT NULL,
    method TEXT NOT NULL,
    label_name TEXT,
    objective_score DOUBLE,
    selected_features_json TEXT,
    rejected_features_json TEXT,
    trials INTEGER,
    promote_to_champion BOOLEAN DEFAULT FALSE,
    notes TEXT,
    built_at TEXT
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


def _finite_float(value: Any) -> float | None:
    if value is None:
        return None
    value = float(value)
    return value if math.isfinite(value) else None


def _parse_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def latest_association_run_id(conn: Any) -> str | None:
    row = conn.execute(
        """
        SELECT run_id
          FROM mart_feature_association_stat
         GROUP BY run_id
         ORDER BY MAX(built_at) DESC NULLS LAST, run_id DESC
         LIMIT 1
        """
    ).fetchone()
    return str(row["run_id"]) if row else None


def _load_association_rows(conn: Any, association_run_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT run_id, panel_table, label_name, feature_name, feature_group,
               coverage_pct, rank_ic, fold_count, fold_same_sign_rate,
               long_short_spread
          FROM mart_feature_association_stat
         WHERE run_id = ?
         ORDER BY feature_name
        """,
        (association_run_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def _load_fold_metrics(conn: Any, association_run_id: str) -> dict[str, dict[str, Any]]:
    try:
        rows = conn.execute(
            """
            SELECT feature_name, rank_ic
              FROM mart_feature_association_fold
             WHERE run_id = ?
               AND rank_ic IS NOT NULL
            """,
            (association_run_id,),
        ).fetchall()
    except Exception:
        return {}
    by_feature: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        value = _finite_float(row["rank_ic"])
        if value is not None:
            by_feature[str(row["feature_name"])].append(value)
    return {
        feature: {
            "fold_values": values,
            "fold_valid_count": len(values),
            "fold_rank_ic_std": _sample_std(values),
        }
        for feature, values in by_feature.items()
    }


def _load_clusters(conn: Any, association_run_id: str) -> dict[str, dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT cluster_id, feature_name, representative_feature, corr_to_representative
          FROM mart_feature_correlation_cluster
         WHERE run_id = ?
        """,
        (association_run_id,),
    ).fetchall()
    return {str(row["feature_name"]): dict(row) for row in rows}


def _table_exists(conn: Any, table: str) -> bool:
    row = conn.execute(
        "SELECT COUNT(*) FROM information_schema.tables WHERE table_name = ?",
        (table,),
    ).fetchone()
    return bool(row and row[0])


def _table_columns(conn: Any, table: str) -> set[str]:
    return {
        row[0]
        for row in conn.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = ?",
            (table,),
        ).fetchall()
    }


def _quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _load_panel_coverage(
    conn: Any,
    *,
    table: str | None,
    feature_set_id: str | None,
    features: list[str],
) -> dict[str, float]:
    if not table:
        return {}
    if not _table_exists(conn, table):
        raise RuntimeError(f"coverage override table does not exist: {table}")
    columns = _table_columns(conn, table)
    has_feature_set = "feature_set_id" in columns
    where_sql = "WHERE feature_set_id = ?" if has_feature_set and feature_set_id else ""
    params = (feature_set_id,) if has_feature_set and feature_set_id else ()
    out: dict[str, float] = {}
    existing_features = []
    for feature in features:
        if feature not in columns:
            out[feature] = 0.0
        else:
            existing_features.append(feature)
    if existing_features:
        select_parts = [
            f"COUNT({_quote_ident(feature)}) * 100.0 / NULLIF(COUNT(*), 0) AS {_quote_ident(f'c_{idx}')}"
            for idx, feature in enumerate(existing_features)
        ]
        row = conn.execute(
            f"""
            SELECT {", ".join(select_parts)}
              FROM {_quote_ident(table)}
              {where_sql}
            """,
            params,
        ).fetchone()
        for idx, feature in enumerate(existing_features):
            out[feature] = float(row[idx] or 0.0)
    return out


def _sign_stability(rank_ic: float | None, same_sign_rate: float | None) -> float | None:
    if rank_ic is None or same_sign_rate is None:
        return None
    return same_sign_rate if rank_ic >= 0 else 1.0 - same_sign_rate


def _direction(rank_ic: float | None) -> int | None:
    if rank_ic is None:
        return None
    return -1 if rank_ic < 0 else 1


def _sample_std(values: list[float]) -> float | None:
    if len(values) < 2:
        return None
    mean = sum(values) / len(values)
    return math.sqrt(sum((value - mean) ** 2 for value in values) / (len(values) - 1))


def _quality_key(item: dict[str, Any]) -> tuple[float, float, str]:
    return (
        abs(float(item.get("rank_ic") or 0.0)),
        float(item.get("coverage_pct") or 0.0),
        str(item.get("feature_name") or ""),
    )


def _rejection_reason(
    row: dict[str, Any],
    *,
    min_abs_rank_ic: float,
    min_coverage_pct: float,
    min_fold_count: int,
    min_sign_stability: float,
    min_fold_same_direction_rate: float | None = None,
    max_fold_rank_ic_std: float | None = None,
) -> str | None:
    rank_ic = row["rank_ic"]
    if rank_ic is None:
        return "missing_rank_ic"
    if abs(rank_ic) < min_abs_rank_ic:
        return f"low_abs_rank_ic:{rank_ic:.6f}"
    coverage = float(row.get("coverage_pct") or 0.0)
    if coverage < min_coverage_pct:
        return f"low_coverage:{coverage:.2f}"
    fold_count = int(row.get("fold_count") or 0)
    if fold_count < min_fold_count:
        return f"low_fold_count:{fold_count}"
    stability = row.get("sign_stability")
    if stability is None:
        return "missing_sign_stability"
    if float(stability) < min_sign_stability:
        return f"low_sign_stability:{float(stability):.3f}"
    if min_fold_same_direction_rate is not None:
        fold_direction = row.get("fold_same_direction_rate")
        if fold_direction is None:
            return "missing_fold_same_direction_rate"
        if float(fold_direction) < min_fold_same_direction_rate:
            return f"low_fold_same_direction_rate:{float(fold_direction):.3f}"
    if max_fold_rank_ic_std is not None:
        fold_std = row.get("fold_rank_ic_std")
        if fold_std is None:
            return "missing_fold_rank_ic_std"
        if float(fold_std) > max_fold_rank_ic_std:
            return f"high_fold_rank_ic_std:{float(fold_std):.6f}"
    return None


def build_feature_search_space(
    conn: Any,
    *,
    association_run_id: str | None = None,
    run_id: str | None = None,
    feature_set_id: str = "production_registry",
    min_abs_rank_ic: float = 0.02,
    min_coverage_pct: float = 60.0,
    min_fold_count: int = 20,
    min_sign_stability: float = 0.55,
    max_features: int = 30,
    protected_features: list[str] | None = None,
    coverage_table: str | None = None,
    coverage_feature_set_id: str | None = None,
    auto_coverage_table: bool = True,
    min_fold_same_direction_rate: float | None = None,
    max_fold_rank_ic_std: float | None = None,
) -> dict[str, Any]:
    ensure_tables(conn)
    association_run_id = association_run_id or latest_association_run_id(conn)
    if not association_run_id:
        raise RuntimeError("no feature association run found")
    association_rows = _load_association_rows(conn, association_run_id)
    if not association_rows:
        raise RuntimeError(f"feature association run has no rows: {association_run_id}")
    panel_table = str(association_rows[0]["panel_table"])
    effective_coverage_table = coverage_table
    if effective_coverage_table is None and auto_coverage_table and _table_exists(conn, panel_table):
        effective_coverage_table = panel_table
    clusters = _load_clusters(conn, association_run_id)
    fold_metrics = _load_fold_metrics(conn, association_run_id)
    coverage_override = _load_panel_coverage(
        conn,
        table=effective_coverage_table,
        feature_set_id=coverage_feature_set_id,
        features=[str(row["feature_name"]) for row in association_rows],
    )
    protected = set(protected_features or [])
    run_id = run_id or f"feature_space_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"
    built_at = datetime.utcnow().isoformat(timespec="seconds")

    enriched = []
    for raw in association_rows:
        row = dict(raw)
        feature = str(row["feature_name"])
        rank_ic = _finite_float(row.get("rank_ic"))
        same_sign = _finite_float(row.get("fold_same_sign_rate"))
        cluster = clusters.get(feature) or {
            "cluster_id": f"single:{feature}",
            "representative_feature": feature,
            "corr_to_representative": 1.0,
        }
        row["rank_ic"] = rank_ic
        row["abs_rank_ic"] = abs(rank_ic) if rank_ic is not None else None
        if feature in coverage_override:
            row["coverage_pct"] = coverage_override[feature]
        row["rank_direction"] = _direction(rank_ic)
        row["sign_stability"] = _sign_stability(rank_ic, same_sign)
        fold = fold_metrics.get(feature, {})
        fold_values = list(fold.get("fold_values") or [])
        direction = row["rank_direction"]
        if direction is None or not fold_values:
            fold_same_direction_rate = None
        else:
            fold_same_direction_rate = sum(
                1
                for value in fold_values
                if (value < 0 and direction < 0) or (value > 0 and direction > 0)
            ) / len(fold_values)
        row["fold_valid_count"] = fold.get("fold_valid_count")
        row["fold_same_direction_rate"] = fold_same_direction_rate
        row["fold_rank_ic_std"] = fold.get("fold_rank_ic_std")
        row["cluster_id"] = cluster["cluster_id"]
        row["representative_feature"] = cluster["representative_feature"]
        row["selection_role"] = "excluded"
        row["selection_reason"] = _rejection_reason(
            row,
            min_abs_rank_ic=min_abs_rank_ic,
            min_coverage_pct=min_coverage_pct,
            min_fold_count=min_fold_count,
            min_sign_stability=min_sign_stability,
            min_fold_same_direction_rate=min_fold_same_direction_rate,
            max_fold_rank_ic_std=max_fold_rank_ic_std,
        )
        if feature in protected:
            row["selection_role"] = "protected"
            row["selection_reason"] = "protected_baseline"
        enriched.append(row)

    eligible = [row for row in enriched if row["selection_reason"] is None]
    by_cluster: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in eligible:
        by_cluster[str(row["cluster_id"])].append(row)

    cluster_best: dict[str, dict[str, Any]] = {}
    for cluster_id, rows in by_cluster.items():
        cluster_best[cluster_id] = sorted(rows, key=_quality_key, reverse=True)[0]
    for row in eligible:
        best = cluster_best[str(row["cluster_id"])]
        row["representative_feature"] = best["feature_name"]
        if row["feature_name"] == best["feature_name"]:
            row["selection_role"] = "candidate"
            row["selection_reason"] = "cluster_representative"
        else:
            row["selection_role"] = "excluded"
            row["selection_reason"] = f"cluster_redundant:{best['feature_name']}"

    selected_candidates = sorted(
        [row for row in enriched if row["selection_role"] == "candidate"],
        key=_quality_key,
        reverse=True,
    )
    protected_rows = [row for row in enriched if row["selection_role"] == "protected"]
    candidate_budget = max(max_features - len(protected_rows), 0)
    selected_feature_names = {row["feature_name"] for row in selected_candidates[:candidate_budget]}
    for row in selected_candidates[candidate_budget:]:
        row["selection_role"] = "excluded"
        row["selection_reason"] = "feature_budget"
    for row in selected_candidates[:candidate_budget]:
        if row["feature_name"] in selected_feature_names:
            row["selection_reason"] = "selected_by_rank_ic_cluster"

    selected = [
        row for row in enriched
        if row["selection_role"] in {"protected", "candidate"}
    ]
    rejected = [
        row for row in enriched
        if row["selection_role"] == "excluded"
    ]
    selected_sorted = [
        *sorted(
            [row for row in selected if row["selection_role"] == "protected"],
            key=_quality_key,
            reverse=True,
        ),
        *sorted(
            [row for row in selected if row["selection_role"] == "candidate"],
            key=_quality_key,
            reverse=True,
        ),
    ]
    group_counts = Counter(str(row.get("feature_group") or "unknown") for row in selected)
    label_name = str(association_rows[0]["label_name"])

    conn.executemany(
        """
        INSERT OR REPLACE INTO mart_feature_search_space
        (run_id, source_association_run_id, panel_table, label_name,
         feature_name, feature_group, cluster_id, representative_feature,
         rank_ic, abs_rank_ic, rank_direction, coverage_pct, fold_count,
         sign_stability, fold_valid_count, fold_same_direction_rate,
         fold_rank_ic_std, long_short_spread, selection_role, selection_reason,
         built_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                run_id,
                association_run_id,
                panel_table,
                label_name,
                row["feature_name"],
                row.get("feature_group"),
                row.get("cluster_id"),
                row.get("representative_feature"),
                row.get("rank_ic"),
                row.get("abs_rank_ic"),
                row.get("rank_direction"),
                row.get("coverage_pct"),
                row.get("fold_count"),
                row.get("sign_stability"),
                row.get("fold_valid_count"),
                row.get("fold_same_direction_rate"),
                row.get("fold_rank_ic_std"),
                row.get("long_short_spread"),
                row.get("selection_role"),
                row.get("selection_reason"),
                built_at,
            )
            for row in enriched
        ],
    )
    selected_features = [str(row["feature_name"]) for row in selected_sorted]
    rejected_features = {str(row["feature_name"]): row.get("selection_reason") for row in rejected}
    config = {
        "min_abs_rank_ic": min_abs_rank_ic,
        "min_coverage_pct": min_coverage_pct,
        "min_fold_count": min_fold_count,
        "min_sign_stability": min_sign_stability,
        "max_features": max_features,
        "feature_set_id": feature_set_id,
        "protected_features": sorted(protected),
        "coverage_table": effective_coverage_table,
        "coverage_feature_set_id": coverage_feature_set_id,
        "auto_coverage_table": auto_coverage_table,
        "coverage_override": bool(coverage_override),
        "min_fold_same_direction_rate": min_fold_same_direction_rate,
        "max_fold_rank_ic_std": max_fold_rank_ic_std,
    }
    conn.execute(
        """
        INSERT OR REPLACE INTO mart_feature_search_space_summary
        (run_id, source_association_run_id, panel_table, label_name,
         selected_count, excluded_count, selected_features_json,
         rejected_features_json, group_counts_json, config_json, built_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_id,
            association_run_id,
            panel_table,
            label_name,
            len(selected_features),
            len(rejected),
            json.dumps(selected_features, ensure_ascii=False),
            json.dumps(rejected_features, ensure_ascii=False, sort_keys=True),
            json.dumps(dict(group_counts), ensure_ascii=False, sort_keys=True),
            json.dumps(config, ensure_ascii=False, sort_keys=True),
            built_at,
        ),
    )
    objective_score = (
        sum(abs(float(row["rank_ic"] or 0.0)) for row in selected) / max(len(selected), 1)
        if selected else None
    )
    conn.execute(
        """
        INSERT OR REPLACE INTO mart_model_selection_run
        (run_id, feature_set_id, method, label_name, objective_score,
         selected_features_json, rejected_features_json, trials,
         promote_to_champion, notes, built_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_id,
            feature_set_id,
            "association_cluster_prefilter",
            label_name,
            objective_score,
            json.dumps(selected_features, ensure_ascii=False),
            json.dumps(rejected_features, ensure_ascii=False, sort_keys=True),
            0,
            False,
            json.dumps(
                {
                    "source_association_run_id": association_run_id,
                    "group_counts": dict(group_counts),
                    "config": config,
                    "message": "feature search-space prefilter; no model trained or promoted",
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            built_at,
        ),
    )
    record_actual_version(conn, "mart_feature_search_space")
    record_actual_version(conn, "mart_feature_search_space_summary")
    record_actual_version(conn, "mart_model_selection_run")
    record_pipeline_run(
        conn,
        run_id=run_id,
        pipeline_name="build_feature_search_space",
        status="success",
        started_at=built_at,
        ended_at=utc_now_iso(),
        commit_sha=git_commit_sha(Path(__file__).resolve().parent.parent.parent),
        input_tables=[
            "mart_feature_association_stat",
            "mart_feature_correlation_cluster",
            "mart_feature_association_fold",
            *( [effective_coverage_table] if effective_coverage_table else [] ),
        ],
        output_tables=[
            "mart_feature_search_space",
            "mart_feature_search_space_summary",
            "mart_model_selection_run",
        ],
        label_name=label_name,
        perf_summary={
            "source_association_run_id": association_run_id,
            "selected_count": len(selected_features),
            "excluded_count": len(rejected),
            "fold_metric_features": len(fold_metrics),
            "group_counts": dict(group_counts),
            "config": config,
        },
    )
    conn.commit()
    return {
        "run_id": run_id,
        "source_association_run_id": association_run_id,
        "selected_count": len(selected_features),
        "excluded_count": len(rejected),
        "selected_features": selected_features,
        "group_counts": dict(group_counts),
        "fold_metric_features": len(fold_metrics),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--association-run-id", default=None)
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--feature-set-id", default="production_registry")
    parser.add_argument("--min-abs-rank-ic", type=float, default=0.02)
    parser.add_argument("--min-coverage-pct", type=float, default=60.0)
    parser.add_argument("--min-fold-count", type=int, default=20)
    parser.add_argument("--min-sign-stability", type=float, default=0.55)
    parser.add_argument("--max-features", type=int, default=30)
    parser.add_argument("--protected-features", default="")
    parser.add_argument(
        "--coverage-table",
        default=None,
        help="Optional production panel table used to override association coverage before filtering",
    )
    parser.add_argument("--coverage-feature-set-id", default=None)
    parser.add_argument("--min-fold-same-direction-rate", type=float, default=None)
    parser.add_argument("--max-fold-rank-ic-std", type=float, default=None)
    parser.add_argument(
        "--no-coverage-override",
        action="store_true",
        help="Disable the default production-panel coverage override",
    )
    args = parser.parse_args()

    with get_conn() as conn:
        result = build_feature_search_space(
            conn,
            association_run_id=args.association_run_id,
            run_id=args.run_id,
            feature_set_id=args.feature_set_id,
            min_abs_rank_ic=args.min_abs_rank_ic,
            min_coverage_pct=args.min_coverage_pct,
            min_fold_count=args.min_fold_count,
            min_sign_stability=args.min_sign_stability,
            max_features=args.max_features,
            protected_features=_parse_csv(args.protected_features),
            coverage_table=args.coverage_table,
            coverage_feature_set_id=args.coverage_feature_set_id,
            auto_coverage_table=not args.no_coverage_override,
            min_fold_same_direction_rate=args.min_fold_same_direction_rate,
            max_fold_rank_ic_std=args.max_fold_rank_ic_std,
        )
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
