#!/usr/bin/env python3
"""Build a challenger-only rank ensemble report from retained TDX features."""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from services.db import get_conn  # noqa: E402
from scripts.build_candidate_feature_panel import CANDIDATE_FEATURE_SET_ID, CANDIDATE_FEATURES  # noqa: E402
from scripts.run_feature_group_ablation import _candidate_features_for_set  # noqa: E402

logger = logging.getLogger("tdx_challenger")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s | %(message)s")


DDL = """
CREATE TABLE IF NOT EXISTS mart_tdx_challenger_report (
    challenger_run_id TEXT PRIMARY KEY,
    feature_set_id TEXT NOT NULL,
    decision_run_id TEXT NOT NULL,
    model_type TEXT NOT NULL,
    selected_features_json TEXT,
    train_window_json TEXT,
    valid_window_json TEXT,
    holdout_window_json TEXT,
    rank_ic DOUBLE,
    long_short_return DOUBLE,
    turnover_adjusted_return DOUBLE,
    max_drawdown DOUBLE,
    baseline_rank_ic DOUBLE,
    baseline_long_short_return DOUBLE,
    promote_to_champion BOOLEAN DEFAULT FALSE,
    notes TEXT,
    built_at TEXT
);

CREATE TABLE IF NOT EXISTS mart_tdx_gpcw_auto_challenger_report (
    challenger_run_id TEXT PRIMARY KEY,
    feature_set_id TEXT NOT NULL,
    decision_run_id TEXT NOT NULL,
    n_features INTEGER,
    rank_ic DOUBLE,
    long_short_return DOUBLE,
    max_drawdown DOUBLE,
    turnover DOUBLE,
    top_features_json TEXT,
    promote_to_champion BOOLEAN DEFAULT FALSE,
    built_at TEXT
);
"""


def ensure_tables(conn: Any) -> None:
    if hasattr(conn, "executescript"):
        conn.executescript(DDL)
    else:
        for stmt in DDL.split(";"):
            stmt = stmt.strip()
            if stmt:
                conn.execute(stmt)


def _rank_score(df: pd.DataFrame, features: list[str], out_col: str) -> pd.DataFrame:
    scored = df.copy()
    rank_cols = []
    for feature in features:
        if feature not in scored.columns:
            continue
        col = f"__rank_{feature}"
        scored[col] = scored.groupby("date")[feature].rank(pct=True)
        rank_cols.append(col)
    scored[out_col] = scored[rank_cols].mean(axis=1) if rank_cols else None
    return scored


def _mean_rank_ic(df: pd.DataFrame, score_col: str, label_col: str = "forward_ret_20d") -> float | None:
    vals = []
    for _, part in df[[score_col, label_col, "date"]].dropna().groupby("date"):
        if len(part) < 10 or part[score_col].nunique() < 2 or part[label_col].nunique() < 2:
            continue
        corr = part[score_col].rank().corr(part[label_col].rank())
        if pd.notna(corr):
            vals.append(float(corr))
    return float(sum(vals) / len(vals)) if vals else None


def _long_short(df: pd.DataFrame, score_col: str, label_col: str = "forward_ret_20d") -> tuple[float | None, float | None]:
    spreads = []
    for _, part in df[["date", score_col, label_col]].dropna().groupby("date"):
        if len(part) < 20 or part[score_col].nunique() < 2:
            continue
        ranked = part.assign(__rank=part[score_col].rank(pct=True))
        top = ranked[ranked["__rank"] >= 0.9]
        bottom = ranked[ranked["__rank"] <= 0.1]
        if not top.empty and not bottom.empty:
            spreads.append(float(top[label_col].mean() - bottom[label_col].mean()))
    if not spreads:
        return None, None
    long_short = float(sum(spreads) / len(spreads))
    equity = 1.0
    peak = 1.0
    max_dd = 0.0
    for ret in spreads:
        equity *= 1.0 + ret
        peak = max(peak, equity)
        max_dd = min(max_dd, equity / peak - 1.0)
    return long_short, float(max_dd)


def _date_windows(dates: list[str]) -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
    n = len(dates)
    train_end = max(int(n * 0.6), 1)
    valid_end = max(int(n * 0.8), train_end + 1)
    train = {"start": dates[0], "end": dates[train_end - 1]}
    valid = {"start": dates[train_end], "end": dates[valid_end - 1]}
    holdout = {"start": dates[valid_end], "end": dates[-1]}
    return train, valid, holdout


