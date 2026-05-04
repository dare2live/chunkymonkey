#!/usr/bin/env python3
"""Audit candidate TDX features for point-in-time ASOF source availability."""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.db import get_conn  # noqa: E402
from scripts.build_candidate_feature_panel import CANDIDATE_FEATURE_SET_ID, CANDIDATE_FEATURES  # noqa: E402

logger = logging.getLogger("tdx_feature_pit")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s | %(message)s")


DDL = """
CREATE TABLE IF NOT EXISTS mart_feature_pit_audit (
    audit_run_id TEXT NOT NULL,
    feature_set_id TEXT NOT NULL,
    feature_name TEXT NOT NULL,
    source_table TEXT NOT NULL,
    sample_rows INTEGER,
    checked_rows INTEGER,
    violation_rows INTEGER,
    max_source_available_date TEXT,
    max_signal_date TEXT,
    status TEXT NOT NULL,
    notes TEXT,
    audited_at TEXT,
    PRIMARY KEY (audit_run_id, feature_set_id, feature_name, source_table)
);
"""


SOURCE_BY_FEATURE = {
    "common_holder_network_count": ("fact_common_major_holder_stock", "report_date"),
    "fund_holding_shares_tdx_f10": ("fact_fund_holding_tdx_f10", "report_date"),
    "fund_holding_float_a_ratio_tdx_f10": ("fact_fund_holding_tdx_f10", "report_date"),
    "fund_holding_market_value_tdx_f10": ("fact_fund_holding_tdx_f10", "report_date"),
    "holder_count_change_pct_tdx": ("fact_holder_count_period", "report_date"),
    "avg_float_shares_change_pct_tdx": ("fact_holder_count_period", "report_date"),
    "holder_count_acceleration_tdx": ("fact_holder_count_period", "report_date"),
    "top10_concentration_change": ("fact_top10_holder_period", "report_date"),
    "tdx_inst_total_shares_qoq": ("raw_gpcw_detail", "report_date"),
    "national_team_shares_qoq": ("raw_gpcw_detail", "report_date"),
    "qfii_shares_qoq": ("raw_gpcw_detail", "report_date"),
    "fund_shares_qoq": ("raw_gpcw_detail", "report_date"),
    "social_security_shares_qoq": ("raw_gpcw_detail", "report_date"),
    "contract_liabilities_to_revenue": ("raw_gpcw_detail", "report_date"),
    "ocf_to_profit_tdx": ("raw_gpcw_detail", "report_date"),
    "receivables_to_revenue": ("raw_gpcw_detail", "report_date"),
    "inventory_to_revenue": ("raw_gpcw_detail", "report_date"),
    "forecast_profit_yoy_mid": ("raw_gpcw_detail", "report_date"),
    "forecast_range_width": ("raw_gpcw_detail", "report_date"),
    "express_net_profit_yoy": ("raw_gpcw_detail", "report_date"),
}


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


def _table_exists(conn: Any, table: str) -> bool:
    row = conn.execute(
        """
        SELECT 1
        FROM information_schema.tables
        WHERE table_schema = 'main' AND table_name = ?
        LIMIT 1
        """,
        (table,),
    ).fetchone()
    return bool(row)


def _audit_feature(conn: Any, feature_set_id: str, feature: str) -> dict[str, Any]:
    source_table, date_col = SOURCE_BY_FEATURE[feature]
    if not _table_exists(conn, source_table):
        return {
            "feature_name": feature,
            "source_table": source_table,
            "sample_rows": 0,
            "checked_rows": 0,
            "violation_rows": 0,
            "max_source_available_date": None,
            "max_signal_date": None,
            "status": "missing_source",
            "notes": f"{source_table} does not exist",
        }

    row = conn.execute(
        f"""
        WITH p AS (
            SELECT stock_code, date, {feature} AS feature_value
            FROM fact_feature_panel_candidate
            WHERE feature_set_id = ?
              AND {feature} IS NOT NULL
            ORDER BY stock_code, date
        ),
        s AS (
            SELECT stock_code, {date_col} AS source_available_date
            FROM {source_table}
            WHERE {date_col} IS NOT NULL
            ORDER BY stock_code, {date_col}
        ),
        joined AS (
            SELECT p.stock_code, p.date, s.source_available_date
            FROM p
            ASOF LEFT JOIN s
              ON p.stock_code = s.stock_code
             AND p.date >= s.source_available_date
        )
        SELECT COUNT(*) AS checked_rows,
               SUM(CASE WHEN source_available_date IS NULL THEN 1 ELSE 0 END) AS violation_rows,
               MAX(source_available_date) AS max_source_available_date,
               MAX(date) AS max_signal_date
        FROM joined
        """,
        (feature_set_id,),
    ).fetchone()
    checked = int(row["checked_rows"] or 0)
    violations = int(row["violation_rows"] or 0)
    status = "passed" if violations == 0 else "failed"
    notes = (
        f"ASOF audit using {source_table}.{date_col}; page_update_date/fetched_at are provenance fields "
        "for the current historical F10 backfill."
    )
    return {
        "feature_name": feature,
        "source_table": source_table,
        "sample_rows": checked,
        "checked_rows": checked,
        "violation_rows": violations,
        "max_source_available_date": str(row["max_source_available_date"]) if row["max_source_available_date"] is not None else None,
        "max_signal_date": str(row["max_signal_date"]) if row["max_signal_date"] is not None else None,
        "status": status,
        "notes": notes,
    }


def validate_tdx_feature_pit(
    conn: Any,
    *,
    feature_set_id: str = CANDIDATE_FEATURE_SET_ID,
    audit_run_id: str = "pit_audit",
) -> dict[str, Any]:
    ensure_tables(conn)
    audited_at = datetime.utcnow().isoformat(timespec="seconds")
    results = []
    for feature in CANDIDATE_FEATURES:
        result = _audit_feature(conn, feature_set_id, feature)
        results.append(result)

    rows = [
        (
            audit_run_id,
            feature_set_id,
            r["feature_name"],
            r["source_table"],
            r["sample_rows"],
            r["checked_rows"],
            r["violation_rows"],
            r["max_source_available_date"],
            r["max_signal_date"],
            r["status"],
            r["notes"],
            audited_at,
        )
        for r in results
    ]
    conn.executemany(
        """
        INSERT OR REPLACE INTO mart_feature_pit_audit
        (audit_run_id, feature_set_id, feature_name, source_table, sample_rows,
         checked_rows, violation_rows, max_source_available_date, max_signal_date,
         status, notes, audited_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    from services.schema_versions import record_actual_version
    record_actual_version(conn, "mart_feature_pit_audit")
    conn.commit()
    violation_rows = sum(int(r["violation_rows"] or 0) for r in results)
    return {
        "audit_run_id": audit_run_id,
        "feature_set_id": feature_set_id,
        "features": len(results),
        "violation_rows": violation_rows,
        "status": "passed" if violation_rows == 0 else "failed",
        "audited_at": audited_at,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--feature-set-id", default=CANDIDATE_FEATURE_SET_ID)
    parser.add_argument("--audit-run-id", default="pit_audit")
    args = parser.parse_args()

    conn = get_conn()
    try:
        result = validate_tdx_feature_pit(
            conn,
            feature_set_id=args.feature_set_id,
            audit_run_id=args.audit_run_id,
        )
        logger.info("PIT audit: %s", result)
        return 0 if result["violation_rows"] == 0 else 1
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
