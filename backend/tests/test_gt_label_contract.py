"""GT 列角色契约通用守卫单测 — macd_episode outcome 防误接 X + rally 委托后 API 不变。"""
from __future__ import annotations

import pytest

from services import gt_label_contract as gc
from services import rally_labels

MACD = "macd_episode_gt_columns.yaml"


# --- macd_episode GT 契约 (2026-06-26 PIT 审计唯一隐患防误接) ---
def test_macd_outcome_columns_declared():
    oc = gc.outcome_columns(MACD)
    assert "peak_gain_pct" in oc
    assert "peak_offset_days" in oc
    assert "max_dd_pct" in oc


def test_macd_anchor_label_pit():
    assert gc.entry_anchor(MACD) == "event_date"
    assert gc.label_column(MACD) == "is_win"
    assert gc.pit_feature_columns(MACD) == []   # 本表无 PIT 特征列, 全经 feature_panel JOIN


def test_macd_assert_blocks_outcome_as_x():
    # 含 forward outcome -> raise (leakage 死)
    with pytest.raises(ValueError):
        gc.assert_no_outcome_leakage(MACD, ["macd_above_zero", "peak_gain_pct"])
    with pytest.raises(ValueError):
        gc.assert_no_outcome_leakage(MACD, ["max_dd_pct"])
    # 纯 PIT 因子 (经 feature_panel JOIN) 不触发
    gc.assert_no_outcome_leakage(MACD, ["macd_above_zero", "range_pos", "stage"])


def test_unknown_contract_raises():
    with pytest.raises(FileNotFoundError):
        gc.outcome_columns("nonexistent_gt_columns.yaml")


# --- rally_labels 重构委托后公共 API 不变 (防回归) ---
def test_rally_labels_delegation_intact():
    assert rally_labels.entry_anchor() == "bottom_date"
    assert rally_labels.label_column() == "is_true_rally"
    assert "base_days" in rally_labels.pit_feature_columns()
    assert "gain_to_peak_pct" in rally_labels.outcome_columns()
    assert "bull_aligned" in rally_labels.outcome_columns()
    with pytest.raises(ValueError):
        rally_labels.assert_no_outcome_leakage(["base_days", "bull_aligned"])
    rally_labels.assert_no_outcome_leakage(["base_days", "mom_60"])  # 纯 PIT 不触发