def train_tdx_challenger_model(
    conn: Any,
    *,
    feature_set_id: str = CANDIDATE_FEATURE_SET_ID,
    decision_run_id: str,
    run_id: str = "tdx_challenger",
) -> dict[str, Any]:
    ensure_tables(conn)
    keep_rows = conn.execute(
        """
        SELECT feature_name
        FROM mart_feature_retention_decision
        WHERE feature_set_id = ? AND decision_run_id = ? AND decision = 'keep'
        ORDER BY feature_group, feature_name
        """,
        (feature_set_id, decision_run_id),
    ).fetchall()
    selected = [row[0] for row in keep_rows]
    if not selected:
        raise RuntimeError(f"no keep features for decision_run_id={decision_run_id}")

    candidate_features = _candidate_features_for_set(conn, feature_set_id)
    baseline_features = [f for f in CANDIDATE_FEATURES if f in candidate_features] or candidate_features[:20]
    cols = ["stock_code", "date", "forward_ret_20d", *selected, *baseline_features]
    table_cols = {row[1] for row in conn.execute("PRAGMA table_info(fact_feature_panel_candidate)").fetchall()}
    cols = list(dict.fromkeys([col for col in cols if col in table_cols]))
    cursor = conn.execute(
        f"SELECT {', '.join(cols)} FROM fact_feature_panel_candidate WHERE feature_set_id = ? AND forward_ret_20d IS NOT NULL",
        (feature_set_id,),
    )
    df = cursor.df() if hasattr(cursor, "df") else pd.DataFrame(cursor.fetchall(), columns=[d[0] for d in cursor.description])
    dates = sorted(str(d) for d in df["date"].dropna().unique())
    train_window, valid_window, holdout_window = _date_windows(dates)
    holdout = df[(df["date"].astype(str) >= holdout_window["start"]) & (df["date"].astype(str) <= holdout_window["end"])]
    challenger = _rank_score(holdout, selected, "__challenger_score")
    baseline = _rank_score(challenger, [f for f in baseline_features if f in challenger.columns], "__baseline_score")
    rank_ic = _mean_rank_ic(baseline, "__challenger_score")
    baseline_rank_ic = _mean_rank_ic(baseline, "__baseline_score")
    long_short_return, max_drawdown = _long_short(baseline, "__challenger_score")
    baseline_long_short, _ = _long_short(baseline, "__baseline_score")
    turnover_adjusted = None if long_short_return is None else long_short_return - 0.001
    built_at = datetime.utcnow().isoformat(timespec="seconds")
    conn.execute(
        """
        INSERT OR REPLACE INTO mart_tdx_challenger_report
        (challenger_run_id, feature_set_id, decision_run_id, model_type,
         selected_features_json, train_window_json, valid_window_json,
         holdout_window_json, rank_ic, long_short_return,
         turnover_adjusted_return, max_drawdown, baseline_rank_ic,
         baseline_long_short_return, promote_to_champion, notes, built_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_id,
            feature_set_id,
            decision_run_id,
            "rank_ensemble",
            json.dumps(selected, ensure_ascii=False),
            json.dumps(train_window, ensure_ascii=False),
            json.dumps(valid_window, ensure_ascii=False),
            json.dumps(holdout_window, ensure_ascii=False),
            rank_ic,
            long_short_return,
            turnover_adjusted,
            max_drawdown,
            baseline_rank_ic,
            baseline_long_short,
            False,
            "challenger-only report; production champion untouched",
            built_at,
        ),
    )
    if feature_set_id.startswith("tdx_gpcw_auto"):
        top_rows = conn.execute(
            """
            WITH latest_score AS (
                SELECT run_id
                FROM mart_tdx_gpcw_auto_feature_score
                WHERE feature_set_id = ?
                ORDER BY built_at DESC
                LIMIT 1
            ),
            ranked AS (
                SELECT feature_name, selected, rank_ic
                FROM mart_tdx_gpcw_auto_feature_score
                WHERE feature_set_id = ?
                  AND run_id = (SELECT run_id FROM latest_score)
                ORDER BY selected DESC, abs(COALESCE(rank_ic, 0)) DESC, feature_name
                LIMIT 20
            )
            SELECT q.feature_name, ANY_VALUE(q.feature_family) AS feature_family,
                   ANY_VALUE(q.field_key) AS field_key,
                   ANY_VALUE(s.zh_name) AS zh_name,
                   ANY_VALUE(r.selected) AS selected,
                   ANY_VALUE(r.rank_ic) AS rank_ic
            FROM ranked r
            JOIN fact_tdx_gpcw_auto_feature_quarterly q
              ON q.feature_name = r.feature_name
            LEFT JOIN dim_tdx_gpcw_field_semantic s ON s.field_key = q.field_key
            WHERE q.feature_set_id = ?
            GROUP BY q.feature_name
            """,
            (feature_set_id, feature_set_id, feature_set_id.replace("_pit", "")),
        ).fetchall()
        top_features = [
            {
                "feature_name": row["feature_name"],
                "feature_family": row["feature_family"],
                "field_key": row["field_key"],
                "zh_name": row["zh_name"],
                "selected": bool(row["selected"]),
                "rank_ic": row["rank_ic"],
            }
            for row in top_rows
        ]
        conn.execute(
            """
            INSERT OR REPLACE INTO mart_tdx_gpcw_auto_challenger_report
            (challenger_run_id, feature_set_id, decision_run_id, n_features,
             rank_ic, long_short_return, max_drawdown, turnover,
             top_features_json, promote_to_champion, built_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                feature_set_id,
                decision_run_id,
                len(selected),
                rank_ic,
                long_short_return,
                max_drawdown,
                None,
                json.dumps(top_features, ensure_ascii=False),
                False,
                built_at,
            ),
        )
    from services.schema_versions import record_actual_version
    record_actual_version(conn, "mart_tdx_challenger_report")
    conn.commit()
    return {
        "challenger_run_id": run_id,
        "feature_set_id": feature_set_id,
        "decision_run_id": decision_run_id,
        "selected_features": selected,
        "rank_ic": rank_ic,
        "long_short_return": long_short_return,
        "baseline_rank_ic": baseline_rank_ic,
        "promote_to_champion": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--feature-set-id", default=CANDIDATE_FEATURE_SET_ID)
    parser.add_argument("--decision-run-id", required=True)
    parser.add_argument("--run-id", default="tdx_challenger")
    args = parser.parse_args()

    conn = get_conn()
    try:
        result = train_tdx_challenger_model(
            conn,
            feature_set_id=args.feature_set_id,
            decision_run_id=args.decision_run_id,
            run_id=args.run_id,
        )
        logger.info("TDX challenger report: %s", result)
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
