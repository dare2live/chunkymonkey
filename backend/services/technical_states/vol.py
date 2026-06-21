"""technical_states.vol — 成交量/量能维度 (档案 L2 每日盘面, 成交量独立成维)。

owner=backend/services/technical_states/ + config/technical_states.yaml 成交量 段。
真相源: price_kline (vol/close)。
描述: 量比(vol/MA20)放量/缩量 + **量价配合** (价涨量增=健康真突破 / 价涨量缩=背离顶部预警 /
  价跌量增=恐慌出货 / 价跌量缩=缩量企稳)。
注: L1 features 已用 vol_ratio (放量突破内部判据); L2 独立成维 = 量能解读行 (双栖不矛盾, master §1.5)。
PIT: vol/close ≤t 盘后, 量基准排当日。纯函数。
"""
from __future__ import annotations

import numpy as np


def volume_signals(dates, closes, vols, window: int = 20, cfg=None) -> dict:
    """成交量/量能 (PIT ≤t)。返回 {date:{量比, 量能状态, 量价配合, 涨跌}}。
    量比=vol/前window日均量 (排当日防泄漏); 量价配合=价格方向×量比组合。
    """
    c = (cfg or {}).get("成交量") or {}
    surge = c.get("放量门", 1.5)      # vol/MA20 > 此 = 放量
    shrink = c.get("缩量门", 0.7)     # < 此 = 缩量
    up = c.get("价格涨门", 0.5)       # pct > 此(%) = 价涨
    dn = c.get("价格跌门", 0.5)       # pct < -此 = 价跌
    v = np.array([float(x) if x else np.nan for x in vols], float)
    out = {}
    ds = [str(d) for d in dates]
    for i in range(window, len(ds)):
        base = np.nanmean(v[max(0, i - window):i])   # 前window日均量 (排当日)
        if not (base > 0) or np.isnan(v[i]):
            continue
        vr = v[i] / base
        vol_state = "放量" if vr > surge else "缩量" if vr < shrink else "量平"
        pc = ((closes[i] / closes[i - 1] - 1) * 100.0) if (closes[i] and closes[i - 1]) else 0.0
        if pc > up and vr > 1:
            pv = "价涨量增(健康)"
        elif pc > up and vr < shrink:
            pv = "价涨量缩(背离预警)"           # 上涨无量 = 顶部背离预警
        elif pc < -dn and vr > surge:
            pv = "价跌量增(恐慌出货)"
        elif pc < -dn and vr < shrink:
            pv = "价跌量缩(缩量企稳)"
        else:
            pv = "量价中性"
        out[ds[i]] = {"量比": round(vr, 2), "量能状态": vol_state, "量价配合": pv, "涨跌": round(pc, 2)}
    return out
