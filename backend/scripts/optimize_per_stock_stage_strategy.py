"""Phase η+++++++ + ψ — 每股每公式每形态 Optuna 寻优 (Config-driven).

⚠ 用户要求: "每个股票每种形态下每个公式下都单独选优"
⚠ PK: (stock × formula_variant × stage_filter)
⚠ 寻优 9 维 (hp/stop/target/trail/offset + 4 K 线阈值)

⚠ Phase ψ 强制 walk-forward / OOS 验证 + 全部参数走 backend/config/optuna_config.yaml.
   - DDL 走 services.optimization.ddl.ensure_optuna_tables
   - 治理走 services.optimization.governance.enforce_pre_insert
   - 切分走 services.optimization.walk_forward (default = expanding_monthly = R1)
   - reject 写 fact_optuna_governance_log 审计表

⚠ Rule 7: 业务代码不许 hardcode n_trials / seed / 表名 / mode — 全走 yaml.

usage:
  PYTHONPATH=backend python backend/scripts/optimize_per_stock_stage_strategy.py
  # 走 cfg 默认 (n_trials=100, walk_forward=expanding_monthly, 8 workers)

  # 也可 CLI override (单独调试 / 一次性 sweep)
  PYTHONPATH=backend python backend/scripts/optimize_per_stock_stage_strategy.py \\
      --trials 200 --walk-forward-mode holdout
"""
from __future__ import annotations

import argparse
import json
import logging
import multiprocessing as mp
import time
import uuid
from collections import defaultdict
from datetime import date as _date
from pathlib import Path

import duckdb

from services.backtest.filters import is_index_code
from services.backtest.optimize import optimize_stock_strategy
from services.backtest.realistic_engine import Bar
from services.backtest.search_space import DEFAULT_SEARCH_SPACE
from services.optimization.config import get_optuna_config
from services.optimization.ddl import ensure_optuna_tables, log_governance_violations
from services.optimization.governance import enforce_pre_insert, GovernanceViolation
from services.trading_config import EXECUTION_MODEL


log = logging.getLogger("optimize_per_stock_stage_strategy")
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s",
                    datefmt="%H:%M:%S")


MARKET_DB = Path(__file__).resolve().parents[2] / "data" / "market.duckdb"
SMART_DB  = Path(__file__).resolve().parents[2] / "data" / "smartmoney.duckdb"


# Worker 共享状态 (fork mode)
_WORKER_BARS: dict = {}
_WORKER_N_TRIALS: int = 100
_WORKER_WF_MODE: str = "expanding_monthly"


def _init_worker(bars: dict, n_trials: int, wf_mode: str):
    global _WORKER_BARS, _WORKER_N_TRIALS, _WORKER_WF_MODE
    _WORKER_BARS = bars
    _WORKER_N_TRIALS = n_trials
    _WORKER_WF_MODE = wf_mode


def _worker_optimize(task: tuple) -> tuple | None:
    """(sc, fid, fvar, stage_filter, sig_list) → out_row or None."""
    sc, fid, fvar, stage_filter, sig_list = task
    result = optimize_stock_strategy(
        stock_code=sc, formula_id=fid, formula_variant=fvar,
        signals=sig_list, bars_by_stock=_WORKER_BARS,
        n_trials=_WORKER_N_TRIALS, search_space=DEFAULT_SEARCH_SPACE,
        walk_forward_mode=_WORKER_WF_MODE,
    )
    if result is None:
        return None
    s = result.summary
    return (
        result.stock_code, result.formula_id, result.formula_variant, stage_filter,
        result.optimal_hp, result.optimal_stop_pct,
        result.optimal_target_pct, result.optimal_trailing_pct,
        result.optimal_buy_offset,
        result.optimal_body_ratio_min, result.optimal_lower_shadow_min,
        result.optimal_close_position_min, result.optimal_volume_relative_min,
        # in-sample (train) metrics
        result.optimal_calmar, result.optimal_sortino,
        result.optimal_pain_index, result.optimal_ulcer_index,
        result.optimal_tail_risk, result.optimal_stability,
        result.n_signals_input, s.n_traded, s.n_blocked,
        s.win_rate, s.avg_ret, s.median_ret, s.avg_max_dd, s.avg_holding_days,
        s.sharpe, s.calmar,
        s.exit_distribution.get("stop_loss", 0.0),
        s.exit_distribution.get("trailing_stop", 0.0),
        s.exit_distribution.get("target_hit", 0.0),
        s.exit_distribution.get("hp_expired", 0.0),
        s.exit_distribution.get("one_word_blocked", 0.0),
        s.exit_distribution.get("data_truncated", 0.0),
        result.score, result.optuna_n_trials,
        # Phase ψ OOS metrics (聚合后, R1 expanding_monthly 多窗已合并)
        result.walk_forward_mode,
        result.train_n_signals, result.test_n_signals,
        result.oos_sharpe, result.oos_win_rate, result.oos_avg_ret,
        result.oos_n_traded, result.oos_period_start, result.oos_period_end,
        result.oos_n_windows, result.oos_monthly_sharpe_std,
        EXECUTION_MODEL.version,
    )


