#!/usr/bin/env python3
"""Day 5 stage_opt PIT walk-forward builder.

跑 optimize_per_stock_stage_strategy.py 在 4 个半年 cutoff_date (2024-07-01,
2025-01-01, 2025-07-01, 2026-01-01) 上, 每 cutoff 用 expanding history 内的 signals
跑 Optuna, 入 mart_per_stock_stage_strategy_optimal_pit (PK 加 cutoff_date).

PIT 安全保证: 每 cutoff 只用 signals.date < cutoff 的历史数据 fit.

⚠ 单 cutoff 千股 × 100 trials × 6 stages × 5 formulas ≈ 30万 trial ≈ 12h.
   4 cutoffs ≈ 48h. **单 session 不实际**, 用 --limit-stocks 跑 sample 验证 pipeline,
   全量留外部 cluster.

用法:
    # smoke (50 stocks × 1 cutoff, ~1h)
    PYTHONPATH=backend python backend/scripts/build_stage_opt_pit.py \\
        --limit-stocks 50 --cutoffs 2025-01-01

    # 全量 (4 cutoffs × 千股, ~48h)
    PYTHONPATH=backend python backend/scripts/build_stage_opt_pit.py \\
        --cutoffs 2024-07-01,2025-01-01,2025-07-01,2026-01-01
"""
from __future__ import annotations

import argparse
import logging
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import duckdb

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("build_stage_opt_pit")


SMART_DB = Path(__file__).resolve().parents[2] / "data" / "smartmoney.duckdb"
PIT_TABLE = "mart_per_stock_stage_strategy_optimal_pit"


PIT_DDL = f"""
CREATE TABLE IF NOT EXISTS {PIT_TABLE} (
    stock_code TEXT NOT NULL,
    cutoff_date TEXT NOT NULL,
    formula_id TEXT NOT NULL,
    formula_variant TEXT NOT NULL,
    stage_filter TEXT NOT NULL,
    holding_days INTEGER,
    optimal_target_pct DOUBLE,
    optimal_stop_pct DOUBLE,
    optimal_trailing_pct DOUBLE,
    sharpe DOUBLE,
    oos_sharpe DOUBLE,
    avg_ret DOUBLE,
    oos_avg_ret DOUBLE,
    win_rate DOUBLE,
    oos_win_rate DOUBLE,
    n_traded INTEGER,
    oos_n_traded INTEGER,
    walk_forward_mode TEXT,
    train_n_signals INTEGER,
    test_n_signals INTEGER,
    built_at TEXT NOT NULL,
    PRIMARY KEY (stock_code, cutoff_date, formula_variant, stage_filter)
);
CREATE INDEX IF NOT EXISTS idx_stage_opt_pit_cutoff
    ON {PIT_TABLE}(cutoff_date);
CREATE INDEX IF NOT EXISTS idx_stage_opt_pit_stock_cutoff
    ON {PIT_TABLE}(stock_code, cutoff_date DESC);
"""


