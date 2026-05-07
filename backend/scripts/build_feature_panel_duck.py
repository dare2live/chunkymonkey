#!/usr/bin/env python3
"""Phase 6 DuckDB feature-panel build.

All large panel operations stay inside DuckDB temp tables. The script writes the
4M+ row feature panel directly from DuckDB relations.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import sys
import time
from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Iterator

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.analytics import duck_connection
from services.feature_registry import FeatureRegistry, load_feature_registry
from services.market_db import canonical_kline_daily_qfq_sql
from services.pipeline_manifest import git_commit_sha, record_pipeline_run, utc_now_iso
from services.pipeline_timing import PipelineTimer
from services.schema_versions import record_actual_version

logger = logging.getLogger("feature_panel_duck")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s | %(message)s")


PANEL_SCHEMA_DDL = """
CREATE TABLE IF NOT EXISTS fact_feature_panel (
    stock_code TEXT NOT NULL,
    date       TEXT NOT NULL,
    close REAL,
    -- K-line source audit lineage, never used as model input.
    kline_source_name TEXT,
    kline_source_tier SMALLINT,
    kline_is_fallback BOOLEAN,
    -- Pillar B 价量
    ret_1d REAL, ret_5d REAL, ret_20d REAL, ret_60d REAL,
    vol_z20d REAL, ma_ratio_5 REAL, ma_ratio_20 REAL, ma_ratio_60 REAL, ma_ratio_250 REAL,
    rz_balance REAL, rz_chg_5d_pct REAL,
    -- Alpha158 inspired
    kmid REAL, klen REAL, kup REAL, klow REAL, ksft REAL,
    vol_ratio_5_20 REAL, vol_std_5d REAL, vol_std_20d REAL,
    range_pos_20 REAL, range_pos_60 REAL,
    momentum_diff REAL, amount_chg_5d REAL,
    -- V2 dense cross-sectional / industry-relative features
    ret_20d_rank REAL, ret_60d_rank REAL, vol_z20d_rank REAL, amount_chg_5d_rank REAL,
    rz_balance_rank REAL, rz_chg_5d_pct_rank REAL,
    ret_20d_tdx_l1_rel REAL, ret_60d_tdx_l1_rel REAL,
    vol_z20d_tdx_l1_rel REAL, amount_chg_5d_tdx_l1_rel REAL,
    rz_balance_to_amount20 REAL,
    -- Pillar A 事件
    inst_event_count_30d INTEGER, inst_event_count_60d INTEGER,
    exec_buy_count_90d INTEGER, exec_buy_ge1_count_90d INTEGER,
    lhb_inst_buy_count_30d INTEGER, lhb_inst_buy_count_60d INTEGER,
    jgdy_count_60d INTEGER,
    dzjy_count_60d INTEGER,
    days_since_exec_buy INTEGER, days_since_lhb INTEGER,
    shareholder_plan_increase_count_180d INTEGER,
    shareholder_plan_decrease_count_180d INTEGER,
    shareholder_plan_completed_count_180d INTEGER,
    shareholder_plan_increase_amount_max_180d REAL,
    shareholder_plan_decrease_amount_max_180d REAL,
    days_since_shareholder_plan_increase INTEGER,
    days_since_shareholder_plan_decrease INTEGER,
    -- Pillar C 基本面
    shareholder_count_qoq REAL, inst_count_qoq REAL,
    fund_count_qoq REAL, qfii_count_qoq REAL,
    yjyg_lower_pct REAL, yjyg_upper_pct REAL,
    roe REAL, eps_basic REAL,
    -- Regime
    hs300_ret_20d REAL, hs300_ret_60d REAL, regime_flag TEXT,
    -- Labels
    forward_ret_5d REAL, forward_ret_10d REAL, forward_ret_20d REAL, forward_ret_60d REAL,
    forward_ret_90d REAL,
    follow_net_return_5d REAL, follow_net_return_10d REAL,
    follow_net_return_20d REAL, follow_net_return_60d REAL,
    follow_net_return_90d REAL,
    built_at TEXT,
    PRIMARY KEY (stock_code, date)
);
CREATE INDEX IF NOT EXISTS idx_fp_code ON fact_feature_panel(stock_code);
CREATE INDEX IF NOT EXISTS idx_fp_date ON fact_feature_panel(date);
ALTER TABLE fact_feature_panel ADD COLUMN IF NOT EXISTS kline_source_name TEXT;
ALTER TABLE fact_feature_panel ADD COLUMN IF NOT EXISTS kline_source_tier SMALLINT;
ALTER TABLE fact_feature_panel ADD COLUMN IF NOT EXISTS kline_is_fallback BOOLEAN;
ALTER TABLE fact_feature_panel ADD COLUMN IF NOT EXISTS forward_ret_5d REAL;
ALTER TABLE fact_feature_panel ADD COLUMN IF NOT EXISTS forward_ret_10d REAL;
ALTER TABLE fact_feature_panel ADD COLUMN IF NOT EXISTS forward_ret_60d REAL;
ALTER TABLE fact_feature_panel ADD COLUMN IF NOT EXISTS forward_ret_90d REAL;
ALTER TABLE fact_feature_panel ADD COLUMN IF NOT EXISTS follow_net_return_5d REAL;
ALTER TABLE fact_feature_panel ADD COLUMN IF NOT EXISTS follow_net_return_10d REAL;
ALTER TABLE fact_feature_panel ADD COLUMN IF NOT EXISTS follow_net_return_20d REAL;
ALTER TABLE fact_feature_panel ADD COLUMN IF NOT EXISTS follow_net_return_60d REAL;
ALTER TABLE fact_feature_panel ADD COLUMN IF NOT EXISTS follow_net_return_90d REAL;
ALTER TABLE fact_feature_panel ADD COLUMN IF NOT EXISTS shareholder_plan_increase_count_180d INTEGER;
ALTER TABLE fact_feature_panel ADD COLUMN IF NOT EXISTS shareholder_plan_decrease_count_180d INTEGER;
ALTER TABLE fact_feature_panel ADD COLUMN IF NOT EXISTS shareholder_plan_completed_count_180d INTEGER;
ALTER TABLE fact_feature_panel ADD COLUMN IF NOT EXISTS shareholder_plan_increase_amount_max_180d REAL;
ALTER TABLE fact_feature_panel ADD COLUMN IF NOT EXISTS shareholder_plan_decrease_amount_max_180d REAL;
ALTER TABLE fact_feature_panel ADD COLUMN IF NOT EXISTS days_since_shareholder_plan_increase INTEGER;
ALTER TABLE fact_feature_panel ADD COLUMN IF NOT EXISTS days_since_shareholder_plan_decrease INTEGER;
CREATE INDEX IF NOT EXISTS idx_fp_date_label ON fact_feature_panel(date, forward_ret_20d);
CREATE INDEX IF NOT EXISTS idx_fp_label ON fact_feature_panel(forward_ret_20d);
CREATE INDEX IF NOT EXISTS idx_fp_label_5d ON fact_feature_panel(forward_ret_5d);
CREATE INDEX IF NOT EXISTS idx_fp_label_10d ON fact_feature_panel(forward_ret_10d);
CREATE INDEX IF NOT EXISTS idx_fp_label_60d ON fact_feature_panel(forward_ret_60d);
CREATE INDEX IF NOT EXISTS idx_fp_label_90d ON fact_feature_panel(forward_ret_90d);
"""

PANEL_DDL = """
DROP TABLE IF EXISTS fact_feature_panel;
""" + PANEL_SCHEMA_DDL

FEATURE_PANEL_VALIDATION_DDL = """
CREATE TABLE IF NOT EXISTS mart_feature_panel_validation (
    validation_id TEXT PRIMARY KEY,
    run_mode TEXT NOT NULL,
    status TEXT NOT NULL,
    validated_at TEXT NOT NULL,
    rows BIGINT,
    duplicate_keys BIGINT,
    close_coverage DOUBLE,
    source_lineage_coverage DOUBLE,
    source_fallback_ratio DOUBLE,
    source_distribution_json TEXT,
    source_watermark_hash TEXT,
    source_watermarks_json TEXT,
    feature_registry_json TEXT,
    blockers_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_feature_panel_validation_at
    ON mart_feature_panel_validation(validated_at DESC);
ALTER TABLE mart_feature_panel_validation ADD COLUMN IF NOT EXISTS source_lineage_coverage DOUBLE;
ALTER TABLE mart_feature_panel_validation ADD COLUMN IF NOT EXISTS source_fallback_ratio DOUBLE;
ALTER TABLE mart_feature_panel_validation ADD COLUMN IF NOT EXISTS source_distribution_json TEXT;
ALTER TABLE mart_feature_panel_validation ADD COLUMN IF NOT EXISTS source_watermark_hash TEXT;
ALTER TABLE mart_feature_panel_validation ADD COLUMN IF NOT EXISTS source_watermarks_json TEXT;
"""

KLINE_DAILY_QFQ_SQL = canonical_kline_daily_qfq_sql(include_source_lineage=True)
HS300_BENCHMARK_CODES = tuple(
    code.strip()
    for code in os.environ.get("CM_HS300_BENCHMARK_CODES", "000300,510300").split(",")
    if code.strip()
)

REAL_ABS_LIMIT = 1e30
FEATURE_ROLLING_LOOKBACK_DAYS = 260
LABEL_DIRTY_LOOKBACK_DAYS = 91

FEATURE_PANEL_SOURCE_SPECS = [
    {
        "domain": "kline_daily",
        "relation": "kline_daily_qfq",
        "query": KLINE_DAILY_QFQ_SQL,
        "dirty_lookback_days": FEATURE_ROLLING_LOOKBACK_DAYS + LABEL_DIRTY_LOOKBACK_DAYS,
    },
    {
        "domain": "margin_daily",
        "table": "smartmoney.raw_margin_daily",
        "date_col": "trade_date",
        "key_col": "stock_code",
        "change_col": "ingested_at",
        "row_level_dirty": True,
        "dirty_lookback_days": 20,
    },
    {
        "domain": "institution_event",
        "table": "smartmoney.fact_institution_event",
        "date_col": "notice_date",
        "key_col": "stock_code",
        "change_col": "created_at",
        "row_level_dirty": True,
        "dirty_lookback_days": 60,
    },
    {
        "domain": "executive_trade_event",
        "table": "smartmoney.fact_executive_trade_event",
        "date_col": "notice_date",
        "key_col": "stock_code",
        "change_col": "built_at",
        "row_level_dirty": True,
        "dirty_lookback_days": 90,
    },
    {
        "domain": "lhb_event",
        "table": "smartmoney.fact_lhb_event",
        "date_col": "trade_date",
        "key_col": "stock_code",
        "change_col": "built_at",
        "row_level_dirty": True,
        "dirty_lookback_days": 60,
    },
    {
        "domain": "jgdy_event",
        "table": "smartmoney.fact_jgdy_event",
        "date_col": "notice_date",
        "key_col": "stock_code",
        "change_col": "built_at",
        "row_level_dirty": True,
        "dirty_lookback_days": 60,
        "optional": True,
    },
    {
        "domain": "dzjy_event",
        "table": "smartmoney.fact_dzjy_event",
        "date_col": "trade_date",
        "key_col": "stock_code",
        "change_col": "built_at",
        "row_level_dirty": True,
        "dirty_lookback_days": 60,
        "optional": True,
    },
    {
        "domain": "shareholder_plan_tdx_f10",
        "table": "smartmoney.fact_shareholder_plan_tdx_f10",
        "date_col": "source_available_date",
        "key_col": "stock_code",
        "change_col": "fetched_at",
        "row_level_dirty": True,
        "dirty_lookback_days": 180,
    },
    {
        "domain": "fundamental_quarterly",
        "table": "smartmoney.fact_fundamental_quarterly",
        "date_col": "report_date",
        "key_col": "stock_code",
        "change_col": "built_at",
        "row_level_dirty": True,
        "availability_lag_days": 90,
        "dirty_lookback_days": 450,
    },
    {
        "domain": "tdx_industry",
        "table": "smartmoney.dim_stock_tdx_industry",
        "date_col": "updated_at",
        "dirty_lookback_days": FEATURE_ROLLING_LOOKBACK_DAYS,
        "optional": True,
    },
]

KEEP_COLS = [
    "stock_code", "date", "close",
    "kline_source_name", "kline_source_tier", "kline_is_fallback",
    "ret_1d", "ret_5d", "ret_20d", "ret_60d",
    "vol_z20d", "ma_ratio_5", "ma_ratio_20", "ma_ratio_60", "ma_ratio_250",
    "rz_balance", "rz_chg_5d_pct",
    "kmid", "klen", "kup", "klow", "ksft",
    "vol_ratio_5_20", "vol_std_5d", "vol_std_20d",
    "range_pos_20", "range_pos_60",
    "momentum_diff", "amount_chg_5d",
    "ret_20d_rank", "ret_60d_rank", "vol_z20d_rank", "amount_chg_5d_rank",
    "rz_balance_rank", "rz_chg_5d_pct_rank",
    "ret_20d_tdx_l1_rel", "ret_60d_tdx_l1_rel",
    "vol_z20d_tdx_l1_rel", "amount_chg_5d_tdx_l1_rel",
    "rz_balance_to_amount20",
    "inst_event_count_30d", "inst_event_count_60d",
    "exec_buy_count_90d", "exec_buy_ge1_count_90d",
    "lhb_inst_buy_count_30d", "lhb_inst_buy_count_60d",
    "jgdy_count_60d", "dzjy_count_60d",
    "days_since_exec_buy", "days_since_lhb",
    "shareholder_plan_increase_count_180d",
    "shareholder_plan_decrease_count_180d",
    "shareholder_plan_completed_count_180d",
    "shareholder_plan_increase_amount_max_180d",
    "shareholder_plan_decrease_amount_max_180d",
    "days_since_shareholder_plan_increase",
    "days_since_shareholder_plan_decrease",
    "shareholder_count_qoq", "inst_count_qoq",
    "fund_count_qoq", "qfii_count_qoq",
    "yjyg_lower_pct", "yjyg_upper_pct", "roe", "eps_basic",
    "hs300_ret_20d", "hs300_ret_60d", "regime_flag",
    "forward_ret_5d", "forward_ret_10d", "forward_ret_20d", "forward_ret_60d",
    "forward_ret_90d",
    "follow_net_return_5d", "follow_net_return_10d", "follow_net_return_20d",
    "follow_net_return_60d", "follow_net_return_90d",
    "built_at",
]

INTEGER_COLS = {
    "kline_source_tier",
    "inst_event_count_30d", "inst_event_count_60d",
    "exec_buy_count_90d", "exec_buy_ge1_count_90d",
    "lhb_inst_buy_count_30d", "lhb_inst_buy_count_60d",
    "jgdy_count_60d", "dzjy_count_60d",
    "days_since_exec_buy", "days_since_lhb",
    "shareholder_plan_increase_count_180d",
    "shareholder_plan_decrease_count_180d",
    "shareholder_plan_completed_count_180d",
    "days_since_shareholder_plan_increase",
    "days_since_shareholder_plan_decrease",
}

TEXT_COLS = {"stock_code", "date", "regime_flag", "built_at", "kline_source_name"}
BOOLEAN_COLS = {"kline_is_fallback"}
KLINE_LINEAGE_COLS = {"kline_source_name", "kline_source_tier", "kline_is_fallback"}
REAL_COLS = set(KEEP_COLS) - INTEGER_COLS - TEXT_COLS - BOOLEAN_COLS
PIT_LABEL_COLS = {
    "forward_ret_5d",
    "forward_ret_10d",
    "forward_ret_20d",
    "forward_ret_60d",
    "forward_ret_90d",
    "follow_net_return_5d",
    "follow_net_return_10d",
    "follow_net_return_20d",
    "follow_net_return_60d",
    "follow_net_return_90d",
}
MODEL_INPUT_EXCLUDED_COLS = {"stock_code", "date", "built_at", *PIT_LABEL_COLS, *KLINE_LINEAGE_COLS}


def execute_script(duck, sql: str) -> None:
    for stmt in sql.split(";"):
        stmt = stmt.strip()
        if stmt:
            duck.execute(stmt)


def _timed_stage(timer: PipelineTimer | None, name: str) -> Iterator[None]:
    if timer is None:
        return nullcontext()
    return timer.stage(name)


def _quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _finite_or_null_sql(column: str) -> str:
    q = _quote_ident(column)
    return (
        f"CASE WHEN {q} IS NULL OR NOT ISFINITE(CAST({q} AS DOUBLE)) "
        f"OR ABS(CAST({q} AS DOUBLE)) > {REAL_ABS_LIMIT} "
        f"THEN NULL ELSE CAST({q} AS DOUBLE) END"
    )


def _date_expr(expr: str) -> str:
    """Normalize YYYYMMDD and ISO-ish date strings to DATE."""
    return (
        f"CASE "
        f"WHEN {expr} IS NULL THEN NULL "
        f"WHEN REGEXP_MATCHES(CAST({expr} AS VARCHAR), '^\\d{{8}}$') "
        f"THEN STRPTIME(CAST({expr} AS VARCHAR), '%Y%m%d')::DATE "
        f"ELSE CAST({expr} AS DATE) END"
    )


def _date_text(expr: str) -> str:
    return f"STRFTIME({_date_expr(expr)}, '%Y-%m-%d')"


def _timestamp_expr(expr: str) -> str:
    """Normalize common string date/timestamp forms to TIMESTAMP."""
    return (
        f"CASE "
        f"WHEN {expr} IS NULL THEN NULL "
        f"WHEN REGEXP_MATCHES(CAST({expr} AS VARCHAR), '^\\d{{8}}$') "
        f"THEN STRPTIME(CAST({expr} AS VARCHAR), '%Y%m%d')::TIMESTAMP "
        f"ELSE TRY_CAST({expr} AS TIMESTAMP) END"
    )


def _replace_temp_table(duck, name: str, select_sql: str, params: list | tuple | None = None) -> None:
    temp_name = f"__tmp_replace_{name}"
    duck.execute(f"DROP TABLE IF EXISTS {_quote_ident(temp_name)}")
    duck.execute(f"CREATE TEMP TABLE {_quote_ident(temp_name)} AS {select_sql}", params or [])
    duck.execute(f"DROP TABLE IF EXISTS {_quote_ident(name)}")
    duck.execute(f"ALTER TABLE {_quote_ident(temp_name)} RENAME TO {_quote_ident(name)}")


def _table_columns(duck, table: str) -> list[str]:
    return [row[0] for row in duck.execute(f"DESCRIBE {_quote_ident(table)}").fetchall()]


def _relation_columns(duck, relation: str) -> set[str]:
    schema, table = relation.split(".", 1) if "." in relation else (None, relation)
    if schema:
        return {
            row[0]
            for row in duck.execute(
                """
                SELECT column_name
                  FROM information_schema.columns
                 WHERE (table_schema = ? OR table_catalog = ?)
                   AND table_name = ?
                """,
                [schema, schema, table],
            ).fetchall()
        }
    return set(_table_columns(duck, relation))


def _relation_exists(duck, relation: str) -> bool:
    schema, table = relation.split(".", 1) if "." in relation else (None, relation)
    if schema:
        row = duck.execute(
            """
            SELECT 1
              FROM information_schema.tables
             WHERE (table_schema = ? OR table_catalog = ?)
               AND table_name = ?
             LIMIT 1
            """,
            [schema, schema, table],
        ).fetchone()
        return row is not None
    try:
        duck.execute(f"SELECT 1 FROM {_quote_ident(relation)} LIMIT 0")
        return True
    except Exception:
        return False


def _active_a_stock_filter_sql(duck, *, alias: str = "kline") -> str:
    """Restrict stock research panels to listed A shares when the master exists."""

    relation = "smartmoney.dim_active_a_stock"
    if not _relation_exists(duck, relation):
        return ""
    try:
        row = duck.execute(
            f"""
            SELECT COUNT(*) AS n
              FROM {relation}
             WHERE stock_code IS NOT NULL
               AND TRIM(CAST(stock_code AS VARCHAR)) != ''
            """
        ).fetchone()
        if int(row[0] or 0) <= 0:
            return ""
    except Exception:
        return ""
    return (
        f"AND {alias}.code IN ("
        "SELECT stock_code FROM smartmoney.dim_active_a_stock "
        "WHERE stock_code IS NOT NULL AND TRIM(CAST(stock_code AS VARCHAR)) != ''"
        ")"
    )


def _hs300_benchmark_codes_sql() -> str:
    codes = [code for code in HS300_BENCHMARK_CODES if code.isdigit() and len(code) == 6]
    if not codes:
        codes = ["000300", "510300"]
    values = ", ".join(f"('{code}', {idx})" for idx, code in enumerate(codes, start=1))
    return f"(VALUES {values}) AS benchmark_codes(code, priority)"


def _row_count(duck, table: str) -> int:
    return int(duck.execute(f"SELECT COUNT(*) FROM {_quote_ident(table)}").fetchone()[0])


def _table_exists(duck, table: str, schema: str | None = None) -> bool:
    if schema:
        row = duck.execute(
            """
            SELECT 1
              FROM information_schema.tables
             WHERE (table_schema = ? OR table_catalog = ?)
               AND table_name = ?
             LIMIT 1
            """,
            [schema, schema, table],
        ).fetchone()
    else:
        row = duck.execute(
            """
            SELECT 1
              FROM information_schema.tables
             WHERE table_name = ?
             LIMIT 1
            """,
            [table],
        ).fetchone()
    return row is not None


def _feature_registry() -> FeatureRegistry:
    return load_feature_registry()


def feature_input_columns(
    *,
    include_disabled: bool = False,
    production_ready_only: bool = True,
) -> list[str]:
    """Return model input columns, excluding labels and row identifiers."""

    try:
        registry_cols = set(
            _feature_registry().model_input_columns(
                include_disabled=include_disabled,
                production_ready_only=production_ready_only,
            )
        )
    except Exception:
        registry_cols = set(KEEP_COLS) - MODEL_INPUT_EXCLUDED_COLS
    return [col for col in KEEP_COLS if col in registry_cols and col not in MODEL_INPUT_EXCLUDED_COLS]


def validate_feature_registry() -> dict[str, list[str] | str | int]:
    """Validate registry coverage for the current production panel schema."""

    registry = _feature_registry()
    required = set(KEEP_COLS) - {"stock_code", "date", "built_at"}
    registered = set(registry.feature_names())
    input_cols = set(registry.model_input_columns())
    label_cols = set(registry.label_columns())
    missing = sorted(required - registered)
    stale = sorted(registered - required)
    labels_in_inputs = sorted(label_cols.intersection(input_cols))
    return {
        "status": "failed" if missing or stale or labels_in_inputs else "passed",
        "registered_features": len(registered),
        "missing_features": missing,
        "stale_features": stale,
        "labels_in_inputs": labels_in_inputs,
    }


def _group_pit_release_lag_days(group: str, default: int = 0) -> int:
    try:
        return _feature_registry().group_pit_release_lag_days(group, default=default)
    except Exception:
        return max(int(default), 0)


def _ensure_fact_panel_schema(duck) -> None:
    execute_script(duck, PANEL_SCHEMA_DDL)


def _calendar_relation(duck) -> str | None:
    for relation in ("smartmoney.dim_trading_calendar", "dim_trading_calendar"):
        schema, table = relation.split(".") if "." in relation else (None, relation)
        if _table_exists(duck, table, schema=schema):
            return relation
    return None


def _shift_trading_date(duck, date_text: str, offset: int) -> str:
    """Shift by trading rows when a calendar is available, else by calendar days."""

    if offset == 0:
        return date_text
    relation = _calendar_relation(duck)
    if relation:
        if offset < 0:
            row = duck.execute(
                f"""
                SELECT MIN(trade_date)
                  FROM (
                    SELECT trade_date
                      FROM {relation}
                     WHERE is_trading = 1
                       AND trade_date <= ?
                     ORDER BY trade_date DESC
                     LIMIT ?
                  )
                """,
                [date_text, abs(offset) + 1],
            ).fetchone()
        else:
            row = duck.execute(
                f"""
                SELECT MAX(trade_date)
                  FROM (
                    SELECT trade_date
                      FROM {relation}
                     WHERE is_trading = 1
                       AND trade_date >= ?
                     ORDER BY trade_date ASC
                     LIMIT ?
                  )
                """,
                [date_text, offset + 1],
            ).fetchone()
        if row and row[0]:
            return str(row[0])
    return (datetime.strptime(date_text, "%Y-%m-%d").date() + timedelta(days=offset)).isoformat()


def _max_query_date(duck, query: str) -> str | None:
    try:
        row = duck.execute(f"SELECT MAX(date) FROM ({query}) q").fetchone()
    except Exception:
        return None
    return str(row[0]) if row and row[0] else None


def _stable_hash(payload: object) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _source_spec_by_domain() -> dict[str, dict]:
    return {str(spec["domain"]): spec for spec in FEATURE_PANEL_SOURCE_SPECS}


def _source_snapshot_item(duck, spec: dict) -> dict:
    domain = str(spec["domain"])
    if spec.get("query"):
        try:
            row = duck.execute(
                f"""
                SELECT COUNT(*) AS row_count,
                       CAST(MIN(date) AS TEXT) AS min_data_date,
                       CAST(MAX(date) AS TEXT) AS last_data_date
                  FROM ({spec["query"]}) q
                """
            ).fetchone()
            row_count = int(row[0] or 0)
            item = {
                "domain": domain,
                "relation": spec.get("relation") or "query",
                "status": "ok" if row_count else "empty",
                "row_count": row_count,
                "min_data_date": row[1],
                "last_data_date": row[2],
                "dirty_lookback_days": int(spec.get("dirty_lookback_days") or 0),
                "availability_lag_days": int(spec.get("availability_lag_days") or 0),
            }
            item["source_hash"] = _stable_hash(item)
            return item
        except Exception as exc:
            item = {
                "domain": domain,
                "relation": spec.get("relation") or "query",
                "status": "error",
                "error": str(exc)[:300],
                "row_count": 0,
                "min_data_date": None,
                "last_data_date": None,
                "dirty_lookback_days": int(spec.get("dirty_lookback_days") or 0),
                "availability_lag_days": int(spec.get("availability_lag_days") or 0),
            }
            item["source_hash"] = _stable_hash(item)
            return item

    table = str(spec["table"])
    schema, table_name = table.split(".", 1) if "." in table else (None, table)
    if not _table_exists(duck, table_name, schema=schema):
        item = {
            "domain": domain,
            "table": table,
            "status": "missing_optional" if spec.get("optional") else "missing",
            "row_count": 0,
            "min_data_date": None,
            "last_data_date": None,
            "dirty_lookback_days": int(spec.get("dirty_lookback_days") or 0),
            "availability_lag_days": int(spec.get("availability_lag_days") or 0),
        }
        item["source_hash"] = _stable_hash(item)
        return item

    date_col = str(spec.get("date_col") or "")
    cols = _relation_columns(duck, table)
    if date_col and date_col in cols:
        min_expr = f"MIN({_date_text(date_col)})"
        max_expr = f"MAX({_date_text(date_col)})"
    else:
        min_expr = max_expr = "CAST(NULL AS TEXT)"
    try:
        row = duck.execute(
            f"""
            SELECT COUNT(*) AS row_count,
                   CAST({min_expr} AS TEXT) AS min_data_date,
                   CAST({max_expr} AS TEXT) AS last_data_date
              FROM {table}
            """
        ).fetchone()
        row_count = int(row[0] or 0)
        item = {
            "domain": domain,
            "table": table,
            "status": "ok" if row_count else "empty",
            "row_count": row_count,
            "min_data_date": row[1],
            "last_data_date": row[2],
            "dirty_lookback_days": int(spec.get("dirty_lookback_days") or 0),
            "availability_lag_days": int(spec.get("availability_lag_days") or 0),
        }
    except Exception as exc:
        item = {
            "domain": domain,
            "table": table,
            "status": "error",
            "error": str(exc)[:300],
            "row_count": 0,
            "min_data_date": None,
            "last_data_date": None,
            "dirty_lookback_days": int(spec.get("dirty_lookback_days") or 0),
            "availability_lag_days": int(spec.get("availability_lag_days") or 0),
        }
    item["source_hash"] = _stable_hash(item)
    return item


def feature_panel_source_snapshot(duck) -> list[dict]:
    """Return compact upstream source fingerprints used by incremental planning."""

    return [_source_snapshot_item(duck, spec) for spec in FEATURE_PANEL_SOURCE_SPECS]


def feature_panel_source_snapshot_hash(snapshot: list[dict]) -> str:
    return _stable_hash(
        [
            {
                "domain": item.get("domain"),
                "status": item.get("status"),
                "row_count": item.get("row_count"),
                "min_data_date": item.get("min_data_date"),
                "last_data_date": item.get("last_data_date"),
                "source_hash": item.get("source_hash"),
            }
            for item in sorted(snapshot, key=lambda row: str(row.get("domain")))
        ]
    )


def _latest_recorded_source_snapshot(duck) -> list[dict] | None:
    if not _table_exists(duck, "mart_feature_panel_validation"):
        return None
    try:
        row = duck.execute(
            """
            SELECT source_watermarks_json
              FROM mart_feature_panel_validation
             WHERE source_watermarks_json IS NOT NULL
             ORDER BY validated_at DESC
             LIMIT 1
            """
        ).fetchone()
    except Exception:
        return None
    if not row or not row[0]:
        return None
    try:
        loaded = json.loads(row[0])
    except Exception:
        return None
    return loaded if isinstance(loaded, list) else None


def _latest_recorded_validation_at(duck) -> str | None:
    if not _table_exists(duck, "mart_feature_panel_validation"):
        return None
    try:
        row = duck.execute(
            """
            SELECT CAST(validated_at AS TEXT)
              FROM mart_feature_panel_validation
             WHERE source_watermarks_json IS NOT NULL
             ORDER BY validated_at DESC
             LIMIT 1
            """
        ).fetchone()
    except Exception:
        return None
    return str(row[0]) if row and row[0] else None


def _changed_source_domains(previous: list[dict] | None, current: list[dict]) -> list[str]:
    if not previous:
        return []
    prev_by_domain = {str(item.get("domain")): item for item in previous}
    changed = []
    for item in current:
        domain = str(item.get("domain"))
        prev = prev_by_domain.get(domain)
        if prev is None or prev.get("source_hash") != item.get("source_hash"):
            changed.append(domain)
    return sorted(changed)


def _add_days(date_text: str, days: int) -> str:
    return (datetime.strptime(date_text, "%Y-%m-%d").date() + timedelta(days=days)).isoformat()


def _changed_source_row_windows(
    duck,
    *,
    changed_domains: list[str],
    since_at: str | None,
    existing_max_date: str,
) -> list[dict]:
    """Return row-level dirty windows for sources with change metadata.

    This narrows historical backfills when compact source fingerprints change
    because a small number of older source rows were ingested after the last
    feature-panel validation. Sources without a usable change timestamp fall
    back to their configured compact-window lookback.
    """

    if not since_at:
        return []
    windows: list[dict] = []
    spec_by_domain = _source_spec_by_domain()
    for domain in changed_domains:
        spec = spec_by_domain.get(domain, {})
        if not spec.get("row_level_dirty"):
            continue
        table = spec.get("table")
        date_col = str(spec.get("date_col") or "")
        key_col = str(spec.get("key_col") or "")
        change_col = str(spec.get("change_col") or "")
        if not table or not date_col or not key_col or not change_col:
            continue
        schema, table_name = str(table).split(".", 1) if "." in str(table) else (None, str(table))
        if not _table_exists(duck, table_name, schema=schema):
            continue
        cols = _relation_columns(duck, str(table))
        if date_col not in cols or key_col not in cols or change_col not in cols:
            continue
        date_expr = _date_text(_quote_ident(date_col))
        change_expr = _timestamp_expr(_quote_ident(change_col))
        try:
            row = duck.execute(
                f"""
                WITH changed AS (
                    SELECT {_quote_ident(key_col)} AS source_key,
                           {date_expr} AS source_date
                      FROM {table}
                     WHERE {change_expr} > TRY_CAST(? AS TIMESTAMP)
                       AND {date_expr} IS NOT NULL
                )
                SELECT COUNT(*) AS changed_rows,
                       COUNT(DISTINCT source_key) AS changed_stock_count,
                       MIN(source_date) AS min_source_date,
                       MAX(source_date) AS max_source_date
                  FROM changed
                """,
                [since_at],
            ).fetchone()
        except Exception:
            continue
        changed_rows = int(row[0] or 0) if row else 0
        if changed_rows <= 0:
            continue
        min_source_date = str(row[2]) if row[2] else None
        max_source_date = str(row[3]) if row[3] else None
        if not min_source_date or not max_source_date:
            continue
        lag_days = int(spec.get("availability_lag_days") or 0)
        impact_start = _add_days(min_source_date, lag_days) if lag_days else min_source_date
        impact_end = _add_days(max_source_date, lag_days) if lag_days else max_source_date
        if impact_start > existing_max_date:
            impact_start = existing_max_date
        if impact_end > existing_max_date:
            impact_end = existing_max_date
        windows.append(
            {
                "domain": domain,
                "table": str(table),
                "key_col": key_col,
                "date_col": date_col,
                "change_col": change_col,
                "since_at": since_at,
                "changed_rows": changed_rows,
                "changed_stock_count": int(row[1] or 0),
                "min_source_date": min_source_date,
                "max_source_date": max_source_date,
                "impact_start_date": impact_start,
                "impact_end_date": impact_end,
                "availability_lag_days": lag_days,
            }
        )
    return sorted(windows, key=lambda item: (item["impact_start_date"], item["domain"]))


def _plan_changed_source_write_start(
    duck,
    *,
    changed_domains: list[str],
    snapshot: list[dict],
    existing_max_date: str,
    row_level_dirty_windows: list[dict] | None = None,
) -> str:
    spec_by_domain = _source_spec_by_domain()
    item_by_domain = {str(item.get("domain")): item for item in snapshot}
    row_window_by_domain = {
        str(item.get("domain")): item
        for item in (row_level_dirty_windows or [])
        if item.get("impact_start_date")
    }
    starts = []
    for domain in changed_domains:
        row_window = row_window_by_domain.get(domain)
        if row_window:
            starts.append(str(row_window["impact_start_date"]))
            continue
        spec = spec_by_domain.get(domain, {})
        item = item_by_domain.get(domain, {})
        lookback = int(spec.get("dirty_lookback_days") or FEATURE_ROLLING_LOOKBACK_DAYS)
        anchor = item.get("last_data_date") or existing_max_date
        if spec.get("availability_lag_days") and anchor:
            anchor = _add_days(str(anchor), int(spec.get("availability_lag_days") or 0))
        if str(anchor) > existing_max_date:
            anchor = existing_max_date
        starts.append(_shift_trading_date(duck, str(anchor), -lookback))
    if not starts:
        return existing_max_date
    return min(starts)


def plan_incremental_window(
    duck,
    *,
    start_date: str | None = None,
    lookback_days: int = FEATURE_ROLLING_LOOKBACK_DAYS,
    label_lookback_days: int = LABEL_DIRTY_LOOKBACK_DAYS,
) -> dict[str, str | bool | None | int]:
    """Plan an incremental rebuild window.

    ``write_start_date`` is the first panel date that may be replaced. It moves
    back by the label horizon when new prices arrive because old tail labels can
    become known. ``read_start_date`` moves back further to provide rolling
    feature context.
    """

    source_max_date = _max_query_date(duck, KLINE_DAILY_QFQ_SQL)
    source_snapshot = feature_panel_source_snapshot(duck)
    previous_source_snapshot = _latest_recorded_source_snapshot(duck)
    previous_validation_at = _latest_recorded_validation_at(duck)
    changed_domains = _changed_source_domains(previous_source_snapshot, source_snapshot)
    row_level_dirty_windows: list[dict] = []
    existing_max_date = None
    if _table_exists(duck, "fact_feature_panel"):
        try:
            row = duck.execute("SELECT MAX(date) FROM fact_feature_panel").fetchone()
            existing_max_date = str(row[0]) if row and row[0] else None
        except Exception:
            existing_max_date = None

    if not source_max_date:
        return {
            "mode": "incremental",
            "noop": True,
            "reason": "no canonical kline rows",
            "source_max_date": None,
            "existing_max_date": existing_max_date,
            "write_start_date": None,
            "read_start_date": None,
            "lookback_days": lookback_days,
            "label_lookback_days": label_lookback_days,
            "changed_source_domains": changed_domains,
            "row_level_dirty_windows": row_level_dirty_windows,
            "source_watermark_hash": feature_panel_source_snapshot_hash(source_snapshot),
        }

    if start_date:
        write_start_date = start_date
        reason = "explicit start date"
    elif existing_max_date:
        if source_max_date <= existing_max_date:
            if not changed_domains:
                return {
                    "mode": "incremental",
                    "noop": True,
                    "reason": "feature panel already reaches canonical kline max date and source snapshot is unchanged",
                    "source_max_date": source_max_date,
                    "existing_max_date": existing_max_date,
                    "write_start_date": None,
                    "read_start_date": None,
                    "lookback_days": lookback_days,
                    "label_lookback_days": label_lookback_days,
                    "changed_source_domains": [],
                    "row_level_dirty_windows": row_level_dirty_windows,
                    "source_watermark_hash": feature_panel_source_snapshot_hash(source_snapshot),
                }
            row_level_dirty_windows = _changed_source_row_windows(
                duck,
                changed_domains=changed_domains,
                since_at=previous_validation_at,
                existing_max_date=existing_max_date,
            )
            write_start_date = _plan_changed_source_write_start(
                duck,
                changed_domains=changed_domains,
                snapshot=source_snapshot,
                existing_max_date=existing_max_date,
                row_level_dirty_windows=row_level_dirty_windows,
            )
            reason = "changed feature-panel source snapshot: " + ",".join(changed_domains)
            if row_level_dirty_windows:
                reason += "; row-level dirty windows: " + ",".join(
                    sorted(str(item["domain"]) for item in row_level_dirty_windows)
                )
        else:
            write_start_date = _shift_trading_date(duck, existing_max_date, -label_lookback_days)
            reason = "new canonical kline dates"
    else:
        row = duck.execute(f"SELECT MIN(date) FROM ({KLINE_DAILY_QFQ_SQL}) q").fetchone()
        write_start_date = start_date or (str(row[0]) if row and row[0] else source_max_date)
        reason = "empty feature panel"

    read_start_date = _shift_trading_date(duck, write_start_date, -lookback_days)
    return {
        "mode": "incremental",
        "noop": False,
        "reason": reason,
        "source_max_date": source_max_date,
        "existing_max_date": existing_max_date,
        "write_start_date": write_start_date,
        "read_start_date": read_start_date,
        "lookback_days": lookback_days,
        "label_lookback_days": label_lookback_days,
        "changed_source_domains": changed_domains,
        "row_level_dirty_windows": row_level_dirty_windows,
        "source_watermark_hash": feature_panel_source_snapshot_hash(source_snapshot),
    }


def _add_literal_columns(duck, definitions: dict[str, str]) -> None:
    additions = ", ".join(f"{expr} AS {_quote_ident(col)}" for col, expr in definitions.items())
    _replace_temp_table(duck, "current_panel", f"SELECT *, {additions} FROM current_panel")


def _rolling_event_count(duck, evt_sql: str, count_col: str, windows: list[int]) -> None:
    select_cols = ", ".join(
        f"COALESCE(r.{_quote_ident(count_col + '_' + str(w) + 'd')}, 0)::INTEGER "
        f"AS {_quote_ident(count_col + '_' + str(w) + 'd')}"
        for w in windows
    )
    rolled_cols = ", ".join(
        f"SUM(n) OVER (PARTITION BY stock_code ORDER BY date ROWS {w - 1} PRECEDING) "
        f"AS {_quote_ident(count_col + '_' + str(w) + 'd')}"
        for w in windows
    )
    _replace_temp_table(
        duck,
        "current_panel",
        f"""
        WITH panel_dates AS (
            SELECT stock_code, date, {_date_expr('date')} AS date_dt
            FROM current_panel
        ),
        panel_bounds AS (
            SELECT MIN(date_dt) AS min_panel_date FROM panel_dates
        ),
        ev_raw AS (
            SELECT stock_code,
                   {_date_expr('event_date')} AS event_dt,
                   ROW_NUMBER() OVER () AS event_id
            FROM ({evt_sql})
            WHERE stock_code IS NOT NULL
              AND event_date IS NOT NULL
        ),
        ev_aligned AS (
            SELECT e.stock_code, MIN(p.date) AS date
            FROM ev_raw e
            JOIN panel_dates p
              ON p.stock_code = e.stock_code
             AND p.date_dt >= e.event_dt
            CROSS JOIN panel_bounds b
            WHERE e.event_dt >= b.min_panel_date
            GROUP BY e.stock_code, e.event_id
        ),
        ev_daily AS (
            SELECT stock_code, date, COUNT(*)::INTEGER AS n
            FROM ev_aligned
            GROUP BY stock_code, date
        ),
        panel_ev AS (
            SELECT p.stock_code, p.date, COALESCE(e.n, 0) AS n
            FROM current_panel p
            LEFT JOIN ev_daily e ON e.stock_code = p.stock_code AND e.date = p.date
        ),
        rolled AS (
            SELECT stock_code, date, {rolled_cols}
            FROM panel_ev
        )
        SELECT p.*, {select_cols}
        FROM current_panel p
        LEFT JOIN rolled r ON r.stock_code = p.stock_code AND r.date = p.date
        """,
    )


def _rolling_event_sum(duck, evt_sql: str, sum_col: str, windows: list[int]) -> None:
    select_cols = ", ".join(
        f"COALESCE(r.{_quote_ident(sum_col + '_' + str(w) + 'd')}, 0)::DOUBLE "
        f"AS {_quote_ident(sum_col + '_' + str(w) + 'd')}"
        for w in windows
    )
    rolled_cols = ", ".join(
        f"SUM(v) OVER (PARTITION BY stock_code ORDER BY date ROWS {w - 1} PRECEDING) "
        f"AS {_quote_ident(sum_col + '_' + str(w) + 'd')}"
        for w in windows
    )
    _replace_temp_table(
        duck,
        "current_panel",
        f"""
        WITH panel_dates AS (
            SELECT stock_code, date, {_date_expr('date')} AS date_dt
            FROM current_panel
        ),
        panel_bounds AS (
            SELECT MIN(date_dt) AS min_panel_date FROM panel_dates
        ),
        ev_raw AS (
            SELECT stock_code,
                   {_date_expr('event_date')} AS event_dt,
                   COALESCE(TRY_CAST(event_value AS DOUBLE), 0.0) AS event_value,
                   ROW_NUMBER() OVER () AS event_id
            FROM ({evt_sql})
            WHERE stock_code IS NOT NULL
              AND event_date IS NOT NULL
        ),
        ev_aligned AS (
            SELECT e.stock_code, MIN(p.date) AS date, ANY_VALUE(e.event_value) AS event_value
            FROM ev_raw e
            JOIN panel_dates p
              ON p.stock_code = e.stock_code
             AND p.date_dt >= e.event_dt
            CROSS JOIN panel_bounds b
            WHERE e.event_dt >= b.min_panel_date
            GROUP BY e.stock_code, e.event_id
        ),
        ev_daily AS (
            SELECT stock_code, date, SUM(event_value)::DOUBLE AS v
            FROM ev_aligned
            GROUP BY stock_code, date
        ),
        panel_ev AS (
            SELECT p.stock_code, p.date, COALESCE(e.v, 0.0) AS v
            FROM current_panel p
            LEFT JOIN ev_daily e ON e.stock_code = p.stock_code AND e.date = p.date
        ),
        rolled AS (
            SELECT stock_code, date, {rolled_cols}
            FROM panel_ev
        )
        SELECT p.*, {select_cols}
        FROM current_panel p
        LEFT JOIN rolled r ON r.stock_code = p.stock_code AND r.date = p.date
        """,
    )


def _days_since_event(duck, ev_sql: str, suffix: str) -> None:
    col = f"days_since_{suffix}"
    _replace_temp_table(
        duck,
        "current_panel",
        f"""
        WITH ev AS ({ev_sql}),
        ds AS (
            SELECT p.stock_code, p.date, {_date_expr('p.date')} AS date_dt,
                   MAX(CASE WHEN {_date_expr('e.event_date')} <= {_date_expr('p.date')}
                            THEN {_date_expr('e.event_date')} END) AS last_ev
            FROM current_panel p
            LEFT JOIN ev e ON e.stock_code = p.stock_code
            GROUP BY p.stock_code, p.date
        )
        SELECT p.*,
               COALESCE(CASE WHEN ds.last_ev IS NULL THEN -1
                             ELSE (ds.date_dt - ds.last_ev)::INTEGER END, -1) AS {_quote_ident(col)}
        FROM current_panel p
        LEFT JOIN ds ON ds.stock_code = p.stock_code AND ds.date = p.date
        """,
    )


def _clean_select_expr(col: str, params: list[str]) -> str:
    q = _quote_ident(col)
    if col == "built_at":
        params.append(datetime.now(UTC).replace(tzinfo=None).isoformat())
        return "? AS built_at"
    if col in REAL_COLS:
        return (
            f"CASE WHEN {q} IS NULL OR NOT ISFINITE(CAST({q} AS DOUBLE)) "
            f"OR ABS(CAST({q} AS DOUBLE)) > {REAL_ABS_LIMIT} "
            f"THEN NULL ELSE CAST({q} AS DOUBLE) END AS {q}"
        )
    if col in INTEGER_COLS:
        return f"CAST({q} AS INTEGER) AS {q}"
    if col in BOOLEAN_COLS:
        return f"CAST({q} AS BOOLEAN) AS {q}"
    return q


def _fact_panel_summary(duck) -> dict[str, int]:
    row = duck.execute("""
        SELECT COUNT(*), COUNT(DISTINCT stock_code), COUNT(DISTINCT date),
               SUM(CASE WHEN forward_ret_20d IS NOT NULL THEN 1 ELSE 0 END)
        FROM fact_feature_panel
    """).fetchone()
    return {
        "rows": int(row[0] or 0),
        "codes": int(row[1] or 0),
        "dates": int(row[2] or 0),
        "label_non_null": int(row[3] or 0),
    }


def _incremental_window_covers_existing_panel(duck, write_start_date: str | None) -> bool:
    """Return true when incremental replacement would delete the whole panel."""

    if not write_start_date or not _table_exists(duck, "fact_feature_panel"):
        return False
    try:
        row = duck.execute("SELECT COUNT(*), MIN(date) FROM fact_feature_panel").fetchone()
    except Exception:
        return False
    row_count = int(row[0] or 0) if row else 0
    min_date = str(row[1]) if row and row[1] else None
    return row_count > 0 and min_date is not None and str(write_start_date) <= min_date


def feature_group_columns(groups: list[str]) -> list[str]:
    registry = _feature_registry()
    known_groups = {spec.group for spec in registry.features.values()}
    unknown = sorted(set(groups) - known_groups)
    if unknown:
        raise ValueError(f"unknown feature registry groups: {','.join(unknown)}")
    selected = []
    for group in groups:
        for col in registry.group_columns(group, include_disabled=True):
            if col in KEEP_COLS and col not in {"stock_code", "date"}:
                selected.append(col)
    return [col for col in KEEP_COLS if col in set(selected)]


def feature_groups_for_columns(columns: list[str] | None) -> set[str]:
    if not columns:
        return set(_feature_registry().groups.keys())
    registry = _feature_registry()
    groups = set()
    for column in columns:
        spec = registry.features.get(column)
        if spec:
            groups.add(spec.group)
    return groups


FEATURE_BLOCK_GROUPS = {
    "price_shape": {"kline_lineage", "price_volume", "alpha158_price_shape"},
    "margin": {"price_volume", "cross_sectional"},
    "labels": {"labels"},
    "event_activity": {"event_activity"},
    "fundamentals": {"fundamentals"},
    "regime": {"regime"},
    "cross_sectional": {"cross_sectional"},
}
SCOPED_REWRITE_ROW_THRESHOLD = 100_000


def feature_block_plan(update_columns: list[str] | None = None) -> list[str]:
    """Return registry-driven compute blocks needed for a full or scoped build."""

    if update_columns is None:
        return list(FEATURE_BLOCK_GROUPS.keys())
    groups = feature_groups_for_columns(update_columns)
    plan = ["price_shape"]
    for block, block_groups in FEATURE_BLOCK_GROUPS.items():
        if block == "price_shape":
            continue
        if groups.intersection(block_groups):
            plan.append(block)
    return plan


def _typed_null_expr(col: str) -> str:
    if col in INTEGER_COLS:
        return f"CAST(NULL AS INTEGER) AS {_quote_ident(col)}"
    if col in BOOLEAN_COLS:
        return f"CAST(NULL AS BOOLEAN) AS {_quote_ident(col)}"
    if col in REAL_COLS:
        return f"CAST(NULL AS DOUBLE) AS {_quote_ident(col)}"
    return f"CAST(NULL AS TEXT) AS {_quote_ident(col)}"


def _rewrite_fact_panel_columns_from_update(
    duck,
    *,
    target_cols: list[str],
    update_rows: int,
) -> dict[str, int]:
    old_cols = set(_table_columns(duck, "fact_feature_panel"))
    target_set = set(target_cols)
    select_exprs = ["dst.stock_code AS stock_code", "dst.date AS date"]
    for col in KEEP_COLS:
        if col in {"stock_code", "date"}:
            continue
        q = _quote_ident(col)
        if col in target_set:
            old_expr = f"dst.{q}" if col in old_cols else "NULL"
            select_exprs.append(f"CASE WHEN src.__has_update IS NOT NULL THEN src.{q} ELSE {old_expr} END AS {q}")
        elif col in old_cols:
            select_exprs.append(f"dst.{q} AS {q}")
        else:
            select_exprs.append(_typed_null_expr(col))
    logger.info(
        "DuckDB rewrite fact_feature_panel for scoped column update columns=%s rows=%d ...",
        ",".join(target_cols),
        update_rows,
    )
    duck.execute("DROP TABLE IF EXISTS __fact_feature_panel_rewrite")
    duck.execute(
        f"""
        CREATE TABLE __fact_feature_panel_rewrite AS
        SELECT {', '.join(select_exprs)}
          FROM fact_feature_panel dst
          LEFT JOIN __current_panel_update src
            ON dst.stock_code = src.stock_code
           AND dst.date = src.date
        """
    )
    duck.execute("DROP TABLE fact_feature_panel")
    duck.execute("ALTER TABLE __fact_feature_panel_rewrite RENAME TO fact_feature_panel")
    _ensure_fact_panel_schema(duck)
    summary = _fact_panel_summary(duck)
    summary["updated_rows"] = update_rows
    summary["updated_columns"] = len(target_cols)
    summary["update_strategy"] = "rewrite"
    return summary


def _update_fact_panel_columns(
    duck,
    *,
    update_columns: list[str],
    write_start_date: str | None = None,
) -> dict[str, int]:
    if not update_columns:
        raise ValueError("feature-group scoped backfill has no columns to update")
    _ensure_fact_panel_schema(duck)
    panel_cols = set(_table_columns(duck, "current_panel"))
    target_cols = [
        col
        for col in KEEP_COLS
        if col in set(update_columns)
        and col in panel_cols
        and col not in {"stock_code", "date"}
    ]
    if "built_at" not in target_cols:
        target_cols.append("built_at")
    params: list[str] = []
    select_exprs = [
        "stock_code",
        "date",
        "1 AS __has_update",
        *[_clean_select_expr(col, params) for col in target_cols],
    ]
    where_sql = ""
    if write_start_date:
        where_sql = "WHERE date >= ?"
        params.append(write_start_date)
    duck.execute("DROP TABLE IF EXISTS __current_panel_update")
    duck.execute(
        f"""
        CREATE TEMP TABLE __current_panel_update AS
        SELECT {', '.join(select_exprs)}
          FROM current_panel
        {where_sql}
        """,
        params,
    )
    update_rows = int(
        duck.execute(
            """
            SELECT COUNT(*)
              FROM fact_feature_panel dst
              JOIN __current_panel_update src
                ON dst.stock_code = src.stock_code
               AND dst.date = src.date
            """
        ).fetchone()[0]
        or 0
    )
    if update_rows >= SCOPED_REWRITE_ROW_THRESHOLD:
        return _rewrite_fact_panel_columns_from_update(
            duck,
            target_cols=target_cols,
            update_rows=update_rows,
        )
    assignments = ", ".join(f"{_quote_ident(col)} = src.{_quote_ident(col)}" for col in target_cols)
    logger.info(
        "DuckDB UPDATE fact_feature_panel columns=%s rows=%d ...",
        ",".join(target_cols),
        update_rows,
    )
    duck.execute(
        f"""
        UPDATE fact_feature_panel AS dst
           SET {assignments}
          FROM __current_panel_update AS src
         WHERE dst.stock_code = src.stock_code
           AND dst.date = src.date
        """
    )
    summary = _fact_panel_summary(duck)
    summary["updated_rows"] = update_rows
    summary["updated_columns"] = len(target_cols)
    summary["update_strategy"] = "update"
    return summary


def _insert_fact_panel(
    duck,
    *,
    reset: bool = True,
    write_start_date: str | None = None,
    update_columns: list[str] | None = None,
) -> dict[str, int]:
    if update_columns is not None:
        if reset:
            raise ValueError("feature-group scoped backfill requires reset=False")
        return _update_fact_panel_columns(
            duck,
            update_columns=update_columns,
            write_start_date=write_start_date,
        )
    if not reset and _incremental_window_covers_existing_panel(duck, write_start_date):
        logger.info(
            "incremental write_start=%s covers existing fact_feature_panel; using drop/recreate instead of DELETE",
            write_start_date,
        )
        reset = True
    panel_cols = set(_table_columns(duck, "current_panel"))
    keep = [col for col in KEEP_COLS if col == "built_at" or col in panel_cols]
    params: list[str] = []
    select_exprs = [_clean_select_expr(col, params) for col in keep]
    if reset:
        execute_script(duck, PANEL_DDL)
        where_sql = ""
        if write_start_date:
            where_sql = "WHERE date >= ?"
            params.append(write_start_date)
    else:
        _ensure_fact_panel_schema(duck)
        where_sql = ""
        if write_start_date:
            duck.execute("DELETE FROM fact_feature_panel WHERE date >= ?", [write_start_date])
            where_sql = "WHERE date >= ?"
            params.append(write_start_date)
    logger.info("DuckDB INSERT INTO fact_feature_panel SELECT FROM current_panel ...")
    duck.execute(
        f"""
        INSERT INTO fact_feature_panel ({', '.join(_quote_ident(col) for col in keep)})
        SELECT {', '.join(select_exprs)}
        FROM current_panel
        {where_sql}
        """,
        params,
    )
    return _fact_panel_summary(duck)


def _build_panel_with_connection(
    duck,
    start_date: str,
    *,
    reset: bool = True,
    write_start_date: str | None = None,
    update_columns: list[str] | None = None,
    timer: PipelineTimer | None = None,
) -> dict[str, int]:
    t0 = time.time()
    blocks = set(feature_block_plan(update_columns))
    logger.info("feature block plan: %s", ",".join(feature_block_plan(update_columns)))
    panel_start_date = write_start_date or start_date
    read_start_date = start_date
    write_filter_date = write_start_date
    if reset and write_start_date is None:
        read_start_date = _shift_trading_date(duck, start_date, -FEATURE_ROLLING_LOOKBACK_DAYS)
        write_filter_date = start_date
    logger.info(
        "feature panel date window: read_start=%s write_start=%s reset=%s",
        read_start_date,
        panel_start_date,
        reset,
    )
    active_a_filter_sql = _active_a_stock_filter_sql(duck, alias="kline")
    if active_a_filter_sql:
        logger.info("feature panel universe: dim_active_a_stock")
    else:
        logger.info("feature panel universe: canonical kline fallback (dim_active_a_stock unavailable)")

    logger.info("Step 1: Pillar B price/volume + Alpha158-inspired features")
    with _timed_stage(timer, "price_volume_features_s"):
        _replace_temp_table(
            duck,
            "current_panel",
            f"""
        WITH px AS (
            SELECT code as stock_code, date,
                   open, high, low, close, volume, amount,
                   source_name AS kline_source_name,
                   source_tier AS kline_source_tier,
                   is_fallback AS kline_is_fallback,
                   (close / NULLIF(LAG(close, 1) OVER (PARTITION BY code ORDER BY date), 0) - 1) AS close_ret_1d
            FROM ({KLINE_DAILY_QFQ_SQL}) AS kline
            WHERE date >= ?
              {active_a_filter_sql}
        ),
        features AS (
            SELECT
                stock_code, date, close, kline_source_name, kline_source_tier, kline_is_fallback,
                close_ret_1d AS ret_1d,
                (close / NULLIF(LAG(close, 5) OVER w, 0) - 1) AS ret_5d,
                (close / NULLIF(LAG(close, 20) OVER w, 0) - 1) AS ret_20d,
                (close / NULLIF(LAG(close, 60) OVER w, 0) - 1) AS ret_60d,
                (volume - AVG(volume) OVER (PARTITION BY stock_code ORDER BY date ROWS 19 PRECEDING))
                    / NULLIF(STDDEV_SAMP(volume) OVER (PARTITION BY stock_code ORDER BY date ROWS 19 PRECEDING), 0)
                    AS vol_z20d,
                (close / NULLIF(AVG(close) OVER (PARTITION BY stock_code ORDER BY date ROWS 4 PRECEDING), 0) - 1) AS ma_ratio_5,
                (close / NULLIF(AVG(close) OVER (PARTITION BY stock_code ORDER BY date ROWS 19 PRECEDING), 0) - 1) AS ma_ratio_20,
                (close / NULLIF(AVG(close) OVER (PARTITION BY stock_code ORDER BY date ROWS 59 PRECEDING), 0) - 1) AS ma_ratio_60,
                (close / NULLIF(AVG(close) OVER (PARTITION BY stock_code ORDER BY date ROWS 249 PRECEDING), 0) - 1) AS ma_ratio_250,
                ((close - open) / NULLIF(open, 0)) AS kmid,
                ((high - low) / NULLIF(open, 0)) AS klen,
                ((high - GREATEST(open, close)) / NULLIF(open, 0)) AS kup,
                ((LEAST(open, close) - low) / NULLIF(open, 0)) AS klow,
                ((2 * close - high - low) / NULLIF(open, 0)) AS ksft,
                (AVG(volume) OVER (PARTITION BY stock_code ORDER BY date ROWS 4 PRECEDING)
                 / NULLIF(AVG(volume) OVER (PARTITION BY stock_code ORDER BY date ROWS 19 PRECEDING), 0)) AS vol_ratio_5_20,
                STDDEV_SAMP(close_ret_1d) OVER (PARTITION BY stock_code ORDER BY date ROWS 4 PRECEDING) AS vol_std_5d,
                STDDEV_SAMP(close_ret_1d) OVER (PARTITION BY stock_code ORDER BY date ROWS 19 PRECEDING) AS vol_std_20d,
                (close - MIN(low) OVER (PARTITION BY stock_code ORDER BY date ROWS 19 PRECEDING))
                    / NULLIF(MAX(high) OVER (PARTITION BY stock_code ORDER BY date ROWS 19 PRECEDING)
                             - MIN(low) OVER (PARTITION BY stock_code ORDER BY date ROWS 19 PRECEDING), 0) AS range_pos_20,
                (close - MIN(low) OVER (PARTITION BY stock_code ORDER BY date ROWS 59 PRECEDING))
                    / NULLIF(MAX(high) OVER (PARTITION BY stock_code ORDER BY date ROWS 59 PRECEDING)
                             - MIN(low) OVER (PARTITION BY stock_code ORDER BY date ROWS 59 PRECEDING), 0) AS range_pos_60,
                (amount / NULLIF(LAG(amount, 5) OVER w, 0) - 1) AS amount_chg_5d,
                AVG(amount) OVER (PARTITION BY stock_code ORDER BY date ROWS 19 PRECEDING) AS amount_ma20
            FROM px
            WINDOW w AS (PARTITION BY stock_code ORDER BY date)
        )
        SELECT *, ret_5d - ret_20d AS momentum_diff
        FROM features
        """,
            [read_start_date],
        )
    logger.info("Pillar B done: %d rows, %.1fs", _row_count(duck, "current_panel"), time.time() - t0)

    if "margin" in blocks:
        logger.info("Step 2: margin join")
        with _timed_stage(timer, "margin_join_s"):
            _replace_temp_table(
                duck,
                "current_panel",
                """
            WITH margin AS (
                SELECT stock_code,
                       {margin_date} AS date,
                       rz_balance,
                       (
                           rz_balance
                           / NULLIF(LAG(rz_balance, 5) OVER (PARTITION BY stock_code ORDER BY {margin_date}), 0)
                           - 1
                       ) AS rz_chg_5d_pct
                FROM smartmoney.raw_margin_daily
            )
            SELECT p.*, m.rz_balance, m.rz_chg_5d_pct
            FROM current_panel p
            LEFT JOIN margin m
              ON p.stock_code = m.stock_code
             AND p.date = STRFTIME(m.date, '%Y-%m-%d')
            """.format(margin_date=_date_expr("trade_date")),
            )
    else:
        logger.info("Step 2: margin join skipped by scoped feature block plan")

    if "labels" in blocks:
        logger.info("Step 3: forward return horizon labels")
        with _timed_stage(timer, "forward_return_labels_s"):
            _replace_temp_table(
                duck,
                "current_panel",
                """
            SELECT *,
                   (LEAD(close, 6) OVER w / NULLIF(LEAD(close, 1) OVER w, 0) - 1) AS forward_ret_5d,
                   (LEAD(close, 11) OVER w / NULLIF(LEAD(close, 1) OVER w, 0) - 1) AS forward_ret_10d,
                   (LEAD(close, 21) OVER w / NULLIF(LEAD(close, 1) OVER w, 0) - 1) AS forward_ret_20d,
                   (LEAD(close, 61) OVER w / NULLIF(LEAD(close, 1) OVER w, 0) - 1) AS forward_ret_60d,
                   (LEAD(close, 91) OVER w / NULLIF(LEAD(close, 1) OVER w, 0) - 1) AS forward_ret_90d
            FROM current_panel
            WINDOW w AS (PARTITION BY stock_code ORDER BY date)
            """,
            )
    else:
        logger.info("Step 3: forward_ret_20d label skipped by scoped feature block plan")

    if "event_activity" in blocks:
        stage_started = time.perf_counter()
        logger.info("Step 4: event rolling counts")
        _rolling_event_count(
            duck,
            "SELECT stock_code, notice_date AS event_date FROM smartmoney.fact_institution_event",
            "inst_event_count", [30, 60],
        )
        _rolling_event_count(
            duck,
            "SELECT stock_code, notice_date AS event_date FROM smartmoney.fact_executive_trade_event WHERE direction='buy'",
            "exec_buy_count", [90],
        )
        _rolling_event_count(
            duck,
            "SELECT stock_code, notice_date AS event_date FROM smartmoney.fact_executive_trade_event "
            "WHERE direction='buy' AND total_change_pct_total >= 1.0",
            "exec_buy_ge1_count", [90],
        )
        _rolling_event_count(
            duck,
            "SELECT stock_code, trade_date AS event_date FROM smartmoney.fact_lhb_event WHERE is_inst_net_buy=1",
            "lhb_inst_buy_count", [30, 60],
        )
        try:
            _rolling_event_count(
                duck,
                "SELECT stock_code, notice_date AS event_date FROM smartmoney.fact_jgdy_event",
                "jgdy_count", [60],
            )
        except Exception as e:
            logger.warning("jgdy rolling skip: %s", e)
            _add_literal_columns(duck, {"jgdy_count_60d": "0::INTEGER"})
        try:
            _rolling_event_count(
                duck,
                "SELECT stock_code, trade_date AS event_date FROM smartmoney.fact_dzjy_event",
                "dzjy_count", [60],
            )
        except Exception as e:
            logger.warning("dzjy rolling skip: %s", e)
            _add_literal_columns(duck, {"dzjy_count_60d": "0::INTEGER"})
        if _relation_exists(duck, "smartmoney.fact_shareholder_plan_tdx_f10"):
            logger.info("Step 4: shareholder plan rolling counts/sums")
            plan_base_sql = """
                SELECT stock_code,
                       source_available_date AS event_date,
                       direction,
                       progress,
                       COALESCE(target_amount_max, target_amount_min, 0) AS event_value
                  FROM smartmoney.fact_shareholder_plan_tdx_f10
                 WHERE source_available_date IS NOT NULL
            """
            _rolling_event_count(
                duck,
                f"SELECT stock_code, event_date FROM ({plan_base_sql}) p WHERE direction LIKE '%增持%'",
                "shareholder_plan_increase_count", [180],
            )
            _rolling_event_count(
                duck,
                f"SELECT stock_code, event_date FROM ({plan_base_sql}) p WHERE direction LIKE '%减持%'",
                "shareholder_plan_decrease_count", [180],
            )
            _rolling_event_count(
                duck,
                f"SELECT stock_code, event_date FROM ({plan_base_sql}) p WHERE progress LIKE '%完成%'",
                "shareholder_plan_completed_count", [180],
            )
            _rolling_event_sum(
                duck,
                f"""
                SELECT stock_code, event_date, event_value
                  FROM ({plan_base_sql}) p
                 WHERE direction LIKE '%增持%'
                """,
                "shareholder_plan_increase_amount_max", [180],
            )
            _rolling_event_sum(
                duck,
                f"""
                SELECT stock_code, event_date, event_value
                  FROM ({plan_base_sql}) p
                 WHERE direction LIKE '%减持%'
                """,
                "shareholder_plan_decrease_amount_max", [180],
            )
        else:
            logger.warning("shareholder plan rolling skip: smartmoney.fact_shareholder_plan_tdx_f10 missing")
            _add_literal_columns(
                duck,
                {
                    "shareholder_plan_increase_count_180d": "0::INTEGER",
                    "shareholder_plan_decrease_count_180d": "0::INTEGER",
                    "shareholder_plan_completed_count_180d": "0::INTEGER",
                    "shareholder_plan_increase_amount_max_180d": "0.0::DOUBLE",
                    "shareholder_plan_decrease_amount_max_180d": "0.0::DOUBLE",
                },
            )
        if timer is not None:
            timer.record("event_rolling_counts_s", time.perf_counter() - stage_started)
    else:
        logger.info("Step 4: event rolling counts skipped by scoped feature block plan")

    if "event_activity" in blocks:
        stage_started = time.perf_counter()
        logger.info("Step 5: days_since features")
        for ev_sql, suffix in [
            ("SELECT stock_code, notice_date AS event_date FROM smartmoney.fact_executive_trade_event WHERE direction='buy'", "exec_buy"),
            ("SELECT stock_code, trade_date AS event_date FROM smartmoney.fact_lhb_event WHERE is_inst_net_buy=1", "lhb"),
        ]:
            try:
                _days_since_event(duck, ev_sql, suffix)
            except Exception as e:
                logger.warning("days_since %s skip: %s", suffix, e)
                _add_literal_columns(duck, {f"days_since_{suffix}": "-1::INTEGER"})
        if _relation_exists(duck, "smartmoney.fact_shareholder_plan_tdx_f10"):
            for ev_sql, suffix in [
                (
                    "SELECT stock_code, source_available_date AS event_date "
                    "FROM smartmoney.fact_shareholder_plan_tdx_f10 "
                    "WHERE source_available_date IS NOT NULL AND direction LIKE '%增持%'",
                    "shareholder_plan_increase",
                ),
                (
                    "SELECT stock_code, source_available_date AS event_date "
                    "FROM smartmoney.fact_shareholder_plan_tdx_f10 "
                    "WHERE source_available_date IS NOT NULL AND direction LIKE '%减持%'",
                    "shareholder_plan_decrease",
                ),
            ]:
                try:
                    _days_since_event(duck, ev_sql, suffix)
                except Exception as e:
                    logger.warning("days_since %s skip: %s", suffix, e)
                    _add_literal_columns(duck, {f"days_since_{suffix}": "-1::INTEGER"})
        else:
            _add_literal_columns(
                duck,
                {
                    "days_since_shareholder_plan_increase": "-1::INTEGER",
                    "days_since_shareholder_plan_decrease": "-1::INTEGER",
                },
            )
        if timer is not None:
            timer.record("days_since_features_s", time.perf_counter() - stage_started)
    else:
        logger.info("Step 5: days_since features skipped by scoped feature block plan")

    if "fundamentals" in blocks:
        fundamental_lag_days = _group_pit_release_lag_days("fundamentals", default=90)
        logger.info("Step 6: fundamentals ASOF join (release_lag_days=%d)", fundamental_lag_days)
        with _timed_stage(timer, "fundamentals_asof_join_s"):
            _replace_temp_table(
                duck,
                "current_panel",
                f"""
            WITH ffq AS (
                SELECT stock_code,
                       STRFTIME(STRPTIME(report_date, '%Y%m%d') + INTERVAL {fundamental_lag_days} DAY, '%Y-%m-%d') AS date,
                       shareholder_count, inst_count, fund_count, qfii_count,
                       yjyg_lower_pct, yjyg_upper_pct, roe, eps_basic,
                       (shareholder_count / NULLIF(LAG(shareholder_count) OVER w, 0) - 1) AS shareholder_count_qoq,
                       (inst_count / NULLIF(LAG(inst_count) OVER w, 0) - 1) AS inst_count_qoq,
                       (fund_count / NULLIF(LAG(fund_count) OVER w, 0) - 1) AS fund_count_qoq,
                       (qfii_count / NULLIF(LAG(qfii_count) OVER w, 0) - 1) AS qfii_count_qoq
                FROM smartmoney.fact_fundamental_quarterly
                WINDOW w AS (PARTITION BY stock_code ORDER BY report_date)
            )
            SELECT p.*,
                   f.shareholder_count_qoq, f.inst_count_qoq, f.fund_count_qoq, f.qfii_count_qoq,
                   f.yjyg_lower_pct, f.yjyg_upper_pct, f.roe, f.eps_basic
            FROM current_panel p
            ASOF LEFT JOIN ffq f
              ON p.stock_code = f.stock_code AND p.date >= f.date
            """,
            )
    else:
        logger.info("Step 6: fundamentals ASOF join skipped by scoped feature block plan")

    if "regime" in blocks:
        logger.info("Step 7: market regime")
        benchmark_codes_sql = _hs300_benchmark_codes_sql()
        with _timed_stage(timer, "market_regime_s"):
            _replace_temp_table(
                duck,
                "current_panel",
                f"""
            WITH benchmark_codes AS (
                SELECT * FROM {benchmark_codes_sql}
            ),
            benchmark_px AS (
                SELECT kline.date,
                       kline.close,
                       ROW_NUMBER() OVER (
                           PARTITION BY kline.date
                           ORDER BY benchmark_codes.priority
                       ) AS rn
                FROM ({KLINE_DAILY_QFQ_SQL}) AS kline
                JOIN benchmark_codes ON benchmark_codes.code = kline.code
            ),
            regime AS (
                SELECT date,
                       (close / NULLIF(LAG(close, 20) OVER (ORDER BY date), 0) - 1) AS hs300_ret_20d,
                       (close / NULLIF(LAG(close, 60) OVER (ORDER BY date), 0) - 1) AS hs300_ret_60d
                FROM benchmark_px
                WHERE rn = 1
            ),
            regime_labeled AS (
                SELECT *,
                       CASE
                         WHEN hs300_ret_20d IS NULL THEN 'na'
                         WHEN hs300_ret_20d > 0.03 THEN 'up'
                         WHEN hs300_ret_20d < -0.03 THEN 'down'
                         ELSE 'flat'
                       END AS regime_flag
                FROM regime
            )
            SELECT p.*, r.hs300_ret_20d, r.hs300_ret_60d, r.regime_flag
            FROM current_panel p
            LEFT JOIN regime_labeled r ON r.date = p.date
            """,
            )
    else:
        logger.info("Step 7: market regime skipped by scoped feature block plan")

    if "cross_sectional" in blocks:
        logger.info("Step 8: cross-sectional rank / industry-relative / margin normalization")
        cross_clean_cols = [
            "ret_20d",
            "ret_60d",
            "vol_z20d",
            "amount_chg_5d",
            "rz_balance",
            "rz_chg_5d_pct",
            "amount_ma20",
        ]
        cross_exclude_sql = ", ".join(_quote_ident(col) for col in cross_clean_cols)
        cross_clean_sql = ",\n                       ".join(
            f"{_finite_or_null_sql(col)} AS {_quote_ident(col)}"
            for col in cross_clean_cols
        )
        with _timed_stage(timer, "cross_sectional_features_s"):
            _replace_temp_table(
                duck,
                "current_panel",
                f"""
            WITH ind AS (
                SELECT stock_code, tdx_l1 FROM smartmoney.dim_stock_tdx_industry
            ),
            joined AS (
                SELECT p.*, ind.tdx_l1
                FROM current_panel p
                LEFT JOIN ind ON ind.stock_code = p.stock_code
            ),
            cleaned AS (
                SELECT * EXCLUDE ({cross_exclude_sql}),
                       {cross_clean_sql}
                FROM joined
            )
            SELECT *,
                   CASE WHEN ret_20d IS NULL THEN NULL ELSE PERCENT_RANK() OVER (PARTITION BY date ORDER BY ret_20d NULLS LAST) END AS ret_20d_rank,
                   CASE WHEN ret_60d IS NULL THEN NULL ELSE PERCENT_RANK() OVER (PARTITION BY date ORDER BY ret_60d NULLS LAST) END AS ret_60d_rank,
                   CASE WHEN vol_z20d IS NULL THEN NULL ELSE PERCENT_RANK() OVER (PARTITION BY date ORDER BY vol_z20d NULLS LAST) END AS vol_z20d_rank,
                   CASE WHEN amount_chg_5d IS NULL THEN NULL ELSE PERCENT_RANK() OVER (PARTITION BY date ORDER BY amount_chg_5d NULLS LAST) END AS amount_chg_5d_rank,
                   CASE WHEN rz_balance IS NULL THEN NULL ELSE PERCENT_RANK() OVER (PARTITION BY date ORDER BY rz_balance NULLS LAST) END AS rz_balance_rank,
                   CASE WHEN rz_chg_5d_pct IS NULL THEN NULL ELSE PERCENT_RANK() OVER (PARTITION BY date ORDER BY rz_chg_5d_pct NULLS LAST) END AS rz_chg_5d_pct_rank,
                   ret_20d - AVG(ret_20d) OVER (PARTITION BY date, tdx_l1) AS ret_20d_tdx_l1_rel,
                   ret_60d - AVG(ret_60d) OVER (PARTITION BY date, tdx_l1) AS ret_60d_tdx_l1_rel,
                   vol_z20d - AVG(vol_z20d) OVER (PARTITION BY date, tdx_l1) AS vol_z20d_tdx_l1_rel,
                   amount_chg_5d - AVG(amount_chg_5d) OVER (PARTITION BY date, tdx_l1) AS amount_chg_5d_tdx_l1_rel,
                   rz_balance / NULLIF(amount_ma20, 0) AS rz_balance_to_amount20
            FROM cleaned
            """,
            )
    else:
        logger.info("Step 8: cross-sectional block skipped by scoped feature block plan")

    logger.info("Step 9: write fact_feature_panel")
    with _timed_stage(timer, "write_fact_feature_panel_s"):
        summary = _insert_fact_panel(
            duck,
            reset=reset,
            write_start_date=write_filter_date,
            update_columns=update_columns,
        )
        record_actual_version(duck, "fact_feature_panel")
    if timer is not None:
        timer.record("total_build_s", time.time() - t0)
    logger.info(
        "fact_feature_panel: rows=%d codes=%d dates=%d label_non_null=%d elapsed=%.1f min",
        summary["rows"], summary["codes"], summary["dates"], summary["label_non_null"],
        (time.time() - t0) / 60,
    )
    return summary


def _default_run_id(run_mode: str) -> str:
    return f"feature_panel_duck_{run_mode}_{time.strftime('%Y%m%d_%H%M%S')}"


def _record_feature_panel_pipeline_run(
    duck,
    *,
    run_id: str,
    run_mode: str,
    status: str,
    started_at: str,
    duration_s: float,
    timer: PipelineTimer,
    start_date: str | None = None,
    plan: dict[str, Any] | None = None,
    summary: dict[str, Any] | None = None,
    validation: dict[str, Any] | None = None,
    feature_groups: list[str] | None = None,
    blockers: list[str] | None = None,
) -> None:
    validation_summary = None
    if validation is not None:
        validation_summary = {
            "status": validation.get("status"),
            "rows": validation.get("rows"),
            "duplicate_keys": validation.get("duplicate_keys"),
            "close_coverage": validation.get("close_coverage"),
            "source_lineage_coverage": validation.get("source_lineage_coverage"),
            "source_fallback_ratio": validation.get("source_fallback_ratio"),
            "source_watermark_hash": validation.get("source_watermark_hash"),
            "blockers": validation.get("blockers") or [],
        }
    record_pipeline_run(
        duck,
        run_id=run_id,
        pipeline_name="build_feature_panel_duck",
        status=status,
        started_at=started_at,
        ended_at=utc_now_iso(),
        duration_s=duration_s,
        commit_sha=git_commit_sha(),
        input_tables=[
            "market.price_kline_tdxhub",
            "smartmoney.dim_active_a_stock",
            "smartmoney.raw_margin_daily",
            "smartmoney.fact_institution_event",
            "smartmoney.fact_executive_trade_event",
            "smartmoney.fact_lhb_event",
            "smartmoney.fact_shareholder_plan_tdx_f10",
            "smartmoney.fact_fundamental_quarterly",
            "smartmoney.dim_stock_tdx_industry",
        ],
        output_tables=["fact_feature_panel", "mart_feature_panel_validation"],
        gate_result="pass" if status == "success" and not blockers else "blocked",
        blockers=blockers or [],
        perf_summary={
            "stage_timings": dict(timer.stage_timings),
            "run_mode": run_mode,
            "start_date": start_date,
            "feature_groups": feature_groups or [],
            "plan": plan,
            "summary": summary,
            "validation": validation_summary,
        },
    )


def build_panel(start_date: str, *, run_id: str | None = None) -> dict[str, int]:
    timer = PipelineTimer()
    started_at = utc_now_iso()
    started = time.perf_counter()
    run_id = run_id or _default_run_id("full")
    recorded = False
    with duck_connection(writable=True) as duck:
        try:
            summary = _build_panel_with_connection(duck, start_date, timer=timer)
            with timer.stage("validate_feature_panel_s"):
                validation = validate_feature_panel(duck)
            with timer.stage("record_feature_panel_validation_s"):
                record_feature_panel_validation(duck, validation, run_mode="full")
            blockers = list(validation.get("blockers") or [])
            status = "success" if validation["status"] == "passed" else "failed"
            _record_feature_panel_pipeline_run(
                duck,
                run_id=run_id,
                run_mode="full",
                status=status,
                started_at=started_at,
                duration_s=round(time.perf_counter() - started, 3),
                timer=timer,
                start_date=start_date,
                summary=summary,
                validation=validation,
                blockers=blockers,
            )
            recorded = True
            if validation["status"] != "passed":
                raise RuntimeError(f"feature panel validation failed: {validation['blockers']}")
            return summary
        except Exception as exc:
            if not recorded:
                _record_feature_panel_pipeline_run(
                    duck,
                    run_id=run_id,
                    run_mode="full",
                    status="failed",
                    started_at=started_at,
                    duration_s=round(time.perf_counter() - started, 3),
                    timer=timer,
                    start_date=start_date,
                    blockers=[str(exc)],
                )
            raise


def build_panel_incremental(
    start_date: str | None = None,
    *,
    feature_groups: list[str] | None = None,
    run_id: str | None = None,
) -> dict:
    timer = PipelineTimer()
    started_at = utc_now_iso()
    started = time.perf_counter()
    run_id = run_id or _default_run_id("incremental")
    recorded = False
    with duck_connection(writable=True) as duck:
        with timer.stage("plan_incremental_window_s"):
            plan = plan_incremental_window(duck, start_date=start_date)
        if plan["noop"]:
            result = {"status": "noop", "plan": plan}
            _record_feature_panel_pipeline_run(
                duck,
                run_id=run_id,
                run_mode="incremental",
                status="success",
                started_at=started_at,
                duration_s=round(time.perf_counter() - started, 3),
                timer=timer,
                start_date=start_date,
                plan=plan,
                feature_groups=feature_groups,
            )
            recorded = True
            return result
        update_columns = feature_group_columns(feature_groups) if feature_groups else None
        try:
            summary = _build_panel_with_connection(
                duck,
                str(plan["read_start_date"]),
                reset=False,
                write_start_date=str(plan["write_start_date"]),
                update_columns=update_columns,
                timer=timer,
            )
            with timer.stage("validate_feature_panel_s"):
                validation = validate_feature_panel(duck)
            with timer.stage("record_feature_panel_validation_s"):
                record_feature_panel_validation(duck, validation, run_mode="incremental")
            blockers = list(validation.get("blockers") or [])
            status = "success" if validation["status"] == "passed" else "failed"
            _record_feature_panel_pipeline_run(
                duck,
                run_id=run_id,
                run_mode="incremental",
                status=status,
                started_at=started_at,
                duration_s=round(time.perf_counter() - started, 3),
                timer=timer,
                start_date=start_date,
                plan=plan,
                summary=summary,
                validation=validation,
                feature_groups=feature_groups,
                blockers=blockers,
            )
            recorded = True
            if validation["status"] != "passed":
                return {"status": "failed", "plan": plan, "summary": summary, "validation": validation}
            return {
                "status": "completed",
                "plan": plan,
                "summary": summary,
                "validation": validation,
                "feature_groups": feature_groups or [],
                "updated_columns": update_columns or [],
            }
        except Exception as exc:
            if not recorded:
                _record_feature_panel_pipeline_run(
                    duck,
                    run_id=run_id,
                    run_mode="incremental",
                    status="failed",
                    started_at=started_at,
                    duration_s=round(time.perf_counter() - started, 3),
                    timer=timer,
                    start_date=start_date,
                    plan=plan,
                    feature_groups=feature_groups,
                    blockers=[str(exc)],
                )
            raise


def validate_feature_panel(duck, *, min_close_coverage: float = 0.99) -> dict:
    blockers = []
    if not _table_exists(duck, "fact_feature_panel"):
        return {"status": "failed", "blockers": ["missing fact_feature_panel"]}

    rows = int(duck.execute("SELECT COUNT(*) FROM fact_feature_panel").fetchone()[0] or 0)
    if rows <= 0:
        blockers.append("empty fact_feature_panel")

    duplicate_count = int(
        duck.execute(
            """
            SELECT COUNT(*)
              FROM (
                SELECT stock_code, date, COUNT(*) AS n
                  FROM fact_feature_panel
                 GROUP BY stock_code, date
                HAVING COUNT(*) > 1
              )
            """
        ).fetchone()[0]
        or 0
    )
    if duplicate_count:
        blockers.append(f"duplicate panel keys: {duplicate_count}")

    coverage = None
    if rows:
        coverage = float(
            duck.execute(
                "SELECT AVG(CASE WHEN close IS NOT NULL THEN 1.0 ELSE 0.0 END) FROM fact_feature_panel"
            ).fetchone()[0]
            or 0.0
        )
        if coverage < min_close_coverage:
            blockers.append(f"close coverage {coverage:.4f} < {min_close_coverage:.4f}")

    source_summary = _feature_panel_source_summary(duck, rows)
    source_snapshot = feature_panel_source_snapshot(duck)
    source_snapshot_hash = feature_panel_source_snapshot_hash(source_snapshot)
    missing_lineage_cols = source_summary.get("missing_columns") or []
    if missing_lineage_cols:
        blockers.append("missing kline source lineage columns: " + ",".join(missing_lineage_cols))
    lineage_coverage = source_summary.get("lineage_coverage")
    if rows and lineage_coverage is not None and float(lineage_coverage) < 0.999:
        blockers.append(f"kline source lineage coverage {float(lineage_coverage):.4f} < 0.9990")

    label_in_inputs = sorted(PIT_LABEL_COLS.intersection(feature_input_columns()))
    if label_in_inputs:
        blockers.append("label columns present in model inputs: " + ",".join(label_in_inputs))

    registry_result = validate_feature_registry()
    if registry_result["status"] != "passed":
        for key in ("missing_features", "stale_features", "labels_in_inputs"):
            values = registry_result.get(key) or []
            if values:
                blockers.append(f"feature registry {key}: {','.join(values)}")

    return {
        "status": "failed" if blockers else "passed",
        "rows": rows,
        "duplicate_keys": duplicate_count,
        "close_coverage": coverage,
        "source_lineage_coverage": source_summary.get("lineage_coverage"),
        "source_fallback_ratio": source_summary.get("fallback_ratio"),
        "source_distribution": source_summary.get("distribution") or [],
        "source_watermark_hash": source_snapshot_hash,
        "source_watermarks": source_snapshot,
        "feature_registry": registry_result,
        "blockers": blockers,
    }


def _feature_panel_source_summary(duck, rows: int) -> dict:
    panel_cols = set(_table_columns(duck, "fact_feature_panel"))
    missing_cols = sorted(KLINE_LINEAGE_COLS - panel_cols)
    if missing_cols or rows <= 0:
        return {
            "missing_columns": missing_cols,
            "lineage_coverage": None,
            "fallback_ratio": None,
            "distribution": [],
        }

    summary_row = duck.execute(
        """
        SELECT
            AVG(
                CASE
                    WHEN kline_source_name IS NOT NULL
                     AND kline_source_tier IS NOT NULL
                     AND kline_is_fallback IS NOT NULL
                    THEN 1.0 ELSE 0.0
                END
            ) AS lineage_coverage,
            AVG(CASE WHEN kline_is_fallback THEN 1.0 ELSE 0.0 END) AS fallback_ratio
          FROM fact_feature_panel
        """
    ).fetchone()
    distribution = []
    for row in duck.execute(
        """
        SELECT
            COALESCE(kline_source_name, 'unknown') AS source_name,
            kline_source_tier AS source_tier,
            COALESCE(kline_is_fallback, FALSE) AS is_fallback,
            COUNT(*) AS row_count,
            COUNT(DISTINCT date) AS date_count
          FROM fact_feature_panel
         GROUP BY 1, 2, 3
         ORDER BY row_count DESC, source_tier NULLS LAST, source_name
        """
    ).fetchall():
        row_count = int(row[3] or 0)
        distribution.append(
            {
                "source_name": row[0],
                "source_tier": int(row[1]) if row[1] is not None else None,
                "is_fallback": bool(row[2]),
                "rows": row_count,
                "row_pct": (row_count / rows) if rows else None,
                "dates": int(row[4] or 0),
            }
        )
    return {
        "missing_columns": [],
        "lineage_coverage": float(summary_row[0] or 0.0) if summary_row else None,
        "fallback_ratio": float(summary_row[1] or 0.0) if summary_row else None,
        "distribution": distribution,
    }


def record_feature_panel_validation(duck, result: dict, *, run_mode: str) -> None:
    """Persist the latest panel validation summary for UI and pipeline audit."""

    execute_script(duck, FEATURE_PANEL_VALIDATION_DDL)
    validated_at = datetime.now(UTC).replace(tzinfo=None).isoformat()
    validation_id = f"feature_panel_{run_mode}_{validated_at}"
    duck.execute(
        """
        INSERT OR REPLACE INTO mart_feature_panel_validation (
            validation_id, run_mode, status, validated_at, rows,
            duplicate_keys, close_coverage, source_lineage_coverage,
            source_fallback_ratio, source_distribution_json,
            source_watermark_hash, source_watermarks_json,
            feature_registry_json, blockers_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            validation_id,
            run_mode,
            result.get("status"),
            validated_at,
            result.get("rows"),
            result.get("duplicate_keys"),
            result.get("close_coverage"),
            result.get("source_lineage_coverage"),
            result.get("source_fallback_ratio"),
            json.dumps(result.get("source_distribution") or [], ensure_ascii=False, sort_keys=True),
            result.get("source_watermark_hash"),
            json.dumps(result.get("source_watermarks") or [], ensure_ascii=False, sort_keys=True),
            json.dumps(result.get("feature_registry"), ensure_ascii=False, sort_keys=True),
            json.dumps(result.get("blockers") or [], ensure_ascii=False, sort_keys=True),
        ],
    )
    record_actual_version(duck, "mart_feature_panel_validation")


