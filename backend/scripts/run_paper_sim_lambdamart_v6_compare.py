#!/usr/bin/env python3
"""Run historical paper_sim comparison for LambdaMART v6 versus v4 baseline."""
from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.db import get_conn
from services.paper_sim.config import load_config
from scripts.backfill_strategy_result_registry import backfill as refresh_strategy_result_registry
from scripts.run_paper_sim_v2 import run_walk_forward


logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("paper_sim_lambdamart_v6_compare")

COMPARE_TABLE = "mart_paper_sim_lambdamart_v6_kpi_compare"
LABEL_COLUMNS = {"fwd_cost_after_5d", "fwd_cost_after_10d", "fwd_cost_after_20d"}

COMPARE_DDL = f"""
CREATE TABLE IF NOT EXISTS {COMPARE_TABLE} (
    comparison_id       TEXT NOT NULL,
    model_label         TEXT NOT NULL,
    model_id            TEXT NOT NULL,
    prediction_table    TEXT NOT NULL,
    sim_run_id          TEXT NOT NULL,
    period_start        TEXT NOT NULL,
    period_end          TEXT NOT NULL,
    rank_ic             DOUBLE,
    rank_ic_n_dates     INTEGER,
    sharpe              DOUBLE,
    ann_ret             DOUBLE,
    max_dd              DOUBLE,
    monthly_win_rate    DOUBLE,
    source_kpi_built_at TIMESTAMP,
    built_at            TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (comparison_id, model_label)
);
"""


@dataclass(frozen=True)
class ModelSpec:
    label: str
    model_id: str
    prediction_table: str
    config_path: Path


@dataclass(frozen=True)
class CompareRow:
    model_label: str
    model_id: str
    prediction_table: str
    sim_run_id: str
    period_start: str
    period_end: str
    rank_ic: float | None
    rank_ic_n_dates: int
    sharpe: float | None
    ann_ret: float | None
    max_dd: float | None
    monthly_win_rate: float | None
    source_kpi_built_at: object


def _validate_identifier(name: str) -> str:
    if not name.replace("_", "").isalnum() or not (name[0].isalpha() or name[0] == "_"):
        raise ValueError(f"invalid SQL identifier: {name}")
    return name


def ensure_compare_table(conn) -> None:
    conn.execute(COMPARE_DDL)


def table_exists(conn, table: str) -> bool:
    row = conn.execute(
        """
        SELECT 1
          FROM information_schema.tables
         WHERE table_schema = 'main' AND table_name = ?
         LIMIT 1
        """,
        [table],
    ).fetchone()
    return row is not None


def prediction_coverage(conn, *, table: str, model_id: str, start: str, end: str) -> dict:
    table = _validate_identifier(table)
    if not table_exists(conn, table):
        return {"n_rows": 0, "min_date": None, "max_date": None, "n_dates": 0}
    row = conn.execute(
        f"""
        SELECT COUNT(*) AS n_rows,
               MIN(signal_date) AS min_date,
               MAX(signal_date) AS max_date,
               COUNT(DISTINCT signal_date) AS n_dates
          FROM {table}
         WHERE model_id = ?
           AND signal_date >= CAST(? AS DATE)
           AND signal_date <= CAST(? AS DATE)
        """,
        [model_id, start, end],
    ).fetchone()
    return {
        "n_rows": int(row["n_rows"] or 0),
        "min_date": str(row["min_date"]) if row["min_date"] is not None else None,
        "max_date": str(row["max_date"]) if row["max_date"] is not None else None,
        "n_dates": int(row["n_dates"] or 0),
    }


def assert_prediction_data(conn, spec: ModelSpec, *, start: str, end: str) -> None:
    coverage = prediction_coverage(
        conn,
        table=spec.prediction_table,
        model_id=spec.model_id,
        start=start,
        end=end,
    )
    if coverage["n_rows"] <= 0 or coverage["n_dates"] <= 0:
        raise RuntimeError(
            f"no prediction rows for {spec.label}: table={spec.prediction_table} "
            f"model_id={spec.model_id} period={start}..{end}"
        )
    log.info(
        "%s coverage: rows=%s dates=%s range=%s..%s",
        spec.label,
        f"{coverage['n_rows']:,}",
        coverage["n_dates"],
        coverage["min_date"],
        coverage["max_date"],
    )


