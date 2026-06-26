"""主升浪 GT 标签/特征角色守卫 — 读 rally_gt_columns.yaml 契约, 执法 outcome 不当训练 X (防 leakage)。

owner=backend/config/rally_gt_columns.yaml + analysis/data_validation_backtest_plan_20260619.md。
缘起 (A0 地基止血 #c): fact_rally_ground_truth 混存 PIT 入场态 (base_days) 与 forward outcome
  (gain/peak/dd/bull_aligned)。episode-first 结果倒推: 从赢家反推入场前兆, outcome 当 X = leakage 死
  ("异常高数字" 根源)。本模块把契约变成可调用守门, 训练前 assert_no_outcome_leakage(用到的列)。
"""
from __future__ import annotations

from services import gt_label_contract as _gc

# 2026-06-26 委托通用 gt_label_contract (rally + macd_episode 共用, CLAUDE §3 抽公共); 公共 API 不变。
_CONTRACT = "rally_gt_columns.yaml"


def entry_anchor() -> str:
    """PIT 决策点列 (= entry_signal_date 来源, fact_feature_panel JOIN 键)。"""
    return _gc.entry_anchor(_CONTRACT)


def pit_feature_columns() -> list[str]:
    """<=bottom_date 可用, 安全做训练 X 的列。"""
    return _gc.pit_feature_columns(_CONTRACT)


def label_column() -> str:
    """目标 y 列。"""
    return _gc.label_column(_CONTRACT)


def outcome_columns() -> list[str]:
    """>bottom_date 事后 outcome, 禁做训练 X 的列。"""
    return _gc.outcome_columns(_CONTRACT)


def assert_no_outcome_leakage(columns) -> None:
    """训练特征集守门: 若含任一 outcome 列 -> raise (leakage 死)。下游建 X 矩阵前调。"""
    _gc.assert_no_outcome_leakage(_CONTRACT, columns)
