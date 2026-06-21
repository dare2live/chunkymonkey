"""technical_states.rs — 相对强度 (RS) 维度 (评审 HIGH 盲点; 个股 vs 大盘/行业)。

owner=backend/services/technical_states/ + config/technical_states.yaml RS 段。
评审 HIGH: 7态全是个股绝对量价, 0 RS维度 → 放量突破/上升通道会纳入'大盘普涨带起的弱势齐涨股'=selection噪音,
  直接威胁 KPI'超额HS300>0'。Weinstein/Minervini 把 RS 升破零轴/RS rank≥70 作突破真伪硬门。
本模块 = **Mansfield RS (时序零轴振荡器)**: RS_ratio=个股/基准, RS_state=RS_ratio vs 其均线(升破=强于大盘)。
  横截面 RS rank (IBD, 需全宇宙面板) 留选股层。RS 是**正交置信度维度**(不改7态, 标强于/弱于大盘)。
PIT: RS 只用 ≤t 的个股与基准收盘。纯函数 (基准 load 在 dossier 服务层)。
"""
from __future__ import annotations

import numpy as np


def relative_strength(dates, stock_close, bench_by_date: dict, window: int = 20,
                      band: float = 0.005, bench_label: str = "大盘") -> dict:
    """Mansfield RS (PIT ≤t): RS_ratio=个股/基准(按date对齐), RS_state=RS_ratio vs 其window均线。
    返回 {date: {rs_ratio, rs_state(强于/弱于/同步<bench_label>), rs_slope}}。band=零轴死区(防抖)。
    bench_label: 基准语义标签 — 大盘(基准=HS300, L2 RS) / 板块(基准=申万行业指数, 个股vs板块相对, dossier 复用)。
    """
    bench = np.array([bench_by_date.get(str(d), np.nan) for d in dates], float)
    sc = np.array([x if x is not None else np.nan for x in stock_close], float)
    with np.errstate(invalid="ignore", divide="ignore"):
        rs = sc / bench                                   # RS 比值 (相对强弱)
    out = {}
    n = len(dates)
    for i in range(window, n):
        if np.isnan(rs[i]):
            continue
        seg = rs[i - window + 1:i + 1]                    # window bars ending at i (PIT ≤i)
        rs_ma = np.nanmean(seg)
        if np.isnan(rs_ma) or rs_ma <= 0:
            continue
        prev = rs[i - window]
        rs_slope = float((rs[i] - prev) / prev) if (not np.isnan(prev) and prev > 0) else None
        state = (f"强于{bench_label}" if rs[i] > rs_ma * (1 + band)
                 else f"弱于{bench_label}" if rs[i] < rs_ma * (1 - band) else f"同步{bench_label}")
        out[str(dates[i])] = {"rs_ratio": float(rs[i]), "rs_state": state, "rs_slope": rs_slope}
    return out
