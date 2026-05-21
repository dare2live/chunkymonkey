#!/usr/bin/env python3
"""Build candidate panels that replace drift offenders with stable transforms."""
from __future__ import annotations

import argparse
from collections import defaultdict
import json
import math
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.db import get_conn  # noqa: E402
from services.pipeline_manifest import git_commit_sha, record_pipeline_run, utc_now_iso  # noqa: E402
from services.schema_versions import record_actual_version  # noqa: E402
from scripts.train_multidim_model import load_model_selection_run  # noqa: E402


LABEL_COLUMNS = ["forward_ret_5d", "forward_ret_10d", "forward_ret_20d", "forward_ret_60d", "forward_ret_90d"]
DEFAULT_RECOMMENDATIONS = {
    "exclude_or_transform_before_next_large_study",
    "winsorize_bucket_or_regime_split",
}
DEFAULT_TRANSFORMS = ["xs_rank", "xs_winsor", "xs_bucket5"]
DEFAULT_MARKET_CONTROLS = ["hs300_ret_20d", "hs300_ret_60d"]
REGIME_CONTROLS = ["regime_up", "regime_flat", "regime_down"]

DDL = """
CREATE TABLE IF NOT EXISTS fact_feature_panel_candidate (
    feature_set_id TEXT NOT NULL,
    stock_code TEXT NOT NULL,
    date TEXT NOT NULL,
    close REAL,
    forward_ret_5d REAL,
    forward_ret_10d REAL,
    forward_ret_20d REAL,
    forward_ret_60d REAL,
    forward_ret_90d REAL,
    built_at TEXT,
    PRIMARY KEY (feature_set_id, stock_code, date)
);
CREATE INDEX IF NOT EXISTS idx_feature_candidate_date
    ON fact_feature_panel_candidate(feature_set_id, date);

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

CREATE TABLE IF NOT EXISTS mart_feature_drift_mitigation_panel_build (
    run_id TEXT PRIMARY KEY,
    output_feature_set_id TEXT NOT NULL,
    model_selection_run_id TEXT NOT NULL,
    base_model_selection_run_id TEXT NOT NULL,
    base_table TEXT NOT NULL,
    root_cause_run_id TEXT,
    transformed_features_json TEXT NOT NULL,
    copied_features_json TEXT NOT NULL,
    original_selected_features_json TEXT NOT NULL,
    selected_features_json TEXT NOT NULL,
    transform_config_json TEXT NOT NULL,
    row_count INTEGER,
    stock_count INTEGER,
    date_count INTEGER,
    min_date TEXT,
    max_date TEXT,
    built_at TEXT NOT NULL
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
    conn.execute("ALTER TABLE mart_model_selection_run ADD COLUMN IF NOT EXISTS promote_to_champion BOOLEAN DEFAULT FALSE")


def _quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _quote_relation(name: str) -> str:
    return ".".join(_quote_ident(part) for part in name.split("."))


def _table_columns(conn: Any, table: str) -> set[str]:
    return {str(row[0]) for row in conn.execute(f"DESCRIBE {_quote_relation(table)}").fetchall()}


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


def _parse_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def _safe_json(value: Any, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except Exception:
        return default


def _finite_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _slug(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_]+", "_", value).strip("_")
    return slug or "feature"


def _transform_column(feature_name: str, transform: str) -> str:
    return f"{_slug(feature_name)}_{transform}"


def latest_root_cause_run_id(conn: Any) -> str | None:
    if not _table_exists(conn, "mart_feature_drift_root_cause_summary"):
        return None
    row = conn.execute(
        """
        SELECT run_id
          FROM mart_feature_drift_root_cause_summary
         ORDER BY built_at DESC NULLS LAST, run_id DESC
         LIMIT 1
        """
    ).fetchone()
    return str(row["run_id"]) if row else None


def load_root_cause_features(
    conn: Any,
    *,
    root_cause_run_id: str | None,
    explicit_features: list[str] | None = None,
    recommendations: set[str] | None = None,
    min_max_psi: float = 0.25,
) -> tuple[str | None, dict[str, dict[str, Any]]]:
    features = {feature: {"source": "explicit"} for feature in (explicit_features or [])}
    if features:
        return root_cause_run_id, features
    if not _table_exists(conn, "mart_feature_drift_root_cause_summary"):
        return root_cause_run_id, {}
    root_cause_run_id = root_cause_run_id or latest_root_cause_run_id(conn)
    if not root_cause_run_id:
        return None, {}
    wanted = {item.lower() for item in (recommendations or DEFAULT_RECOMMENDATIONS)}
    rows = conn.execute(
        """
        SELECT source_run_id, feature_name, offender_count, severe_count,
               max_psi, recommendation, built_at
          FROM mart_feature_drift_root_cause_summary
         WHERE run_id = ?
        """,
        (root_cause_run_id,),
    ).fetchall()
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        recommendation = str(row["recommendation"] or "").lower()
        max_psi = _finite_float(row["max_psi"]) or 0.0
        severe_count = int(row["severe_count"] or 0)
        if recommendation not in wanted and max_psi < min_max_psi and severe_count <= 0:
            continue
        feature_name = str(row["feature_name"])
        current = out.get(feature_name)
        if current and float(current.get("max_psi") or 0.0) >= max_psi:
            continue
        out[feature_name] = {
            "source": "root_cause",
            "root_cause_run_id": root_cause_run_id,
            "source_run_id": row["source_run_id"],
            "offender_count": int(row["offender_count"] or 0),
            "severe_count": severe_count,
            "max_psi": max_psi,
            "recommendation": recommendation,
            "built_at": row["built_at"],
        }
    return root_cause_run_id, out


def _ensure_candidate_columns(conn: Any, columns: list[str], *, labels: list[str], has_regime: bool) -> None:
    statements = ["ALTER TABLE fact_feature_panel_candidate ADD COLUMN IF NOT EXISTS close REAL"]
    statements.extend(
        f"ALTER TABLE fact_feature_panel_candidate ADD COLUMN IF NOT EXISTS {_quote_ident(label)} REAL"
        for label in labels
    )
    if has_regime:
        statements.append("ALTER TABLE fact_feature_panel_candidate ADD COLUMN IF NOT EXISTS regime_flag TEXT")
    statements.extend(
        f"ALTER TABLE fact_feature_panel_candidate ADD COLUMN IF NOT EXISTS {_quote_ident(column)} REAL"
        for column in columns
    )
    _execute_script(conn, ";\n".join(statements))


def _build_quantile_selects(features: list[str], low: float, high: float) -> list[str]:
    out: list[str] = []
    for feature in features:
        q_feature = _quote_ident(feature)
        prefix = _slug(feature)
        out.append(f"quantile_cont({q_feature}, {low}) AS {_quote_ident(prefix + '_q_low')}")
        out.append(f"quantile_cont({q_feature}, {high}) AS {_quote_ident(prefix + '_q_high')}")
    return out


def _build_window_selects(features: list[str], transforms: list[str], bucket_count: int) -> list[str]:
    out: list[str] = []
    for feature in features:
        q_feature = _quote_ident(feature)
        if "xs_rank" in transforms:
            out.append(
                "CASE WHEN b.{feature} IS NULL THEN NULL ELSE "
                "percent_rank() OVER (PARTITION BY CAST(b.date AS VARCHAR) "
                "ORDER BY b.{feature} NULLS LAST) END AS {alias}".format(
                    feature=q_feature,
                    alias=_quote_ident(_transform_column(feature, "xs_rank")),
                )
            )
        if "xs_bucket5" in transforms:
            out.append(
                "CASE WHEN b.{feature} IS NULL THEN NULL ELSE "
                "CAST(ntile({buckets}) OVER (PARTITION BY CAST(b.date AS VARCHAR) "
                "ORDER BY b.{feature} NULLS LAST) AS DOUBLE) END AS {alias}".format(
                    feature=q_feature,
                    buckets=int(bucket_count),
                    alias=_quote_ident(_transform_column(feature, "xs_bucket5")),
                )
            )
    return out


def _winsor_expr(feature: str) -> str:
    prefix = _slug(feature)
    q_feature = _quote_ident(feature)
    q_low = _quote_ident(prefix + "_q_low")
    q_high = _quote_ident(prefix + "_q_high")
    return (
        f"CASE WHEN r.{q_feature} IS NULL THEN NULL "
        f"WHEN r.{q_low} IS NULL OR r.{q_high} IS NULL THEN CAST(r.{q_feature} AS DOUBLE) "
        f"ELSE LEAST(GREATEST(CAST(r.{q_feature} AS DOUBLE), r.{q_low}), r.{q_high}) END"
    )


def _select_expr_for_feature(feature: str, output_column: str, transform: str | None) -> str:
    if transform == "xs_winsor":
        return f"{_winsor_expr(feature)} AS {_quote_ident(output_column)}"
    if transform:
        return f"CAST(r.{_quote_ident(output_column)} AS DOUBLE) AS {_quote_ident(output_column)}"
    return f"CAST(r.{_quote_ident(feature)} AS DOUBLE) AS {_quote_ident(output_column)}"


def _select_expr_for_regime_control(column: str) -> str:
    regime = column.replace("regime_", "", 1)
    return (
        f"CASE WHEN CAST(r.regime_flag AS TEXT) = '{regime}' THEN 1.0 ELSE 0.0 END "
        f"AS {_quote_ident(column)}"
    )


def build_feature_drift_mitigation_panel(
    conn: Any,
    *,
    base_model_selection_run_id: str,
    output_feature_set_id: str,
    run_id: str | None = None,
    model_selection_run_id: str | None = None,
    base_table: str = "fact_feature_panel",
    root_cause_run_id: str | None = None,
    explicit_features: list[str] | None = None,
    transform_types: list[str] | None = None,
    keep_original: bool = False,
    include_regime_controls: bool = False,
    include_market_controls: bool = False,
    market_control_features: list[str] | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    winsor_low: float = 0.01,
    winsor_high: float = 0.99,
    bucket_count: int = 5,
    min_root_cause_max_psi: float = 0.25,
    recommendations: set[str] | None = None,
) -> dict[str, Any]:
    ensure_tables(conn)
    started_at = utc_now_iso()
    t0 = time.perf_counter()
    built_at = datetime.utcnow().isoformat(timespec="seconds")
    run_id = run_id or f"feature_drift_mitigation_panel_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"
    model_selection_run_id = model_selection_run_id or run_id
    transform_types = list(dict.fromkeys(transform_types or DEFAULT_TRANSFORMS))
    supported = set(DEFAULT_TRANSFORMS)
    unsupported = sorted(set(transform_types) - supported)
    if unsupported:
        raise ValueError(f"unsupported transform_types: {unsupported}")

    base_cols = _table_columns(conn, base_table)
    selection = load_model_selection_run(conn, base_model_selection_run_id)
    original_features = list(selection["selected_features"])
    missing = [feature for feature in original_features if feature not in base_cols]
    if missing:
        raise RuntimeError(f"base table missing selected features: {missing}")
    if "stock_code" not in base_cols or "date" not in base_cols:
        raise RuntimeError("base table must include stock_code and date")
    labels = [label for label in LABEL_COLUMNS if label in base_cols]
    if not labels:
        raise RuntimeError("base table must include at least one forward label")
    has_regime = "regime_flag" in base_cols

    root_cause_run_id, root_cause_features = load_root_cause_features(
        conn,
        root_cause_run_id=root_cause_run_id,
        explicit_features=explicit_features,
        recommendations=recommendations,
        min_max_psi=min_root_cause_max_psi,
    )
    mitigated = [feature for feature in original_features if feature in root_cause_features]
    if not mitigated:
        raise RuntimeError(
            "no selected features matched drift root-cause mitigation inputs; "
            "pass --feature explicitly or verify mart_feature_drift_root_cause_summary"
        )

    copied_features = [feature for feature in original_features if feature not in mitigated or keep_original]
    transformed_map: dict[str, list[str]] = {}
    selected_features = list(copied_features)
    control_features: list[str] = []
    if include_regime_controls:
        if not has_regime:
            raise RuntimeError("include_regime_controls requires base table regime_flag")
        control_features.extend(REGIME_CONTROLS)
    if include_market_controls:
        requested_market_controls = market_control_features or DEFAULT_MARKET_CONTROLS
        missing_market_controls = [feature for feature in requested_market_controls if feature not in base_cols]
        if missing_market_controls:
            raise RuntimeError(f"base table missing market control features: {missing_market_controls}")
        control_features.extend(requested_market_controls)
    selected_features.extend(control_features)
    transform_pairs: list[tuple[str, str, str]] = []
    for feature in mitigated:
        transformed_map[feature] = []
        for transform in transform_types:
            column = _transform_column(feature, transform)
            transformed_map[feature].append(column)
            selected_features.append(column)
            transform_pairs.append((feature, column, transform))

    selected_features = list(dict.fromkeys(selected_features))
    _ensure_candidate_columns(conn, selected_features, labels=labels, has_regime=has_regime)

    where: list[str] = []
    params: list[Any] = []
    if start_date:
        where.append("CAST(date AS VARCHAR) >= ?")
        params.append(start_date)
    if end_date:
        where.append("CAST(date AS VARCHAR) <= ?")
        params.append(end_date)
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""

    quantile_selects = _build_quantile_selects(mitigated, winsor_low, winsor_high)
    window_selects = _build_window_selects(mitigated, transform_types, bucket_count)
    quantile_sql = ",\n               ".join(["CAST(date AS VARCHAR) AS date_key", *quantile_selects])
    ranked_cols = ["b.*"]
    ranked_cols.extend(f"q.{_quote_ident(_slug(feature) + '_q_low')}" for feature in mitigated)
    ranked_cols.extend(f"q.{_quote_ident(_slug(feature) + '_q_high')}" for feature in mitigated)
    ranked_cols.extend(window_selects)

    include_close = "close" in base_cols
    insert_cols = ["feature_set_id", "stock_code", "date"]
    if include_close:
        insert_cols.append("close")
    insert_cols.extend(labels)
    if has_regime:
        insert_cols.append("regime_flag")
    insert_cols.extend(selected_features)
    insert_cols.append("built_at")

    select_cols = [
        "? AS feature_set_id",
        "r.stock_code",
        "CAST(r.date AS VARCHAR) AS date",
    ]
    if include_close:
        select_cols.append("CAST(r.close AS REAL) AS close")
    select_cols.extend(f"CAST(r.{_quote_ident(label)} AS REAL) AS {_quote_ident(label)}" for label in labels)
    if has_regime:
        select_cols.append("CAST(r.regime_flag AS TEXT) AS regime_flag")
    for feature in copied_features:
        select_cols.append(_select_expr_for_feature(feature, feature, None))
    for feature in control_features:
        if feature in REGIME_CONTROLS:
            select_cols.append(_select_expr_for_regime_control(feature))
        else:
            select_cols.append(_select_expr_for_feature(feature, feature, None))
    for feature, column, transform in transform_pairs:
        select_cols.append(_select_expr_for_feature(feature, column, transform))
    select_cols.append("? AS built_at")

    conn.execute("DELETE FROM fact_feature_panel_candidate WHERE feature_set_id = ?", (output_feature_set_id,))
    conn.execute(
        f"""
        INSERT INTO fact_feature_panel_candidate
        ({', '.join(_quote_ident(col) for col in insert_cols)})
        WITH base AS (
            SELECT *
              FROM {_quote_relation(base_table)}
              {where_sql}
        ),
        quantiles AS (
            SELECT {quantile_sql}
              FROM base
             GROUP BY CAST(date AS VARCHAR)
        ),
        ranked AS (
            SELECT {', '.join(ranked_cols)}
              FROM base b
              LEFT JOIN quantiles q
                ON q.date_key = CAST(b.date AS VARCHAR)
        )
        SELECT {', '.join(select_cols)}
          FROM ranked r
        """,
        [*params, output_feature_set_id, built_at],
    )
    summary = conn.execute(
        """
        SELECT COUNT(*) AS row_count,
               COUNT(DISTINCT stock_code) AS stock_count,
               COUNT(DISTINCT date) AS date_count,
               MIN(date) AS min_date,
               MAX(date) AS max_date
          FROM fact_feature_panel_candidate
         WHERE feature_set_id = ?
        """,
        (output_feature_set_id,),
    ).fetchone()

    rejected = {
        "mitigated_original_features": mitigated,
        "root_cause_features": root_cause_features,
        "keep_original": keep_original,
    }
    notes = {
        "run_id": run_id,
        "base_model_selection_run_id": base_model_selection_run_id,
        "base_table": base_table,
        "output_feature_set_id": output_feature_set_id,
        "root_cause_run_id": root_cause_run_id,
        "transform_types": transform_types,
        "transformed_features": transformed_map,
        "control_features": control_features,
        "winsor_low": winsor_low,
        "winsor_high": winsor_high,
        "bucket_count": bucket_count,
        "message": "drift mitigation candidate panel; no model trained or promoted",
    }
    conn.execute("DELETE FROM mart_model_selection_run WHERE run_id = ?", (model_selection_run_id,))
    conn.execute(
        """
        INSERT INTO mart_model_selection_run
        (run_id, feature_set_id, method, label_name, objective_score,
         selected_features_json, rejected_features_json, trials,
         promote_to_champion, notes, built_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            model_selection_run_id,
            output_feature_set_id,
            "feature_drift_mitigation_panel_builder",
            selection.get("label_name") or "forward_ret_20d",
            None,
            json.dumps(selected_features, ensure_ascii=False),
            json.dumps(rejected, ensure_ascii=False, sort_keys=True),
            0,
            False,
            json.dumps(notes, ensure_ascii=False, sort_keys=True),
            built_at,
        ),
    )
    conn.execute("DELETE FROM mart_feature_drift_mitigation_panel_build WHERE run_id = ?", (run_id,))
    conn.execute(
        """
        INSERT INTO mart_feature_drift_mitigation_panel_build
        (run_id, output_feature_set_id, model_selection_run_id,
         base_model_selection_run_id, base_table, root_cause_run_id,
         transformed_features_json, copied_features_json,
         original_selected_features_json, selected_features_json,
         transform_config_json, row_count, stock_count, date_count,
         min_date, max_date, built_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_id,
            output_feature_set_id,
            model_selection_run_id,
            base_model_selection_run_id,
            base_table,
            root_cause_run_id,
            json.dumps(transformed_map, ensure_ascii=False, sort_keys=True),
            json.dumps(copied_features, ensure_ascii=False),
            json.dumps(original_features, ensure_ascii=False),
            json.dumps(selected_features, ensure_ascii=False),
            json.dumps(
                {
                    "transform_types": transform_types,
                    "keep_original": keep_original,
                    "include_regime_controls": include_regime_controls,
                    "include_market_controls": include_market_controls,
                    "market_control_features": market_control_features or [],
                    "winsor_low": winsor_low,
                    "winsor_high": winsor_high,
                    "bucket_count": bucket_count,
                    "min_root_cause_max_psi": min_root_cause_max_psi,
                    "recommendations": sorted(recommendations or DEFAULT_RECOMMENDATIONS),
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            summary["row_count"],
            summary["stock_count"],
            summary["date_count"],
            summary["min_date"],
            summary["max_date"],
            built_at,
        ),
    )
    record_actual_version(conn, "fact_feature_panel_candidate")
    record_actual_version(conn, "mart_feature_drift_mitigation_panel_build")
    record_actual_version(conn, "mart_model_selection_run")
    duration_s = time.perf_counter() - t0
    record_pipeline_run(
        conn,
        run_id=run_id,
        pipeline_name="build_feature_drift_mitigation_panel",
        status="success",
        started_at=started_at,
        ended_at=utc_now_iso(),
        duration_s=duration_s,
        commit_sha=git_commit_sha(Path(__file__).resolve().parent.parent.parent),
        input_tables=[base_table, "mart_model_selection_run", "mart_feature_drift_root_cause_summary"],
        output_tables=[
            "fact_feature_panel_candidate",
            "mart_feature_drift_mitigation_panel_build",
            "mart_model_selection_run",
        ],
        label_name=selection.get("label_name"),
        perf_summary={
            "output_feature_set_id": output_feature_set_id,
            "model_selection_run_id": model_selection_run_id,
            "base_model_selection_run_id": base_model_selection_run_id,
            "root_cause_run_id": root_cause_run_id,
            "original_features": len(original_features),
            "mitigated_features": len(mitigated),
            "control_features": len(control_features),
            "selected_features": len(selected_features),
            "row_count": summary["row_count"],
            "stock_count": summary["stock_count"],
            "date_count": summary["date_count"],
            "duration_s": duration_s,
        },
    )
    conn.commit()
    return {
        "run_id": run_id,
        "model_selection_run_id": model_selection_run_id,
        "output_feature_set_id": output_feature_set_id,
        "root_cause_run_id": root_cause_run_id,
        "original_features": original_features,
        "mitigated_features": mitigated,
        "transformed_features": transformed_map,
        "copied_features": copied_features,
        "control_features": control_features,
        "selected_features": selected_features,
        "row_count": summary["row_count"],
        "stock_count": summary["stock_count"],
        "date_count": summary["date_count"],
        "min_date": summary["min_date"],
        "max_date": summary["max_date"],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-model-selection-run-id", required=True)
    parser.add_argument("--output-feature-set-id", required=True)
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--model-selection-run-id", default=None)
    parser.add_argument("--base-table", default="fact_feature_panel")
    parser.add_argument("--root-cause-run-id", default=None)
    parser.add_argument("--feature", action="append", default=[])
    parser.add_argument("--transform-types", default=",".join(DEFAULT_TRANSFORMS))
    parser.add_argument("--keep-original", action="store_true")
    parser.add_argument("--include-regime-controls", action="store_true")
    parser.add_argument("--include-market-controls", action="store_true")
    parser.add_argument(
        "--market-control-features",
        default="",
        help="Comma-separated market controls; defaults to hs300_ret_20d,hs300_ret_60d when enabled.",
    )
    parser.add_argument("--start-date", default=None)
    parser.add_argument("--end-date", default=None)
    parser.add_argument("--winsor-low", type=float, default=0.01)
    parser.add_argument("--winsor-high", type=float, default=0.99)
    parser.add_argument("--bucket-count", type=int, default=5)
    parser.add_argument("--min-root-cause-max-psi", type=float, default=0.25)
    parser.add_argument(
        "--recommendations",
        default=",".join(sorted(DEFAULT_RECOMMENDATIONS)),
        help="Comma-separated root-cause recommendations to transform.",
    )
    args = parser.parse_args()
    with get_conn() as conn:
        result = build_feature_drift_mitigation_panel(
            conn,
            base_model_selection_run_id=args.base_model_selection_run_id,
            output_feature_set_id=args.output_feature_set_id,
            run_id=args.run_id,
            model_selection_run_id=args.model_selection_run_id,
            base_table=args.base_table,
            root_cause_run_id=args.root_cause_run_id,
            explicit_features=args.feature,
            transform_types=_parse_csv(args.transform_types),
            keep_original=args.keep_original,
            include_regime_controls=args.include_regime_controls,
            include_market_controls=args.include_market_controls,
            market_control_features=_parse_csv(args.market_control_features) or None,
            start_date=args.start_date,
            end_date=args.end_date,
            winsor_low=args.winsor_low,
            winsor_high=args.winsor_high,
            bucket_count=args.bucket_count,
            min_root_cause_max_psi=args.min_root_cause_max_psi,
            recommendations=set(_parse_csv(args.recommendations)),
        )
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
