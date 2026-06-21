"""technical_states.features — 技术形态特征计算 (PIT, 多时间框架日/周/月)。

owner=backend/services/technical_states/ + config/technical_states.yaml。
PIT 铁律 (CLAUDE §4.1): 每 bar i 的特征只用 bars[:i+1]; 量基准用 bars[:i] 排除当日 (盘中量未完整)。
多时间框架: resample 日线→周/月线 (PIT: 周bar用该周完整数据, 特征在周bar序列上算只看≤当前周)。
"""
from __future__ import annotations

from datetime import date, datetime

import numpy as np

# 特征向量维度顺序 (classifier 依赖)
FEATURE_KEYS = ["ma_dist", "ma_slope", "mom20", "range_pct", "pctile", "vol_ratio",
                "ma_align", "er", "r2", "accel", "maxdd", "zvol"]
# 默认窗口 (日线); 周/月线由 config.timeframes[tf].windows 覆盖 (按 TF 缩放, 防月线用 40个月窗)
DEFAULT_WINDOWS = {"ma": [5, 10, 20, 60], "er": 40, "pctile": 120, "mom": 20, "range": 60, "vol": 20}


def _period_key(d: str, rule: str):
    """resample 分组键: 'W'=ISO周 / 'ME'=月。d=ISO 日期串。"""
    dt = datetime.strptime(d[:10], "%Y-%m-%d").date() if isinstance(d, str) else d
    if rule == "W":
        iso = dt.isocalendar()
        return (iso[0], iso[1])
    return (dt.year, dt.month)


def resample(dates, o, h, l, c, v, rule):
    """日线 OHLCV → 周/月线 (open=首/high=max/low=min/close=尾/vol=sum)。返回新序列 (PIT, 每周期闭合后才出bar)。"""
    out_d, out_o, out_h, out_l, out_c, out_v = [], [], [], [], [], []
    cur = None
    bo = bh = bl = bc = bv = None
    bd = None
    for i in range(len(dates)):
        if c[i] is None or (isinstance(c[i], float) and np.isnan(c[i])):
            continue
        k = _period_key(dates[i], rule)
        if cur is None:
            cur, bo, bh, bl, bc, bv, bd = k, o[i], h[i], l[i], c[i], (v[i] or 0), dates[i]
        elif k == cur:
            bh = max(bh, h[i]) if h[i] is not None else bh
            bl = min(bl, l[i]) if l[i] is not None else bl
            bc, bd = c[i], dates[i]
            bv += (v[i] or 0)
        else:
            out_d.append(bd); out_o.append(bo); out_h.append(bh); out_l.append(bl); out_c.append(bc); out_v.append(bv)
            cur, bo, bh, bl, bc, bv, bd = k, o[i], h[i], l[i], c[i], (v[i] or 0), dates[i]
    if cur is not None:
        out_d.append(bd); out_o.append(bo); out_h.append(bh); out_l.append(bl); out_c.append(bc); out_v.append(bv)
    return out_d, out_o, out_h, out_l, out_c, out_v


def _sma(a, w):
    n = len(a)
    out = np.full(n, np.nan)
    for i in range(w, n):
        out[i] = np.nanmean(a[i-w:i])
    return out


def compute(dates, o, h, l, c, v, *, timeframe="daily", resample_rule=None, warmup=120, windows=None):
    """返回 {date_iso -> {feat: val}} 仅有效(暖机后)bar。windows=按TF缩放的窗口(缺省日线)。"""
    w = {**DEFAULT_WINDOWS, **(windows or {})}
    mw = w["ma"]; er_win, pct_win, mom_w, rng_w, vol_w = w["er"], w["pctile"], w["mom"], w["range"], w["vol"]
    if resample_rule:
        dates, o, h, l, c, v = resample(dates, o, h, l, c, v, resample_rule)
    c = np.array([x if x is not None else np.nan for x in c], float)
    h = np.array([x if x is not None else np.nan for x in h], float)
    lo = np.array([x if x is not None else np.nan for x in l], float)
    vv = np.array([x if x is not None else np.nan for x in v], float)
    n = len(c)
    if n < warmup + er_win + 5:
        return {}
    ma1, ma2, ma3, ma4 = (_sma(c, mw[0]), _sma(c, mw[1]), _sma(c, mw[2]), _sma(c, mw[3]))
    ma_dist = (c - ma3) / ma3                                  # 距中均线 (ma3=主均线)
    ma_slope = np.full(n, np.nan); ma_slope[mom_w:] = (ma3[mom_w:] - ma3[:-mom_w]) / ma3[:-mom_w]
    mom = np.full(n, np.nan); mom[mom_w:] = c[mom_w:] / c[:-mom_w] - 1
    ma_align = ((ma1 > ma2).astype(float) + (ma2 > ma3).astype(float) + (ma3 > ma4).astype(float)) / 3.0
    lv = np.log(np.where(vv > 0, vv, np.nan))
    out = {}
    for i in range(warmup, n):
        rp = (np.nanmax(h[i-rng_w:i]) - np.nanmin(lo[i-rng_w:i])) / (np.nanmean(c[i-rng_w:i]) + 1e-12)
        wc = c[i-pct_win:i+1]
        pct = float(np.nanmean(wc <= c[i]))
        vm = np.nanmean(vv[i-vol_w:i])
        vr = vv[i] / vm if vm and vm > 0 else np.nan
        seg = c[i-er_win:i+1]
        if np.isnan(seg).any():
            er = r2 = accel = mdd = np.nan
        else:
            net = abs(seg[-1] - seg[0]); path = float(np.sum(np.abs(np.diff(seg))))
            er = net / path if path > 0 else 0.0
            lg = np.log(seg); x = np.arange(len(lg))
            sl, ic = np.polyfit(x, lg, 1)
            r2 = 1 - np.sum((lg - (sl*x+ic))**2) / (np.sum((lg-lg.mean())**2) + 1e-12)
            half = len(seg)//2
            accel = float(np.polyfit(np.arange(len(lg)-half), lg[half:], 1)[0] - np.polyfit(np.arange(half), lg[:half], 1)[0])
            rm = np.maximum.accumulate(seg); mdd = float(np.max((rm-seg)/rm))
        sv = lv[i-vol_w:i]
        mu, sd = np.nanmean(sv), np.nanstd(sv)
        zvol = float((lv[i]-mu)/sd) if sd and sd > 0 and not np.isnan(lv[i]) else 0.0
        feats = {"ma_dist": float(ma_dist[i]), "ma_slope": float(ma_slope[i]), "mom20": float(mom[i]),
                 "range_pct": float(rp), "pctile": pct, "vol_ratio": float(vr) if not np.isnan(vr) else np.nan,
                 "ma_align": float(ma_align[i]), "er": float(er) if not np.isnan(er) else np.nan,
                 "r2": float(r2) if not np.isnan(r2) else np.nan, "accel": accel if not np.isnan(accel) else np.nan,
                 "maxdd": mdd if not np.isnan(mdd) else np.nan, "zvol": zvol}
        if not any(np.isnan(x) for x in (feats["ma_slope"], feats["mom20"], feats["pctile"])):
            out[str(dates[i])] = feats
    return out
