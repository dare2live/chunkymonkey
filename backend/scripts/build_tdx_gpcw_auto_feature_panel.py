#!/usr/bin/env python3
"""ASOF quarterly TDX gpcw auto features into the daily candidate panel."""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.db import get_conn  # noqa: E402
from scripts.build_tdx_gpcw_auto_features import AUTO_FEATURE_SET_ID  # noqa: E402

logger = logging.getLogger("tdx_gpcw_auto_panel")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s | %(message)s")


AUTO_PANEL_FEATURE_SET_ID = "tdx_gpcw_auto_v1_pit"
LABEL_COLUMNS = ["forward_ret_5d", "forward_ret_10d", "forward_ret_20d", "forward_ret_60d", "forward_ret_90d"]
BASELINE_FEATURES = [
    "forecast_profit_yoy_mid",
    "inverse_holder_count_change_pct_tdx",
    "ocf_to_profit_tdx",
    "contract_liabilities_to_revenue",
]


DDL = """
CREATE TABLE IF NOT EXISTS fact_feature_panel_candidate (
    feature_set_id TEXT NOT NULL,
    stock_code TEXT NOT NULL,
    date TEXT NOT NULL,
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
"""


def ensure_tables(conn: Any) -> None:
    if hasattr(conn, "executescript"):
        conn.executescript(DDL)
    else:
        for stmt in DDL.split(";"):
            stmt = stmt.strip()
            if stmt:
                conn.execute(stmt)