def main():
    cfg = get_optuna_config()

    parser = argparse.ArgumentParser()
    parser.add_argument("--trials", type=int, default=cfg.execution.n_trials,
                        help=f"Optuna trials (默认 cfg.execution.n_trials={cfg.execution.n_trials})")
    parser.add_argument("--start",  default="2023-01-01")
    parser.add_argument("--end",    default=None,
                        help="默认 calendar-gated latest_closed_trade_date")
    parser.add_argument("--workers", type=int, default=cfg.execution.n_workers,
                        help=f"workers (默认 cfg.execution.n_workers={cfg.execution.n_workers})")
    parser.add_argument("--min-signals", type=int, default=cfg.execution.sample_min,
                        help=f"(stock × variant × stage) 至少这么多信号才寻优 "
                             f"(默认 cfg.execution.sample_min={cfg.execution.sample_min})")
    parser.add_argument("--walk-forward-mode",
                        default=cfg.walk_forward.default_mode,
                        choices=["expanding_monthly", "expanding", "holdout", "none"],
                        help=f"时序切分模式 (默认 cfg.walk_forward.default_mode="
                             f"{cfg.walk_forward.default_mode}). "
                             f"none=旧 in-sample fit (governance 拒入业务表, 仅调试).")
    args = parser.parse_args()

    if args.end is None:
        from services.utils import latest_closed_or_raise
        args.end = latest_closed_or_raise()

    run_id = uuid.uuid4().hex[:12]
    t0 = time.time()
    log.info(f"=== Phase ψ η+++++++ A 路线 — 每股每公式每形态 Optuna (run_id={run_id}) ===")
    log.info(f"  trials={args.trials}, workers={args.workers}, min_signals={args.min_signals}")
    log.info(f"  walk_forward_mode={args.walk_forward_mode} (R1 expanding_monthly = 推荐)")
    log.info(f"  execution_model: {EXECUTION_MODEL.version}")
    log.info(f"  config: backend/config/optuna_config.yaml (Rule 7)")

    # 1. 加载 signals × signal_context.technical_stage
    mkt = duckdb.connect(str(MARKET_DB), read_only=True)
    mkt.execute(f"ATTACH '{SMART_DB}' AS sm (READ_ONLY)")
    log.info("加载 signals × context.stage ...")
    sigs = mkt.execute(
        """SELECT t.stock_code, t.date, t.formula_id, t.formula_variant,
                  COALESCE(c.technical_stage, '?') AS stage
             FROM sm.fact_technical_trigger t
             LEFT JOIN sm.fact_signal_context c
               ON c.stock_code = t.stock_code AND c.date = t.date
            WHERE t.date >= ? AND t.date <= ?
            ORDER BY t.stock_code, t.formula_variant, t.date""",
        [args.start, args.end],
    ).fetchall()
    n_raw = len(sigs)
    sigs = [r for r in sigs if not is_index_code(r[0]) and r[4] in ("1", "1.5", "2", "3", "4")]
    log.info(f"  signals: {len(sigs):,} (过滤指数 + stage='?' 后, 剔 {n_raw - len(sigs):,})")

    # 2. 分组 (stock × variant × stage)
    sigs_by_key: dict[tuple, list[dict]] = defaultdict(list)
    fid_by_variant: dict[str, str] = {}
    for sc, d, fid, fvar, stage in sigs:
        sigs_by_key[(sc, fvar, stage)].append({"stock_code": sc, "signal_date": str(d)})
        fid_by_variant[fvar] = fid
    log.info(f"  唯一 (stock × variant × stage): {len(sigs_by_key):,}")

    # 3. 样本量过滤
    keys_to_optimize = [k for k, v in sigs_by_key.items() if len(v) >= args.min_signals]
    log.info(f"  样本 ≥ {args.min_signals} 的: {len(keys_to_optimize):,}")

    # 4. 加载 K 线
    all_codes = sorted({k[0] for k in keys_to_optimize})
    log.info(f"加载 {len(all_codes):,} 股 K 线 ...")
    placeholders = ",".join(["?"] * len(all_codes))
    kl_rows = mkt.execute(
        f"""SELECT code, date, open, high, low, close, volume, amount
              FROM v_price_kline_qfq
             WHERE freq='daily' AND adjust='qfq' AND code IN ({placeholders})
               AND date >= ? AND date <= ?
             ORDER BY code, date""",
        all_codes + [args.start, args.end],
    ).fetchall()
    log.info(f"  K 线: {len(kl_rows):,} 行")
    mkt.close()

    bars_by_stock: dict[str, list[Bar]] = defaultdict(list)
    for code, date, o, h, l, c, v, a in kl_rows:
        bars_by_stock[code].append(Bar(
            date=date, open=float(o or 0), high=float(h or 0), low=float(l or 0),
            close=float(c or 0), volume=float(v or 0), amount=float(a or 0),
        ))

    # 5. 多进程跑 Optuna
    n_workers = max(1, min(args.workers, mp.cpu_count() - 1))
    tasks = [(sc, fid_by_variant.get(fvar, fvar), fvar, stage,
              sigs_by_key[(sc, fvar, stage)])
             for sc, fvar, stage in keys_to_optimize]
    log.info(f"开始 Optuna ({len(tasks):,} 任务 × {args.trials} trials, "
             f"{n_workers} workers fork, wf={args.walk_forward_mode}) ...")
    t_opt = time.time()
    out_rows: list = []
    n_done = 0; n_succ = 0
    ctx = mp.get_context("fork")
    with ctx.Pool(processes=n_workers, initializer=_init_worker,
                  initargs=(bars_by_stock, args.trials,
                            args.walk_forward_mode)) as pool:
        chunksize = max(1, len(tasks) // (n_workers * 4))
        for row in pool.imap_unordered(_worker_optimize, tasks, chunksize=chunksize):
            n_done += 1
            if row is not None:
                out_rows.append(row + (args.start, args.end))
                n_succ += 1
            if n_done % 1000 == 0:
                elapsed = time.time() - t_opt
                rate = n_done / max(elapsed, 0.001)
                remaining = (len(tasks) - n_done) / max(rate, 0.001)
                log.info(f"  {n_done:,}/{len(tasks):,} ({n_succ:,} 成功) "
                         f"— {elapsed:.0f}s elapsed, est. {remaining:.0f}s remaining")
    log.info(f"Optuna 完成 ({time.time()-t_opt:.0f}s) — {n_succ:,} / {n_done:,} 成功")

    # 6. 写库 (走 governance 守门, OOS 字段必填 / 反 estimation)
    log.info("写库 ...")
    from services.db import get_conn
    conn = get_conn()
    try:
        ensure_optuna_tables(conn, cfg)   # 走 ddl.py, 不在脚本内 hardcode DDL

        # 守门: 逐行检查 (governance.enforce_pre_insert)
        validated_rows: list = []
        violations: list[dict] = []
        for row in out_rows:
            # row 索引 (49 字段, 见 _worker_optimize tuple 顺序):
            #   [37] walk_forward_mode  [38] train_n  [39] test_n
            #   [40] oos_sharpe  [41] oos_win  [42] oos_avg
            #   [43] oos_n_traded  [44] oos_start  [45] oos_end
            #   [46] oos_n_windows  [47] oos_monthly_sharpe_std
            #   [48] execution_model_version  [49] eval_start  [50] eval_end
            record = {
                "stock_code": row[0],
                "formula_id": row[1],
                "formula_variant": row[2],
                "stage_filter": row[3],
                "walk_forward_mode": row[37],
                "train_n_signals": row[38],
                "test_n_signals": row[39],
                "oos_sharpe": row[40],
                "oos_win_rate": row[41],
                "oos_avg_ret": row[42],
                "oos_n_traded": row[43],
                "oos_period_start": row[44],
                "oos_period_end": row[45],
                "oos_n_windows": row[46],
                "oos_monthly_sharpe_std": row[47],
            }
            try:
                enforce_pre_insert(record, cfg)
                validated_rows.append(row)
            except GovernanceViolation as e:
                violations.append({
                    "stock_code": record["stock_code"],
                    "formula_id": record["formula_id"],
                    "formula_variant": record["formula_variant"],
                    "stage_filter": record["stage_filter"],
                    "reason": str(e),
                    "record_json": json.dumps(record, default=str),
                })

        log.info(f"  governance: {len(validated_rows):,} pass / "
                 f"{len(violations):,} reject (写 fact_optuna_governance_log 审计)")

        # 写治理审计表
        if violations:
            log_governance_violations(conn, run_id, violations, cfg)

        # 写业务表
        table = cfg.output.stage_optimal_table
        conn.execute("BEGIN TRANSACTION")
        try:
            conn.execute(f"DELETE FROM {table}")
            conn.executemany(
                f"""INSERT INTO {table} (
                    stock_code, formula_id, formula_variant, stage_filter,
                    optimal_hp, optimal_stop_pct, optimal_target_pct, optimal_trailing_pct,
                    optimal_buy_offset,
                    optimal_body_ratio_min, optimal_lower_shadow_min,
                    optimal_close_position_min, optimal_volume_relative_min,
                    optimal_calmar, optimal_sortino,
                    optimal_pain_index, optimal_ulcer_index,
                    optimal_tail_risk, optimal_stability,
                    n_signals_input, n_traded, n_blocked,
                    win_rate, avg_ret, median_ret, avg_max_dd, avg_holding_days,
                    sharpe, calmar,
                    pct_exit_stop, pct_exit_trailing, pct_exit_target,
                    pct_exit_hp, pct_exit_blocked, pct_exit_truncated,
                    optuna_score, optuna_n_trials,
                    walk_forward_mode, train_n_signals, test_n_signals,
                    oos_sharpe, oos_win_rate, oos_avg_ret,
                    oos_n_traded, oos_period_start, oos_period_end,
                    oos_n_windows, oos_monthly_sharpe_std,
                    execution_model_version,
                    eval_start_date, eval_end_date
                ) VALUES ({','.join(['?']*51)})""",
                validated_rows,
            )
            conn.execute("COMMIT")
        except BaseException:
            try: conn.execute("ROLLBACK")
            except Exception: pass
            raise

        log.info(f"=== 完成 — {len(validated_rows):,} 行写 {table} "
                 f"({time.time()-t0:.0f}s) ===")
        log.info(f"  run_id={run_id} (审计: fact_optuna_governance_log)")

        # 报告
        print()
        print(f"{'='*120}")
        print(f"  Phase ψ — stage × variant 寻优结果分布 (run_id={run_id})")
        print(f"{'='*120}")
        for r in conn.execute(f"""
            SELECT stage_filter, formula_variant, COUNT(*) AS n,
                   AVG(oos_sharpe) AS avg_oos_sharpe,
                   AVG(oos_win_rate)*100 AS oos_win,
                   AVG(oos_n_windows) AS avg_n_windows
              FROM {table}
             GROUP BY 1, 2 ORDER BY 1, 2""").fetchall():
            print(f"  stage={r[0]:<4} {r[1]:<32} n={r[2]:>5,} "
                  f"avg_oos_sharpe={r[3]:+.2f} oos_win={r[4]:.1f}% "
                  f"avg_n_windows={r[5]:.1f}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
