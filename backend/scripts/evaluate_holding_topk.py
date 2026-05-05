#!/usr/bin/env python3
"""Evaluate holding-period and topK tradeability grids for model predictions."""
from __future__ import annotations

import argparse
import json
import logging
import math
import re
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.db import get_conn
from services.ml_lifecycle.registry import select_default_model_id
from services.pipeline_manifest import git_commit_sha, record_pipeline_run, utc_now_iso


logger = logging.getLogger("holding_topk_eval")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s | %(message)s")

REPO = Path(__file__).resolve().parent.parent.parent
IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

DDL = """
CREATE TABLE IF NOT EXISTS mart_model_holding_topk_eval (
    run_id TEXT NOT NULL,
    model_id TEXT NOT NULL,
    feature_table TEXT NOT NULL,
    feature_set_id TEXT,
    label_name TEXT NOT NULL,
    holding_period INTEGER NOT NULL,
    top_k INTEGER NOT NULL,
    cost_bps DOUBLE NOT NULL,
    n_dates INTEGER,
    n_signals INTEGER,
    ic_mean DOUBLE,
    rank_ic_mean DOUBLE,
    avg_top_return DOUBLE,
    avg_benchmark_return DOUBLE,
    avg_excess_return DOUBLE,
    long_short_spread DOUBLE,
    winrate DOUBLE,
    avg_turnover DOUBLE,
    max_drawdown DOUBLE,
    after_cost_return DOUBLE,
    avg_industry_hhi DOUBLE,
    recommendation TEXT,
    notes TEXT,
    built_at TEXT NOT NULL,
    PRIMARY KEY (run_id, model_id, label_name, top_k, cost_bps)
);
CREATE INDEX IF NOT EXISTS idx_holding_topk_model
    ON mart_model_holding_topk_eval(model_id, label_name, top_k);
"""


def _quote_ident(name: str) -> str:
    if not IDENT_RE.match(name or ""):
        raise ValueError(f"unsafe identifier: {name!r}")
    return f'"{name}"'


def _table_exists(conn, table: str) -> bool:
    row = conn.execute(
        "SELECT COUNT(*) FROM information_schema.tables WHERE table_name = ?",
        (table,),
    ).fetchone()
    return bool(row and row[0])


def _has_column(conn, table: str, column: str) -> bool:
    row = conn.execute(
        "SELECT COUNT(*) FROM information_schema.columns WHERE table_name = ? AND column_name = ?",
        (table, column),
    ).fetchone()
    return bool(row and row[0])


def _records_from_cursor(cursor: Any) -> list[dict[str, Any]]:
    names = [desc[0] for desc in (cursor.description or [])]
    return [{name: value for name, value in zip(names, row)} for row in cursor.fetchall()]


def _parse_csv_ints(raw: str) -> list[int]:
    return [int(item.strip()) for item in raw.split(",") if item.strip()]


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(number) else number


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 3 or len(xs) != len(ys):
        return None
    mx = sum(xs) / len(xs)
    my = sum(ys) / len(ys)
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    vx = sum((x - mx) ** 2 for x in xs)
    vy = sum((y - my) ** 2 for y in ys)
    if vx <= 0 or vy <= 0:
        return None
    return cov / math.sqrt(vx * vy)


def _ranks(values: list[float]) -> list[float]:
    ordered = sorted(enumerate(values), key=lambda item: item[1])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(ordered):
        j = i + 1
        while j < len(ordered) and ordered[j][1] == ordered[i][1]:
            j += 1
        avg_rank = (i + j + 1) / 2.0
        for k in range(i, j):
            ranks[ordered[k][0]] = avg_rank
        i = j
    return ranks


def _max_drawdown(returns: list[float]) -> float | None:
    if not returns:
        return None
    nav = 1.0
    peak = 1.0
    max_dd = 0.0
    for ret in returns:
        nav *= 1.0 + ret
        peak = max(peak, nav)
        if peak > 0:
            max_dd = min(max_dd, nav / peak - 1.0)
    return max_dd


