"""technical_states.features — K线特征 (12 沿用 + 3 新增) + 周期 resample (只见已闭合 bar)。

股票状态特征 (契约: 本文件 + backend/config/technical_states.yaml; 历史证据: technical_states_audit_20260702.json):
- 12 维窗口逻辑直接搬运旧实现 (审查 keeps: 日线特征窗口 PIT 截断 0 diff) + 3 新特征 (H8):
  rv_pctile (已实现波动率滚动分位) / pth (52周高贴近度, George-Hwang) / rs_ratio (Mansfield RS vs 基准)。
- 按审查裁决修正:
  * H1 (CONFIRMED): resample bar 键 = 周期内最后一个**日历交易日** (period_end, 交易日历定) —
    消费方按 `bar_key <= t` as-of 即只见已闭合周期 bar; 周三看到的 weekly = 上周五闭合 bar,
    live 逐日重算与批量回填逐 bit 一致 (旧实现 flush 未闭合尾 bar → 决策日 weekly 23%/monthly 38% 分裂)。
  * medium: MA/range 窗口含当日 (对齐教科书, 旧实现 a[i-w:i] 与 docstring 矛盾);
    pctile 改严格分位 mean(w < c) (tie 不虚高; 旧 <= 使死平股 pctile=1.0 误判高位);
    零成交量 bar → vol_ratio/zvol = NaN (不再默认值伪装量能正常, 下游标 not covered);
    r2 零方差窗 → 0.0 (旧 1e-12 guard 引向 1.0 = 伪完美趋势);
    特征有效性只依赖 bar 下标 i >= warmup (旧 `n < warmup+er+5 return {}` 使历史输出依赖序列总长)。
- 量基准仍排除当日 (vol_ratio 分子当日全天量 / 基准前 vol_w 日) — 审查 keep, EOD 决策合法口径。

PIT 铁律: bar i 特征只用 bars[:i+1]; 全部窗口固定尾窗 (截断不变性: 只要 bar 前有 warmup 根 bar,
前导截断不改变该 bar 特征)。纯函数, 无 DB; 窗口/warmup 全部来自 config (timeframes 节)。
"""
from __future__ import annotations

from datetime import date, datetime

import numpy as np

# 特征向量 15 维 (12 沿用 + rv_pctile/pth/rs_ratio, H8)。
FEATURE_KEYS = ["ma_dist", "ma_slope", "mom20", "range_pct", "pctile", "vol_ratio",
                "ma_align", "er", "r2", "accel", "maxdd", "zvol", "rv_pctile", "pth", "rs_ratio"]
# 这三个缺 = 无法判任何轴 (价格主干) — 整 bar 不输出 (诚实缺席, 非默认值填充)。
REQUIRED_KEYS = ("ma_slope", "mom20", "pctile")


def _period_key(d, rule: str):
    """resample 分组键: 'W'=ISO周 (跨年正确, 审查 keep) / 'ME'=自然月。"""
    if isinstance(d, str):
        dt = datetime.strptime(d[:10], "%Y-%m-%d").date()
    elif isinstance(d, datetime):
        dt = d.date()
    else:
        dt = d
    if rule == "W":
        iso = dt.isocalendar()
        return (iso[0], iso[1])
    return (dt.year, dt.month)


def _iso(d) -> str:
    if isinstance(d, str):
        return d[:10]
    if isinstance(d, (date, datetime)):
        return d.isoformat()[:10]
    return str(d)[:10]


def resample(dates, o, h, l, c, v, rule: str, trading_days):
    """日线 OHLCV → 周/月线 (open=首/high=max/low=min/close=尾/vol=sum)。

    H1 修复核心: 返回的 bar 键 = **周期最后一个日历交易日** (period_end), 非 bar 内最后数据日 —
    消费方 as-of `key <= t` 时, 未走完的周期 (period_end > t) 自动不可见 = 只消费已闭合 bar;
    停牌股周期尾缺数据不影响 (闭合按日历判, 数据齐不齐都在 period_end 定格)。
    trading_days: 升序交易日 (ISO), 必须覆盖 dates 所有周期 (不足 → ValueError fail loud)。
    """
    period_end: dict = {}
    for td in trading_days:
        period_end[_period_key(td, rule)] = _iso(td)   # 升序覆盖 → 留下周期末交易日

    out_k, out_o, out_h, out_l, out_c, out_v = [], [], [], [], [], []
    cur = None
    bo = bh = bl = bc = bv = None

    def _flush():
        pe = period_end.get(cur)
        if pe is None:
            raise ValueError(f"交易日历未覆盖周期 {cur} (rule={rule}) — dim_trading_calendar 覆盖不足")
        out_k.append(pe)
        out_o.append(bo); out_h.append(bh); out_l.append(bl); out_c.append(bc); out_v.append(bv)

    for i in range(len(dates)):
        ci = c[i]
        if ci is None or (isinstance(ci, float) and np.isnan(ci)):
            continue
        k = _period_key(dates[i], rule)
        if cur is None:
            cur, bo, bh, bl, bc, bv = k, o[i], h[i], l[i], ci, (v[i] or 0)
        elif k == cur:
            bh = max(bh, h[i]) if h[i] is not None else bh
            bl = min(bl, l[i]) if l[i] is not None else bl
            bc = ci
            bv += (v[i] or 0)
        else:
            _flush()
            cur, bo, bh, bl, bc, bv = k, o[i], h[i], l[i], ci, (v[i] or 0)
    if cur is not None:
        _flush()
    return out_k, out_o, out_h, out_l, out_c, out_v


