#!/usr/bin/env python3
"""Build Phase 3 candidate feature panel without replacing production champion."""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.db import get_conn  # noqa: E402

logger = logging.getLogger("candidate_feature_panel")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s | %(message)s")


CANDIDATE_FEATURE_SET_ID = "tdx_f10_gpcw_v1"

CANDIDATE_FEATURES = [
    "common_holder_network_count",
    "fund_holding_shares_tdx_f10",
    "fund_holding_float_a_ratio_tdx_f10",
    "fund_holding_market_value_tdx_f10",
    "holder_count_change_pct_tdx",
    "avg_float_shares_change_pct_tdx",
    "holder_count_acceleration_tdx",
    "top10_concentration_change",
    "tdx_inst_total_shares_qoq",
    "national_team_shares_qoq",
    "qfii_shares_qoq",
    "fund_shares_qoq",
    "social_security_shares_qoq",
    "contract_liabilities_to_revenue",
    "ocf_to_profit_tdx",
    "receivables_to_revenue",
    "inventory_to_revenue",
    "forecast_profit_yoy_mid",
    "forecast_range_width",
    "express_net_profit_yoy",
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
    follow_net_return_5d REAL,
    follow_net_return_10d REAL,
    follow_net_return_20d REAL,
    follow_net_return_60d REAL,
    follow_net_return_90d REAL,
    common_holder_network_count REAL,
    fund_holding_shares_tdx_f10 REAL,
    fund_holding_float_a_ratio_tdx_f10 REAL,
    fund_holding_market_value_tdx_f10 REAL,
    holder_count_change_pct_tdx REAL,
    avg_float_shares_change_pct_tdx REAL,
    holder_count_acceleration_tdx REAL,
    top10_concentration_change REAL,
    tdx_inst_total_shares_qoq REAL,
    national_team_shares_qoq REAL,
    qfii_shares_qoq REAL,
    fund_shares_qoq REAL,
    social_security_shares_qoq REAL,
    contract_liabilities_to_revenue REAL,
    ocf_to_profit_tdx REAL,
    receivables_to_revenue REAL,
    inventory_to_revenue REAL,
    forecast_profit_yoy_mid REAL,
    forecast_range_width REAL,
    express_net_profit_yoy REAL,
    built_at TEXT,
    PRIMARY KEY (feature_set_id, stock_code, date)
);
CREATE INDEX IF NOT EXISTS idx_feature_candidate_date
    ON fact_feature_panel_candidate(feature_set_id, date);
ALTER TABLE fact_feature_panel_candidate ADD COLUMN IF NOT EXISTS forward_ret_5d REAL;
ALTER TABLE fact_feature_panel_candidate ADD COLUMN IF NOT EXISTS forward_ret_10d REAL;
ALTER TABLE fact_feature_panel_candidate ADD COLUMN IF NOT EXISTS forward_ret_60d REAL;
ALTER TABLE fact_feature_panel_candidate ADD COLUMN IF NOT EXISTS forward_ret_90d REAL;
ALTER TABLE fact_feature_panel_candidate ADD COLUMN IF NOT EXISTS follow_net_return_5d REAL;
ALTER TABLE fact_feature_panel_candidate ADD COLUMN IF NOT EXISTS follow_net_return_10d REAL;
ALTER TABLE fact_feature_panel_candidate ADD COLUMN IF NOT EXISTS follow_net_return_20d REAL;
ALTER TABLE fact_feature_panel_candidate ADD COLUMN IF NOT EXISTS follow_net_return_60d REAL;
ALTER TABLE fact_feature_panel_candidate ADD COLUMN IF NOT EXISTS follow_net_return_90d REAL;
ALTER TABLE fact_feature_panel_candidate ADD COLUMN IF NOT EXISTS common_holder_network_count REAL;
ALTER TABLE fact_feature_panel_candidate ADD COLUMN IF NOT EXISTS fund_holding_shares_tdx_f10 REAL;
ALTER TABLE fact_feature_panel_candidate ADD COLUMN IF NOT EXISTS fund_holding_float_a_ratio_tdx_f10 REAL;
ALTER TABLE fact_feature_panel_candidate ADD COLUMN IF NOT EXISTS fund_holding_market_value_tdx_f10 REAL;
ALTER TABLE fact_feature_panel_candidate ADD COLUMN IF NOT EXISTS holder_count_acceleration_tdx REAL;
ALTER TABLE fact_feature_panel_candidate ADD COLUMN IF NOT EXISTS social_security_shares_qoq REAL;
ALTER TABLE fact_feature_panel_candidate ADD COLUMN IF NOT EXISTS receivables_to_revenue REAL;
ALTER TABLE fact_feature_panel_candidate ADD COLUMN IF NOT EXISTS inventory_to_revenue REAL;
ALTER TABLE fact_feature_panel_candidate ADD COLUMN IF NOT EXISTS forecast_range_width REAL;
ALTER TABLE fact_feature_panel_candidate ADD COLUMN IF NOT EXISTS express_net_profit_yoy REAL;
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


