"""technical_states.classifier — 读 config 给任一股任一时点识别技术形态 (软隶属度+子态+多时间框架)。

owner=backend/services/technical_states/ + config/technical_states.yaml。
公式**结构**在此 (稳定); 阈值/k/τ/子态分位等**可调 judgment 参数**全读 config (宪法 §1.0 不 hardcode)。
软隶属度: 一股可同时部分属多态 (softmax τ); 报熵 (低=干净主态/高=过渡态)。多TF: 日线主态 + 周/月线确认。
"""
from __future__ import annotations

import math
from functools import lru_cache
from pathlib import Path

import yaml

from services.technical_states.features import FEATURE_KEYS, compute  # noqa: F401 (re-export)

_CFG_PATH = Path(__file__).resolve().parents[2] / "config" / "technical_states.yaml"


@lru_cache(maxsize=1)
def load_config(path: str | None = None) -> dict:
    return yaml.safe_load(Path(path or _CFG_PATH).read_text(encoding="utf-8"))


def _sig(x, c, k):
    z = max(min(k * (x - c), 50.0), -50.0)   # clip 防 overflow
    return 1.0 / (1.0 + math.exp(-z))


def state_scores(f: dict, cfg: dict) -> dict:
    """单 bar 特征 f -> {state: [0,1] 匹配分}; 公式结构固定, 阈值/k 读 config。NaN 特征 -> 该门 0。"""
    p = {s: cfg["states"][s]["params"] for s in cfg["states"]}
    k = {s: cfg["states"][s]["k"] for s in cfg["states"]}
    g = f.get  # feature getter
    md, ms, mo, rp, pc, vr, al = (g("ma_dist"), g("ma_slope"), g("mom20"), g("range_pct"),
                                  g("pctile"), g("vol_ratio"), g("ma_align"))
    if any(v is None or (isinstance(v, float) and math.isnan(v)) for v in (ms, mo, pc)):
        return {s: float("nan") for s in cfg["states"]}
    vr = 1.0 if (vr is None or math.isnan(vr)) else vr
    rp = 0.3 if (rp is None or math.isnan(rp)) else rp
    al = 0.5 if (al is None or math.isnan(al)) else al
    md = 0.0 if (md is None or math.isnan(md)) else md
    s = {}
    s["低位横盘"] = _sig(p["低位横盘"]["base_pc"]-pc, 0, 10*k["低位横盘"]) * _sig(p["低位横盘"]["base_rp"]-rp, 0, 8*k["低位横盘"]) * _sig(0.04-abs(ms), 0, 30)
    s["放量突破"] = _sig(mo-p["放量突破"]["brk_mo"], 0, 12*k["放量突破"]) * _sig(vr-p["放量突破"]["brk_vr"], 0, 4*k["放量突破"]) * _sig(md, 0, 12)
    s["上升通道"] = _sig(ms-p["上升通道"]["up_ms"], 0, 30*k["上升通道"]) * _sig(al-p["上升通道"]["up_al"], 0, 6*k["上升通道"]) * _sig(mo, 0, 8)
    s["缩量上涨"] = _sig(mo-0.02, 0, 10*k["缩量上涨"]) * _sig(p["缩量上涨"]["shr_vr"]-vr, 0, 5*k["缩量上涨"]) * _sig(ms, 0, 15)
    s["高位滞涨"] = _sig(pc-p["高位滞涨"]["top_pc"], 0, 10*k["高位滞涨"]) * _sig(0.03-abs(ms), 0, 30) * _sig(p["高位滞涨"]["top_mo"]-abs(mo), 0, 15)
    s["下跌通道"] = _sig(p["下跌通道"]["dn_ms"]-ms, 0, 30*k["下跌通道"]) * _sig(-md, 0, 12) * _sig(-mo, 0, 8)
    s["缩量回踩"] = _sig(p["缩量回踩"]["pb_mo"]-mo, 0, 12*k["缩量回踩"]) * _sig(mo+0.08, 0, 12) * _sig(p["缩量回踩"]["pb_vr"]-vr, 0, 5*k["缩量回踩"]) * _sig(ms, 0, 12)
    return s


