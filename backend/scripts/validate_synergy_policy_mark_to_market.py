#!/usr/bin/env python3
"""Mark-to-market validation for synergy policy candidates.

This validator uses signal-day executable VWAP as the follow entry cost, then
marks each selected position on the daily canonical TDXHub K-line path until the
candidate horizon exit date.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.validate_synergy_policy_candidate import (  # noqa: E402
    _finite,
    _json,
    _latest_candidate_run_id,
    _load_candidate,
    _load_feature_directions,
    _quote_ident,
    _quote_relation,
    _table_columns,
    _table_exists,
)
from services.db import get_conn  # noqa: E402
from services.model_feature_schema import holding_period_from_label  # noqa: E402
from services.pipeline_manifest import git_commit_sha, record_pipeline_run, utc_now_iso  # noqa: E402
from services.pricing_policy import load_pricing_label_policy, record_pricing_label_policy  # noqa: E402
from services.schema_versions import record_actual_version  # noqa: E402


REPO = Path(__file__).resolve().parent.parent.parent
MARKET_DB = REPO / "data" / "market.duckdb"
PRIMARY_TDXHUB_KLINE_RELATION = "market.price_kline_tdxhub"

DDL = """
CREATE TABLE IF NOT EXISTS mart_synergy_policy_mtm_position (
    run_id TEXT NOT NULL,
    position_id BIGINT NOT NULL,
    candidate_run_id TEXT NOT NULL,
    source_run_id TEXT NOT NULL,
    label_name TEXT NOT NULL,
    stock_code TEXT NOT NULL,
    entry_date TEXT NOT NULL,
    scheduled_exit_date TEXT,
    exit_date TEXT NOT NULL,
    holding_days INTEGER NOT NULL,
    signal_score DOUBLE,
    score_rank DOUBLE,
    label_value DOUBLE,
    entry_price DOUBLE NOT NULL,
    entry_price_method TEXT NOT NULL,
    exit_price_method TEXT,
    exit_delay_calendar_days INTEGER,
    entry_close DOUBLE,
    exit_close DOUBLE NOT NULL,
    gross_return DOUBLE,
    net_return DOUBLE,
    transaction_cost_bps DOUBLE,
    kline_source_name TEXT,
    kline_source_tier INTEGER,
    kline_is_fallback BOOLEAN,
    built_at TEXT NOT NULL,
    PRIMARY KEY (run_id, position_id)
);
ALTER TABLE mart_synergy_policy_mtm_position ADD COLUMN IF NOT EXISTS scheduled_exit_date TEXT;
ALTER TABLE mart_synergy_policy_mtm_position ADD COLUMN IF NOT EXISTS exit_price_method TEXT;
ALTER TABLE mart_synergy_policy_mtm_position ADD COLUMN IF NOT EXISTS exit_delay_calendar_days INTEGER;
CREATE INDEX IF NOT EXISTS idx_synergy_mtm_position_run_date
    ON mart_synergy_policy_mtm_position(run_id, entry_date);
CREATE INDEX IF NOT EXISTS idx_synergy_mtm_position_stock
    ON mart_synergy_policy_mtm_position(run_id, stock_code);

CREATE TABLE IF NOT EXISTS mart_synergy_policy_mtm_daily_path (
    run_id TEXT NOT NULL,
    date TEXT NOT NULL,
    active_position_count BIGINT NOT NULL,
    daily_gross_return DOUBLE NOT NULL,
    daily_cost_rate DOUBLE NOT NULL,
    daily_net_return DOUBLE NOT NULL,
    equity DOUBLE NOT NULL,
    drawdown DOUBLE NOT NULL,
    built_at TEXT NOT NULL,
    PRIMARY KEY (run_id, date)
);

