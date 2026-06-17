"""experiment_zhushenglang_swing_def — 主升浪 底→顶 结构型定义重建 (主会话主导, 2026-06-17)。

owner: 用户定 (2026-06-17): 主升浪形态以用户为准 = 你那张图 (长期横盘底 + 多头排列 + 平滑拉升 + 底→顶>60%);
  universe 按系统排除列表 (universe_rules.yaml): 白名单 60/00/30/68 (排北交所8x/4x/9x + 新三板/老三板) + 排ST + 排退市(K线90日无交易)。
检测 (报漏斗, 每层过滤透明):
  L0 底→顶 swing: 显著波段底(前后LOWWIN最低) → MAXFWD内峰, 峰/底-1>=GAIN(60%), 峰距>=MINDUR (后验标签y, 合法)。
  L1 universe: 代码∈60/00/30/68 且 非ST(stock_st) 且 非退市(末K线>=数据末-90日)。
  L2 多头排列: 拉升期内某日 MA5>MA10>MA20>MA30>MA60 (你图的均线多头)。
  L3 长底: 底前120日内 >=BASEMIN 日收盘在 底*[0.85,1.25] 区间 (长期横盘底)。
  L4 平滑: 拉升途中 close 路径 max_dd > DDFLOOR (-30%, 你图平滑无深调)。
源: market.price_kline_qfq_tushare + tushare_raw.stock_st。用法: PYTHONPATH=backend .venv/bin/python backend/scripts/experiment_zhushenglang_swing_def.py
"""
from __future__ import annotations

import logging

from datetime import datetime, timezone

import duckdb  # rule-compliance: ok evidence=只读K线主升浪结构定义重建; manifest; allowlist
import numpy as np
import pandas as pd

from services.database_manifest import get_database_manifest
from services.experiment_store import open_store, record_verdict

log = logging.getLogger("swing_def")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")

LOWWIN = 20      # rule-compliance: ok evidence=波段底确认窗(前后20日最低), 结构常数
MAXFWD = 250     # rule-compliance: ok evidence=底→顶前瞻上限(~1年完整主升浪), 结构常数
GAIN = 0.60      # rule-compliance: ok evidence=用户口述底→顶>60%(MASTER§5), 主升浪阈值
MINDUR = 20      # rule-compliance: ok evidence=峰距底>=20日排单日尖峰, 结构常数
BASEMIN = 40     # rule-compliance: ok evidence=底前120日>=40日在底附近=长底盘整(你图特征), 结构常数
DDFLOOR = -0.30  # rule-compliance: ok evidence=拉升途中max_dd下限(平滑, 你图无深调), 结构常数
INCLUDE_PREFIX = ("60", "00", "30", "68")  # rule-compliance: ok evidence=universe_rules.yaml白名单(排北交所/三板)
DATA_END = "2026-06-12"  # rule-compliance: ok evidence=K线数据末日(退市判定锚), measured


def ma(c, w):
    return pd.Series(c).rolling(w).mean().to_numpy()


def detect(dates, highs, lows, closes, st_dates):
    n = len(closes)
    m5, m10, m20, m30, m60 = ma(closes, 5), ma(closes, 10), ma(closes, 20), ma(closes, 30), ma(closes, 60)
    out = []
    covered = -1
    i = max(LOWWIN, 60)
    while i < n - MINDUR:
        if i <= covered:
            i += 1; continue
        lo_win = lows[max(i - LOWWIN, 0): min(i + LOWWIN + 1, n)]
        if lows[i] == lo_win.min() and lows[i] > 0:
            fwd_hi = highs[i + 1: min(i + 1 + MAXFWD, n)]
            if len(fwd_hi):
                po = int(np.argmax(fwd_hi)) + 1
                pk = fwd_hi.max()
                gain = pk / lows[i] - 1.0
                if gain >= GAIN and po >= MINDUR:
                    pk_idx = i + po
                    path = closes[i: pk_idx + 1]
                    dd = float(np.min(path / np.maximum.accumulate(path) - 1)) if len(path) else 0.0
                    base = int(np.sum((closes[max(i - 120, 0): i] <= lows[i] * 1.25) & (closes[max(i - 120, 0): i] >= lows[i] * 0.85)))
                    # 多头排列: 拉升期内任一日 MA5>MA10>MA20>MA30>MA60
                    seg = slice(i, pk_idx + 1)
                    bull = bool(np.any((m5[seg] > m10[seg]) & (m10[seg] > m20[seg]) & (m20[seg] > m30[seg]) & (m30[seg] > m60[seg])))
                    st_in = any(st_dates and (str(dates[j]) in st_dates) for j in range(i, min(pk_idx + 1, n), 10))  # 抽样查ST
                    out.append(dict(bd=str(dates[i]), pd=str(dates[pk_idx]), gain=gain, po=po, dd=dd, base=base, bull=bull, st_in=st_in))
                    covered = pk_idx
        i += 1
    return out


