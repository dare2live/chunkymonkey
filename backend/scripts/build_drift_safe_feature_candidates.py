#!/usr/bin/env python3
"""Build drift-safe model-selection candidates from stored feature evidence."""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.db import get_conn  # noqa: E402
from services.pipeline_manifest import git_commit_sha, record_pipeline_run, utc_now_iso  # noqa: E402
from services.schema_versions import record_actual_version  # noqa: E402


DEFAULT_EXCLUDED_SEVERITIES = {"critical", "unknown"}

DDL = """
CREATE TABLE IF NOT EXISTS mart_drift_safe_candidate_feature (
    run_id TEXT NOT NULL,
    candidate_id TEXT NOT NULL,
    feature_name TEXT NOT NULL,
    feature_group TEXT,
    feature_rank INTEGER,
    feature_score DOUBLE,
    selection_role TEXT,
    rank_ic DOUBLE,
    abs_rank_ic DOUBLE,
    coverage_pct DOUBLE,
    sign_stability DOUBLE,
    fold_rank_ic_std DOUBLE,
    latest_drift_psi DOUBLE,
    latest_drift_severity TEXT,
    historical_max_psi DOUBLE,
    built_at TEXT NOT NULL,
    PRIMARY KEY (run_id, candidate_id, feature_name)
);
CREATE INDEX IF NOT EXISTS idx_drift_safe_candidate_feature_run
    ON mart_drift_safe_candidate_feature(run_id, candidate_id);

CREATE TABLE IF NOT EXISTS mart_drift_safe_candidate_summary (
    run_id TEXT PRIMARY KEY,
    source_search_space_run_id TEXT NOT NULL,
    drift_model_id TEXT,
    historical_run_ids_json TEXT,
    generated_count INTEGER,
    candidate_ids_json TEXT,
    eligible_features_json TEXT,
    excluded_features_json TEXT,
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


def _finite_float(value: Any, default: float | None = None) -> float | None:
    if value is None:
        return default
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return out if math.isfinite(out) else default


def _json_loads(value: Any, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except Exception:
        return default


def _parse_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def latest_search_space_run_id(conn: Any) -> str | None:
    if not _table_exists(conn, "mart_feature_search_space_summary"):
        return None
    row = conn.execute(
        """
        SELECT run_id
          FROM mart_feature_search_space_summary
         ORDER BY built_at DESC NULLS LAST, run_id DESC
         LIMIT 1
        """
    ).fetchone()
    return str(row["run_id"]) if row else None


def latest_champion_model_id(conn: Any) -> str | None:
    if not _table_exists(conn, "mart_model_lifecycle"):
        return None
    row = conn.execute(
        """
        SELECT model_id
          FROM mart_model_lifecycle
         WHERE status = 'champion'
         ORDER BY deployed_at DESC NULLS LAST, updated_at DESC NULLS LAST
         LIMIT 1
        """
    ).fetchone()
    return str(row["model_id"]) if row else None


def _load_search_space(conn: Any, run_id: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT feature_name, feature_group, selection_role, selection_reason,
               rank_ic, abs_rank_ic, rank_direction, coverage_pct, fold_count,
               sign_stability, fold_valid_count, fold_same_direction_rate,
               fold_rank_ic_std, long_short_spread
          FROM mart_feature_search_space
         WHERE run_id = ?
           AND selection_role IN ('candidate', 'protected')
         ORDER BY abs_rank_ic DESC NULLS LAST, feature_name
        """,
        (run_id,),
    ).fetchall()
    summary = conn.execute(
        """
        SELECT run_id, source_association_run_id, panel_table, label_name,
               selected_features_json, config_json, built_at
          FROM mart_feature_search_space_summary
         WHERE run_id = ?
        """,
        (run_id,),
    ).fetchone()
    if not rows:
        raise RuntimeError(f"search space has no candidate/protected features: {run_id}")
    if not summary:
        raise RuntimeError(f"missing feature search-space summary: {run_id}")
    return [dict(row) for row in rows], dict(summary)


