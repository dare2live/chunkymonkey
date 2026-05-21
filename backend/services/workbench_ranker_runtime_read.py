"""Ranker runtime read model for the Workbench research surface."""
from __future__ import annotations

from typing import Any
import json


def _relation_exists(conn: Any, relation: str) -> bool:
    try:
        conn.execute(f"SELECT 1 FROM {relation} LIMIT 0").fetchone()
        return True
    except Exception:
        return False


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
    return row is not None and _relation_exists(conn, table_name)


def _safe_json(value: str | None) -> Any:
    if not value:
        return None
    try:
        return json.loads(value)
    except Exception:
        return None


def _float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _timing_seconds(timing: Any, key: str) -> float | None:
    if not isinstance(timing, dict):
        return None
    seconds = timing.get("seconds") if isinstance(timing.get("seconds"), dict) else {}
    for probe in (f"{key}_s", key):
        value = _float_or_none(seconds.get(probe))
        if value is not None:
            return value
    for probe in (f"{key}_s", key):
        value = _float_or_none(timing.get(probe))
        if value is not None:
            return value
    return None


def _runtime_profile_summary(
    perf: dict[str, Any],
    *,
    duration_s: Any,
    regression_per_trial_s: float | None = None,
) -> dict[str, Any]:
    trials = int(perf.get("trials") or perf.get("study_total_trials") or 0)
    duration = _float_or_none(duration_s)
    if duration is None:
        duration = _float_or_none(perf.get("duration_s"))
    duration_per_trial = duration / trials if duration is not None and trials > 0 else None
    timing = perf.get("timing") if isinstance(perf.get("timing"), dict) else {}
    train_s = _timing_seconds(timing, "train")
    cache = perf.get("ranker_cache") if isinstance(perf.get("ranker_cache"), dict) else {}
    cache_hits = int(cache.get("hits") or 0)
    cache_misses = int(cache.get("misses") or 0)
    cache_total = cache_hits + cache_misses
    eval_cache = perf.get("evaluation_cache") if isinstance(perf.get("evaluation_cache"), dict) else {}
    eval_hits = eval_cache.get("hits") if isinstance(eval_cache.get("hits"), dict) else {}
    eval_misses = eval_cache.get("misses") if isinstance(eval_cache.get("misses"), dict) else {}

    def eval_cache_rate(key: str | None = None) -> float | None:
        if key is None:
            hits = sum(int(value or 0) for value in eval_hits.values())
            misses = sum(int(value or 0) for value in eval_misses.values())
        else:
            hits = int(eval_hits.get(key) or 0)
            misses = int(eval_misses.get(key) or 0)
        total = hits + misses
        return hits / total if total > 0 else None

    return {
        "trials": trials,
        "duration_per_trial_s": duration_per_trial,
        "train_time_pct": train_s / duration if train_s is not None and duration and duration > 0 else None,
        "cache_hit_rate": cache_hits / cache_total if cache_total > 0 else None,
        "eval_cache_hit_rate": eval_cache_rate(),
        "matrix_cache_hit_rate": eval_cache_rate("matrix"),
        "feature_drift_cache_hit_rate": eval_cache_rate("feature_drift"),
        "runtime_ratio_vs_regression": (
            duration_per_trial / regression_per_trial_s
            if duration_per_trial is not None and regression_per_trial_s and regression_per_trial_s > 0
            else None
        ),
    }


def build_ranker_runtime_view(conn: Any, *, schedule_run_id: str | None = None) -> dict[str, Any]:
    ranker_profiles = []
    ranker_policy = {"run_id": None, "ranker_policy_deferred": 0, "policy": {}}
    if not _table_exists(conn, "mart_pipeline_run_manifest"):
        return {"ranker_policy": ranker_policy, "ranker_profiles": ranker_profiles}

    policy_row = None
    if schedule_run_id:
        policy_row = conn.execute(
            """
            SELECT run_id, perf_summary_json,
                   CAST(started_at AS VARCHAR) AS started_at
              FROM mart_pipeline_run_manifest
             WHERE pipeline_name = 'plan_research_schedule'
               AND run_id = ?
             LIMIT 1
            """,
            (schedule_run_id,),
        ).fetchone()
    if not policy_row:
        policy_row = conn.execute(
            """
            SELECT run_id, perf_summary_json,
                   CAST(started_at AS VARCHAR) AS started_at
              FROM mart_pipeline_run_manifest
             WHERE pipeline_name = 'plan_research_schedule'
             ORDER BY started_at DESC
             LIMIT 1
            """
        ).fetchone()
    if policy_row:
        policy_perf = _safe_json(policy_row["perf_summary_json"]) or {}
        if isinstance(policy_perf, dict):
            ranker_policy = {
                "run_id": policy_row["run_id"],
                "started_at": policy_row["started_at"],
                "ranker_policy_deferred": int(policy_perf.get("ranker_policy_deferred") or 0),
                "policy": policy_perf.get("ranker_policy") if isinstance(policy_perf.get("ranker_policy"), dict) else {},
            }

    rows = conn.execute(
        """
        SELECT run_id, duration_s, perf_summary_json,
               CAST(started_at AS VARCHAR) AS started_at
          FROM mart_pipeline_run_manifest
         WHERE pipeline_name = 'run_optuna_model_stability_search'
         ORDER BY started_at DESC
        LIMIT 12
        """
    ).fetchall()
    parsed_rows = []
    regression_per_trial_s = None
    for row in rows:
        perf = _safe_json(row["perf_summary_json"]) or {}
        if not isinstance(perf, dict):
            perf = {}
        summary = _runtime_profile_summary(perf, duration_s=row["duration_s"])
        parsed_rows.append((row, perf, summary))
        if perf.get("model_family") == "lightgbm" and regression_per_trial_s is None:
            regression_per_trial_s = summary.get("duration_per_trial_s")

    for row, perf, _summary in parsed_rows:
        cache = perf.get("ranker_cache") if isinstance(perf, dict) else None
        is_ranker_profile = (
            isinstance(perf, dict)
            and (
                perf.get("model_family") == "lightgbm_ranker"
                or (isinstance(cache, dict) and bool(cache.get("enabled")))
                or "ranker" in str(row["run_id"]).lower()
            )
        )
        if not is_ranker_profile:
            continue
        runtime_summary = _runtime_profile_summary(
            perf,
            duration_s=row["duration_s"],
            regression_per_trial_s=regression_per_trial_s,
        )
        ranker_profiles.append(
            {
                "run_id": row["run_id"],
                "duration_s": row["duration_s"],
                "started_at": row["started_at"],
                "model_family": perf.get("model_family") if isinstance(perf, dict) else None,
                "trials": runtime_summary["trials"],
                "duration_per_trial_s": runtime_summary["duration_per_trial_s"],
                "train_time_pct": runtime_summary["train_time_pct"],
                "cache_hit_rate": runtime_summary["cache_hit_rate"],
                "eval_cache_hit_rate": runtime_summary["eval_cache_hit_rate"],
                "matrix_cache_hit_rate": runtime_summary["matrix_cache_hit_rate"],
                "feature_drift_cache_hit_rate": runtime_summary["feature_drift_cache_hit_rate"],
                "runtime_ratio_vs_regression": runtime_summary["runtime_ratio_vs_regression"],
                "ranker_cache": cache,
                "evaluation_cache": perf.get("evaluation_cache") if isinstance(perf, dict) else None,
                "timing": perf.get("timing") if isinstance(perf, dict) else None,
            }
        )
    return {"ranker_policy": ranker_policy, "ranker_profiles": ranker_profiles}
