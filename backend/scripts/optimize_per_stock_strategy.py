"""Phase ζ — 每股每 variant Optuna 自动寻优 (entry script, I/O 层).

针对每个 (stock × formula_variant) 跑 Optuna TPE 100 trials, 寻优 4 维:
  - hp ∈ {5, 10, 15, 20, 30, 60, 90}
  - stop_pct  ∈ [-0.15, -0.03]
  - target_pct ∈ [+0.05, +0.30]
  - trailing_pct ∈ [+0.01, +0.08]

目标函数: sharpe × log(1+n_traded) × sample_weight (services.backtest.objective)
回测引擎: services.backtest.realistic_engine (含 stop/trailing/target/hp/一字板/成本)

输出: mart_per_stock_strategy_optimal
  → daily 推荐查每股的 optimal_params (替代全局 DEFAULT_STRATEGY)

usage:
  PYTHONPATH=backend python backend/scripts/optimize_per_stock_strategy.py [--trials 100] [--limit-stocks N]
"""
from __future__ import annotations

import argparse
import logging
import multiprocessing as mp
import os
import time
from collections import defaultdict
from datetime import date as _date
from pathlib import Path

import duckdb

from services.backtest.filters import is_index_code
from services.backtest.optimize import optimize_stock_strategy
from services.backtest.realistic_engine import Bar
from services.backtest.search_space import DEFAULT_SEARCH_SPACE
from services.trading_config import EXECUTION_MODEL


log = logging.getLogger("optimize_per_stock_strategy")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")


MARKET_DB = Path(__file__).resolve().parents[2] / "data" / "market.duckdb"
SMART_DB  = Path(__file__).resolve().parents[2] / "data" / "smartmoney.duckdb"


# ─────────────────────────────────────────────────────────────────────
# 进程池 worker (模块级函数, fork 后子进程能调用)
# ─────────────────────────────────────────────────────────────────────

# 共享状态: fork 模式下子进程继承 parent 的全局变量
# (在 _init_worker 中赋值, parent 不直接共享 bars_by_stock 给 worker)
_WORKER_BARS: dict = {}
_WORKER_N_TRIALS: int = 100


def _init_worker(bars: dict, n_trials: int):
    """fork 后, 每个子进程通过这个函数继承共享状态."""
    global _WORKER_BARS, _WORKER_N_TRIALS
    _WORKER_BARS = bars
    _WORKER_N_TRIALS = n_trials


def _worker_optimize(task: tuple) -> tuple | None:
    """(sc, fid, fvar, sig_list) → out_row or None.

    fork 模式: _WORKER_BARS 由 parent 继承 (Linux/macOS COW).
    """
    sc, fid, fvar, sig_list = task
    result = optimize_stock_strategy(
        stock_code=sc, formula_id=fid, formula_variant=fvar,
        signals=sig_list, bars_by_stock=_WORKER_BARS,
        n_trials=_WORKER_N_TRIALS, search_space=DEFAULT_SEARCH_SPACE,
    )
    if result is None:
        return None
    s = result.summary
    return (
        result.stock_code, result.formula_id, result.formula_variant,
        result.optimal_hp, result.optimal_stop_pct,
        result.optimal_target_pct, result.optimal_trailing_pct,
        result.optimal_buy_offset,
        result.optimal_body_ratio_min, result.optimal_lower_shadow_min,
        result.optimal_close_position_min, result.optimal_volume_relative_min,
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
        EXECUTION_MODEL.version,
    )