def _sma(a, w: int):
    """含当日的简单均线 (medium 修复: 对齐教科书定义; 旧实现排除当日与 docstring 矛盾)。"""
    n = len(a)
    out = np.full(n, np.nan)
    for i in range(w - 1, n):
        seg = a[i - w + 1:i + 1]
        m = seg[~np.isnan(seg)]
        if len(m) == w:            # 窗口内有 NaN → 均线 NaN (诚实缺席)
            out[i] = m.mean()
    return out


def _strict_pctile(window_vals, x) -> float:
    """严格分位: 窗口内严格小于 x 的占比 (medium 修复: tie 不虚高, 死平序列 → 0 非 1)。"""
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return float("nan")
    vals = window_vals[~np.isnan(window_vals)]
    if len(vals) == 0:
        return float("nan")
    return float(np.mean(vals < x))


def compute(dates, o, h, l, c, v, *, windows: dict, warmup: int, resample_rule: str | None = None,
            trading_days=None, bench_close: dict | None = None, start_index: int | None = None) -> dict:
    """返回 {bar_key -> {feat: val}}。bar_key = 日线 ISO 日期 / resample 后 = period_end (H1)。

    - windows/warmup 必传 (来自 config timeframes, 无内置默认 — 双真相源禁止)。
    - 只输出 i >= max(warmup, start_index) 且 REQUIRED_KEYS 非 NaN 的 bar;
      有效性只依赖下标 i (截断不变性), warmup 必须 >= 全特征最深回看 (config 责任, 见 yaml 注释)。
    - bench_close: {ISO日期: 基准收盘} (rs_ratio 用, 缺省 → rs_ratio=NaN)。
    - start_index: 增量构建优化 — 只对 i >= start_index 的 bar 跑重循环 (特征值与全量逐 bit 一致)。
    - 同时输出 vol_ratio_eff / zvol_eff (初值 = 实测值; 涨跌停日由 limits.enrich_features 覆盖
      eff 视图, 实测 vol_ratio/zvol 永不改写 — 审查 medium: 不污染 measured 特征)。
    """
    w = dict(windows)
    if resample_rule:
        if trading_days is None:
            raise ValueError("resample 需要 trading_days (交易日历判周期闭合, H1)")
        dates, o, h, l, c, v = resample(dates, o, h, l, c, v, resample_rule, trading_days)
    keys = [_iso(d) for d in dates]

    def _arr(x):
        return np.array([xi if xi is not None else np.nan for xi in x], float)

    c = _arr(c); h = _arr(h); lo = _arr(l); vv = _arr(v)
    n = len(c)
    start = max(int(warmup), int(start_index) if start_index is not None else 0)
    if n == 0 or start >= n:
        return {}

    mw = w["ma"]
    er_win, pct_win, mom_w, rng_w, vol_w = w["er"], w["pctile"], w["mom"], w["range"], w["vol"]
    pth_win = w.get("pth")
    rv_win, rv_pct_win = w.get("rv"), w.get("rv_pct")
    rs_win = w.get("rs")

    ma1, ma2, ma3, ma4 = (_sma(c, mw[0]), _sma(c, mw[1]), _sma(c, mw[2]), _sma(c, mw[3]))
    ma_dist = (c - ma3) / ma3                                   # 距主均线 (ma3)
    ma_slope = np.full(n, np.nan)
    ma_slope[mom_w:] = (ma3[mom_w:] - ma3[:-mom_w]) / ma3[:-mom_w]
    mom = np.full(n, np.nan)
    mom[mom_w:] = c[mom_w:] / c[:-mom_w] - 1
    ma_align = ((ma1 > ma2).astype(float) + (ma2 > ma3).astype(float) + (ma3 > ma4).astype(float)) / 3.0
    with np.errstate(divide="ignore", invalid="ignore"):
        lv = np.log(np.where(vv > 0, vv, np.nan))               # 零量日 → NaN (不伪装量能正常)

    # 已实现波动率 rv (H8): 滚动 rv_win 日收益 std (样本 std, 与 B1 SQL STDDEV_SAMP 口径一致)
    rv = np.full(n, np.nan)
    if rv_win:
        ret = np.full(n, np.nan)
        ret[1:] = c[1:] / c[:-1] - 1
        rv_from = max(int(rv_win), start - (int(rv_pct_win) if rv_pct_win else 0))
        for i in range(rv_from, n):
            seg = ret[i - rv_win + 1:i + 1]
            if not np.isnan(seg).any():
                rv[i] = float(np.std(seg, ddof=1))

    # Mansfield RS (H8): rp = 价/基准, rs_ratio = rp/SMA(rp, rs_win) - 1 (日线代理窗, 原版 52周周线)
    rs_ratio_arr = np.full(n, np.nan)
    if bench_close and rs_win:
        bv = np.array([bench_close.get(k, np.nan) for k in keys], float)
        with np.errstate(divide="ignore", invalid="ignore"):
            rp = np.where(bv > 0, c / bv, np.nan)
        rp_ma = _sma(rp, rs_win)
        with np.errstate(divide="ignore", invalid="ignore"):
            rs_ratio_arr = np.where(rp_ma > 0, rp / rp_ma - 1, np.nan)

    out = {}
    for i in range(start, n):
        rp_seg_h = h[i - rng_w + 1:i + 1]
        rp_seg_l = lo[i - rng_w + 1:i + 1]
        rp_seg_c = c[i - rng_w + 1:i + 1]
        rngp = (np.nanmax(rp_seg_h) - np.nanmin(rp_seg_l)) / (np.nanmean(rp_seg_c) + 1e-12)
        pct = _strict_pctile(c[i - pct_win + 1:i + 1], c[i])

        vm_seg = vv[i - vol_w:i]                                # 量基准排除当日 (keep)
        vm = np.nanmean(vm_seg) if not np.isnan(vm_seg).all() else np.nan
        vr = (vv[i] / vm) if (vv[i] > 0 and vm and vm > 0) else np.nan

        seg = c[i - er_win:i + 1]
        if np.isnan(seg).any():
            er = r2 = accel = mdd = np.nan
        else:
            net = abs(seg[-1] - seg[0])
            path = float(np.sum(np.abs(np.diff(seg))))
            er = net / path if path > 0 else 0.0
            lg = np.log(seg)
            x = np.arange(len(lg))
            sl, ic = np.polyfit(x, lg, 1)
            ss_tot = float(np.sum((lg - lg.mean()) ** 2))
            # 零方差窗 → r2=0.0 (medium 修复: 旧 guard 引向 1.0 = 伪完美趋势)
            r2 = 1 - float(np.sum((lg - (sl * x + ic)) ** 2)) / ss_tot if ss_tot > 1e-12 else 0.0
            half = len(seg) // 2
            accel = float(np.polyfit(np.arange(len(lg) - half), lg[half:], 1)[0]
                          - np.polyfit(np.arange(half), lg[:half], 1)[0])
            rm = np.maximum.accumulate(seg)
            mdd = float(np.max((rm - seg) / rm))

        sv = lv[i - vol_w:i]
        sv_ok = sv[~np.isnan(sv)]
        if len(sv_ok) > 0 and not np.isnan(lv[i]):
            sd = float(np.std(sv_ok))
            zvol = float((lv[i] - float(np.mean(sv_ok))) / sd) if sd > 0 else np.nan
        else:
            zvol = np.nan                                       # 零量/无基准 → NaN (不 0.0 伪中性)

        pth = np.nan
        if pth_win:
            hh = h[i - pth_win + 1:i + 1]
            hh_ok = hh[~np.isnan(hh)]
            if len(hh_ok) == pth_win and not np.isnan(c[i]):
                pth = float(c[i] / np.max(hh_ok))

        rvp = np.nan
        if rv_win and rv_pct_win:
            rvp = _strict_pctile(rv[i - rv_pct_win + 1:i + 1], rv[i])

        feats = {"ma_dist": float(ma_dist[i]), "ma_slope": float(ma_slope[i]), "mom20": float(mom[i]),
                 "range_pct": float(rngp), "pctile": pct,
                 "vol_ratio": float(vr) if not np.isnan(vr) else np.nan,
                 "ma_align": float(ma_align[i]),
                 "er": float(er) if not np.isnan(er) else np.nan,
                 "r2": float(r2) if not np.isnan(r2) else np.nan,
                 "accel": float(accel) if not np.isnan(accel) else np.nan,
                 "maxdd": float(mdd) if not np.isnan(mdd) else np.nan,
                 "zvol": zvol, "rv_pctile": rvp, "pth": pth,
                 "rs_ratio": float(rs_ratio_arr[i]) if not np.isnan(rs_ratio_arr[i]) else np.nan}
        feats["vol_ratio_eff"] = feats["vol_ratio"]             # 涨跌停日由 limits.enrich 覆盖 eff
        feats["zvol_eff"] = feats["zvol"]
        if not any(np.isnan(feats[k]) for k in REQUIRED_KEYS):
            out[keys[i]] = feats
    return out