def _load_latest_feature_drift(conn: Any, model_id: str | None) -> dict[str, dict[str, Any]]:
    if not model_id or not _table_exists(conn, "mart_feature_drift"):
        return {}
    snapshot = conn.execute(
        "SELECT MAX(snapshot_at) AS snapshot_at FROM mart_feature_drift WHERE model_id = ?",
        (model_id,),
    ).fetchone()
    if not snapshot or snapshot["snapshot_at"] is None:
        return {}
    rows = conn.execute(
        """
        SELECT feature, psi, severity, n_recent, notes
          FROM mart_feature_drift
         WHERE model_id = ?
           AND snapshot_at = ?
        """,
        (model_id, snapshot["snapshot_at"]),
    ).fetchall()
    return {
        str(row["feature"]): {
            "psi": _finite_float(row["psi"]),
            "severity": str(row["severity"] or "").lower(),
            "n_recent": row["n_recent"],
            "notes": row["notes"],
            "snapshot_at": str(snapshot["snapshot_at"]),
        }
        for row in rows
    }


def _update_max_psi(out: dict[str, float], feature: str, value: Any) -> None:
    psi = _finite_float(value)
    if psi is None:
        return
    out[feature] = max(float(out.get(feature, 0.0)), psi)


def _load_historical_feature_drift(
    conn: Any,
    *,
    run_ids: list[str] | None = None,
) -> dict[str, float]:
    if not _table_exists(conn, "mart_model_stability_search_trial"):
        return {}
    params: list[Any] = []
    where = ""
    if run_ids:
        placeholders = ", ".join("?" for _ in run_ids)
        where = f"WHERE run_id IN ({placeholders})"
        params.extend(run_ids)
    rows = conn.execute(
        f"""
        SELECT fold_metrics_json
          FROM mart_model_stability_search_trial
          {where}
        """,
        params,
    ).fetchall()
    out: dict[str, float] = {}
    for row in rows:
        folds = _json_loads(row["fold_metrics_json"], [])
        if not isinstance(folds, list):
            continue
        for fold in folds:
            if not isinstance(fold, dict):
                continue
            by_feature = fold.get("feature_drift_psi_by_feature") or {}
            if not isinstance(by_feature, dict):
                continue
            for feature, psi in by_feature.items():
                _update_max_psi(out, str(feature), psi)
    if _table_exists(conn, "mart_model_stability_search_summary"):
        summary_params: list[Any] = []
        summary_where = ""
        if run_ids:
            placeholders = ", ".join("?" for _ in run_ids)
            summary_where = f"WHERE run_id IN ({placeholders})"
            summary_params.extend(run_ids)
        rows = conn.execute(
            f"""
            SELECT config_json
              FROM mart_model_stability_search_summary
              {summary_where}
            """,
            summary_params,
        ).fetchall()
        for row in rows:
            config = _json_loads(row["config_json"], {})
            metrics = config.get("best_metrics") if isinstance(config, dict) else {}
            by_feature = (metrics or {}).get("holdout_feature_drift_psi_by_feature") or {}
            if isinstance(by_feature, dict):
                for feature, psi in by_feature.items():
                    _update_max_psi(out, str(feature), psi)
    return out


def _load_fold_rank_ic_by_feature(conn: Any, association_run_id: str | None) -> dict[str, dict[str, float]]:
    if not association_run_id or not _table_exists(conn, "mart_feature_association_fold"):
        return {}
    try:
        rows = conn.execute(
            """
            SELECT fold_id, feature_name, rank_ic
              FROM mart_feature_association_fold
             WHERE run_id = ?
               AND rank_ic IS NOT NULL
            """,
            (association_run_id,),
        ).fetchall()
    except Exception:
        return {}
    out: dict[str, dict[str, float]] = defaultdict(dict)
    for row in rows:
        value = _finite_float(row["rank_ic"])
        if value is not None:
            out[str(row["feature_name"])][str(row["fold_id"])] = value
    return dict(out)