def _rank_sort_key(row: dict[str, Any]) -> tuple[bool, float, str]:
    rank = _to_float(row.get("rank_in_date"))
    score = _to_float(row.get("pred_score"))
    return (
        rank is None and score is None,
        rank if rank is not None else -(score or 0.0),
        str(row.get("stock_code") or ""),
    )


def _industry_hhi(rows: list[dict[str, Any]]) -> float | None:
    if not rows:
        return None
    counts: dict[str, int] = defaultdict(int)
    for row in rows:
        industry = row.get("tdx_l1") or row.get("tdx_l1_name") or "_UNKNOWN"
        counts[str(industry)] += 1
    total = sum(counts.values())
    if total <= 0:
        return None
    return sum((count / total) ** 2 for count in counts.values())


def latest_model_id(conn) -> str:
    model_id, _fallback = select_default_model_id(conn)
    if model_id:
        return model_id
    row = conn.execute("SELECT model_id FROM mart_multidim_model ORDER BY created_at DESC LIMIT 1").fetchone()
    if not row:
        raise RuntimeError("mart_multidim_model has no records")
    return row[0]


def load_prediction_panel(
    conn,
    *,
    model_id: str,
    feature_table: str,
    feature_set_id: str | None,
    labels: list[str],
) -> list[dict[str, Any]]:
    if not _table_exists(conn, "mart_multidim_prediction"):
        raise RuntimeError("missing mart_multidim_prediction")
    if not _table_exists(conn, feature_table):
        raise RuntimeError(f"missing {feature_table}")

    missing_labels = [label for label in labels if not _has_column(conn, feature_table, label)]
    if missing_labels:
        raise RuntimeError(f"{feature_table} missing labels: {missing_labels}")

    table_sql = _quote_ident(feature_table)
    feature_filter = ""
    params: list[Any] = []
    if feature_set_id and _has_column(conn, feature_table, "feature_set_id"):
        feature_filter = "AND fp.feature_set_id = ?"
        params.append(feature_set_id)
    params.append(model_id)

    industry_join = ""
    industry_cols = "NULL AS tdx_l1, NULL AS tdx_l1_name"
    if _table_exists(conn, "dim_stock_tdx_industry"):
        industry_join = "LEFT JOIN dim_stock_tdx_industry ind ON ind.stock_code = p.stock_code"
        industry_cols = "ind.tdx_l1, ind.tdx_l1_name"

    label_sql = ", ".join(f"fp.{_quote_ident(label)} AS {_quote_ident(label)}" for label in labels)
    return _records_from_cursor(
        conn.execute(
            f"""
            SELECT p.model_id, p.stock_code, p.date, p.pred_score, p.rank_in_date,
                   {label_sql}, {industry_cols}
              FROM mart_multidim_prediction p
              JOIN {table_sql} fp
                ON fp.stock_code = p.stock_code
               AND fp.date = p.date
               {feature_filter}
              {industry_join}
             WHERE p.model_id = ?
             ORDER BY p.date, p.rank_in_date
            """,
            params,
        )
    )


def _daily_ic(rows_by_date: dict[str, list[dict[str, Any]]], label_name: str) -> tuple[float | None, float | None]:
    ic_values: list[float] = []
    rank_ic_values: list[float] = []
    for rows in rows_by_date.values():
        pairs = [
            (_to_float(row.get("pred_score")), _to_float(row.get(label_name)))
            for row in rows
        ]
        xs = [x for x, y in pairs if x is not None and y is not None]
        ys = [y for x, y in pairs if x is not None and y is not None]
        ic = _pearson(xs, ys)
        if ic is not None:
            ic_values.append(ic)
        rank_ic = _pearson(_ranks(xs), _ranks(ys)) if len(xs) >= 3 else None
        if rank_ic is not None:
            rank_ic_values.append(rank_ic)
    return _mean(ic_values), _mean(rank_ic_values)