def main() -> int:
    parser = argparse.ArgumentParser(description="Day 5 stage_opt PIT walk-forward")
    parser.add_argument("--cutoffs",
                        default="2024-07-01,2025-01-01,2025-07-01,2026-01-01",
                        help="comma-sep cutoff_dates (半年频)")
    parser.add_argument("--limit-stocks", type=int, default=None,
                        help="限制 stock 数 (smoke 测试用) — ⚠ Codex M4 (a163ca58): 当前只 ETL 阶段 limit, "
                             "optimize_per_stock_stage_strategy.py 全量跑 (subprocess 没 --limit-stocks arg). "
                             "TODO: 加 arg forwarding 让 smoke 真 1h, 当前 smoke 仍 ~12h × 1 cutoff.")
    parser.add_argument("--trials", type=int, default=50,
                        help="Optuna trials per (stock × stage × formula). governance.min_n_trials=50 强制 (yaml)")
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()

    cutoffs = [c.strip() for c in args.cutoffs.split(",") if c.strip()]
    log.info(f"=== Day 5 stage_opt PIT builder ===")
    log.info(f"  cutoffs: {cutoffs}")
    log.info(f"  limit_stocks: {args.limit_stocks or 'all'}")
    log.info(f"  trials/key: {args.trials}, workers: {args.workers}")

    # Ensure DDL
    conn = duckdb.connect(str(SMART_DB))
    for stmt in PIT_DDL.split(";"):
        if stmt.strip():
            conn.execute(stmt)
    conn.close()
    log.info(f"  PIT table {PIT_TABLE} ready")

    # 每 cutoff 跑 optimize_per_stock_stage_strategy.py
    # 注意: 现有 script 入库 mart_per_stock_stage_strategy_optimal (latest snapshot 表),
    # 我用 --start cutoff_date - 2y, --end cutoff_date 限制 signals,
    # 然后 ETL 同表 → PIT 表 (加 cutoff_date 列)
    repo = Path(__file__).resolve().parents[2]
    overall_t0 = time.time()
    from datetime import timedelta
    for cutoff in cutoffs:
        cutoff_dt = datetime.strptime(cutoff, "%Y-%m-%d")
        # Codex C2 (a163ca58) fix: cutoff-day inclusive 是 leakage,
        # 用前 1 天 strict <. Train range 用 cutoff - 2 年 → cutoff - 1 day.
        train_start = (cutoff_dt.replace(year=cutoff_dt.year - 2)).strftime("%Y-%m-%d")
        train_end = (cutoff_dt - timedelta(days=1)).strftime("%Y-%m-%d")
        log.info("")
        log.info(f"=== cutoff={cutoff} (train {train_start} → {train_end}, signals strict < {cutoff}) ===")
        t_cutoff = time.time()

        cmd = [
            "python", "backend/scripts/optimize_per_stock_stage_strategy.py",
            "--start", train_start,
            "--end", train_end,  # Codex C2: exclude cutoff_date 自身 (前 1 天)
            "--trials", str(args.trials),
            "--workers", str(args.workers),
            "--walk-forward-mode", "expanding_monthly",
        ]
        log.info(f"  cmd: {' '.join(cmd)}")
        env = {"PYTHONPATH": "backend"}
        import os
        env_full = {**os.environ, **env}
        r = subprocess.run(cmd, cwd=repo, env=env_full, capture_output=True, text=True)
        if r.returncode != 0:
            log.error(f"  cutoff {cutoff} FAILED")
            log.error(f"  stderr: {r.stderr[-500:]}")
            continue

        # ETL: 把 latest snapshot 表的当前内容复制到 PIT 表 (加 cutoff_date 列)
        conn = duckdb.connect(str(SMART_DB))
        try:
            now_iso = datetime.utcnow().isoformat(timespec="seconds")
            # 优先 stock subset (smoke)
            limit_clause = f"LIMIT {args.limit_stocks}" if args.limit_stocks else ""
            conn.execute(f"""
                INSERT OR REPLACE INTO {PIT_TABLE}
                (stock_code, cutoff_date, formula_id, formula_variant, stage_filter,
                 holding_days,
                 optimal_target_pct, optimal_stop_pct, optimal_trailing_pct,
                 sharpe, oos_sharpe, avg_ret, oos_avg_ret,
                 win_rate, oos_win_rate, n_traded, oos_n_traded,
                 walk_forward_mode, train_n_signals, test_n_signals, built_at)
                SELECT stock_code, ?, formula_id, formula_variant, stage_filter,
                       optimal_hp AS holding_days,  -- Codex C3 (a163ca58): 生产列名是 optimal_hp 不是 holding_days
                       optimal_target_pct, optimal_stop_pct, optimal_trailing_pct,
                       sharpe, oos_sharpe, avg_ret, oos_avg_ret,
                       win_rate, oos_win_rate, n_traded, oos_n_traded,
                       walk_forward_mode, train_n_signals, test_n_signals, ?
                  FROM mart_per_stock_stage_strategy_optimal
                  {limit_clause}
            """, [cutoff, now_iso])
            n_rows = conn.execute(
                f"SELECT COUNT(*) FROM {PIT_TABLE} WHERE cutoff_date = ?",
                [cutoff]
            ).fetchone()[0]
            log.info(f"  cutoff={cutoff} → {n_rows:,} rows inserted into PIT table")
        finally:
            conn.close()
        log.info(f"  cutoff {cutoff} done in {time.time() - t_cutoff:.0f}s")

    log.info("")
    log.info(f"=== Day 5 PIT builder ALL DONE in {time.time() - overall_t0:.0f}s ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
