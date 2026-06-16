"""experiment_macd_episode_scan — D1 (其他公式): MACD 金叉买 → 持有窗波峰 ground truth。

用户修正 (2026-06-16): MACD **只用金叉作买点, 不用死叉作卖点**; episode 潜力 = 金叉后的**波峰值**
(像主升浪 买点→峰值), label win = 峰值 gain > 30%。死叉出场太晚=假亏 (旧版死叉口径中位 -2.2%),
真正"金叉能不能带出一波" 看的是峰。出场(怎么抓峰: 均线破位/移动止盈/其他) 是**单独探索的因子, 不写死**。

episode: 金叉 t 买 → 持有窗 [t+1, min(下个金叉, t+MAX_HOLD)] 内 max(high)=峰; peak_gain=峰/close[t]-1;
max_dd=到峰路径最深回撤; label is_win = peak_gain>WIN_GAIN。买点=金叉日 t (PIT 锚, 特征<=t)。
源: market.price_kline_qfq_tushare。落 smartmoney.fact_macd_episode_ground_truth。
用法: PYTHONPATH=backend .venv/bin/python backend/scripts/experiment_macd_episode_scan.py --end 2025-05-31 --land
"""
from __future__ import annotations

import argparse
import logging
from datetime import datetime, timezone

import duckdb  # rule-compliance: ok evidence=D1 ground truth 扫描, read_only市场+写smartmoney; manifest路径; allowlist
import numpy as np

from services.database_manifest import get_database_manifest
from services.experiment_store import open_store, record_verdict

log = logging.getLogger("macd_episode_scan")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")

FAST, SLOW, SIGNAL = 12, 26, 9  # rule-compliance: ok evidence=MACD Appel 原始定义常数(行业通用), 非业务可调
WIN_GAIN = 0.30   # rule-compliance: ok evidence=用户"其他公式盈利>30%"门槛(口述方法论 MASTER §5)
MAX_HOLD = 120    # rule-compliance: ok evidence=金叉持有峰值窗上限(~6月, 防无限持有; 信号到下个金叉自然renew), 与主升浪180同族可调
GT_TABLE = "fact_macd_episode_ground_truth"


def _ema(x: np.ndarray, span: int) -> np.ndarray:
    a = 2.0 / (span + 1.0); out = np.empty(len(x)); out[0] = x[0]
    for i in range(1, len(x)):
        out[i] = a * x[i] + (1 - a) * out[i - 1]
    return out


def scan_code(code: str, dates, highs: np.ndarray, closes: np.ndarray):
    """金叉买 → 持有窗 [t+1, min(下个金叉, t+MAX_HOLD)] 波峰。出场不用死叉。"""
    n = len(closes)
    if n < SLOW + SIGNAL + 5:
        return []
    dif = _ema(closes, FAST) - _ema(closes, SLOW)
    dea = _ema(dif, SIGNAL)
    gc = list(np.where((dif[:-1] <= dea[:-1]) & (dif[1:] > dea[1:]))[0] + 1)  # 金叉 at i+1
    eps = []
    for k, bi in enumerate(gc):
        nxt = gc[k + 1] if k + 1 < len(gc) else n          # 下个金叉 (信号 renew)
        we = min(nxt, bi + MAX_HOLD, n - 1)                 # 持有窗结束
        if we <= bi + 1:
            continue
        win_high = highs[bi + 1:we + 1]
        if len(win_high) == 0:
            continue
        peak_rel = int(np.argmax(win_high))
        peak = win_high[peak_rel]
        peak_gain = peak / closes[bi] - 1.0
        # 到峰路径最深回撤 (用 close, 含买入日)
        path = closes[bi:bi + 1 + peak_rel + 1]
        cummax = np.maximum.accumulate(path)
        max_dd = float(np.min(path / cummax - 1.0))
        complete = 1 if (nxt < n or bi + MAX_HOLD < n - 1) else 0  # 窗自然结束(非数据截断)
        eps.append((code, str(dates[bi]), float(peak_gain), peak_rel + 1, max_dd, bool(peak_gain > WIN_GAIN), complete))
    return eps


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--end", default="2025-05-31")  # rule-compliance: ok evidence=train窗截止(2025-06前), 与主升浪GT同口径
    ap.add_argument("--land", action="store_true")
    args = ap.parse_args()

    mf = get_database_manifest()
    mk = duckdb.connect(str(mf.path_for("market")), read_only=True)  # rule-compliance: ok evidence=只读K线; manifest; allowlist
    arr = mk.execute("SELECT code, date, high, close FROM price_kline_qfq_tushare WHERE date <= ? AND close>0 ORDER BY code, date", [args.end]).fetchnumpy()
    mk.close()
    codes = arr["code"]; uniq, first = np.unique(codes, return_index=True)
    order = np.argsort(first); uniq, first = uniq[order], first[order]
    last = np.concatenate([first[1:], [len(codes)]])
    eps = []
    for ci, code in enumerate(uniq):
        s, e = int(first[ci]), int(last[ci])
        eps.extend(scan_code(str(code), arr["date"][s:e], arr["high"][s:e].astype(float), arr["close"][s:e].astype(float)))
    n = len(eps); wins = sum(1 for x in eps if x[5]); comp = sum(1 for x in eps if x[6])
    gains = sorted(x[2] for x in eps if x[6])
    med = gains[len(gains) // 2] if gains else 0
    log.info("MACD 金叉峰值 episode: %s 个 (%s win峰>30%% = %.1f%%, %s 完整), 完整中位峰gain %.1f%%", f"{n:,}", f"{wins:,}", 100 * wins / max(n, 1), f"{comp:,}", 100 * med)
    run_id = "macd_episode_scan_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    with open_store() as store:
        record_verdict(store, run_id=run_id, family="macd_episode_ground_truth", verdict="MACD_PEAK_SCAN",
                       judges={"n_episodes": n, "win_peak_gt30": wins, "win_rate": round(wins / max(n, 1), 4),
                               "n_complete": comp, "median_peak_gain": round(med, 4),
                               "summary": f"金叉峰值episode {n}个, win峰>30%={100*wins/max(n,1):.1f}%, 完整中位峰gain{100*med:.1f}%"},
                       confirmed_by_owner=0)

    if args.land:
        smart = duckdb.connect(str(mf.path_for("smartmoney")))  # rule-compliance: ok evidence=D1产物落库, 单写; manifest; allowlist
        try:
            smart.execute(f"DROP TABLE IF EXISTS {GT_TABLE}")
            smart.execute(f"CREATE TABLE {GT_TABLE} (stock_code TEXT, event_date TEXT, peak_gain_pct DOUBLE, peak_offset_days INT, max_dd_pct DOUBLE, is_win BOOLEAN, fwd_complete INT, built_at TEXT DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY(stock_code,event_date))")
            import pandas as pd
            df = pd.DataFrame([(c, d, round(g * 100, 4), po, round(dd * 100, 4), w, fc) for c, d, g, po, dd, w, fc in eps],
                              columns=["stock_code", "event_date", "peak_gain_pct", "peak_offset_days", "max_dd_pct", "is_win", "fwd_complete"])
            smart.register("stg", df)
            smart.execute(f"INSERT INTO {GT_TABLE} SELECT stock_code,event_date,peak_gain_pct,peak_offset_days,max_dd_pct,is_win,fwd_complete,CURRENT_TIMESTAMP FROM (SELECT DISTINCT ON(stock_code,event_date) * FROM stg)")
            smart.commit()
        finally:
            smart.close()
        log.info("落库 %s: %s 行", GT_TABLE, f"{n:,}")


if __name__ == "__main__":
    main()
