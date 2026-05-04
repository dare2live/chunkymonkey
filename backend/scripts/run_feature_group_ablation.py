#!/usr/bin/env python3
"""Lightweight feature-group ablation for candidate-only TDX features."""
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
from scripts.build_candidate_feature_panel import (  # noqa: E402
    CANDIDATE_FEATURE_SET_ID,
    CANDIDATE_FEATURES,
)

logger = logging.getLogger("feature_group_ablation")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s | %(message)s")


FEATURE_GROUPS = {
    "ownership_tdx_f10": [
        "top10_concentration_change",
        "common_holder_network_count",
        "fund_holding_shares_tdx_f10",
        "fund_holding_float_a_ratio_tdx_f10",
    ],
    "holder_count_chip": [
        "holder_count_change_pct_tdx",
        "avg_float_shares_change_pct_tdx",
        "holder_count_acceleration_tdx",
    ],
    "institution_gpcw": [
        "tdx_inst_total_shares_qoq",
        "national_team_shares_qoq",
        "qfii_shares_qoq",
        "fund_shares_qoq",
        "social_security_shares_qoq",
    ],
    "fundamental_quality": [
        "contract_liabilities_to_revenue",
        "ocf_to_profit_tdx",
        "receivables_to_revenue",
        "inventory_to_revenue",
    ],
    "forecast_express": [
        "forecast_profit_yoy_mid",
        "forecast_range_width",
        "express_net_profit_yoy",
    ],
}

LABEL_COLUMNS = ["forward_ret_5d", "forward_ret_10d", "forward_ret_20d", "forward_ret_60d"]
META_COLUMNS = {"feature_set_id", "stock_code", "date", "built_at", *LABEL_COLUMNS}


DDL = """
CREATE TABLE IF NOT EXISTS mart_feature_group_ablation (
    run_id TEXT NOT NULL,
    feature_set_id TEXT NOT NULL,
    group_name TEXT NOT NULL,
    n_features INTEGER,
    rank_ic_full DOUBLE,
    rank_ic_without_group DOUBLE,
    rank_ic_delta DOUBLE,
    rank_ic_5d DOUBLE,
    rank_ic_10d DOUBLE,
    rank_ic_60d DOUBLE,
    feature_cols_json TEXT,
    built_at TEXT,
    PRIMARY KEY (run_id, group_name)
);
ALTER TABLE mart_feature_group_ablation ADD COLUMN IF NOT EXISTS rank_ic_5d DOUBLE;
ALTER TABLE mart_feature_group_ablation ADD COLUMN IF NOT EXISTS rank_ic_10d DOUBLE;
ALTER TABLE mart_feature_group_ablation ADD COLUMN IF NOT EXISTS rank_ic_60d DOUBLE;
"""


def ensure_tables(conn: Any) -> None:
    if hasattr(conn, "executescript"):
        conn.executescript(DDL)
    else:
        conn.execute(DDL)


def _feature_group(feature: str) -> str:
    for group, features in FEATURE_GROUPS.items():
        if feature in features:
            return group
    return "other"


def _mean_rank_ic(df: pd.DataFrame, score_col: str, label_col: str = "forward_ret_20d") -> float | None:
    vals = []
    for _, part in df[[score_col, label_col, "date"]].dropna().groupby("date"):
        if len(part) < 3:
            continue
        if part[score_col].nunique() < 2 or part[label_col].nunique() < 2:
            continue
        corr = part[score_col].rank().corr(part[label_col].rank())
        if pd.notna(corr):
            vals.append(float(corr))
    if not vals:
        return None
    return float(sum(vals) / len(vals))


def _auto_source_feature_set_id(feature_set_id: str) -> str:
    return feature_set_id[:-4] if feature_set_id.endswith("_pit") else feature_set_id


def _candidate_features_for_set(conn: Any, feature_set_id: str) -> list[str]:
    table_cols = {row[1] for row in conn.execute("PRAGMA table_info(fact_feature_panel_candidate)").fetchall()}
    if feature_set_id.startswith("tdx_gpcw_auto"):
        source_feature_set_id = _auto_source_feature_set_id(feature_set_id)
        rows = conn.execute(
            """
            SELECT feature_name, COUNT(feature_value) AS n
            FROM fact_tdx_gpcw_auto_feature_quarterly
            WHERE feature_set_id = ?
            GROUP BY feature_name
            ORDER BY n DESC, feature_name
            """,
            (source_feature_set_id,),
        ).fetchall()
        features = [row["feature_name"] for row in rows if row["feature_name"] in table_cols]
        if features:
            return features
    return [feature for feature in CANDIDATE_FEATURES if feature in table_cols]


