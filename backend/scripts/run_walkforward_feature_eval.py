#!/usr/bin/env python3
"""Walk-forward evaluation for candidate TDX feature sets."""
from __future__ import annotations

import argparse
import logging
import math
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.db import get_conn  # noqa: E402
from scripts.build_candidate_feature_panel import CANDIDATE_FEATURE_SET_ID  # noqa: E402
from scripts.run_feature_group_ablation import (  # noqa: E402
    FEATURE_GROUPS,
    LABEL_COLUMNS,
    _candidate_features_for_set,
    _feature_group_map_for_set,
    _group_by_date,
    _load_candidate_panel,
    _pearson,
    _rank_percentiles,
    _to_float,
)

logger = logging.getLogger("walkforward_feature_eval")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s | %(message)s")


DDL = """
CREATE TABLE IF NOT EXISTS mart_candidate_walkforward_eval (
    run_id TEXT NOT NULL,
    feature_set_id TEXT NOT NULL,
    fold_id TEXT NOT NULL,
    train_start TEXT,
    train_end TEXT,
    valid_start TEXT,
    valid_end TEXT,
    holdout_start TEXT,
    holdout_end TEXT,
    feature_name TEXT NOT NULL,
    feature_group TEXT,
    rank_ic DOUBLE,
    icir DOUBLE,
    same_sign BOOLEAN,
    long_short_return DOUBLE,
    turnover DOUBLE,
    turnover_adjusted_return DOUBLE,
    max_drawdown DOUBLE,
    label_name TEXT NOT NULL,
    built_at TEXT,
    PRIMARY KEY (run_id, fold_id, feature_name, label_name)
);
"""


def _quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def ensure_tables(conn: Any) -> None:
    if hasattr(conn, "executescript"):
        conn.executescript(DDL)
    else:
        for stmt in DDL.split(";"):
            stmt = stmt.strip()
            if stmt:
                conn.execute(stmt)


def _feature_group(feature: str) -> str:
    for group, features in FEATURE_GROUPS.items():
        if feature in features:
            return group
    return "other"


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _sample_std(values: list[float]) -> float | None:
    if len(values) < 2:
        return None
    mean = sum(values) / len(values)
    return (sum((value - mean) ** 2 for value in values) / (len(values) - 1)) ** 0.5


def _daily_rank_ics(part: list[dict[str, Any]], feature: str, label: str) -> list[float]:
    vals: list[float] = []
    for by_date in _group_by_date(part).values():
        pairs = [
            (feature_value, label_value)
            for row in by_date
            if (feature_value := _to_float(row.get(feature))) is not None
            if (label_value := _to_float(row.get(label))) is not None
        ]
        if len(pairs) < 10:
            continue
        feature_values = [feature_value for feature_value, _ in pairs]
        label_values = [label_value for _, label_value in pairs]
        if len(set(feature_values)) < 2 or len(set(label_values)) < 2:
            continue
        corr = _pearson(_rank_percentiles(feature_values), _rank_percentiles(label_values))
        if corr is not None:
            vals.append(float(corr))
    return vals


def _long_short_and_turnover(
    part: list[dict[str, Any]],
    feature: str,
    label: str,
) -> tuple[float | None, float | None, float | None]:
    spreads: list[float] = []
    top_sets: list[set[str]] = []
    for by_date in _group_by_date(part).values():
        rows = [
            (str(row.get("stock_code") or ""), feature_value, label_value)
            for row in by_date
            if (feature_value := _to_float(row.get(feature))) is not None
            if (label_value := _to_float(row.get(label))) is not None
        ]
        if len(rows) < 20:
            continue
        feature_values = [feature_value for _, feature_value, _ in rows]
        if len(set(feature_values)) < 2:
            continue
        ranked = [
            (rows[idx][0], rows[idx][2], rank)
            for idx, rank in enumerate(_rank_percentiles(feature_values))
        ]
        top = [(code, label_value) for code, label_value, rank in ranked if rank >= 0.9]
        bottom = [label_value for _, label_value, rank in ranked if rank <= 0.1]
        if not top or not bottom:
            continue
        top_mean = _mean([label_value for _, label_value in top])
        bottom_mean = _mean(bottom)
        if top_mean is None or bottom_mean is None:
            continue
        spreads.append(float(top_mean - bottom_mean))
        top_sets.append({code for code, _ in top})
    if not spreads:
        return None, None, None
    turnovers = []
    for prev, cur in zip(top_sets, top_sets[1:]):
        union = prev | cur
        if union:
            turnovers.append(1.0 - len(prev & cur) / len(union))
    turnover = _mean(turnovers) if turnovers else 0.0
    long_short = _mean(spreads)
    if long_short is None:
        return None, turnover, None
    return long_short, turnover, long_short - 0.001 * turnover