def _recommendation(row: dict[str, Any]) -> str:
    if (row.get("after_cost_return") or 0.0) <= 0:
        return "unusable_after_cost"
    if (row.get("long_short_spread") or 0.0) <= 0:
        return "weak_topk_spread"
    if (row.get("winrate") or 0.0) < 0.50:
        return "watch_low_winrate"
    if (row.get("avg_turnover") or 0.0) > 0.80:
        return "watch_high_turnover"
    return "keep_candidate"


def evaluate_grid(
    rows: list[dict[str, Any]],
    *,
    model_id: str,
    feature_table: str,
    feature_set_id: str | None,
    horizons: list[int],
    top_sizes: list[int],
    cost_bps: float,
    run_id: str | None = None,
) -> list[dict[str, Any]]:
    built_at = datetime.utcnow().isoformat()
    run_id = run_id or f"holding_topk_{model_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    rows_by_date_all: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        rows_by_date_all[str(row.get("date"))].append(row)

    out: list[dict[str, Any]] = []
    for horizon in horizons:
        label_name = f"forward_ret_{horizon}d"
        rows_by_date = {
            date: sorted(
                [row for row in date_rows if _to_float(row.get(label_name)) is not None],
                key=_rank_sort_key,
            )
            for date, date_rows in rows_by_date_all.items()
        }
        rows_by_date = {date: date_rows for date, date_rows in rows_by_date.items() if date_rows}
        ic_mean, rank_ic_mean = _daily_ic(rows_by_date, label_name)
        for top_k in top_sizes:
            top_returns: list[float] = []
            benchmark_returns: list[float] = []
            spreads: list[float] = []
            selected_returns: list[float] = []
            turnovers: list[float] = []
            hhis: list[float] = []
            prev_codes: set[str] | None = None
            signal_count = 0
            for date in sorted(rows_by_date):
                date_rows = rows_by_date[date]
                top_rows = date_rows[:top_k]
                bottom_rows = date_rows[-top_k:]
                top_vals = [_to_float(row.get(label_name)) for row in top_rows]
                bottom_vals = [_to_float(row.get(label_name)) for row in bottom_rows]
                all_vals = [_to_float(row.get(label_name)) for row in date_rows]
                top_clean = [value for value in top_vals if value is not None]
                bottom_clean = [value for value in bottom_vals if value is not None]
                all_clean = [value for value in all_vals if value is not None]
                if not top_clean or not all_clean:
                    continue
                top_return = _mean(top_clean)
                benchmark_return = _mean(all_clean)
                bottom_return = _mean(bottom_clean)
                if top_return is None or benchmark_return is None or bottom_return is None:
                    continue
                top_returns.append(top_return)
                benchmark_returns.append(benchmark_return)
                spreads.append(top_return - bottom_return)
                selected_returns.extend(top_clean)
                signal_count += len(top_clean)
                hhi = _industry_hhi(top_rows)
                if hhi is not None:
                    hhis.append(hhi)
                codes = {str(row.get("stock_code")) for row in top_rows if row.get("stock_code")}
                if prev_codes is not None:
                    denom = max(len(codes), len(prev_codes), 1)
                    turnovers.append(1.0 - len(codes & prev_codes) / denom)
                prev_codes = codes

            row = {
                "run_id": run_id,
                "model_id": model_id,
                "feature_table": feature_table,
                "feature_set_id": feature_set_id,
                "label_name": label_name,
                "holding_period": horizon,
                "top_k": top_k,
                "cost_bps": cost_bps,
                "n_dates": len(top_returns),
                "n_signals": signal_count,
                "ic_mean": ic_mean,
                "rank_ic_mean": rank_ic_mean,
                "avg_top_return": _mean(top_returns),
                "avg_benchmark_return": _mean(benchmark_returns),
                "avg_excess_return": _mean([a - b for a, b in zip(top_returns, benchmark_returns)]),
                "long_short_spread": _mean(spreads),
                "winrate": _mean([1.0 if value > 0 else 0.0 for value in selected_returns]),
                "avg_turnover": _mean(turnovers) or 0.0,
                "max_drawdown": _max_drawdown(top_returns),
                "avg_industry_hhi": _mean(hhis),
                "built_at": built_at,
                "notes": json.dumps({"cost_model": "avg_top_return - avg_turnover * cost_bps / 10000"}, ensure_ascii=False),
            }
            row["after_cost_return"] = (
                None if row["avg_top_return"] is None
                else row["avg_top_return"] - (row["avg_turnover"] or 0.0) * cost_bps / 10000.0
            )
            row["recommendation"] = _recommendation(row)
            out.append(row)
    return out


