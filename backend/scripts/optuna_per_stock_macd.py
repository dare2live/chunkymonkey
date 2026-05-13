"""Phase η+ — per-stock 真 Optuna 寻优 (借鉴 bestchoice macd_optuna_backtest)。

针对每只 candidate 股票 (top 500 信号最多 + n≥30 的股), 跑 Optuna 寻最佳:
  - MACD combo: S(10,22,8) / M(12,26,9) / L(14,30,11)
  - vol_ratio_min  : 量比下限 [0.3, 3.0]
  - amt_ratio_min  : 额比下限 [0.3, 2.5]
  - price_pos_max  : 价格位置上限 [0.55, 1.0]
  - dif_positive   : DIF 是否需要 > 0 (0/1)
  - holding_days   : [5, 10, 15, 20, 30, 40, 60]

目标: max(calmar × win_rate × log(1+n))  (n 加权防小样本)

输出: mart_per_stock_optuna_best (该股最佳全套配置)

用法:
  PYTHONPATH=backend python backend/scripts/optuna_per_stock_macd.py [--n-stocks 500] [--trials 30]
"""
from __future__ import annotations

import argparse
import logging
import math
import time
from collections import defaultdict
from datetime import date as _date
from pathlib import Path

import duckdb
import numpy as np


log = logging.getLogger("optuna_per_stock_macd")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")


MARKET_DB = Path(__file__).resolve().parents[2] / "data" / "market.duckdb"
SMART_DB  = Path(__file__).resolve().parents[2] / "data" / "smartmoney.duckdb"


HOLDING_PERIODS = [5, 10, 15, 20, 30, 40, 60]
MACD_COMBOS = {
    "S": (10, 22, 8),
    "M": (12, 26, 9),
    "L": (14, 30, 11),
}
MIN_TRIALS_SIGNALS = 5  # 每 trial 最少 5 个信号才不算 -999 (per-stage 桶样本少, 降阈值)


DDL = """
DROP TABLE IF EXISTS mart_per_stock_optuna_best;
CREATE TABLE IF NOT EXISTS mart_per_stock_optuna_best (
    stock_code        TEXT NOT NULL,
    stage_filter      TEXT NOT NULL DEFAULT 'all',  -- 'all' / '1' / '1.5' / '2' / '3' / '4'
    macd_combo        TEXT,
    holding_days      INTEGER,
    vol_min           REAL,
    amt_min           REAL,
    price_max         REAL,
    dif_positive      BOOLEAN,
    n_signals         INTEGER,
    win_rate          REAL,
    avg_ret           REAL,
    avg_dd            REAL,
    sharpe            REAL,
    calmar            REAL,
    score             REAL,
    optuna_n_trials   INTEGER,
    eval_start_date   TEXT,
    eval_end_date     TEXT,
    built_at          TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (stock_code, stage_filter)
);
CREATE INDEX IF NOT EXISTS idx_mpsob_score  ON mart_per_stock_optuna_best(score);
CREATE INDEX IF NOT EXISTS idx_mpsob_filter ON mart_per_stock_optuna_best(stock_code, stage_filter);
"""


def ema_np(arr: np.ndarray, span: int) -> np.ndarray:
    alpha = 2.0 / (span + 1)
    c = 1.0 - alpha
    out = np.empty(len(arr), dtype=np.float64)
    out[0] = arr[0]
    for i in range(1, len(arr)):
        out[i] = alpha * arr[i] + c * out[i - 1]
    return out


def sma_np(arr: np.ndarray, window: int) -> np.ndarray:
    n = len(arr)
    out = np.full(n, np.nan, dtype=np.float64)
    if n < window:
        return out
    kernel = np.ones(window, dtype=np.float64) / window
    out[window - 1:] = np.convolve(arr, kernel, mode="valid")
    return out


def rolling_max_np(arr: np.ndarray, window: int) -> np.ndarray:
    n = len(arr)
    if n < window:
        return np.full(n, np.nan, dtype=np.float64)
    from numpy.lib.stride_tricks import sliding_window_view
    padded = np.pad(arr, (window - 1, 0), mode="edge")
    return sliding_window_view(padded, window).max(axis=1)


