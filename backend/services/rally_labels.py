"""主升浪 GT 标签/特征角色守卫 — 读 rally_gt_columns.yaml 契约, 执法 outcome 不当训练 X (防 leakage)。

owner=backend/config/rally_gt_columns.yaml + analysis/data_validation_backtest_plan_20260619.md。
缘起 (A0 地基止血 #c): fact_rally_ground_truth 混存 PIT 入场态 (base_days) 与 forward outcome
  (gain/peak/dd/bull_aligned)。episode-first 结果倒推: 从赢家反推入场前兆, outcome 当 X = leakage 死
  ("异常高数字" 根源)。本模块把契约变成可调用守门, 训练前 assert_no_outcome_leakage(用到的列)。
"""
from __future__ import annotations

from pathlib import Path

import yaml

_CONTRACT_PATH = Path(__file__).resolve().parent.parent / "config" / "rally_gt_columns.yaml"


def _load() -> dict:
    return yaml.safe_load(_CONTRACT_PATH.read_text(encoding="utf-8")) or {}


_CONTRACT = _load()


def entry_anchor() -> str:
    """PIT 决策点列 (= entry_signal_date 来源, fact_feature_panel JOIN 键)。"""
    return _CONTRACT["entry_anchor"]


def pit_feature_columns() -> list[str]:
    """<=bottom_date 可用, 安全做训练 X 的列。"""
    return list(_CONTRACT.get("pit_features", []))


def label_column() -> str:
    """目标 y 列。"""
    return _CONTRACT["label"]


def outcome_columns() -> list[str]:
    """>bottom_date 事后 outcome, 禁做训练 X 的列。"""
    return list(_CONTRACT.get("outcomes_forbidden_as_x", []))


def assert_no_outcome_leakage(columns) -> None:
    """训练特征集守门: 若含任一 outcome 列 -> raise (leakage 死)。下游建 X 矩阵前调。"""
    bad = sorted(set(columns) & set(outcome_columns()))
    if bad:
        raise ValueError(
            f"GT outcome 列禁做训练 X (leakage 死): {bad}; "
            f"允许 pit_features={pit_feature_columns()} + fact_feature_panel JOIN 的 PIT 因子。"
        )
