#!/usr/bin/env python3
"""PIT audit for registry/model-selected production panel features."""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.db import get_conn  # noqa: E402
from services.feature_registry import FeatureRegistry, FeatureSpec, load_feature_registry  # noqa: E402
from services.model_feature_schema import feature_cols_from_json, normalize_feature_cols  # noqa: E402
from services.pipeline_manifest import git_commit_sha, record_pipeline_run, utc_now_iso  # noqa: E402
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
    pit_risk_level TEXT,
    pit_audit_scope TEXT,
    notes TEXT,
    audited_at TEXT,
    PRIMARY KEY (audit_run_id, feature_set_id, feature_name, source_table)
);
ALTER TABLE mart_feature_pit_audit ADD COLUMN IF NOT EXISTS pit_risk_level TEXT;
ALTER TABLE mart_feature_pit_audit ADD COLUMN IF NOT EXISTS pit_audit_scope TEXT;

CREATE TABLE IF NOT EXISTS mart_feature_pit_coverage_summary (
    audit_run_id TEXT NOT NULL,
    feature_set_id TEXT NOT NULL,
    feature_table TEXT NOT NULL,
    audit_scope TEXT NOT NULL,
    total_columns INTEGER,
    audited_columns INTEGER,
    passed_columns INTEGER,
    failed_columns INTEGER,
    unknown_blocking_columns INTEGER,
    missing_source_columns INTEGER,
    not_applicable_columns INTEGER,
    high_risk_columns INTEGER,
    critical_risk_columns INTEGER,
    audited_at TEXT,
    PRIMARY KEY (audit_run_id, feature_set_id, audit_scope)
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
        "window_rows": 30,
    },
    "inst_event_count_60d": {
        "kind": "count",
        "source_table": "fact_institution_event",
        "date_col": "notice_date",
        "window_rows": 60,
    },
    "exec_buy_count_90d": {
        "kind": "count",
        "source_table": "fact_executive_trade_event",
        "date_col": "notice_date",
        "where": "direction = 'buy'",
        "window_rows": 90,
    },
    "exec_buy_ge1_count_90d": {
        "kind": "count",
        "source_table": "fact_executive_trade_event",
        "date_col": "notice_date",
        "where": "direction = 'buy' AND total_change_pct_total >= 1.0",
        "window_rows": 90,
    },
    "lhb_inst_buy_count_30d": {
        "kind": "count",
        "source_table": "fact_lhb_event",
        "date_col": "trade_date",
        "where": "is_inst_net_buy = 1",
        "window_rows": 30,
    },
    "lhb_inst_buy_count_60d": {
        "kind": "count",
        "source_table": "fact_lhb_event",
        "date_col": "trade_date",
        "where": "is_inst_net_buy = 1",
        "window_rows": 60,
    },
    "jgdy_count_60d": {
        "kind": "count",
        "source_table": "fact_jgdy_event",
        "date_col": "notice_date",
        "window_rows": 60,
    },
    "dzjy_count_60d": {
        "kind": "count",
        "source_table": "fact_dzjy_event",
        "date_col": "trade_date",
        "window_rows": 60,
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
    "shareholder_plan_increase_count_180d": {
        "kind": "count",
        "source_table": "fact_shareholder_plan_tdx_f10",
        "date_col": "source_available_date",
        "where": "direction LIKE '%增持%'",
        "window_rows": 180,
    },
    "shareholder_plan_decrease_count_180d": {
        "kind": "count",
        "source_table": "fact_shareholder_plan_tdx_f10",
        "date_col": "source_available_date",
        "where": "direction LIKE '%减持%'",
        "window_rows": 180,
    },
    "shareholder_plan_completed_count_180d": {
        "kind": "count",
        "source_table": "fact_shareholder_plan_tdx_f10",
        "date_col": "source_available_date",
        "where": "progress LIKE '%完成%'",
        "window_rows": 180,
    },
    "shareholder_plan_increase_amount_max_180d": {
        "kind": "sum",
        "source_table": "fact_shareholder_plan_tdx_f10",
        "date_col": "source_available_date",
        "value_col": "target_amount_max",
        "fallback_value_col": "target_amount_min",
        "where": "direction LIKE '%增持%'",
        "window_rows": 180,
    },
    "shareholder_plan_decrease_amount_max_180d": {
        "kind": "sum",
        "source_table": "fact_shareholder_plan_tdx_f10",
        "date_col": "source_available_date",
        "value_col": "target_amount_max",
        "fallback_value_col": "target_amount_min",
        "where": "direction LIKE '%减持%'",
        "window_rows": 180,
    },
    "days_since_shareholder_plan_increase": {
        "kind": "days_since",
        "source_table": "fact_shareholder_plan_tdx_f10",
        "date_col": "source_available_date",
        "where": "direction LIKE '%增持%'",
    },
    "days_since_shareholder_plan_decrease": {
        "kind": "days_since",
        "source_table": "fact_shareholder_plan_tdx_f10",
        "date_col": "source_available_date",
        "where": "direction LIKE '%减持%'",
    },
}

