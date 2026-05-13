"""Phase ψ — Optuna 治理规则中心 (单一职责, Config-driven).

⚠ Rule 5 (Root Cause) + Rule 6 (Measured, Not Estimated) + Rule 7 (Optuna 治理):
    凡是写进 mart_per_stock_*_optimal 表的 sharpe / win_rate / avg_ret, **必须**是
    OOS 实测, 不许 in-sample fit, 不许公式估算. 所有阈值 / 上限 / 必填字段 走
    backend/config/optuna_config.yaml, 不许 hardcode.

⚠ 任何 Optuna 调参脚本入库前调 `enforce_pre_insert(record)`, 不通过 raise.
⚠ 改治理规则 → 改 optuna_config.yaml, 业务脚本不动.
"""
from __future__ import annotations

from typing import Optional

from services.optimization.config import (
    GovernanceConfig, OptunaConfig, get_optuna_config,
)


# 入库前必填字段 (任何 mart_per_stock_*_optimal 表都要这 9 个 OOS 字段)
REQUIRED_OOS_FIELDS = (
    "oos_sharpe",
    "oos_win_rate",
    "oos_avg_ret",
    "oos_n_traded",
    "oos_period_start",
    "oos_period_end",
    "walk_forward_mode",
    "train_n_signals",
    "test_n_signals",
)


# ─────────────────────────────────────────────────────────────────────
# 守门函数 (业务脚本调用)
# ─────────────────────────────────────────────────────────────────────


def enforce_pre_optimize(
    n_trials: int,
    has_seed: bool,
    cfg: Optional[OptunaConfig] = None,
) -> None:
    """`study.optimize(...)` 调用前必须先调此函数, 不通过 raise.

    用法:
        from services.optimization.governance import enforce_pre_optimize
        enforce_pre_optimize(n_trials=100, has_seed=True)
        study.optimize(objective, n_trials=n_trials)
    """
    cfg = cfg or get_optuna_config()
    g = cfg.governance

    if n_trials < g.min_n_trials:
        raise GovernanceViolation(
            f"n_trials={n_trials} < min_n_trials={g.min_n_trials} "
            f"(太少, 结果不稳, 禁止入业务表). 改 optuna_config.yaml.governance.min_n_trials"
        )
    if n_trials > g.max_n_trials:
        raise GovernanceViolation(
            f"n_trials={n_trials} > max_n_trials={g.max_n_trials} "
            f"(边际效益低). 改 optuna_config.yaml.governance.max_n_trials"
        )
    if g.require_sampler_seed and not has_seed:
        raise GovernanceViolation(
            "Optuna sampler 必须 seed=固定值 (复现性). "
            "改 optuna_config.yaml.governance.require_sampler_seed=false 关掉此检查"
        )


def enforce_pre_insert(
    record: dict,
    cfg: Optional[OptunaConfig] = None,
) -> None:
    """`INSERT INTO mart_per_stock_*_optimal` 调用前必须先调此函数.

    record 必须含 REQUIRED_OOS_FIELDS 全部, walk_forward_mode != 'none', oos_* 数字在
    "现实可能"范围 (governance.max_realistic_*).

    用法:
        from services.optimization.governance import enforce_pre_insert, GovernanceViolation
        for row in rows_to_insert:
            try:
                enforce_pre_insert(row)
            except GovernanceViolation as e:
                # 记录到 fact_optuna_governance_log + 跳过
                ...
        conn.executemany("INSERT ...", validated_rows)
    """
    cfg = cfg or get_optuna_config()
    g = cfg.governance

    if g.require_walk_forward:
        mode = record.get("walk_forward_mode")
        if mode in (None, "", "none"):
            raise GovernanceViolation(
                f"walk_forward_mode={mode!r} — 业务表禁止 in-sample fit. "
                f"必须 'holdout' / 'expanding' / 'expanding_monthly'. "
                f"调试 in-sample 写另一张 *_insample 表."
            )

    missing = [f for f in REQUIRED_OOS_FIELDS if f not in record or record[f] is None]
    if missing:
        raise GovernanceViolation(
            f"OOS 字段缺失: {missing}. 业务表必须有 OOS metrics, 不许只填 in-sample fit."
        )

    # 反 estimation 守门 (Rule 6)
    sharpe = record.get("oos_sharpe")
    win = record.get("oos_win_rate")
    avg = record.get("oos_avg_ret")
    n = record.get("oos_n_traded", 0)

    if sharpe is not None and abs(sharpe) > g.max_realistic_sharpe:
        raise GovernanceViolation(
            f"oos_sharpe={sharpe:.2f} 超 ±{g.max_realistic_sharpe} "
            f"(几乎不可能, 多半 leakage / bug). record={record}"
        )
    if win is not None and (win > g.max_realistic_win_rate or win < 0):
        raise GovernanceViolation(
            f"oos_win_rate={win:.2f} 超出现实区间 [0, {g.max_realistic_win_rate}]. "
            f"record={record}"
        )
    if avg is not None and (
        avg > g.max_realistic_avg_ret or avg < g.min_realistic_avg_ret
    ):
        raise GovernanceViolation(
            f"oos_avg_ret={avg:.4f} 超出现实区间 "
            f"[{g.min_realistic_avg_ret}, {g.max_realistic_avg_ret}]. record={record}"
        )
    if n is not None and n < g.min_test_signals:
        raise GovernanceViolation(
            f"oos_n_traded={n} < min_test_signals={g.min_test_signals} "
            f"(OOS 样本太少, 不可信)"
        )


class GovernanceViolation(ValueError):
    """Optuna 治理规则违反, 应该 raise 不 silent fallback (Rule 5)."""