def validate_and_record_feature_panel(duck, *, run_mode: str = "validate-only") -> dict:
    """Validate the current panel and persist the validation audit row."""

    result = validate_feature_panel(duck)
    record_feature_panel_validation(duck, result, run_mode=run_mode)
    try:
        duck.commit()
    except Exception:
        pass
    return result


def _parse_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default=None)
    parser.add_argument("--mode", choices=["full", "incremental", "backfill", "validate-only"], default="full")
    parser.add_argument(
        "--feature-groups",
        default=None,
        help="comma-separated registry groups for scoped incremental/backfill column updates",
    )
    parser.add_argument("--run-id", default=None)
    args = parser.parse_args()
    feature_groups = _parse_csv(args.feature_groups)
    if feature_groups and args.mode not in {"incremental", "backfill"}:
        raise SystemExit("--feature-groups is only supported with --mode incremental/backfill")
    if args.mode == "full":
        build_panel(args.start or "2023-01-01", run_id=args.run_id)
    elif args.mode == "backfill":
        if not args.start:
            raise SystemExit("--start is required with --mode backfill")
        build_panel_incremental(args.start, feature_groups=feature_groups or None, run_id=args.run_id)
    elif args.mode == "incremental":
        build_panel_incremental(args.start, feature_groups=feature_groups or None, run_id=args.run_id)
    else:
        with duck_connection(writable=True) as duck:
            result = validate_and_record_feature_panel(duck, run_mode="validate-only")
        if result["status"] != "passed":
            raise SystemExit(f"feature panel validation failed: {result['blockers']}")


if __name__ == "__main__":
    main()