def _feature_group_map_for_set(conn: Any, feature_set_id: str) -> dict[str, str]:
    if not feature_set_id.startswith("tdx_gpcw_auto"):
        return {feature: group for group, features in FEATURE_GROUPS.items() for feature in features}
    source_feature_set_id = _auto_source_feature_set_id(feature_set_id)
    return {
        row["feature_name"]: row["feature_family"] or "other"
        for row in conn.execute(
            """
            SELECT feature_name, ANY_VALUE(feature_family) AS feature_family
            FROM fact_tdx_gpcw_auto_feature_quarterly
            WHERE feature_set_id = ?
            GROUP BY feature_name
            """,
            (source_feature_set_id,),
        ).fetchall()
    }


def _load_candidate_panel(conn: Any, feature_set_id: str) -> pd.DataFrame:
    table_cols = {row[1] for row in conn.execute("PRAGMA table_info(fact_feature_panel_candidate)").fetchall()}
    labels = [label for label in LABEL_COLUMNS if label in table_cols]
    if "forward_ret_20d" not in labels:
        labels.append("forward_ret_20d")
    features = _candidate_features_for_set(conn, feature_set_id)
    cols = ["stock_code", "date", *labels, *features]
    sql = (
        f"SELECT {', '.join(cols)} FROM fact_feature_panel_candidate "
        "WHERE feature_set_id = ? AND forward_ret_20d IS NOT NULL"
    )
    cursor = conn.execute(sql, (feature_set_id,))
    if hasattr(cursor, "df"):
        return cursor.df()
    rows = cursor.fetchall()
    desc = getattr(cursor, "description", None) or []
    names = [d[0] for d in desc]
    return pd.DataFrame([tuple(r) for r in rows], columns=names)


def _score_panel(df: pd.DataFrame, features: list[str], signs: dict[str, int], out_col: str) -> pd.DataFrame:
    scored = df.copy()
    rank_cols = []
    for feature in features:
        if feature not in scored.columns:
            continue
        col = f"__rank_{feature}"
        scored[col] = scored.groupby("date")[feature].rank(pct=True) * signs.get(feature, 1)
        rank_cols.append(col)
    scored[out_col] = scored[rank_cols].mean(axis=1) if rank_cols else None
    return scored


