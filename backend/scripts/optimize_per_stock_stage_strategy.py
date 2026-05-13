"""Phase η+++++++ — 每股每公式每形态 Optuna 寻优 (A 路线).

⚠ 用户要求: "每个股票每种形态下每个公式下都单独选优"
⚠ PK: (stock × formula_variant × stage_filter), 130K 任务级别
⚠ 寻优 9 维 (现有: hp/stop/target/trail/offset + 4 K线阈值)
⚠ 单组样本可能 5-20 笔, 过拟合风险高 — 用户接受 "跑出来才知道"

实施:
  按 (stock × variant × stage) 切样本, 每组跑 100 trials Optuna
  样本不足 (n < 5) 直接跳过 — 不浪费 compute, 不输出
  Output: mart_per_stock_stage_strategy_optimal

usage:
  PYTHONPATH=backend python backend/scripts/optimize_per_stock_stage_strategy.py [--trials 100] [--workers 8]
"""
from __future__ import annotations

import argparse
import logging
import multiprocessing as mp
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


log = logging.getLogger("optimize_per_stock_stage_strategy")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")


MARKET_DB = Path(__file__).resolve().parents[2] / "data" / "market.duckdb"
SMART_DB  = Path(__file__).resolve().parents[2] / "data" / "smartmoney.duckdb"

# Optuna 任务样本下限 (低于不寻优, 数据驱动诚实)
STAGE_MIN_SIGNALS = 5


DDL = """
DROP TABLE IF EXISTS mart_per_stock_stage_strategy_optimal;
CREATE TABLE IF NOT EXISTS mart_per_stock_stage_strategy_optimal (
    stock_code         TEXT NOT NULL,
    formula_id         TEXT NOT NULL,
    formula_variant    TEXT NOT NULL,
    stage_filter       TEXT NOT NULL,             -- 1 / 1.5 / 2 / 3 / 4
    optimal_hp         INTEGER NOT NULL,
    optimal_stop_pct   REAL NOT NULL,
    optimal_target_pct REAL NOT NULL,
    optimal_trailing_pct REAL NOT NULL,
    optimal_buy_offset INTEGER NOT NULL DEFAULT 1,
    optimal_body_ratio_min      REAL DEFAULT 0.0,
    optimal_lower_shadow_min    REAL DEFAULT 0.0,
    optimal_close_position_min  REAL DEFAULT 0.0,
    optimal_volume_relative_min REAL DEFAULT 0.0,
    optimal_calmar       REAL,
    optimal_sortino      REAL,
    optimal_pain_index   REAL,
    optimal_ulcer_index  REAL,
    optimal_tail_risk    REAL,
    optimal_stability    REAL,
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
    pct_exit_stop      REAL,
    pct_exit_trailing  REAL,
    pct_exit_target    REAL,
    pct_exit_hp        REAL,
    pct_exit_blocked   REAL,
    pct_exit_truncated REAL,
    optuna_score       REAL,
    optuna_n_trials    INTEGER,
    execution_model_version TEXT,
    eval_start_date    TEXT,
    eval_end_date      TEXT,
    built_at           TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (stock_code, formula_id, formula_variant, stage_filter)
);
CREATE INDEX IF NOT EXISTS idx_mpsso_stg_calmar ON mart_per_stock_stage_strategy_optimal(optimal_calmar);
CREATE INDEX IF NOT EXISTS idx_mpsso_stg_sc_var ON mart_per_stock_stage_strategy_optimal(stock_code, formula_variant);
CREATE INDEX IF NOT EXISTS idx_mpsso_stg_stage ON mart_per_stock_stage_strategy_optimal(stage_filter);
"""


# Worker 共享状态 (fork mode)
_WORKER_BARS: dict = {}
_WORKER_N_TRIALS: int = 100


def _init_worker(bars: dict, n_trials: int):
    global _WORKER_BARS, _WORKER_N_TRIALS
    _WORKER_BARS = bars
    _WORKER_N_TRIALS = n_trials


def _worker_optimize(task: tuple) -> tuple | None:
    """(sc, fid, fvar, stage_filter, sig_list) → out_row or None."""
    sc, fid, fvar, stage_filter, sig_list = task
    result = optimize_stock_strategy(
        stock_code=sc, formula_id=fid, formula_variant=fvar,
        signals=sig_list, bars_by_stock=_WORKER_BARS,
        n_trials=_WORKER_N_TRIALS, search_space=DEFAULT_SEARCH_SPACE,
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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--trials", type=int, default=100)
    parser.add_argument("--start",  default="2023-01-01")
    parser.add_argument("--end",    default=None,
                        help="默认 calendar-gated latest_closed_trade_date (Phase ψ.5)")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--min-signals", type=int, default=STAGE_MIN_SIGNALS,
                        help="(stock × variant × stage) 至少这么多信号才寻优")
    args = parser.parse_args()

    if args.end is None:
        from services.utils import latest_closed_or_raise
        args.end = latest_closed_or_raise()

    t0 = time.time()
    log.info(f"=== η+++++++ A 路线 — 每股每公式每形态 Optuna ===")
    log.info(f"  trials={args.trials}, workers={args.workers}, min_signals={args.min_signals}")
    log.info(f"  execution_model: {EXECUTION_MODEL.version}")

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
    tasks = [(sc, fid_by_variant.get(fvar, fvar), fvar, stage, sigs_by_key[(sc, fvar, stage)])
             for sc, fvar, stage in keys_to_optimize]
    log.info(f"开始 Optuna ({len(tasks):,} 任务 × {args.trials} trials, {n_workers} workers fork) ...")
    t_opt = time.time()
    out_rows: list = []
    n_done = 0; n_succ = 0
    ctx = mp.get_context("fork")
    with ctx.Pool(processes=n_workers, initializer=_init_worker,
                  initargs=(bars_by_stock, args.trials)) as pool:
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
                         f"— {elapsed:.0f}s elapsed, est. {remaining:.0f}s remaining "
                         f"({rate:.1f} 任务/秒)")
    log.info(f"Optuna 完成 ({time.time()-t_opt:.0f}s) — {n_succ:,} / {n_done:,} 成功")

    # 6. 写库
    log.info("写库 ...")
    from services.db import get_conn
    conn = get_conn()
    try:
        conn.executescript(DDL)
        conn.execute("BEGIN TRANSACTION")
        try:
            conn.execute("DELETE FROM mart_per_stock_stage_strategy_optimal")
            conn.executemany(
                """INSERT INTO mart_per_stock_stage_strategy_optimal (
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
                    optuna_score, optuna_n_trials, execution_model_version,
                    eval_start_date, eval_end_date
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                out_rows,
            )
            conn.execute("COMMIT")
        except BaseException:
            try: conn.execute("ROLLBACK")
            except Exception: pass
            raise

        log.info(f"=== 完成 — {len(out_rows):,} 行 ({time.time()-t0:.0f}s) ===")

        # 报告: stage 分布
        print()
        print(f"{'='*120}")
        print(f"  η+++++++ A 路线 — stage × variant 寻优结果分布")
        print(f"{'='*120}")
        for r in conn.execute("""
            SELECT stage_filter, formula_variant, COUNT(*) AS n,
                   AVG(optimal_calmar) AS avg_calmar, AVG(win_rate)*100 AS win
              FROM mart_per_stock_stage_strategy_optimal
             GROUP BY 1, 2 ORDER BY 1, 2""").fetchall():
            print(f"  stage={r[0]:<4} {r[1]:<32} n={r[2]:>5,} avg_calmar={r[3]:+.2f} win={r[4]:.1f}%")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
