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

import duckdb

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.calendar import DEFAULT_CLOSE_HOUR, DEFAULT_CLOSE_MINUTE
from services.backtest.filters import is_index_code


MARKET_DB = Path(__file__).resolve().parents[2] / "data" / "market.duckdb"
SMART_DB = Path(__file__).resolve().parents[2] / "data" / "smartmoney.duckdb"
ALLOWED_STAGES = {"1", "1.5", "2", "3", "4"}

log = logging.getLogger("audit_stage_opt_candidate_supply")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


def _latest_signal_date(conn) -> str | None:
    row = conn.execute("SELECT MAX(date) FROM sm.fact_technical_trigger").fetchone()
    return str(row[0]) if row and row[0] is not None else None


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

    rows = conn.execute(
        f"""
        SELECT t.stock_code, t.date, t.formula_id, t.formula_variant,
               COALESCE(c.technical_stage, '?') AS stage_bin
          FROM sm.fact_technical_trigger t
          LEFT JOIN sm.fact_signal_context c
            ON c.stock_code = t.stock_code AND c.date = t.date
         WHERE t.date >= ? AND t.date <= ?
           {formula_filter_sql}
           {stock_filter_sql}
         ORDER BY t.stock_code, t.formula_variant, t.date
        """,
        [start, end] + formula_filter_params + stock_filter_params,
    ).fetchall()

    raw_rows = len(rows)
    dropped_index_rows = 0
    dropped_unknown_stage_rows = 0
    dropped_unknown_stage_rows_by_formula_id: Counter[str] = Counter()
    dropped_unknown_stage_rows_by_formula_variant: Counter[str] = Counter()
    dropped_unknown_stage_examples: list[dict[str, Any]] = []
    signal_rows: list[dict[str, Any]] = []
    for stock_code, signal_date, formula_id, formula_variant, stage_bin in rows:
        if is_index_code(stock_code):
            dropped_index_rows += 1
            continue
        stage_bin = str(stage_bin or "?")
        if stage_bin not in ALLOWED_STAGES:
            dropped_unknown_stage_rows += 1
            dropped_unknown_stage_rows_by_formula_id[str(formula_id)] += 1
            dropped_unknown_stage_rows_by_formula_variant[str(formula_variant)] += 1
            if len(dropped_unknown_stage_examples) < 8:
                dropped_unknown_stage_examples.append(
                    {
                        "stock_code": str(stock_code),
                        "signal_date": str(signal_date),
                        "formula_id": str(formula_id),
                        "formula_variant": str(formula_variant),
                        "stage_bin": stage_bin,
                    }
                )
            continue
        signal_rows.append(
            {
                "stock_code": str(stock_code),
                "signal_date": str(signal_date),
                "formula_id": str(formula_id),
                "formula_variant": str(formula_variant),
                "stage_bin": stage_bin,
            }
        )

    return {
        "raw_rows": raw_rows,
        "dropped_index_rows": dropped_index_rows,
        "dropped_unknown_stage_rows": dropped_unknown_stage_rows,
        "dropped_unknown_stage_rows_by_formula_id": dict(sorted(dropped_unknown_stage_rows_by_formula_id.items())),
        "dropped_unknown_stage_rows_by_formula_variant": dict(sorted(dropped_unknown_stage_rows_by_formula_variant.items())),
        "dropped_unknown_stage_examples": dropped_unknown_stage_examples,
        "signal_rows": signal_rows,
    }


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
) -> dict[str, Any]:
    """Summarize how much stage-opt candidate supply survives the pre-Optuna gates."""
    key_rows: dict[tuple[str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    rows_by_formula_id = Counter()
    rows_by_formula_variant = Counter()
    rows_by_stage = Counter()
    key_counts_by_formula_id = Counter()
    key_counts_by_formula_variant = Counter()
    key_counts_by_stage = Counter()
    ready_keys_by_formula_id = Counter()
    ready_keys_by_formula_variant = Counter()
    ready_keys_by_stage = Counter()
    blocked_reason_counts = Counter()
    blocked_reason_counts_by_formula_id: defaultdict[str, Counter[str]] = defaultdict(Counter)
    blocked_reason_counts_by_formula_variant: defaultdict[str, Counter[str]] = defaultdict(Counter)
    blocked_reason_counts_by_stage = defaultdict(Counter)
    blocked_examples: list[dict[str, Any]] = []

    for row in signal_rows:
        key = (
            row["stock_code"],
            row["formula_id"],
            row["formula_variant"],
            row["stage_bin"],
        )
        key_rows[key].append(row)
        rows_by_formula_id[row["formula_id"]] += 1
        rows_by_formula_variant[row["formula_variant"]] += 1
        rows_by_stage[row["stage_bin"]] += 1

    for (stock_code, formula_id, formula_variant, stage_bin), rows in key_rows.items():
        n_rows = len(rows)
        has_bars = stock_code in codes_with_bars
        key_counts_by_formula_id[formula_id] += 1
        key_counts_by_formula_variant[formula_variant] += 1
        key_counts_by_stage[stage_bin] += 1

        blocked_reasons: list[str] = []
        if n_rows < min_signals:
            blocked_reasons.append("below_min_signals")
            blocked_reason_counts["below_min_signals"] += 1
            blocked_reason_counts_by_formula_id[formula_id]["below_min_signals"] += 1
            blocked_reason_counts_by_formula_variant[formula_variant]["below_min_signals"] += 1
            blocked_reason_counts_by_stage[stage_bin]["below_min_signals"] += 1
        if not has_bars:
            blocked_reasons.append("no_kline_bars")
            blocked_reason_counts["no_kline_bars"] += 1
            blocked_reason_counts_by_formula_id[formula_id]["no_kline_bars"] += 1
            blocked_reason_counts_by_formula_variant[formula_variant]["no_kline_bars"] += 1
            blocked_reason_counts_by_stage[stage_bin]["no_kline_bars"] += 1

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
        ready_keys_by_stage[stage_bin] += 1

    ready_key_count = sum(ready_keys_by_formula_id.values())  # same as len(ready_keys)
    total_key_count = len(key_rows)

    def _coverage(ready: int, total: int) -> float:
        return round(100.0 * ready / total, 2) if total else 0.0

    formula_id_rows = []
    sorted_formula_ids = sorted(key_counts_by_formula_id)
    for formula_id in sorted_formula_ids:
        formula_id_rows.append(
            {
                "formula_id": formula_id,
                "keys_total": key_counts_by_formula_id[formula_id],
                "keys_ready": ready_keys_by_formula_id[formula_id],
                "ready_coverage_pct": _coverage(
                    ready_keys_by_formula_id[formula_id], key_counts_by_formula_id[formula_id]
                ),
                "signal_rows": rows_by_formula_id[formula_id],
            }
        )
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
        stage_rows.append(
            {
                "stage_bin": stage_bin,
                "keys_total": key_counts_by_stage[stage_bin],
                "keys_ready": ready_keys_by_stage[stage_bin],
                "ready_coverage_pct": _coverage(
                    ready_keys_by_stage[stage_bin], key_counts_by_stage[stage_bin]
                ),
                "signal_rows": rows_by_stage[stage_bin],
            }
        )
    weakest_stage_rows = sorted(
        stage_rows,
        key=lambda row: (row["ready_coverage_pct"], -row["keys_total"], row["stage_bin"]),
    )

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

    return {
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
        "blocked_reason_counts_by_stage_bin": {
            stage_bin: dict(sorted(reason_counts.items()))
            for stage_bin, reason_counts in sorted(blocked_reason_counts_by_stage.items())
        },
        "rows_by_formula_id": dict(sorted(rows_by_formula_id.items())),
        "rows_by_formula_variant": dict(sorted(rows_by_formula_variant.items())),
        "rows_by_stage_bin": dict(sorted(rows_by_stage.items())),
        "keys_by_formula_id": formula_id_rows,
        "weakest_keys_by_formula_id": weakest_formula_id_rows,
        "keys_by_formula_variant": formula_variant_rows,
        "weakest_keys_by_formula_variant": weakest_formula_variant_rows,
        "keys_by_stage_bin": stage_rows,
        "weakest_keys_by_stage_bin": weakest_stage_rows,
        "blocked_examples": top_blocked_keys,
        "dropped_unknown_stage_rows_by_formula_id": dict(sorted((dropped_unknown_stage_rows_by_formula_id or {}).items())),
        "dropped_unknown_stage_rows_by_formula_variant": dict(sorted((dropped_unknown_stage_rows_by_formula_variant or {}).items())),
        "dropped_unknown_stage_examples": (dropped_unknown_stage_examples or [])[:max_examples],
    }


def _render_markdown(result: dict[str, Any]) -> str:
    lines = [
        "# Stage Opt Candidate Supply",
        f"- start: {result['start']}",
        f"- end: {result['end']}",
        f"- min_signals: {result['min_signals']}",
        f"- raw_signal_rows: {result['raw_signal_rows']}",
        f"- filtered_signal_rows: {result['filtered_signal_rows']}",
        f"- dropped_index_rows: {result['dropped_index_rows']}",
        f"- dropped_unknown_stage_rows: {result['dropped_unknown_stage_rows']}",
        f"- codes_with_bars: {result['codes_with_bars']}",
        f"- codes_without_bars: {result['codes_without_bars']}",
        f"- unique_keys: {result['unique_keys']}",
        f"- ready_keys: {result['ready_keys']}",
        f"- ready_coverage_pct: {result['ready_coverage_pct']}",
        f"- blocked_reason_counts: {result['blocked_reason_counts']}",
        "",
        "## By Formula Id",
    ]
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
    return {
        **summary,
        "start": start,
        "end": end,
        "min_signals": min_signals,
        "raw_signal_rows": load_result["raw_rows"],
        "filtered_signal_rows": len(signal_rows),
        "dropped_index_rows": load_result["dropped_index_rows"],
        "dropped_unknown_stage_rows": load_result["dropped_unknown_stage_rows"],
        "dropped_unknown_stage_rows_by_formula_id": load_result["dropped_unknown_stage_rows_by_formula_id"],
        "dropped_unknown_stage_rows_by_formula_variant": load_result["dropped_unknown_stage_rows_by_formula_variant"],
        "dropped_unknown_stage_examples": load_result["dropped_unknown_stage_examples"],
        "codes_with_bars": len(codes_with_bars),
        "codes_without_bars": codes_total - len(codes_with_bars),
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
    parser.add_argument("--min-signals", type=int, default=5, help="minimum signal rows per (stock × formula × stage)")
    parser.add_argument("--format", choices=["markdown", "json"], default="markdown")
    args = parser.parse_args()

    conn = duckdb.connect(str(MARKET_DB), read_only=True)
    conn.execute(f"ATTACH '{SMART_DB}' AS sm (READ_ONLY)")
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
        if args.limit_stocks is not None:
            codes = codes[: max(0, args.limit_stocks)]
            allowed = set(codes)
            signal_rows = [row for row in signal_rows if row["stock_code"] in allowed]

        codes_with_bars = _load_kline_codes(conn, codes=codes, start=args.start, end=end)
        summary = summarize_stage_opt_candidate_supply(
            signal_rows,
            codes_with_bars,
            min_signals=args.min_signals,
            dropped_unknown_stage_rows_by_formula_id=load_result["dropped_unknown_stage_rows_by_formula_id"],
            dropped_unknown_stage_rows_by_formula_variant=load_result["dropped_unknown_stage_rows_by_formula_variant"],
            dropped_unknown_stage_examples=load_result["dropped_unknown_stage_examples"],
        )
        result = _compose_audit_result(
            load_result,
            summary,
            start=start,
            end=end,
            min_signals=args.min_signals,
            signal_rows=signal_rows,
            codes_total=len(codes),
            codes_with_bars=codes_with_bars,
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