def compute_rank_ic(conn, *, table: str, model_id: str, label_col: str, start: str, end: str) -> tuple[float | None, int]:
    table = _validate_identifier(table)
    if label_col not in LABEL_COLUMNS:
        raise ValueError(f"unsupported label_col={label_col}")
    row = conn.execute(
        f"""
        WITH ranked AS (
            SELECT signal_date,
                   PERCENT_RANK() OVER (PARTITION BY signal_date ORDER BY score) AS score_rank,
                   PERCENT_RANK() OVER (PARTITION BY signal_date ORDER BY {label_col}) AS label_rank
              FROM {table}
             WHERE model_id = ?
               AND signal_date >= CAST(? AS DATE)
               AND signal_date <= CAST(? AS DATE)
               AND score IS NOT NULL
               AND {label_col} IS NOT NULL
        ),
        per_date AS (
            SELECT signal_date,
                   CORR(score_rank, label_rank) AS rank_ic,
                   COUNT(*) AS n
              FROM ranked
             GROUP BY signal_date
            HAVING COUNT(*) >= 2
        )
        SELECT AVG(rank_ic) AS rank_ic,
               COUNT(rank_ic) AS n_dates
          FROM per_date
         WHERE rank_ic IS NOT NULL
        """,
        [model_id, start, end],
    ).fetchone()
    n_dates = int(row["n_dates"] or 0)
    rank_ic = float(row["rank_ic"]) if row["rank_ic"] is not None else None
    return rank_ic, n_dates


def load_kpi_row(conn, sim_run_id: str) -> dict:
    row = conn.execute(
        """
        SELECT sim_run_id, period_start, period_end, annual_return, max_dd,
               sharpe, monthly_win_rate, built_at
          FROM mart_paper_sim_kpi
         WHERE sim_run_id = ?
        """,
        [sim_run_id],
    ).fetchone()
    if row is None:
        raise RuntimeError(f"mart_paper_sim_kpi missing sim_run_id={sim_run_id}")
    return {
        "period_start": row["period_start"],
        "period_end": row["period_end"],
        "ann_ret": row["annual_return"],
        "max_dd": row["max_dd"],
        "sharpe": row["sharpe"],
        "monthly_win_rate": row["monthly_win_rate"],
        "built_at": row["built_at"],
    }


