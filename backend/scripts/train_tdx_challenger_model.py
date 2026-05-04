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

from services.db import get_conn  # noqa: E402
from scripts.build_candidate_feature_panel import CANDIDATE_FEATURE_SET_ID, CANDIDATE_FEATURES  # noqa: E402

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


def _records_from_cursor(cursor: Any) -> list[dict[str, Any]]:
    names = [desc[0] for desc in (cursor.description or [])]
    return [
        {name: value for name, value in zip(names, row)}
        for row in cursor.fetchall()
    ]


def _is_missing(value: Any) -> bool:
    if value is None:
        return True
    try:
        return value != value
    except Exception:
        return False


def _to_float(value: Any) -> float | None:
    if _is_missing(value):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _rank_percentiles(values: list[float]) -> list[float]:
    n = len(values)
    if n == 0:
        return []
    indexed = sorted(enumerate(values), key=lambda item: item[1])
    ranks = [0.0] * n
    pos = 0
    while pos < n:
        end = pos + 1
        while end < n and indexed[end][1] == indexed[pos][1]:
            end += 1
        avg_rank = ((pos + 1) + end) / 2.0
        percentile = avg_rank / n
        for idx in range(pos, end):
            ranks[indexed[idx][0]] = percentile
        pos = end
    return ranks


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    n = len(xs)
    if n == 0 or n != len(ys):
        return None
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    dx = [value - mean_x for value in xs]
    dy = [value - mean_y for value in ys]
    denom_x = sum(value * value for value in dx)
    denom_y = sum(value * value for value in dy)
    if denom_x <= 0 or denom_y <= 0:
        return None
    return sum(x * y for x, y in zip(dx, dy)) / ((denom_x * denom_y) ** 0.5)


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _group_by_date(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        date = str(row.get("date") or "")
        groups.setdefault(date, []).append(row)
    return groups


def _rank_score(rows: list[dict[str, Any]], features: list[str], out_col: str) -> list[dict[str, Any]]:
    scored = [dict(row) for row in rows]
    groups = _group_by_date(scored)
    for feature in features:
        for group_rows in groups.values():
            indexed_values = [
                (idx, value)
                for idx, row in enumerate(group_rows)
                if (value := _to_float(row.get(feature))) is not None
            ]
            ranks = _rank_percentiles([value for _, value in indexed_values])
            for rank_idx, (row_idx, _) in enumerate(indexed_values):
                group_rows[row_idx].setdefault("__rank_values", []).append(ranks[rank_idx])
    for row in scored:
        ranks = row.pop("__rank_values", [])
        row[out_col] = _mean(ranks)
    return scored


def _mean_rank_ic(
    rows: list[dict[str, Any]],
    score_col: str,
    label_col: str = "forward_ret_20d",
) -> float | None:
    vals = []
    for part in _group_by_date(rows).values():
        pairs = [
            (score, label)
            for row in part
            if (score := _to_float(row.get(score_col))) is not None
            if (label := _to_float(row.get(label_col))) is not None
        ]
        if len(pairs) < 10:
            continue
        scores = [score for score, _ in pairs]
        labels = [label for _, label in pairs]
        if len(set(scores)) < 2 or len(set(labels)) < 2:
            continue
        corr = _pearson(_rank_percentiles(scores), _rank_percentiles(labels))
        if corr is not None:
            vals.append(float(corr))
    return float(sum(vals) / len(vals)) if vals else None


def _long_short(
    rows: list[dict[str, Any]],
    score_col: str,
    label_col: str = "forward_ret_20d",
) -> tuple[float | None, float | None]:
    spreads = []
    for part in _group_by_date(rows).values():
        pairs = [
            (score, label)
            for row in part
            if (score := _to_float(row.get(score_col))) is not None
            if (label := _to_float(row.get(label_col))) is not None
        ]
        if len(pairs) < 20:
            continue
        scores = [score for score, _ in pairs]
        if len(set(scores)) < 2:
            continue
        ranks = _rank_percentiles(scores)
        top = [pairs[idx][1] for idx, rank in enumerate(ranks) if rank >= 0.9]
        bottom = [pairs[idx][1] for idx, rank in enumerate(ranks) if rank <= 0.1]
        if top and bottom:
            spreads.append(float((sum(top) / len(top)) - (sum(bottom) / len(bottom))))
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


def _auto_source_feature_set_id(feature_set_id: str) -> str:
    return feature_set_id[:-4] if feature_set_id.endswith("_pit") else feature_set_id


def _candidate_features_for_set(conn: Any, feature_set_id: str) -> list[str]:
    table_cols = {row[0] for row in conn.execute("DESCRIBE fact_feature_panel_candidate").fetchall()}
    if feature_set_id.startswith("tdx_gpcw_auto"):
        rows = conn.execute(
            """
            SELECT feature_name, COUNT(feature_value) AS n
            FROM fact_tdx_gpcw_auto_feature_quarterly
            WHERE feature_set_id = ?
            GROUP BY feature_name
            ORDER BY n DESC, feature_name
            """,
            (_auto_source_feature_set_id(feature_set_id),),
        ).fetchall()
        features = [row["feature_name"] for row in rows if row["feature_name"] in table_cols]
        if features:
            return features
    return [feature for feature in CANDIDATE_FEATURES if feature in table_cols]


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
    table_cols = {row[0] for row in conn.execute("DESCRIBE fact_feature_panel_candidate").fetchall()}
    cols = list(dict.fromkeys([col for col in cols if col in table_cols]))
    cursor = conn.execute(
        f"""
        SELECT {', '.join(cols)}
        FROM fact_feature_panel_candidate
        WHERE feature_set_id = ? AND forward_ret_20d IS NOT NULL
        """,
        (feature_set_id,),
    )
    records = _records_from_cursor(cursor)
    dates = sorted({str(row.get("date")) for row in records if not _is_missing(row.get("date"))})
    if not dates:
        raise RuntimeError(f"fact_feature_panel_candidate has no scored rows for feature_set_id={feature_set_id}")
    train_window, valid_window, holdout_window = _date_windows(dates)
    holdout = [
        row for row in records
        if holdout_window["start"] <= str(row.get("date")) <= holdout_window["end"]
    ]
    challenger = _rank_score(holdout, selected, "__challenger_score")
    baseline = _rank_score(
        challenger,
        [feature for feature in baseline_features if feature in table_cols],
        "__baseline_score",
    )
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
