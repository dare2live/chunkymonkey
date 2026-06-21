"""technical_states.patterns — 命名形态模板匹配 (D5b, 用户点名 老鸭头/cup-handle 等)。

owner=backend/services/technical_states/ + config/technical_states.yaml 命名形态 段。
命名形态(老鸭头/圆弧底/头肩) = **态序列模板** (refined_dominant 序列的有序子序列), 是量化态的**派生纯函数标签**
  (评审: 不进软隶属/零独立参数/量化冲突以量化为准, 否则第二真相源违宪)。加命名形态=改 config 不动代码。
**PIT 三时点契约** (评审防回贴泄漏总闸): 模板在**完成bar(出水/突破)命中**, 前序态在窗口内 ≤完成bar 找,
  **严禁回贴历史bar(鸭头/吸筹段)** — 命中只写完成bar。decision_date=完成bar, 无未来。
provenance: 命名形态主观性分级(西方经典有统计基础 / 中文实战主观), 标在 config, 主观高的仅描述不当 alpha。
纯函数 (无 DB)。
"""
from __future__ import annotations


def _match_ordered_subsequence(states: list, i: int, seq: list, window: int) -> bool:
    """states[max(0,i-window):i+1] 内是否按序出现 seq (有序子序列, 末态==states[i]=完成bar)。
    PIT: 只看 ≤i 的 bar; 前序态在 i 之前逆向按序找到即匹配。
    """
    if not seq or states[i] != seq[-1]:                # 完成态须匹配模板末态
        return False
    pos = len(seq) - 2                                 # 待匹配的前序态指针 (从倒数第二个)
    lo = max(0, i - window)
    j = i - 1
    while pos >= 0 and j >= lo:
        if states[j] == seq[pos]:
            pos -= 1
        j -= 1
    return pos < 0                                      # 全部前序态按序找到


def match_named_patterns(refined_seq: list, cfg: dict) -> dict:
    """态序列 → 命名形态。refined_seq=[(date, refined_dominant), ...] 时间升序。
    返回 {完成date: [{名称, provenance}]} —— **只在完成bar命中, 不回贴历史bar (PIT三时点)**。
    """
    templates = cfg.get("命名形态") or {}
    if not templates:
        return {}
    dates = [d for d, _ in refined_seq]
    states = [s for _, s in refined_seq]
    out: dict = {}
    for i in range(len(states)):
        for name, tpl in templates.items():
            if _match_ordered_subsequence(states, i, tpl.get("序列", []), tpl.get("窗口", 90)):
                out.setdefault(dates[i], []).append({"名称": name, "provenance": tpl.get("provenance", "")})
    return out
