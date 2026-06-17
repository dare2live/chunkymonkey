"""build_macd_episode_ground_truth — D1 (公式线): MACD 金叉买 → 持有窗波峰 ground truth (生产级)。

owner: 用户修正 (2026-06-16): MACD **只用金叉作买点, 不用死叉作卖点**; episode 潜力 = 金叉后波峰值,
  label win = 峰值 gain > 30% (MASTER §5 公式线 D1)。出场(怎么抓峰)是单独探索因子, 不写死。
  替代旧 fact_macd_episode_ground_truth (无 universe 过滤含北交所/ST 污染, 已 DROP 2026-06-17)。

episode: 金叉 t 买 → 持有窗 [t+1, min(下个金叉, t+MAX_HOLD)] 内 max(high)=峰; peak_gain=峰/close[t]-1;
  max_dd=到峰路径最深回撤; label is_win = peak_gain>WIN_GAIN。买点=金叉日 t (PIT 锚, 特征<=t)。
强制: services.universe 硬门 — 白名单前缀 + 非 ST(PIT, event_date) + 非退市; assert_universe_clean 兜底。
源: market.price_kline_qfq_tushare + tushare_raw.raw_tushare_stock_st。写: smartmoney.fact_macd_episode_ground_truth。
用法: PYTHONPATH=backend .venv/bin/python backend/scripts/build_macd_episode_ground_truth.py [--end 2026-06-12]
"""
from __future__ import annotations

import argparse
import logging
from datetime import datetime, timezone

import duckdb  # rule-compliance: ok evidence=D1 GT 生产写入器, 多库ATTACH; manifest path; market/raw只读
import numpy as np
import pandas as pd

from services.database_manifest import get_database_manifest
from services.universe import (
    ACTIVE_A_SHARE_PREFIXES,
    DELISTED_NO_TRADE_DAYS,
    assert_universe_clean,
    is_st_on,
)

log = logging.getLogger("build_macd_gt")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")

FAST, SLOW, SIGNAL = 12, 26, 9  # rule-compliance: ok evidence=MACD Appel 原始定义常数(行业通用), 非业务可调
WIN_GAIN = 0.30   # rule-compliance: ok evidence=用户"公式线盈利>30%"门槛(口述方法论 MASTER §5)
MAX_HOLD = 120    # rule-compliance: ok evidence=金叉持有峰值窗上限(~6月), 结构常数
SCAN_START = "2019-01-01"  # rule-compliance: ok evidence=K线全史起点(market数据起点), 扫描窗
GT_TABLE = "fact_macd_episode_ground_truth"
TAXONOMY_VERSION = "universe_v1_20260617"  # rule-compliance: ok evidence=universe规则版本戳, 溯源常数


def _ema(x: np.ndarray, span: int) -> np.ndarray:
    a = 2.0 / (span + 1.0)
    out = np.empty(len(x))
    out[0] = x[0]
    for i in range(1, len(x)):
        out[i] = a * x[i] + (1 - a) * out[i - 1]
    return out