def write_results(conn, rows: list[dict[str, Any]]) -> None:
    conn.executescript(DDL)
    if not rows:
        return
    run_id = rows[0]["run_id"]
    conn.execute("DELETE FROM mart_model_holding_topk_eval WHERE run_id = ?", (run_id,))
    cols = [
        "run_id", "model_id", "feature_table", "feature_set_id", "label_name",
        "holding_period", "top_k", "cost_bps", "n_dates", "n_signals",
        "ic_mean", "rank_ic_mean", "avg_top_return", "avg_benchmark_return",
        "avg_excess_return", "long_short_spread", "winrate", "avg_turnover",
        "max_drawdown", "after_cost_return", "avg_industry_hhi", "recommendation",
        "notes", "built_at",
    ]
    conn.executemany(
        f"INSERT INTO mart_model_holding_topk_eval ({', '.join(cols)}) VALUES ({', '.join(['?'] * len(cols))})",
        [tuple(row.get(col) for col in cols) for row in rows],
    )
    conn.commit()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-id", default=None)
    parser.add_argument("--feature-table", default="fact_feature_panel_candidate")
    parser.add_argument("--feature-set-id", default="tdx_f10_gpcw_v1")
    parser.add_argument("--horizons", default="5,10,20,60")
    parser.add_argument("--top-sizes", default="20,50,100,200,500")
    parser.add_argument("--cost-bps", type=float, default=30.0)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    started_at = utc_now_iso()
    conn = get_conn()
    try:
        model_id = args.model_id or latest_model_id(conn)
        horizons = _parse_csv_ints(args.horizons)
        top_sizes = _parse_csv_ints(args.top_sizes)
        labels = [f"forward_ret_{horizon}d" for horizon in horizons]
        panel = load_prediction_panel(
            conn,
            model_id=model_id,
            feature_table=args.feature_table,
            feature_set_id=args.feature_set_id,
            labels=labels,
        )
        rows = evaluate_grid(
            panel,
            model_id=model_id,
            feature_table=args.feature_table,
            feature_set_id=args.feature_set_id,
            horizons=horizons,
            top_sizes=top_sizes,
            cost_bps=args.cost_bps,
        )
        if not args.dry_run:
            write_results(conn, rows)
        ended_at = utc_now_iso()
        record_pipeline_run(
            conn,
            run_id=rows[0]["run_id"] if rows else f"holding_topk_empty_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
            pipeline_name="evaluate_holding_topk",
            status="success",
            started_at=started_at,
            ended_at=ended_at,
            duration_s=(datetime.fromisoformat(ended_at) - datetime.fromisoformat(started_at)).total_seconds(),
            commit_sha=git_commit_sha(REPO),
            input_tables=["mart_multidim_prediction", args.feature_table],
            output_tables=[] if args.dry_run else ["mart_model_holding_topk_eval"],
            model_id=model_id,
            blockers=[],
            perf_summary={
                "rows_loaded": len(panel),
                "rows_evaluated": len(rows),
                "horizons": horizons,
                "top_sizes": top_sizes,
                "dry_run": args.dry_run,
            },
        )
        logger.info("holding/topK evaluation rows=%d model=%s", len(rows), model_id)
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
