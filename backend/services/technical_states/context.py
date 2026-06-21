"""technical_states.context — 上下文层 (D4, 两遍架构 pass-2)。

owner=backend/services/technical_states/ + config/technical_states.yaml 上下文 段。
评审/D1发现: 升势健康回踩(缩量回踩)本质=**前序趋势依赖**(价 WAS 涨现小回调), 瞬时特征表达不了(全宇宙0.01%死态)。
两遍架构 (评审决策点1 的确定解, PIT 安全): pass-1 瞬时分类(classifier, 只用≤t, 无前瞻) → pass-2 本模块用
  **前序态(as-of ≤t-1)** refine 上下文依赖态 + 标 prior_trend(升/平/跌) 供位置消歧。
PIT 三时点契约 (评审防回贴泄漏总闸): decision_date=t(当前判定bar, 用前序≤t-1 + 当前t, 无未来)。
  需 N 日确认的边界事件(假突破/挖坑)走 trigger_date=t / confirm_date=t+N 分离(D5+, 此处先立契约)。
纯函数 (无 DB)。
"""
from __future__ import annotations

from collections import Counter

_BULL = {"放量突破", "上升通道", "缩量上涨", "中继平台"}
_BEAR = {"下跌通道", "高位滞涨", "放量下跌"}


def _prior_dominant(cls_by_date: dict, dates: list, i: int, window: int) -> str | None:
    """bar i 前 window 根 (<i, PIT 只用历史) 的主导态 (出现最多)。"""
    cnt = Counter()
    for j in range(max(0, i - window), i):
        dom = cls_by_date.get(dates[j], {}).get("dominant")
        if dom:
            cnt[dom] += 1
    return cnt.most_common(1)[0][0] if cnt else None


def _ctx_cond_met(conds: list, f: dict, mmap: dict) -> bool:
    """上下文 当前条件 (原始量纲, 同子态规则): {指标, 大于|小于: 数值}。NaN→不满足。"""
    for c in conds:
        key = mmap.get(c["指标"], c["指标"])
        x = f.get(key)
        if x is None or (isinstance(x, float) and x != x):
            return False
        if "大于" in c and not (x > c["大于"]):
            return False
        if "小于" in c and not (x < c["小于"]):
            return False
    return True


def apply_context(cls_by_date: dict, feats_by_date: dict, cfg: dict) -> dict:
    """pass-2 上下文层: 原地给每 bar 加 prior_trend + context_state + refined_dominant。
    缩量回踩 = 前序窗口主导属升势态 AND 当前 mild 回调(config 上下文条件)。PIT: 前序用 ≤t-1, 当前用 t。
    """
    ctx = cfg.get("上下文") or {}
    window = ctx.get("前序窗口", 10)
    mmap = cfg.get("修饰指标", {})
    pb = ctx.get("缩量回踩") or {}
    pb_prior = set(pb.get("前序态", []))
    pb_conds = pb.get("当前条件", [])
    dates = sorted(cls_by_date)
    for i, d in enumerate(dates):
        r = cls_by_date[d]
        prior = _prior_dominant(cls_by_date, dates, i, window)
        r["prior_trend"] = "升" if prior in _BULL else "跌" if prior in _BEAR else "平"
        f = feats_by_date.get(d, {})
        ctx_state = None
        if prior in pb_prior and _ctx_cond_met(pb_conds, f, mmap):
            ctx_state = "缩量回踩"
        r["context_state"] = ctx_state
        r["refined_dominant"] = ctx_state or r.get("dominant")
    return cls_by_date