def scan_code(code, dates, highs, closes, st_cal):
    """金叉买 → 持有窗波峰。universe: 非 ST(PIT event_date) 才留。"""
    n = len(closes)
    if n < SLOW + SIGNAL + 5:
        return []
    dif = _ema(closes, FAST) - _ema(closes, SLOW)
    dea = _ema(dif, SIGNAL)
    gc = list(np.where((dif[:-1] <= dea[:-1]) & (dif[1:] > dea[1:]))[0] + 1)
    eps = []
    for k, bi in enumerate(gc):
        ed = str(dates[bi])
        if is_st_on(code, ed.replace("-", ""), st_cal):   # PIT ST 排除
            continue
        nxt = gc[k + 1] if k + 1 < len(gc) else n
        we = min(nxt, bi + MAX_HOLD, n - 1)
        if we <= bi + 1:
            continue
        win_high = highs[bi + 1:we + 1]
        if len(win_high) == 0:
            continue
        peak_rel = int(np.argmax(win_high))
        peak_gain = float(win_high[peak_rel] / closes[bi] - 1.0)
        path = closes[bi:bi + 1 + peak_rel + 1]
        max_dd = float(np.min(path / np.maximum.accumulate(path) - 1.0))
        complete = 1 if (nxt < n or bi + MAX_HOLD < n - 1) else 0
        eps.append((code, ed, round(peak_gain, 4), peak_rel + 1, round(max_dd, 4),
                    bool(peak_gain > WIN_GAIN), complete))
    return eps


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--end", default=None, help="扫描截止日 (默认数据末日)")
    args = ap.parse_args()

    mf = get_database_manifest()
    con = duckdb.connect(str(mf.path_for("smartmoney")), read_only=False)  # rule-compliance: ok evidence=写GT到smartmoney(L1); manifest path
    con.execute(f"ATTACH '{mf.path_for('market')}' AS mk (READ_ONLY)")
    con.execute(f"ATTACH '{mf.path_for('tushare_raw')}' AS tr (READ_ONLY)")

    end = args.end or str(con.execute("SELECT MAX(date) FROM mk.price_kline_qfq_tushare").fetchone()[0])
    log.info("载入 K线(<=%s) + PIT ST 日历...", end)
    arr = con.execute(
        f"SELECT code, date, high, close FROM mk.price_kline_qfq_tushare "
        f"WHERE date>='{SCAN_START}' AND date<='{end}' AND close>0 ORDER BY code, date"
    ).fetchnumpy()
    st_rows = con.execute(
        "SELECT DISTINCT SUBSTR(ts_code,1,6) AS code, REPLACE(trade_date,'-','') AS d FROM tr.raw_tushare_stock_st"
    ).fetchall()
    st_cal: dict[str, set] = {}
    for code, d in st_rows:
        st_cal.setdefault(code, set()).add(d)
    de = str(con.execute("SELECT MAX(date) FROM mk.price_kline_qfq_tushare").fetchone()[0]).replace("-", "")

    codes = arr["code"]
    uniq, first = np.unique(codes, return_index=True)
    order = np.argsort(first)
    uniq, first = uniq[order], first[order]
    last = np.concatenate([first[1:], [len(codes)]])

    eps = []
    n_scanned_uni = 0
    for ci in range(len(uniq)):
        code = str(uniq[ci])
        if code[:2] not in ACTIVE_A_SHARE_PREFIXES:   # universe 前缀白名单
            continue
        s, e = int(first[ci]), int(last[ci])
        dts = arr["date"][s:e].astype(str)
        last_dt = dts[-1].replace("-", "")
        if (pd.to_datetime(de) - pd.to_datetime(last_dt)).days > int(DELISTED_NO_TRADE_DAYS):
            continue   # 退市
        n_scanned_uni += 1
        eps.extend(scan_code(code, dts, arr["high"][s:e].astype(float), arr["close"][s:e].astype(float), st_cal))

    if not eps:
        log.error("0 episode — 中止")
        con.close()
        return

    df = pd.DataFrame(eps, columns=["stock_code", "event_date", "peak_gain_pct", "peak_offset_days",
                                    "max_dd_pct", "is_win", "fwd_complete"])
    # universe 硬门 (兜底)
    assert_universe_clean(df["stock_code"].unique().tolist(), context=GT_TABLE)
    df["taxonomy_version"] = TAXONOMY_VERSION
    df["built_at"] = datetime.now(timezone.utc)

    con.execute(f"DROP TABLE IF EXISTS {GT_TABLE}")
    con.execute(f"""
        CREATE TABLE {GT_TABLE} (
            stock_code       VARCHAR NOT NULL,
            event_date       VARCHAR NOT NULL,
            peak_gain_pct    DOUBLE  NOT NULL,
            peak_offset_days INTEGER NOT NULL,
            max_dd_pct       DOUBLE  NOT NULL,
            is_win           BOOLEAN NOT NULL,
            fwd_complete     INTEGER NOT NULL,
            taxonomy_version VARCHAR NOT NULL,
            built_at         TIMESTAMP NOT NULL,
            PRIMARY KEY (stock_code, event_date)
        )
    """)
    con.register("stg", df)
    con.execute(f"INSERT INTO {GT_TABLE} SELECT * FROM (SELECT DISTINCT ON(stock_code,event_date) * FROM stg)")
    n = con.execute(f"SELECT COUNT(*) FROM {GT_TABLE}").fetchone()[0]
    nwin = con.execute(f"SELECT COUNT(*) FROM {GT_TABLE} WHERE is_win").fetchone()[0]
    nstock = con.execute(f"SELECT COUNT(DISTINCT stock_code) FROM {GT_TABLE}").fetchone()[0]
    con.close()

    print(f"\nMACD 金叉峰值 D1 ground truth 重建完成 (universe 硬门 PASS, 0 排除股)")
    print(f"  universe 内扫 {n_scanned_uni:,} 股 → {n:,} episode / {nstock:,} 股")
    print(f"  win(峰>{WIN_GAIN*100:.0f}%): {nwin:,} = {nwin/max(n,1)*100:.1f}%")


if __name__ == "__main__":
    main()