DDL = """
DROP TABLE IF EXISTS mart_per_stock_strategy_optimal;
CREATE TABLE IF NOT EXISTS mart_per_stock_strategy_optimal (
    stock_code         TEXT NOT NULL,
    formula_id         TEXT NOT NULL,
    formula_variant    TEXT NOT NULL,
    -- 寻优结果
    optimal_hp         INTEGER NOT NULL,
    optimal_stop_pct   REAL NOT NULL,
    optimal_target_pct REAL NOT NULL,
    optimal_trailing_pct REAL NOT NULL,
    optimal_buy_offset INTEGER NOT NULL DEFAULT 1,  -- Phase ζ.full+: T+N 买入
    -- Phase η++++++ K 线形态过滤阈值 (Optuna 寻优)
    optimal_body_ratio_min      REAL DEFAULT 0.0,
    optimal_lower_shadow_min    REAL DEFAULT 0.0,
    optimal_close_position_min  REAL DEFAULT 0.0,
    optimal_volume_relative_min REAL DEFAULT 0.0,
    -- Phase η++++++ 多目标 metrics
    optimal_calmar       REAL,
    optimal_sortino      REAL,
    optimal_pain_index   REAL,
    optimal_ulcer_index  REAL,
    optimal_tail_risk    REAL,
    optimal_stability    REAL,
    -- 最佳参数下的真实 metrics (走 realistic_engine)
    n_signals_input    INTEGER,
    n_traded           INTEGER,
    n_blocked          INTEGER,
    win_rate           REAL,
    avg_ret            REAL,
    median_ret         REAL,
    avg_max_dd         REAL,
    avg_holding_days   REAL,
    sharpe             REAL,
    calmar             REAL,
    -- 出场分布
    pct_exit_stop      REAL,
    pct_exit_trailing  REAL,
    pct_exit_target    REAL,
    pct_exit_hp        REAL,
    pct_exit_blocked   REAL,
    pct_exit_truncated REAL,
    -- 元数据
    optuna_score       REAL,
    optuna_n_trials    INTEGER,
    execution_model_version TEXT,
    eval_start_date    TEXT,
    eval_end_date      TEXT,
    built_at           TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (stock_code, formula_id, formula_variant)
);
CREATE INDEX IF NOT EXISTS idx_mpsso_score ON mart_per_stock_strategy_optimal(optuna_score);
CREATE INDEX IF NOT EXISTS idx_mpsso_sharpe ON mart_per_stock_strategy_optimal(sharpe);
"""


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--trials", type=int, default=100)
    parser.add_argument("--start",  default="2024-01-01")
    parser.add_argument("--end",    default=None,
                        help="默认 calendar-gated latest_closed_trade_date (Phase ψ.5)")
    parser.add_argument("--limit-stocks", type=int, default=None)
    parser.add_argument("--min-signals", type=int, default=10,
                        help="(stock × variant) 至少这么多信号才寻优")
    parser.add_argument("--workers", type=int, default=8,
                        help="进程池 worker 数 (默认 8, 实际取 min(cpu_count-1, this))")
    args = parser.parse_args()

    if args.end is None:
        from services.utils import latest_closed_or_raise
        args.end = latest_closed_or_raise()

    t0 = time.time()
    log.info(f"=== ζ 每股每 variant Optuna 寻优 ===")
    log.info(f"  execution_model: {EXECUTION_MODEL.version}")
    log.info(f"  search_space: hp{DEFAULT_SEARCH_SPACE.hp_choices}, "
             f"stop[{DEFAULT_SEARCH_SPACE.stop_pct_lo:+.2f},{DEFAULT_SEARCH_SPACE.stop_pct_hi:+.2f}], "
             f"target[{DEFAULT_SEARCH_SPACE.target_pct_lo:+.2f},{DEFAULT_SEARCH_SPACE.target_pct_hi:+.2f}], "
             f"trailing[{DEFAULT_SEARCH_SPACE.trailing_pct_lo:.2f},{DEFAULT_SEARCH_SPACE.trailing_pct_hi:.2f}]")
    log.info(f"  trials: {args.trials} / (stock × variant)")

    # 1. 读信号 (过滤指数代码)
    mkt = duckdb.connect(str(MARKET_DB), read_only=True)
    mkt.execute(f"ATTACH '{SMART_DB}' AS sm (READ_ONLY)")
    log.info("加载 signals × context ...")
    sigs = mkt.execute(
        """SELECT t.stock_code, t.date, t.formula_id, t.formula_variant
             FROM sm.fact_technical_trigger t
            WHERE t.date >= ? AND t.date <= ?
            ORDER BY t.stock_code, t.formula_variant, t.date""",
        [args.start, args.end],
    ).fetchall()
    n_raw_sigs = len(sigs)
    sigs = [r for r in sigs if not is_index_code(r[0])]
    n_idx_filtered = n_raw_sigs - len(sigs)
    log.info(f"  signals: {len(sigs):,} (过滤指数后, 剔除 {n_idx_filtered:,} 条指数信号)")

    # 2. 分组 (stock × variant) → [signal_date, ...]
    sigs_by_stock_variant: dict[tuple, list[dict]] = defaultdict(list)
    fid_by_variant: dict[str, str] = {}
    for sc, d, fid, fvar in sigs:
        sigs_by_stock_variant[(sc, fvar)].append({"stock_code": sc, "signal_date": str(d)})
        fid_by_variant[fvar] = fid
    log.info(f"  唯一 (stock × variant): {len(sigs_by_stock_variant):,}")

    # 3. 过滤样本量不足的组合
    keys_to_optimize = [k for k, v in sigs_by_stock_variant.items() if len(v) >= args.min_signals]
    log.info(f"  样本数 >= {args.min_signals} 的: {len(keys_to_optimize):,}")

    # 4. limit-stocks (debug)
    if args.limit_stocks:
        # 取按股票去重后前 N 只
        unique_stocks = []
        seen = set()
        for sc, fvar in keys_to_optimize:
            if sc not in seen:
                seen.add(sc); unique_stocks.append(sc)
                if len(unique_stocks) >= args.limit_stocks:
                    break
        keys_to_optimize = [(sc, fvar) for sc, fvar in keys_to_optimize if sc in seen]
        log.info(f"  limit-stocks 后: {len(keys_to_optimize)} 个 (stock × variant)")

    # 5. 加载 K 线
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

    # 6. 每 (stock × variant) 跑 Optuna — multiprocessing Pool (fork mode, COW 共享 bars)
    n_workers = max(1, min(args.workers, mp.cpu_count() - 1))
    tasks = [(sc, fid_by_variant.get(fvar, fvar), fvar, sigs_by_stock_variant[(sc, fvar)])
             for sc, fvar in keys_to_optimize]
    log.info(f"开始 Optuna 寻优 ({len(tasks):,} 任务 × {args.trials} trials, {n_workers} workers fork mode) ...")
    t_opt = time.time()
    out_rows: list = []
    n_done = 0; n_succeeded = 0

    # macOS Python 3.13 默认 spawn, fork 在 macOS 14+ 可能 fragile;
    # 但回测 worker 是纯计算, fork 仍可用 (Optuna study 在 worker 内创建)
    ctx = mp.get_context("fork")
    with ctx.Pool(processes=n_workers, initializer=_init_worker,
                  initargs=(bars_by_stock, args.trials)) as pool:
        # chunksize 平衡: 任务多→大 chunksize 减少 IPC; 任务少→小 chunksize 均衡负载
        chunksize = max(1, len(tasks) // (n_workers * 4))
        for result_row in pool.imap_unordered(_worker_optimize, tasks, chunksize=chunksize):
            n_done += 1
            if result_row is not None:
                out_rows.append(result_row + (args.start, args.end))
                n_succeeded += 1
            if n_done % 500 == 0:
                elapsed = time.time() - t_opt
                rate = n_done / max(elapsed, 0.001)
                remaining = (len(tasks) - n_done) / max(rate, 0.001)
                log.info(f"  {n_done:,}/{len(tasks):,} ({n_succeeded:,} 成功) "
                         f"— {elapsed:.0f}s elapsed, est. {remaining:.0f}s remaining "
                         f"({rate:.1f} 任务/秒)")

    log.info(f"Optuna 完成 ({time.time()-t_opt:.0f}s) — {n_succeeded:,} / {n_done:,} 成功")

    # 7. 写库
    log.info("写库 ...")
    from services.db import get_conn
    conn = get_conn()
    try:
        conn.executescript(DDL)
        conn.execute("BEGIN TRANSACTION")
        try:
            conn.execute("DELETE FROM mart_per_stock_strategy_optimal")
            conn.executemany(
                """INSERT INTO mart_per_stock_strategy_optimal (
                    stock_code, formula_id, formula_variant,
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
                    execution_model_version, eval_start_date, eval_end_date
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                out_rows,
            )
            conn.execute("COMMIT")
        except BaseException:
            try: conn.execute("ROLLBACK")
            except Exception: pass
            raise

        # 8. 报告
        log.info(f"=== ζ 完成 — {len(out_rows):,} 行写入 ({time.time()-t0:.0f}s) ===")
        print()
        print(f"{'='*120}")
        print(f"  Top 15 by optuna_score")
        print(f"{'='*120}")
        print(f"{'股票':>8} {'公式':<30} {'hp':>3} {'stop':>7} {'target':>7} {'trail':>6} "
              f"{'n':>3} {'win':>5} {'ret':>6} {'sharpe':>6} {'score':>6}")
        for r in conn.execute("""
            SELECT stock_code, formula_variant, optimal_hp,
                   optimal_stop_pct, optimal_target_pct, optimal_trailing_pct,
                   n_traded, win_rate, avg_ret, sharpe, optuna_score
              FROM mart_per_stock_strategy_optimal
             ORDER BY optuna_score DESC LIMIT 15
        """).fetchall():
            print(f"{r[0]:>8} {r[1]:<30} {r[2]:>3}d "
                  f"{r[3]*100:>+6.1f}% {r[4]*100:>+6.1f}% {r[5]*100:>5.2f}% "
                  f"{r[6]:>3} {r[7]*100:>4.0f}% {r[8]*100:>+5.1f}% "
                  f"{r[9]:>+6.2f} {r[10]:>+6.2f}")

        # 寻优后的 hp 分布
        print()
        print(f"{'='*120}")
        print(f"  寻优后 hp 分布 (各 hp 被选中的比例)")
        print(f"{'='*120}")
        for r in conn.execute("""SELECT optimal_hp, COUNT(*), AVG(sharpe) AS avg_sharpe
                                  FROM mart_per_stock_strategy_optimal
                                 GROUP BY optimal_hp ORDER BY optimal_hp""").fetchall():
            print(f"  hp={r[0]:>2}d: {r[1]:>5,} 组合, 平均 sharpe={r[2]:+.3f}")

        # 寻优后的 stop / target / trailing 分布
        print()
        print(f"{'='*120}")
        print(f"  寻优后 stop / target / trailing 分布")
        print(f"{'='*120}")
        for r in conn.execute("""
            SELECT AVG(optimal_stop_pct)*100 AS avg_stop,
                   STDDEV(optimal_stop_pct)*100 AS std_stop,
                   AVG(optimal_target_pct)*100 AS avg_target,
                   STDDEV(optimal_target_pct)*100 AS std_target,
                   AVG(optimal_trailing_pct)*100 AS avg_trail,
                   STDDEV(optimal_trailing_pct)*100 AS std_trail
              FROM mart_per_stock_strategy_optimal
        """).fetchall():
            print(f"  stop_pct:   平均 {r[0]:>+5.2f}% (std {r[1]:>+4.2f}%)")
            print(f"  target_pct: 平均 {r[2]:>+5.2f}% (std {r[3]:>+4.2f}%)")
            print(f"  trailing:   平均 {r[4]:>+5.2f}% (std {r[5]:>+4.2f}%)")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
