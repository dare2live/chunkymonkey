"""episode-first GT 列角色契约通用守卫 — 读 *_gt_columns.yaml, 执法 outcome 不当训练 X (防 leakage)。

为何存在 (真金白银/感知死): episode-first 结果倒推 = 从赢家 episode 反推 PIT 入场前兆特征。
  outcome 列 (事后才知的涨幅/峰值/回撤/胜负) 当训练 X = leakage 死 ("异常高数字" 根源)。
  本模块把每张 GT 表的列角色契约 (yaml) 变成可调用守门, 下游建 X 矩阵前 assert_no_outcome_leakage。

v2 重立 (2026-07-02, 修正#4 "第一天重立, 不做事后补"): 逻辑照搬 v1.5 归档版
  (gt_label_contract.py @ 13b95a62, 2026-06-28 纯数据平台重建时随加工层退役);
  v1.5 的 rally_labels.py 委托层不复活 (已无该模块), 消费方直接以 contract 文件名调用。
owner: backend/config/*_gt_columns.yaml (当前: rally_gt_columns.yaml)。
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml

_CONFIG = Path(__file__).resolve().parent.parent / "config"


@lru_cache(maxsize=None)
def _load(contract: str) -> dict:
    p = _CONFIG / contract
    if not p.exists():
        raise FileNotFoundError(f"GT 列契约不存在: {contract}")
    return yaml.safe_load(p.read_text(encoding="utf-8")) or {}


def entry_anchor(contract: str) -> str:
    """PIT 决策点列 (= entry_signal_date 来源, 特征面 JOIN 键)。"""
    return _load(contract)["entry_anchor"]


def pit_feature_columns(contract: str) -> list[str]:
    """<= 锚日可用, 安全做训练 X 的列。"""
    return list(_load(contract).get("pit_features", []))


def label_column(contract: str) -> str:
    """目标 y 列。"""
    return _load(contract)["label"]


def outcome_columns(contract: str) -> list[str]:
    """> 锚日事后 outcome, 禁做训练 X 的列。"""
    return list(_load(contract).get("outcomes_forbidden_as_x", []))


def meta_columns(contract: str) -> list[str]:
    """非特征非 outcome 的元信息列 (landing 断言核契约-表同步用)。"""
    return list(_load(contract).get("meta", []))


def assert_no_outcome_leakage(contract: str, columns) -> None:
    """训练特征集守门: 若含任一 outcome 列 -> raise (leakage 死)。下游建 X 矩阵前调。"""
    bad = sorted(set(columns) & set(outcome_columns(contract)))
    if bad:
        raise ValueError(
            f"[{contract}] GT outcome 列禁做训练 X (leakage 死): {bad}; "
            f"允许 pit_features={pit_feature_columns(contract)} + PIT 特征面 JOIN 的因子。"
        )