def write_compare_rows(conn, *, comparison_id: str, rows: list[CompareRow]) -> None:
    ensure_compare_table(conn)
    conn.execute(f"DELETE FROM {COMPARE_TABLE} WHERE comparison_id = ?", [comparison_id])
    conn.executemany(
        f"""
        INSERT INTO {COMPARE_TABLE}
        (comparison_id, model_label, model_id, prediction_table, sim_run_id,
         period_start, period_end, rank_ic, rank_ic_n_dates, sharpe,
         ann_ret, max_dd, monthly_win_rate, source_kpi_built_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            [
                comparison_id,
                row.model_label,
                row.model_id,
                row.prediction_table,
                row.sim_run_id,
                row.period_start,
                row.period_end,
                row.rank_ic,
                row.rank_ic_n_dates,
                row.sharpe,
                row.ann_ret,
                row.max_dd,
                row.monthly_win_rate,
                row.source_kpi_built_at,
            ]
            for row in rows
        ],
    )
    conn.commit()
    refresh_strategy_result_registry(conn, dry_run=False)


def _fmt_pct(value: float | None) -> str:
    return "NA" if value is None else f"{value * 100:.2f}%"


def _fmt_num(value: float | None) -> str:
    return "NA" if value is None else f"{value:.4f}"


def print_compare_table(rows: list[CompareRow]) -> None:
    print("| model | RankIC | Sharpe | ann_ret | max_dd | monthly win-rate |")
    print("|---|---:|---:|---:|---:|---:|")
    for row in rows:
        print(
            f"| {row.model_label} | {_fmt_num(row.rank_ic)} | {_fmt_num(row.sharpe)} | "
            f"{_fmt_pct(row.ann_ret)} | {_fmt_pct(row.max_dd)} | {_fmt_pct(row.monthly_win_rate)} |"
        )


def run_one_model(
    spec: ModelSpec,
    *,
    comparison_id: str,
    start: str,
    end: str,
    label_col: str,
) -> CompareRow:
    cfg = load_config(
        path=spec.config_path,
        override={
            "selection": {
                "ml_score_model_id": spec.model_id,
                "ml_score_prediction_table": spec.prediction_table,
            }
        },
    )
    sim_run_id = f"{comparison_id}_{spec.label}"
    run_walk_forward(spec.label, start, end, cfg, sim_run_id=sim_run_id)

    conn = get_conn()
    try:
        rank_ic, n_dates = compute_rank_ic(
            conn,
            table=spec.prediction_table,
            model_id=spec.model_id,
            label_col=label_col,
            start=start,
            end=end,
        )
        kpi = load_kpi_row(conn, sim_run_id)
    finally:
        conn.close()

    return CompareRow(
        model_label=spec.label,
        model_id=spec.model_id,
        prediction_table=spec.prediction_table,
        sim_run_id=sim_run_id,
        period_start=kpi["period_start"],
        period_end=kpi["period_end"],
        rank_ic=rank_ic,
        rank_ic_n_dates=n_dates,
        sharpe=kpi["sharpe"],
        ann_ret=kpi["ann_ret"],
        max_dd=kpi["max_dd"],
        monthly_win_rate=kpi["monthly_win_rate"],
        source_kpi_built_at=kpi["built_at"],
    )


def main() -> int:
    repo = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description="Compare paper_sim KPIs for LambdaMART v6 and v4 baseline")
    parser.add_argument("--start", default="2024-07-01")  # rule-compliance: ok evidence=p0b-walk-forward-起始
    parser.add_argument("--end", default="2026-04-13")     # rule-compliance: ok evidence=panel-cutoff
    parser.add_argument("--label-col", default="fwd_cost_after_20d", choices=sorted(LABEL_COLUMNS))
    parser.add_argument("--comparison-id", default=None)
    parser.add_argument("--baseline-config", default=str(repo / "backend/config/paper_sim_ml_score_governance_v1.yaml"))
    parser.add_argument("--lambdamart-config", default=str(repo / "backend/config/paper_sim_ml_score_lambdamart_v6.yaml"))
    parser.add_argument("--baseline-model-id", default="lgbm_20260517_governance_v1_20d")
    parser.add_argument("--baseline-prediction-table", default="mart_p0b_oos_predictions")
    parser.add_argument("--lambdamart-model-id", default="lambdamart_v6_20260518")
    parser.add_argument("--lambdamart-prediction-table", default="mart_p0b_lambdamart_v6_predictions")
    args = parser.parse_args()

    comparison_id = args.comparison_id or f"lm_v6_compare_{datetime.now(UTC).strftime('%Y%m%dT%H%M%S')}"
    specs = [
        ModelSpec(
            label="v4_baseline",
            model_id=args.baseline_model_id,
            prediction_table=args.baseline_prediction_table,
            config_path=Path(args.baseline_config),
        ),
        ModelSpec(
            label="lambdamart_v6",
            model_id=args.lambdamart_model_id,
            prediction_table=args.lambdamart_prediction_table,
            config_path=Path(args.lambdamart_config),
        ),
    ]

    conn = get_conn()
    try:
        for spec in specs:
            assert_prediction_data(conn, spec, start=args.start, end=args.end)
    finally:
        conn.close()

    rows = [
        run_one_model(spec, comparison_id=comparison_id, start=args.start, end=args.end, label_col=args.label_col)
        for spec in specs
    ]

    conn = get_conn()
    try:
        write_compare_rows(conn, comparison_id=comparison_id, rows=rows)
    finally:
        conn.close()

    print_compare_table(rows)
    print(f"comparison_id={comparison_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
