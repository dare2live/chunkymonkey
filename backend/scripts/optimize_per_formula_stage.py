"""Phase ψ.α B — per-formula × stage × train_end_date 严格 walk-forward Optuna.

⚠ Rule 7 + 用户原话 "真金白银, 不是数字游戏" 严格实施:
   每月末跑一次 Optuna, train = signals < month_end, OOS = forward 60 天窗.
   paper_sim 在 t 选股: WHERE train_end_date <= t LIMIT 1 → 0 selection leakage.

⚠ 跟 optimize_per_stock_stage_strategy.py 区别:
   - 不分股票, 全市场合并 (适合稀疏信号公式如 reversal_1m_deep)
   - 多行入库 (per train_end_date, ~每月底 1 行), 而不是单行
   - paper_sim 不读 oos_sharpe 排名 (无 selection bias), 只读 best params

⚠ 配置走 backend/config/optuna_config.yaml.

usage:
  PYTHONPATH=backend python backend/scripts/optimize_per_formula_stage.py \\
      --formula reversal_1m_mild reversal_1m_deep reversal_1w
"""
from __future__ import annotations

import argparse
import json
import logging
import multiprocessing as mp
import time
import uuid
from collections import defaultdict
from pathlib import Path

import duckdb

from services.backtest.filters import is_index_code
from services.backtest.objective import make_objective, MIN_TRADED_SIGNALS
from services.backtest.optimize import _evaluate_at_params, _trades_at_params
from services.backtest.realistic_engine import Bar
from services.backtest.search_space import DEFAULT_SEARCH_SPACE
from services.optimization.config import get_optuna_config
from services.optimization.ddl import ensure_optuna_tables, log_governance_violations
from services.optimization.governance import enforce_pre_insert, GovernanceViolation, enforce_pre_optimize
from services.optimization.oos_aggregator import aggregate_oos_metrics
from services.optimization.walk_forward import (
    assert_no_temporal_leak, list_month_ends, split_train_end_forward,
)
from services.trading_config import EXECUTION_MODEL


log = logging.getLogger("optimize_per_formula_stage")
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s",
                    datefmt="%H:%M:%S")


MARKET_DB = Path(__file__).resolve().parents[2] / "data" / "market.duckdb"
SMART_DB  = Path(__file__).resolve().parents[2] / "data" / "smartmoney.duckdb"


# Worker 共享状态 (fork mode, bars 共享避免重复加载)
_WORKER_BARS: dict = {}
_WORKER_N_TRIALS: int = 100
_WORKER_FORWARD_DAYS: int = 60


def _init_worker(bars: dict, n_trials: int, forward_days: int):
    global _WORKER_BARS, _WORKER_N_TRIALS, _WORKER_FORWARD_DAYS
    _WORKER_BARS = bars
    _WORKER_N_TRIALS = n_trials
    _WORKER_FORWARD_DAYS = forward_days


