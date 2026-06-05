#!/usr/bin/env python3
"""Stage-opt candidate supply audit.

This diagnostic reports how many (stock × formula × stage) keys survive the
pre-Optuna supply chain used by ``optimize_per_stock_stage_strategy.py``.
It is evidence for upstream candidate supply / formula coverage, not a strategy
change.

Usage:
    PYTHONPATH=backend python backend/scripts/audit_stage_opt_candidate_supply.py
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.calendar import DEFAULT_CLOSE_HOUR, DEFAULT_CLOSE_MINUTE
from services.backtest.filters import is_index_code
from services.duck_adapter import attach_with_retry, connect as duck_connect
import services.formula_engine.bootstrap  # noqa: F401
from services.formula_engine import REGISTRY
from services.formula_engine.bootstrap import LIVE_FORMULA_IDS
from services.formula_engine.bc_absorbed_challengers import BANK_CHALLENGER_REGISTRY, BANK_EXTENSION_REGISTRY
from services.stage_opt_candidate_supply import DEFAULT_STAGE_OPT_CANDIDATE_SUPPLY_CONTRACT


MARKET_DB = Path(__file__).resolve().parents[2] / "data" / "market.duckdb"
SMART_DB = Path(__file__).resolve().parents[2] / "data" / "smartmoney.duckdb"
CONSUMER_ID = "audit_stage_opt_candidate_supply"
SUPPLY_CONTRACT = DEFAULT_STAGE_OPT_CANDIDATE_SUPPLY_CONTRACT
ALLOWED_STAGES = SUPPLY_CONTRACT.allowed_stage_set
RESEARCH_FORMULA_IDS = SUPPLY_CONTRACT.formula_ids_for_scope("research_challenger")
TRIGGER_SOURCE = SUPPLY_CONTRACT.source("fact_technical_trigger")
MACD_STATE_SOURCE = SUPPLY_CONTRACT.source("mart_macd_state_history")
TRIGGER_SOURCE.require_consumer(CONSUMER_ID)
MACD_STATE_SOURCE.require_consumer(CONSUMER_ID)

log = logging.getLogger("audit_stage_opt_candidate_supply")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


def _latest_signal_date(conn) -> str | None:
    row = conn.execute("SELECT MAX(date) FROM sm.fact_technical_trigger").fetchone()
    return str(row[0]) if row and row[0] is not None else None


def _live_formula_registry_summary() -> dict[str, Any]:
    formula_ids = list(LIVE_FORMULA_IDS)
    return {
        "formula_count": len(formula_ids),
        "formula_ids": formula_ids,
    }


def _research_formula_registry_summary() -> dict[str, Any]:
    # Challenger surface stays visible as a separate catalog for audit narration.
    # The live registry also carries thin adapters for these formulas so the shared
    # signal pipeline can populate them without duplicating the signal logic.
    formula_ids = list(RESEARCH_FORMULA_IDS)
    return {
        "formula_count": len(formula_ids),
        "formula_ids": formula_ids,
    }


def _formula_family(formula_id: str) -> str:
    """Return a stable coarse family for audit grouping, not business routing."""
    if formula_id in BANK_CHALLENGER_REGISTRY:
        return "bc_absorbed_challenger"
    if formula_id in BANK_EXTENSION_REGISTRY:
        return "bc_absorbed_extension"
    if formula_id.startswith("dynamic_ma_"):
        return "dynamic_ma"
    if formula_id.startswith("multi_tf_"):
        return "multi_tf"
    return formula_id.split("_", 1)[0] or "unknown"


def _formula_registry_scopes(formula_id: str) -> list[str]:
    return SUPPLY_CONTRACT.formula_scopes(
        formula_id,
        live_formula_ids=tuple(LIVE_FORMULA_IDS),
        registered_formula_ids=tuple(REGISTRY.keys()),
    )


def _formula_registry_scope_label(formula_id: str) -> str:
    return "+".join(_formula_registry_scopes(formula_id))


def _reason_counts_dict(reason_counts: Counter[str]) -> dict[str, int]:
    return {reason: int(count) for reason, count in sorted(reason_counts.items())}


def _top_reason(reason_counts: Counter[str]) -> str | None:
    if not reason_counts:
        return None
    return sorted(reason_counts.items(), key=lambda item: (-int(item[1]), item[0]))[0][0]


def _load_signal_rows(
    conn: Any,
    *,
    start: str,
    end: str,
    formula: list[str] | None = None,
    stock_codes: list[str] | None = None,
) -> dict[str, Any]:
    formula_filter_sql = ""
    formula_filter_params: list[str] = []
    if formula:
        placeholders = ",".join(["?"] * len(formula))
        formula_filter_sql = f" AND t.formula_id IN ({placeholders})"
        formula_filter_params = list(formula)

    stock_filter_sql = ""
    stock_filter_params: list[str] = []
    if stock_codes:
        stock_codes = sorted({str(code).strip() for code in stock_codes if str(code).strip()})
        placeholders = ",".join(["?"] * len(stock_codes))
        stock_filter_sql = f" AND t.stock_code IN ({placeholders})"
        stock_filter_params = stock_codes

    trigger_rows = conn.execute(
        f"""
        SELECT t.stock_code, t.date, t.formula_id, t.formula_variant,
               COALESCE(c.technical_stage, '?') AS stage_bin
          FROM {TRIGGER_SOURCE.table} t
          LEFT JOIN sm.fact_signal_context c
            ON c.stock_code = t.stock_code AND c.date = t.date
         WHERE t.date >= ? AND t.date <= ?
           {formula_filter_sql}
           {stock_filter_sql}
         ORDER BY t.stock_code, t.formula_variant, t.date
        """,
        [start, end] + formula_filter_params + stock_filter_params,
    ).fetchall()

    state_history_rows: list[tuple[str, str, str, str, str]] = []
    source_load_errors: list[dict[str, str]] = []
    include_macd_state_history = MACD_STATE_SOURCE.include_for_formula_filter(formula)
    if include_macd_state_history:
        try:
            state_formula_sql = ""
            state_formula_params: list[str] = []
            if formula:
                state_placeholders = ",".join(["?"] * len(formula))
                state_formula_sql = f" AND s.formula_id IN ({state_placeholders})"
                state_formula_params = list(formula)

            state_stock_sql = ""
            state_stock_params: list[str] = []
            if stock_codes:
                state_placeholders = ",".join(["?"] * len(stock_codes))
                state_stock_sql = f" AND s.stock_code IN ({state_placeholders})"
                state_stock_params = list(stock_codes)

            state_history_rows = conn.execute(
                f"""
                SELECT s.stock_code, s.date, s.formula_id, s.formula_variant,
                       COALESCE(c.technical_stage, '?') AS stage_bin
                  FROM {MACD_STATE_SOURCE.table} s
                  LEFT JOIN sm.fact_signal_context c
                    ON c.stock_code = s.stock_code AND c.date = s.date
                 WHERE s.date >= ? AND s.date <= ?
                   {state_formula_sql}
                   {state_stock_sql}
                """,
                [start, end] + state_formula_params + state_stock_params,
            ).fetchall()
        except Exception as exc:
            state_history_rows = []
            source_load_errors.append(
                {
                    "source_id": MACD_STATE_SOURCE.source_id,
                    "table": MACD_STATE_SOURCE.table,
                    "error_type": type(exc).__name__,
                    "error": str(exc)[:500],
                }
            )

    rows = trigger_rows + state_history_rows

    raw_trigger_rows = len(trigger_rows)
    raw_state_history_rows = len(state_history_rows)
    raw_rows = len(rows)
    raw_trigger_rows_by_stock_code = Counter(str(row[0]) for row in trigger_rows)
    raw_state_history_rows_by_stock_code = Counter(str(row[0]) for row in state_history_rows)
    raw_rows_by_stock_code = raw_trigger_rows_by_stock_code + raw_state_history_rows_by_stock_code
    dropped_index_rows = 0
    dropped_index_rows_by_stock_code: Counter[str] = Counter()
    dropped_unknown_stage_rows = 0
    dropped_unknown_stage_rows_by_formula_id: Counter[str] = Counter()
    dropped_unknown_stage_rows_by_formula_variant: Counter[str] = Counter()
    dropped_unknown_stage_rows_by_stock_code: Counter[str] = Counter()
    dropped_unknown_stage_rows_by_stock_formula_id: Counter[tuple[str, str]] = Counter()
    dropped_unknown_stage_rows_by_stock_formula_variant: Counter[tuple[str, str]] = Counter()
    dropped_unknown_stage_examples: list[dict[str, Any]] = []
    signal_rows: list[dict[str, Any]] = []
    for stock_code, signal_date, formula_id, formula_variant, stage_bin in rows:
        stock_code = str(stock_code)
        formula_id = str(formula_id)
        formula_variant = str(formula_variant)
        if is_index_code(stock_code):
            dropped_index_rows += 1
            dropped_index_rows_by_stock_code[stock_code] += 1
            continue
        stage_bin = str(stage_bin or "?")
        if stage_bin not in ALLOWED_STAGES:
            dropped_unknown_stage_rows += 1
            dropped_unknown_stage_rows_by_formula_id[formula_id] += 1
            dropped_unknown_stage_rows_by_formula_variant[formula_variant] += 1
            dropped_unknown_stage_rows_by_stock_code[stock_code] += 1
            dropped_unknown_stage_rows_by_stock_formula_id[(stock_code, formula_id)] += 1
            dropped_unknown_stage_rows_by_stock_formula_variant[(stock_code, formula_variant)] += 1
            if len(dropped_unknown_stage_examples) < 8:
                dropped_unknown_stage_examples.append(
                    {
                        "stock_code": stock_code,
                        "signal_date": str(signal_date),
                        "formula_id": formula_id,
                        "formula_variant": formula_variant,
                        "stage_bin": stage_bin,
                    }
                )
            continue
        signal_rows.append(
            {
                "stock_code": stock_code,
                "signal_date": str(signal_date),
                "formula_id": formula_id,
                "formula_variant": formula_variant,
                "stage_bin": stage_bin,
            }
        )

    return {
        "raw_rows": raw_rows,
        "raw_trigger_rows": raw_trigger_rows,
        "raw_state_history_rows": raw_state_history_rows,
        "source_load_errors": source_load_errors,
        "_raw_rows_by_stock_code": dict(sorted(raw_rows_by_stock_code.items())),
        "_raw_trigger_rows_by_stock_code": dict(sorted(raw_trigger_rows_by_stock_code.items())),
        "_raw_state_history_rows_by_stock_code": dict(sorted(raw_state_history_rows_by_stock_code.items())),
        "dropped_index_rows": dropped_index_rows,
        "_dropped_index_rows_by_stock_code": dict(sorted(dropped_index_rows_by_stock_code.items())),
        "dropped_unknown_stage_rows": dropped_unknown_stage_rows,
        "dropped_unknown_stage_rows_by_formula_id": dict(sorted(dropped_unknown_stage_rows_by_formula_id.items())),
        "dropped_unknown_stage_rows_by_formula_variant": dict(sorted(dropped_unknown_stage_rows_by_formula_variant.items())),
        "dropped_unknown_stage_examples": dropped_unknown_stage_examples,
        "_dropped_unknown_stage_rows_by_stock_code": dict(sorted(dropped_unknown_stage_rows_by_stock_code.items())),
        "_dropped_unknown_stage_rows_by_stock_formula_id": dict(dropped_unknown_stage_rows_by_stock_formula_id),
        "_dropped_unknown_stage_rows_by_stock_formula_variant": dict(dropped_unknown_stage_rows_by_stock_formula_variant),
        "signal_rows": signal_rows,
    }


def _filter_load_result_for_stock_codes(load_result: dict[str, Any], stock_codes: set[str]) -> dict[str, Any]:
    stock_codes = {str(code) for code in stock_codes}
    raw_by_stock = load_result.get("_raw_rows_by_stock_code") or {}
    raw_trigger_by_stock = load_result.get("_raw_trigger_rows_by_stock_code") or {}
    raw_state_by_stock = load_result.get("_raw_state_history_rows_by_stock_code") or {}
    index_by_stock = load_result.get("_dropped_index_rows_by_stock_code") or {}
    by_stock = load_result.get("_dropped_unknown_stage_rows_by_stock_code") or {}
    by_stock_formula = load_result.get("_dropped_unknown_stage_rows_by_stock_formula_id") or {}
    by_stock_variant = load_result.get("_dropped_unknown_stage_rows_by_stock_formula_variant") or {}

    formula_counts: Counter[str] = Counter()
    for (stock_code, formula_id), count in by_stock_formula.items():
        if str(stock_code) in stock_codes:
            formula_counts[str(formula_id)] += int(count)

    variant_counts: Counter[str] = Counter()
    for (stock_code, formula_variant), count in by_stock_variant.items():
        if str(stock_code) in stock_codes:
            variant_counts[str(formula_variant)] += int(count)

    examples = [
        row
        for row in load_result.get("dropped_unknown_stage_examples", [])
        if str(row.get("stock_code")) in stock_codes
    ][:8]
    return {
        **load_result,
        "raw_rows": sum(int(count) for code, count in raw_by_stock.items() if str(code) in stock_codes),
        "raw_trigger_rows": sum(int(count) for code, count in raw_trigger_by_stock.items() if str(code) in stock_codes),
        "raw_state_history_rows": sum(int(count) for code, count in raw_state_by_stock.items() if str(code) in stock_codes),
        "dropped_index_rows": sum(int(count) for code, count in index_by_stock.items() if str(code) in stock_codes),
        "dropped_unknown_stage_rows": sum(int(count) for code, count in by_stock.items() if str(code) in stock_codes),
        "dropped_unknown_stage_rows_by_formula_id": dict(sorted(formula_counts.items())),
        "dropped_unknown_stage_rows_by_formula_variant": dict(sorted(variant_counts.items())),
        "dropped_unknown_stage_examples": examples,
        "signal_rows": [
            row
            for row in load_result.get("signal_rows", [])
            if str(row.get("stock_code")) in stock_codes
        ],
    }


def _limit_candidate_stock_codes(load_result: dict[str, Any], signal_rows: list[dict[str, Any]]) -> list[str]:
    """Select limited audit stocks from raw non-index rows, not only rows that survived stage filtering."""
    codes = {str(row["stock_code"]) for row in signal_rows}
    raw_by_stock = load_result.get("_raw_rows_by_stock_code") or {}
    index_by_stock = load_result.get("_dropped_index_rows_by_stock_code") or {}
    for stock_code, raw_count in raw_by_stock.items():
        code = str(stock_code)
        if int(raw_count) > int(index_by_stock.get(stock_code, index_by_stock.get(code, 0))):
            codes.add(code)
    return sorted(codes)


def _load_kline_codes(
    conn: Any,
    *,
    codes: list[str],
    start: str,
    end: str,
) -> set[str]:
    if not codes:
        return set()
    placeholders = ",".join(["?"] * len(codes))
    rows = conn.execute(
        f"""
        SELECT DISTINCT code
          FROM v_price_kline_qfq
         WHERE freq='daily' AND adjust='qfq'
           AND code IN ({placeholders})
           AND date >= ? AND date <= ?
        """,
        codes + [start, end],
    ).fetchall()
    return {str(r[0]) for r in rows if r and r[0] is not None}


def _latest_closed_trade_date(conn: Any) -> str | None:
    """Reuse the current connection's calendar truth source for default end date."""
    now_local = datetime.now(ZoneInfo("Asia/Shanghai"))
    anchor_date = now_local.date()
    if (now_local.hour, now_local.minute) < (DEFAULT_CLOSE_HOUR, DEFAULT_CLOSE_MINUTE):
        anchor_date -= timedelta(days=1)
    anchor = anchor_date.strftime("%Y-%m-%d")

    try:
        row = conn.execute(
            "SELECT MAX(trade_date) AS d FROM sm.dim_trading_calendar "
            "WHERE is_trading=1 AND trade_date <= ?",
            (anchor,),
        ).fetchone()
    except Exception:
        row = None
    if row and row[0] is not None:
        return str(row[0])

    try:
        row = conn.execute(
            "SELECT MAX(trade_date) AS d FROM dim_trading_calendar "
            "WHERE is_trading=1 AND trade_date <= ?",
            (anchor,),
        ).fetchone()
    except Exception:
        row = None
    if row and row[0] is not None:
        return str(row[0])

    try:
        row = conn.execute(
            "SELECT MAX(date) AS d FROM sm.dim_trading_calendar "
            "WHERE is_trading=1 AND date <= ?",
            (anchor,),
        ).fetchone()
    except Exception:
        row = None
    if row and row[0] is not None:
        return str(row[0])

    try:
        row = conn.execute(
            "SELECT MAX(date) AS d FROM dim_trading_calendar "
            "WHERE is_trading=1 AND date <= ?",
            (anchor,),
        ).fetchone()
    except Exception:
        row = None
    if row and row[0] is not None:
        return str(row[0])
    return None


