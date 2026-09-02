"""technical_states.patterns — 命名形态模板匹配 (审查 keep: 3 模板/零参数/完成bar命中不回贴)。

命名形态 (老鸭头/圆弧底突破/顶部派发转跌) = **标签序列有序子序列模板** 的派生纯函数标签。
PIT 三时点契约 (keep): 模板在完成 bar 命中, 前序在窗口内 <= 完成 bar 逆向找, 严禁回贴历史 bar。
新词表适配: 瞬时"放量突破"态已删 (C1) — 模板元素 "突破事件" 匹配 breakout overlay 事件
(is_breakout_event), 其余元素匹配 form_sub (子标签; v5 顶层重划后位置降至子层, 序列需要
表达位置时只有子标签携带这个信息, cell映射 §2 子标签 = 位置+标签拼接)。
序列元素可以是单个字符串 (精确匹配), 也可以是字符串列表/元组/集合 (命中其中任一即算匹配,
用于表达"同一语义跨多个子标签"如位置不限时枚举 3 个位置, 或量能不限时枚举同位置 3 个量能)。
加/改形态 = 改 config 不动代码。
纯函数, 无 DB。provenance 标主观性 (主观性高的仅描述不当 alpha)。
"""
from __future__ import annotations

BREAKOUT_TOKEN = "突破事件"


def _elem_hit(labels: list, events: list, j: int, elem) -> bool:
    if elem == BREAKOUT_TOKEN:
        return bool(events[j])
    if isinstance(elem, (list, tuple, set)):
        return labels[j] in elem
    return labels[j] == elem


def _match_ordered_subsequence(labels: list, events: list, i: int, seq: list, window: int) -> bool:
    """labels[max(0,i-window):i+1] 内按序出现 seq, 且末元素命中完成 bar i。PIT: 只看 <= i。"""
    if not seq or not _elem_hit(labels, events, i, seq[-1]):
        return False
    pos = len(seq) - 2
    lo = max(0, i - window)
    j = i - 1
    while pos >= 0 and j >= lo:
        if _elem_hit(labels, events, j, seq[pos]):
            pos -= 1
        j -= 1
    return pos < 0


def match_named_patterns(seq: list, cfg: dict) -> dict:
    """seq = [(date, form_sub, is_breakout_event), ...] 时间升序 → {完成date: [{名称, provenance}]}。

    只在完成 bar 命中, 不回贴历史 bar (PIT 三时点, 审查实测 keep: 截断 vs 全量历史命中 0 不一致)。
    """
    templates = cfg.get("命名形态") or {}
    if not templates:
        return {}
    dates = [d for d, _, _ in seq]
    labels = [s for _, s, _ in seq]
    events = [e for _, _, e in seq]
    out: dict = {}
    for i in range(len(labels)):
        for name, tpl in templates.items():
            if _match_ordered_subsequence(labels, events, i, tpl.get("序列", []), tpl.get("窗口", 90)):
                out.setdefault(dates[i], []).append({"名称": name, "provenance": tpl.get("provenance", "")})
    return out
