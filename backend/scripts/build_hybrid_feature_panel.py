#!/usr/bin/env python3
"""Build a joined candidate panel from production and supplemental feature runs."""
from __future__ import annotations

import argparse
import json
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


LABEL_COLUMNS = [
    "forward_ret_5d",
    "forward_ret_10d",
    "forward_ret_20d",
    "forward_ret_60d",
    "forward_ret_90d",
    "follow_net_return_5d",
    "follow_net_return_10d",
    "follow_net_return_20d",
    "follow_net_return_60d",
    "follow_net_return_90d",
]

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

CREATE TABLE IF NOT EXISTS mart_hybrid_feature_panel_build (
    run_id TEXT PRIMARY KEY,
    output_feature_set_id TEXT NOT NULL,
    base_table TEXT NOT NULL,
    base_feature_set_id TEXT,
    base_model_selection_run_id TEXT NOT NULL,
    extra_table TEXT NOT NULL,
    extra_feature_set_id TEXT,
    extra_model_selection_run_id TEXT NOT NULL,
    model_selection_run_id TEXT NOT NULL,
    selected_features_json TEXT,
    base_features_json TEXT,
    extra_features_json TEXT,
    labels_json TEXT,
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


def _parse_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def _ensure_candidate_columns(conn: Any, columns: list[str], *, regime: bool) -> None:
    conn.execute("ALTER TABLE fact_feature_panel_candidate ADD COLUMN IF NOT EXISTS close REAL")
    for label in LABEL_COLUMNS:
        conn.execute(f"ALTER TABLE fact_feature_panel_candidate ADD COLUMN IF NOT EXISTS {_quote_ident(label)} REAL")
    if regime:
        conn.execute("ALTER TABLE fact_feature_panel_candidate ADD COLUMN IF NOT EXISTS regime_flag TEXT")
    for col in columns:
        conn.execute(f"ALTER TABLE fact_feature_panel_candidate ADD COLUMN IF NOT EXISTS {_quote_ident(col)} REAL")


def _feature_set_filter(
    columns: set[str],
    alias: str,
    feature_set_id: str | None,
    params: list[Any],
) -> str | None:
    if not feature_set_id:
        return None
    if "feature_set_id" not in columns:
        raise RuntimeError(f"{alias} table has no feature_set_id column")
    params.append(feature_set_id)
    return f"{alias}.feature_set_id = ?"


def build_hybrid_feature_panel(
    conn: Any,
    *,
    base_model_selection_run_id: str,
    extra_model_selection_run_id: str,
    output_feature_set_id: str,
    run_id: str | None = None,
    model_selection_run_id: str | None = None,
    base_table: str = "fact_feature_panel",
    extra_table: str = "fact_feature_panel_candidate",
    base_feature_set_id: str | None = None,
    extra_feature_set_id: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict[str, Any]:
    ensure_tables(conn)
    started_at = utc_now_iso()
    t0 = time.perf_counter()
    built_at = datetime.utcnow().isoformat(timespec="seconds")
    run_id = run_id or f"hybrid_feature_panel_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"
    model_selection_run_id = model_selection_run_id or run_id

    base_selection = load_model_selection_run(conn, base_model_selection_run_id)
    extra_selection = load_model_selection_run(conn, extra_model_selection_run_id)
    base_features = list(base_selection["selected_features"])
    extra_features = list(extra_selection["selected_features"])
    if extra_feature_set_id is None and extra_selection.get("feature_set_id") != "production_registry":
        extra_feature_set_id = extra_selection.get("feature_set_id")

    overlap = sorted(set(base_features) & set(extra_features))
    if overlap:
        raise RuntimeError(f"hybrid feature names overlap: {overlap}")

    base_cols = _table_columns(conn, base_table)
    extra_cols = _table_columns(conn, extra_table)
    missing_base = [feature for feature in base_features if feature not in base_cols]
    missing_extra = [feature for feature in extra_features if feature not in extra_cols]
    if missing_base:
        raise RuntimeError(f"base table missing selected features: {missing_base}")
    if missing_extra:
        raise RuntimeError(f"extra table missing selected features: {missing_extra}")
    if "stock_code" not in base_cols or "date" not in base_cols:
        raise RuntimeError("base table must include stock_code and date")
    if "stock_code" not in extra_cols or "date" not in extra_cols:
        raise RuntimeError("extra table must include stock_code and date")

    labels = [label for label in LABEL_COLUMNS if label in base_cols]
    if "forward_ret_20d" not in labels:
        raise RuntimeError("base table must include forward_ret_20d")
    has_regime = "regime_flag" in base_cols
    selected_features = [*base_features, *extra_features]
    _ensure_candidate_columns(conn, selected_features, regime=has_regime)

    base_where: list[str] = []
    params: list[Any] = []
    fs_filter = _feature_set_filter(base_cols, "b", base_feature_set_id, params)
    if fs_filter:
        base_where.append(fs_filter)
    if start_date:
        base_where.append("b.date >= ?")
        params.append(start_date)
    if end_date:
        base_where.append("b.date <= ?")
        params.append(end_date)
    base_where_sql = ("WHERE " + " AND ".join(base_where)) if base_where else ""

    extra_join_filters: list[str] = ["e.stock_code = b.stock_code", "e.date = b.date"]
    join_params: list[Any] = []
    extra_filter = _feature_set_filter(extra_cols, "e", extra_feature_set_id, join_params)
    if extra_filter:
        extra_join_filters.append(extra_filter)
    params = [*join_params, *params]

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
        "b.stock_code",
        "CAST(b.date AS VARCHAR) AS date",
    ]
    if include_close:
        select_cols.append("CAST(b.close AS REAL) AS close")
    select_cols.extend(f"CAST(b.{_quote_ident(label)} AS REAL) AS {_quote_ident(label)}" for label in labels)
    if has_regime:
        select_cols.append("CAST(b.regime_flag AS TEXT) AS regime_flag")
    select_cols.extend(f"CAST(b.{_quote_ident(feature)} AS REAL) AS {_quote_ident(feature)}" for feature in base_features)
    select_cols.extend(f"CAST(e.{_quote_ident(feature)} AS REAL) AS {_quote_ident(feature)}" for feature in extra_features)
    select_cols.append("? AS built_at")

    conn.execute(
        f"""
        INSERT OR REPLACE INTO fact_feature_panel_candidate
        ({', '.join(_quote_ident(col) for col in insert_cols)})
        SELECT {', '.join(select_cols)}
          FROM {_quote_relation(base_table)} b
          LEFT JOIN {_quote_relation(extra_table)} e
            ON {' AND '.join(extra_join_filters)}
          {base_where_sql}
        """,
        [output_feature_set_id, built_at, *params],
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
            "hybrid_feature_panel_builder",
            base_selection.get("label_name") or extra_selection.get("label_name") or "forward_ret_20d",
            None,
            json.dumps(selected_features, ensure_ascii=False),
            json.dumps([], ensure_ascii=False),
            0,
            False,
            json.dumps(
                {
                    "base_model_selection_run_id": base_model_selection_run_id,
                    "extra_model_selection_run_id": extra_model_selection_run_id,
                    "base_table": base_table,
                    "extra_table": extra_table,
                    "base_feature_set_id": base_feature_set_id,
                    "extra_feature_set_id": extra_feature_set_id,
                    "output_feature_set_id": output_feature_set_id,
                    "message": "hybrid candidate panel; no model trained or promoted",
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            built_at,
        ),
    )
    conn.execute("DELETE FROM mart_hybrid_feature_panel_build WHERE run_id = ?", (run_id,))
    conn.execute(
        """
        INSERT INTO mart_hybrid_feature_panel_build
        (run_id, output_feature_set_id, base_table, base_feature_set_id,
         base_model_selection_run_id, extra_table, extra_feature_set_id,
         extra_model_selection_run_id, model_selection_run_id,
         selected_features_json, base_features_json, extra_features_json,
         labels_json, row_count, stock_count, date_count, min_date, max_date,
         built_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_id,
            output_feature_set_id,
            base_table,
            base_feature_set_id,
            base_model_selection_run_id,
            extra_table,
            extra_feature_set_id,
            extra_model_selection_run_id,
            model_selection_run_id,
            json.dumps(selected_features, ensure_ascii=False),
            json.dumps(base_features, ensure_ascii=False),
            json.dumps(extra_features, ensure_ascii=False),
            json.dumps(labels, ensure_ascii=False),
            summary["row_count"],
            summary["stock_count"],
            summary["date_count"],
            summary["min_date"],
            summary["max_date"],
            built_at,
        ),
    )
    record_actual_version(conn, "fact_feature_panel_candidate")
    record_actual_version(conn, "mart_hybrid_feature_panel_build")
    record_actual_version(conn, "mart_model_selection_run")
    duration_s = time.perf_counter() - t0
    record_pipeline_run(
        conn,
        run_id=run_id,
        pipeline_name="build_hybrid_feature_panel",
        status="success",
        started_at=started_at,
        ended_at=utc_now_iso(),
        duration_s=duration_s,
        commit_sha=git_commit_sha(Path(__file__).resolve().parent.parent.parent),
        input_tables=[base_table, extra_table, "mart_model_selection_run"],
        output_tables=["fact_feature_panel_candidate", "mart_hybrid_feature_panel_build", "mart_model_selection_run"],
        label_name=base_selection.get("label_name") or extra_selection.get("label_name"),
        perf_summary={
            "output_feature_set_id": output_feature_set_id,
            "model_selection_run_id": model_selection_run_id,
            "base_features": len(base_features),
            "extra_features": len(extra_features),
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
        "base_features": base_features,
        "extra_features": extra_features,
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
    parser.add_argument("--extra-model-selection-run-id", required=True)
    parser.add_argument("--output-feature-set-id", required=True)
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--model-selection-run-id", default=None)
    parser.add_argument("--base-table", default="fact_feature_panel")
    parser.add_argument("--extra-table", default="fact_feature_panel_candidate")
    parser.add_argument("--base-feature-set-id", default=None)
    parser.add_argument("--extra-feature-set-id", default=None)
    parser.add_argument("--start-date", default=None)
    parser.add_argument("--end-date", default=None)
    args = parser.parse_args()
    with get_conn() as conn:
        result = build_hybrid_feature_panel(
            conn,
            base_model_selection_run_id=args.base_model_selection_run_id,
            extra_model_selection_run_id=args.extra_model_selection_run_id,
            output_feature_set_id=args.output_feature_set_id,
            run_id=args.run_id,
            model_selection_run_id=args.model_selection_run_id,
            base_table=args.base_table,
            extra_table=args.extra_table,
            base_feature_set_id=args.base_feature_set_id,
            extra_feature_set_id=args.extra_feature_set_id,
            start_date=args.start_date,
            end_date=args.end_date,
        )
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