CREATE TABLE IF NOT EXISTS mart_synergy_policy_mtm_gate (
    run_id TEXT PRIMARY KEY,
    candidate_run_id TEXT NOT NULL,
    source_run_id TEXT NOT NULL,
    label_name TEXT NOT NULL,
    baseline_horizon_days INTEGER,
    candidate_horizon_days INTEGER,
    validation_status TEXT NOT NULL,
    promotion_status TEXT NOT NULL,
    production_eligible BOOLEAN NOT NULL,
    signal_count BIGINT,
    repeated_signal_suppressed_count BIGINT,
    no_exit_date_count BIGINT,
    position_count BIGINT,
    date_count BIGINT,
    expected_path_rows BIGINT,
    missing_path_price_count BIGINT,
    non_tdxhub_kline_count BIGINT,
    total_return DOUBLE,
    annualized_return DOUBLE,
    max_drawdown DOUBLE,
    avg_daily_return DOUBLE,
    volatility DOUBLE,
    sharpe DOUBLE,
    avg_active_positions DOUBLE,
    avg_position_net_return DOUBLE,
    position_hit_rate DOUBLE,
    transaction_cost_bps DOUBLE,
    blockers_json TEXT,
    thresholds_json TEXT,
    evidence_json TEXT,
    built_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS mart_synergy_policy_mtm_evidence_bundle (
    run_id TEXT PRIMARY KEY,
    candidate_run_id TEXT NOT NULL,
    source_run_id TEXT NOT NULL,
    label_name TEXT NOT NULL,
    selected_features_json TEXT,
    selected_interactions_json TEXT,
    feature_directions_json TEXT,
    gate_json TEXT,
    stage_timings_json TEXT,
    built_at TEXT NOT NULL
);
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


def _progress(enabled: bool, message: str) -> None:
    if enabled:
        print(f"[synergy-mtm] {utc_now_iso()} {message}", flush=True)


def _schema_exists(conn: Any, schema: str) -> bool:
    try:
        row = conn.execute(
            """
            SELECT 1
              FROM information_schema.schemata
             WHERE schema_name = ?
             LIMIT 1
            """,
            (schema,),
        ).fetchone()
        if row is not None:
            return True
    except Exception:
        pass
    try:
        row = conn.execute(
            """
            SELECT 1
              FROM duckdb_databases()
             WHERE database_name = ?
             LIMIT 1
            """,
            (schema,),
        ).fetchone()
        return row is not None
    except Exception:
        return False


def _attach_market(conn: Any, *, schema: str = "market", market_db: Path = MARKET_DB) -> bool:
    if _schema_exists(conn, schema):
        return True
    if not market_db.exists():
        return False
    conn.execute(f"ATTACH IF NOT EXISTS '{market_db}' AS {schema} (READ_ONLY)")
    return _schema_exists(conn, schema)


def _relation_columns(conn: Any, relation: str) -> set[str]:
    try:
        return {str(row[0]) for row in conn.execute(f"DESCRIBE {_quote_relation(relation)}").fetchall()}
    except Exception:
        return set()


def _require_calendar(conn: Any) -> list[tuple[str, int]]:
    if not _table_exists(conn, "dim_trading_calendar"):
        raise RuntimeError("dim_trading_calendar is required before mark-to-market validation")
    rows = conn.execute(
        """
        SELECT trade_date
          FROM dim_trading_calendar
         WHERE is_trading = 1
         ORDER BY trade_date
        """
    ).fetchall()
    if not rows:
        raise RuntimeError("dim_trading_calendar has no trading days")
    return [(str(row["trade_date"]), idx) for idx, row in enumerate(rows)]


def _stddev(values: list[float]) -> float:
    clean = [value for value in values if math.isfinite(value)]
    if len(clean) <= 1:
        return 0.0
    avg = sum(clean) / len(clean)
    return math.sqrt(sum((value - avg) ** 2 for value in clean) / (len(clean) - 1))


def _build_scored_signals(
    conn: Any,
    *,
    source_run_id: str,
    label_name: str,
    selected_features: list[str],
    selected_interactions: list[dict[str, str]],
    feature_directions: dict[str, int],
    start_date: str | None,
    end_date: str | None,
    conditional_threshold: float,
) -> None:
    panel_table = "mart_temporal_research_panel"
    if not _table_exists(conn, panel_table):
        raise RuntimeError("mart_temporal_research_panel is required for mark-to-market validation")
    panel_cols = _table_columns(conn, panel_table)
    date_column = "date" if "date" in panel_cols else "signal_date" if "signal_date" in panel_cols else None
    required = {"run_id", "stock_code", label_name}
    missing_required = sorted(required - panel_cols)
    if not date_column:
        missing_required.append("date_or_signal_date")
    if missing_required:
        raise RuntimeError(f"temporal research panel missing required columns: {missing_required}")
    usable_features = [feature for feature in selected_features if feature in panel_cols]
    if not usable_features:
        raise RuntimeError("candidate has no usable selected features in mart_temporal_research_panel")

    filters = ["run_id = ?"]
    params: list[Any] = [source_run_id]
    if start_date:
        filters.append(f"CAST({_quote_ident(date_column)} AS DATE) >= CAST(? AS DATE)")
        params.append(start_date)
    if end_date:
        filters.append(f"CAST({_quote_ident(date_column)} AS DATE) <= CAST(? AS DATE)")
        params.append(end_date)
    label_expr = _quote_ident(label_name)
    filters.append(f"{label_expr} IS NOT NULL")
    rank_exprs = []
    for feature in usable_features:
        direction = feature_directions.get(feature, 1)
        order = "ASC" if direction >= 0 else "DESC"
        alias = f"__rank_{feature}"
        rank_exprs.append(
            f"PERCENT_RANK() OVER (PARTITION BY signal_date ORDER BY {_quote_ident(feature)} {order} NULLS FIRST) AS {_quote_ident(alias)}"
        )
    conn.execute(
        f"""
        CREATE OR REPLACE TEMP TABLE synergy_mtm_feature_rank AS
        SELECT stock_code,
               CAST({_quote_ident(date_column)} AS VARCHAR) AS signal_date,
               {label_expr} AS label_value,
               {", ".join(rank_exprs)}
          FROM {_quote_relation(panel_table)}
         WHERE {" AND ".join(filters)}
        """,
        params,
    )

    score_terms = [f"{_quote_ident(f'__rank_{feature}')}" for feature in usable_features]
    interaction_terms = []
    for interaction in selected_interactions:
        a = interaction["feature_a"]
        b = interaction["feature_b"]
        interaction_type = str(interaction.get("interaction_type") or "pair").lower()
        if a in usable_features and b in usable_features:
            rank_a = _quote_ident(f"__rank_{a}")
            rank_b = _quote_ident(f"__rank_{b}")
            if interaction_type == "conditional":
                threshold_sql = max(min(float(conditional_threshold), 0.999), 0.001)
                interaction_terms.append(
                    f"CASE WHEN {rank_a} >= {threshold_sql} THEN {rank_b} ELSE 0.5 END"
                )
            else:
                interaction_terms.append(f"{rank_a} * {rank_b}")
    all_terms = score_terms + interaction_terms
    conn.execute(
        f"""
        CREATE OR REPLACE TEMP TABLE synergy_mtm_scored AS
        SELECT stock_code,
               signal_date,
               label_value,
               ({' + '.join(all_terms)}) / {float(len(all_terms))} AS policy_score
          FROM synergy_mtm_feature_rank
        """
    )
    conn.execute(
        """
        CREATE OR REPLACE TEMP TABLE synergy_mtm_ranked AS
        SELECT stock_code,
               signal_date,
               label_value,
               policy_score,
               PERCENT_RANK() OVER (
                   PARTITION BY signal_date
                   ORDER BY policy_score NULLS FIRST, stock_code
               ) AS score_rank
          FROM synergy_mtm_scored
         WHERE policy_score IS NOT NULL
        """
    )


def _load_kept_signals(
    conn: Any,
    *,
    threshold: float,
    horizon_days: int,
    calendar_days: list[tuple[str, int]],
) -> dict[str, int]:
    conn.execute("DROP TABLE IF EXISTS __mtm_calendar")
    conn.execute("CREATE TEMP TABLE __mtm_calendar(trade_date TEXT PRIMARY KEY, trade_idx BIGINT)")
    conn.executemany("INSERT INTO __mtm_calendar VALUES (?, ?)", calendar_days)
    max_trade_idx = calendar_days[-1][1]
    rows = conn.execute(
        """
        SELECT r.stock_code,
               r.signal_date AS entry_date,
               c.trade_idx AS entry_trade_idx,
               r.policy_score,
               r.score_rank,
               r.label_value
          FROM synergy_mtm_ranked r
          JOIN __mtm_calendar c
            ON c.trade_date = r.signal_date
         WHERE r.score_rank >= ?
         ORDER BY r.stock_code, c.trade_idx, r.score_rank DESC
        """,
        (threshold,),
    ).fetchall()
    signal_count = len(rows)
    kept_rows: list[tuple[Any, ...]] = []
    active_until_by_stock: dict[str, int] = {}
    repeated = 0
    no_exit = 0
    position_id = 0
    for row in rows:
        stock_code = str(row["stock_code"])
        entry_idx = int(row["entry_trade_idx"])
        exit_idx = entry_idx + int(horizon_days)
        if exit_idx > max_trade_idx:
            no_exit += 1
            continue
        if entry_idx <= active_until_by_stock.get(stock_code, -1):
            repeated += 1
            continue
        position_id += 1
        active_until_by_stock[stock_code] = exit_idx
        kept_rows.append(
            (
                position_id,
                stock_code,
                str(row["entry_date"]),
                entry_idx,
                exit_idx,
                _finite(row["policy_score"]),
                _finite(row["score_rank"]),
                row["label_value"],
            )
        )
    conn.execute(
        """
        CREATE OR REPLACE TEMP TABLE __mtm_kept_signals (
            position_id BIGINT,
            stock_code TEXT,
            entry_date TEXT,
            entry_trade_idx BIGINT,
            exit_trade_idx BIGINT,
            signal_score DOUBLE,
            score_rank DOUBLE,
            label_value DOUBLE
        )
        """
    )
    if kept_rows:
        conn.executemany("INSERT INTO __mtm_kept_signals VALUES (?, ?, ?, ?, ?, ?, ?, ?)", kept_rows)
    return {
        "signal_count": signal_count,
        "position_signal_count": len(kept_rows),
        "repeated_signal_suppressed_count": repeated,
        "no_exit_date_count": no_exit,
    }


def _materialize_kline(conn: Any, *, kline_relation: str) -> dict[str, Any]:
    cols = _relation_columns(conn, kline_relation)
    required = {"code", "date", "open", "close", "volume", "amount"}
    missing = sorted(required - cols)
    if missing:
        raise RuntimeError(f"kline relation missing required columns: {missing}")
    if "source_name" in cols:
        source_name_expr = "COALESCE(source_name, 'unknown')"
    elif "source" in cols:
        source_name_expr = "COALESCE(source, 'tdxhub')"
    else:
        source_name_expr = "'unknown'"
    if "source_tier" in cols:
        source_tier_expr = "COALESCE(source_tier, 1)"
    elif "source" in cols:
        source_tier_expr = "CASE WHEN LOWER(COALESCE(source, 'tdxhub')) LIKE '%tdxhub%' THEN 1 ELSE 3 END"
    else:
        source_tier_expr = "1"
    is_fallback_expr = "COALESCE(is_fallback, FALSE)" if "is_fallback" in cols else "FALSE"
    lineage_available = bool({"source", "source_name", "source_tier", "is_fallback"} & cols)
    filters = ["TRUE"]
    if "freq" in cols:
        filters.append("freq = 'daily'")
    if "adjust" in cols:
        filters.append("adjust = 'qfq'")
    for column in ("open", "close", "volume", "amount"):
        filters.append(f"{_quote_ident(column)} IS NOT NULL")
        filters.append(f"{_quote_ident(column)} > 0")
    row = conn.execute(
        """
        SELECT MIN(entry_date) AS min_date,
               MAX(exit_date) AS max_date
          FROM (
              SELECT s.entry_date, c.trade_date AS exit_date
                FROM __mtm_kept_signals s
                JOIN __mtm_calendar c
                  ON c.trade_idx = s.exit_trade_idx
          )
        """
    ).fetchone()
    min_date = row["min_date"]
    max_date = row["max_date"]
    conn.execute("DROP TABLE IF EXISTS __mtm_codes")
    conn.execute(
        """
        CREATE TEMP TABLE __mtm_codes AS
        SELECT DISTINCT stock_code AS code
          FROM __mtm_kept_signals
        """
    )
    conn.execute(
        f"""
        CREATE OR REPLACE TEMP TABLE __mtm_kline AS
        SELECT k.code,
               CAST(k.date AS TEXT) AS date,
               CAST(k.open AS DOUBLE) AS open,
               CAST(k.close AS DOUBLE) AS close,
               CAST(k.volume AS DOUBLE) AS volume,
               CAST(k.amount AS DOUBLE) AS amount,
               {source_name_expr} AS source_name,
               CAST({source_tier_expr} AS INTEGER) AS source_tier,
               CAST({is_fallback_expr} AS BOOLEAN) AS is_fallback
          FROM {_quote_relation(kline_relation)} k
          JOIN __mtm_codes c
            ON c.code = k.code
         WHERE {" AND ".join(filters)}
           AND CAST(k.date AS DATE) >= CAST(? AS DATE)
           AND CAST(k.date AS DATE) <= CAST(? AS DATE)
        """,
        (min_date, max_date),
    )
    kline_count = int(conn.execute("SELECT COUNT(*) AS n FROM __mtm_kline").fetchone()["n"] or 0)
    return {
        "kline_relation": kline_relation,
        "kline_rows": kline_count,
        "kline_lineage_available": lineage_available,
        "min_kline_date": min_date,
        "max_kline_date": max_date,
    }


def _materialize_positions(
    conn: Any,
    *,
    run_id: str,
    candidate_run_id: str,
    source_run_id: str,
    label_name: str,
    horizon_days: int,
    transaction_cost_bps: float,
    built_at: str,
) -> dict[str, int]:
    cost_rate = max(float(transaction_cost_bps), 0.0) / 10000.0
    conn.execute(
        """
        CREATE OR REPLACE TEMP TABLE __mtm_positions_raw AS
        WITH positioned AS (
            SELECT s.*,
                   c.trade_date AS scheduled_exit_date
              FROM __mtm_kept_signals s
              JOIN __mtm_calendar c
                ON c.trade_idx = s.exit_trade_idx
        ),
        entry_px AS (
            SELECT p.*,
                   e.open AS entry_open,
                   e.close AS entry_close,
                   e.amount AS entry_amount,
                   e.volume AS entry_volume,
                   e.source_name AS kline_source_name,
                   e.source_tier AS kline_source_tier,
                   e.is_fallback AS kline_is_fallback,
                   COALESCE(x.date, nx.date) AS executable_exit_date,
                   COALESCE(x.close, nx.close) AS exit_close,
                   CASE
                     WHEN x.close > 0 THEN 'horizon_day_close_qfq'
                     WHEN nx.close > 0 THEN 'next_traded_close_after_horizon_qfq'
                     ELSE NULL
                   END AS exit_price_method,
                   CASE
                     WHEN COALESCE(x.date, nx.date) IS NULL THEN NULL
                     ELSE DATEDIFF('day', CAST(p.scheduled_exit_date AS DATE), CAST(COALESCE(x.date, nx.date) AS DATE))
                   END AS exit_delay_calendar_days
              FROM positioned p
              LEFT JOIN __mtm_kline e
                ON e.code = p.stock_code
               AND e.date = p.entry_date
              LEFT JOIN __mtm_kline x
                ON x.code = p.stock_code
               AND x.date = p.scheduled_exit_date
              LEFT JOIN LATERAL (
                  SELECT k.date, k.close
                    FROM __mtm_kline k
                   WHERE k.code = p.stock_code
                     AND CAST(k.date AS DATE) >= CAST(p.scheduled_exit_date AS DATE)
                   ORDER BY CAST(k.date AS DATE)
                   LIMIT 1
              ) nx ON TRUE
        )
        SELECT *,
               CASE
                 WHEN entry_amount IS NOT NULL AND entry_volume IS NOT NULL
                  AND entry_amount > 0 AND entry_volume > 0
                  AND entry_close > 0
                  AND (entry_amount / entry_volume) / entry_close BETWEEN 0.5 AND 1.5
                 THEN entry_amount / entry_volume
                 WHEN entry_amount IS NOT NULL AND entry_volume IS NOT NULL
                  AND entry_amount > 0 AND entry_volume > 0
                  AND entry_close > 0
                  AND ((entry_amount / entry_volume) / 100.0) / entry_close BETWEEN 0.5 AND 1.5
                 THEN (entry_amount / entry_volume) / 100.0
                 WHEN entry_open > 0
                 THEN entry_open
                 ELSE NULL
               END AS entry_price,
               CASE
                 WHEN entry_amount IS NOT NULL AND entry_volume IS NOT NULL
                  AND entry_amount > 0 AND entry_volume > 0
                  AND entry_close > 0
                  AND (entry_amount / entry_volume) / entry_close BETWEEN 0.5 AND 1.5
                 THEN 'signal_day_vwap_qfq'
                 WHEN entry_amount IS NOT NULL AND entry_volume IS NOT NULL
                  AND entry_amount > 0 AND entry_volume > 0
                  AND entry_close > 0
                  AND ((entry_amount / entry_volume) / 100.0) / entry_close BETWEEN 0.5 AND 1.5
                 THEN 'signal_day_vwap_qfq_volume_hand_adjusted'
                 WHEN entry_open > 0
                 THEN 'signal_day_vwap_qfq_fallback_open'
                 ELSE NULL
               END AS entry_price_method
          FROM entry_px
        """
    )
    counts = conn.execute(
        """
        SELECT COUNT(*) AS raw_positions,
               SUM(CASE WHEN entry_price IS NULL OR entry_price <= 0 THEN 1 ELSE 0 END) AS missing_entry,
               SUM(CASE WHEN exit_close IS NULL OR exit_close <= 0 THEN 1 ELSE 0 END) AS missing_exit
          FROM __mtm_positions_raw
        """
    ).fetchone()
    conn.execute(
        f"""
        CREATE OR REPLACE TEMP TABLE __mtm_positions AS
        SELECT position_id,
               stock_code,
               entry_date,
               scheduled_exit_date,
               executable_exit_date AS exit_date,
               signal_score,
               score_rank,
               label_value,
               entry_price,
               entry_price_method,
               exit_price_method,
               exit_delay_calendar_days,
               entry_close,
               exit_close,
               exit_close / NULLIF(entry_price, 0) - 1 AS gross_return,
               exit_close / NULLIF(entry_price, 0) - 1 - {cost_rate:.12f} AS net_return,
               COALESCE(kline_source_name, 'unknown') AS kline_source_name,
               COALESCE(kline_source_tier, 1) AS kline_source_tier,
               COALESCE(kline_is_fallback, FALSE) AS kline_is_fallback
          FROM __mtm_positions_raw
         WHERE entry_price > 0
           AND exit_close > 0
           AND executable_exit_date IS NOT NULL
        """
    )
    conn.execute("DELETE FROM mart_synergy_policy_mtm_position WHERE run_id = ?", (run_id,))
    conn.execute(
        """
        INSERT INTO mart_synergy_policy_mtm_position (
            run_id, position_id, candidate_run_id, source_run_id, label_name,
            stock_code, entry_date, scheduled_exit_date, exit_date, holding_days, signal_score,
            score_rank, label_value, entry_price, entry_price_method,
            exit_price_method, exit_delay_calendar_days,
            entry_close, exit_close, gross_return, net_return,
            transaction_cost_bps, kline_source_name, kline_source_tier,
            kline_is_fallback, built_at
        )
        SELECT ?, position_id, ?, ?, ?, stock_code, entry_date, scheduled_exit_date, exit_date,
               ?, signal_score, score_rank, label_value, entry_price,
               entry_price_method, exit_price_method, exit_delay_calendar_days,
               entry_close, exit_close, gross_return,
               net_return, ?, kline_source_name, kline_source_tier,
               kline_is_fallback, ?
          FROM __mtm_positions
        """,
        (
            run_id,
            candidate_run_id,
            source_run_id,
            label_name,
            int(horizon_days),
            float(transaction_cost_bps),
            built_at,
        ),
    )
    return {
        "raw_positions": int(counts["raw_positions"] or 0),
        "missing_entry_price_count": int(counts["missing_entry"] or 0),
        "missing_exit_price_count": int(counts["missing_exit"] or 0),
        "position_count": int(conn.execute("SELECT COUNT(*) AS n FROM __mtm_positions").fetchone()["n"] or 0),
    }


def _materialize_daily_path(
    conn: Any,
    *,
    run_id: str,
    transaction_cost_bps: float,
    built_at: str,
) -> dict[str, Any]:
    cost_rate = max(float(transaction_cost_bps), 0.0) / 10000.0
    half_cost = cost_rate / 2.0
    conn.execute(
        """
        CREATE OR REPLACE TEMP TABLE __mtm_expected_path AS
        SELECT p.position_id,
               p.stock_code,
               p.entry_date,
               p.exit_date,
               p.entry_price,
               c.trade_date AS date,
               c.trade_idx
          FROM __mtm_positions p
          JOIN __mtm_calendar c
            ON c.trade_date >= p.entry_date
           AND c.trade_date <= p.exit_date
        """
    )
    expected_path_rows = int(conn.execute("SELECT COUNT(*) AS n FROM __mtm_expected_path").fetchone()["n"] or 0)
    conn.execute(
        """
        CREATE OR REPLACE TEMP TABLE __mtm_path_with_price AS
        SELECT e.*,
               COALESCE(k.close, pk.close) AS close_price,
               CASE WHEN k.close > 0 THEN FALSE ELSE TRUE END AS forward_filled_price
          FROM __mtm_expected_path e
          LEFT JOIN __mtm_kline k
            ON k.code = e.stock_code
           AND k.date = e.date
          LEFT JOIN LATERAL (
              SELECT px.close
                FROM __mtm_kline px
               WHERE px.code = e.stock_code
                 AND CAST(px.date AS DATE) <= CAST(e.date AS DATE)
               ORDER BY CAST(px.date AS DATE) DESC
               LIMIT 1
          ) pk ON TRUE
        """
    )
    missing_path_price_count = int(
        conn.execute(
            """
            SELECT COUNT(*) AS n
              FROM __mtm_path_with_price
             WHERE close_price IS NULL OR close_price <= 0
            """
        ).fetchone()["n"]
        or 0
    )
    forward_filled_path_price_count = int(
        conn.execute(
            """
            SELECT COUNT(*) AS n
              FROM __mtm_path_with_price
             WHERE close_price > 0
               AND forward_filled_price
            """
        ).fetchone()["n"]
        or 0
    )
    conn.execute(
        """
        CREATE OR REPLACE TEMP TABLE __mtm_path_valid AS
        SELECT *
          FROM __mtm_path_with_price
         WHERE close_price > 0
        """
    )
    conn.execute(
        f"""
        CREATE OR REPLACE TEMP TABLE __mtm_position_daily_return AS
        WITH lagged AS (
            SELECT *,
                   LAG(close_price) OVER (
                       PARTITION BY position_id
                       ORDER BY trade_idx
                   ) AS prev_close
              FROM __mtm_path_valid
        ),
        returns AS (
            SELECT date,
                   position_id,
                   CASE
                     WHEN date = entry_date THEN close_price / NULLIF(entry_price, 0) - 1
                     WHEN prev_close > 0 THEN close_price / NULLIF(prev_close, 0) - 1
                     ELSE NULL
                   END AS daily_gross_return,
                   CASE WHEN date = entry_date THEN {half_cost:.12f} ELSE 0 END
                   + CASE WHEN date = exit_date THEN {half_cost:.12f} ELSE 0 END
                   AS daily_cost_rate
              FROM lagged
        )
        SELECT date,
               COUNT(*) AS active_position_count,
               AVG(daily_gross_return) AS daily_gross_return,
               AVG(daily_cost_rate) AS daily_cost_rate,
               AVG(daily_gross_return - daily_cost_rate) AS daily_net_return
          FROM returns
         WHERE daily_gross_return IS NOT NULL
         GROUP BY date
         ORDER BY CAST(date AS DATE)
        """
    )
    daily_rows = [dict(row) for row in conn.execute("SELECT * FROM __mtm_position_daily_return ORDER BY date").fetchall()]
    equity = 1.0
    peak = 1.0
    path_rows = []
    net_returns = []
    active_counts = []
    max_drawdown = 0.0
    for row in daily_rows:
        daily_net = _finite(row["daily_net_return"])
        equity *= 1.0 + daily_net
        peak = max(peak, equity)
        drawdown = equity / peak - 1.0 if peak > 0 else 0.0
        max_drawdown = min(max_drawdown, drawdown)
        net_returns.append(daily_net)
        active_counts.append(float(row["active_position_count"] or 0))
        path_rows.append(
            (
                run_id,
                row["date"],
                int(row["active_position_count"] or 0),
                _finite(row["daily_gross_return"]),
                _finite(row["daily_cost_rate"]),
                daily_net,
                equity,
                drawdown,
                built_at,
            )
        )
    conn.execute("DELETE FROM mart_synergy_policy_mtm_daily_path WHERE run_id = ?", (run_id,))
    if path_rows:
        conn.executemany(
            """
            INSERT INTO mart_synergy_policy_mtm_daily_path (
                run_id, date, active_position_count, daily_gross_return,
                daily_cost_rate, daily_net_return, equity, drawdown, built_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            path_rows,
        )
    avg_daily_return = sum(net_returns) / len(net_returns) if net_returns else 0.0
    volatility = _stddev(net_returns)
    sharpe = (avg_daily_return / volatility * math.sqrt(252.0)) if volatility > 0 else 0.0
    annualized = math.pow(max(equity, 1e-9), 252.0 / len(net_returns)) - 1.0 if net_returns else 0.0
    return {
        "expected_path_rows": expected_path_rows,
        "missing_path_price_count": missing_path_price_count,
        "forward_filled_path_price_count": forward_filled_path_price_count,
        "date_count": len(path_rows),
        "total_return": equity - 1.0,
        "annualized_return": annualized,
        "max_drawdown": max_drawdown,
        "avg_daily_return": avg_daily_return,
        "volatility": volatility,
        "sharpe": sharpe,
        "avg_active_positions": sum(active_counts) / len(active_counts) if active_counts else 0.0,
    }


def validate_synergy_policy_mark_to_market(
    conn: Any,
    *,
    candidate_run_id: str | None = None,
    run_id: str | None = None,
    top_quantile: float = 0.10,
    baseline_horizon_days: int = 60,
    min_positions: int = 100,
    min_active_days: int = 60,
    min_total_return: float = 0.0,
    max_drawdown: float = 0.25,
    transaction_cost_bps: float | None = None,
    conditional_threshold: float = 0.80,
    start_date: str | None = None,
    end_date: str | None = None,
    kline_relation: str | None = None,
    allow_kline_fallback: bool = False,
    progress: bool = True,
) -> dict[str, Any]:
    ensure_tables(conn)
    pricing_policy = load_pricing_label_policy()
    if transaction_cost_bps is None:
        transaction_cost_bps = pricing_policy.transaction_cost_bps
    record_pricing_label_policy(conn, pricing_policy)
    started_at = utc_now_iso()
    started = time.perf_counter()
    stage_timings: dict[str, float] = {}
    candidate_run_id = candidate_run_id or _latest_candidate_run_id(conn)
    if not candidate_run_id:
        raise RuntimeError("candidate_run_id is required and no synergy policy candidate exists")
    candidate = _load_candidate(conn, candidate_run_id)
    source_run_id = str(candidate["source_run_id"])
    label_name = str(candidate["label_name"])
    horizon_days = holding_period_from_label(label_name)
    run_id = run_id or f"synergy_policy_mtm_{candidate_run_id}_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}"
    built_at = datetime.now(UTC).replace(tzinfo=None).isoformat(timespec="seconds")

    _progress(progress, f"calendar_preflight start run_id={run_id}")
    stage_started = time.perf_counter()
    calendar_days = _require_calendar(conn)
    stage_timings["calendar_preflight_s"] = round(time.perf_counter() - stage_started, 3)
    _progress(progress, f"calendar_preflight done trading_days={len(calendar_days)}")

    if kline_relation is None:
        if not _attach_market(conn):
            raise RuntimeError(f"market.duckdb is required: {MARKET_DB}")
        kline_relation = PRIMARY_TDXHUB_KLINE_RELATION

    selected_features = [str(feature) for feature in candidate["selected_features"]]
    selected_interactions = [
        {
            "interaction_type": str(row.get("interaction_type") or "pair"),
            "feature_a": str(row.get("feature_a")),
            "feature_b": str(row.get("feature_b")),
        }
        for row in candidate["selected_interactions"]
        if row.get("feature_a") and row.get("feature_b")
    ]
    feature_directions = _load_feature_directions(
        conn,
        source_run_id=source_run_id,
        label_name=label_name,
        features=selected_features,
    )

    _progress(progress, "score_signals start")
    stage_started = time.perf_counter()
    _build_scored_signals(
        conn,
        source_run_id=source_run_id,
        label_name=label_name,
        selected_features=selected_features,
        selected_interactions=selected_interactions,
        feature_directions=feature_directions,
        start_date=start_date,
        end_date=end_date,
        conditional_threshold=conditional_threshold,
    )
    stage_timings["score_signals_s"] = round(time.perf_counter() - stage_started, 3)
    _progress(progress, "score_signals done")

    _progress(progress, "select_positions start")
    stage_started = time.perf_counter()
    threshold = 1.0 - max(min(float(top_quantile), 1.0), 0.001)
    selection_counts = _load_kept_signals(
        conn,
        threshold=threshold,
        horizon_days=horizon_days,
        calendar_days=calendar_days,
    )
    stage_timings["select_positions_s"] = round(time.perf_counter() - stage_started, 3)
    _progress(
        progress,
        "select_positions done "
        f"signals={selection_counts['signal_count']} kept={selection_counts['position_signal_count']} "
        f"repeated={selection_counts['repeated_signal_suppressed_count']}",
    )

    _progress(progress, "materialize_kline start")
    stage_started = time.perf_counter()
    kline_evidence = _materialize_kline(conn, kline_relation=kline_relation)
    stage_timings["materialize_kline_s"] = round(time.perf_counter() - stage_started, 3)
    _progress(progress, f"materialize_kline done rows={kline_evidence['kline_rows']}")

    _progress(progress, "materialize_positions start")
    stage_started = time.perf_counter()
    position_evidence = _materialize_positions(
        conn,
        run_id=run_id,
        candidate_run_id=candidate_run_id,
        source_run_id=source_run_id,
        label_name=label_name,
        horizon_days=horizon_days,
        transaction_cost_bps=float(transaction_cost_bps),
        built_at=built_at,
    )
    stage_timings["materialize_positions_s"] = round(time.perf_counter() - stage_started, 3)
    _progress(progress, f"materialize_positions done positions={position_evidence['position_count']}")

    _progress(progress, "daily_path start")
    stage_started = time.perf_counter()
    path_evidence = _materialize_daily_path(
        conn,
        run_id=run_id,
        transaction_cost_bps=float(transaction_cost_bps),
        built_at=built_at,
    )
    stage_timings["daily_path_s"] = round(time.perf_counter() - stage_started, 3)
    _progress(progress, f"daily_path done dates={path_evidence['date_count']}")

    position_summary = conn.execute(
        """
        SELECT AVG(net_return) AS avg_position_net_return,
               AVG(CASE WHEN net_return > 0 THEN 1.0 ELSE 0.0 END) AS position_hit_rate,
               SUM(CASE WHEN kline_is_fallback OR kline_source_tier > 1
                         OR (LOWER(kline_source_name) NOT LIKE '%tdxhub%'
                             AND kline_source_name <> 'unknown')
                        THEN 1 ELSE 0 END) AS non_tdxhub_kline_count
          FROM mart_synergy_policy_mtm_position
         WHERE run_id = ?
        """,
        (run_id,),
    ).fetchone()
    non_tdxhub_kline_count = int(position_summary["non_tdxhub_kline_count"] or 0)
    blockers = []
    if position_evidence["position_count"] < int(min_positions):
        blockers.append("insufficient_positions")
    if path_evidence["date_count"] < int(min_active_days):
        blockers.append("insufficient_active_days")
    if position_evidence["missing_entry_price_count"] > 0:
        blockers.append("missing_signal_day_entry_price")
    if position_evidence["missing_exit_price_count"] > 0:
        blockers.append("missing_horizon_exit_price")
    if path_evidence["missing_path_price_count"] > 0:
        blockers.append("missing_mark_to_market_path_price")
    if not allow_kline_fallback and kline_evidence["kline_lineage_available"] and non_tdxhub_kline_count > 0:
        blockers.append("non_tdxhub_kline_path")
    if path_evidence["total_return"] < float(min_total_return):
        blockers.append("low_total_return")
    if path_evidence["max_drawdown"] < -abs(float(max_drawdown)):
        blockers.append("excessive_mark_to_market_drawdown")

    validation_status = "pass" if not blockers else "blocked"
    production_eligible = bool(validation_status == "pass" and horizon_days == baseline_horizon_days)
    promotion_status = "production_candidate" if production_eligible else "research_only"
    if validation_status == "pass" and horizon_days != baseline_horizon_days:
        promotion_status = "research_only"
    thresholds = {
        "baseline_horizon_days": baseline_horizon_days,
        "candidate_horizon_days": horizon_days,
        "top_quantile": top_quantile,
        "min_positions": min_positions,
        "min_active_days": min_active_days,
        "min_total_return": min_total_return,
        "max_drawdown": max_drawdown,
        "transaction_cost_bps": float(transaction_cost_bps),
        "transaction_cost_path_mode": "round_trip_cost_split_half_entry_half_exit",
        "entry_price_mode": "signal_day_vwap_qfq",
        "exit_price_mode": "horizon_day_close_qfq_for_mtm_exit",
        "kline_relation": kline_relation,
        "allow_kline_fallback": allow_kline_fallback,
    }
    evidence = {
        **selection_counts,
        **kline_evidence,
        **position_evidence,
        **path_evidence,
        "non_tdxhub_kline_count": non_tdxhub_kline_count,
        "pricing_policy_hash": pricing_policy.policy_hash(),
    }
    gate = {
        "validation_status": validation_status,
        "promotion_status": promotion_status,
        "production_eligible": production_eligible,
        "blockers": blockers,
        "thresholds": thresholds,
        "evidence": evidence,
    }
    conn.execute("DELETE FROM mart_synergy_policy_mtm_gate WHERE run_id = ?", (run_id,))
    conn.execute(
        """
        INSERT OR REPLACE INTO mart_synergy_policy_mtm_gate (
            run_id, candidate_run_id, source_run_id, label_name,
            baseline_horizon_days, candidate_horizon_days, validation_status,
            promotion_status, production_eligible, signal_count,
            repeated_signal_suppressed_count, no_exit_date_count,
            position_count, date_count, expected_path_rows,
            missing_path_price_count, non_tdxhub_kline_count,
            total_return, annualized_return, max_drawdown, avg_daily_return,
            volatility, sharpe, avg_active_positions, avg_position_net_return,
            position_hit_rate, transaction_cost_bps, blockers_json,
            thresholds_json, evidence_json, built_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_id,
            candidate_run_id,
            source_run_id,
            label_name,
            int(baseline_horizon_days),
            int(horizon_days),
            validation_status,
            promotion_status,
            production_eligible,
            selection_counts["signal_count"],
            selection_counts["repeated_signal_suppressed_count"],
            selection_counts["no_exit_date_count"],
            position_evidence["position_count"],
            path_evidence["date_count"],
            path_evidence["expected_path_rows"],
            path_evidence["missing_path_price_count"],
            non_tdxhub_kline_count,
            path_evidence["total_return"],
            path_evidence["annualized_return"],
            path_evidence["max_drawdown"],
            path_evidence["avg_daily_return"],
            path_evidence["volatility"],
            path_evidence["sharpe"],
            path_evidence["avg_active_positions"],
            _finite(position_summary["avg_position_net_return"]),
            _finite(position_summary["position_hit_rate"]),
            float(transaction_cost_bps),
            _json(blockers),
            _json(thresholds),
            _json(evidence),
            built_at,
        ),
    )
    conn.execute("DELETE FROM mart_synergy_policy_mtm_evidence_bundle WHERE run_id = ?", (run_id,))
    conn.execute(
        """
        INSERT OR REPLACE INTO mart_synergy_policy_mtm_evidence_bundle (
            run_id, candidate_run_id, source_run_id, label_name,
            selected_features_json, selected_interactions_json,
            feature_directions_json, gate_json, stage_timings_json, built_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_id,
            candidate_run_id,
            source_run_id,
            label_name,
            _json(selected_features),
            _json(selected_interactions),
            _json(feature_directions),
            _json(gate),
            _json(stage_timings),
            built_at,
        ),
    )
    for table in (
        "mart_pricing_label_policy",
        "mart_synergy_policy_mtm_position",
        "mart_synergy_policy_mtm_daily_path",
        "mart_synergy_policy_mtm_gate",
        "mart_synergy_policy_mtm_evidence_bundle",
    ):
        record_actual_version(conn, table)
    duration_s = time.perf_counter() - started
    stage_timings["total_s"] = round(duration_s, 3)
    record_pipeline_run(
        conn,
        run_id=run_id,
        pipeline_name="validate_synergy_policy_mark_to_market",
        status="success",
        started_at=started_at,
        ended_at=utc_now_iso(),
        duration_s=duration_s,
        commit_sha=git_commit_sha(REPO),
        input_tables=[
            "mart_synergy_policy_candidate",
            "mart_temporal_research_panel",
            "mart_feature_temporal_relevance",
            kline_relation,
            "dim_trading_calendar",
        ],
        output_tables=[
            "mart_synergy_policy_mtm_position",
            "mart_synergy_policy_mtm_daily_path",
            "mart_synergy_policy_mtm_gate",
            "mart_synergy_policy_mtm_evidence_bundle",
        ],
        label_name=label_name,
        holding_period=horizon_days,
        gate_result=validation_status,
        blockers=blockers,
        perf_summary={
            "candidate_run_id": candidate_run_id,
            "source_run_id": source_run_id,
            "stage_timings": stage_timings,
            **evidence,
            "promotion_status": promotion_status,
            "production_eligible": production_eligible,
            "thresholds": thresholds,
        },
    )
    conn.commit()
    return {
        "run_id": run_id,
        "candidate_run_id": candidate_run_id,
        "source_run_id": source_run_id,
        "label_name": label_name,
        "validation_status": validation_status,
        "promotion_status": promotion_status,
        "production_eligible": production_eligible,
        "blockers": blockers,
        **evidence,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate-run-id", default=None)
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--top-quantile", type=float, default=0.10)
    parser.add_argument("--baseline-horizon-days", type=int, default=60)
    parser.add_argument("--min-positions", type=int, default=100)
    parser.add_argument("--min-active-days", type=int, default=60)
    parser.add_argument("--min-total-return", type=float, default=0.0)
    parser.add_argument("--max-drawdown", type=float, default=0.25)
    parser.add_argument("--transaction-cost-bps", type=float, default=None)
    parser.add_argument("--conditional-threshold", type=float, default=0.80)
    parser.add_argument("--start-date", default=None)
    parser.add_argument("--end-date", default=None)
    parser.add_argument("--kline-relation", default=None)
    parser.add_argument("--allow-kline-fallback", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()
    with get_conn() as conn:
        result = validate_synergy_policy_mark_to_market(
            conn,
            candidate_run_id=args.candidate_run_id,
            run_id=args.run_id,
            top_quantile=args.top_quantile,
            baseline_horizon_days=args.baseline_horizon_days,
            min_positions=args.min_positions,
            min_active_days=args.min_active_days,
            min_total_return=args.min_total_return,
            max_drawdown=args.max_drawdown,
            transaction_cost_bps=args.transaction_cost_bps,
            conditional_threshold=args.conditional_threshold,
            start_date=args.start_date,
            end_date=args.end_date,
            kline_relation=args.kline_relation,
            allow_kline_fallback=args.allow_kline_fallback,
            progress=not args.quiet,
        )
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
