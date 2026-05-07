#!/usr/bin/env python3
"""Optuna search over the registry feature search space.

This runner is intentionally lightweight: it does not train a model. It uses
stored association/fold/cluster evidence to search feature subset composition
and objective weights, then persists every trial for reproducibility. The
resulting selected feature set is a candidate input for later model training.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import optuna

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.db import get_conn  # noqa: E402
from services.pipeline_manifest import git_commit_sha, record_pipeline_run, utc_now_iso  # noqa: E402
from services.schema_versions import record_actual_version  # noqa: E402

optuna.logging.set_verbosity(optuna.logging.WARNING)


DEFAULT_WEIGHTS = {
    "rank_ic": 1.0,
    "spread": 0.2,
    "sign": 0.2,
    "fold_direction": 0.3,
    "coverage": 0.05,
    "fold_std_penalty": 0.2,
    "subset_fold_mean": 0.5,
    "subset_fold_min": 0.2,
    "subset_fold_std_penalty": 0.5,
    "subset_fold_coverage": 0.05,
}

DDL = """
CREATE TABLE IF NOT EXISTS mart_optuna_feature_space_trial (
    run_id TEXT NOT NULL,
    search_space_run_id TEXT NOT NULL,
    trial_number INTEGER NOT NULL,
    objective_value DOUBLE,
    selected_count INTEGER,
    selected_features_json TEXT,
    params_json TEXT,
    built_at TEXT NOT NULL,
    PRIMARY KEY (run_id, trial_number)
);
CREATE INDEX IF NOT EXISTS idx_optuna_feature_space_run
    ON mart_optuna_feature_space_trial(run_id);

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


def default_optuna_storage_url() -> str:
    storage_dir = Path(__file__).resolve().parent.parent.parent / "data" / "optuna"
    storage_dir.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{storage_dir / 'feature_space_studies.sqlite3'}"