def _feature_score(row: dict[str, Any], latest_psi: float | None, historical_psi: float | None) -> float:
    abs_rank_ic = _finite_float(row.get("abs_rank_ic"), 0.0) or 0.0
    coverage = (_finite_float(row.get("coverage_pct"), 0.0) or 0.0) / 100.0
    sign_stability = _finite_float(row.get("sign_stability"), 0.5) or 0.5
    fold_direction = _finite_float(row.get("fold_same_direction_rate"), sign_stability) or sign_stability
    spread = abs(_finite_float(row.get("long_short_spread"), 0.0) or 0.0)
    fold_std = _finite_float(row.get("fold_rank_ic_std"), 0.0) or 0.0
    return float(
        abs_rank_ic
        + 0.20 * spread
        + 0.03 * sign_stability
        + 0.02 * fold_direction
        + 0.01 * coverage
        - 0.30 * fold_std
        - 0.05 * (latest_psi or 0.0)
        - 0.05 * (historical_psi or 0.0)
    )


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _sample_std(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    avg = _mean(values)
    return math.sqrt(sum((value - avg) ** 2 for value in values) / (len(values) - 1))


def _subset_fold_metrics(
    rows: list[dict[str, Any]],
    fold_rank_ic_by_feature: dict[str, dict[str, float]],
) -> dict[str, float]:
    feature_names = [str(row["feature_name"]) for row in rows]
    folds = sorted(
        {
            fold_id
            for feature_name in feature_names
            for fold_id in fold_rank_ic_by_feature.get(feature_name, {})
        }
    )
    if not rows or not folds:
        return {
            "fold_count": 0.0,
            "fold_mean": 0.0,
            "fold_min": 0.0,
            "fold_std": 0.0,
            "fold_coverage": 0.0,
        }
    values_by_fold: dict[str, list[float]] = defaultdict(list)
    observations = 0
    for row in rows:
        feature_name = str(row["feature_name"])
        direction = int(_finite_float(row.get("rank_direction"), 1.0) or 1)
        if direction == 0:
            direction = 1
        for fold_id, rank_ic in fold_rank_ic_by_feature.get(feature_name, {}).items():
            values_by_fold[str(fold_id)].append(direction * rank_ic)
            observations += 1
    fold_values = [_mean(values_by_fold[fold_id]) for fold_id in folds if values_by_fold.get(fold_id)]
    if not fold_values:
        return {
            "fold_count": 0.0,
            "fold_mean": 0.0,
            "fold_min": 0.0,
            "fold_std": 0.0,
            "fold_coverage": 0.0,
        }
    return {
        "fold_count": float(len(fold_values)),
        "fold_mean": float(_mean(fold_values)),
        "fold_min": float(min(fold_values)),
        "fold_std": float(_sample_std(fold_values)),
        "fold_coverage": float(observations / max(len(rows) * len(folds), 1)),
    }


def _subset_fold_score(
    rows: list[dict[str, Any]],
    fold_rank_ic_by_feature: dict[str, dict[str, float]],
) -> float:
    metrics = _subset_fold_metrics(rows, fold_rank_ic_by_feature)
    if metrics["fold_count"] <= 0:
        return 0.0
    avg_feature_score = _mean([float(row.get("feature_score") or 0.0) for row in rows])
    return float(
        1.20 * metrics["fold_mean"]
        + 0.50 * metrics["fold_min"]
        + 0.05 * metrics["fold_coverage"]
        + 0.15 * avg_feature_score
        - 0.80 * metrics["fold_std"]
    )


def _eligible_pool(
    features: list[dict[str, Any]],
    *,
    latest_drift: dict[str, dict[str, Any]],
    historical_drift: dict[str, float],
    excluded_severities: set[str],
    min_abs_rank_ic: float,
    min_coverage_pct: float,
    min_sign_stability: float,
    max_latest_psi: float,
    max_historical_psi: float,
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    eligible: list[dict[str, Any]] = []
    excluded: dict[str, str] = {}
    for row in features:
        name = str(row["feature_name"])
        role = str(row.get("selection_role") or "")
        latest = latest_drift.get(name, {})
        latest_psi = _finite_float(latest.get("psi"))
        latest_severity = str(latest.get("severity") or "").lower()
        historical_psi = historical_drift.get(name)
        if latest_severity in excluded_severities:
            excluded[name] = f"latest_drift_severity:{latest_severity}"
            continue
        if latest_psi is not None and latest_psi > max_latest_psi:
            excluded[name] = f"latest_drift_psi:{latest_psi:.6f}>{max_latest_psi:.6f}"
            continue
        if historical_psi is not None and historical_psi > max_historical_psi:
            excluded[name] = f"historical_drift_psi:{historical_psi:.6f}>{max_historical_psi:.6f}"
            continue
        if role != "protected":
            abs_rank_ic = _finite_float(row.get("abs_rank_ic"), 0.0) or 0.0
            coverage = _finite_float(row.get("coverage_pct"), 0.0) or 0.0
            sign_stability = _finite_float(row.get("sign_stability"), 0.0) or 0.0
            if abs_rank_ic < min_abs_rank_ic:
                excluded[name] = f"low_abs_rank_ic:{abs_rank_ic:.6f}<{min_abs_rank_ic:.6f}"
                continue
            if coverage < min_coverage_pct:
                excluded[name] = f"low_coverage:{coverage:.2f}<{min_coverage_pct:.2f}"
                continue
            if sign_stability < min_sign_stability:
                excluded[name] = f"low_sign_stability:{sign_stability:.4f}<{min_sign_stability:.4f}"
                continue
        enriched = dict(row)
        enriched["latest_drift_psi"] = latest_psi
        enriched["latest_drift_severity"] = latest_severity or None
        enriched["historical_max_psi"] = historical_psi
        enriched["feature_score"] = _feature_score(row, latest_psi, historical_psi)
        eligible.append(enriched)
    return eligible, excluded


def _dedupe_features(features: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in features:
        name = str(row["feature_name"])
        if name in seen:
            continue
        seen.add(name)
        out.append(row)
    return out


def _ranked_pool(eligible: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    protected = sorted(
        [row for row in eligible if row.get("selection_role") == "protected"],
        key=lambda row: (float(row.get("feature_score") or 0.0), str(row["feature_name"])),
        reverse=True,
    )
    candidates = sorted(
        [row for row in eligible if row.get("selection_role") != "protected"],
        key=lambda row: (
            float(row.get("feature_score") or 0.0),
            float(row.get("abs_rank_ic") or 0.0),
            str(row["feature_name"]),
        ),
        reverse=True,
    )
    return protected, candidates


def _build_variants(
    eligible: list[dict[str, Any]],
    *,
    max_features: int,
    compact_features: int,
    per_group_limit: int,
    fold_rank_ic_by_feature: dict[str, dict[str, float]] | None = None,
) -> list[tuple[str, list[dict[str, Any]]]]:
    protected, candidates = _ranked_pool(eligible)
    variants: list[tuple[str, list[dict[str, Any]]]] = []
    top = _dedupe_features(protected + candidates)[:max_features]
    variants.append(("top", top))
    compact = _dedupe_features(protected + candidates)[: max(1, min(compact_features, max_features))]
    variants.append(("compact", compact))
    by_group: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in candidates:
        by_group[str(row.get("feature_group") or "unknown")].append(row)
    balanced = list(protected)
    group_counts: Counter[str] = Counter(str(row.get("feature_group") or "unknown") for row in balanced)
    while len(balanced) < max_features:
        added = False
        for group in sorted(by_group):
            if group_counts[group] >= per_group_limit:
                continue
            if not by_group[group]:
                continue
            balanced.append(by_group[group].pop(0))
            group_counts[group] += 1
            added = True
            if len(balanced) >= max_features:
                break
        if not added:
            break
    variants.append(("balanced", _dedupe_features(balanced)[:max_features]))
    if fold_rank_ic_by_feature:
        stable = list(protected)
        remaining = [row for row in candidates if row not in stable]
        while remaining and len(stable) < max_features:
            current_names = {str(row["feature_name"]) for row in stable}
            best_idx = -1
            best_score = -float("inf")
            for idx, row in enumerate(remaining):
                if str(row["feature_name"]) in current_names:
                    continue
                score = _subset_fold_score(stable + [row], fold_rank_ic_by_feature)
                if score > best_score:
                    best_idx = idx
                    best_score = score
            if best_idx < 0:
                break
            stable.append(remaining.pop(best_idx))
        variants.append(("fold_stable", _dedupe_features(stable)[:max_features]))
    out: list[tuple[str, list[dict[str, Any]]]] = []
    seen_sets: set[tuple[str, ...]] = set()
    for suffix, rows in variants:
        key = tuple(str(row["feature_name"]) for row in rows)
        if not key or key in seen_sets:
            continue
        seen_sets.add(key)
        out.append((suffix, rows))
    return out


def build_drift_safe_feature_candidates(
    conn: Any,
    *,
    search_space_run_id: str | None = None,
    run_id: str | None = None,
    drift_model_id: str | None = None,
    historical_run_ids: list[str] | None = None,
    max_features: int = 16,
    compact_features: int = 8,
    min_features: int = 4,
    per_group_limit: int = 4,
    min_abs_rank_ic: float = 0.02,
    min_coverage_pct: float = 60.0,
    min_sign_stability: float = 0.55,
    max_latest_psi: float = 0.25,
    max_historical_psi: float = 0.25,
    excluded_severities: set[str] | None = None,
) -> dict[str, Any]:
    ensure_tables(conn)
    started_at = utc_now_iso()
    t0 = time.perf_counter()
    run_id = run_id or f"drift_safe_candidates_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"
    search_space_run_id = search_space_run_id or latest_search_space_run_id(conn)
    if not search_space_run_id:
        raise RuntimeError("no feature search space run found")
    drift_model_id = drift_model_id or latest_champion_model_id(conn)
    excluded_severities = {item.lower() for item in (excluded_severities or DEFAULT_EXCLUDED_SEVERITIES)}
    features, summary = _load_search_space(conn, search_space_run_id)
    latest_drift = _load_latest_feature_drift(conn, drift_model_id)
    historical_drift = _load_historical_feature_drift(conn, run_ids=historical_run_ids)
    fold_rank_ic_by_feature = _load_fold_rank_ic_by_feature(conn, summary.get("source_association_run_id"))
    eligible, excluded = _eligible_pool(
        features,
        latest_drift=latest_drift,
        historical_drift=historical_drift,
        excluded_severities=excluded_severities,
        min_abs_rank_ic=min_abs_rank_ic,
        min_coverage_pct=min_coverage_pct,
        min_sign_stability=min_sign_stability,
        max_latest_psi=max_latest_psi,
        max_historical_psi=max_historical_psi,
    )
    variants = [
        (suffix, rows)
        for suffix, rows in _build_variants(
            eligible,
            max_features=max_features,
            compact_features=compact_features,
            per_group_limit=per_group_limit,
            fold_rank_ic_by_feature=fold_rank_ic_by_feature,
        )
        if len(rows) >= min_features
    ]
    built_at = datetime.utcnow().isoformat(timespec="seconds")
    conn.execute("DELETE FROM mart_drift_safe_candidate_feature WHERE run_id = ?", (run_id,))
    candidate_ids: list[str] = []
    selected_features_by_candidate: dict[str, list[str]] = {}
    model_rows: list[tuple[Any, ...]] = []
    feature_rows: list[tuple[Any, ...]] = []
    eligible_names = [str(row["feature_name"]) for row in eligible]
    for suffix, rows in variants:
        candidate_id = f"{run_id}_{suffix}"
        candidate_ids.append(candidate_id)
        selected = [str(row["feature_name"]) for row in rows]
        selected_features_by_candidate[candidate_id] = selected
        selected_set = set(selected)
        not_selected = [name for name in eligible_names if name not in selected_set]
        objective_score = sum(float(row.get("feature_score") or 0.0) for row in rows) / max(len(rows), 1)
        group_counts = Counter(str(row.get("feature_group") or "unknown") for row in rows)
        subset_fold_metrics = _subset_fold_metrics(rows, fold_rank_ic_by_feature)
        model_rows.append(
            (
                candidate_id,
                "production_registry",
                "drift_safe_candidate_generator",
                summary.get("label_name"),
                objective_score,
                json.dumps(selected, ensure_ascii=False),
                json.dumps(
                    {
                        "excluded": excluded,
                        "eligible_not_selected": not_selected,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                0,
                False,
                json.dumps(
                    {
                        "run_id": run_id,
                        "search_space_run_id": search_space_run_id,
                        "drift_model_id": drift_model_id,
                        "historical_run_ids": historical_run_ids or [],
                        "variant": suffix,
                        "group_counts": dict(group_counts),
                        "subset_fold_metrics": subset_fold_metrics,
                        "thresholds": {
                            "max_latest_psi": max_latest_psi,
                            "max_historical_psi": max_historical_psi,
                            "excluded_severities": sorted(excluded_severities),
                        },
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                built_at,
            )
        )
        for idx, row in enumerate(rows, start=1):
            feature_rows.append(
                (
                    run_id,
                    candidate_id,
                    row["feature_name"],
                    row.get("feature_group"),
                    idx,
                    row.get("feature_score"),
                    row.get("selection_role"),
                    row.get("rank_ic"),
                    row.get("abs_rank_ic"),
                    row.get("coverage_pct"),
                    row.get("sign_stability"),
                    row.get("fold_rank_ic_std"),
                    row.get("latest_drift_psi"),
                    row.get("latest_drift_severity"),
                    row.get("historical_max_psi"),
                    built_at,
                )
            )
    if model_rows:
        conn.executemany(
            """
            INSERT OR REPLACE INTO mart_model_selection_run
            (run_id, feature_set_id, method, label_name, objective_score,
             selected_features_json, rejected_features_json, trials,
             promote_to_champion, notes, built_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            model_rows,
        )
    if feature_rows:
        conn.executemany(
            """
            INSERT INTO mart_drift_safe_candidate_feature
            (run_id, candidate_id, feature_name, feature_group, feature_rank,
             feature_score, selection_role, rank_ic, abs_rank_ic, coverage_pct,
             sign_stability, fold_rank_ic_std, latest_drift_psi,
             latest_drift_severity, historical_max_psi, built_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            feature_rows,
        )
    config = {
        "max_features": max_features,
        "compact_features": compact_features,
        "min_features": min_features,
        "per_group_limit": per_group_limit,
        "min_abs_rank_ic": min_abs_rank_ic,
        "min_coverage_pct": min_coverage_pct,
        "min_sign_stability": min_sign_stability,
        "max_latest_psi": max_latest_psi,
        "max_historical_psi": max_historical_psi,
        "excluded_severities": sorted(excluded_severities),
        "fold_stable_variant": bool(fold_rank_ic_by_feature),
    }
    conn.execute(
        """
        INSERT OR REPLACE INTO mart_drift_safe_candidate_summary
        (run_id, source_search_space_run_id, drift_model_id,
         historical_run_ids_json, generated_count, candidate_ids_json,
         eligible_features_json, excluded_features_json, config_json, built_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_id,
            search_space_run_id,
            drift_model_id,
            json.dumps(historical_run_ids or [], ensure_ascii=False),
            len(candidate_ids),
            json.dumps(candidate_ids, ensure_ascii=False),
            json.dumps(eligible_names, ensure_ascii=False),
            json.dumps(excluded, ensure_ascii=False, sort_keys=True),
            json.dumps(config, ensure_ascii=False, sort_keys=True),
            built_at,
        ),
    )
    record_actual_version(conn, "mart_drift_safe_candidate_feature")
    record_actual_version(conn, "mart_drift_safe_candidate_summary")
    record_actual_version(conn, "mart_model_selection_run")
    record_pipeline_run(
        conn,
        run_id=run_id,
        pipeline_name="build_drift_safe_feature_candidates",
        status="success",
        started_at=started_at,
        ended_at=utc_now_iso(),
        duration_s=time.perf_counter() - t0,
        commit_sha=git_commit_sha(Path(__file__).resolve().parent.parent.parent),
        input_tables=[
            "mart_feature_search_space",
            "mart_feature_search_space_summary",
            "mart_feature_drift",
            "mart_model_stability_search_trial",
        ],
        output_tables=[
            "mart_drift_safe_candidate_feature",
            "mart_drift_safe_candidate_summary",
            "mart_model_selection_run",
        ],
        label_name=summary.get("label_name"),
        perf_summary={
            "search_space_run_id": search_space_run_id,
            "drift_model_id": drift_model_id,
            "historical_run_ids": historical_run_ids or [],
            "input_features": len(features),
            "eligible_features": len(eligible),
            "excluded_features": len(excluded),
            "generated_count": len(candidate_ids),
            "candidate_ids": candidate_ids,
            "fold_rank_ic_features": len(fold_rank_ic_by_feature),
        },
    )
    conn.commit()
    return {
        "run_id": run_id,
        "search_space_run_id": search_space_run_id,
        "drift_model_id": drift_model_id,
        "historical_run_ids": historical_run_ids or [],
        "eligible_count": len(eligible),
        "excluded_count": len(excluded),
        "generated_count": len(candidate_ids),
        "candidate_ids": candidate_ids,
        "selected_features_by_candidate": selected_features_by_candidate,
        "excluded_features": excluded,
        "fold_rank_ic_features": len(fold_rank_ic_by_feature),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--search-space-run-id", default=None)
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--drift-model-id", default=None)
    parser.add_argument("--historical-run-id", action="append", default=[])
    parser.add_argument("--max-features", type=int, default=16)
    parser.add_argument("--compact-features", type=int, default=8)
    parser.add_argument("--min-features", type=int, default=4)
    parser.add_argument("--per-group-limit", type=int, default=4)
    parser.add_argument("--min-abs-rank-ic", type=float, default=0.02)
    parser.add_argument("--min-coverage-pct", type=float, default=60.0)
    parser.add_argument("--min-sign-stability", type=float, default=0.55)
    parser.add_argument("--max-latest-psi", type=float, default=0.25)
    parser.add_argument("--max-historical-psi", type=float, default=0.25)
    parser.add_argument(
        "--exclude-severities",
        default="critical,unknown",
        help="Comma-separated latest drift severities to exclude.",
    )
    args = parser.parse_args()
    with get_conn() as conn:
        result = build_drift_safe_feature_candidates(
            conn,
            search_space_run_id=args.search_space_run_id,
            run_id=args.run_id,
            drift_model_id=args.drift_model_id,
            historical_run_ids=args.historical_run_id,
            max_features=args.max_features,
            compact_features=args.compact_features,
            min_features=args.min_features,
            per_group_limit=args.per_group_limit,
            min_abs_rank_ic=args.min_abs_rank_ic,
            min_coverage_pct=args.min_coverage_pct,
            min_sign_stability=args.min_sign_stability,
            max_latest_psi=args.max_latest_psi,
            max_historical_psi=args.max_historical_psi,
            excluded_severities=set(_parse_csv(args.exclude_severities)),
        )
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