def summarize_stage_opt_candidate_supply(
    signal_rows: list[dict[str, Any]],
    codes_with_bars: set[str],
    *,
    min_signals: int = 5,
    max_examples: int = 8,
    dropped_unknown_stage_rows_by_formula_id: dict[str, int] | None = None,
    dropped_unknown_stage_rows_by_formula_variant: dict[str, int] | None = None,
    dropped_unknown_stage_examples: list[dict[str, Any]] | None = None,
    include_attrition_detail: bool = True,
) -> dict[str, Any]:
    """Summarize how much stage-opt candidate supply survives the pre-Optuna gates."""
    key_rows: dict[tuple[str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    rows_by_formula_id = Counter()
    rows_by_formula_variant = Counter()
    rows_by_formula_family = Counter()
    rows_by_stage = Counter()
    rows_by_stage_formula = Counter()
    rows_by_registry_family = Counter()
    key_counts_by_formula_id = Counter()
    key_counts_by_formula_variant = Counter()
    key_counts_by_formula_family = Counter()
    key_counts_by_stage = Counter()
    key_counts_by_stage_formula = Counter()
    key_counts_by_registry_family = Counter()
    ready_keys_by_formula_id = Counter()
    ready_keys_by_formula_variant = Counter()
    ready_keys_by_formula_family = Counter()
    ready_keys_by_stage = Counter()
    ready_keys_by_stage_formula = Counter()
    ready_keys_by_registry_family = Counter()
    blocked_reason_counts = Counter()
    blocked_reason_counts_by_formula_id: defaultdict[str, Counter[str]] = defaultdict(Counter)
    blocked_reason_counts_by_formula_variant: defaultdict[str, Counter[str]] = defaultdict(Counter)
    blocked_reason_counts_by_formula_family: defaultdict[str, Counter[str]] = defaultdict(Counter)
    blocked_reason_counts_by_stage = defaultdict(Counter)
    blocked_reason_counts_by_stage_formula: defaultdict[tuple[str, str], Counter[str]] = defaultdict(Counter)
    blocked_reason_counts_by_registry_family: defaultdict[tuple[str, str], Counter[str]] = defaultdict(Counter)
    blocked_examples: list[dict[str, Any]] = []

    for row in signal_rows:
        formula_id = row["formula_id"]
        stage_bin = row["stage_bin"]
        formula_family = _formula_family(formula_id)
        registry_scope = _formula_registry_scope_label(formula_id)
        key = (
            row["stock_code"],
            formula_id,
            row["formula_variant"],
            stage_bin,
        )
        key_rows[key].append(row)
        rows_by_formula_id[formula_id] += 1
        rows_by_formula_variant[row["formula_variant"]] += 1
        rows_by_formula_family[formula_family] += 1
        rows_by_stage[stage_bin] += 1
        rows_by_stage_formula[(stage_bin, formula_id)] += 1
        rows_by_registry_family[(registry_scope, formula_family)] += 1

    for (stock_code, formula_id, formula_variant, stage_bin), rows in key_rows.items():
        n_rows = len(rows)
        has_bars = stock_code in codes_with_bars
        formula_family = _formula_family(formula_id)
        registry_scope = _formula_registry_scope_label(formula_id)
        stage_formula_key = (stage_bin, formula_id)
        registry_family_key = (registry_scope, formula_family)
        key_counts_by_formula_id[formula_id] += 1
        key_counts_by_formula_variant[formula_variant] += 1
        key_counts_by_formula_family[formula_family] += 1
        key_counts_by_stage[stage_bin] += 1
        key_counts_by_stage_formula[stage_formula_key] += 1
        key_counts_by_registry_family[registry_family_key] += 1

        blocked_reasons: list[str] = []
        if n_rows < min_signals:
            blocked_reasons.append("below_min_signals")
            blocked_reason_counts["below_min_signals"] += 1
            blocked_reason_counts_by_formula_id[formula_id]["below_min_signals"] += 1
            blocked_reason_counts_by_formula_variant[formula_variant]["below_min_signals"] += 1
            blocked_reason_counts_by_formula_family[formula_family]["below_min_signals"] += 1
            blocked_reason_counts_by_stage[stage_bin]["below_min_signals"] += 1
            blocked_reason_counts_by_stage_formula[stage_formula_key]["below_min_signals"] += 1
            blocked_reason_counts_by_registry_family[registry_family_key]["below_min_signals"] += 1
        if not has_bars:
            blocked_reasons.append("no_kline_bars")
            blocked_reason_counts["no_kline_bars"] += 1
            blocked_reason_counts_by_formula_id[formula_id]["no_kline_bars"] += 1
            blocked_reason_counts_by_formula_variant[formula_variant]["no_kline_bars"] += 1
            blocked_reason_counts_by_formula_family[formula_family]["no_kline_bars"] += 1
            blocked_reason_counts_by_stage[stage_bin]["no_kline_bars"] += 1
            blocked_reason_counts_by_stage_formula[stage_formula_key]["no_kline_bars"] += 1
            blocked_reason_counts_by_registry_family[registry_family_key]["no_kline_bars"] += 1

        if blocked_reasons:
            if len(blocked_examples) < max_examples:
                blocked_examples.append(
                    {
                        "stock_code": stock_code,
                        "formula_id": formula_id,
                        "formula_variant": formula_variant,
                        "stage_bin": stage_bin,
                        "signal_rows": n_rows,
                        "has_bars": has_bars,
                        "blocked_reasons": blocked_reasons,
                    }
                )
            continue

        ready_keys_by_formula_id[formula_id] += 1
        ready_keys_by_formula_variant[formula_variant] += 1
        ready_keys_by_formula_family[formula_family] += 1
        ready_keys_by_stage[stage_bin] += 1
        ready_keys_by_stage_formula[stage_formula_key] += 1
        ready_keys_by_registry_family[registry_family_key] += 1

    ready_key_count = sum(ready_keys_by_formula_id.values())  # same as len(ready_keys)
    total_key_count = len(key_rows)

    def _coverage(ready: int, total: int) -> float:
        return round(100.0 * ready / total, 2) if total else 0.0

    formula_id_rows = []
    formula_attrition_rows = []
    sorted_formula_ids = sorted(key_counts_by_formula_id)
    for formula_id in sorted_formula_ids:
        keys_total = key_counts_by_formula_id[formula_id]
        keys_ready = ready_keys_by_formula_id[formula_id]
        keys_blocked = keys_total - keys_ready
        formula_reason_counts = blocked_reason_counts_by_formula_id[formula_id]
        formula_id_rows.append(
            {
                "formula_id": formula_id,
                "keys_total": keys_total,
                "keys_ready": keys_ready,
                "keys_blocked": keys_blocked,
                "ready_coverage_pct": _coverage(keys_ready, keys_total),
                "blocked_pct": _coverage(keys_blocked, keys_total),
                "signal_rows": rows_by_formula_id[formula_id],
                "formula_family": _formula_family(formula_id),
                "registry_scopes": _formula_registry_scopes(formula_id),
                "blocked_reason_counts": _reason_counts_dict(formula_reason_counts),
                "top_blocked_reason": _top_reason(formula_reason_counts),
            }
        )
        formula_attrition_rows.append(formula_id_rows[-1])
    weakest_formula_id_rows = sorted(
        formula_id_rows,
        key=lambda row: (row["ready_coverage_pct"], -row["keys_total"], row["formula_id"]),
    )

    formula_variant_rows = []
    sorted_formula_variants = sorted(key_counts_by_formula_variant)
    for formula_variant in sorted_formula_variants:
        formula_variant_rows.append(
            {
                "formula_variant": formula_variant,
                "keys_total": key_counts_by_formula_variant[formula_variant],
                "keys_ready": ready_keys_by_formula_variant[formula_variant],
                "ready_coverage_pct": _coverage(
                    ready_keys_by_formula_variant[formula_variant], key_counts_by_formula_variant[formula_variant]
                ),
                "signal_rows": rows_by_formula_variant[formula_variant],
            }
        )
    weakest_formula_variant_rows = sorted(
        formula_variant_rows,
        key=lambda row: (row["ready_coverage_pct"], -row["keys_total"], row["formula_variant"]),
    )

    stage_rows = []
    sorted_stage_bins = sorted(key_counts_by_stage)
    for stage_bin in sorted_stage_bins:
        keys_total = key_counts_by_stage[stage_bin]
        keys_ready = ready_keys_by_stage[stage_bin]
        keys_blocked = keys_total - keys_ready
        stage_rows.append(
            {
                "stage_bin": stage_bin,
                "keys_total": keys_total,
                "keys_ready": keys_ready,
                "keys_blocked": keys_blocked,
                "ready_coverage_pct": _coverage(keys_ready, keys_total),
                "blocked_pct": _coverage(keys_blocked, keys_total),
                "signal_rows": rows_by_stage[stage_bin],
                "blocked_reason_counts": _reason_counts_dict(blocked_reason_counts_by_stage[stage_bin]),
                "top_blocked_reason": _top_reason(blocked_reason_counts_by_stage[stage_bin]),
            }
        )
    weakest_stage_rows = sorted(
        stage_rows,
        key=lambda row: (row["ready_coverage_pct"], -row["keys_total"], row["stage_bin"]),
    )

    formula_family_rows = []
    for formula_family in sorted(key_counts_by_formula_family):
        keys_total = key_counts_by_formula_family[formula_family]
        keys_ready = ready_keys_by_formula_family[formula_family]
        keys_blocked = keys_total - keys_ready
        reason_counts = blocked_reason_counts_by_formula_family[formula_family]
        formula_family_rows.append(
            {
                "formula_family": formula_family,
                "keys_total": keys_total,
                "keys_ready": keys_ready,
                "keys_blocked": keys_blocked,
                "ready_coverage_pct": _coverage(keys_ready, keys_total),
                "blocked_pct": _coverage(keys_blocked, keys_total),
                "signal_rows": rows_by_formula_family[formula_family],
                "blocked_reason_counts": _reason_counts_dict(reason_counts),
                "top_blocked_reason": _top_reason(reason_counts),
            }
        )

    def _recommend_next_action() -> dict[str, Any]:
        below_min_signals = blocked_reason_counts.get("below_min_signals", 0)
        no_kline_bars = blocked_reason_counts.get("no_kline_bars", 0)
        weakest_formula_ids = [row["formula_id"] for row in weakest_formula_id_rows[:3]]
        weakest_stage_bins = [row["stage_bin"] for row in weakest_stage_rows[:3]]
        if below_min_signals == 0 and no_kline_bars == 0:
            return {
                "priority": "P2",
                "focus": "candidate_supply_monitoring",
                "reason": "no blocking reasons detected in current slice",
                "recommended_lever": "keep monitoring upstream supply and PIT coverage",
                "weakest_formula_ids": weakest_formula_ids,
                "weakest_stage_bins": weakest_stage_bins,
                "top_blocked_reason": None,
            }
        if below_min_signals >= no_kline_bars:
            recommendation = {
                "priority": "P1",
                "focus": "upstream_candidate_supply",
                "reason": "below_min_signals dominates current blocked keys",
                "recommended_lever": "expand upstream formula coverage or signal density before tuning profile knobs",
                "weakest_formula_ids": weakest_formula_ids,
                "weakest_stage_bins": weakest_stage_bins,
                "top_blocked_reason": "below_min_signals",
            }
            structural_notes: list[str] = []
            if "macd_golden_cross" in weakest_formula_ids:
                structural_notes.append(
                    "macd_golden_cross is capped by fact_technical_trigger PRIMARY KEY "
                    "(stock_code, date, formula_id); extra MACD state rows need schema evolution, "
                    "not a state-only formula tweak"
                )
            if structural_notes:
                recommendation["structural_notes"] = structural_notes
            return recommendation
        return {
            "priority": "P1",
            "focus": "kline_coverage",
            "reason": "no_kline_bars dominates current blocked keys",
            "recommended_lever": "repair missing bars or date coverage before re-running candidate supply",
            "weakest_formula_ids": weakest_formula_ids,
            "weakest_stage_bins": weakest_stage_bins,
            "top_blocked_reason": "no_kline_bars",
        }

    sorted_key_rows = sorted(
        key_rows.items(),
        key=lambda item: (-len(item[1]), item[0][0], item[0][1], item[0][2], item[0][3]),
    )
    top_blocked_keys = []
    for (stock_code, formula_id, formula_variant, stage_bin), rows in sorted_key_rows:
        has_bars = stock_code in codes_with_bars
        n_rows = len(rows)
        reasons: list[str] = []
        if n_rows < min_signals:
            reasons.append("below_min_signals")
        if not has_bars:
            reasons.append("no_kline_bars")
        if reasons:
            top_blocked_keys.append(
                {
                    "stock_code": stock_code,
                    "formula_id": formula_id,
                    "formula_variant": formula_variant,
                    "stage_bin": stage_bin,
                    "signal_rows": n_rows,
                    "has_bars": has_bars,
                    "blocked_reasons": reasons,
                }
            )
        if len(top_blocked_keys) >= max_examples:
            break

    result = {
        "raw_signal_rows": len(signal_rows),
        "unique_keys": total_key_count,
        "ready_keys": ready_key_count,
        "ready_coverage_pct": _coverage(ready_key_count, total_key_count),
        "blocked_reason_counts": dict(blocked_reason_counts),
        "blocked_reason_counts_by_formula_id": {
            formula_id: dict(sorted(reason_counts.items()))
            for formula_id, reason_counts in sorted(blocked_reason_counts_by_formula_id.items())
        },
        "blocked_reason_counts_by_formula_variant": {
            formula_variant: dict(sorted(reason_counts.items()))
            for formula_variant, reason_counts in sorted(blocked_reason_counts_by_formula_variant.items())
        },
        "blocked_reason_counts_by_formula_family": {
            formula_family: _reason_counts_dict(reason_counts)
            for formula_family, reason_counts in sorted(blocked_reason_counts_by_formula_family.items())
        },
        "blocked_reason_counts_by_stage_bin": {
            stage_bin: _reason_counts_dict(reason_counts)
            for stage_bin, reason_counts in sorted(blocked_reason_counts_by_stage.items())
        },
        "blocked_reason_counts_by_stage_formula": {
            f"{stage_bin}|{formula_id}": _reason_counts_dict(reason_counts)
            for (stage_bin, formula_id), reason_counts in sorted(blocked_reason_counts_by_stage_formula.items())
        },
        "blocked_reason_counts_by_registry_family": {
            f"{registry_scope}|{formula_family}": _reason_counts_dict(reason_counts)
            for (registry_scope, formula_family), reason_counts in sorted(
                blocked_reason_counts_by_registry_family.items()
            )
        },
        "rows_by_formula_id": dict(sorted(rows_by_formula_id.items())),
        "rows_by_formula_variant": dict(sorted(rows_by_formula_variant.items())),
        "rows_by_formula_family": dict(sorted(rows_by_formula_family.items())),
        "rows_by_stage_bin": dict(sorted(rows_by_stage.items())),
        "rows_by_registry_family": {
            f"{registry_scope}|{formula_family}": count
            for (registry_scope, formula_family), count in sorted(rows_by_registry_family.items())
        },
        "keys_by_formula_id": formula_id_rows,
        "formula_attrition": formula_attrition_rows,
        "formula_family_attrition": formula_family_rows,
        "weakest_keys_by_formula_id": weakest_formula_id_rows,
        "keys_by_formula_variant": formula_variant_rows,
        "weakest_keys_by_formula_variant": weakest_formula_variant_rows,
        "keys_by_stage_bin": stage_rows,
        "weakest_keys_by_stage_bin": weakest_stage_rows,
        "blocked_examples": top_blocked_keys,
        "next_action_recommendation": _recommend_next_action(),
        "dropped_unknown_stage_rows_by_formula_id": dict(sorted((dropped_unknown_stage_rows_by_formula_id or {}).items())),
        "dropped_unknown_stage_rows_by_formula_variant": dict(sorted((dropped_unknown_stage_rows_by_formula_variant or {}).items())),
        "dropped_unknown_stage_examples": (dropped_unknown_stage_examples or [])[:max_examples],
    }
    if include_attrition_detail:
        stage_formula_matrix_rows = []
        for stage_bin, formula_id in sorted(key_counts_by_stage_formula):
            keys_total = key_counts_by_stage_formula[(stage_bin, formula_id)]
            keys_ready = ready_keys_by_stage_formula[(stage_bin, formula_id)]
            keys_blocked = keys_total - keys_ready
            reason_counts = blocked_reason_counts_by_stage_formula[(stage_bin, formula_id)]
            stage_formula_matrix_rows.append(
                {
                    "stage_bin": stage_bin,
                    "formula_id": formula_id,
                    "formula_family": _formula_family(formula_id),
                    "registry_scopes": _formula_registry_scopes(formula_id),
                    "keys_total": keys_total,
                    "keys_ready": keys_ready,
                    "keys_blocked": keys_blocked,
                    "ready_coverage_pct": _coverage(keys_ready, keys_total),
                    "blocked_pct": _coverage(keys_blocked, keys_total),
                    "signal_rows": rows_by_stage_formula[(stage_bin, formula_id)],
                    "blocked_reason_counts": _reason_counts_dict(reason_counts),
                    "top_blocked_reason": _top_reason(reason_counts),
                }
            )
        top_blocked_stage_formula_cells = [
            row
            for row in sorted(
                stage_formula_matrix_rows,
                key=lambda item: (
                    -int(item["keys_blocked"]),
                    float(item["ready_coverage_pct"]),
                    item["stage_bin"],
                    item["formula_id"],
                ),
            )
            if int(row["keys_blocked"]) > 0
        ][:10]

        registry_family_matrix_rows = []
        for registry_scope, formula_family in sorted(key_counts_by_registry_family):
            keys_total = key_counts_by_registry_family[(registry_scope, formula_family)]
            keys_ready = ready_keys_by_registry_family[(registry_scope, formula_family)]
            keys_blocked = keys_total - keys_ready
            reason_counts = blocked_reason_counts_by_registry_family[(registry_scope, formula_family)]
            registry_family_matrix_rows.append(
                {
                    "registry_scope": registry_scope,
                    "formula_family": formula_family,
                    "keys_total": keys_total,
                    "keys_ready": keys_ready,
                    "keys_blocked": keys_blocked,
                    "ready_coverage_pct": _coverage(keys_ready, keys_total),
                    "blocked_pct": _coverage(keys_blocked, keys_total),
                    "signal_rows": rows_by_registry_family[(registry_scope, formula_family)],
                    "blocked_reason_counts": _reason_counts_dict(reason_counts),
                    "top_blocked_reason": _top_reason(reason_counts),
                }
            )
        top_blocked_registry_family_cells = [
            row
            for row in sorted(
                registry_family_matrix_rows,
                key=lambda item: (
                    -int(item["keys_blocked"]),
                    float(item["ready_coverage_pct"]),
                    item["registry_scope"],
                    item["formula_family"],
                ),
            )
            if int(row["keys_blocked"]) > 0
        ][:10]
        result.update(
            {
                "blocked_matrix_by_stage_formula": stage_formula_matrix_rows,
                "top_blocked_stage_formula_cells": top_blocked_stage_formula_cells,
                "blocked_matrix_by_registry_family": registry_family_matrix_rows,
                "top_blocked_registry_family_cells": top_blocked_registry_family_cells,
            }
        )
    return result


def _build_min_signals_sensitivity(
    signal_rows: list[dict[str, Any]],
    codes_with_bars: set[str],
    *,
    baseline_min_signals: int,
    probe_min_signals: tuple[int, ...] = (4, 3, 2),
    max_examples: int = 8,
    dropped_unknown_stage_rows_by_formula_id: dict[str, int] | None = None,
    dropped_unknown_stage_rows_by_formula_variant: dict[str, int] | None = None,
    dropped_unknown_stage_examples: list[dict[str, Any]] | None = None,
    baseline_summary: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Compare a small min_signals sweep without rebuilding full attrition matrices."""
    probe_values = [value for value in probe_min_signals if value != baseline_min_signals]
    if not probe_values:
        return []

    key_counts: Counter[tuple[str, str, str, str]] = Counter()
    for row in signal_rows:
        key_counts[
            (
                str(row["stock_code"]),
                str(row["formula_id"]),
                str(row["formula_variant"]),
                str(row["stage_bin"]),
            )
        ] += 1
    total_keys = len(key_counts)

    def _coverage(ready: int) -> float:
        return round(100.0 * ready / total_keys, 2) if total_keys else 0.0

    def _threshold_counts(min_signals: int) -> tuple[int, float, Counter[str]]:
        ready_keys = 0
        blocked_reason_counts: Counter[str] = Counter()
        for stock_code, _formula_id, _formula_variant, _stage_bin in key_counts:
            n_rows = key_counts[(stock_code, _formula_id, _formula_variant, _stage_bin)]
            blocked = False
            if n_rows < min_signals:
                blocked_reason_counts["below_min_signals"] += 1
                blocked = True
            if stock_code not in codes_with_bars:
                blocked_reason_counts["no_kline_bars"] += 1
                blocked = True
            if not blocked:
                ready_keys += 1
        return ready_keys, _coverage(ready_keys), blocked_reason_counts

    def _recommend_from_counts(reason_counts: Counter[str]) -> dict[str, str | None]:
        below_min_signals = reason_counts.get("below_min_signals", 0)
        no_kline_bars = reason_counts.get("no_kline_bars", 0)
        if below_min_signals == 0 and no_kline_bars == 0:
            return {
                "priority": "P2",
                "focus": "candidate_supply_monitoring",
                "reason": "no blocking reasons detected in current slice",
                "recommended_lever": "keep monitoring upstream supply and PIT coverage",
                "top_blocked_reason": None,
            }
        if below_min_signals >= no_kline_bars:
            return {
                "priority": "P1",
                "focus": "upstream_candidate_supply",
                "reason": "below_min_signals dominates current blocked keys",
                "recommended_lever": "expand upstream formula coverage or signal density before tuning profile knobs",
                "top_blocked_reason": "below_min_signals",
            }
        return {
            "priority": "P1",
            "focus": "kline_coverage",
            "reason": "no_kline_bars dominates current blocked keys",
            "recommended_lever": "repair missing bars or date coverage before re-running candidate supply",
            "top_blocked_reason": "no_kline_bars",
        }

    if baseline_summary is not None:
        baseline_ready_keys = int(baseline_summary["ready_keys"])
        baseline_ready_coverage_pct = float(baseline_summary["ready_coverage_pct"])
        baseline_blocked = int((baseline_summary.get("blocked_reason_counts") or {}).get("below_min_signals", 0))
    else:
        baseline_ready_keys, baseline_ready_coverage_pct, baseline_counts = _threshold_counts(baseline_min_signals)
        baseline_blocked = int(baseline_counts.get("below_min_signals", 0))

    sensitivity_rows: list[dict[str, Any]] = []
    for probe_min_signals_value in probe_values:
        probe_ready_keys, probe_ready_coverage_pct, probe_counts = _threshold_counts(probe_min_signals_value)
        probe_blocked = int(probe_counts.get("below_min_signals", 0))
        probe_recommendation = _recommend_from_counts(probe_counts)
        sensitivity_rows.append(
            {
                "min_signals": probe_min_signals_value,
                "ready_keys": probe_ready_keys,
                "ready_coverage_pct": probe_ready_coverage_pct,
                "delta_ready_keys": probe_ready_keys - baseline_ready_keys,
                "delta_ready_coverage_pct": round(
                    probe_ready_coverage_pct - baseline_ready_coverage_pct,
                    2,
                ),
                "below_min_signals": probe_blocked,
                "delta_below_min_signals": probe_blocked - baseline_blocked,
                "next_action_recommendation": {
                    "priority": probe_recommendation.get("priority"),
                    "focus": probe_recommendation.get("focus"),
                    "reason": probe_recommendation.get("reason"),
                    "recommended_lever": probe_recommendation.get("recommended_lever"),
                    "top_blocked_reason": probe_recommendation.get("top_blocked_reason"),
                },
            }
        )
    return sensitivity_rows


def _render_markdown(result: dict[str, Any]) -> str:
    lines = [
        "# Stage Opt Candidate Supply",
        f"- start: {result['start']}",
        f"- end: {result['end']}",
        f"- min_signals: {result['min_signals']}",
        f"- raw_signal_rows: {result['raw_signal_rows']}",
    ]
    if "raw_trigger_rows" in result:
        lines.append(f"- raw_trigger_rows: {result['raw_trigger_rows']}")
    if "raw_state_history_rows" in result:
        lines.append(f"- raw_state_history_rows: {result['raw_state_history_rows']}")
    lines.extend([
        f"- filtered_signal_rows: {result['filtered_signal_rows']}",
        f"- dropped_index_rows: {result['dropped_index_rows']}",
        f"- dropped_unknown_stage_rows: {result['dropped_unknown_stage_rows']}",
        f"- source_load_errors: {len(result.get('source_load_errors') or [])}",
        f"- codes_with_bars: {result['codes_with_bars']}",
        f"- codes_without_bars: {result['codes_without_bars']}",
        f"- unique_keys: {result['unique_keys']}",
        f"- ready_keys: {result['ready_keys']}",
        f"- ready_coverage_pct: {result['ready_coverage_pct']}",
        f"- blocked_reason_counts: {result['blocked_reason_counts']}",
        f"- next_action_recommendation: {result['next_action_recommendation']}",
        "",
    ])
    if result.get("attrition_funnel"):
        funnel = result["attrition_funnel"]
        lines.extend(
            [
                "## Attrition Funnel",
                f"- raw_rows: {funnel['raw_rows']}",
                f"- dropped_index_rows: {funnel['dropped_index_rows']}",
                f"- dropped_unknown_stage_rows: {funnel['dropped_unknown_stage_rows']}",
                f"- filtered_signal_rows: {funnel['filtered_signal_rows']}",
                f"- unique_keys: {funnel['unique_keys']}",
                f"- blocked_keys: {funnel['blocked_keys']}",
                f"- ready_keys: {funnel['ready_keys']}",
                f"- ready_coverage_pct: {funnel['ready_coverage_pct']}",
                "",
            ]
        )
    if result.get("live_formula_registry"):
        registry = result["live_formula_registry"]
        lines.extend(
            [
                "## Live Formula Registry",
                f"- formula_count: {registry.get('formula_count', 0)}",
            ]
        )
        formula_ids = registry.get("formula_ids") or []
        if formula_ids:
            lines.append(f"- formula_ids: {', '.join(str(item) for item in formula_ids)}")
        lines.append("")
    if result.get("candidate_supply_contract"):
        contract = result["candidate_supply_contract"]
        lines.extend(
            [
                "## Candidate Supply Contract",
                f"- version: {contract.get('version')}",
                f"- allowed_stage_bins: {', '.join(str(item) for item in contract.get('allowed_stage_bins') or [])}",
            ]
        )
        readiness = contract.get("readiness") or {}
        if readiness:
            lines.append(f"- readiness.min_signals_per_key: {readiness.get('min_signals_per_key')}")
        for source in contract.get("sources") or []:
            lines.append(
                f"- {source.get('source_id')}: table={source.get('table')} "
                f"role={source.get('semantic_role')} eligibility={source.get('eligibility')} "
                f"pit_status={source.get('pit_status')}"
            )
        lines.append("")
    if result.get("research_formula_registry"):
        registry = result["research_formula_registry"]
        lines.extend(
            [
                "## Research Formula Registry",
                f"- formula_count: {registry.get('formula_count', 0)}",
            ]
        )
        formula_ids = registry.get("formula_ids") or []
        if formula_ids:
            lines.append(f"- formula_ids: {', '.join(str(item) for item in formula_ids)}")
        lines.append("")
    if result.get("source_load_errors"):
        lines.append("## Source Load Errors")
        for error in result["source_load_errors"]:
            lines.append(
                f"- {error.get('source_id')}: table={error.get('table')} "
                f"{error.get('error_type')}: {error.get('error')}"
            )
        lines.append("")
    lines.extend([
        "## By Formula Id",
    ])
    for row in result["keys_by_formula_id"]:
        lines.append(
            f"- {row['formula_id']}: keys_total={row['keys_total']} "
            f"ready={row['keys_ready']} coverage={row['ready_coverage_pct']}% "
            f"signal_rows={row['signal_rows']}"
        )
    lines.append("")
    lines.append("## Weakest Formula Ids")
    for row in result["weakest_keys_by_formula_id"][:10]:
        lines.append(
            f"- {row['formula_id']}: keys_total={row['keys_total']} "
            f"ready={row['keys_ready']} coverage={row['ready_coverage_pct']}% "
            f"signal_rows={row['signal_rows']}"
        )
    lines.append("")
    if result.get("formula_family_attrition"):
        lines.append("## Formula Family Attrition")
        for row in result["formula_family_attrition"]:
            reason_text = ", ".join(f"{reason}={count}" for reason, count in row["blocked_reason_counts"].items())
            if not reason_text:
                reason_text = "none"
            lines.append(
                f"- {row['formula_family']}: keys_total={row['keys_total']} ready={row['keys_ready']} "
                f"blocked={row['keys_blocked']} coverage={row['ready_coverage_pct']}% "
                f"blocked_pct={row['blocked_pct']}% reasons={reason_text}"
            )
        lines.append("")
    if result.get("next_action_recommendation", {}).get("structural_notes"):
        lines.append("## Structural Notes")
        for note in result["next_action_recommendation"]["structural_notes"]:
            lines.append(f"- {note}")
        lines.append("")
    if result.get("dropped_unknown_stage_rows_by_formula_id"):
        lines.append("## Unknown Stage Drops By Formula Id")
        for formula_id, n_rows in Counter(result["dropped_unknown_stage_rows_by_formula_id"]).most_common(10):
            lines.append(f"- {formula_id}: dropped_unknown_stage_rows={n_rows}")
        lines.append("")
    if result.get("dropped_unknown_stage_rows_by_formula_variant"):
        lines.append("## Unknown Stage Drops By Formula Variant")
        for formula_variant, n_rows in Counter(
            result["dropped_unknown_stage_rows_by_formula_variant"]
        ).most_common(10):
            lines.append(f"- {formula_variant}: dropped_unknown_stage_rows={n_rows}")
        lines.append("")
    if result.get("dropped_unknown_stage_examples"):
        lines.append("## Unknown Stage Examples")
        for row in result["dropped_unknown_stage_examples"][:10]:
            lines.append(
                f"- {row['stock_code']} {row['formula_variant']} stage={row['stage_bin']} "
                f"date={row['signal_date']} formula={row['formula_id']}"
            )
        lines.append("")
    if result.get("min_signals_sensitivity"):
        lines.append("## Min Signals Sensitivity")
        for row in result["min_signals_sensitivity"]:
            delta_cov = row.get("delta_ready_coverage_pct")
            delta_keys = row.get("delta_ready_keys")
            delta_blocked = row.get("delta_below_min_signals")
            lines.append(
                f"- min_signals={row['min_signals']}: ready_keys={row['ready_keys']} "
                f"coverage={row['ready_coverage_pct']}% (Δkeys={delta_keys:+}, Δcoverage={delta_cov:+.2f}pp, "
                f"Δbelow_min={delta_blocked:+})"
            )
        lines.append("")
    lines.append("## By Formula Variant")
    for row in result["keys_by_formula_variant"]:
        lines.append(
            f"- {row['formula_variant']}: keys_total={row['keys_total']} "
            f"ready={row['keys_ready']} coverage={row['ready_coverage_pct']}% "
            f"signal_rows={row['signal_rows']}"
        )
    lines.append("")
    lines.append("## Weakest Formula Variants")
    for row in result["weakest_keys_by_formula_variant"][:10]:
        lines.append(
            f"- {row['formula_variant']}: keys_total={row['keys_total']} "
            f"ready={row['keys_ready']} coverage={row['ready_coverage_pct']}% "
            f"signal_rows={row['signal_rows']}"
        )
    lines.append("")
    lines.append("## By Stage")
    for row in result["keys_by_stage_bin"]:
        lines.append(
            f"- stage {row['stage_bin']}: keys_total={row['keys_total']} "
            f"ready={row['keys_ready']} coverage={row['ready_coverage_pct']}% "
            f"signal_rows={row['signal_rows']}"
        )
    lines.append("")
    lines.append("## Weakest Stages")
    for row in result["weakest_keys_by_stage_bin"][:10]:
        lines.append(
            f"- stage {row['stage_bin']}: keys_total={row['keys_total']} "
            f"ready={row['keys_ready']} coverage={row['ready_coverage_pct']}% "
            f"signal_rows={row['signal_rows']}"
        )
    if result.get("top_blocked_stage_formula_cells"):
        lines.append("")
        lines.append("## Top Blocked Stage x Formula Cells")
        for row in result["top_blocked_stage_formula_cells"]:
            reason_text = ", ".join(f"{reason}={count}" for reason, count in row["blocked_reason_counts"].items())
            lines.append(
                f"- stage {row['stage_bin']} × {row['formula_id']}: keys_total={row['keys_total']} "
                f"ready={row['keys_ready']} blocked={row['keys_blocked']} "
                f"coverage={row['ready_coverage_pct']}% reasons={reason_text}"
            )
    if result.get("top_blocked_registry_family_cells"):
        lines.append("")
        lines.append("## Top Blocked Registry x Family Cells")
        for row in result["top_blocked_registry_family_cells"]:
            reason_text = ", ".join(f"{reason}={count}" for reason, count in row["blocked_reason_counts"].items())
            lines.append(
                f"- {row['registry_scope']} × {row['formula_family']}: keys_total={row['keys_total']} "
                f"ready={row['keys_ready']} blocked={row['keys_blocked']} "
                f"coverage={row['ready_coverage_pct']}% reasons={reason_text}"
            )
    if result["blocked_examples"]:
        lines.append("")
        lines.append("## Blocked Examples")
        for row in result["blocked_examples"]:
            lines.append(
                f"- {row['stock_code']} {row['formula_variant']} stage={row['stage_bin']} "
                f"signals={row['signal_rows']} bars={row['has_bars']} "
                f"reasons={row['blocked_reasons']}"
            )
    if result["blocked_reason_counts_by_formula_id"]:
        lines.append("")
        lines.append("## Blocked Reasons By Formula Id")
        for formula_id, reason_counts in result["blocked_reason_counts_by_formula_id"].items():
            reason_text = ", ".join(f"{reason}={count}" for reason, count in reason_counts.items())
            lines.append(f"- {formula_id}: {reason_text}")
    if result["blocked_reason_counts_by_formula_variant"]:
        lines.append("")
        lines.append("## Blocked Reasons By Formula Variant")
        for formula_variant, reason_counts in result["blocked_reason_counts_by_formula_variant"].items():
            reason_text = ", ".join(f"{reason}={count}" for reason, count in reason_counts.items())
            lines.append(f"- {formula_variant}: {reason_text}")
    if result["blocked_reason_counts_by_stage_bin"]:
        lines.append("")
        lines.append("## Blocked Reasons By Stage")
        for stage_bin, reason_counts in result["blocked_reason_counts_by_stage_bin"].items():
            reason_text = ", ".join(f"{reason}={count}" for reason, count in reason_counts.items())
            lines.append(f"- stage {stage_bin}: {reason_text}")
    if result.get("blocked_reason_counts_by_formula_family"):
        lines.append("")
        lines.append("## Blocked Reasons By Formula Family")
        for formula_family, reason_counts in result["blocked_reason_counts_by_formula_family"].items():
            reason_text = ", ".join(f"{reason}={count}" for reason, count in reason_counts.items())
            lines.append(f"- {formula_family}: {reason_text}")
    return "\n".join(lines).rstrip() + "\n"


def _compose_audit_result(
    load_result: dict[str, Any],
    summary: dict[str, Any],
    *,
    start: str,
    end: str,
    min_signals: int,
    signal_rows: list[dict[str, Any]],
    codes_total: int,
    codes_with_bars: set[str],
) -> dict[str, Any]:
    """Compose the final audit payload without letting summary counters shadow raw counts."""
    unique_keys = int(summary.get("unique_keys") or 0)
    ready_keys = int(summary.get("ready_keys") or 0)
    blocked_keys = max(0, unique_keys - ready_keys)
    blocked_reason_counts = summary.get("blocked_reason_counts") or {}
    dropped_unknown_stage_rows = int(load_result["dropped_unknown_stage_rows"])
    source_load_errors = list(load_result.get("source_load_errors") or [])
    has_candidate_supply = unique_keys > 0
    verdict = "WARN" if blocked_keys or dropped_unknown_stage_rows or source_load_errors or not has_candidate_supply else "PASS"
    next_action_recommendation = summary.get("next_action_recommendation")
    if dropped_unknown_stage_rows and not blocked_keys:
        top_unknown_formulas = [
            formula_id
            for formula_id, _count in Counter(load_result["dropped_unknown_stage_rows_by_formula_id"]).most_common(3)
        ]
        next_action_recommendation = {
            "priority": "P1",
            "focus": "stage_context_coverage",
            "reason": "unknown-stage rows were dropped before candidate-key evaluation",
            "recommended_lever": "repair missing or invalid technical_stage coverage before treating candidate supply as clean",
            "weakest_formula_ids": top_unknown_formulas,
            "weakest_stage_bins": ["unknown"],
            "top_blocked_reason": "unknown_stage",
        }
    elif source_load_errors and not blocked_keys:
        next_action_recommendation = {
            "priority": "P1",
            "focus": "candidate_supply_source_load",
            "reason": "one or more configured candidate-supply sources failed to load",
            "recommended_lever": "repair source table/schema availability before treating zero rows as true supply absence",
            "weakest_formula_ids": [],
            "weakest_stage_bins": [],
            "top_blocked_reason": "source_load_error",
        }
    elif not has_candidate_supply:
        next_action_recommendation = {
            "priority": "P1",
            "focus": "candidate_supply_coverage",
            "reason": "no candidate keys were available for the selected audit scope",
            "recommended_lever": "check date, formula, stock filters, and upstream signal inputs before treating supply as clean",
            "weakest_formula_ids": [],
            "weakest_stage_bins": [],
            "top_blocked_reason": "no_candidate_supply",
        }
    return {
        **summary,
        "schema_version": 1,
        "verdict": verdict,
        "next_action_recommendation": next_action_recommendation,
        "start": start,
        "end": end,
        "min_signals": min_signals,
        "raw_signal_rows": load_result["raw_rows"],
        "raw_trigger_rows": load_result.get("raw_trigger_rows", load_result["raw_rows"]),
        "raw_state_history_rows": load_result.get("raw_state_history_rows", 0),
        "source_load_errors": source_load_errors,
        "filtered_signal_rows": len(signal_rows),
        "dropped_index_rows": load_result["dropped_index_rows"],
        "dropped_unknown_stage_rows": load_result["dropped_unknown_stage_rows"],
        "dropped_unknown_stage_rows_by_formula_id": load_result["dropped_unknown_stage_rows_by_formula_id"],
        "dropped_unknown_stage_rows_by_formula_variant": load_result["dropped_unknown_stage_rows_by_formula_variant"],
        "dropped_unknown_stage_examples": load_result["dropped_unknown_stage_examples"],
        "codes_with_bars": len(codes_with_bars),
        "codes_without_bars": codes_total - len(codes_with_bars),
        "attrition_funnel": {
            "raw_rows": load_result["raw_rows"],
            "raw_trigger_rows": load_result.get("raw_trigger_rows", load_result["raw_rows"]),
            "raw_state_history_rows": load_result.get("raw_state_history_rows", 0),
            "dropped_index_rows": load_result["dropped_index_rows"],
            "dropped_unknown_stage_rows": dropped_unknown_stage_rows,
            "filtered_signal_rows": len(signal_rows),
            "unique_keys": unique_keys,
            "blocked_keys": blocked_keys,
            "ready_keys": ready_keys,
            "ready_coverage_pct": summary.get("ready_coverage_pct"),
            "blocked_reason_counts": blocked_reason_counts,
        },
        "candidate_supply_contract": SUPPLY_CONTRACT.to_report(),
        "live_formula_registry": _live_formula_registry_summary(),
        "research_formula_registry": _research_formula_registry_summary(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit stage-opt candidate supply and formula coverage")
    parser.add_argument(
        "--start",
        default="2023-01-01",  # rule-compliance: ok evidence=stage-opt-candidate-supply-audit-window
        help="signal_date start",
    )
    parser.add_argument("--end", default=None, help="signal_date end, default latest closed trade date")
    parser.add_argument("--formula", nargs="+", default=None, help="only audit selected formula ids")
    parser.add_argument("--stock-codes", nargs="+", default=None, help="only audit selected stock codes")
    parser.add_argument("--limit-stocks", type=int, default=None, help="limit audited stocks after sorting")
    parser.add_argument(
        "--min-signals",
        type=int,
        default=None,
        help="override readiness.min_signals_per_key from stage_opt_candidate_supply.yaml",
    )
    parser.add_argument("--format", choices=["markdown", "json"], default="markdown")
    args = parser.parse_args()
    min_signals = args.min_signals if args.min_signals is not None else SUPPLY_CONTRACT.min_signals_per_key
    if min_signals <= 0:
        parser.error("--min-signals must be a positive integer")

    conn = duck_connect(str(MARKET_DB), read_only=True)
    attach_with_retry(conn.raw, "sm", str(SMART_DB), read_only=True, timeout=60)
    try:
        start = args.start
        end = args.end
        if end is None:
            end = _latest_closed_trade_date(conn)
            if end is None:
                raise SystemExit("no trading calendar data found for default end date")

        load_result = _load_signal_rows(
            conn,
            start=start,
            end=end,
            formula=args.formula,
            stock_codes=args.stock_codes,
        )
        signal_rows = load_result["signal_rows"]
        codes = sorted({row["stock_code"] for row in signal_rows})
        scoped_load_result = load_result
        if args.limit_stocks is not None:
            codes = _limit_candidate_stock_codes(load_result, signal_rows)[: max(0, args.limit_stocks)]
            allowed = set(codes)
            scoped_load_result = _filter_load_result_for_stock_codes(load_result, allowed)
            signal_rows = scoped_load_result["signal_rows"]

        codes_with_bars = _load_kline_codes(conn, codes=codes, start=args.start, end=end)
        summary = summarize_stage_opt_candidate_supply(
            signal_rows,
            codes_with_bars,
            min_signals=min_signals,
            dropped_unknown_stage_rows_by_formula_id=scoped_load_result["dropped_unknown_stage_rows_by_formula_id"],
            dropped_unknown_stage_rows_by_formula_variant=scoped_load_result["dropped_unknown_stage_rows_by_formula_variant"],
            dropped_unknown_stage_examples=scoped_load_result["dropped_unknown_stage_examples"],
        )
        result = _compose_audit_result(
            scoped_load_result,
            summary,
            start=start,
            end=end,
            min_signals=min_signals,
            signal_rows=signal_rows,
            codes_total=len(codes),
            codes_with_bars=codes_with_bars,
        )
        result["min_signals_sensitivity"] = _build_min_signals_sensitivity(
            signal_rows,
            codes_with_bars,
            baseline_min_signals=min_signals,
            dropped_unknown_stage_rows_by_formula_id=scoped_load_result["dropped_unknown_stage_rows_by_formula_id"],
            dropped_unknown_stage_rows_by_formula_variant=scoped_load_result["dropped_unknown_stage_rows_by_formula_variant"],
            dropped_unknown_stage_examples=scoped_load_result["dropped_unknown_stage_examples"],
            baseline_summary=summary,
        )

        if args.format == "json":
            print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        else:
            print(_render_markdown(result))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