def _quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _feature_candidates(conn: Any, source_feature_set_id: str, max_features: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT feature_name,
               ANY_VALUE(feature_family) AS feature_family,
               COUNT(feature_value) AS non_null_rows,
               COUNT(DISTINCT stock_code) AS stock_count,
               COUNT(DISTINCT report_date) AS quarter_count
        FROM fact_tdx_gpcw_auto_feature_quarterly
        WHERE feature_set_id = ?
          AND feature_value IS NOT NULL
        GROUP BY feature_name
        HAVING COUNT(DISTINCT feature_value) > 1
           AND COUNT(DISTINCT stock_code) >= 5000
           AND COUNT(DISTINCT report_date) >= 10
        ORDER BY
          CASE
            WHEN feature_name IN ('forecast_profit_yoy_mid',
                                  'inverse_holder_count_change_pct_tdx',
                                  'ocf_to_profit_tdx',
                                  'contract_liabilities_to_revenue') THEN 0
            WHEN ANY_VALUE(feature_family) = 'forecast_express' THEN 1
            WHEN ANY_VALUE(feature_family) = 'ownership' THEN 2
            WHEN ANY_VALUE(feature_family) = 'fundamental_quality' THEN 3
            WHEN ANY_VALUE(feature_family) = 'profit_growth' THEN 4
            ELSE 5
          END,
          COUNT(feature_value) DESC,
          feature_name
        LIMIT ?
        """,
        (source_feature_set_id, max_features),
    ).fetchall()
    return [{k: row[k] for k in row.keys()} for row in rows]


def _ensure_feature_columns(conn: Any, features: list[str]) -> None:
    for label in LABEL_COLUMNS:
        conn.execute(f"ALTER TABLE fact_feature_panel_candidate ADD COLUMN IF NOT EXISTS {_quote_ident(label)} REAL")
    for feature in features:
        conn.execute(f"ALTER TABLE fact_feature_panel_candidate ADD COLUMN IF NOT EXISTS {_quote_ident(feature)} REAL")


def build_tdx_gpcw_auto_feature_panel(
    conn: Any,
    *,
    source_feature_set_id: str = AUTO_FEATURE_SET_ID,
    feature_set_id: str = AUTO_PANEL_FEATURE_SET_ID,
    start_date: str = "2023-01-01",
    end_date: str | None = None,
    max_features: int = 120,
) -> dict[str, Any]:
    ensure_tables(conn)
    feature_rows = _feature_candidates(conn, source_feature_set_id, max_features)
    features = [str(row["feature_name"]) for row in feature_rows]
    missing_baseline = [f for f in BASELINE_FEATURES if f not in features]
    if missing_baseline:
        raise RuntimeError(f"required baseline auto features missing from quarterly source: {missing_baseline}")
    if len(features) < 100:
        raise RuntimeError(f"only {len(features)} auto features passed panel gates")

    _ensure_feature_columns(conn, features)
    built_at = datetime.utcnow().isoformat(timespec="seconds")
    feature_filter = ", ".join("?" for _ in features)
    pivot_cols = ",\n               ".join(
        f"MAX(CASE WHEN feature_name = ? THEN feature_value END) AS {_quote_ident(feature)}"
        for feature in features
    )
    select_feature_cols = ",\n               ".join(f"q.{_quote_ident(feature)}" for feature in features)
    insert_cols = ["feature_set_id", "stock_code", "date", *LABEL_COLUMNS, *features, "built_at"]

    filters = ["date >= ?"]
    params: list[Any] = [start_date]
    if end_date:
        filters.append("date <= ?")
        params.append(end_date)
    where_sql = " AND ".join(filters)

    conn.execute(
        f"""
        CREATE OR REPLACE TEMP TABLE tmp_tdx_gpcw_auto_quarterly_wide AS
        SELECT stock_code,
               report_date,
               MAX(available_date) AS available_date,
               {pivot_cols}
        FROM fact_tdx_gpcw_auto_feature_quarterly
        WHERE feature_set_id = ?
          AND feature_name IN ({feature_filter})
        GROUP BY stock_code, report_date
        """,
        [*features, source_feature_set_id, *features],
    )
    conn.execute(
        f"""
        INSERT OR REPLACE INTO fact_feature_panel_candidate
        ({', '.join(_quote_ident(c) for c in insert_cols)})
        WITH priced AS (
            SELECT stock_code, date, close, forward_ret_20d,
                   LEAD(close, 5) OVER w / NULLIF(close, 0) - 1 AS forward_ret_5d_calc,
                   LEAD(close, 10) OVER w / NULLIF(close, 0) - 1 AS forward_ret_10d_calc,
                   LEAD(close, 20) OVER w / NULLIF(close, 0) - 1 AS forward_ret_20d_calc,
                   LEAD(close, 60) OVER w / NULLIF(close, 0) - 1 AS forward_ret_60d_calc,
                   LEAD(close, 90) OVER w / NULLIF(close, 0) - 1 AS forward_ret_90d_calc
            FROM fact_feature_panel
            WINDOW w AS (PARTITION BY stock_code ORDER BY date)
        ),
        base AS (
            SELECT stock_code, date,
                   forward_ret_5d_calc AS forward_ret_5d,
                   forward_ret_10d_calc AS forward_ret_10d,
                   COALESCE(forward_ret_20d, forward_ret_20d_calc) AS forward_ret_20d,
                   forward_ret_60d_calc AS forward_ret_60d,
                   forward_ret_90d_calc AS forward_ret_90d
            FROM priced
            WHERE {where_sql}
        )
        SELECT ? AS feature_set_id,
               b.stock_code,
               b.date,
               b.forward_ret_5d,
               b.forward_ret_10d,
               b.forward_ret_20d,
               b.forward_ret_60d,
               b.forward_ret_90d,
               {select_feature_cols},
               ? AS built_at
        FROM base b
        ASOF LEFT JOIN tmp_tdx_gpcw_auto_quarterly_wide q
          ON b.stock_code = q.stock_code
         AND b.date >= q.available_date
        """,
        [*params, feature_set_id, built_at],
    )
    from services.schema_versions import record_actual_version
    record_actual_version(conn, "fact_feature_panel_candidate")
    conn.commit()
    summary = conn.execute(
        """
        SELECT COUNT(*) AS rows,
               COUNT(DISTINCT stock_code) AS stocks,
               COUNT(DISTINCT date) AS dates,
               MIN(date) AS min_date,
               MAX(date) AS max_date
        FROM fact_feature_panel_candidate
        WHERE feature_set_id = ?
        """,
        (feature_set_id,),
    ).fetchone()
    return {
        "source_feature_set_id": source_feature_set_id,
        "feature_set_id": feature_set_id,
        "features": len(features),
        "rows": summary["rows"],
        "stocks": summary["stocks"],
        "dates": summary["dates"],
        "min_date": summary["min_date"],
        "max_date": summary["max_date"],
        "built_at": built_at,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-feature-set-id", default=AUTO_FEATURE_SET_ID)
    parser.add_argument("--feature-set-id", default=AUTO_PANEL_FEATURE_SET_ID)
    parser.add_argument("--start", default="2023-01-01")
    parser.add_argument("--end", default=None)
    parser.add_argument("--max-features", type=int, default=120)
    args = parser.parse_args()

    conn = get_conn()
    try:
        result = build_tdx_gpcw_auto_feature_panel(
            conn,
            source_feature_set_id=args.source_feature_set_id,
            feature_set_id=args.feature_set_id,
            start_date=args.start,
            end_date=args.end,
            max_features=args.max_features,
        )
        logger.info("tdx gpcw auto feature panel: %s", result)
        return 0 if result["features"] >= 100 and result["stocks"] >= 5000 and result["dates"] >= 750 else 1
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
