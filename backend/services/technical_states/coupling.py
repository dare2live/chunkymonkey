"""technical_states.coupling — 边界耦合 resolver (J2: 调一个边界参数, 同步关联的, 并给出变化)。

owner=backend/services/technical_states/ + config/technical_states.yaml 边界耦合 段。
设计 (用户 J2 "形态之间边界参数的关联性, 整体应同步调整并给出调整后的变化"):
状态间的边界共享一条轴 (上升退出≈下跌进入 / 低位上界<高位下界 / 量比有序)。前端调一个滑块时:
  1. apply_coupling 按 config 边界耦合 关系同步关联参数 + 返回人话变化说明;
  2. with_overrides 产出"调整后"的 effective config, 供 classifier 重分类做 before/after 叠加对比。
参数 override 用人话单位 (与 config 阈值同, 如均线斜率 6.55 = 6.55%), key = "状态.指标"。
"""
from __future__ import annotations

import copy


def _set_threshold(cfg: dict, state: str, indicator: str, value: float) -> bool:
    """把 cfg.状态[state] 中 指标==indicator 的条件阈值改成 value (人话单位)。返回是否命中。"""
    hit = False
    for cond in cfg["状态"].get(state, {}).get("条件", []):
        if cond["指标"] == indicator:
            cond["阈值"] = value
            hit = True
    return hit


def with_overrides(cfg: dict, overrides: dict) -> dict:
    """深拷贝 cfg 并应用参数 override ({"状态.指标": 人话单位值}) → effective config。不改原 cfg。"""
    eff = copy.deepcopy(cfg)
    for key, value in overrides.items():
        state, indicator = key.split(".", 1)
        _set_threshold(eff, state, indicator, value)
    return eff


def apply_coupling(overrides: dict, cfg: dict) -> tuple[dict, list[str]]:
    """给用户改动 overrides ({"状态.指标": 新值}), 按 config 边界耦合 同步关联参数。
    返回 (完整同步后 overrides, 人话变化说明 list)。互补对称=自动镜像同步; 互斥/有序=校验并告警(不强改)。
    """
    synced = dict(overrides)
    notes: list[str] = []
    changed = set(overrides)

    def cur(param: str) -> float:
        """param='状态.指标' 当前生效值 (override 优先, 否则 config 现值)。"""
        if param in synced:
            return synced[param]
        state, ind = param.split(".", 1)
        for cond in cfg["状态"].get(state, {}).get("条件", []):
            if cond["指标"] == ind:
                return cond["阈值"]
        return float("nan")

    for rule in cfg.get("边界耦合", []):
        params = rule["参数"]
        rel = rule["关系"]
        if rel == "互补对称" and len(params) == 2:
            a, b = params
            if a in changed and b not in changed:
                synced[b] = -synced[a]
                notes.append(f"{a} 调到 {synced[a]} → {b} 镜像同步到 {synced[b]}")
            elif b in changed and a not in changed:
                synced[a] = -synced[b]
                notes.append(f"{b} 调到 {synced[b]} → {a} 镜像同步到 {synced[a]}")
        elif rel == "互斥不重叠" and len(params) == 2:
            low, high = params           # low(上界) 须 < high(下界)
            if cur(low) >= cur(high):
                notes.append(f"[告警] {low}({cur(low)}) 须 < {high}({cur(high)}), 否则低位/高位区间重叠无过渡带")
        elif rel == "首项高于其余":        # params[0] 须 > 其余每一个 (放量阈 > 各缩量阈, 留中性带)
            head = cur(params[0])
            for p in params[1:]:
                if not (head > cur(p)):
                    notes.append(f"[告警] {params[0]}({head}) 须 > {p}({cur(p)}), 否则放量/缩量带交叉无中性带")
    return synced, notes


def list_tunables(cfg: dict) -> list[dict]:
    """枚举所有可调边界参数 (前端滑块来源): 每条条件 → {param, 指标, 单位, 判断, 当前值, 耦合}。"""
    imap = {name: spec.get("单位", "比例") for name, spec in cfg["指标"].items()}
    coupled = {}
    for rule in cfg.get("边界耦合", []):
        for p in rule["参数"]:
            coupled.setdefault(p, []).append(rule["关系"])
    out = []
    for state, spec in cfg["状态"].items():
        for cond in spec["条件"]:
            param = f"{state}.{cond['指标']}"
            out.append({"param": param, "状态": state, "指标": cond["指标"],
                        "单位": imap.get(cond["指标"], "比例"), "判断": cond["判断"],
                        "当前值": cond["阈值"], "锐度": cond["锐度"],
                        "耦合": coupled.get(param, [])})
    return out