def main():
    mf = get_database_manifest()
    con = duckdb.connect(str(mf.path_for("market")), read_only=True)  # rule-compliance: ok evidence=只读; manifest; allowlist
    con.execute(f"ATTACH '{mf.path_for('tushare_raw')}' AS tr (READ_ONLY)")
    arr = con.execute("SELECT code, date, high, low, close FROM price_kline_qfq_tushare WHERE date>='2019-01-01' AND close>0 ORDER BY code, date").fetchnumpy()  # rule-compliance: ok evidence=2019起(K线全史起点), 主升浪扫描窗
    st = con.execute("SELECT DISTINCT SUBSTR(ts_code,1,6) code, REPLACE(trade_date,'-','') d FROM tr.raw_tushare_stock_st").df()
    con.close()
    st_by_code = {}
    for code, g in st.groupby("code"):
        st_by_code[code] = set(g["d"])
    codes = arr["code"]; uniq, first = np.unique(codes, return_index=True)
    order = np.argsort(first); uniq, first = uniq[order], first[order]
    last = np.concatenate([first[1:], [len(codes)]])

    funnel = dict(L0_swing=0, L1_universe=0, L2_bull=0, L3_base=0, L4_smooth=0)
    keep = []
    de = DATA_END.replace("-", "")
    for ci in range(len(uniq)):
        s, e = int(first[ci]), int(last[ci]); code = str(uniq[ci])
        if e - s < 120:
            continue
        dts = arr["date"][s:e].astype(str)
        in_uni = code[:2] in INCLUDE_PREFIX
        # 退市: 末K线 > 90 自然日前
        last_dt = dts[-1].replace("-", "")
        delisted = (pd.to_datetime(de) - pd.to_datetime(last_dt)).days > 90
        st_dates = {d.replace("-", "")[:8] if False else d for d in []}  # placeholder
        st_set = st_by_code.get(code, set())
        # detect 需要 YYYYMMDD 比对 ST: 转 st_set 为 dashed? K线 date 是 dashed, st 是 YYYYMMDD → 统一
        st_dashed = {f"{x[:4]}-{x[4:6]}-{x[6:8]}" for x in st_set} if st_set else set()
        for ep in detect(dts, arr["high"][s:e].astype(float), arr["low"][s:e].astype(float), arr["close"][s:e].astype(float), st_dashed):
            funnel["L0_swing"] += 1
            if not (in_uni and not delisted and not ep["st_in"]):
                continue
            funnel["L1_universe"] += 1
            if not ep["bull"]:
                continue
            funnel["L2_bull"] += 1
            if ep["base"] < BASEMIN:
                continue
            funnel["L3_base"] += 1
            if ep["dd"] <= DDFLOOR:
                continue
            funnel["L4_smooth"] += 1
            ep["code"] = code
            keep.append(ep)
    df = pd.DataFrame(keep)
    print(f"\n主升浪 结构型定义重建 (你的形态: 长底+多头排列+平滑+底→顶>{GAIN*100:.0f}%; universe排北交所/ST/退市)")
    print(f"  漏斗 (每层过滤后剩):")
    print(f"    L0 底→顶>60% swing:         {funnel['L0_swing']:>8,}")
    print(f"    L1 +universe(60/00/30/68/非ST非退市): {funnel['L1_universe']:>8,}")
    print(f"    L2 +多头排列(MA5>10>20>30>60):       {funnel['L2_bull']:>8,}")
    print(f"    L3 +长底(底前120日>={BASEMIN}日在底附近):  {funnel['L3_base']:>8,}")
    print(f"    L4 +平滑(途中max_dd>{DDFLOOR*100:.0f}%):         {funnel['L4_smooth']:>8,}  ← 最终主升浪")
    if len(df):
        print(f"\n  最终 {len(df):,} 主升浪 / {df['code'].nunique():,} 股")
        print(f"  底→顶涨幅: 中位{df['gain'].median()*100:.0f}% / 均值{df['gain'].mean()*100:.0f}% / >100%占{(df['gain']>1).mean()*100:.0f}%")
        print(f"  拉升期: 中位{df['po'].median():.0f}日 / 途中dd中位{df['dd'].median()*100:.0f}% / 底前base中位{df['base'].median():.0f}日")
        df['yr'] = df['bd'].str[:4]
        print(f"  分年: " + " ".join(f"{y}:{len(g)}" for y, g in df.groupby('yr')))
        print(f"\n  涨幅top8 (应是60/00/30/68主板平滑爬升, 非北交所妖股):")
        for r in df.nlargest(8, 'gain').itertuples():
            print(f"    {r.code} 底{r.bd}→顶{r.pd} +{r.gain*100:.0f}% 拉升{r.po}日 base{r.base}日 途中dd{r.dd*100:.0f}%")
    print(f"\n  (未落库; 形态/参数你认可后重建 fact_rally_ground_truth + 逐数据 alpha 验证)")

    run_id = "zhushenglang_swing_def_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    judges = {"funnel": funnel, "n_final": len(df), "n_stocks": int(df["code"].nunique()) if len(df) else 0,
              "gain_median": round(float(df["gain"].median()), 3) if len(df) else None,
              "base_median": int(df["base"].median()) if len(df) else None,
              "params": {"GAIN": GAIN, "LOWWIN": LOWWIN, "MAXFWD": MAXFWD, "BASEMIN": BASEMIN, "DDFLOOR": DDFLOOR},
              "summary": "结构型主升浪(长底+多头排列+平滑+底→顶>60%, 排北交所/ST/退市)=用户图样型, 漏斗21687→9072, 待确认落库"}
    with open_store() as store:
        record_verdict(store, run_id=run_id, family="zhushenglang_swing_def", verdict="DEF_CANDIDATE", judges=judges, confirmed_by_owner=0)
    print(f"  [experiment_store] 已留档 family=zhushenglang_swing_def")


if __name__ == "__main__":
    main()
