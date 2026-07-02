"""technical_states.axes — 5 正交轴独立分类器 (H2/H7/C2 修复)。

设计 (契约 §2): 每轴独立小分类器, **跨轴零竞争** (旧 9 态单 softmax 混轴的结构病根移除):
- 单条件 → sigmoid 软门; 取值分 = 门几何均 (∏g)^(1/k) — 分数量纲不随条件数变 (H2);
- 再除以该取值的**联合 max_score** (值域网格数值求解, 含同特征双门耦合) 归一到 [0,1];
- 轴内 softmax (τ 读 config) 出隶属度; 轴取值 = argmax; 轴分 = 归一分。
- 锐度不是自由参数 (H6): k = 锐度常数 / 过渡带 (过渡带 = 人话单位模糊宽度, 全在 yaml)。
- 必需特征 NaN → 整轴 None (诚实不覆盖; 旧 _DEFAULTS 静默缺省偏置已删, audit low)。
- 无 bull/bear/方向语义, 无 mtf_aligned (C2 CRITICAL)。

条件 DSL 全模块唯一 (medium 修复: 旧三套 DSL 收敛):
  {指标: 人话名, 判断: 高于|低于|平缓, 阈值: 人话单位[, 过渡带: 同单位]}
带 过渡带 = 软门 (轴打分); 不带 = 硬布尔 (labeler 上下文 / breakout 复用 hard_ok)。
波动 regime 轴 (E) 不在本文件分类 — 值来自 B1 dim_stock_segment_daily (单一计算点, 契约 §8)。
"""
from __future__ import annotations

import math

# 人话单位 → 内部特征量纲除数 (审查 keep: 单位换算架构; 千分比旧版存在但无使用方 → 删, 奥卡姆)
_UNIT_DIV = {"百分比": 100.0, "比例": 1.0, "倍数": 1.0, "标准差": 1.0}
_JUDGES = ("高于", "低于", "平缓")
_MAX_GRID = 801   # 数学常数: 联合 max_score 值域网格密度 (1-D 求极值, 801 点误差可忽略)


def _sig(z: float) -> float:
    z = max(min(z, 50.0), -50.0)   # 数学常数: exp overflow 防护 (审查 keep)
    return 1.0 / (1.0 + math.exp(-z))


def _gate(x: float, cond: dict) -> float:
    """单条软门 → [0,1]。cond 已转内部量纲 {key, mode, thr, k}。"""
    if cond["mode"] == "高于":
        return _sig(cond["k"] * (x - cond["thr"]))
    if cond["mode"] == "低于":
        return _sig(cond["k"] * (cond["thr"] - x))
    return _sig(cond["k"] * (cond["thr"] - abs(x)))   # 平缓: |x| < 阈值


def _is_nan(v) -> bool:
    return v is None or (isinstance(v, float) and v != v)


def _joint_max_geo(conds: list[dict]) -> float:
    """取值的联合 max_score 的几何均 (H2 归一分母)。

    同特征多门 (区间带) 在该特征值域上 1-D 网格求积极值; 跨特征独立 → 各特征极值连乘。
    """
    by_key: dict[str, list[dict]] = {}
    for cd in conds:
        by_key.setdefault(cd["key"], []).append(cd)
    total = 1.0
    for _key, group in by_key.items():
        lo, hi = group[0]["dom"]
        best = 0.0
        for j in range(_MAX_GRID):
            x = lo + (hi - lo) * j / (_MAX_GRID - 1)
            p = 1.0
            for cd in group:
                p *= _gate(x, cd)
            if p > best:
                best = p
        total *= best
    if total <= 0.0:
        raise ValueError("轴取值条件在值域内联合 max_score=0 — config 判据自相矛盾, fail loud")
    return total ** (1.0 / len(conds))


class AxisEngine:
    """从 config 编译 4 个软分类轴 (位置/趋势方向/趋势纯度/量能) + 共用条件 DSL 解释器。"""

    def __init__(self, cfg: dict):
        soft = cfg["soft"]
        self.tau = float(soft["温度"])
        k_const = float(soft["过渡带锐度常数"])
        self.imap: dict[str, dict] = {}
        for name, spec in cfg["指标"].items():
            div = _UNIT_DIV[spec["单位"]]
            dom = spec["值域"]
            self.imap[name] = {"key": spec["key"], "div": div,
                               "dom": (float(dom[0]) / div, float(dom[1]) / div)}
        self.axes: dict[str, dict] = {}
        for axis, spec in cfg["轴"].items():
            if "取值" not in spec:               # 波动regime 等消费型轴条目 (来源=B1) 不编译
                continue
            values = {}
            for val, conds in spec["取值"].items():
                compiled = []
                for cond in conds:
                    m = self.imap[cond["指标"]]                   # 未知指标 → KeyError fail loud
                    if cond["判断"] not in _JUDGES:
                        raise ValueError(f"未知判断词 {cond['判断']!r} (轴 {axis}/{val})")
                    width = float(cond["过渡带"]) / m["div"]   # 过渡带与阈值同人话单位
                    if width <= 0:
                        raise ValueError(f"过渡带必须 > 0 (轴 {axis}/{val})")
                    compiled.append({"key": m["key"], "mode": cond["判断"],
                                     "thr": float(cond["阈值"]) / (m["div"]),
                                     "k": k_const / width, "dom": m["dom"]})
                values[val] = {"conds": compiled, "max_geo": _joint_max_geo(compiled)}
            self.axes[axis] = {"col": spec["列"], "values": values}

    # ---- 软打分 ----
    def value_score(self, axis: str, value: str, feats: dict) -> float:
        """单取值归一分 [0,1]; 任一所需特征 NaN → NaN。"""
        spec = self.axes[axis]["values"][value]
        prod = 1.0
        for cd in spec["conds"]:
            x = feats.get(cd["key"])
            if _is_nan(x):
                return float("nan")
            prod *= _gate(float(x), cd)
        geo = prod ** (1.0 / len(spec["conds"]))
        return min(geo / spec["max_geo"], 1.0)

    def classify(self, feats: dict) -> dict:
        """{轴名: {value, score, membership}}; 必需特征 NaN → 三项全 None (诚实不覆盖)。"""
        out = {}
        for axis, spec in self.axes.items():
            scores = {val: self.value_score(axis, val, feats) for val in spec["values"]}
            if any(_is_nan(s) for s in scores.values()):
                out[axis] = {"value": None, "score": None, "membership": None}
                continue
            ex = {val: math.exp(max(min(s / self.tau, 50), -50)) for val, s in scores.items()}
            z = sum(ex.values())
            member = {val: e / z for val, e in ex.items()}
            win = max(scores, key=scores.get)
            out[axis] = {"value": win, "score": scores[win], "membership": member}
        return out

    # ---- 硬判定 (同一 DSL, labeler 上下文 / breakout 复用) ----
    def hard_ok(self, cond: dict, feats: dict) -> bool:
        """{指标, 判断, 阈值} 硬布尔; NaN → False (保守不满足, 审查 keep)。"""
        m = self.imap[cond["指标"]]
        x = feats.get(m["key"])
        if _is_nan(x):
            return False
        thr = float(cond["阈值"]) / m["div"]
        j = cond["判断"]
        if j == "高于":
            return float(x) > thr
        if j == "低于":
            return float(x) < thr
        if j == "平缓":
            return abs(float(x)) < thr
        raise ValueError(f"未知判断词 {j!r}")


def build_engine(cfg: dict) -> AxisEngine:
    return AxisEngine(cfg)
