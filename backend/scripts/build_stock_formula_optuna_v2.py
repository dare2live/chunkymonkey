"""Phase ε.4 — 重建 mart_stock_formula_optuna_v2 (用真实回测引擎).

差异 vs v1 (build_stock_formula_optuna.py):
  v1: 永持有到 T+1+hd close, 不止损不止盈, 不扣成本, 不过滤涨跌停
  v2: 走 services.backtest.realistic_engine
      - stop_loss / trailing / target_hit / hp_expired 真触发
      - 双边成本 ~25 bps 扣减
      - 一字涨停拒绝买入 (n_blocked 单列)
      - max_drawdown 是真 intraday low (不是错误的 negative-return-mean)

输入: 同 v1 (fact_technical_trigger × fact_signal_context × kline)
输出: mart_stock_formula_optuna_v2 — 新 schema, 含 exit_distribution 列

依赖:
  - services.trading_config.EXECUTION_MODEL (全局执行参数)
  - services.backtest.realistic_engine.backtest_signals
  - services.backtest.strategy_defaults.DEFAULT_STRATEGY

usage:
  PYTHONPATH=backend python backend/scripts/build_stock_formula_optuna_v2.py [--start 2024-01-01]
"""
from __future__ import annotations

import argparse
import logging
import time
from collections import defaultdict
from datetime import date as _date
from pathlib import Path

import duckdb
import numpy as np

from services.backtest.realistic_engine import Bar, backtest_signals
from services.backtest.result import BacktestSummary
from services.backtest.strategy_defaults import DEFAULT_STRATEGY
from services.trading_config import EXECUTION_MODEL


log = logging.getLogger("build_stock_formula_optuna_v2")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")


MARKET_DB = Path(__file__).resolve().parents[2] / "data" / "market.duckdb"
SMART_DB  = Path(__file__).resolve().parents[2] / "data" / "smartmoney.duckdb"


HOLDING_DAYS = (5, 10, 15, 20, 30, 60, 90)
VOL_BINS  = [(0, 0.7, "缩量"), (0.7, 1.3, "平量"), (1.3, 2.0, "温量"), (2.0, 99, "爆量")]
AMT_BINS  = [(0, 0.7, "额减"), (0.7, 1.3, "额平"), (1.3, 2.0, "额温"), (2.0, 99, "额爆")]
P60_BINS  = [(0, 0.65, "深底"), (0.65, 0.85, "中位"), (0.85, 0.97, "高位"), (0.97, 99, "新高")]

MIN_N_PER_BUCKET = 3


DDL = """
DROP TABLE IF EXISTS mart_stock_formula_optuna_v2;
CREATE TABLE IF NOT EXISTS mart_stock_formula_optuna_v2 (
    stock_code        TEXT NOT NULL,
    formula_id        TEXT NOT NULL,
    formula_variant   TEXT NOT NULL,
    holding_days      INTEGER NOT NULL,
    vol_bin           TEXT NOT NULL,
    amt_bin           TEXT NOT NULL,
    price_pos_bin     TEXT NOT NULL,
    stage_bin         TEXT NOT NULL,
    -- 策略参数 (改 strategy_defaults 全表都影响)
    stop_pct          REAL,
    target_pct        REAL,
    trailing_pct      REAL,
    -- ε.3 新 metrics (基于实际策略执行)
    n_signals         INTEGER,         -- 触发的信号数
    n_traded          INTEGER,         -- 真实开仓 (剔除一字板等)
    n_blocked         INTEGER,         -- 一字涨停拒绝
    win_rate          REAL,            -- net_ret > 0 比例 (扣成本后)
    avg_ret           REAL,            -- 净收益均值
    median_ret        REAL,
    avg_max_dd        REAL,            -- ⚠ 真 intraday max drawdown (不是 v1 的负收益均值!)
    avg_holding_days  REAL,            -- 实际平均持仓 (含提前止损/止盈)
    sharpe            REAL,
    calmar            REAL,
    -- 出场原因分布 (理解策略行为)
    pct_exit_stop     REAL,
    pct_exit_trailing REAL,
    pct_exit_target   REAL,
    pct_exit_hp       REAL,
    pct_exit_blocked  REAL,
    pct_exit_truncated REAL,
    -- 标记
    is_best_hd          BOOLEAN,
    is_high_conviction  BOOLEAN,
    -- 元数据
    execution_model_version TEXT,
    eval_start_date     TEXT,
    eval_end_date       TEXT,
    built_at            TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (stock_code, formula_id, formula_variant, holding_days,
                 vol_bin, amt_bin, price_pos_bin, stage_bin)
);
CREATE INDEX IF NOT EXISTS idx_msfo2_stock ON mart_stock_formula_optuna_v2(stock_code);
CREATE INDEX IF NOT EXISTS idx_msfo2_bucket ON mart_stock_formula_optuna_v2(vol_bin, amt_bin, price_pos_bin, stage_bin);
CREATE INDEX IF NOT EXISTS idx_msfo2_best ON mart_stock_formula_optuna_v2(is_best_hd);
"""