def _sub_state(top: str, f: dict, cfg: dict) -> str:
    """主态 top 下按修饰量(ER/maxDD/accel/zvol)分子态; 阈值读 config.sub_thresholds。"""
    t = cfg["sub_thresholds"]
    er, mdd, ac, zv = f.get("er"), f.get("maxdd"), f.get("accel"), f.get("zvol")
    nan = lambda x: x is None or (isinstance(x, float) and math.isnan(x))
    if top == "上升通道":
        if not nan(ac) and ac > t["up_accel"]:
            return "加速上涨"
        if not nan(er) and er > t["up_er"] and not nan(mdd) and mdd < t["up_dd"]:
            return "温和上涨"
        return "震荡上涨"
    if top == "低位横盘":
        return "地量横盘" if (not nan(zv) and zv < t["base_zv"]) else "温和横盘"
    if top == "放量突破":
        return "巨量突破" if (not nan(zv) and zv > t["brk_zv"]) else "温和突破"
    if top == "高位滞涨":
        return "放量派发" if (not nan(zv) and zv > t["top_zv"]) else "缩量惜售"
    if top == "下跌通道":
        return "加速杀跌" if (not nan(ac) and ac < -t["dn_accel"] and not nan(zv) and zv > 0) else "温和阴跌"
    if top == "缩量回踩":
        return "深回踩" if (not nan(mdd) and mdd > t["pb_dd"]) else "浅回踩"
    subs = cfg["sub_states"].get(top, [None])
    return subs[0]


def classify_bar(f: dict, cfg: dict | None = None) -> dict:
    """单 bar 识别: {membership, dominant, sub_state, entropy, covered}。covered=False 即过渡/无清晰主态。"""
    cfg = cfg or load_config()
    order = list(cfg["states"])
    sc = state_scores(f, cfg)
    vals = [sc[s] for s in order]
    if any(math.isnan(v) for v in vals):
        return {"membership": None, "dominant": None, "sub_state": None, "entropy": None, "covered": False}
    mx = max(vals)
    covered = mx > cfg["soft"]["min_score"]
    tau = cfg["soft"]["tau"]
    ex = [math.exp(max(min(v/tau, 50), -50)) for v in vals]
    z = sum(ex)
    m = {order[i]: ex[i]/z for i in range(len(order))}
    ent = -sum(p*math.log(p+1e-12) for p in m.values()) / math.log(len(order))
    dom = order[vals.index(mx)]
    sub = _sub_state(dom, f, cfg) if covered else None
    return {"membership": m, "dominant": dom if covered else None, "sub_state": sub,
            "entropy": ent, "covered": covered}


def classify_series(features_by_date: dict, cfg: dict | None = None) -> dict:
    """{date->feat} -> {date->classify_bar}。"""
    cfg = cfg or load_config()
    return {d: classify_bar(f, cfg) for d, f in features_by_date.items()}


def classify_stock(dates, o, h, l, c, v, cfg: dict | None = None) -> dict:
    """便捷 API: 给一股 OHLCV → 多时间框架识别 (读 config 三框窗口)。返回 classify_multi_timeframe。"""
    cfg = cfg or load_config()
    tf = cfg["timeframes"]
    f = {name: compute(dates, o, h, l, c, v, timeframe=name, resample_rule=spec.get("resample"),
                       warmup=spec.get("warmup", 120), windows=spec.get("windows"))
         for name, spec in tf.items()}
    return classify_multi_timeframe(f["daily"], f["weekly"], f["monthly"], cfg)


def classify_multi_timeframe(daily_feats: dict, weekly_feats: dict, monthly_feats: dict,
                             cfg: dict | None = None) -> dict:
    """多时间框架聚合: 每日线 bar 配最近≤当日的周/月线主态 (确认/辅助)。
    返回 {date -> {daily, weekly, monthly(dominant), mtf_aligned}}。mtf_aligned=日周月主态方向一致。
    """
    cfg = cfg or load_config()
    dd = classify_series(daily_feats, cfg)
    wk = classify_series(weekly_feats, cfg)
    mo = classify_series(monthly_feats, cfg)
    wk_dates = sorted(wk); mo_dates = sorted(mo)
    bull = {"放量突破", "上升通道", "缩量上涨", "缩量回踩"}
    bear = {"下跌通道", "高位滞涨"}

    def _asof(dates, table, d):
        prev = None
        for x in dates:
            if x <= d:
                prev = x
            else:
                break
        return table.get(prev, {}).get("dominant") if prev else None
    out = {}
    for d, r in dd.items():
        wdom = _asof(wk_dates, wk, d)
        mdom = _asof(mo_dates, mo, d)
        ddom = r["dominant"]
        def side(s):
            return "bull" if s in bull else "bear" if s in bear else "neutral"
        aligned = ddom is not None and side(ddom) == side(wdom) == side(mdom) and side(ddom) != "neutral"
        out[d] = {"daily": ddom, "daily_sub": r["sub_state"], "weekly": wdom, "monthly": mdom,
                  "entropy": r["entropy"], "mtf_aligned": aligned}
    return out
