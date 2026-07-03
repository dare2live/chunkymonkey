"""technical_states.labeler — cell→人话标签 + context 两遍 + 多TF as-of (C2: 无方向语义)。

- 人话标签 = cell (位置×趋势×纯度×量能 4 轴取值) → 映射表派生 (config cell映射, 顺序首中);
  load 时校验**全组合覆盖** (3×3×2×3=54 cell 任一无标签 → 抛错, 无静默 fallback — 契约 §2)。
- 前序依赖态 (缩量回踩/中继平台) = context 两遍架构 pass-2 (审查 keep: 前序严格 <=t-1 PIT 0 diff):
  pass-1 逐 bar 轴分类 → pass-2 用前序窗口的**趋势轴取值** (非标签) 多数派 + 当前条件 refine。
- 多 TF: 周/月线特征键 = period_end (features.resample, H1) → as-of `key <= t` 天然只见闭合 bar;
  闭合口径 (2026-07-03 审计修6): weekly/monthly bar 在周期末交易日 EOD 即闭合可见
  (与日线同 EOD 口径), 非次周一 — t=周五 EOD 时该周 bar 已可用;
  只输出各框描述标签, **无 mtf_aligned / bull/bear 集合 / 方向 claim** (C2 CRITICAL 裁决)。
"""
from __future__ import annotations

from bisect import bisect_right
from collections import Counter
from itertools import product

from services.technical_states.axes import AxisEngine, build_engine

# cell 轴序 (E 波动 regime 不进 cell — 值来自 B1, 契约 §8)
CELL_AXES = ("位置", "趋势方向", "趋势纯度", "量能")
# cell映射 规则字段名 → 轴名
_RULE_FIELDS = {"位置": "位置", "趋势": "趋势方向", "纯度": "趋势纯度", "量能": "量能"}
_WILDCARD = "*"


def load_cell_rules(cfg: dict, engine: AxisEngine) -> list[dict]:
    """加载 + 校验 cell 映射: (1) 规则值必须是轴合法取值或 '*'; (2) 54 组合全覆盖。"""
    rules = cfg["cell映射"]
    valid = {axis: set(engine.axes[axis]["values"]) for axis in CELL_AXES}
    for r in rules:
        for field, axis in _RULE_FIELDS.items():
            val = r[field]
            if val != _WILDCARD and val not in valid[axis]:
                raise ValueError(f"cell映射 规则值非法: {field}={val!r} (轴 {axis} 合法取值 {sorted(valid[axis])})")
        if "标签" not in r or "子标签" not in r:
            raise ValueError(f"cell映射 规则缺 标签/子标签: {r}")
    for combo in product(*(sorted(valid[a]) for a in CELL_AXES)):
        if _match_rule(rules, combo) is None:
            raise ValueError(f"cell映射 未覆盖组合 {dict(zip(CELL_AXES, combo))} — 无静默 fallback, 补规则")
    return rules


def _match_rule(rules: list[dict], cell: tuple) -> dict | None:
    for r in rules:
        ok = True
        for (field, _axis), got in zip(_RULE_FIELDS.items(), cell):
            want = r[field]
            if want != _WILDCARD and want != got:
                ok = False
                break
        if ok:
            return r
    return None


class Labeler:
    """config 编译一次, 多股复用 (轴引擎 + cell 规则 + 上下文规则)。"""

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.engine = build_engine(cfg)
        self.cell_rules = load_cell_rules(cfg, self.engine)
        ctx = cfg.get("上下文") or {}
        self.ctx_window = int(ctx.get("前序窗口", 0))
        self.ctx_rules = ctx.get("规则") or []
        for r in self.ctx_rules:
            if r.get("前序轴") not in self.engine.axes:
                raise ValueError(f"上下文规则 前序轴 非法: {r}")

    # ---- pass-1: 逐 bar 轴分类 + cell 标签 ----
    def classify_frame(self, feats_by_key: dict) -> dict:
        """{bar_key: feats} → {bar_key: {axes, label, sub}} (key 升序无要求, 输出同键)。"""
        out = {}
        for k, f in feats_by_key.items():
            ax = self.engine.classify(f)
            cell = tuple(ax[a]["value"] for a in CELL_AXES)
            if any(v is None for v in cell):
                label = sub = None                      # 轴不覆盖 (如零成交量) → 无标签, 诚实缺席
            else:
                r = _match_rule(self.cell_rules, cell)  # load 已校验全覆盖, 必中
                label, sub = r["标签"], r["子标签"]
            out[k] = {"axes": ax, "label": label, "sub": sub}
        return out

    # ---- pass-2: 前序依赖态 refine (PIT: 前序只用 < t 的 pass-1 轴值) ----
    def apply_context(self, rows: dict, feats_by_key: dict) -> dict:
        keys = sorted(rows)
        trend_seq = [rows[k]["axes"]["趋势方向"]["value"] for k in keys]
        for i, k in enumerate(keys):
            r = rows[k]
            if r["label"] is None:
                continue
            for rule in self.ctx_rules:
                prior_axis = rule["前序轴"]
                if prior_axis == "趋势方向":
                    window_vals = trend_seq[max(0, i - self.ctx_window):i]
                else:
                    window_vals = [rows[keys[j]]["axes"][prior_axis]["value"]
                                   for j in range(max(0, i - self.ctx_window), i)]
                window_vals = [v for v in window_vals if v is not None]
                if not window_vals:
                    continue
                if Counter(window_vals).most_common(1)[0][0] != rule["前序值"]:
                    continue
                ax_req = rule.get("当前轴") or {}
                ok = True
                for axis, allowed in ax_req.items():
                    allowed = allowed if isinstance(allowed, list) else [allowed]
                    if r["axes"][axis]["value"] not in allowed:
                        ok = False
                        break
                if ok and all(self.engine.hard_ok(cond, feats_by_key[k])
                              for cond in (rule.get("当前条件") or [])):
                    r["label"], r["sub"] = rule["标签"], rule["子标签"]
                    break
        return rows

    # ---- 多 TF as-of (H1: 键 = period_end, `<= t` 即只见闭合 bar) ----
    @staticmethod
    def asof_label(rows: dict, sorted_keys: list, d: str) -> str | None:
        i = bisect_right(sorted_keys, d) - 1
        return rows[sorted_keys[i]]["label"] if i >= 0 else None