def _worker_optimize(task: tuple) -> tuple | None:
    """(formula_id, formula_variant, stage_filter, train_end_date, signals, n_stocks) → out_row | None.

    严格 walk-forward:
      train = signals where signal_date < train_end_date
      test  = signals where train_end_date <= signal_date < train_end_date + forward_days
      Optuna 100 trials on train → best params
      OOS metrics = best params 在 test 上跑
    """
    formula_id, formula_variant, stage_filter, train_end_date, all_sigs, n_stocks = task
    cfg = get_optuna_config()

    # 1. 切分 (anti-leak guard 内置)
    split = split_train_end_forward(
        all_sigs, train_end_date=train_end_date,
        forward_days=_WORKER_FORWARD_DAYS, cfg=cfg,
    )
    if split is None:
        return None
    assert_no_temporal_leak(split)

    # 2. Optuna 在 train 集上调参 (governance 守门)
    enforce_pre_optimize(n_trials=_WORKER_N_TRIALS, has_seed=True, cfg=cfg)

    import optuna
    optuna.logging.set_verbosity(optuna.logging.ERROR)
    objective_fn = make_objective(split.train, _WORKER_BARS, DEFAULT_SEARCH_SPACE)
    study = optuna.create_study(
        direction="maximize",
        sampler=optuna.samplers.TPESampler(seed=cfg.governance.default_optuna_seed),
    )
    try:
        study.optimize(objective_fn, n_trials=_WORKER_N_TRIALS, show_progress_bar=False)
    except Exception:
        return None
    if not study.trials or study.best_value < -1e8:
        return None

    bp = study.best_params
    best_hp = bp["hp"]
    best_stop = bp["stop_pct"]
    best_target = bp["target_pct"]
    best_trail = bp["trailing_pct"]
    best_buy_offset = bp.get("buy_offset", 1)
    best_body = bp.get("body_ratio_min", 0.0)
    best_lower = bp.get("lower_shadow_min", 0.0)
    best_close = bp.get("close_position_min", 0.0)
    best_vol = bp.get("volume_relative_min", 0.0)

    # 3. in-sample summary (train 集 best params)
    in_sample_summary, _ = _evaluate_at_params(
        signals=split.train, bars_by_stock=_WORKER_BARS,
        best_stop=best_stop, best_target=best_target, best_trail=best_trail,
        best_hp=best_hp, best_buy_offset=best_buy_offset,
        best_body=best_body, best_lower=best_lower,
        best_close=best_close, best_vol=best_vol,
    )
    if in_sample_summary is None:
        return None

    # 4. OOS metrics (test 窗 best params 真实跑)
    test_trades = _trades_at_params(
        signals=split.test, bars_by_stock=_WORKER_BARS,
        best_stop=best_stop, best_target=best_target, best_trail=best_trail,
        best_hp=best_hp, best_buy_offset=best_buy_offset,
        best_body=best_body, best_lower=best_lower,
        best_close=best_close, best_vol=best_vol,
    )
    if not test_trades:
        return None
    agg = aggregate_oos_metrics([{
        "trades": test_trades,
        "test_start": split.test_start,
        "test_end": split.test_end,
    }])
    if agg is None:
        return None

    return (
        formula_id, formula_variant, stage_filter, train_end_date,
        # 5 维 strategy params
        best_hp, best_stop, best_target, best_trail, best_buy_offset,
        # 4 维 K 线形态阈值
        best_body, best_lower, best_close, best_vol,
        # in-sample (train) metrics
        in_sample_summary.n_traded, in_sample_summary.win_rate, in_sample_summary.avg_ret,
        in_sample_summary.sharpe, in_sample_summary.calmar, in_sample_summary.avg_max_dd,
        # OOS metrics (forward 窗实测)
        "train_end_forward",
        split.n_train, split.n_test,
        agg.oos_sharpe, agg.oos_win_rate, agg.oos_avg_ret,
        agg.oos_n_traded, agg.oos_period_start, agg.oos_period_end,
        agg.oos_n_windows, agg.oos_monthly_sharpe_std,
        # Optuna 元
        float(study.best_value), _WORKER_N_TRIALS,
        len(all_sigs), n_stocks,
        EXECUTION_MODEL.version,
        _WORKER_FORWARD_DAYS,
    )