def build_candidate_feature_panel(
    conn: Any,
    *,
    feature_set_id: str = CANDIDATE_FEATURE_SET_ID,
    start_date: str = "2023-01-01",
    end_date: str | None = None,
) -> dict:
    """Build a candidate-only panel from existing production panel + TDX facts."""

    ensure_tables(conn)
    built_at = datetime.utcnow().isoformat(timespec="seconds")
    filters = ["date >= ?"]
    params: list[Any] = [start_date]
    if end_date:
        filters.append("date <= ?")
        params.append(end_date)

    where_sql = " AND ".join(filters)
    insert_params = [*params, feature_set_id, built_at]
    conn.execute(
        f"""
        INSERT OR REPLACE INTO fact_feature_panel_candidate (
            feature_set_id, stock_code, date,
            forward_ret_5d, forward_ret_10d, forward_ret_20d, forward_ret_60d,
            forward_ret_90d,
            follow_net_return_5d,
            follow_net_return_10d,
            follow_net_return_20d,
            follow_net_return_60d,
            follow_net_return_90d,
            common_holder_network_count,
            fund_holding_shares_tdx_f10,
            fund_holding_float_a_ratio_tdx_f10,
            fund_holding_market_value_tdx_f10,
            holder_count_change_pct_tdx,
            avg_float_shares_change_pct_tdx,
            holder_count_acceleration_tdx,
            top10_concentration_change,
            tdx_inst_total_shares_qoq,
            national_team_shares_qoq,
            qfii_shares_qoq,
            fund_shares_qoq,
            social_security_shares_qoq,
            contract_liabilities_to_revenue,
            ocf_to_profit_tdx,
            receivables_to_revenue,
            inventory_to_revenue,
            forecast_profit_yoy_mid,
            forecast_range_width,
            express_net_profit_yoy,
            built_at
        )
        WITH priced AS (
            SELECT stock_code, date, close, forward_ret_20d,
                   follow_net_return_5d, follow_net_return_10d,
                   follow_net_return_20d, follow_net_return_60d,
                   follow_net_return_90d,
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
                   forward_ret_90d_calc AS forward_ret_90d,
                   follow_net_return_5d,
                   follow_net_return_10d,
                   follow_net_return_20d,
                   follow_net_return_60d,
                   follow_net_return_90d
            FROM priced
            WHERE {where_sql}
        ),
        holder_count AS (
            SELECT stock_code, report_date AS date,
                   holder_count_change_pct AS holder_count_change_pct_tdx,
                   avg_float_shares_change_pct AS avg_float_shares_change_pct_tdx,
                   holder_count_change_pct - LAG(holder_count_change_pct) OVER (
                       PARTITION BY stock_code ORDER BY report_date
                   ) AS holder_count_acceleration_tdx
            FROM fact_holder_count_period
            WHERE report_date IS NOT NULL
        ),
        top10 AS (
            WITH conc AS (
                SELECT stock_code, report_date AS date,
                       SUM(COALESCE(hold_ratio_total, hold_ratio_float, hold_ratio)) AS concentration
                FROM fact_top10_holder_period
                WHERE holder_set = 'all'
                  AND NOT COALESCE(is_exit_row, FALSE)
                  AND NOT COALESCE(is_secondary_class, FALSE)
                GROUP BY stock_code, report_date
            )
            SELECT stock_code, date,
                   concentration - LAG(concentration) OVER (
                       PARTITION BY stock_code ORDER BY date
                   ) AS top10_concentration_change
            FROM conc
        ),
        common_holder AS (
            SELECT stock_code, report_date AS date,
                   COUNT(DISTINCT CASE
                       WHEN peer_stock_code <> stock_code THEN peer_stock_code
                   END) AS common_holder_network_count
            FROM fact_common_major_holder_stock
            WHERE report_date IS NOT NULL
            GROUP BY stock_code, report_date
        ),
        fund_f10 AS (
            SELECT stock_code, report_date AS date,
                   SUM(shares) AS fund_holding_shares_tdx_f10,
                   SUM(float_a_ratio) AS fund_holding_float_a_ratio_tdx_f10,
                   SUM(market_value) AS fund_holding_market_value_tdx_f10
            FROM fact_fund_holding_tdx_f10
            WHERE report_date IS NOT NULL
            GROUP BY stock_code, report_date
        ),
        gpcw AS (
            SELECT stock_code, report_date AS date,
                   inst_total_shares / NULLIF(LAG(inst_total_shares) OVER w, 0) - 1
                       AS tdx_inst_total_shares_qoq,
                   national_team_shares_wan / NULLIF(LAG(national_team_shares_wan) OVER w, 0) - 1
                       AS national_team_shares_qoq,
                   qfii_shares / NULLIF(LAG(qfii_shares) OVER w, 0) - 1
                       AS qfii_shares_qoq,
                   fund_shares / NULLIF(LAG(fund_shares) OVER w, 0) - 1
                       AS fund_shares_qoq,
                   social_security_shares / NULLIF(LAG(social_security_shares) OVER w, 0) - 1
                       AS social_security_shares_qoq,
                   contract_liabilities / NULLIF(revenue, 0)
                       AS contract_liabilities_to_revenue,
                   operating_cashflow / NULLIF(net_profit, 0)
                       AS ocf_to_profit_tdx,
                   accounts_receivable / NULLIF(revenue, 0)
                       AS receivables_to_revenue,
                   inventory / NULLIF(revenue, 0)
                       AS inventory_to_revenue,
                   (forecast_profit_yoy_low + forecast_profit_yoy_high) / 2.0
                       AS forecast_profit_yoy_mid,
                   forecast_profit_yoy_high - forecast_profit_yoy_low
                       AS forecast_range_width,
                   express_net_profit / NULLIF(LAG(express_net_profit) OVER w, 0) - 1
                       AS express_net_profit_yoy
            FROM raw_gpcw_detail
            WINDOW w AS (PARTITION BY stock_code ORDER BY report_date)
        )
        SELECT
            ? AS feature_set_id,
            b.stock_code,
            b.date,
            b.forward_ret_5d,
            b.forward_ret_10d,
            b.forward_ret_20d,
            b.forward_ret_60d,
            b.forward_ret_90d,
            b.follow_net_return_5d,
            b.follow_net_return_10d,
            b.follow_net_return_20d,
            b.follow_net_return_60d,
            b.follow_net_return_90d,
            ch.common_holder_network_count,
            ff.fund_holding_shares_tdx_f10,
            ff.fund_holding_float_a_ratio_tdx_f10,
            ff.fund_holding_market_value_tdx_f10,
            hc.holder_count_change_pct_tdx,
            hc.avg_float_shares_change_pct_tdx,
            hc.holder_count_acceleration_tdx,
            t10.top10_concentration_change,
            g.tdx_inst_total_shares_qoq,
            g.national_team_shares_qoq,
            g.qfii_shares_qoq,
            g.fund_shares_qoq,
            g.social_security_shares_qoq,
            g.contract_liabilities_to_revenue,
            g.ocf_to_profit_tdx,
            g.receivables_to_revenue,
            g.inventory_to_revenue,
            g.forecast_profit_yoy_mid,
            g.forecast_range_width,
            g.express_net_profit_yoy,
            ? AS built_at
        FROM base b
        ASOF LEFT JOIN holder_count hc
          ON b.stock_code = hc.stock_code AND b.date >= hc.date
        ASOF LEFT JOIN top10 t10
          ON b.stock_code = t10.stock_code AND b.date >= t10.date
        ASOF LEFT JOIN common_holder ch
          ON b.stock_code = ch.stock_code AND b.date >= ch.date
        ASOF LEFT JOIN fund_f10 ff
          ON b.stock_code = ff.stock_code AND b.date >= ff.date
        ASOF LEFT JOIN gpcw g
          ON b.stock_code = g.stock_code AND b.date >= g.date
        """,
        insert_params,
    )
    from services.schema_versions import record_actual_version
    record_actual_version(conn, "fact_feature_panel_candidate")
    conn.commit()
    row = conn.execute(
        """
        SELECT COUNT(*) AS rows,
               COUNT(DISTINCT stock_code) AS stocks,
               COUNT(DISTINCT date) AS dates
        FROM fact_feature_panel_candidate
        WHERE feature_set_id = ?
        """,
        (feature_set_id,),
    ).fetchone()
    return {
        "feature_set_id": feature_set_id,
        "rows": int(row[0] or 0),
        "stocks": int(row[1] or 0),
        "dates": int(row[2] or 0),
        "features": list(CANDIDATE_FEATURES),
        "built_at": built_at,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--feature-set-id", default=CANDIDATE_FEATURE_SET_ID)
    parser.add_argument("--start", default="2023-01-01")
    parser.add_argument("--end", default=None)
    args = parser.parse_args()

    conn = get_conn()
    try:
        result = build_candidate_feature_panel(
            conn,
            feature_set_id=args.feature_set_id,
            start_date=args.start,
            end_date=args.end,
        )
        logger.info("candidate panel: %s", result)
        return 0 if result["rows"] > 0 else 1
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
