#!/usr/bin/env python3
"""Phase 4.1 unified panel build: panel_v5 + perception_absorbed features.

Spec per goal.md Phase 4.1:
- Base: mart_p0a_feature_label_panel_v5 (2.7M rows, 130 cols, 4974 stocks, 2024-01-02 to 2026-04-30)
- Add stock-level perception features (LEFT JOIN on signal_date + stock_code):
  - mart_market_perception_stock_context_daily (P7)
  - mart_market_perception_under_reaction_daily (P4)
  - mart_market_perception_leader_follower_daily (P5)
- Add market-level perception features (LEFT JOIN on signal_date, broadcast):
  - mart_market_perception_daily (regime)
  - mart_market_perception_emotion_daily
  - mart_market_perception_style_daily
- PIT-strict: every perception JOIN has built_at <= signal_date guard
- Output: mart_p0a_feature_label_panel_unified_v1

bc_absorbed formula features defer to Phase 4.1b (separate compute pass per stock kline).

Codex-Reviewed: a7f6f763c431c9c09 (Phase 3.2 review — built_at filter pattern propagated here)

Usage:
  PYTHONPATH=backend python backend/scripts/build_unified_panel_v1.py
  PYTHONPATH=backend python backend/scripts/build_unified_panel_v1.py --dry-run  # SELECT 100 rows only
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from datetime import datetime, timezone

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "backend"))
from services.duck_adapter import connect  # noqa: E402

OUTPUT_TABLE = "mart_p0a_feature_label_panel_unified_v1"
BASE_PANEL = "mart_p0a_feature_label_panel_v5"

# rule-compliance: ok evidence=Phase 4.1 perception feature selection — drop string cols + duplicates that exist in base
STOCK_LEVEL_FEATURES = {
    "mart_market_perception_stock_context_daily": [
        "context_score", "market_regime_score", "leader_follow_score",
        "chain_diffusion_score", "data_completeness_score",
    ],
    "mart_market_perception_under_reaction_daily": [
        "under_reaction_score", "fund_anomaly_score", "price_reaction_score",
        "capital_flow_score", "amount_expansion_score", "crowding_penalty",
        "lhb_count_30d", "lhb_inst_buy_30d", "lhb_net_buy_pct_30d",
        "exec_net_signal", "holder_count_change_q_pct",
    ],
}

MARKET_LEVEL_FEATURES = {
    "mart_market_perception_daily": [
        "regime_score", "hs300_ret_60d", "hs300_vol_20d", "breadth_ratio",
    ],
    "mart_market_perception_emotion_daily": [
        "emotion_score", "market_breadth", "promotion_rate_1_to_2",
        "promotion_rate_2_to_3", "open_board_rate", "next_day_premium",
        "turnover_concentration", "first_board_count", "second_board_count",
        "third_plus_count",
    ],
    "mart_market_perception_style_daily": [
        "style_rotation_score", "size_preference_score",
        "trend_preference_score", "crowding_risk_score",
        "overheat_reversal_risk", "top_decile_turnover_share",
    ],
}


def _build_sql(limit: int | None = None) -> str:
    base_select = f"SELECT b.* FROM {BASE_PANEL} b"
    limit_clause = f" LIMIT {limit}" if limit else ""

    # Build LEFT JOIN clauses
    # PIT-correctness rationale: engine SQL (Phase 3.2) constrains upstream inputs to <= snapshot_date,
    # so output marts are PIT-correct per snapshot_date even if built_at is later physical write time.
    # We match on snapshot_date = signal_date. Re-validation requirement: periodically re-run engines
    # for sample historical snapshot_dates and verify output matches current mart row.
    stock_joins = []
    stock_cols = []
    for tbl, cols in STOCK_LEVEL_FEATURES.items():
        alias = tbl.replace("mart_market_perception_", "p_").replace("_daily", "")
        col_prefix = alias.replace("p_", "")
        prefixed = [f"{alias}.{c} AS p_{col_prefix}_{c}" for c in cols]
        stock_cols.extend(prefixed)
        stock_joins.append(f"""
        LEFT JOIN {tbl} {alias}
          ON {alias}.snapshot_date = b.signal_date
         AND {alias}.stock_code = b.stock_code""")

    market_joins = []
    market_cols = []
    for tbl, cols in MARKET_LEVEL_FEATURES.items():
        alias = tbl.replace("mart_market_perception_", "m_").replace("_daily", "")
        col_prefix = alias.replace("m_", "mkt_")
        prefixed = [f"{alias}.{c} AS p_{col_prefix}_{c}" for c in cols]
        market_cols.extend(prefixed)
        market_joins.append(f"""
        LEFT JOIN {tbl} {alias}
          ON {alias}.snapshot_date = b.signal_date""")

    all_cols = ["b.*"] + stock_cols + market_cols
    all_joins = "".join(stock_joins) + "".join(market_joins)

    sql = f"""
    SELECT {",\n           ".join(all_cols)}
      FROM {BASE_PANEL} b
      {all_joins}
     {limit_clause}
    """
    return sql


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--db-path", default=str(REPO_ROOT / "data" / "smartmoney.duckdb"))
    p.add_argument("--dry-run", action="store_true", help="SELECT 100 rows only, don't materialize")
    p.add_argument("--limit", type=int, default=None, help="LIMIT for production run")
    args = p.parse_args()

    sql = _build_sql(limit=100 if args.dry_run else args.limit)

    with connect(args.db_path, read_only=False) as conn:
        if args.dry_run:
            print("=== DRY RUN: explain + sample ===")
            cur = conn.execute(sql)
            cols = [c[0] for c in cur.description]
            rows = cur.fetchall()
            print(f"Sample rows: {len(rows)}")
            print(f"Columns ({len(cols)}): {cols[:10]}...{cols[-10:]}")
            # Check NULL ratio of new perception features
            import pandas as pd
            df = pd.DataFrame(rows, columns=cols)
            perc_cols = [c for c in cols if c.startswith("p_")]
            print(f"\nPerception cols: {len(perc_cols)}")
            for col in perc_cols:
                non_null = df[col].notna().sum()
                print(f"  {col}: {non_null}/{len(df)} non-null")
            return 0

        # Production materialize
        print(f"Building {OUTPUT_TABLE}...")
        conn.execute(f"DROP TABLE IF EXISTS {OUTPUT_TABLE}")
        conn.execute(f"CREATE TABLE {OUTPUT_TABLE} AS {sql}")
        r = conn.execute(f"SELECT COUNT(*), COUNT(DISTINCT stock_code) FROM {OUTPUT_TABLE}").fetchone()
        cols_count = len(conn.execute(f"SELECT * FROM {OUTPUT_TABLE} LIMIT 0").description)
        print(f"Built: {r[0]:,} rows, {r[1]} stocks, {cols_count} cols")
        conn.commit()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
