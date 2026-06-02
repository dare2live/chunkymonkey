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

from services.db import get_conn  # noqa: E402
from services.feature_registry import forward_return_label_columns  # noqa: E402
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

LABEL_COLUMNS = list(forward_return_label_columns())
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
    rank_ic_90d DOUBLE,
    feature_cols_json TEXT,
    built_at TEXT,
    PRIMARY KEY (run_id, group_name)
);
ALTER TABLE mart_feature_group_ablation ADD COLUMN IF NOT EXISTS rank_ic_5d DOUBLE;
ALTER TABLE mart_feature_group_ablation ADD COLUMN IF NOT EXISTS rank_ic_10d DOUBLE;
ALTER TABLE mart_feature_group_ablation ADD COLUMN IF NOT EXISTS rank_ic_60d DOUBLE;
ALTER TABLE mart_feature_group_ablation ADD COLUMN IF NOT EXISTS rank_ic_90d DOUBLE;
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
        if len(pairs) < 3:
            continue
        scores = [score for score, _ in pairs]
        labels = [label for _, label in pairs]
        if len(set(scores)) < 2 or len(set(labels)) < 2:
            continue
        corr = _pearson(_rank_percentiles(scores), _rank_percentiles(labels))
        if corr is not None:
            vals.append(float(corr))
    if not vals:
        return None
    return float(sum(vals) / len(vals))


def _auto_source_feature_set_id(feature_set_id: str) -> str:
    return feature_set_id[:-4] if feature_set_id.endswith("_pit") else feature_set_id


def _candidate_features_for_set(conn: Any, feature_set_id: str) -> list[str]:
    table_cols = {row[0] for row in conn.execute("DESCRIBE fact_feature_panel_candidate").fetchall()}
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


def _load_candidate_panel(conn: Any, feature_set_id: str) -> list[dict[str, Any]]:
    table_cols = {row[0] for row in conn.execute("DESCRIBE fact_feature_panel_candidate").fetchall()}
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
    return _records_from_cursor(cursor)


def _score_panel(
    rows: list[dict[str, Any]],
    features: list[str],
    signs: dict[str, int],
    out_col: str,
) -> list[dict[str, Any]]:
    scored = [dict(row) for row in rows]
    groups = _group_by_date(scored)
    for feature in features:
        sign = signs.get(feature, 1)
        for group_rows in groups.values():
            indexed_values = [
                (idx, value)
                for idx, row in enumerate(group_rows)
                if (value := _to_float(row.get(feature))) is not None
            ]
            ranks = _rank_percentiles([value for _, value in indexed_values])
            for rank_idx, (row_idx, _) in enumerate(indexed_values):
                group_rows[row_idx].setdefault("__rank_values", []).append(ranks[rank_idx] * sign)
    for row in scored:
        ranks = row.pop("__rank_values", [])
        row[out_col] = _mean(ranks)
    return scored


def run_group_ablation(
    conn: Any,
    *,
    feature_set_id: str = CANDIDATE_FEATURE_SET_ID,
    run_id: str | None = None,
    method: str = "full",
) -> dict:
    ensure_tables(conn)
    if method == "walkforward" or feature_set_id.startswith("tdx_gpcw_auto"):
        feature_group_map = _feature_group_map_for_set(conn, feature_set_id)
        usable = _candidate_features_for_set(conn, feature_set_id)
        if not usable:
            raise RuntimeError(f"no features registered for feature_set_id={feature_set_id}")
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
                    "rank_ic_90d": label_sensitivity.get("forward_ret_90d"),
                    "feature_cols_json": json.dumps(group_features, ensure_ascii=False),
                    "built_at": built_at,
                }
            )
        conn.executemany(
            """
            INSERT OR REPLACE INTO mart_feature_group_ablation
            (run_id, feature_set_id, group_name, n_features, rank_ic_full,
             rank_ic_without_group, rank_ic_delta, rank_ic_5d, rank_ic_10d,
             rank_ic_60d, rank_ic_90d, feature_cols_json, built_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    r["run_id"], r["feature_set_id"], r["group_name"], r["n_features"],
                    r["rank_ic_full"], r["rank_ic_without_group"], r["rank_ic_delta"],
                    r["rank_ic_5d"], r["rank_ic_10d"], r["rank_ic_60d"], r["rank_ic_90d"],
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
            "method": "walkforward_sql",
        }
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

    individual_ic = {
        feature: _mean_rank_ic(
            [{**row, "__score": row.get(feature)} for row in records],
            "__score",
        )
        for feature in usable
    }
    signs = {f: (-1 if (individual_ic.get(f) or 0) < 0 else 1) for f in usable}
    full = _score_panel(records, usable, signs, "__score_full")
    rank_ic_full = _mean_rank_ic(full, "__score_full")
    label_sensitivity = {
        label: _mean_rank_ic(full, "__score_full", label_col=label)
        for label in LABEL_COLUMNS
        if label in panel_cols
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
        without = _score_panel(records, remaining, signs, "__score_without")
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
            "rank_ic_90d": label_sensitivity.get("forward_ret_90d"),
            "feature_cols_json": json.dumps(group_existing, ensure_ascii=False),
            "built_at": built_at,
        }
        results.append(row)

    conn.executemany(
        """
        INSERT OR REPLACE INTO mart_feature_group_ablation
        (run_id, feature_set_id, group_name, n_features, rank_ic_full,
         rank_ic_without_group, rank_ic_delta, rank_ic_5d, rank_ic_10d,
         rank_ic_60d, rank_ic_90d, feature_cols_json, built_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                r["run_id"], r["feature_set_id"], r["group_name"], r["n_features"],
                r["rank_ic_full"], r["rank_ic_without_group"], r["rank_ic_delta"],
                r["rank_ic_5d"], r["rank_ic_10d"], r["rank_ic_60d"], r["rank_ic_90d"],
                r["feature_cols_json"], r["built_at"],
            )
            for r in results
        ],
    )
    from services.schema_versions import record_actual_version
    record_actual_version(conn, "mart_feature_group_ablation")
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
    parser.add_argument("--method", choices=["full", "walkforward"], default="full")
    args = parser.parse_args()

    conn = get_conn()
    try:
        result = run_group_ablation(conn, feature_set_id=args.feature_set_id, method=args.method)
        logger.info("group ablation: %s", result)
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