BLOCKING_AUDIT_STATUSES = {
    "failed",
    "missing_panel_column",
    "missing_source",
    "unsupported_feature",
    "unknown_blocking",
    "zero_coverage_blocking",
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


def _table_columns(conn: Any, table: str) -> set[str]:
    return {str(row[0]) for row in conn.execute(f"DESCRIBE {_quote_ident(table)}").fetchall()}


STRUCTURAL_COLUMNS = {
    "stock_code",
    "stock_name",
    "date",
    "built_at",
    "updated_at",
    "feature_set_id",
    "regime_flag",
    "kline_source_name",
    "kline_source_tier",
    "kline_is_fallback",
}


def _base_feature_for_transform(feature: str, registry: FeatureRegistry) -> str | None:
    for suffix in ("_xs_rank", "_xs_bucket5", "_xs_bucket10", "_zscore", "_winsor"):
        if feature.endswith(suffix):
            base = feature[: -len(suffix)]
            if base in registry.features:
                return base
    return None


def _pit_risk_level(feature: str, spec: FeatureSpec | None, registry: FeatureRegistry | None = None) -> str:
    if feature in STRUCTURAL_COLUMNS:
        return "not_applicable"
    if feature in {"regime_up", "regime_flat", "regime_down"}:
        return "low"
    if registry is not None and spec is None:
        base = _base_feature_for_transform(feature, registry)
        if base:
            return _pit_risk_level(base, registry.features.get(base), registry)
    if spec and spec.label:
        return "not_applicable"
    if feature.startswith("forward_ret_"):
        return "not_applicable"
    if spec is None:
        return "critical"
    if spec.group == "fundamentals" or spec.pit_release_lag_days > 0:
        return "high"
    if feature in EVENT_FEATURE_AUDITS:
        return "medium"
    if spec.group in {"price_volume", "alpha158_price_shape", "cross_sectional", "regime", "kline_lineage"}:
        return "low"
    return "critical" if not spec.source_tables else "medium"


def _with_scope(row: dict[str, Any], *, risk_level: str, audit_scope: str) -> dict[str, Any]:
    row["pit_risk_level"] = risk_level
    row["pit_audit_scope"] = audit_scope
    if (
        risk_level in {"high", "critical"}
        and int(row.get("checked_rows") or 0) == 0
        and row.get("status") in {"passed", "not_applicable"}
    ):
        row["status"] = "zero_coverage_blocking"
        row["violation_rows"] = max(int(row.get("violation_rows") or 0), 1)
        row["notes"] = f"{row.get('notes') or ''}; high/critical feature has zero non-null rows and is not PIT-verifiable"
    return row


def _unknown_blocking_row(
    conn: Any,
    *,
    feature_table: str,
    feature_set_id: str | None,
    feature: str,
    source_table: str,
    risk_level: str,
    audit_scope: str,
    notes: str,
) -> dict[str, Any]:
    checked, max_signal_date = _count_non_null_panel_rows(
        conn,
        feature_table=feature_table,
        feature_set_id=feature_set_id,
        feature=feature,
    )
    if checked == 0:
        return _with_scope(
            {
                "feature_name": feature,
                "source_table": source_table,
                "sample_rows": 0,
                "checked_rows": 0,
                "violation_rows": 0,
                "max_source_available_date": None,
                "max_signal_date": max_signal_date,
                "status": "not_applicable",
                "notes": f"{notes}; no non-null rows in audited feature set",
            },
            risk_level=risk_level,
            audit_scope=audit_scope,
        )
    return _with_scope(
        {
            "feature_name": feature,
            "source_table": source_table,
            "sample_rows": checked,
            "checked_rows": checked,
            "violation_rows": checked,
            "max_source_available_date": None,
            "max_signal_date": max_signal_date,
            "status": "unknown_blocking",
            "notes": notes,
        },
        risk_level=risk_level,
        audit_scope=audit_scope,
    )


def _not_applicable_row(
    conn: Any,
    *,
    feature_table: str,
    feature_set_id: str | None,
    feature: str,
    risk_level: str,
    audit_scope: str,
    notes: str,
) -> dict[str, Any]:
    checked, max_signal_date = _count_non_null_panel_rows(
        conn,
        feature_table=feature_table,
        feature_set_id=feature_set_id,
        feature=feature,
    )
    return _with_scope(
        {
            "feature_name": feature,
            "source_table": feature_table,
            "sample_rows": checked,
            "checked_rows": checked,
            "violation_rows": 0,
            "max_source_available_date": max_signal_date,
            "max_signal_date": max_signal_date,
            "status": "not_applicable",
            "notes": notes,
        },
        risk_level=risk_level,
        audit_scope=audit_scope,
    )


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
    elif kind in {"count", "sum"} and int(config.get("window_rows") or 0) > 0:
        window_rows = int(config["window_rows"])
        value_col = str(config.get("value_col") or "")
        fallback_value_col = str(config.get("fallback_value_col") or "")
        if kind == "sum":
            if not value_col or not _has_column(conn, source_table, value_col):
                return {
                    "feature_name": feature,
                    "source_table": source_table,
                    "sample_rows": checked,
                    "checked_rows": checked,
                    "violation_rows": checked,
                    "max_source_available_date": None,
                    "max_signal_date": max_signal_date,
                    "status": "unsupported_feature",
                    "notes": f"sum event audit missing value_col={value_col}",
                }
            value_terms = [f"TRY_CAST(e.{_quote_ident(value_col)} AS DOUBLE)"]
            if fallback_value_col and _has_column(conn, source_table, fallback_value_col):
                value_terms.append(f"TRY_CAST(e.{_quote_ident(fallback_value_col)} AS DOUBLE)")
            value_expr = f"COALESCE({', '.join(value_terms)}, 0.0)"
        else:
            value_expr = "1.0"
        all_filter_params = tuple(params) + tuple(params)
        row = conn.execute(
            f"""
            WITH panel_all AS (
                SELECT stock_code,
                       CAST(date AS VARCHAR) AS date,
                       {_date_expr('date')} AS date_dt
                  FROM {_quote_ident(feature_table)}
                 WHERE date IS NOT NULL
                   {feature_filter}
            ),
            checked AS (
                SELECT stock_code,
                       CAST(date AS VARCHAR) AS date,
                       CAST({feature_q} AS DOUBLE) AS panel_value
                  FROM {_quote_ident(feature_table)}
                 WHERE {feature_q} IS NOT NULL
                   {feature_filter}
            ),
            panel_bounds AS (
                SELECT MIN(date_dt) AS min_panel_date FROM panel_all
            ),
            ev_raw AS (
                SELECT e.stock_code,
                       {event_date} AS event_dt,
                       {value_expr} AS event_value,
                       ROW_NUMBER() OVER () AS event_id
                  FROM {_quote_ident(source_table)} e
                 WHERE e.stock_code IS NOT NULL
                   AND e.{_quote_ident(date_col)} IS NOT NULL
                   {where_clause}
            ),
            ev_aligned AS (
                SELECT e.stock_code,
                       MIN(p.date) AS date,
                       ANY_VALUE(e.event_dt) AS event_dt,
                       ANY_VALUE(e.event_value) AS event_value
                  FROM ev_raw e
                  JOIN panel_all p
                    ON p.stock_code = e.stock_code
                   AND p.date_dt >= e.event_dt
                  CROSS JOIN panel_bounds b
                 WHERE e.event_dt >= b.min_panel_date
                 GROUP BY e.stock_code, e.event_id
            ),
            ev_daily AS (
                SELECT stock_code, date,
                       SUM(event_value) AS event_value,
                       MAX(event_dt) AS max_event_dt
                  FROM ev_aligned
                 GROUP BY stock_code, date
            ),
            panel_ev AS (
                SELECT p.stock_code, p.date,
                       COALESCE(e.event_value, 0.0) AS event_value,
                       e.max_event_dt
                  FROM panel_all p
                  LEFT JOIN ev_daily e
                    ON e.stock_code = p.stock_code
                   AND e.date = p.date
            ),
            rolled AS (
                SELECT stock_code,
                       date,
                       SUM(event_value) OVER (
                           PARTITION BY stock_code
                           ORDER BY date
                           ROWS {window_rows - 1} PRECEDING
                       ) AS expected_value,
                       MAX(max_event_dt) OVER (
                           PARTITION BY stock_code
                           ORDER BY date
                           ROWS {window_rows - 1} PRECEDING
                       ) AS max_source_available_date
                  FROM panel_ev
            ),
            joined AS (
                SELECT c.stock_code, c.date, c.panel_value,
                       COALESCE(r.expected_value, 0.0) AS expected_value,
                       r.max_source_available_date
                  FROM checked c
                  LEFT JOIN rolled r
                    ON r.stock_code = c.stock_code
                   AND r.date = c.date
            )
            SELECT COUNT(*) AS checked_rows,
                   SUM(
                       CASE
                         WHEN panel_value < 0 THEN 1
                         WHEN ABS(panel_value - expected_value) > 1e-6 THEN 1
                         ELSE 0
                       END
                   ) AS violation_rows,
                   MAX(max_source_available_date) AS max_source_available_date,
                   MAX(date) AS max_signal_date
              FROM joined
            """,
            all_filter_params,
        ).fetchone()
        notes = (
            f"event {kind} PIT audit: value must equal source events aligned to first "
            f"panel date >= source date over trailing {window_rows} panel rows"
        )
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
    model_id: str | None = None,
    feature_names: list[str] | None = None,
    feature_table: str = "fact_feature_panel",
    feature_set_id: str | None = None,
    audit_run_id: str | None = None,
    audit_scope: str = "model_selected",
) -> dict[str, Any]:
    ensure_tables(conn)
    if not _table_exists(conn, feature_table):
        raise RuntimeError(f"missing feature table: {feature_table}")
    registry = load_feature_registry()
    if feature_names is not None:
        features = normalize_feature_cols(feature_names)
        model_key = model_id or "explicit_feature_list"
    else:
        if not model_id:
            raise RuntimeError("model_id is required when feature_names is not provided")
        features = _model_feature_cols(conn, model_id)
        model_key = model_id
    audit_run_id = audit_run_id or f"pit_registry_{model_key}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"
    audited_at = datetime.now(UTC).replace(tzinfo=None).isoformat(timespec="seconds")
    feature_set_key = feature_set_id or "production_registry"
    rows = []
    for feature in features:
        spec = registry.features.get(feature)
        risk_level = _pit_risk_level(feature, spec, registry)
        if not _has_column(conn, feature_table, feature):
            rows.append(
                _with_scope(
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
                    },
                    risk_level=risk_level,
                    audit_scope=audit_scope,
                )
            )
            continue
        group = spec.group if spec else "unregistered"
        if group == "fundamentals":
            rows.append(
                _with_scope(
                    _audit_fundamental_feature(
                        conn,
                        feature_table=feature_table,
                        feature_set_id=feature_set_id,
                        feature=feature,
                        lag_days=spec.pit_release_lag_days if spec else 90,
                    ),
                    risk_level=risk_level,
                    audit_scope=audit_scope,
                )
            )
        elif feature in EVENT_FEATURE_AUDITS:
            rows.append(
                _with_scope(
                    _audit_event_feature(
                        conn,
                        feature_table=feature_table,
                        feature_set_id=feature_set_id,
                        feature=feature,
                        config=EVENT_FEATURE_AUDITS[feature],
                    ),
                    risk_level=risk_level,
                    audit_scope=audit_scope,
                )
            )
        else:
            source_table = ",".join(spec.source_tables) if spec and spec.source_tables else feature_table
            rows.append(
                _with_scope(
                    _audit_static_feature(
                        conn,
                        feature_table=feature_table,
                        feature_set_id=feature_set_id,
                        feature=feature,
                        source_table=source_table,
                        group=group,
                    ),
                    risk_level=risk_level,
                    audit_scope=audit_scope,
                )
            )
    _write_audit_rows(
        conn,
        audit_run_id=audit_run_id,
        feature_set_key=feature_set_key,
        feature_table=feature_table,
        audit_scope=audit_scope,
        audited_at=audited_at,
        rows=rows,
    )
    conn.commit()
    violation_rows = sum(int(row["violation_rows"] or 0) for row in rows)
    return {
        "audit_run_id": audit_run_id,
        "model_id": model_key,
        "feature_table": feature_table,
        "feature_set_id": feature_set_key,
        "features": len(rows),
        "violation_rows": violation_rows,
        "status": "passed" if violation_rows == 0 else "failed",
        "unknown_blocking": sum(1 for row in rows if row.get("status") == "unknown_blocking"),
        "audited_at": audited_at,
    }