def main():
    cfg = get_optuna_config()

    parser = argparse.ArgumentParser()
    parser.add_argument("--trials", type=int, default=cfg.execution.n_trials)
    parser.add_argument("--start",  default="2023-01-01")
    parser.add_argument("--end",    default=None)
    parser.add_argument("--workers", type=int, default=cfg.execution.n_workers)
    parser.add_argument("--min-signals", type=int, default=50,
                        help="(formula × variant × stage) 跨所有股票至少这么多信号才跑 "
                             "(默认 50, 比 per-stock 严)")
    parser.add_argument("--formula", nargs="+", default=None,
                        help="只跑指定公式")
    parser.add_argument("--forward-days", type=int, default=60,
                        help="OOS 窗大小 (天), 默认 60 (~2 月)")
    parser.add_argument("--min-train-end", default="2023-06-30",
                        help="第一个 train_end_date — 前 6 个月当 train base, "
                             "之后才开始月度滚动 (默认 2023-06-30)")
    args = parser.parse_args()

    if args.end is None:
        from services.utils import latest_closed_or_raise
        args.end = latest_closed_or_raise()

    run_id = uuid.uuid4().hex[:12]
    t0 = time.time()
    log.info(f"=== Phase ψ.α B per-formula × stage × train_end Optuna (run_id={run_id}) ===")
    log.info(f"  trials={args.trials}, workers={args.workers}, "
             f"min_signals={args.min_signals}, forward_days={args.forward_days}")
    log.info(f"  --formula: {args.formula or '(全部 REGISTRY)'}")
    log.info(f"  range: {args.start} → {args.end}")

    # 1. 加载 signals
    mkt = duckdb.connect(str(MARKET_DB), read_only=True)
    mkt.execute(f"ATTACH '{SMART_DB}' AS sm (READ_ONLY)")
    log.info("加载 signals × context.stage ...")
    formula_filter_sql = ""
    formula_filter_params: list = []
    if args.formula:
        ph = ",".join(["?"] * len(args.formula))
        formula_filter_sql = f" AND t.formula_id IN ({ph})"
        formula_filter_params = list(args.formula)
    sigs = mkt.execute(
        f"""SELECT t.stock_code, t.date, t.formula_id, t.formula_variant,
                   COALESCE(c.technical_stage, '?') AS stage
              FROM sm.fact_technical_trigger t
              LEFT JOIN sm.fact_signal_context c
                ON c.stock_code = t.stock_code AND c.date = t.date
             WHERE t.date >= ? AND t.date <= ?
               {formula_filter_sql}
             ORDER BY t.formula_id, t.formula_variant, t.date""",
        [args.start, args.end] + formula_filter_params,
    ).fetchall()
    n_raw = len(sigs)
    sigs = [r for r in sigs if not is_index_code(r[0]) and r[4] in ("1", "1.5", "2", "3", "4")]
    log.info(f"  signals: {len(sigs):,} (剔指数 + stage='?' 后, 剔 {n_raw - len(sigs):,})")

    # 2. 分组 (formula × variant × stage)
    sigs_by_key: dict[tuple, list[dict]] = defaultdict(list)
    stocks_by_key: dict[tuple, set] = defaultdict(set)
    for sc, d, fid, fvar, stage in sigs:
        key = (fid, fvar, stage)
        sigs_by_key[key].append({"stock_code": sc, "signal_date": str(d)})
        stocks_by_key[key].add(sc)
    log.info(f"  唯一 (formula × variant × stage): {len(sigs_by_key):,}")

    # 3. 样本量过滤
    keys_to_optimize = [k for k, v in sigs_by_key.items() if len(v) >= args.min_signals]
    log.info(f"  样本 ≥ {args.min_signals} 的: {len(keys_to_optimize):,}")

    # 4. 生成 train_end_date 月末序列
    month_ends = list_month_ends(args.min_train_end, args.end)
    log.info(f"  train_end_date 月末序列: {len(month_ends)} 个 "
             f"({month_ends[0]} → {month_ends[-1] if month_ends else 'N/A'})")

    # 5. 任务 = key × month_end (笛卡尔积)
    tasks = []
    for key in keys_to_optimize:
        for me in month_ends:
            tasks.append((
                key[0], key[1], key[2], me,
                sigs_by_key[key],
                len(stocks_by_key[key]),
            ))
    log.info(f"  总任务数: {len(tasks):,} ({len(keys_to_optimize)} keys × {len(month_ends)} train_ends)")

    # 6. 加载全市场 K 线
    all_codes = sorted({s["stock_code"]
                        for k in keys_to_optimize
                        for s in sigs_by_key[k]})
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

    # 7. 多进程跑 Optuna
    n_workers = max(1, min(args.workers, mp.cpu_count() - 1))
    log.info(f"开始 Optuna ({len(tasks):,} 任务 × {args.trials} trials, "
             f"{n_workers} workers fork) ...")
    t_opt = time.time()
    out_rows: list = []
    n_done = 0
    ctx = mp.get_context("fork")
    with ctx.Pool(processes=n_workers, initializer=_init_worker,
                  initargs=(bars_by_stock, args.trials, args.forward_days)) as pool:
        chunksize = max(1, len(tasks) // (n_workers * 4))
        for row in pool.imap_unordered(_worker_optimize, tasks, chunksize=chunksize):
            n_done += 1
            if row is not None:
                out_rows.append(row + (args.start, args.end))
            if n_done % 100 == 0:
                elapsed = time.time() - t_opt
                rate = n_done / max(elapsed, 0.001)
                rem = (len(tasks) - n_done) / max(rate, 0.001)
                log.info(f"  {n_done:,}/{len(tasks):,} "
                         f"({len(out_rows)} 成功) — {elapsed:.0f}s elapsed, "
                         f"est. {rem:.0f}s remaining")
    log.info(f"Optuna 完成 ({time.time()-t_opt:.0f}s) — "
             f"{len(out_rows)} / {len(tasks)} 成功")

    # 8. 写库 (governance 守门)
    log.info("写库 ...")
    from services.db import get_conn
    conn = get_conn()
    try:
        ensure_optuna_tables(conn, cfg, with_per_formula=True)
        table = "mart_per_formula_stage_optimal"

        validated_rows: list = []
        violations: list[dict] = []
        for row in out_rows:
            # row 索引 (35 字段, 见 _worker_optimize tuple + main 加 start/end):
            #  [0] formula_id [1] variant [2] stage [3] train_end_date
            #  [4-12] strategy + pattern params
            #  [13-18] in_sample metrics
            #  [19] wf_mode [20] train_n [21] test_n
            #  [22] oos_sharpe [23] oos_win [24] oos_avg [25] oos_n_traded
            #  [26] oos_start [27] oos_end [28] oos_n_windows [29] oos_monthly_std
            #  [30] optuna_score [31] optuna_n_trials [32] n_signals [33] n_stocks
            #  [34] exec_model_version [35] forward_days [36] eval_start [37] eval_end
            record = {
                "stock_code": "__GLOBAL__",
                "formula_id": row[0], "formula_variant": row[1],
                "stage_filter": row[2],
                "walk_forward_mode": row[19],
                "train_n_signals": row[20], "test_n_signals": row[21],
                "oos_sharpe": row[22], "oos_win_rate": row[23],
                "oos_avg_ret": row[24], "oos_n_traded": row[25],
                "oos_period_start": row[26], "oos_period_end": row[27],
                "oos_n_windows": row[28], "oos_monthly_sharpe_std": row[29],
            }
            try:
                enforce_pre_insert(record, cfg)
                validated_rows.append(row)
            except GovernanceViolation as e:
                violations.append({
                    "stock_code": "__GLOBAL__",
                    "formula_id": record["formula_id"],
                    "formula_variant": record["formula_variant"],
                    "stage_filter": record["stage_filter"],
                    "reason": f"train_end={row[3]}: {e}",
                    "record_json": json.dumps(record, default=str),
                })

        log.info(f"  governance: {len(validated_rows):,} pass / "
                 f"{len(violations):,} reject")
        if violations:
            log_governance_violations(conn, run_id, violations, cfg)

        # 9. 写表 (增量 DELETE 只清本次 formula)
        conn.execute("BEGIN TRANSACTION")
        try:
            if args.formula:
                ph = ",".join(["?"] * len(args.formula))
                conn.execute(f"DELETE FROM {table} WHERE formula_id IN ({ph})",
                             list(args.formula))
                log.info(f"  增量 DELETE {table} WHERE formula_id IN {args.formula}")
            else:
                conn.execute(f"DELETE FROM {table}")

            # 38 placeholders: 36 row 字段 + 2 末尾 (eval_start, eval_end) — wait, 已含
            # 实际 row 长度: 35 + 2 (start, end appended) = 37
            # 但 INSERT 列也是 37 (含 forward_days)
            insert_cols = """
                formula_id, formula_variant, stage_filter, train_end_date,
                optimal_hp, optimal_stop_pct, optimal_target_pct, optimal_trailing_pct,
                optimal_buy_offset,
                optimal_body_ratio_min, optimal_lower_shadow_min,
                optimal_close_position_min, optimal_volume_relative_min,
                in_sample_n_traded, in_sample_win_rate, in_sample_avg_ret,
                in_sample_sharpe, in_sample_calmar, in_sample_avg_max_dd,
                walk_forward_mode, train_n_signals, test_n_signals,
                oos_sharpe, oos_win_rate, oos_avg_ret,
                oos_n_traded, oos_period_start, oos_period_end,
                oos_n_windows, oos_monthly_sharpe_std,
                optuna_score, optuna_n_trials,
                n_signals_input, n_stocks_input,
                execution_model_version,
                forward_days,
                eval_start_date, eval_end_date
            """
            n_cols = len([c for c in insert_cols.split(",") if c.strip()])
            conn.executemany(
                f"INSERT INTO {table} ({insert_cols}) VALUES "
                f"({','.join(['?']*n_cols)})",
                validated_rows,
            )
            conn.execute("COMMIT")
        except BaseException:
            try: conn.execute("ROLLBACK")
            except Exception: pass
            raise

        log.info(f"=== 完成 — {len(validated_rows)} 行写 {table} "
                 f"({time.time()-t0:.0f}s) ===")
        log.info(f"  run_id={run_id}")

        # 10. 报告 (按 train_end_date DESC, 看最近的 setup)
        print()
        print(f"{'='*120}")
        print(f"  Phase ψ.α B 严格 walk-forward 寻优 (run_id={run_id})")
        print(f"  paper_sim 在 t 时选 WHERE train_end_date <= t ORDER BY train_end_date DESC LIMIT 1")
        print(f"{'='*120}")
        print(f"  按 (formula × stage) 聚合 OOS sharpe 平均 + 最强 setup:")
        rows = conn.execute(f"""
            SELECT formula_id, stage_filter,
                   COUNT(*) AS n_train_ends,
                   ROUND(AVG(oos_sharpe), 3) AS avg_oos_sharpe,
                   ROUND(AVG(oos_win_rate)*100, 1) AS avg_oos_win,
                   ROUND(AVG(oos_avg_ret)*100, 2) AS avg_oos_avg_ret,
                   ROUND(MAX(oos_sharpe), 3) AS max_oos_sharpe,
                   MAX(optimal_hp) AS max_hp
              FROM {table}
             GROUP BY 1, 2
             ORDER BY avg_oos_sharpe DESC""").fetchall()
        print(f"  {'formula':<25} {'stage':<5} {'n_rows':>6} "
              f"{'avg_sh':>7} {'avg_win':>8} {'avg_avg':>8} {'max_sh':>7}")
        for r in rows:
            print(f"  {r[0]:<25} {r[1]:<5} {r[2]:>6} "
                  f"{r[3]:>+7.3f} {r[4]:>7.1f}% {r[5]:>+7.2f}% {r[6]:>+7.3f}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