def _finite_float(value: Any, default: float = 0.0) -> float:
    if value is None:
        return default
    value = float(value)
    return value if math.isfinite(value) else default


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _sample_std(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    avg = _mean(values)
    return math.sqrt(sum((value - avg) ** 2 for value in values) / (len(values) - 1))


def _normalise_weights(weights: dict[str, float] | None) -> dict[str, float]:
    normalised = dict(DEFAULT_WEIGHTS)
    if weights:
        normalised.update({key: _finite_float(value) for key, value in weights.items()})
    return normalised


def _safe_json(raw: Any, default: Any) -> Any:
    if not raw:
        return default
    try:
        return json.loads(raw)
    except Exception:
        return default


def latest_search_space_run_id(conn: Any) -> str | None:
    row = conn.execute(
        """
        SELECT run_id
          FROM mart_feature_search_space_summary
         ORDER BY built_at DESC NULLS LAST, run_id DESC
         LIMIT 1
        """
    ).fetchone()
    return str(row["run_id"]) if row else None


def _load_search_space(conn: Any, search_space_run_id: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT feature_name, feature_group, rank_ic, abs_rank_ic, rank_direction,
               coverage_pct, fold_count, sign_stability, fold_valid_count,
               fold_same_direction_rate, fold_rank_ic_std, long_short_spread,
               selection_role, selection_reason
          FROM mart_feature_search_space
         WHERE run_id = ?
           AND selection_role IN ('candidate', 'protected')
         ORDER BY abs_rank_ic DESC NULLS LAST, feature_name
        """,
        (search_space_run_id,),
    ).fetchall()
    summary = conn.execute(
        """
        SELECT run_id, source_association_run_id, panel_table, label_name,
               selected_features_json, group_counts_json, config_json
          FROM mart_feature_search_space_summary
         WHERE run_id = ?
        """,
        (search_space_run_id,),
    ).fetchone()
    if not rows:
        raise RuntimeError(f"search space has no selected features: {search_space_run_id}")
    if not summary:
        raise RuntimeError(f"missing search space summary: {search_space_run_id}")
    summary_dict = dict(summary)
    summary_dict["config"] = _safe_json(summary_dict.get("config_json"), {})
    return [dict(row) for row in rows], summary_dict


def _load_fold_rank_ic_by_feature(conn: Any, association_run_id: str | None) -> dict[str, dict[str, float]]:
    if not association_run_id:
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
    by_feature: dict[str, dict[str, float]] = defaultdict(dict)
    for row in rows:
        rank_ic = _finite_float(row["rank_ic"])
        by_feature[str(row["feature_name"])][str(row["fold_id"])] = rank_ic
    return by_feature


def _load_rank_matrix_proxy_features(
    conn: Any,
    *,
    rank_matrix_run_id: str,
    label_name: str,
    required_features: set[str],
    require_gate_pass: bool = True,
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    benchmark = conn.execute(
        """
        SELECT run_id, gate_status, gate_blockers_json, gate_config_json,
               compared_pairs, max_abs_rank_ic_delta, avg_abs_rank_ic_delta,
               matrix_duration_s, exact_run_id
          FROM mart_feature_rank_matrix_benchmark
         WHERE run_id = ?
        """,
        (rank_matrix_run_id,),
    ).fetchone()
    if not benchmark:
        raise RuntimeError(f"missing rank-matrix benchmark run: {rank_matrix_run_id}")
    gate_status = str(benchmark["gate_status"] or "")
    gate_blockers = _safe_json(benchmark["gate_blockers_json"], [])
    if require_gate_pass and gate_status != "pass":
        raise RuntimeError(
            f"rank-matrix proxy run {rank_matrix_run_id} is not gate-pass: "
            f"{gate_status or 'unknown'} blockers={gate_blockers}"
        )
    rows = conn.execute(
        """
        SELECT feature_name, rank_ic, long_short_spread, exact_rank_ic,
               abs_rank_ic_delta, daily_count, valid_rank_rows
          FROM mart_feature_rank_matrix_proxy_stat
         WHERE run_id = ?
           AND label_name = ?
        """,
        (rank_matrix_run_id, label_name),
    ).fetchall()
    by_feature = {str(row["feature_name"]): dict(row) for row in rows}
    missing = sorted(required_features - set(by_feature))
    if missing:
        raise RuntimeError(
            f"rank-matrix proxy run {rank_matrix_run_id} missing {len(missing)} search-space features: "
            f"{missing[:10]}"
        )
    summary = dict(benchmark)
    summary["gate_blockers"] = gate_blockers
    summary["gate_config"] = _safe_json(summary.get("gate_config_json"), {})
    return by_feature, summary


def _apply_rank_matrix_proxy(
    features: list[dict[str, Any]],
    proxy_by_feature: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    updated = []
    for row in features:
        feature_name = str(row["feature_name"])
        proxy = proxy_by_feature[feature_name]
        proxy_rank_ic = _finite_float(proxy.get("rank_ic"), default=_finite_float(row.get("rank_ic")))
        merged = dict(row)
        merged["rank_ic"] = proxy_rank_ic
        merged["abs_rank_ic"] = abs(proxy_rank_ic)
        merged["rank_direction"] = -1 if proxy_rank_ic < 0 else 1
        if proxy.get("long_short_spread") is not None:
            merged["long_short_spread"] = _finite_float(proxy.get("long_short_spread"))
        merged["matrix_proxy_rank_ic"] = proxy_rank_ic
        merged["matrix_proxy_exact_rank_ic"] = proxy.get("exact_rank_ic")
        merged["matrix_proxy_abs_rank_ic_delta"] = proxy.get("abs_rank_ic_delta")
        merged["matrix_proxy_daily_count"] = proxy.get("daily_count")
        merged["matrix_proxy_valid_rank_rows"] = proxy.get("valid_rank_rows")
        updated.append(merged)
    return updated


def _feature_score(feature: dict[str, Any], weights: dict[str, float]) -> float:
    weights = _normalise_weights(weights)
    abs_rank_ic = _finite_float(feature.get("abs_rank_ic"))
    spread = abs(_finite_float(feature.get("long_short_spread")))
    sign_stability = _finite_float(feature.get("sign_stability"), 0.5)
    fold_direction = _finite_float(feature.get("fold_same_direction_rate"), sign_stability)
    fold_std = _finite_float(feature.get("fold_rank_ic_std"))
    coverage = _finite_float(feature.get("coverage_pct")) / 100.0
    return (
        weights["rank_ic"] * abs_rank_ic
        + weights["spread"] * spread
        + weights["sign"] * sign_stability
        + weights["fold_direction"] * fold_direction
        + weights["coverage"] * coverage
        - weights["fold_std_penalty"] * fold_std
    )


def _subset_fold_metrics(
    selected: list[dict[str, Any]],
    fold_rank_ic_by_feature: dict[str, dict[str, float]],
) -> dict[str, float]:
    selected_names = [str(row["feature_name"]) for row in selected]
    all_folds = sorted(
        {
            fold_id
            for feature_name in selected_names
            for fold_id in fold_rank_ic_by_feature.get(feature_name, {})
        }
    )
    if not selected or not all_folds:
        return {
            "subset_fold_count": 0,
            "subset_fold_mean": 0.0,
            "subset_fold_std": 0.0,
            "subset_fold_min": 0.0,
            "subset_fold_observation_coverage": 0.0,
        }
    values_by_fold: dict[str, list[float]] = defaultdict(list)
    observation_count = 0
    for row in selected:
        feature_name = str(row["feature_name"])
        direction = int(_finite_float(row.get("rank_direction"), 1.0) or 1)
        if direction == 0:
            direction = 1
        for fold_id, rank_ic in fold_rank_ic_by_feature.get(feature_name, {}).items():
            values_by_fold[str(fold_id)].append(direction * rank_ic)
            observation_count += 1
    fold_means = [_mean(values_by_fold[fold_id]) for fold_id in all_folds if values_by_fold.get(fold_id)]
    if not fold_means:
        return {
            "subset_fold_count": 0,
            "subset_fold_mean": 0.0,
            "subset_fold_std": 0.0,
            "subset_fold_min": 0.0,
            "subset_fold_observation_coverage": 0.0,
        }
    possible_observations = max(len(selected) * len(all_folds), 1)
    return {
        "subset_fold_count": float(len(fold_means)),
        "subset_fold_mean": float(_mean(fold_means)),
        "subset_fold_std": float(_sample_std(fold_means)),
        "subset_fold_min": float(min(fold_means)),
        "subset_fold_observation_coverage": float(observation_count / possible_observations),
    }


def _subset_fold_score(metrics: dict[str, float], weights: dict[str, float]) -> float:
    if int(metrics.get("subset_fold_count", 0)) <= 0:
        return 0.0
    weights = _normalise_weights(weights)
    return (
        weights["subset_fold_mean"] * metrics["subset_fold_mean"]
        + weights["subset_fold_min"] * metrics["subset_fold_min"]
        + weights["subset_fold_coverage"] * metrics["subset_fold_observation_coverage"]
        - weights["subset_fold_std_penalty"] * metrics["subset_fold_std"]
    )


def _select_features(
    features: list[dict[str, Any]],
    *,
    group_take: dict[str, int],
    weights: dict[str, float],
    max_features: int,
) -> list[dict[str, Any]]:
    protected = [row for row in features if row["selection_role"] == "protected"]
    by_group: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in features:
        if row["selection_role"] != "protected":
            by_group[str(row.get("feature_group") or "unknown")].append(row)
    selected = list(protected)
    for group, rows in by_group.items():
        take = group_take.get(group, 0)
        ranked = sorted(
            rows,
            key=lambda row: (_feature_score(row, weights), _finite_float(row.get("abs_rank_ic")), row["feature_name"]),
            reverse=True,
        )
        selected.extend(ranked[:take])
    protected_by_name = {row["feature_name"]: row for row in protected}
    candidates = sorted(
        {
            row["feature_name"]: row
            for row in selected
            if row["feature_name"] not in protected_by_name
        }.values(),
        key=lambda row: (_feature_score(row, weights), _finite_float(row.get("abs_rank_ic")), row["feature_name"]),
        reverse=True,
    )
    protected_sorted = sorted(
        protected_by_name.values(),
        key=lambda row: (_feature_score(row, weights), _finite_float(row.get("abs_rank_ic")), row["feature_name"]),
        reverse=True,
    )
    if len(protected_sorted) >= max_features:
        return protected_sorted[:max_features]
    return protected_sorted + candidates[: max_features - len(protected_sorted)]


def _trial_params(trial: optuna.Trial, groups: list[str], group_sizes: dict[str, int]) -> tuple[dict[str, int], dict[str, float], float]:
    group_take = {
        group: trial.suggest_int(f"take_{group}", 0, group_sizes[group])
        for group in groups
    }
    weights = {
        "rank_ic": trial.suggest_float("w_rank_ic", 0.4, 2.0),
        "spread": trial.suggest_float("w_spread", 0.0, 1.0),
        "sign": trial.suggest_float("w_sign", 0.0, 0.6),
        "fold_direction": trial.suggest_float("w_fold_direction", 0.0, 0.8),
        "coverage": trial.suggest_float("w_coverage", 0.0, 0.2),
        "fold_std_penalty": trial.suggest_float("w_fold_std_penalty", 0.0, 1.0),
        "subset_fold_mean": trial.suggest_float("w_subset_fold_mean", 0.0, 2.0),
        "subset_fold_min": trial.suggest_float("w_subset_fold_min", 0.0, 1.0),
        "subset_fold_std_penalty": trial.suggest_float("w_subset_fold_std_penalty", 0.0, 2.0),
        "subset_fold_coverage": trial.suggest_float("w_subset_fold_coverage", 0.0, 0.2),
    }
    complexity_penalty = trial.suggest_float("complexity_penalty", 0.0, 0.04)
    return group_take, weights, complexity_penalty


def run_optuna_feature_space(
    conn: Any,
    *,
    search_space_run_id: str | None = None,
    run_id: str | None = None,
    trials: int = 32,
    max_features: int = 20,
    seed: int = 42,
    storage_url: str | None = None,
    study_name: str | None = None,
    load_if_exists: bool = True,
    rank_matrix_run_id: str | None = None,
    require_rank_matrix_gate: bool = True,
) -> dict[str, Any]:
    ensure_tables(conn)
    search_space_run_id = search_space_run_id or latest_search_space_run_id(conn)
    if not search_space_run_id:
        raise RuntimeError("no feature search space run found")
    features, summary = _load_search_space(conn, search_space_run_id)
    rank_matrix_summary: dict[str, Any] | None = None
    if rank_matrix_run_id:
        proxy_by_feature, rank_matrix_summary = _load_rank_matrix_proxy_features(
            conn,
            rank_matrix_run_id=rank_matrix_run_id,
            label_name=str(summary.get("label_name")),
            required_features={str(row["feature_name"]) for row in features},
            require_gate_pass=require_rank_matrix_gate,
        )
        features = _apply_rank_matrix_proxy(features, proxy_by_feature)
    fold_rank_ic_by_feature = _load_fold_rank_ic_by_feature(conn, summary.get("source_association_run_id"))
    groups = sorted({str(row.get("feature_group") or "unknown") for row in features if row["selection_role"] != "protected"})
    group_sizes = Counter(str(row.get("feature_group") or "unknown") for row in features if row["selection_role"] != "protected")
    max_features = max(1, min(int(max_features), len(features)))
    run_id = run_id or f"optuna_feature_space_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}"
    study_name = study_name or run_id
    built_at = datetime.now(UTC).replace(tzinfo=None).isoformat(timespec="seconds")
    trial_rows: list[tuple] = []

    def evaluate(
        group_take: dict[str, int],
        weights: dict[str, float],
        complexity_penalty: float,
    ) -> tuple[float, list[dict[str, Any]], dict[str, float]]:
        weights = _normalise_weights(weights)
        selected = _select_features(
            features,
            group_take=group_take,
            weights=weights,
            max_features=max_features,
        )
        if not selected:
            return -1.0, [], _subset_fold_metrics([], fold_rank_ic_by_feature)
        raw_score = sum(_feature_score(row, weights) for row in selected) / len(selected)
        subset_metrics = _subset_fold_metrics(selected, fold_rank_ic_by_feature)
        diversity_bonus = len({row.get("feature_group") for row in selected}) * 0.005
        complexity = complexity_penalty * (len(selected) / max_features)
        value = raw_score + _subset_fold_score(subset_metrics, weights) + diversity_bonus - complexity
        return float(value), selected, subset_metrics

    if trials <= 0:
        group_take = {group: min(group_sizes[group], max(1, max_features // max(len(groups), 1))) for group in groups}
        weights = dict(DEFAULT_WEIGHTS)
        value, selected, subset_metrics = evaluate(group_take, weights, 0.01)
        trial_rows.append(
            (
                run_id,
                search_space_run_id,
                0,
                value,
                len(selected),
                json.dumps([row["feature_name"] for row in selected], ensure_ascii=False),
                json.dumps(
                    {
                        "group_take": group_take,
                        "weights": weights,
                        "complexity_penalty": 0.01,
                        "subset_fold_metrics": subset_metrics,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                built_at,
            )
        )
        best_value = value
        best_selected = selected
        best_subset_metrics = subset_metrics
        best_params = {
            "group_take": group_take,
            "weights": weights,
            "complexity_penalty": 0.01,
            "subset_fold_metrics": subset_metrics,
        }
    else:
        sampler = optuna.samplers.TPESampler(seed=seed)
        study = optuna.create_study(
            direction="maximize",
            sampler=sampler,
            storage=storage_url,
            study_name=study_name if storage_url else None,
            load_if_exists=load_if_exists if storage_url else False,
        )

        def objective(trial: optuna.Trial) -> float:
            group_take, weights, complexity_penalty = _trial_params(trial, groups, dict(group_sizes))
            value, selected, subset_metrics = evaluate(group_take, weights, complexity_penalty)
            trial.set_user_attr("group_take", group_take)
            trial.set_user_attr("weights", weights)
            trial.set_user_attr("complexity_penalty", complexity_penalty)
            trial.set_user_attr("subset_fold_metrics", subset_metrics)
            trial_rows.append(
                (
                    run_id,
                    search_space_run_id,
                    trial.number,
                    value,
                    len(selected),
                    json.dumps([row["feature_name"] for row in selected], ensure_ascii=False),
                    json.dumps(
                        {
                            "group_take": group_take,
                            "weights": weights,
                            "complexity_penalty": complexity_penalty,
                            "subset_fold_metrics": subset_metrics,
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                    built_at,
                )
            )
            return value

        study.optimize(objective, n_trials=trials, show_progress_bar=False)
        current_best: tuple[float, int, list[dict[str, Any]], dict[str, Any], dict[str, float]] | None = None
        for frozen_trial in study.trials:
            if frozen_trial.state != optuna.trial.TrialState.COMPLETE:
                continue
            group_take = dict(frozen_trial.user_attrs.get("group_take") or {})
            weights = _normalise_weights(dict(frozen_trial.user_attrs.get("weights") or {}))
            if not group_take:
                continue
            complexity_penalty = float(frozen_trial.user_attrs.get("complexity_penalty") or 0.0)
            value, selected, subset_metrics = evaluate(group_take, weights, complexity_penalty)
            if current_best is None or value > current_best[0]:
                current_best = (
                    value,
                    int(frozen_trial.number),
                    selected,
                    {
                        "group_take": group_take,
                        "weights": weights,
                        "complexity_penalty": complexity_penalty,
                        "optuna_params": dict(frozen_trial.params),
                        "study_name": study.study_name,
                        "study_total_trials": len(study.trials),
                        "best_trial_number": int(frozen_trial.number),
                        "objective_recomputed_with_current_version": True,
                    },
                    subset_metrics,
                )
        if current_best is None:
            group_take = dict(study.best_trial.user_attrs.get("group_take") or {})
            weights = _normalise_weights(dict(study.best_trial.user_attrs.get("weights") or {}))
            complexity_penalty = float(study.best_trial.user_attrs.get("complexity_penalty") or 0.0)
            best_value, best_selected, best_subset_metrics = evaluate(group_take, weights, complexity_penalty)
            best_params = {
                "group_take": group_take,
                "weights": weights,
                "complexity_penalty": complexity_penalty,
                "optuna_params": dict(study.best_trial.params),
                "study_name": study.study_name,
                "study_total_trials": len(study.trials),
                "best_trial_number": int(study.best_trial.number),
                "objective_recomputed_with_current_version": True,
            }
        else:
            best_value, _, best_selected, best_params, best_subset_metrics = current_best
        best_params = {
            **best_params,
            "subset_fold_metrics": best_subset_metrics,
        }
        study_total_trials = len(study.trials)

    if trials <= 0:
        study_total_trials = len(trial_rows)

    if trial_rows:
        conn.executemany(
            """
            INSERT OR REPLACE INTO mart_optuna_feature_space_trial
            (run_id, search_space_run_id, trial_number, objective_value,
             selected_count, selected_features_json, params_json, built_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            trial_rows,
        )
    selected_features = [str(row["feature_name"]) for row in best_selected]
    rejected = [
        str(row["feature_name"])
        for row in features
        if row["feature_name"] not in set(selected_features)
    ]
    group_counts = Counter(str(row.get("feature_group") or "unknown") for row in best_selected)
    feature_set_id = str((summary.get("config") or {}).get("feature_set_id") or "production_registry")
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
            "optuna_feature_space_rank_matrix_proxy" if rank_matrix_run_id else "optuna_feature_space_proxy",
            summary.get("label_name"),
            best_value,
            json.dumps(selected_features, ensure_ascii=False),
            json.dumps(rejected, ensure_ascii=False),
            int(max(trials, 0)),
            False,
            json.dumps(
                {
                    "search_space_run_id": search_space_run_id,
                    "feature_set_id": feature_set_id,
                    "source_association_run_id": summary.get("source_association_run_id"),
                    "best_params": best_params,
                    "group_counts": dict(group_counts),
                    "storage_url": storage_url,
                    "study_name": study_name if storage_url else None,
                    "study_total_trials": study_total_trials,
                    "best_subset_fold_metrics": best_subset_metrics,
                    "rank_matrix_run_id": rank_matrix_run_id,
                    "rank_matrix_summary": rank_matrix_summary,
                    "message": "Optuna proxy feature-space search; no model trained or promoted",
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            built_at,
        ),
    )
    record_actual_version(conn, "mart_optuna_feature_space_trial")
    record_actual_version(conn, "mart_model_selection_run")
    record_pipeline_run(
        conn,
        run_id=run_id,
        pipeline_name="run_optuna_feature_space",
        status="success",
        started_at=built_at,
        ended_at=utc_now_iso(),
        commit_sha=git_commit_sha(Path(__file__).resolve().parent.parent.parent),
        input_tables=[
            "mart_feature_search_space",
            "mart_feature_search_space_summary",
            *([] if not fold_rank_ic_by_feature else ["mart_feature_association_fold"]),
            *([] if not rank_matrix_run_id else ["mart_feature_rank_matrix_proxy_stat", "mart_feature_rank_matrix_benchmark"]),
        ],
        output_tables=["mart_optuna_feature_space_trial", "mart_model_selection_run"],
        label_name=summary.get("label_name"),
        perf_summary={
            "search_space_run_id": search_space_run_id,
            "feature_set_id": feature_set_id,
            "trials": int(max(trials, 0)),
            "max_features": max_features,
            "selected_count": len(selected_features),
            "group_counts": dict(group_counts),
            "best_value": best_value,
            "storage_url": storage_url,
            "study_name": study_name if storage_url else None,
            "study_total_trials": study_total_trials,
            "best_subset_fold_metrics": best_subset_metrics,
            "rank_matrix_run_id": rank_matrix_run_id,
            "rank_matrix_gate_status": (rank_matrix_summary or {}).get("gate_status"),
            "rank_matrix_compared_pairs": (rank_matrix_summary or {}).get("compared_pairs"),
            "rank_matrix_max_abs_rank_ic_delta": (rank_matrix_summary or {}).get("max_abs_rank_ic_delta"),
            "rank_matrix_avg_abs_rank_ic_delta": (rank_matrix_summary or {}).get("avg_abs_rank_ic_delta"),
        },
    )
    conn.commit()
    return {
        "run_id": run_id,
        "search_space_run_id": search_space_run_id,
        "feature_set_id": feature_set_id,
        "trials": int(max(trials, 0)),
        "best_value": best_value,
        "selected_count": len(selected_features),
        "selected_features": selected_features,
        "group_counts": dict(group_counts),
        "storage_url": storage_url,
        "study_name": study_name if storage_url else None,
        "study_total_trials": study_total_trials,
        "best_subset_fold_metrics": best_subset_metrics,
        "rank_matrix_run_id": rank_matrix_run_id,
        "rank_matrix_gate_status": (rank_matrix_summary or {}).get("gate_status"),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--search-space-run-id", default=None)
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--trials", type=int, default=32)
    parser.add_argument("--max-features", type=int, default=20)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--storage",
        default=None,
        help="Optuna storage URL. Default: data/optuna/feature_space_studies.sqlite3",
    )
    parser.add_argument("--study-name", default=None)
    parser.add_argument("--no-persistent-study", action="store_true")
    parser.add_argument("--no-resume", action="store_true")
    parser.add_argument("--rank-matrix-run-id", default=None)
    parser.add_argument("--allow-rank-matrix-gate-fail", action="store_true")
    args = parser.parse_args()

    with get_conn() as conn:
        storage_url = None if args.no_persistent_study else (args.storage or default_optuna_storage_url())
        result = run_optuna_feature_space(
            conn,
            search_space_run_id=args.search_space_run_id,
            run_id=args.run_id,
            trials=args.trials,
            max_features=args.max_features,
            seed=args.seed,
            storage_url=storage_url,
            study_name=args.study_name,
            load_if_exists=not args.no_resume,
            rank_matrix_run_id=args.rank_matrix_run_id,
            require_rank_matrix_gate=not args.allow_rank_matrix_gate_fail,
        )
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