def _write_audit_rows(
    conn: Any,
    *,
    audit_run_id: str,
    feature_set_key: str,
    feature_table: str,
    audit_scope: str,
    audited_at: str,
    rows: list[dict[str, Any]],
) -> None:
    if rows:
        conn.executemany(
            """
            INSERT OR REPLACE INTO mart_feature_pit_audit
            (audit_run_id, feature_set_id, feature_name, source_table, sample_rows,
             checked_rows, violation_rows, max_source_available_date, max_signal_date,
             status, pit_risk_level, pit_audit_scope, notes, audited_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    row.get("pit_risk_level"),
                    row.get("pit_audit_scope") or audit_scope,
                    row["notes"],
                    audited_at,
                )
                for row in rows
            ],
        )
    total = len(rows)
    conn.execute(
        """
        INSERT OR REPLACE INTO mart_feature_pit_coverage_summary
        (audit_run_id, feature_set_id, feature_table, audit_scope, total_columns,
         audited_columns, passed_columns, failed_columns, unknown_blocking_columns,
         missing_source_columns, not_applicable_columns, high_risk_columns,
         critical_risk_columns, audited_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            audit_run_id,
            feature_set_key,
            feature_table,
            audit_scope,
            total,
            sum(1 for row in rows if row.get("status") not in {"not_applicable"}),
            sum(1 for row in rows if row.get("status") == "passed"),
            sum(1 for row in rows if row.get("status") in BLOCKING_AUDIT_STATUSES),
            sum(1 for row in rows if row.get("status") == "unknown_blocking"),
            sum(1 for row in rows if row.get("status") == "missing_source"),
            sum(1 for row in rows if row.get("status") == "not_applicable"),
            sum(1 for row in rows if row.get("pit_risk_level") == "high"),
            sum(1 for row in rows if row.get("pit_risk_level") == "critical"),
            audited_at,
        ),
    )
    record_actual_version(conn, "mart_feature_pit_audit")
    record_actual_version(conn, "mart_feature_pit_coverage_summary")


