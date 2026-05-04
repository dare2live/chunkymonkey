#!/usr/bin/env python3
"""Build a production-grade TDX keep challenger panel without touching champion panel."""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.db import get_conn
from services.model_feature_schema import (
    TDX_KEEP_FEATURE_COLS,
    ordered_feature_cols,
)
from services.schema_versions import record_actual_version


logger = logging.getLogger("tdx_keep_panel")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s | %(message)s")


LABEL_COLS = ["forward_ret_5d", "forward_ret_10d", "forward_ret_20d", "forward_ret_60d"]


def _columns(conn, table: str) -> set[str]:
    return {
        r[0] for r in conn.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = ?",
            (table,),
        ).fetchall()
    }


def _label_expr(label: str, base_cols: set[str], candidate_cols: set[str]) -> str:
    if label in base_cols and label in candidate_cols:
        return f"COALESCE(p.{label}, c.{label}) AS {label}"
    if label in base_cols:
        return f"p.{label} AS {label}"
    if label in candidate_cols:
        return f"c.{label} AS {label}"
    raise RuntimeError(f"缺少 label 列: {label}")


def _rank_feature_expr(feature: str) -> str:
    """Use daily cross-sectional ranks for TDX overlays.

    Raw forecast/fundamental magnitudes are seasonal around earnings windows.
    The model consumes cross-sectional ordering, so ranking keeps the signal
    while making train/recent PSI comparable.
    """

    non_null_count = f"COUNT(c.{feature}) OVER (PARTITION BY p.date)"
    row_number = (
        "ROW_NUMBER() OVER ("
        f"PARTITION BY p.date ORDER BY CASE WHEN c.{feature} IS NULL THEN 1 ELSE 0 END, "
        f"c.{feature}, p.stock_code)"
    )
    return (
        f"CASE WHEN c.{feature} IS NULL THEN NULL "
        f"WHEN {non_null_count} <= 1 THEN 0.5 ELSE "
        f"CAST(({row_number} - 1) AS REAL) / CAST(({non_null_count} - 1) AS REAL) "
        f"END AS {feature}"
    )


def build_panel(
    conn,
    *,
    feature_set_id: str = "tdx_keep_challenger_v1",
    source_feature_set_id: str = "tdx_f10_gpcw_v1",
    start_date: str = "2023-01-01",
) -> dict:
    base_cols = _columns(conn, "fact_feature_panel")
    candidate_cols = _columns(conn, "fact_feature_panel_candidate")
    missing_keep = [c for c in TDX_KEEP_FEATURE_COLS if c not in candidate_cols]
    if missing_keep:
        raise RuntimeError(f"fact_feature_panel_candidate 缺少 TDX keep 特征: {missing_keep}")

    baseline_features = [c for c in ordered_feature_cols(include_dense_v2=True) if c in base_cols]
    label_exprs = [_label_expr(c, base_cols, candidate_cols) for c in LABEL_COLS]
    select_cols = [
        f"'{feature_set_id}' AS feature_set_id",
        "p.stock_code",
        "p.date",
        "p.regime_flag" if "regime_flag" in base_cols else "NULL AS regime_flag",
        *label_exprs,
        *[f"CAST(p.{c} AS REAL) AS {c}" for c in baseline_features],
        *[_rank_feature_expr(c) for c in TDX_KEEP_FEATURE_COLS],
        f"'{datetime.utcnow().isoformat()}' AS built_at",
    ]
    conn.execute(
        f"""
        CREATE OR REPLACE TABLE fact_feature_panel_tdx_keep_challenger AS
        SELECT {', '.join(select_cols)}
          FROM fact_feature_panel p
          JOIN fact_feature_panel_candidate c
            ON c.stock_code = p.stock_code
           AND c.date = p.date
           AND c.feature_set_id = ?
         WHERE p.date >= ?
        """,
        (source_feature_set_id, start_date),
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_tdx_keep_panel_date ON fact_feature_panel_tdx_keep_challenger(feature_set_id, date)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_tdx_keep_panel_stock ON fact_feature_panel_tdx_keep_challenger(feature_set_id, stock_code)"
    )
    try:
        record_actual_version(conn, "fact_feature_panel_tdx_keep_challenger")
    except Exception as exc:
        logger.warning("record schema version failed: %s", exc)
    conn.commit()
    row = conn.execute(
        """
        SELECT COUNT(*) n, COUNT(DISTINCT stock_code) stocks, COUNT(DISTINCT date) dates,
               MIN(date) min_date, MAX(date) max_date
          FROM fact_feature_panel_tdx_keep_challenger
         WHERE feature_set_id = ?
        """,
        (feature_set_id,),
    ).fetchone()
    coverage = {}
    for feature in TDX_KEEP_FEATURE_COLS:
        c = conn.execute(
            f"""
            SELECT COUNT({feature}) * 100.0 / NULLIF(COUNT(*), 0)
              FROM fact_feature_panel_tdx_keep_challenger
             WHERE feature_set_id = ?
            """,
            (feature_set_id,),
        ).fetchone()[0]
        coverage[feature] = float(c or 0.0)
    return {
        "feature_set_id": feature_set_id,
        "source_feature_set_id": source_feature_set_id,
        "baseline_features": len(baseline_features),
        "keep_features": list(TDX_KEEP_FEATURE_COLS),
        "tdx_keep_transform": "daily_cross_sectional_percent_rank",
        "rows": dict(row),
        "coverage_pct": coverage,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--feature-set-id", default="tdx_keep_challenger_v1")
    parser.add_argument("--source-feature-set-id", default="tdx_f10_gpcw_v1")
    parser.add_argument("--start", default="2023-01-01")
    args = parser.parse_args()
    with get_conn() as conn:
        result = build_panel(
            conn,
            feature_set_id=args.feature_set_id,
            source_feature_set_id=args.source_feature_set_id,
            start_date=args.start,
        )
    logger.info("tdx keep challenger panel: %s", result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
