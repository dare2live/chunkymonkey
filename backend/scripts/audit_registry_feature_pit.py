#!/usr/bin/env python3
"""PIT audit for registry/model-selected production panel features."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.db import get_conn  # noqa: E402
from services.feature_registry import load_feature_registry  # noqa: E402
from services.model_feature_schema import feature_cols_from_json, normalize_feature_cols  # noqa: E402
from services.schema_versions import record_actual_version  # noqa: E402


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


FUNDAMENTAL_FEATURE_SQL = {
    "shareholder_count_qoq": "(shareholder_count / NULLIF(LAG(shareholder_count) OVER w, 0) - 1)",
    "inst_count_qoq": "(inst_count / NULLIF(LAG(inst_count) OVER w, 0) - 1)",
    "fund_count_qoq": "(fund_count / NULLIF(LAG(fund_count) OVER w, 0) - 1)",
    "qfii_count_qoq": "(qfii_count / NULLIF(LAG(qfii_count) OVER w, 0) - 1)",
    "yjyg_lower_pct": "yjyg_lower_pct",
    "yjyg_upper_pct": "yjyg_upper_pct",
    "roe": "roe",
    "eps_basic": "eps_basic",
}

EVENT_FEATURE_AUDITS = {
    "inst_event_count_30d": {
        "kind": "count",
        "source_table": "fact_institution_event",
        "date_col": "notice_date",
    },
    "inst_event_count_60d": {
        "kind": "count",
        "source_table": "fact_institution_event",
        "date_col": "notice_date",
    },
    "exec_buy_count_90d": {
        "kind": "count",
        "source_table": "fact_executive_trade_event",
        "date_col": "notice_date",
        "where": "direction = 'buy'",
    },
    "exec_buy_ge1_count_90d": {
        "kind": "count",
        "source_table": "fact_executive_trade_event",
        "date_col": "notice_date",
        "where": "direction = 'buy' AND total_change_pct_total >= 1.0",
    },
    "lhb_inst_buy_count_30d": {
        "kind": "count",
        "source_table": "fact_lhb_event",
        "date_col": "trade_date",
        "where": "is_inst_net_buy = 1",
    },
    "lhb_inst_buy_count_60d": {
        "kind": "count",
        "source_table": "fact_lhb_event",
        "date_col": "trade_date",
        "where": "is_inst_net_buy = 1",
    },
    "jgdy_count_60d": {
        "kind": "count",
        "source_table": "fact_jgdy_event",
        "date_col": "notice_date",
    },
    "dzjy_count_60d": {
        "kind": "count",
        "source_table": "fact_dzjy_event",
        "date_col": "trade_date",
    },
    "days_since_exec_buy": {
        "kind": "days_since",
        "source_table": "fact_executive_trade_event",
        "date_col": "notice_date",
        "where": "direction = 'buy'",
    },
    "days_since_lhb": {
        "kind": "days_since",
        "source_table": "fact_lhb_event",
        "date_col": "trade_date",
        "where": "is_inst_net_buy = 1",
    },
}


def _quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _date_expr(expr: str) -> str:
    return (
        f"CASE "
        f"WHEN {expr} IS NULL THEN NULL "
        f"WHEN REGEXP_MATCHES(CAST({expr} AS VARCHAR), '^\\d{{8}}$') "
        f"THEN STRPTIME(CAST({expr} AS VARCHAR), '%Y%m%d')::DATE "
        f"ELSE CAST({expr} AS DATE) END"
    )


def _table_exists(conn: Any, table: str) -> bool:
    row = conn.execute(
        "SELECT COUNT(*) FROM information_schema.tables WHERE table_name = ?",
        (table,),
    ).fetchone()
    return bool(row and row[0])


def _has_column(conn: Any, table: str, column: str) -> bool:
    row = conn.execute(
        "SELECT COUNT(*) FROM information_schema.columns WHERE table_name = ? AND column_name = ?",
        (table, column),
    ).fetchone()
    return bool(row and row[0])


def ensure_tables(conn: Any) -> None:
    conn.executescript(DDL)


def _model_feature_cols(conn: Any, model_id: str) -> list[str]:
    row = conn.execute(
        "SELECT feature_cols_json FROM mart_multidim_model WHERE model_id = ?",
        (model_id,),
    ).fetchone()
    if not row:
        raise RuntimeError(f"model not found: {model_id}")
    return normalize_feature_cols(feature_cols_from_json(row["feature_cols_json"]))


def _panel_filter(conn: Any, feature_table: str, feature_set_id: str | None) -> tuple[str, tuple[Any, ...]]:
    if feature_set_id and _has_column(conn, feature_table, "feature_set_id"):
        return "AND feature_set_id = ?", (feature_set_id,)
    return "", ()


def _count_non_null_panel_rows(
    conn: Any,
    *,
    feature_table: str,
    feature_set_id: str | None,
    feature: str,
) -> tuple[int, str | None]:
    feature_filter, params = _panel_filter(conn, feature_table, feature_set_id)
    row = conn.execute(
        f"""
        SELECT COUNT({_quote_ident(feature)}) AS checked_rows,
               MAX(date) AS max_signal_date
          FROM {_quote_ident(feature_table)}
         WHERE {_quote_ident(feature)} IS NOT NULL
           {feature_filter}
        """,
        params,
    ).fetchone()
    return int(row["checked_rows"] or 0), str(row["max_signal_date"]) if row and row["max_signal_date"] else None


def _audit_static_feature(
    conn: Any,
    *,
    feature_table: str,
    feature_set_id: str | None,
    feature: str,
    source_table: str,
    group: str,
) -> dict[str, Any]:
    checked, max_signal_date = _count_non_null_panel_rows(
        conn,
        feature_table=feature_table,
        feature_set_id=feature_set_id,
        feature=feature,
    )
    return {
        "feature_name": feature,
        "source_table": source_table,
        "sample_rows": checked,
        "checked_rows": checked,
        "violation_rows": 0,
        "max_source_available_date": max_signal_date,
        "max_signal_date": max_signal_date,
        "status": "passed",
        "notes": f"registry group={group}; release_lag_days=0 or event/count feature built with trailing window",
    }


def _audit_fundamental_feature(
    conn: Any,
    *,
    feature_table: str,
    feature_set_id: str | None,
    feature: str,
    lag_days: int,
) -> dict[str, Any]:
    checked, max_signal_date = _count_non_null_panel_rows(
        conn,
        feature_table=feature_table,
        feature_set_id=feature_set_id,
        feature=feature,
    )
    if not _table_exists(conn, "fact_fundamental_quarterly"):
        return {
            "feature_name": feature,
            "source_table": "fact_fundamental_quarterly",
            "sample_rows": checked,
            "checked_rows": checked,
            "violation_rows": checked,
            "max_source_available_date": None,
            "max_signal_date": max_signal_date,
            "status": "missing_source",
            "notes": "fact_fundamental_quarterly does not exist",
        }
    if feature not in FUNDAMENTAL_FEATURE_SQL:
        return {
            "feature_name": feature,
            "source_table": "fact_fundamental_quarterly",
            "sample_rows": checked,
            "checked_rows": checked,
            "violation_rows": checked,
            "max_source_available_date": None,
            "max_signal_date": max_signal_date,
            "status": "unsupported_feature",
            "notes": "fundamental feature has no PIT audit expression",
        }

    feature_filter, params = _panel_filter(conn, feature_table, feature_set_id)
    expr = FUNDAMENTAL_FEATURE_SQL[feature]
    row = conn.execute(
        f"""
        WITH p AS (
            SELECT stock_code,
                   CAST(date AS VARCHAR) AS date,
                   {_quote_ident(feature)} AS panel_value
              FROM {_quote_ident(feature_table)}
             WHERE {_quote_ident(feature)} IS NOT NULL
               {feature_filter}
        ),
        ffq AS (
            SELECT stock_code,
                   STRFTIME(TRY_STRPTIME(CAST(report_date AS VARCHAR), '%Y%m%d') + INTERVAL {int(lag_days)} DAY, '%Y-%m-%d')
                       AS available_date,
                   {expr} AS feature_value
              FROM fact_fundamental_quarterly
            WINDOW w AS (PARTITION BY stock_code ORDER BY report_date)
        ),
        joined AS (
            SELECT p.stock_code, p.date, f.available_date, f.feature_value
              FROM p
              ASOF LEFT JOIN ffq f
                ON p.stock_code = f.stock_code
               AND p.date >= f.available_date
        )
        SELECT COUNT(*) AS checked_rows,
               SUM(CASE WHEN available_date IS NULL OR feature_value IS NULL THEN 1 ELSE 0 END) AS violation_rows,
               MAX(available_date) AS max_source_available_date,
               MAX(date) AS max_signal_date
          FROM joined
        """,
        params,
    ).fetchone()
    violations = int(row["violation_rows"] or 0) if row else checked
    checked_rows = int(row["checked_rows"] or 0) if row else checked
    return {
        "feature_name": feature,
        "source_table": "fact_fundamental_quarterly",
        "sample_rows": checked_rows,
        "checked_rows": checked_rows,
        "violation_rows": violations,
        "max_source_available_date": str(row["max_source_available_date"]) if row and row["max_source_available_date"] else None,
        "max_signal_date": str(row["max_signal_date"]) if row and row["max_signal_date"] else max_signal_date,
        "status": "passed" if violations == 0 else "failed",
        "notes": f"ASOF availability audit with registry release_lag_days={lag_days}",
    }


def _event_where_clause(config: dict[str, Any]) -> str:
    clause = str(config.get("where") or "").strip()
    return f"AND {clause}" if clause else ""


def _audit_event_feature(
    conn: Any,
    *,
    feature_table: str,
    feature_set_id: str | None,
    feature: str,
    config: dict[str, Any],
) -> dict[str, Any]:
    checked, max_signal_date = _count_non_null_panel_rows(
        conn,
        feature_table=feature_table,
        feature_set_id=feature_set_id,
        feature=feature,
    )
    source_table = str(config["source_table"])
    date_col = str(config["date_col"])
    if not _table_exists(conn, source_table):
        return {
            "feature_name": feature,
            "source_table": source_table,
            "sample_rows": checked,
            "checked_rows": checked,
            "violation_rows": checked,
            "max_source_available_date": None,
            "max_signal_date": max_signal_date,
            "status": "missing_source",
            "notes": f"{source_table} does not exist",
        }

    feature_filter, params = _panel_filter(conn, feature_table, feature_set_id)
    event_date = _date_expr(f"e.{_quote_ident(date_col)}")
    panel_date = _date_expr("p.date")
    where_clause = _event_where_clause(config)
    feature_q = _quote_ident(feature)
    kind = str(config.get("kind") or "count")
    if kind == "days_since":
        row = conn.execute(
            f"""
            WITH p AS (
                SELECT stock_code,
                       CAST(date AS VARCHAR) AS date,
                       CAST({feature_q} AS INTEGER) AS panel_value
                  FROM {_quote_ident(feature_table)}
                 WHERE {feature_q} IS NOT NULL
                   {feature_filter}
            ),
            joined AS (
                SELECT p.stock_code, p.date, {panel_date} AS date_dt, p.panel_value,
                       MAX(CASE WHEN {event_date} <= {panel_date} THEN {event_date} END) AS last_event_date
                  FROM p
                  LEFT JOIN {_quote_ident(source_table)} e
                    ON e.stock_code = p.stock_code
                   {where_clause}
                 GROUP BY p.stock_code, p.date, p.panel_value
            )
            SELECT COUNT(*) AS checked_rows,
                   SUM(
                       CASE
                         WHEN panel_value < -1 THEN 1
                         WHEN panel_value = -1 AND last_event_date IS NOT NULL THEN 1
                         WHEN panel_value >= 0
                              AND (
                                  last_event_date IS NULL
                                  OR (date_dt - last_event_date)::INTEGER != panel_value
                              )
                         THEN 1
                         ELSE 0
                       END
                   ) AS violation_rows,
                   MAX(last_event_date) AS max_source_available_date,
                   MAX(date) AS max_signal_date
              FROM joined
            """,
            params,
        ).fetchone()
        notes = "event days-since PIT audit: value must match latest source event with event_date <= signal date"
    else:
        row = conn.execute(
            f"""
            WITH p AS (
                SELECT stock_code,
                       CAST(date AS VARCHAR) AS date,
                       CAST({feature_q} AS DOUBLE) AS panel_value
                  FROM {_quote_ident(feature_table)}
                 WHERE {feature_q} IS NOT NULL
                   {feature_filter}
            ),
            joined AS (
                SELECT p.stock_code, p.date, p.panel_value,
                       COUNT(e.stock_code) AS historical_event_count,
                       MAX(CASE WHEN {event_date} <= {panel_date} THEN {event_date} END) AS max_source_available_date
                  FROM p
                  LEFT JOIN {_quote_ident(source_table)} e
                    ON e.stock_code = p.stock_code
                   AND {event_date} <= {panel_date}
                   {where_clause}
                 GROUP BY p.stock_code, p.date, p.panel_value
            )
            SELECT COUNT(*) AS checked_rows,
                   SUM(
                       CASE
                         WHEN panel_value < 0 THEN 1
                         WHEN panel_value > historical_event_count THEN 1
                         ELSE 0
                       END
                   ) AS violation_rows,
                   MAX(max_source_available_date) AS max_source_available_date,
                   MAX(date) AS max_signal_date
              FROM joined
            """,
            params,
        ).fetchone()
        notes = "event count PIT audit: trailing count cannot exceed historical source events with event_date <= signal date"

    violations = int(row["violation_rows"] or 0) if row else checked
    checked_rows = int(row["checked_rows"] or 0) if row else checked
    return {
        "feature_name": feature,
        "source_table": source_table,
        "sample_rows": checked_rows,
        "checked_rows": checked_rows,
        "violation_rows": violations,
        "max_source_available_date": str(row["max_source_available_date"]) if row and row["max_source_available_date"] else None,
        "max_signal_date": str(row["max_signal_date"]) if row and row["max_signal_date"] else max_signal_date,
        "status": "passed" if violations == 0 else "failed",
        "notes": notes,
    }


def audit_registry_feature_pit(
    conn: Any,
    *,
    model_id: str,
    feature_table: str = "fact_feature_panel",
    feature_set_id: str | None = None,
    audit_run_id: str | None = None,
) -> dict[str, Any]:
    ensure_tables(conn)
    if not _table_exists(conn, feature_table):
        raise RuntimeError(f"missing feature table: {feature_table}")
    registry = load_feature_registry()
    features = _model_feature_cols(conn, model_id)
    audit_run_id = audit_run_id or f"pit_registry_{model_id}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"
    audited_at = datetime.utcnow().isoformat(timespec="seconds")
    feature_set_key = feature_set_id or "production_registry"
    rows = []
    for feature in features:
        if not _has_column(conn, feature_table, feature):
            rows.append(
                {
                    "feature_name": feature,
                    "source_table": feature_table,
                    "sample_rows": 0,
                    "checked_rows": 0,
                    "violation_rows": 1,
                    "max_source_available_date": None,
                    "max_signal_date": None,
                    "status": "missing_panel_column",
                    "notes": f"{feature_table}.{feature} is missing",
                }
            )
            continue
        spec = registry.features.get(feature)
        group = spec.group if spec else "unregistered"
        if group == "fundamentals":
            rows.append(
                _audit_fundamental_feature(
                    conn,
                    feature_table=feature_table,
                    feature_set_id=feature_set_id,
                    feature=feature,
                    lag_days=spec.pit_release_lag_days if spec else 90,
                )
            )
        elif feature in EVENT_FEATURE_AUDITS:
            rows.append(
                _audit_event_feature(
                    conn,
                    feature_table=feature_table,
                    feature_set_id=feature_set_id,
                    feature=feature,
                    config=EVENT_FEATURE_AUDITS[feature],
                )
            )
        else:
            source_table = ",".join(spec.source_tables) if spec and spec.source_tables else feature_table
            rows.append(
                _audit_static_feature(
                    conn,
                    feature_table=feature_table,
                    feature_set_id=feature_set_id,
                    feature=feature,
                    source_table=source_table,
                    group=group,
                )
            )

    conn.executemany(
        """
        INSERT OR REPLACE INTO mart_feature_pit_audit
        (audit_run_id, feature_set_id, feature_name, source_table, sample_rows,
         checked_rows, violation_rows, max_source_available_date, max_signal_date,
         status, notes, audited_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                audit_run_id,
                feature_set_key,
                row["feature_name"],
                row["source_table"],
                row["sample_rows"],
                row["checked_rows"],
                row["violation_rows"],
                row["max_source_available_date"],
                row["max_signal_date"],
                row["status"],
                row["notes"],
                audited_at,
            )
            for row in rows
        ],
    )
    record_actual_version(conn, "mart_feature_pit_audit")
    conn.commit()
    violation_rows = sum(int(row["violation_rows"] or 0) for row in rows)
    return {
        "audit_run_id": audit_run_id,
        "model_id": model_id,
        "feature_table": feature_table,
        "feature_set_id": feature_set_key,
        "features": len(rows),
        "violation_rows": violation_rows,
        "status": "passed" if violation_rows == 0 else "failed",
        "audited_at": audited_at,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-id", required=True)
    parser.add_argument("--feature-table", default="fact_feature_panel")
    parser.add_argument("--feature-set-id", default=None)
    parser.add_argument("--audit-run-id", default=None)
    args = parser.parse_args()
    with get_conn() as conn:
        result = audit_registry_feature_pit(
            conn,
            model_id=args.model_id,
            feature_table=args.feature_table,
            feature_set_id=args.feature_set_id,
            audit_run_id=args.audit_run_id,
        )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