def audit_high_critical_feature_pit(
    conn: Any,
    *,
    feature_table: str = "fact_feature_panel_candidate",
    feature_set_id: str | None = None,
    audit_run_id: str | None = None,
    include_not_applicable: bool = True,
) -> dict[str, Any]:
    ensure_tables(conn)
    if not _table_exists(conn, feature_table):
        raise RuntimeError(f"missing feature table: {feature_table}")
    registry = load_feature_registry()
    table_cols = sorted(_table_columns(conn, feature_table))
    audit_run_id = audit_run_id or f"pit_high_critical_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"
    audited_at = datetime.now(UTC).replace(tzinfo=None).isoformat(timespec="seconds")
    feature_set_key = feature_set_id or "all_feature_table"
    rows: list[dict[str, Any]] = []
    for feature in table_cols:
        spec = registry.features.get(feature)
        risk_level = _pit_risk_level(feature, spec, registry)
        if risk_level not in {"high", "critical"}:
            if include_not_applicable:
                rows.append(
                    _not_applicable_row(
                        conn,
                        feature_table=feature_table,
                        feature_set_id=feature_set_id,
                        feature=feature,
                        risk_level=risk_level,
                        audit_scope="all_high_critical",
                        notes=f"risk={risk_level}; outside high/critical production gate",
                    )
                )
            continue
        if spec and spec.group == "fundamentals":
            rows.append(
                _with_scope(
                    _audit_fundamental_feature(
                        conn,
                        feature_table=feature_table,
                        feature_set_id=feature_set_id,
                        feature=feature,
                        lag_days=spec.pit_release_lag_days,
                    ),
                    risk_level=risk_level,
                    audit_scope="all_high_critical",
                )
            )
        elif feature in EVENT_FEATURE_AUDITS:
            rows.append(
                _with_scope(
                    _audit_event_feature(
                        conn,
                        feature_table=feature_table,
                        feature_set_id=feature_set_id,
                        feature=feature,
                        config=EVENT_FEATURE_AUDITS[feature],
                    ),
                    risk_level=risk_level,
                    audit_scope="all_high_critical",
                )
            )
        else:
            source_table = ",".join(spec.source_tables) if spec and spec.source_tables else feature_table
            rows.append(
                _unknown_blocking_row(
                    conn,
                    feature_table=feature_table,
                    feature_set_id=feature_set_id,
                    feature=feature,
                    source_table=source_table,
                    risk_level=risk_level,
                    audit_scope="all_high_critical",
                    notes="high/critical feature has no supported PIT audit method or source-date metadata",
                )
            )
    _write_audit_rows(
        conn,
        audit_run_id=audit_run_id,
        feature_set_key=feature_set_key,
        feature_table=feature_table,
        audit_scope="all_high_critical",
        audited_at=audited_at,
        rows=rows,
    )
    conn.commit()
    unknown = sum(1 for row in rows if row.get("status") == "unknown_blocking")
    failed = sum(1 for row in rows if row.get("status") in BLOCKING_AUDIT_STATUSES)
    return {
        "audit_run_id": audit_run_id,
        "feature_table": feature_table,
        "feature_set_id": feature_set_key,
        "features": len(rows),
        "high_risk_features": sum(1 for row in rows if row.get("pit_risk_level") == "high"),
        "critical_risk_features": sum(1 for row in rows if row.get("pit_risk_level") == "critical"),
        "unknown_blocking": unknown,
        "failed": failed,
        "status": "passed" if unknown == 0 and failed == 0 else "failed",
        "audited_at": audited_at,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-id", default=None)
    parser.add_argument(
        "--features",
        default=None,
        help="comma-separated feature list for a standalone PIT audit without requiring a model row",
    )
    parser.add_argument("--feature-table", default="fact_feature_panel")
    parser.add_argument("--feature-set-id", default=None)
    parser.add_argument("--audit-run-id", default=None)
    parser.add_argument(
        "--audit-scope",
        choices=["model-selected", "all-high-critical"],
        default="model-selected",
    )
    args = parser.parse_args()
    started_at = utc_now_iso()
    t0 = time.perf_counter()
    with get_conn() as conn:
        if args.audit_scope == "all-high-critical":
            result = audit_high_critical_feature_pit(
                conn,
                feature_table=args.feature_table,
                feature_set_id=args.feature_set_id,
                audit_run_id=args.audit_run_id,
            )
        else:
            feature_names = [
                item.strip()
                for item in str(args.features or "").split(",")
                if item.strip()
            ] or None
            if not args.model_id and not feature_names:
                raise RuntimeError("--model-id or --features is required for --audit-scope model-selected")
            result = audit_registry_feature_pit(
                conn,
                model_id=args.model_id,
                feature_names=feature_names,
                feature_table=args.feature_table,
                feature_set_id=args.feature_set_id,
                audit_run_id=args.audit_run_id,
                audit_scope="explicit_feature_list" if feature_names and not args.model_id else "model_selected",
            )
        duration_s = time.perf_counter() - t0
        record_pipeline_run(
            conn,
            run_id=result["audit_run_id"],
            pipeline_name="audit_registry_feature_pit",
            status="success" if result["status"] == "passed" else "failed",
            started_at=started_at,
            ended_at=utc_now_iso(),
            duration_s=duration_s,
            commit_sha=git_commit_sha(Path(__file__).resolve().parent.parent.parent),
            input_tables=[args.feature_table],
            output_tables=["mart_feature_pit_audit", "mart_feature_pit_coverage_summary"],
            gate_result="pass" if result["status"] == "passed" else "fail",
            blockers={
                "audit_scope": args.audit_scope,
                "feature_table": args.feature_table,
                "feature_set_id": args.feature_set_id,
                "unknown_blocking": result.get("unknown_blocking", 0),
                "failed": result.get("failed"),
                "violation_rows": result.get("violation_rows"),
            },
            perf_summary={**result, "duration_s": round(duration_s, 3)},
        )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
