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

import pandas as pd

from services.db import get_conn  # noqa: E402
from scripts.build_candidate_feature_panel import CANDIDATE_FEATURE_SET_ID  # noqa: E402
from scripts.run_feature_group_ablation import (  # noqa: E402
    FEATURE_GROUPS,
    LABEL_COLUMNS,
    _candidate_features_for_set,
    _feature_group_map_for_set,
    _load_candidate_panel,
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


def _daily_rank_ics(part: pd.DataFrame, feature: str, label: str) -> list[float]:
    vals: list[float] = []
    for _, by_date in part[["date", feature, label]].dropna().groupby("date"):
        if len(by_date) < 10:
            continue
        if by_date[feature].nunique() < 2 or by_date[label].nunique() < 2:
            continue
        corr = by_date[feature].rank().corr(by_date[label].rank())
        if pd.notna(corr):
            vals.append(float(corr))
    return vals


def _long_short_and_turnover(part: pd.DataFrame, feature: str, label: str) -> tuple[float | None, float | None, float | None]:
    spreads: list[float] = []
    top_sets: list[set[str]] = []
    for _, by_date in part[["stock_code", "date", feature, label]].dropna().groupby("date"):
        if len(by_date) < 20 or by_date[feature].nunique() < 2:
            continue
        ranked = by_date.assign(__rank=by_date[feature].rank(pct=True))
        top = ranked[ranked["__rank"] >= 0.9]
        bottom = ranked[ranked["__rank"] <= 0.1]
        if top.empty or bottom.empty:
            continue
        spreads.append(float(top[label].mean() - bottom[label].mean()))
        top_sets.append(set(str(code) for code in top["stock_code"]))
    if not spreads:
        return None, None, None
    turnovers = []
    for prev, cur in zip(top_sets, top_sets[1:]):
        union = prev | cur
        if union:
            turnovers.append(1.0 - len(prev & cur) / len(union))
    turnover = float(sum(turnovers) / len(turnovers)) if turnovers else 0.0
    long_short = float(sum(spreads) / len(spreads))
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


def _fold_rank_ic_fast(part: pd.DataFrame, feature: str, label: str) -> float | None:
    values = part[[feature, label]].dropna()
    if len(values) < 50 or values[feature].nunique() < 2 or values[label].nunique() < 2:
        return None
    corr = values[feature].rank().corr(values[label].rank())
    return float(corr) if pd.notna(corr) else None


def _long_short_fast(part: pd.DataFrame, feature: str, label: str) -> tuple[float | None, float | None, float | None]:
    values = part[[feature, label]].dropna()
    if len(values) < 100 or values[feature].nunique() < 2:
        return None, None, None
    ranked = values.assign(__rank=values[feature].rank(pct=True))
    top = ranked[ranked["__rank"] >= 0.9]
    bottom = ranked[ranked["__rank"] <= 0.1]
    if top.empty or bottom.empty:
        return None, None, None
    long_short = float(top[label].mean() - bottom[label].mean())
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


def _run_auto_sql_walkforward(
    conn: Any,
    *,
    feature_set_id: str,
    folds: int,
    run_id: str | None,
) -> dict[str, Any]:
    table_cols = {row[0] for row in conn.execute("DESCRIBE fact_feature_panel_candidate").fetchall()}
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
                rank_ic = corr_row[feature] if corr_row else None
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
                    None if rank_ic is None or pd.isna(rank_ic) else float(rank_ic),
                    None,
                    None if rank_ic is None or pd.isna(rank_ic) else bool(rank_ic > 0),
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
        "method": "sql_corr_auto",
    }


def run_walkforward_feature_eval(
    conn: Any,
    *,
    feature_set_id: str = CANDIDATE_FEATURE_SET_ID,
    folds: int = 4,
    run_id: str | None = None,
) -> dict[str, Any]:
    ensure_tables(conn)
    if feature_set_id.startswith("tdx_gpcw_auto"):
        return _run_auto_sql_walkforward(
            conn,
            feature_set_id=feature_set_id,
            folds=folds,
            run_id=run_id,
        )
    df = _load_candidate_panel(conn, feature_set_id)
    if df.empty:
        raise RuntimeError(f"candidate panel empty for feature_set_id={feature_set_id}")
    dates = sorted(str(d) for d in df["date"].dropna().unique())
    ranges = _fold_ranges(dates, folds)
    feature_group_map = _feature_group_map_for_set(conn, feature_set_id)
    usable_features = [
        f for f in _candidate_features_for_set(conn, feature_set_id)
        if f in df.columns and df[f].notna().any()
    ]
    labels = [label for label in LABEL_COLUMNS if label in df.columns]
    run_id = run_id or f"wf_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"
    built_at = datetime.utcnow().isoformat(timespec="seconds")
    rows = []
    fast_auto = feature_set_id.startswith("tdx_gpcw_auto")
    for fold in ranges:
        part = df[(df["date"].astype(str) >= fold["holdout_start"]) & (df["date"].astype(str) <= fold["holdout_end"])]
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
                        std = pd.Series(ics).std()
                        icir = float(rank_ic / std * math.sqrt(252)) if std and not pd.isna(std) else None
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
    args = parser.parse_args()

    conn = get_conn()
    try:
        result = run_walkforward_feature_eval(
            conn,
            feature_set_id=args.feature_set_id,
            folds=args.folds,
            run_id=args.run_id,
        )
        logger.info("walk-forward feature eval: %s", result)
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
