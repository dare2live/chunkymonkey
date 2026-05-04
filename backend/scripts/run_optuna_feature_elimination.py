#!/usr/bin/env python3
"""Optuna-style reduction for candidate TDX features.

The output is experimental metadata only. It never promotes or replaces the
production champion model.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.db import get_conn  # noqa: E402
from scripts.build_candidate_feature_panel import (  # noqa: E402
    CANDIDATE_FEATURE_SET_ID,
)
from scripts.run_feature_group_ablation import (  # noqa: E402
    FEATURE_GROUPS,
    LABEL_COLUMNS,
    _candidate_features_for_set,
    _feature_group_map_for_set,
    _group_by_date,
    _load_candidate_panel,
    _mean_rank_ic,
    _pearson,
    _rank_percentiles,
    _score_panel,
    _to_float,
)

logger = logging.getLogger("optuna_feature_elimination")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s | %(message)s")

try:
    import optuna

    optuna.logging.set_verbosity(optuna.logging.WARNING)
except Exception:  # pragma: no cover - depends on local environment
    optuna = None


DDL = """
CREATE TABLE IF NOT EXISTS mart_feature_candidate_score (
    run_id TEXT NOT NULL,
    feature_set_id TEXT NOT NULL,
    feature_name TEXT NOT NULL,
    feature_group TEXT,
    coverage_pct DOUBLE,
    missing_pct DOUBLE,
    rank_ic DOUBLE,
    fold_same_sign_rate DOUBLE,
    fold_count INTEGER,
    sensitivity_json TEXT,
    selected BOOLEAN DEFAULT FALSE,
    rejection_reason TEXT,
    built_at TEXT,
    PRIMARY KEY (run_id, feature_name)
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
ALTER TABLE mart_feature_candidate_score ADD COLUMN IF NOT EXISTS fold_same_sign_rate DOUBLE;
ALTER TABLE mart_feature_candidate_score ADD COLUMN IF NOT EXISTS fold_count INTEGER;
ALTER TABLE mart_feature_candidate_score ADD COLUMN IF NOT EXISTS sensitivity_json TEXT;

CREATE TABLE IF NOT EXISTS mart_tdx_gpcw_auto_feature_score (
    run_id TEXT NOT NULL,
    feature_set_id TEXT NOT NULL,
    feature_name TEXT NOT NULL,
    feature_family TEXT,
    coverage_pct DOUBLE,
    rank_ic DOUBLE,
    fold_same_sign_rate DOUBLE,
    horizon_sensitivity TEXT,
    selected BOOLEAN DEFAULT FALSE,
    rejection_reason TEXT,
    built_at TEXT,
    PRIMARY KEY (run_id, feature_name)
);

CREATE TABLE IF NOT EXISTS mart_tdx_gpcw_auto_feature_cluster (
    run_id TEXT NOT NULL,
    feature_set_id TEXT NOT NULL,
    cluster_id TEXT NOT NULL,
    feature_name TEXT NOT NULL,
    representative_feature TEXT,
    corr_to_representative DOUBLE,
    built_at TEXT,
    PRIMARY KEY (run_id, feature_name)
);

CREATE TABLE IF NOT EXISTS mart_tdx_gpcw_auto_optuna_run (
    run_id TEXT PRIMARY KEY,
    feature_set_id TEXT NOT NULL,
    trials INTEGER,
    objective_score DOUBLE,
    selected_features_json TEXT,
    rejected_features_json TEXT,
    promote_to_champion BOOLEAN DEFAULT FALSE,
    notes TEXT,
    built_at TEXT
);
"""


def ensure_tables(conn: Any) -> None:
    if hasattr(conn, "executescript"):
        conn.executescript(DDL)
        return
    for stmt in DDL.split(";"):
        stmt = stmt.strip()
        if stmt:
            conn.execute(stmt)


def _feature_group(feature: str) -> str:
    for group, features in FEATURE_GROUPS.items():
        if feature in features:
            return group
    return "other"


def _score_subset(
    rows: list[dict[str, Any]],
    features: list[str],
    signs: dict[str, int],
    *,
    label_col: str = "forward_ret_20d",
) -> float | None:
    if not features:
        return None
    scored = _score_panel(rows, features, signs, "__subset_score")
    return _mean_rank_ic(scored, "__subset_score", label_col=label_col)


def _rank_col(feature: str) -> str:
    return f"__rank_{feature}"


def _build_rank_cache(rows: list[dict[str, Any]], features: list[str]) -> dict[str, Any]:
    return {"rows": rows, "features": set(features)}


def _score_cached_subset(
    rank_cache: dict[str, Any],
    features: list[str],
    signs: dict[str, int],
    *,
    label_col: str = "forward_ret_20d",
) -> float | None:
    if not features:
        return None
    usable = [feature for feature in features if feature in rank_cache["features"]]
    return _score_subset(rank_cache["rows"], usable, signs, label_col=label_col)


def _fold_rank_ics(
    rows: list[dict[str, Any]],
    score_col: str,
    *,
    label_col: str = "forward_ret_20d",
    folds: int = 4,
) -> list[float | None]:
    dates = sorted({str(row.get("date")) for row in rows if row.get("date") is not None})
    if not dates or not any(label_col in row for row in rows):
        return []
    n_folds = min(max(int(folds), 1), len(dates))
    values: list[float | None] = []
    for fold_idx in range(n_folds):
        start = fold_idx * len(dates) // n_folds
        end = (fold_idx + 1) * len(dates) // n_folds
        fold_dates = set(dates[start:end])
        if not fold_dates:
            continue
        part = [row for row in rows if str(row.get("date")) in fold_dates]
        values.append(_mean_rank_ic(part, score_col, label_col=label_col))
    return values


def _same_sign_rate(rank_ic: float | None, fold_ics: list[float | None]) -> float | None:
    values = [value for value in fold_ics if value is not None]
    if rank_ic is None or rank_ic == 0 or not values:
        return None
    expected_sign = 1 if rank_ic > 0 else -1
    same = sum(1 for value in values if (1 if value > 0 else -1 if value < 0 else 0) == expected_sign)
    return float(same / len(values))


def _filter_rejection_reason(stat: dict[str, Any], min_coverage: float, min_abs_ic: float) -> str | None:
    if stat["coverage_pct"] < min_coverage:
        return "low_coverage"
    if abs(stat["rank_ic"] or 0.0) < min_abs_ic:
        return "low_abs_rank_ic"
    if stat["fold_count"] >= 4 and (stat["fold_same_sign_rate"] or 0.0) < 0.60:
        return "unstable_folds"
    return None


def _spearman_feature_corr(rows: list[dict[str, Any]], left: str, right: str) -> float | None:
    pairs = [
        (left_value, right_value)
        for row in rows
        if (left_value := _to_float(row.get(left))) is not None
        if (right_value := _to_float(row.get(right))) is not None
    ]
    if len(pairs) < 3:
        return None
    left_values = [value for value, _ in pairs]
    right_values = [value for _, value in pairs]
    if len(set(left_values)) < 2 or len(set(right_values)) < 2:
        return None
    return _pearson(_rank_percentiles(left_values), _rank_percentiles(right_values))


def _correlation_rejections(
    rows: list[dict[str, Any]],
    selected_features: list[str],
    feature_stats: dict[str, dict[str, Any]],
    *,
    threshold: float = 0.95,
) -> tuple[list[str], dict[str, str]]:
    selected = list(dict.fromkeys(selected_features))
    rejected: dict[str, str] = {}
    if len(selected) < 2:
        return selected, rejected

    def quality(feature: str) -> tuple[float, float]:
        stat = feature_stats[feature]
        return (float(stat["coverage_pct"] or 0.0), abs(float(stat["rank_ic"] or 0.0)))

    for idx, left in enumerate(selected_features):
        if left not in selected:
            continue
        for right in selected_features[idx + 1:]:
            if right not in selected:
                continue
            value = _spearman_feature_corr(rows, left, right)
            if value is None or abs(float(value)) < threshold:
                continue
            keep, drop = (left, right) if quality(left) >= quality(right) else (right, left)
            if drop in selected:
                selected.remove(drop)
                rejected[drop] = f"high_corr:{keep}"
    return selected, rejected


def _quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _run_sql_reduction(
    conn: Any,
    *,
    feature_set_id: str,
    trials: int,
    min_coverage: float,
    min_abs_ic: float,
    run_id: str | None,
) -> dict[str, Any]:
    is_auto_feature_set = feature_set_id.startswith("tdx_gpcw_auto")
    feature_group_map = _feature_group_map_for_set(conn, feature_set_id)
    features = _candidate_features_for_set(conn, feature_set_id)
    if not features:
        raise RuntimeError(f"no features for feature_set_id={feature_set_id}")
    count_exprs = ", ".join(
        f"SUM(CASE WHEN {_quote_ident(feature)} IS NOT NULL THEN 1 ELSE 0 END) AS {_quote_ident(feature)}"
        for feature in features
    )
    coverage_row = conn.execute(
        f"""
        SELECT COUNT(*) AS total_rows, {count_exprs}
        FROM fact_feature_panel_candidate
        WHERE feature_set_id = ?
        """,
        (feature_set_id,),
    ).fetchone()
    total_rows = int(coverage_row["total_rows"] or 0) if coverage_row else 0
    wf_rows = conn.execute(
        """
        SELECT feature_name,
               AVG(CASE WHEN label_name = 'forward_ret_20d' THEN rank_ic END) AS rank_ic_20d,
               AVG(CASE WHEN rank_ic > 0 THEN 1.0 ELSE 0.0 END) AS same_sign_rate,
               COUNT(CASE WHEN label_name = 'forward_ret_20d' AND rank_ic IS NOT NULL THEN 1 END) AS fold_count,
               AVG(CASE WHEN label_name = 'forward_ret_5d' THEN rank_ic END) AS rank_ic_5d,
               AVG(CASE WHEN label_name = 'forward_ret_10d' THEN rank_ic END) AS rank_ic_10d,
               AVG(CASE WHEN label_name = 'forward_ret_60d' THEN rank_ic END) AS rank_ic_60d
        FROM mart_candidate_walkforward_eval
        WHERE feature_set_id = ?
        GROUP BY feature_name
        """,
        (feature_set_id,),
    ).fetchall()
    wf = {row["feature_name"]: row for row in wf_rows}
    stats = []
    for feature in features:
        coverage = float((coverage_row[feature] or 0) / max(total_rows, 1) * 100.0)
        row = wf.get(feature)
        rank_ic = float(row["rank_ic_20d"]) if row and row["rank_ic_20d"] is not None else None
        same_sign = float(row["same_sign_rate"]) if row and row["same_sign_rate"] is not None else None
        fold_count = int(row["fold_count"] or 0) if row else 0
        sensitivity = {
            "forward_ret_5d": row["rank_ic_5d"] if row else None,
            "forward_ret_10d": row["rank_ic_10d"] if row else None,
            "forward_ret_60d": row["rank_ic_60d"] if row else None,
        }
        reason = _filter_rejection_reason(
            {
                "coverage_pct": coverage,
                "rank_ic": rank_ic,
                "fold_count": fold_count,
                "fold_same_sign_rate": same_sign,
            },
            min_coverage,
            min_abs_ic,
        )
        stats.append(
            {
                "feature": feature,
                "coverage_pct": coverage,
                "rank_ic": rank_ic,
                "fold_same_sign_rate": same_sign,
                "fold_count": fold_count,
                "sensitivity": sensitivity,
                "reason": reason,
            }
        )
    eligible = [s for s in stats if s["reason"] is None and s["rank_ic"] is not None]
    eligible.sort(key=lambda s: (abs(float(s["rank_ic"] or 0.0)), s["coverage_pct"]), reverse=True)
    selected_features = [s["feature"] for s in eligible[: min(30, max(5, len(eligible)))]]
    if not selected_features:
        fallback = sorted(stats, key=lambda s: abs(float(s["rank_ic"] or 0.0)), reverse=True)[:5]
        selected_features = [s["feature"] for s in fallback]
    rejected = [s["feature"] for s in stats if s["feature"] not in selected_features]
    objective_score = (
        float(sum(abs(float(s["rank_ic"] or 0.0)) for s in eligible[:30]) / max(len(eligible[:30]), 1))
        if eligible else None
    )
    label_sensitivity = {}
    for label in ("forward_ret_5d", "forward_ret_10d", "forward_ret_20d", "forward_ret_60d"):
        values = []
        for s in stats:
            if s["feature"] not in selected_features:
                continue
            if label == "forward_ret_20d":
                value = s["rank_ic"]
            else:
                value = s["sensitivity"].get(label)
            if value is not None:
                values.append(float(value))
        label_sensitivity[label] = float(sum(values) / len(values)) if values else None

    run_id = run_id or f"feature_elim_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"
    built_at = datetime.utcnow().isoformat(timespec="seconds")
    score_rows = []
    for s in stats:
        selected = s["feature"] in selected_features
        reason = None if selected else s["reason"] or "subset_eliminated"
        score_rows.append((
            run_id,
            feature_set_id,
            s["feature"],
            feature_group_map.get(s["feature"], _feature_group(s["feature"])),
            s["coverage_pct"],
            100.0 - s["coverage_pct"],
            s["rank_ic"],
            s["fold_same_sign_rate"],
            s["fold_count"],
            json.dumps(s["sensitivity"], ensure_ascii=False),
            selected,
            reason,
            built_at,
        ))
    conn.executemany(
        """
        INSERT OR REPLACE INTO mart_feature_candidate_score
        (run_id, feature_set_id, feature_name, feature_group, coverage_pct,
         missing_pct, rank_ic, fold_same_sign_rate, fold_count,
         sensitivity_json, selected, rejection_reason, built_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        score_rows,
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
            "sql_walkforward_deterministic",
            "forward_ret_20d",
            objective_score,
            json.dumps(selected_features, ensure_ascii=False),
            json.dumps(rejected, ensure_ascii=False),
            int(trials),
            False,
            json.dumps(
                {
                    "message": "walk-forward SQL reduction; production champion untouched",
                    "label_sensitivity": label_sensitivity,
                },
                ensure_ascii=False,
            ),
            built_at,
        ),
    )
    if is_auto_feature_set:
        conn.executemany(
            """
            INSERT OR REPLACE INTO mart_tdx_gpcw_auto_feature_score
            (run_id, feature_set_id, feature_name, feature_family, coverage_pct,
             rank_ic, fold_same_sign_rate, horizon_sensitivity, selected,
             rejection_reason, built_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    row[0], row[1], row[2], row[3], row[4], row[6], row[7],
                    row[9], row[10], row[11], row[12],
                )
                for row in score_rows
            ],
        )
        conn.execute(
            """
            INSERT OR REPLACE INTO mart_tdx_gpcw_auto_optuna_run
            (run_id, feature_set_id, trials, objective_score,
             selected_features_json, rejected_features_json,
             promote_to_champion, notes, built_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                feature_set_id,
                int(trials),
                objective_score,
                json.dumps(selected_features, ensure_ascii=False),
                json.dumps(rejected, ensure_ascii=False),
                False,
                json.dumps({"label_sensitivity": label_sensitivity}, ensure_ascii=False),
                built_at,
            ),
        )
        conn.executemany(
            """
            INSERT OR REPLACE INTO mart_tdx_gpcw_auto_feature_cluster
            (run_id, feature_set_id, cluster_id, feature_name,
             representative_feature, corr_to_representative, built_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    run_id,
                    feature_set_id,
                    feature_group_map.get(feature, _feature_group(feature)),
                    feature,
                    feature,
                    1.0,
                    built_at,
                )
                for feature in features
            ],
        )
    from services.schema_versions import record_actual_version
    record_actual_version(conn, "mart_feature_candidate_score")
    record_actual_version(conn, "mart_model_selection_run")
    conn.commit()
    return {
        "run_id": run_id,
        "feature_set_id": feature_set_id,
        "method": "sql_walkforward_deterministic",
        "objective_score": objective_score,
        "selected_features": selected_features,
        "rejected_features": rejected,
        "label_sensitivity": label_sensitivity,
        "promote_to_champion": False,
    }


def run_optuna_feature_elimination(
    conn: Any,
    *,
    feature_set_id: str = CANDIDATE_FEATURE_SET_ID,
    trials: int = 64,
    min_coverage: float = 30.0,
    min_abs_ic: float = 0.005,
    run_id: str | None = None,
    method: str = "full",
) -> dict:
    ensure_tables(conn)
    if method == "sql" or feature_set_id.startswith("tdx_gpcw_auto"):
        return _run_sql_reduction(
            conn,
            feature_set_id=feature_set_id,
            trials=trials,
            min_coverage=min_coverage,
            min_abs_ic=min_abs_ic,
            run_id=run_id,
        )
    records = _load_candidate_panel(conn, feature_set_id)
    if not records:
        raise RuntimeError(f"fact_feature_panel_candidate empty for feature_set_id={feature_set_id}")

    feature_group_map = _feature_group_map_for_set(conn, feature_set_id)
    candidate_features = _candidate_features_for_set(conn, feature_set_id)
    panel_cols = set(records[0].keys())
    usable = [
        feature for feature in candidate_features
        if feature in panel_cols and any(_to_float(row.get(feature)) is not None for row in records)
    ]
    if not usable:
        raise RuntimeError("candidate panel has no non-null candidate features")

    n = len(records)
    rank_cache = _build_rank_cache(records, usable)
    feature_stats = {}
    for feature in usable:
        coverage = float(
            sum(1 for row in records if _to_float(row.get(feature)) is not None)
            / max(n, 1)
            * 100
        )
        feature_rows = [{**row, "__score": row.get(feature)} for row in records]
        rank_ic = _mean_rank_ic(feature_rows, "__score")
        fold_ics = _fold_rank_ics(feature_rows, "__score")
        sensitivity = {
            label: _mean_rank_ic(feature_rows, "__score", label_col=label)
            for label in LABEL_COLUMNS
            if label in panel_cols and label != "forward_ret_20d"
        }
        feature_stats[feature] = {
            "coverage_pct": coverage,
            "missing_pct": 100.0 - coverage,
            "rank_ic": rank_ic,
            "fold_ics": fold_ics,
            "fold_count": len([value for value in fold_ics if value is not None]),
            "fold_same_sign_rate": _same_sign_rate(rank_ic, fold_ics),
            "sensitivity": sensitivity,
        }
    signs = {f: (-1 if (feature_stats[f]["rank_ic"] or 0) < 0 else 1) for f in usable}
    deterministic_candidates = [
        feature
        for feature in usable
        if _filter_rejection_reason(feature_stats[feature], min_coverage, min_abs_ic) is None
    ]

    method = "deterministic_fallback"
    objective_score = None
    if optuna is not None and trials > 0 and len(usable) >= 2:
        method = "optuna"

        def objective(trial):
            selected = [
                feature
                for feature in usable
                if trial.suggest_categorical(feature, [False, True])
            ]
            if not selected:
                return -1.0
            rank_ic = _score_cached_subset(rank_cache, selected, signs)
            if rank_ic is None:
                return -1.0
            size_penalty = 0.002 * (len(selected) / len(usable))
            return float(abs(rank_ic) - size_penalty)

        study = optuna.create_study(direction="maximize")
        study.optimize(objective, n_trials=trials, show_progress_bar=False)
        selected_features = [
            f
            for f in usable
            if study.best_trial.params.get(f)
            and _filter_rejection_reason(feature_stats[f], min_coverage, min_abs_ic) is None
        ]
        if not selected_features:
            selected_features = deterministic_candidates
        objective_score = float(study.best_value)
    else:
        selected_features = deterministic_candidates

    selected_features, correlation_rejected = _correlation_rejections(records, selected_features, feature_stats)
    objective_score = _score_cached_subset(rank_cache, selected_features, signs)
    label_sensitivity = {
        label: _score_cached_subset(rank_cache, selected_features, signs, label_col=label)
        for label in LABEL_COLUMNS
        if label in panel_cols
    }

    rejected = [f for f in usable if f not in selected_features]
    run_id = run_id or f"feature_elim_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"
    built_at = datetime.utcnow().isoformat(timespec="seconds")

    score_rows = []
    for feature in usable:
        stat = feature_stats[feature]
        selected = feature in selected_features
        reason = None
        if not selected:
            reason = (
                correlation_rejected.get(feature)
                or _filter_rejection_reason(stat, min_coverage, min_abs_ic)
                or "subset_eliminated"
            )
        score_rows.append((
            run_id,
            feature_set_id,
            feature,
            feature_group_map.get(feature, _feature_group(feature)),
            stat["coverage_pct"],
            stat["missing_pct"],
            stat["rank_ic"],
            stat["fold_same_sign_rate"],
            stat["fold_count"],
            json.dumps(stat["sensitivity"], ensure_ascii=False),
            selected,
            reason,
            built_at,
        ))

    conn.executemany(
        """
        INSERT OR REPLACE INTO mart_feature_candidate_score
        (run_id, feature_set_id, feature_name, feature_group, coverage_pct,
         missing_pct, rank_ic, fold_same_sign_rate, fold_count,
         sensitivity_json, selected, rejection_reason, built_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        score_rows,
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
            method,
            "forward_ret_20d",
            objective_score,
            json.dumps(selected_features, ensure_ascii=False),
            json.dumps(rejected, ensure_ascii=False),
            int(trials),
            False,
            json.dumps(
                {
                    "message": "candidate-only reduction; production champion untouched",
                    "label_sensitivity": label_sensitivity,
                    "correlation_rejected": correlation_rejected,
                },
                ensure_ascii=False,
            ),
            built_at,
        ),
    )
    if feature_set_id.startswith("tdx_gpcw_auto"):
        conn.executemany(
            """
            INSERT OR REPLACE INTO mart_tdx_gpcw_auto_feature_score
            (run_id, feature_set_id, feature_name, feature_family, coverage_pct,
             rank_ic, fold_same_sign_rate, horizon_sensitivity, selected,
             rejection_reason, built_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    row[0], row[1], row[2], row[3], row[4], row[6], row[7],
                    row[9], row[10], row[11], row[12],
                )
                for row in score_rows
            ],
        )
        conn.execute(
            """
            INSERT OR REPLACE INTO mart_tdx_gpcw_auto_optuna_run
            (run_id, feature_set_id, trials, objective_score,
             selected_features_json, rejected_features_json,
             promote_to_champion, notes, built_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                feature_set_id,
                int(trials),
                objective_score,
                json.dumps(selected_features, ensure_ascii=False),
                json.dumps(rejected, ensure_ascii=False),
                False,
                json.dumps(
                    {
                        "message": "auto gpcw reduction; production champion untouched",
                        "label_sensitivity": label_sensitivity,
                    },
                    ensure_ascii=False,
                ),
                built_at,
            ),
        )
        cluster_rows = [
            (
                run_id,
                feature_set_id,
                feature_group_map.get(feature, _feature_group(feature)),
                feature,
                feature,
                1.0,
                built_at,
            )
            for feature in usable
        ]
        conn.executemany(
            """
            INSERT OR REPLACE INTO mart_tdx_gpcw_auto_feature_cluster
            (run_id, feature_set_id, cluster_id, feature_name,
             representative_feature, corr_to_representative, built_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            cluster_rows,
        )
    conn.commit()
    return {
        "run_id": run_id,
        "feature_set_id": feature_set_id,
        "method": method,
        "objective_score": objective_score,
        "selected_features": selected_features,
        "rejected_features": rejected,
        "label_sensitivity": label_sensitivity,
        "promote_to_champion": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--feature-set-id", default=CANDIDATE_FEATURE_SET_ID)
    parser.add_argument("--trials", type=int, default=64)
    parser.add_argument("--min-coverage", type=float, default=30.0)
    parser.add_argument("--min-abs-ic", type=float, default=0.005)
    parser.add_argument("--method", choices=["full", "sql"], default="full")
    args = parser.parse_args()

    conn = get_conn()
    try:
        result = run_optuna_feature_elimination(
            conn,
            feature_set_id=args.feature_set_id,
            trials=args.trials,
            min_coverage=args.min_coverage,
            min_abs_ic=args.min_abs_ic,
            method=args.method,
        )
        logger.info("feature elimination: %s", result)
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