def _max_drawdown(returns: list[float]) -> float | None:
    if not returns:
        return None
    equity = 1.0
    peak = 1.0
    max_dd = 0.0
    for ret in returns:
        equity *= 1.0 + ret
        peak = max(peak, equity)
        max_dd = min(max_dd, equity / peak - 1.0)
    return float(max_dd)


def _fold_rank_ic_fast(part: list[dict[str, Any]], feature: str, label: str) -> float | None:
    pairs = [
        (feature_value, label_value)
        for row in part
        if (feature_value := _to_float(row.get(feature))) is not None
        if (label_value := _to_float(row.get(label))) is not None
    ]
    if len(pairs) < 50:
        return None
    feature_values = [feature_value for feature_value, _ in pairs]
    label_values = [label_value for _, label_value in pairs]
    if len(set(feature_values)) < 2 or len(set(label_values)) < 2:
        return None
    corr = _pearson(_rank_percentiles(feature_values), _rank_percentiles(label_values))
    return float(corr) if corr is not None else None


def _long_short_fast(
    part: list[dict[str, Any]],
    feature: str,
    label: str,
) -> tuple[float | None, float | None, float | None]:
    pairs = [
        (feature_value, label_value)
        for row in part
        if (feature_value := _to_float(row.get(feature))) is not None
        if (label_value := _to_float(row.get(label))) is not None
    ]
    if len(pairs) < 100:
        return None, None, None
    feature_values = [feature_value for feature_value, _ in pairs]
    if len(set(feature_values)) < 2:
        return None, None, None
    ranked = [
        (pairs[idx][1], rank)
        for idx, rank in enumerate(_rank_percentiles(feature_values))
    ]
    top = [label_value for label_value, rank in ranked if rank >= 0.9]
    bottom = [label_value for label_value, rank in ranked if rank <= 0.1]
    if not top or not bottom:
        return None, None, None
    long_short = (_mean(top) or 0.0) - (_mean(bottom) or 0.0)
    return long_short, None, None if long_short is None else long_short - 0.001


