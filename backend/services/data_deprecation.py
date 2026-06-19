"""Data-asset deprecation registry and metadata writer."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


DATA_DEPRECATION_ASSETS = [
    {
        "table_name": "market_raw_holdings",
        "replacement_table": "fact_top10_holder_period",
        "reason": "TDX F10 canonical holder table replaced legacy miaoxiang raw holdings.",
    },
    {
        "table_name": "raw_fetch_batch",
        "replacement_table": "step_status",
        "reason": "Legacy fetch batch metadata was retired; updater step_status is the active run record.",
    },
    {
        "table_name": "inst_name_aliases",
        "replacement_table": "inst_institutions",
        "reason": "Institution aliases are stored on inst_institutions and dim_holder_alias.",
    },
    {
        "table_name": "fact_institution_event_industry_snapshot",
        "replacement_table": "dim_stock_tdx_industry",
        "reason": "Industry snapshot was replaced by direct TDX industry joins.",
    },
    {
        "table_name": "fact_chain_alpha_truth",
        "replacement_table": None,
        "reason": "Legacy chain-alpha experiment table has no active writer or reader.",
    },
    {
        "table_name": "fact_hsgt_daily",
        "replacement_table": None,
        "reason": "akshare HSGT endpoint currently returns stale 2024 holdings and no active reader consumes this legacy panel.",
    },
    {
        "table_name": "fact_regime_state",
        "replacement_table": "fact_feature_panel",
        "reason": "Legacy regime snapshot has no active writer or reader; regime features now live on fact_feature_panel.",
    },
    {
        "table_name": "fact_setup_snapshot",
        "replacement_table": "services.signals_v2.build_today_signals",
        "reason": "Legacy setup snapshot is empty and superseded by signals_v2 today-signal generation.",
    },
    {
        "table_name": "fact_stock_archetype",
        "replacement_table": "mart_stock_trend",
        "reason": "Legacy stock archetype snapshot has no active writer or reader; stock scoring now lives on mart_stock_trend.",
    },
    {
        "table_name": "fact_stock_quality_features",
        "replacement_table": "mart_stock_trend",
        "reason": "Legacy quality feature snapshot has no active writer or reader; current quality scoring is materialized on mart_stock_trend.",
    },
    {
        "table_name": "dim_stock_industry",
        "replacement_table": "dim_stock_tdx_industry",
        "reason": "Legacy SW industry table was replaced by TDX industry mapping.",
    },
]


DDL = """
CREATE TABLE IF NOT EXISTS mart_data_deprecation_record (
    record_id TEXT PRIMARY KEY,
    table_name TEXT NOT NULL,
    deprecation_status TEXT NOT NULL,
    replacement_table TEXT,
    reason TEXT,
    recorded_at TEXT NOT NULL,
    dry_run BOOLEAN DEFAULT FALSE
);
ALTER TABLE dim_data_asset ADD COLUMN deprecation_status TEXT DEFAULT 'active';
ALTER TABLE dim_data_asset ADD COLUMN deprecated_at TEXT;
ALTER TABLE dim_data_asset ADD COLUMN deprecated_reason TEXT;
ALTER TABLE dim_data_asset ADD COLUMN replacement_table TEXT;
"""


def _execute_script(conn: Any, sql: str) -> None:
    if hasattr(conn, "executescript"):
        conn.executescript(sql)
        return
    for stmt in sql.split(";"):
        stmt = stmt.strip()
        if not stmt:
            continue
        try:
            conn.execute(stmt)
        except Exception as exc:
            msg = str(exc).lower()
            if "already exists" in msg or "duplicate" in msg:
                continue
            raise


def ensure_data_deprecation_tables(conn: Any) -> None:
    _execute_script(conn, DDL)


def record_data_deprecations(conn: Any, *, dry_run: bool = False) -> dict[str, Any]:
    ensure_data_deprecation_tables(conn)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    existing = {
        row[0]
        for row in conn.execute("SELECT table_name FROM dim_data_asset").fetchall()
    }
    actions = []
    for item in DATA_DEPRECATION_ASSETS:
        table = item["table_name"]
        status = "deprecated" if table in existing else "candidate_absent"
        action = {
            "table_name": table,
            "deprecation_status": status,
            "replacement_table": item["replacement_table"],
            "reason": item["reason"],
        }
        actions.append(action)
        if dry_run or table not in existing:
            continue
        conn.execute(
            """
            UPDATE dim_data_asset
            SET deprecation_status = 'deprecated',
                deprecated_at = ?,
                deprecated_reason = ?,
                replacement_table = ?,
                last_updated_at = CURRENT_TIMESTAMP
            WHERE table_name = ?
            """,
            (now, item["reason"], item["replacement_table"], table),
        )

    if not dry_run:
        rows = []
        for idx, action in enumerate(actions, 1):
            rows.append((
                f"{now}:{idx}:{action['table_name']}",
                action["table_name"],
                action["deprecation_status"],
                action["replacement_table"],
                action["reason"],
                now,
                False,
            ))
        conn.executemany(
            """
            INSERT OR REPLACE INTO mart_data_deprecation_record
            (record_id, table_name, deprecation_status, replacement_table,
             reason, recorded_at, dry_run)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        from services.schema_versions import record_actual_version
        record_actual_version(conn, "mart_data_deprecation_record")
        conn.commit()

    return {
        "dry_run": dry_run,
        "deprecated": [a for a in actions if a["deprecation_status"] == "deprecated"],
        "candidate_absent": [a for a in actions if a["deprecation_status"] == "candidate_absent"],
        "recorded_at": now,
    }
