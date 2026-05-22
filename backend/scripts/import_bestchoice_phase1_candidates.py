#!/usr/bin/env python3
"""Import BestChoice formula-optuna replacement candidates into the main project mart.

BestChoice Phase 1: import 1146 dry-run replacement candidates as read-only challenger
evidence. Schema follows plan §5 (bestchoice/analysis/bestchoice_chunkymonkey_validation_plan.md).

Target table: mart_stock_formula_optuna_bestchoice_v1
Source CSV:   bestchoice/analysis/formula_local_optuna_batch_stock_best_replacements.csv

Run ID:       bestchoice_formula_optuna_20260521_v1
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from services.duck_adapter import connect  # noqa: E402

DEFAULT_CSV = (
    REPO_ROOT.parent
    / "bestchoice"
    / "analysis"
    / "formula_local_optuna_batch_stock_best_replacements.csv"
)

DEFAULT_RUN_ID = "bestchoice_formula_optuna_20260521_v1"
# rule-compliance: ok evidence=BestChoice source CSV latest_data_date metadata
DEFAULT_DATA_LATEST = "2026-05-19"


def _holding_days(s: str) -> int | None:
    s = (s or "").strip()
    if s.startswith("fixed_"):
        try:
            return int(s.replace("fixed_", ""))
        except ValueError:
            return None
    if s.isdigit():
        return int(s)
    return None


def _opt_float(s: str) -> float | None:
    if not s or s.strip() == "":
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _opt_int(s: str) -> int | None:
    if not s or s.strip() == "":
        return None
    try:
        return int(s)
    except ValueError:
        return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv-path", default=str(DEFAULT_CSV))
    parser.add_argument("--db-path", default=str(REPO_ROOT / "data" / "smartmoney.duckdb"))
    parser.add_argument("--run-id", default=DEFAULT_RUN_ID)
    parser.add_argument("--data-latest", default=DEFAULT_DATA_LATEST)
    args = parser.parse_args()

    csv_path = Path(args.csv_path)
    if not csv_path.exists():
        print(f"ERROR: CSV not found: {csv_path}", file=sys.stderr)
        return 1

    rows: list[tuple] = []
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    with open(csv_path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            params_str = row.get("params", "{}")
            params_json = params_str if params_str.startswith("{") else "{}"
            params_hash = hashlib.sha256(params_json.encode()).hexdigest()[:16]
            # Validation metrics may not exist in this CSV (only train+oos in summary CSV);
            # the adoption CSV at analysis/formula_local_optuna_adoption_candidates.csv has them
            rows.append(
                (
                    args.run_id,
                    row["stock_code"],
                    row["formula_id"],
                    row["variant_id"],
                    params_json,
                    params_hash,
                    row["sell_rule"],
                    _holding_days(row.get("holding_days", "")),
                    _opt_int(row.get("signal_count", "")),
                    _opt_float(row.get("win_rate", "")),
                    _opt_float(row.get("avg_ret", "")),
                    _opt_float(row.get("avg_dd", "")),
                    _opt_float(row.get("calmar", "")),
                    _opt_float(row.get("delay_buy_rate", "")),
                    _opt_float(row.get("delay_sell_rate", "")),
                    _opt_float(row.get("score", "")),
                    _opt_int(row.get("validation_signal_count", "")),
                    _opt_float(row.get("validation_win_rate", "")),
                    _opt_float(row.get("validation_avg_ret", "")),
                    _opt_float(row.get("validation_score", "")),
                    _opt_float(row.get("score_delta", "")),
                    _opt_float(row.get("validation_score_delta", "")),
                    str(csv_path),
                    args.data_latest,
                    now_str,
                )
            )

    if not rows:
        print("ERROR: 0 rows parsed from CSV", file=sys.stderr)
        return 1

    with connect(args.db_path, read_only=False) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS mart_stock_formula_optuna_bestchoice_v1 (
                run_id VARCHAR,
                stock_code VARCHAR,
                formula_id VARCHAR,
                variant_id VARCHAR,
                params_json VARCHAR,
                params_hash VARCHAR,
                sell_rule VARCHAR,
                holding_days INTEGER,
                signal_count INTEGER,
                win_rate DOUBLE,
                avg_ret DOUBLE,
                avg_dd DOUBLE,
                calmar DOUBLE,
                delay_buy_rate DOUBLE,
                delay_sell_rate DOUBLE,
                score DOUBLE,
                validation_signal_count INTEGER,
                validation_win_rate DOUBLE,
                validation_avg_ret DOUBLE,
                validation_score DOUBLE,
                score_delta DOUBLE,
                validation_score_delta DOUBLE,
                source_artifact VARCHAR,
                source_data_latest_date DATE,
                created_at TIMESTAMP
            )
            """
        )
        conn.execute(
            "DELETE FROM mart_stock_formula_optuna_bestchoice_v1 WHERE run_id = ?",
            [args.run_id],
        )
        conn.executemany(
            """
            INSERT INTO mart_stock_formula_optuna_bestchoice_v1 (
                run_id, stock_code, formula_id, variant_id, params_json, params_hash,
                sell_rule, holding_days, signal_count, win_rate, avg_ret, avg_dd, calmar,
                delay_buy_rate, delay_sell_rate, score,
                validation_signal_count, validation_win_rate, validation_avg_ret,
                validation_score, score_delta, validation_score_delta,
                source_artifact, source_data_latest_date, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        conn.commit()

        audit = conn.execute(
            """
            SELECT COUNT(*) AS n_rows,
                   COUNT(DISTINCT stock_code) AS n_stocks,
                   COUNT(DISTINCT formula_id) AS n_formulas,
                   COUNT(DISTINCT variant_id) AS n_variants,
                   MIN(score), MAX(score), AVG(score),
                   AVG(win_rate)
              FROM mart_stock_formula_optuna_bestchoice_v1
             WHERE run_id = ?
            """,
            [args.run_id],
        ).fetchone()
        n_rows = audit[0]
        print(f"[OK] imported {n_rows} candidates (run_id={args.run_id})")
        print(f"  stocks={audit[1]} formulas={audit[2]} variants={audit[3]}")
        print(f"  score range [{audit[4]:.2f}, {audit[5]:.2f}] mean={audit[6]:.2f}")
        print(f"  win_rate mean={audit[7]:.4f}")
        print(f"  source: {csv_path}")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