def signals_for_stock(close, high, low, volume, amount, fast, slow, sig, stage_arr=None):
    """每股全 MACD 金叉 + 5 特征 (含 stage) + 7 hd × (ret, dd)。

    Args:
        stage_arr: 与 close 同长的 stage 字符串数组 (来自 fact_stock_technical_stage)
                   若为 None, 全部填 '?' (向后兼容)
    """
    n = len(close)
    warmup = slow + sig + max(HOLDING_PERIODS) + 2
    if n < warmup:
        return []
    if stage_arr is None:
        stage_arr = np.full(n, '?', dtype=object)
    dif = ema_np(close, fast) - ema_np(close, slow)
    dea = ema_np(dif, sig)
    vol_ma20 = sma_np(volume, 20)
    amt_ma20 = sma_np(amount, 20)
    max60 = rolling_max_np(close, 60)
    cross = (dif[:-1] < dea[:-1]) & (dif[1:] > dea[1:])
    sig_idx = np.where(cross)[0] + 1
    out = []
    for si in sig_idx:
        buy_i = si + 1
        if buy_i >= n: continue
        if (vol_ma20[si] <= 0 or np.isnan(vol_ma20[si])
                or amt_ma20[si] <= 0 or np.isnan(amt_ma20[si])
                or max60[si] <= 0): continue
        if volume[buy_i] <= 0 or amount[buy_i] <= 0: continue
        dif_val = float(dif[si])
        vol_r20 = float(volume[si] / vol_ma20[si])
        amt_r20 = float(amount[si] / amt_ma20[si])
        price60 = float(close[si] / max60[si])
        stage = str(stage_arr[si]) if stage_arr[si] is not None else '?'
        buy_price = float(amount[buy_i] / (volume[buy_i] * 100))
        rets = []; dds = []
        for hp in HOLDING_PERIODS:
            sell_i = buy_i + hp
            if sell_i >= n:
                rets.append(None); dds.append(None)
            else:
                sell = float(close[sell_i])
                lo_h = float(np.min(low[buy_i:sell_i + 1]))
                rets.append((sell - buy_price) / buy_price)
                dds.append((lo_h - buy_price) / buy_price)
        # 输出: (dif_val, vol_r20, amt_r20, price60, stage, *rets, *dds)
        out.append((dif_val, vol_r20, amt_r20, price60, stage, *rets, *dds))
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--n-stocks", type=int, default=500)
    parser.add_argument("--trials", type=int, default=30)
    parser.add_argument("--start", default="2024-01-01")
    parser.add_argument("--end", default=_date.today().isoformat())
    args = parser.parse_args()

    import optuna
    optuna.logging.set_verbosity(optuna.logging.ERROR)

    t_total = time.time()
    log.info(f"per-stock Optuna 寻优 (top {args.n_stocks} 候选 × {args.trials} trials)")

    mkt = duckdb.connect(str(MARKET_DB), read_only=True)
    mkt.execute(f"ATTACH '{SMART_DB}' AS sm (READ_ONLY)")

    # 1. 找候选股 (信号最多的 top N)
    log.info("筛选候选股 (信号数 top N)...")
    cands = mkt.execute(
        """
        SELECT stock_code, COUNT(*) AS n_sigs
          FROM sm.fact_technical_trigger
         WHERE formula_id='macd_golden_cross' AND date >= ? AND date <= ?
         GROUP BY stock_code
         HAVING COUNT(*) >= 15
         ORDER BY n_sigs DESC LIMIT ?
        """,
        [args.start, args.end, args.n_stocks],
    ).fetchall()
    codes = [r[0] for r in cands]
    log.info(f"  候选: {len(codes)} 股")

    # 2. 加载 K 线 + technical_stage (LEFT JOIN, 缺则 '?')
    log.info("加载 K 线 + technical_stage...")
    placeholders = ",".join(["?"] * len(codes))
    rows = mkt.execute(
        f"""
        SELECT k.code, k.date, k.close, k.high, k.low, k.volume, k.amount,
               COALESCE(ts.stage, '?') AS stage
          FROM v_price_kline_qfq k
          LEFT JOIN sm.fact_stock_technical_stage ts
            ON ts.stock_code = k.code AND ts.date = k.date
         WHERE k.adjust='qfq' AND k.freq='daily' AND k.code IN ({placeholders})
           AND k.date >= ?
         ORDER BY k.code, k.date
        """,
        codes + [args.start],
    ).fetchall()
    by_code: dict[str, list[tuple]] = defaultdict(list)
    for r in rows:
        by_code[r[0]].append(r)
    log.info(f"  K 线+stage {len(rows):,} 行 / {len(by_code)} 股")
    mkt.close()

    # 3. 算每股 × 3 MACD combo 的全部 signals (含 stage 列)
    log.info(f"算 3 MACD combo × {len(codes)} 股 signals...")
    t_sig = time.time()
    # raw signal tuple: (dif_val, vol_r20, amt_r20, price60, stage_str, *rets, *dds)
    # 我们存 numeric 部分到 numpy array (5 个连续浮点), stage 单独存
    n_num_cols = 4 + 2 * len(HOLDING_PERIODS)  # dif_val/vol/amt/p60 + 14 rets/dds
    sig_by_stock: dict[str, dict[str, tuple[np.ndarray, np.ndarray]]] = {}
    for code in codes:
        kls = by_code[code]
        if len(kls) < 80:
            continue
        close = np.array([float(r[2]) for r in kls])
        high  = np.array([float(r[3]) for r in kls])
        low   = np.array([float(r[4]) for r in kls])
        volume = np.array([float(r[5]) for r in kls])
        amount = np.array([float(r[6]) for r in kls])
        stages = np.array([str(r[7]) if r[7] is not None else '?' for r in kls], dtype=object)
        combos: dict[str, tuple[np.ndarray, np.ndarray]] = {}
        for cname, (f, s, g) in MACD_COMBOS.items():
            out = signals_for_stock(close, high, low, volume, amount, f, s, g, stage_arr=stages)
            if out:
                num_arr = np.full((len(out), n_num_cols), np.nan, dtype=np.float64)
                stage_arr = np.full(len(out), '?', dtype=object)
                for i, row in enumerate(out):
                    # row = (dif_val, vol_r20, amt_r20, price60, stage, *rets, *dds)
                    num_arr[i, 0:4] = row[0:4]
                    stage_arr[i] = row[4]
                    # rets + dds (14 cols)
                    for j, v in enumerate(row[5:]):
                        if v is not None:
                            num_arr[i, 4 + j] = float(v)
                combos[cname] = (num_arr, stage_arr)
        if combos:
            sig_by_stock[code] = combos
    log.info(f"  signals 完成 ({time.time()-t_sig:.1f}s) — 有 signals 的股: {len(sig_by_stock)}")

    # 4. per-stock per-stage Optuna (6 stage_filter × len(stocks) 组合)
    STAGE_FILTERS = ['all', '1', '1.5', '2', '3', '4']
    log.info(f"per-stock per-stage Optuna ({args.trials} trials × {len(sig_by_stock)} 股 × {len(STAGE_FILTERS)} stage = 估 {args.trials * len(sig_by_stock) * len(STAGE_FILTERS):,} trials)...")
    out_rows = []
    t_opt = time.time()

    def make_objective(combos, stage_filter):
        def objective(trial):
            cname = trial.suggest_categorical("combo", list(combos.keys()))
            hp = trial.suggest_categorical("hp", HOLDING_PERIODS)
            vol_min = trial.suggest_float("vol_min", 0.3, 3.0)
            amt_min = trial.suggest_float("amt_min", 0.3, 2.5)
            p_max = trial.suggest_float("p_max", 0.55, 1.0)
            dif_pos = trial.suggest_categorical("dif_pos", [0, 1])
            num_arr, stage_arr = combos[cname]
            hp_idx = HOLDING_PERIODS.index(hp)
            ret_col = 4 + hp_idx
            dd_col = 4 + len(HOLDING_PERIODS) + hp_idx
            mask = (num_arr[:, 1] >= vol_min) & (num_arr[:, 2] >= amt_min) & (num_arr[:, 3] <= p_max)
            mask &= ~np.isnan(num_arr[:, ret_col]) & ~np.isnan(num_arr[:, dd_col])
            if dif_pos:
                mask &= num_arr[:, 0] > 0
            # NEW: stage filter
            if stage_filter != 'all':
                stage_mask = np.array([s == stage_filter for s in stage_arr])
                mask &= stage_mask
            rets = num_arr[mask, ret_col]
            dds = num_arr[mask, dd_col]
            n = len(rets)
            if n < MIN_TRIALS_SIGNALS:
                return -999.0
            win = float((rets > 0).mean())
            avg_ret = float(rets.mean())
            avg_dd = float(dds.mean())
            calmar = avg_ret / max(abs(avg_dd), 0.005)
            return float(calmar * win * math.log(1.0 + n))
        return objective

    n_studies = 0
    for ci, (code, combos) in enumerate(sig_by_stock.items()):
        for stage_filter in STAGE_FILTERS:
            study = optuna.create_study(direction="maximize",
                                         sampler=optuna.samplers.TPESampler(seed=42))
            try:
                study.optimize(make_objective(combos, stage_filter),
                              n_trials=args.trials, show_progress_bar=False)
            except Exception:
                continue
            n_studies += 1
            if study.best_value <= -100:
                continue
            bp = study.best_params
            # 算最佳配置 metrics
            cname = bp["combo"]; hp = bp["hp"]
            num_arr, stage_arr = combos[cname]
            hp_idx = HOLDING_PERIODS.index(hp)
            mask = (num_arr[:, 1] >= bp["vol_min"]) & (num_arr[:, 2] >= bp["amt_min"]) & (num_arr[:, 3] <= bp["p_max"])
            ret_col = 4 + hp_idx; dd_col = 4 + len(HOLDING_PERIODS) + hp_idx
            mask &= ~np.isnan(num_arr[:, ret_col]) & ~np.isnan(num_arr[:, dd_col])
            if bp["dif_pos"]: mask &= num_arr[:, 0] > 0
            if stage_filter != 'all':
                mask &= np.array([s == stage_filter for s in stage_arr])
            rets = num_arr[mask, ret_col]; dds = num_arr[mask, dd_col]
            n = len(rets)
            if n < MIN_TRIALS_SIGNALS:
                continue
            win = float((rets > 0).mean())
            avg_ret = float(rets.mean())
            avg_dd = float(dds.mean())
            sd = float(rets.std())
            sharpe = float(avg_ret * 252 / hp / sd) if sd > 0 else 0.0
            calmar = float(avg_ret / max(abs(avg_dd), 0.005))
            score = float(study.best_value)
            out_rows.append((
                code, stage_filter, cname, hp,
                bp["vol_min"], bp["amt_min"], bp["p_max"], bool(bp["dif_pos"]),
                n, win, avg_ret, avg_dd, sharpe, calmar, score,
                args.trials, args.start, args.end,
            ))
        if (ci + 1) % 20 == 0:
            log.info(f"  {ci+1}/{len(sig_by_stock)} ({time.time()-t_opt:.0f}s) — {n_studies} studies / {len(out_rows)} configs")

    log.info(f"Optuna 完成 ({time.time()-t_opt:.1f}s) — {n_studies} studies / {len(out_rows)} 个有效最佳配置")

    # 5. 写库
    log.info("写库...")
    from services.db import get_conn
    conn = get_conn()
    try:
        conn.executescript(DDL)
        conn.execute("BEGIN TRANSACTION")
        try:
            conn.execute("DELETE FROM mart_per_stock_optuna_best")
            conn.executemany(
                """INSERT INTO mart_per_stock_optuna_best
                   (stock_code, stage_filter, macd_combo, holding_days,
                    vol_min, amt_min, price_max, dif_positive,
                    n_signals, win_rate, avg_ret, avg_dd, sharpe, calmar, score,
                    optuna_n_trials, eval_start_date, eval_end_date)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                out_rows,
            )
            conn.execute("COMMIT")
        except BaseException:
            try: conn.execute("ROLLBACK")
            except Exception: pass
            raise

        # 简单 top 15 报告 (按 score 降序)
        print(f"\n{'='*120}")
        print(f"  Per-stock × Per-stage Optuna 最优配置 (Top 15 by score)")
        print(f"{'='*120}")
        print(f"{'股票':>8} {'阶段':>6} {'combo':>6} {'hp':>4} {'vol≥':>5} {'amt≥':>5} {'p≤':>5} {'DIF+':>5} "
              f"{'n':>4} {'胜率':>6} {'均收益':>8} {'均DD':>8} {'Sharpe':>7} {'Calmar':>7} {'score':>7}")
        top_rows = sorted(out_rows, key=lambda x: x[14], reverse=True)[:15]
        for r in top_rows:
            stage = r[1] if r[1] != 'all' else '全部'
            print(f"{r[0]:>8} {stage:>6} {r[2]:>6} {r[3]:>4}d {r[4]:>5.2f} {r[5]:>5.2f} {r[6]:>5.2f} "
                  f"{('Y' if r[7] else 'N'):>5} "
                  f"{r[8]:>4} {r[9]*100:>5.1f}% {r[10]*100:>+7.2f}% {r[11]*100:>+7.2f}% "
                  f"{r[12]:>7.2f} {r[13]:>7.2f} {r[14]:>7.2f}")
        print(f"{'='*120}")

        # stage 分布
        from collections import Counter
        stage_counts = Counter(r[1] for r in out_rows)
        print(f"\n按 stage_filter 分布: {dict(stage_counts)}")
    finally:
        conn.close()
    log.info(f"=== 总耗时 {time.time()-t_total:.0f}s | {len(out_rows)} 个有效配置 ===")


if __name__ == "__main__":
    main()
