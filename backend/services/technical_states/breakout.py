"""technical_states.breakout — 突破 event-in-context 检测器 (C1 CRITICAL 修复; D1 GT 起涨点原语)。

F0/审查证伪: 瞬时"放量突破"态 = 裸突破 (71% 高位触发, fwd10 win 40.8% 全场最差) — 已从态系统删除。
本检测器 = **overlay 事件非态** (契约 §4), 三层全 config (理论锚 VCP/O'Neil):
  1. 底盘 context (t-1 及之前): 位置轴 ∈ 底盘位置值 连续 >= 底盘最少天数
     + 波动收缩 (rv_pctile <= 收缩分位门) + 突破前枯量 (枯量窗内中位实测量比 <= 门);
  2. 触发 (t): 收盘破前 破高窗 日高 + 量比 >= 触发量比门 (涨停日用 eff proxy — 封板量截断);
  3. 可成交闸 (H4): 一字板 → tradable=False; 收盘封涨停 → buyable=False (T+1 语义标注留消费方)。
F0 教训明标: 突破胜率 44.5% 是接刀 — 检测器只做描述与 GT 标注原语, **不是买入信号**。
PIT: 底盘用 <=t-1 的 pass-1 轴值, 触发用 <=t 的价量。纯函数, 无 DB。
"""
from __future__ import annotations

import numpy as np


def _nan(v) -> bool:
    return v is None or (isinstance(v, float) and v != v)


def detect(dates, h, c, feats_by_key: dict, rows_by_key: dict, limit_flags: dict, cfg: dict) -> dict:
    """→ {ISO日期: {base_days, trigger_strength, tradable, buyable}} (只含事件日)。

    dates/h/c: 日线原序列 (ISO 键与 feats/rows 对齐); rows_by_key: labeler pass-1 输出 (轴值);
    limit_flags: {日期: {is_up_limit, is_down_limit, is_one_word}} (缺 = 无涨跌停信息)。
    """
    p = cfg["突破"]
    base_pos = set(p["底盘位置值"])
    base_min = int(p["底盘最少天数"])
    base_cap = int(p["底盘统计上限"])
    rv_gate = float(p["收缩分位门"])
    dry_win = int(p["枯量窗"])
    dry_gate = float(p["枯量中位量比门"])
    hi_win = int(p["破高窗"])
    vol_gate = float(p["触发量比门"])

    keys = [str(d)[:10] for d in dates]
    idx = {k: i for i, k in enumerate(keys)}
    h_arr = np.array([x if x is not None else np.nan for x in h], float)
    c_arr = np.array([x if x is not None else np.nan for x in c], float)

    out = {}
    for k, f in feats_by_key.items():
        i = idx.get(k)
        if i is None or i < hi_win:
            continue
        # 2) 触发: 收盘破前 hi_win 日高 (不含当日) + 量比门 (涨停日 vol_ratio_eff=proxy)
        prior_high = np.nanmax(h_arr[i - hi_win:i])
        if _nan(prior_high) or _nan(c_arr[i]) or c_arr[i] <= prior_high:
            continue
        vr_eff = f.get("vol_ratio_eff")
        if _nan(vr_eff) or vr_eff < vol_gate:
            continue
        # 1) 底盘 context (<= t-1): 位置轴连续处于底盘位置值
        streak = 0
        j = i - 1
        while j >= 0 and streak < base_cap:
            rj = rows_by_key.get(keys[j])
            if rj is None or rj["axes"]["位置"]["value"] not in base_pos:
                break
            streak += 1
            j -= 1
        if streak < base_min:
            continue
        prev = feats_by_key.get(keys[i - 1])
        if prev is None or _nan(prev.get("rv_pctile")) or prev["rv_pctile"] > rv_gate:
            continue                                     # 波动未收缩 (VCP) → 非底盘突破
        dry = [feats_by_key[keys[j2]].get("vol_ratio")
               for j2 in range(i - dry_win, i) if keys[j2] in feats_by_key]
        dry = [v for v in dry if not _nan(v)]
        if len(dry) < dry_win or float(np.median(dry)) > dry_gate:
            continue                                     # 突破前未枯量 (O'Neil dry-up)
        # 3) 可成交闸 (H4)
        lim = limit_flags.get(k) or {}
        one_word = lim.get("is_one_word")
        up_close = lim.get("is_up_limit")
        out[k] = {"base_days": streak,
                  "trigger_strength": float(vr_eff),
                  "tradable": (None if one_word is None else not one_word),
                  "buyable": (None if up_close is None else not up_close)}
    return out