def _bin_label(value, bins):
    for lo, hi, label in bins:
        if value is not None and lo <= value < hi:
            return label
    return "?"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2024-01-01")
    parser.add_argument("--end", default=_date.today().isoformat())
    parser.add_argument("--limit-stocks", type=int, default=None,
                        help="限测 N 只 (debug 用)")
    args = parser.parse_args()

    t0 = time.time()
    log.info(f"=== ε.4 重建 mart_stock_formula_optuna_v2 ({args.start} → {args.end}) ===")
    log.info(f"  execution_model: {EXECUTION_MODEL.version}")
    log.info(f"  strategy_defaults: stop={DEFAULT_STRATEGY.stop_pct:+.2%}, "
             f"target={DEFAULT_STRATEGY.target_pct:+.2%}, "
             f"trailing={DEFAULT_STRATEGY.trailing_pct:.2%}")

    mkt = duckdb.connect(str(MARKET_DB), read_only=True)
    mkt.execute(f"ATTACH '{SMART_DB}' AS sm (READ_ONLY)")

    # 1. 加载 signal × context
    log.info("加载 signal × context ...")
    sigs = mkt.execute(
        """
        SELECT t.stock_code, t.date, t.formula_id, t.formula_variant,
               c.vol_r20, c.amt_r20, c.price_pos_60d, c.technical_stage
          FROM sm.fact_technical_trigger t
          INNER JOIN sm.fact_signal_context c
            ON c.stock_code = t.stock_code AND c.date = t.date
         WHERE t.date >= ? AND t.date <= ?
         ORDER BY t.stock_code, t.formula_id, t.formula_variant, t.date
        """,
        [args.start, args.end],
    ).fetchall()
    log.info(f"  signals: {len(sigs):,}")

    # 2. 加载 K 线 (OHLCV + amount) → Bar 列表
    all_codes = sorted({r[0] for r in sigs})
    if args.limit_stocks:
        all_codes = all_codes[:args.limit_stocks]
        sigs = [r for r in sigs if r[0] in set(all_codes)]
        log.info(f"  limit-stocks 后: {len(all_codes)} 股, {len(sigs):,} signals")

    log.info(f"加载 {len(all_codes):,} 股 K 线 (OHLCV+amount) ...")
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

    # 3. 按 (stock, formula, variant, hd, 5维bucket) 分桶
    log.info("分桶 ...")
    bucket_signals: dict[tuple, list[dict]] = defaultdict(list)
    for sc, d, fid, fvar, vr, ar, p60, stage in sigs:
        if vr is None or ar is None or p60 is None:
            continue
        vol_b = _bin_label(vr, VOL_BINS)
        amt_b = _bin_label(ar, AMT_BINS)
        p60_b = _bin_label(p60, P60_BINS)
        stage_b = stage if stage in ("1", "1.5", "2", "3", "4") else "?"
        key = (sc, fid, fvar, vol_b, amt_b, p60_b, stage_b)
        bucket_signals[key].append({"stock_code": sc, "signal_date": str(d)})

    log.info(f"  唯一 (stock × variant × bucket): {len(bucket_signals):,}")

    # 4. 每桶 × 每 hd 跑 realistic_engine
    log.info(f"回测 (每桶 × {len(HOLDING_DAYS)} hd) ...")
    t_calc = time.time()
    out_rows: list[tuple] = []
    n_bucket_hd_done = 0
    per_stock_variant_best: dict[tuple, tuple] = {}   # for is_best_hd 标记

    for bucket_key, sig_list in bucket_signals.items():
        if len(sig_list) < MIN_N_PER_BUCKET:
            continue
        sc, fid, fvar, vol_b, amt_b, p60_b, stage_b = bucket_key
        for hd in HOLDING_DAYS:
            summary = backtest_signals(
                signals=sig_list,
                bars_by_stock=bars_by_stock,
                stop_pct=DEFAULT_STRATEGY.stop_pct,
                target_pct=DEFAULT_STRATEGY.target_pct,
                trailing_pct=DEFAULT_STRATEGY.trailing_pct,
                hp_target=hd,
                execution=EXECUTION_MODEL,
            )
            if summary.n_traded < MIN_N_PER_BUCKET:
                continue
            is_high_conviction = (summary.win_rate >= 0.55 and summary.n_traded >= 5)
            exit_dist = summary.exit_distribution
            row_key = (sc, fid, fvar, hd, vol_b, amt_b, p60_b, stage_b)
            row_data = (
                sc, fid, fvar, hd, vol_b, amt_b, p60_b, stage_b,
                DEFAULT_STRATEGY.stop_pct, DEFAULT_STRATEGY.target_pct, DEFAULT_STRATEGY.trailing_pct,
                summary.n_signals, summary.n_traded, summary.n_blocked,
                summary.win_rate, summary.avg_ret, summary.median_ret,
                summary.avg_max_dd, summary.avg_holding_days,
                summary.sharpe, summary.calmar,
                exit_dist.get("stop_loss", 0.0),
                exit_dist.get("trailing_stop", 0.0),
                exit_dist.get("target_hit", 0.0),
                exit_dist.get("hp_expired", 0.0),
                exit_dist.get("one_word_blocked", 0.0),
                exit_dist.get("data_truncated", 0.0),
                False,   # is_best_hd 后填
                is_high_conviction,
                EXECUTION_MODEL.version, args.start, args.end,
            )
            out_rows.append((row_key, row_data, summary.calmar))
            n_bucket_hd_done += 1

            # is_best_hd 跟踪
            bk = (sc, fid, fvar, vol_b, amt_b, p60_b, stage_b)
            prev = per_stock_variant_best.get(bk)
            if prev is None or summary.calmar > prev[1]:
                per_stock_variant_best[bk] = (row_key, summary.calmar)
        if n_bucket_hd_done % 5000 == 0 and n_bucket_hd_done > 0:
            log.info(f"  {n_bucket_hd_done:,} 桶×hd 完成 ({time.time()-t_calc:.0f}s)")
    log.info(f"  共 {n_bucket_hd_done:,} 桶×hd 完成 ({time.time()-t_calc:.0f}s)")

    # 标记 is_best_hd
    best_keys = {x[0] for x in per_stock_variant_best.values()}

    # 5. 写库
    log.info("写库 ...")
    from services.db import get_conn
    conn = get_conn()
    try:
        conn.executescript(DDL)
        conn.execute("BEGIN TRANSACTION")
        try:
            conn.execute("DELETE FROM mart_stock_formula_optuna_v2")
            insert_rows = []
            for row_key, row_data, _calmar in out_rows:
                rd = list(row_data)
                # row_data 字段 32 个, is_best_hd 在索引 27 (= -5)
                rd[-5] = row_key in best_keys
                insert_rows.append(tuple(rd))
            conn.executemany(
                """INSERT INTO mart_stock_formula_optuna_v2 (
                    stock_code, formula_id, formula_variant, holding_days,
                    vol_bin, amt_bin, price_pos_bin, stage_bin,
                    stop_pct, target_pct, trailing_pct,
                    n_signals, n_traded, n_blocked,
                    win_rate, avg_ret, median_ret, avg_max_dd, avg_holding_days,
                    sharpe, calmar,
                    pct_exit_stop, pct_exit_trailing, pct_exit_target,
                    pct_exit_hp, pct_exit_blocked, pct_exit_truncated,
                    is_best_hd, is_high_conviction,
                    execution_model_version, eval_start_date, eval_end_date
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                insert_rows,
            )
            conn.execute("COMMIT")
        except BaseException:
            try: conn.execute("ROLLBACK")
            except Exception: pass
            raise

        # 6. 报告
        log.info("=== 完成 ===")
        log.info(f"  行: {len(insert_rows):,} 桶 (含 is_best_hd: {sum(1 for r in insert_rows if r[-5]):,})")
        log.info(f"  总耗时: {time.time()-t0:.0f}s")

        # 出场分布全表统计
        print()
        print(f"{'='*100}")
        print(f"  v2 实际出场分布 (vs v1 的 '永持有到期' 假设)")
        print(f"{'='*100}")
        for r in conn.execute("""
            SELECT
                AVG(pct_exit_stop)*100      AS avg_stop,
                AVG(pct_exit_trailing)*100  AS avg_trail,
                AVG(pct_exit_target)*100    AS avg_target,
                AVG(pct_exit_hp)*100        AS avg_hp,
                AVG(pct_exit_blocked)*100   AS avg_blocked,
                AVG(pct_exit_truncated)*100 AS avg_trunc
              FROM mart_stock_formula_optuna_v2
        """).fetchall():
            print(f"  止损出场:      {r[0]:>5.1f}%")
            print(f"  Trailing 出场: {r[1]:>5.1f}%")
            print(f"  止盈出场:      {r[2]:>5.1f}%")
            print(f"  到期出场:      {r[3]:>5.1f}%  ← v1 假设 100% 是这个!")
            print(f"  一字板拒买:    {r[4]:>5.1f}%")
            print(f"  数据截断:      {r[5]:>5.1f}%")

        # v1 vs v2 桶对比 (取 1000 个共有桶比较)
        print()
        print(f"{'='*100}")
        print(f"  v1 vs v2 metrics 对比 (随机 1000 桶, only is_best_hd)")
        print(f"{'='*100}")
        try:
            for r in conn.execute("""
                SELECT
                    AVG(v1.win_rate) - AVG(v2.win_rate) AS d_win,
                    AVG(v1.avg_ret)  - AVG(v2.avg_ret)  AS d_ret,
                    AVG(v1.avg_dd)   - AVG(v2.avg_max_dd) AS d_dd,
                    COUNT(*) AS n
                  FROM (SELECT * FROM mart_stock_formula_optuna WHERE is_best_hd LIMIT 1000) v1
                  JOIN mart_stock_formula_optuna_v2 v2
                    ON v1.stock_code=v2.stock_code AND v1.formula_id=v2.formula_id
                   AND v1.formula_variant=v2.formula_variant AND v1.holding_days=v2.holding_days
                   AND v1.vol_bin=v2.vol_bin AND v1.amt_bin=v2.amt_bin
                   AND v1.price_pos_bin=v2.price_pos_bin AND v1.stage_bin=v2.stage_bin
                  WHERE v2.is_best_hd
            """).fetchall():
                if r[3]:
                    print(f"  对比桶数: {r[3]:,}")
                    print(f"  Δwin_rate (v1 - v2): {(r[0] or 0)*100:>+6.2f}%   ← v1 高估了 (无成本)")
                    print(f"  Δavg_ret  (v1 - v2): {(r[1] or 0)*100:>+6.2f}%")
                    print(f"  Δavg_dd   (v1 - v2): {(r[2] or 0)*100:>+6.2f}%   ← v1 错算 (负收益均值)")
        except Exception as e:
            print(f"  对比失败 (v1 表可能已不存在): {e}")
        print()
    finally:
        conn.close()


if __name__ == "__main__":
    main()
