"""technical_states.classifier — 读声明式 config 给任一股任一时点识别技术形态 (软隶属度+子态+多TF)。

owner=backend/services/technical_states/ + config/technical_states.yaml (档案系统维度①)。
设计 (用户 J1): 状态**公式结构在 config**(声明式条件列表, 人话指标+%/倍数单位); 本文件只持 evaluator —
把每条条件解释成 sigmoid 软门取积。加/改状态 = 改 config 不动本文件。
软隶属度: 一股可同时部分属多态 (softmax 温度); 报熵 (低=干净主态/高=过渡态)。多TF: 日线主态 + 周/月线确认。
"""
from __future__ import annotations

import math
from functools import lru_cache
from pathlib import Path

import yaml

from services.technical_states.features import FEATURE_KEYS, compute  # noqa: F401 (re-export)

_CFG_PATH = Path(__file__).resolve().parents[2] / "config" / "technical_states.yaml"

# 人话单位 → 内部值除数 (config 阈值是人话单位, evaluator 转内部特征量纲)
_UNIT_DIV = {"百分比": 100.0, "比例": 1.0, "倍数": 1.0, "千分比": 1000.0, "标准差": 1.0}
# 缺失特征的安全缺省 (仅次要门; 必需特征缺失 → 整 bar 无主态)
_DEFAULTS = {"vol_ratio": 1.0, "range_pct": 0.3, "ma_align": 0.5, "ma_dist": 0.0}
_REQUIRED = ("ma_slope", "mom20", "pctile")   # 这三个缺 = 无法判主态


@lru_cache(maxsize=1)
def load_config(path: str | None = None) -> dict:
    return yaml.safe_load(Path(path or _CFG_PATH).read_text(encoding="utf-8"))


def _sig(x, c, k):
    """sigmoid(k*(x-c)), clip 防 overflow。"""
    z = max(min(k * (x - c), 50.0), -50.0)
    return 1.0 / (1.0 + math.exp(-z))


def _indicator_map(cfg: dict) -> dict:
    """人话指标名 → (内部key, 单位)。"""
    return {name: (spec["key"], spec.get("单位", "比例")) for name, spec in cfg["指标"].items()}


def _is_nan(v) -> bool:
    return v is None or (isinstance(v, float) and math.isnan(v))


def _gate(x: float, cond: dict, unit: str) -> float:
    """单条人话条件 → [0,1] 软门。判断: 高于/低于/平缓(|x|<阈值)。"""
    thr = cond["阈值"] / _UNIT_DIV.get(unit, 1.0)
    k = cond["锐度"]
    j = cond["判断"]
    if j == "高于":
        return _sig(x, thr, k)
    if j == "低于":
        return _sig(thr, x, k)
    return _sig(thr, abs(x), k)   # 平缓: 越接近0越满足


def state_scores(f: dict, cfg: dict) -> dict:
    """单 bar 特征 f -> {state: [0,1] 匹配分}; 公式结构读 config.状态 声明式条件。必需特征 NaN -> 全 NaN。"""
    imap = _indicator_map(cfg)
    keys = {k for k, _ in imap.values()}
    raw = {k: f.get(k) for k in keys}
    if any(_is_nan(raw.get(rk)) for rk in _REQUIRED):
        return {s: float("nan") for s in cfg["状态"]}
    fv = {k: (_DEFAULTS.get(k, 0.0) if _is_nan(v) else v) for k, v in raw.items()}
    out = {}
    for state, spec in cfg["状态"].items():
        score = 1.0
        for cond in spec["条件"]:
            key, unit = imap[cond["指标"]]
            score *= _gate(fv[key], cond, unit)
        out[state] = score
    return out


def _sub_cond_met(cond: dict, f: dict, mmap: dict, thr: dict) -> bool:
    """单条子态条件: {指标:修饰指标, 大于|小于:子态阈值名}; 阈值=原始量纲(不换算)。NaN→不满足。"""
    key = mmap.get(cond["指标"])
    x = f.get(key)
    if _is_nan(x):
        return False
    if "大于" in cond:
        return x > thr[cond["大于"]]
    if "小于" in cond:
        return x < thr[cond["小于"]]
    return False


def _sub_state(top: str, f: dict, cfg: dict) -> str:
    """主态 top 下分子态 — **全 config 驱动** (D2: 读 config.子态规则, 消除硬编码)。
    规则按序匹配, 全 AND 满足即取该子态; 无条件规则=默认。放量下跌=按价格分位位置消歧(config 化)。
    """
    rules = cfg.get("子态规则", {}).get(top)
    if not rules:
        subs = cfg["子态"].get(top, [None])
        return subs[0]
    mmap = cfg.get("修饰指标", {})
    thr = cfg["子态阈值"]
    for rule in rules:
        conds = rule.get("条件")
        if not conds:                                          # 默认规则 (无条件)
            return rule["则"]
        if all(_sub_cond_met(c, f, mmap, thr) for c in conds):
            return rule["则"]
    return None


def classify_bar(f: dict, cfg: dict | None = None) -> dict:
    """单 bar 识别: {membership, dominant, sub_state, entropy, covered}。covered=False 即过渡/无清晰主态。"""
    cfg = cfg or load_config()
    order = list(cfg["状态"])
    sc = state_scores(f, cfg)
    vals = [sc[s] for s in order]
    if any(math.isnan(v) for v in vals):
        return {"membership": None, "dominant": None, "sub_state": None, "entropy": None, "covered": False}
    mx = max(vals)
    covered = mx > cfg["soft"]["最低分"]
    tau = cfg["soft"]["温度"]
    ex = [math.exp(max(min(v / tau, 50), -50)) for v in vals]
    z = sum(ex)
    m = {order[i]: ex[i] / z for i in range(len(order))}
    ent = -sum(p * math.log(p + 1e-12) for p in m.values()) / math.log(len(order))
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
    返回 {date -> {daily, daily_sub, weekly, monthly, entropy, mtf_aligned}}。mtf_aligned=日周月主态方向一致。
    """
    cfg = cfg or load_config()
    dd = classify_series(daily_feats, cfg)
    wk = classify_series(weekly_feats, cfg)
    mo = classify_series(monthly_feats, cfg)
    import bisect
    wk_dates = sorted(wk); mo_dates = sorted(mo)
    bull = {"放量突破", "上升通道", "缩量上涨", "缩量回踩"}
    bear = {"下跌通道", "高位滞涨", "放量下跌"}   # 中继平台=neutral (整理)

    def _asof(dates, table, d):
        """最近 ≤d 的 bar 主态 (PIT as-of); dates 已排序, bisect O(log n)。"""
        i = bisect.bisect_right(dates, d) - 1
        return table[dates[i]]["dominant"] if i >= 0 else None

    def side(s):
        return "bull" if s in bull else "bear" if s in bear else "neutral"

    out = {}
    for d, r in dd.items():
        wdom = _asof(wk_dates, wk, d)
        mdom = _asof(mo_dates, mo, d)
        ddom = r["dominant"]
        aligned = ddom is not None and side(ddom) == side(wdom) == side(mdom) and side(ddom) != "neutral"
        out[d] = {"daily": ddom, "daily_sub": r["sub_state"], "weekly": wdom, "monthly": mdom,
                  "entropy": r["entropy"], "mtf_aligned": aligned}
    return out