def run_group_ablation(
    conn: Any,
    *,
    feature_set_id: str = CANDIDATE_FEATURE_SET_ID,
    run_id: str | None = None,
) -> dict:
    ensure_tables(conn)
    if feature_set_id.startswith("tdx_gpcw_auto"):
        feature_group_map = _feature_group_map_for_set(conn, feature_set_id)
        usable = _candidate_features_for_set(conn, feature_set_id)
        if not usable:
            raise RuntimeError(f"no auto features registered for feature_set_id={feature_set_id}")
        run_id = run_id or f"group_ablation_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"
        built_at = datetime.utcnow().isoformat(timespec="seconds")
        wf_rows = conn.execute(
            """
            SELECT feature_name, label_name, AVG(rank_ic) AS rank_ic
            FROM mart_candidate_walkforward_eval
            WHERE feature_set_id = ?
            GROUP BY feature_name, label_name
            """,
            (feature_set_id,),
        ).fetchall()
        if not wf_rows:
            raise RuntimeError(f"walk-forward rows required before auto group ablation for {feature_set_id}")
        by_feature_label = {(row["feature_name"], row["label_name"]): row["rank_ic"] for row in wf_rows}
        full_values = [
            by_feature_label.get((feature, "forward_ret_20d"))
            for feature in usable
            if by_feature_label.get((feature, "forward_ret_20d")) is not None
        ]
        rank_ic_full = float(sum(full_values) / len(full_values)) if full_values else None
        label_sensitivity = {}
        for label in LABEL_COLUMNS:
            values = [
                by_feature_label.get((feature, label))
                for feature in usable
                if by_feature_label.get((feature, label)) is not None
            ]
            label_sensitivity[label] = float(sum(values) / len(values)) if values else None
        grouped_features: dict[str, list[str]] = {}
        for feature in usable:
            grouped_features.setdefault(feature_group_map.get(feature, "other"), []).append(feature)
        results = []
        for group_name, group_features in grouped_features.items():
            remaining = [feature for feature in usable if feature not in set(group_features)]
            remaining_values = [
                by_feature_label.get((feature, "forward_ret_20d"))
                for feature in remaining
                if by_feature_label.get((feature, "forward_ret_20d")) is not None
            ]
            rank_ic_without = float(sum(remaining_values) / len(remaining_values)) if remaining_values else None
            delta = None if rank_ic_full is None or rank_ic_without is None else rank_ic_full - rank_ic_without
            results.append(
                {
                    "run_id": run_id,
                    "feature_set_id": feature_set_id,
                    "group_name": group_name,
                    "n_features": len(group_features),
                    "rank_ic_full": rank_ic_full,
                    "rank_ic_without_group": rank_ic_without,
                    "rank_ic_delta": delta,
                    "rank_ic_5d": label_sensitivity.get("forward_ret_5d"),
                    "rank_ic_10d": label_sensitivity.get("forward_ret_10d"),
                    "rank_ic_60d": label_sensitivity.get("forward_ret_60d"),
                    "feature_cols_json": json.dumps(group_features, ensure_ascii=False),
                    "built_at": built_at,
                }
            )
        conn.executemany(
            """
            INSERT OR REPLACE INTO mart_feature_group_ablation
            (run_id, feature_set_id, group_name, n_features, rank_ic_full,
             rank_ic_without_group, rank_ic_delta, rank_ic_5d, rank_ic_10d,
             rank_ic_60d, feature_cols_json, built_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    r["run_id"], r["feature_set_id"], r["group_name"], r["n_features"],
                    r["rank_ic_full"], r["rank_ic_without_group"], r["rank_ic_delta"],
                    r["rank_ic_5d"], r["rank_ic_10d"], r["rank_ic_60d"],
                    r["feature_cols_json"], r["built_at"],
                )
                for r in results
            ],
        )
        conn.commit()
        return {
            "run_id": run_id,
            "feature_set_id": feature_set_id,
            "rank_ic_full": rank_ic_full,
            "label_sensitivity": label_sensitivity,
            "groups": results,
            "method": "walkforward_sql_auto",
        }
    df = _load_candidate_panel(conn, feature_set_id)
    if df.empty:
        raise RuntimeError(f"fact_feature_panel_candidate empty for feature_set_id={feature_set_id}")

    feature_group_map = _feature_group_map_for_set(conn, feature_set_id)
    candidate_features = _candidate_features_for_set(conn, feature_set_id)
    usable = [f for f in candidate_features if f in df.columns and df[f].notna().any()]
    if not usable:
        raise RuntimeError("candidate panel has no non-null candidate features")

    individual_ic = {f: _mean_rank_ic(df.rename(columns={f: "__score"}), "__score") for f in usable}
    signs = {f: (-1 if (individual_ic.get(f) or 0) < 0 else 1) for f in usable}
    full = _score_panel(df, usable, signs, "__score_full")
    rank_ic_full = _mean_rank_ic(full, "__score_full")
    label_sensitivity = {
        label: _mean_rank_ic(full, "__score_full", label_col=label)
        for label in LABEL_COLUMNS
        if label in full.columns
    }

    run_id = run_id or f"group_ablation_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"
    built_at = datetime.utcnow().isoformat(timespec="seconds")
    results = []
    if feature_set_id.startswith("tdx_gpcw_auto"):
        grouped_features: dict[str, list[str]] = {}
        for feature in usable:
            grouped_features.setdefault(feature_group_map.get(feature, _feature_group(feature)), []).append(feature)
    else:
        grouped_features = {
            group: [feature for feature in features if feature in usable]
            for group, features in FEATURE_GROUPS.items()
        }
    for group_name, group_features in grouped_features.items():
        group_existing = [f for f in group_features if f in usable]
        remaining = [f for f in usable if f not in group_existing]
        without = _score_panel(df, remaining, signs, "__score_without")
        rank_ic_without = _mean_rank_ic(without, "__score_without")
        delta = (
            None
            if rank_ic_full is None or rank_ic_without is None
            else rank_ic_full - rank_ic_without
        )
        row = {
            "run_id": run_id,
            "feature_set_id": feature_set_id,
            "group_name": group_name,
            "n_features": len(group_existing),
            "rank_ic_full": rank_ic_full,
            "rank_ic_without_group": rank_ic_without,
            "rank_ic_delta": delta,
            "rank_ic_5d": label_sensitivity.get("forward_ret_5d"),
            "rank_ic_10d": label_sensitivity.get("forward_ret_10d"),
            "rank_ic_60d": label_sensitivity.get("forward_ret_60d"),
            "feature_cols_json": json.dumps(group_existing, ensure_ascii=False),
            "built_at": built_at,
        }
        results.append(row)

    conn.executemany(
        """
        INSERT OR REPLACE INTO mart_feature_group_ablation
        (run_id, feature_set_id, group_name, n_features, rank_ic_full,
         rank_ic_without_group, rank_ic_delta, rank_ic_5d, rank_ic_10d,
         rank_ic_60d, feature_cols_json, built_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                r["run_id"], r["feature_set_id"], r["group_name"], r["n_features"],
                r["rank_ic_full"], r["rank_ic_without_group"], r["rank_ic_delta"],
                r["rank_ic_5d"], r["rank_ic_10d"], r["rank_ic_60d"],
                r["feature_cols_json"], r["built_at"],
            )
            for r in results
        ],
    )
    conn.commit()
    return {
        "run_id": run_id,
        "feature_set_id": feature_set_id,
        "rank_ic_full": rank_ic_full,
        "label_sensitivity": label_sensitivity,
        "groups": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--feature-set-id", default=CANDIDATE_FEATURE_SET_ID)
    args = parser.parse_args()

    conn = get_conn()
    try:
        result = run_group_ablation(conn, feature_set_id=args.feature_set_id)
        logger.info("group ablation: %s", result)
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
