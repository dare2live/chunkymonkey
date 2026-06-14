"""寻参计划校验闸 — 搜索空间非空 (地基-reset 后最小重建)。

反例 (2026-05-26): 29/34 公式无 search space 跑 Optuna = 100 trials 重复跑同一点, 白烧算力。
本闸在寻参 RUN 前强制: 每个待寻参公式必须在 formula_search_spaces.yaml 有非空网格, 否则 raise。
验的不是"能不能跑", 是"跑了有没有用" (CLAUDE.md §3.6 grill gate 代码级)。
"""
from __future__ import annotations

from pathlib import Path

import yaml

SEARCH_SPACES = Path(__file__).resolve().parents[1].parent / "config" / "experiments" / "formula_search_spaces.yaml"


class PlanValidationError(RuntimeError):
    """寻参计划不通过 — 阻断 RUN (同 backtest_preflight 强度)。"""


def load_search_spaces(path: Path = SEARCH_SPACES) -> dict:
    return (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("formula_search_spaces", {})


def grid_size(axes: dict) -> int:
    """轴笛卡尔积组合数 (空轴 -> 0)。"""
    if not axes:
        return 0
    n = 1
    for values in axes.values():
        if not values:
            return 0
        n *= len(values)
    return n


def enforce_search_space_nonempty(formulas: list[str], spaces: dict | None = None) -> dict:
    """每公式必须有非空网格 (>=1 组合)。不通过 raise PlanValidationError。

    返回 {formula: grid_size} (全 PASS 时)。空/缺失 = FAIL。
    """
    spaces = spaces if spaces is not None else load_search_spaces()
    empty = [f for f in formulas if grid_size(spaces.get(f, {})) < 1]
    if empty:
        raise PlanValidationError(
            f"{len(empty)}/{len(formulas)} 公式无搜索空间 (寻参=重复跑同一点, 白烧): {empty}")
    return {f: grid_size(spaces[f]) for f in formulas}