def _fold_ranges(dates: list[str], folds: int) -> list[dict[str, str]]:
    ranges = []
    folds = max(1, min(int(folds), len(dates)))
    for idx in range(folds):
        start = idx * len(dates) // folds
        end = (idx + 1) * len(dates) // folds
        holdout_dates = dates[start:end]
        if not holdout_dates:
            continue
        mid = max(1, len(holdout_dates) // 2)
        valid_dates = holdout_dates[:mid]
        final_dates = holdout_dates[mid:] or holdout_dates[-1:]
        train_dates = dates[:start] or valid_dates[:1]
        ranges.append(
            {
                "fold_id": f"fold_{idx + 1}",
                "train_start": train_dates[0],
                "train_end": train_dates[-1],
                "valid_start": valid_dates[0],
                "valid_end": valid_dates[-1],
                "holdout_start": final_dates[0],
                "holdout_end": final_dates[-1],
            }
        )
    return ranges


def _run_sql_walkforward(
    conn: Any,
    *,
    feature_set_id: str,
    folds: int,
    run_id: str | None,
) -> dict[str, Any]:
    table_cols = {
        row[0]
        for row in conn.execute("DESCRIBE fact_feature_panel_candidate").fetchall()
    }
    usable_features = [
        feature for feature in _candidate_features_for_set(conn, feature_set_id)
        if feature in table_cols
    ]
    labels = [label for label in LABEL_COLUMNS if label in table_cols]
    dates = [
        str(row["date"])
        for row in conn.execute(
            """
            SELECT DISTINCT date
            FROM fact_feature_panel_candidate
            WHERE feature_set_id = ? AND forward_ret_20d IS NOT NULL
            ORDER BY date
            """,
            (feature_set_id,),
        ).fetchall()
    ]
    ranges = _fold_ranges(dates, folds)
    feature_group_map = _feature_group_map_for_set(conn, feature_set_id)
    run_id = run_id or f"wf_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"
    built_at = datetime.utcnow().isoformat(timespec="seconds")
    rows = []
    for fold in ranges:
        for label in labels:
            corr_expr = ", ".join(
                f"corr({_quote_ident(feature)}, {_quote_ident(label)}) AS {_quote_ident(feature)}"
                for feature in usable_features
            )
            corr_row = conn.execute(
                f"""
                SELECT {corr_expr}
                FROM fact_feature_panel_candidate
                WHERE feature_set_id = ?
                  AND date >= ?
                  AND date <= ?
                  AND {_quote_ident(label)} IS NOT NULL
                """,
                (feature_set_id, fold["holdout_start"], fold["holdout_end"]),
            ).fetchone()
            for feature in usable_features:
                rank_ic = _to_float(corr_row[feature]) if corr_row else None
                rows.append((
                    run_id,
                    feature_set_id,
                    fold["fold_id"],
                    fold["train_start"],
                    fold["train_end"],
                    fold["valid_start"],
                    fold["valid_end"],
                    fold["holdout_start"],
                    fold["holdout_end"],
                    feature,
                    feature_group_map.get(feature, _feature_group(feature)),
                    rank_ic,
                    None,
                    None if rank_ic is None else bool(rank_ic > 0),
                    None,
                    None,
                    None,
                    None,
                    label,
                    built_at,
                ))
    conn.executemany(
        """
        INSERT OR REPLACE INTO mart_candidate_walkforward_eval
        (run_id, feature_set_id, fold_id, train_start, train_end, valid_start,
         valid_end, holdout_start, holdout_end, feature_name, feature_group,
         rank_ic, icir, same_sign, long_short_return, turnover,
         turnover_adjusted_return, max_drawdown, label_name, built_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    from services.schema_versions import record_actual_version
    record_actual_version(conn, "mart_candidate_walkforward_eval")
    conn.commit()
    return {
        "run_id": run_id,
        "feature_set_id": feature_set_id,
        "folds": len(ranges),
        "features": len(usable_features),
        "labels": labels,
        "rows": len(rows),
        "method": "sql_corr",
    }


def run_walkforward_feature_eval(
    conn: Any,
    *,
    feature_set_id: str = CANDIDATE_FEATURE_SET_ID,
    folds: int = 4,
    run_id: str | None = None,
    method: str = "full",
) -> dict[str, Any]:
    ensure_tables(conn)
    if method == "sql" or feature_set_id.startswith("tdx_gpcw_auto"):
        return _run_sql_walkforward(
            conn,
            feature_set_id=feature_set_id,
            folds=folds,
            run_id=run_id,
        )
    records = _load_candidate_panel(conn, feature_set_id)
    if not records:
        raise RuntimeError(f"candidate panel empty for feature_set_id={feature_set_id}")
    dates = sorted({str(row.get("date")) for row in records if row.get("date") is not None})
    ranges = _fold_ranges(dates, folds)
    feature_group_map = _feature_group_map_for_set(conn, feature_set_id)
    panel_cols = set(records[0].keys())
    usable_features = [
        f for f in _candidate_features_for_set(conn, feature_set_id)
        if f in panel_cols and any(_to_float(row.get(f)) is not None for row in records)
    ]
    labels = [label for label in LABEL_COLUMNS if label in panel_cols]
    run_id = run_id or f"wf_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"
    built_at = datetime.utcnow().isoformat(timespec="seconds")
    rows = []
    fast_auto = feature_set_id.startswith("tdx_gpcw_auto")
    for fold in ranges:
        part = [
            row for row in records
            if fold["holdout_start"] <= str(row.get("date")) <= fold["holdout_end"]
        ]
        for feature in usable_features:
            for label in labels:
                if fast_auto:
                    rank_ic = _fold_rank_ic_fast(part, feature, label)
                    icir = None
                    long_short, turnover, adjusted = _long_short_fast(part, feature, label)
                else:
                    ics = _daily_rank_ics(part, feature, label)
                    rank_ic = float(sum(ics) / len(ics)) if ics else None
                    icir = None
                    if ics and len(ics) > 1:
                        std = _sample_std(ics)
                        icir = float(rank_ic / std * math.sqrt(252)) if std else None
                    long_short, turnover, adjusted = _long_short_and_turnover(part, feature, label)
                rows.append((
                    run_id,
                    feature_set_id,
                    fold["fold_id"],
                    fold["train_start"],
                    fold["train_end"],
                    fold["valid_start"],
                    fold["valid_end"],
                    fold["holdout_start"],
                    fold["holdout_end"],
                    feature,
                    feature_group_map.get(feature, _feature_group(feature)),
                    rank_ic,
                    icir,
                    None if rank_ic is None else bool(rank_ic > 0),
                    long_short,
                    turnover,
                    adjusted,
                    _max_drawdown([long_short] if long_short is not None else []),
                    label,
                    built_at,
                ))
    conn.executemany(
        """
        INSERT OR REPLACE INTO mart_candidate_walkforward_eval
        (run_id, feature_set_id, fold_id, train_start, train_end, valid_start,
         valid_end, holdout_start, holdout_end, feature_name, feature_group,
         rank_ic, icir, same_sign, long_short_return, turnover,
         turnover_adjusted_return, max_drawdown, label_name, built_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    conn.commit()
    return {
        "run_id": run_id,
        "feature_set_id": feature_set_id,
        "folds": len(ranges),
        "features": len(usable_features),
        "labels": labels,
        "rows": len(rows),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--feature-set-id", default=CANDIDATE_FEATURE_SET_ID)
    parser.add_argument("--folds", type=int, default=4)
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--method", choices=["full", "sql"], default="full")
    args = parser.parse_args()

    conn = get_conn()
    try:
        result = run_walkforward_feature_eval(
            conn,
            feature_set_id=args.feature_set_id,
            folds=args.folds,
            run_id=args.run_id,
            method=args.method,
        )
        logger.info("walk-forward feature eval: %s", result)
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
