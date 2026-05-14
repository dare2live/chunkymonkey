"""Phase ψ — Optuna 治理规则中心 (单一职责, Config-driven).

⚠ Rule 5 (Root Cause) + Rule 6 (Measured, Not Estimated) + Rule 7 (Optuna 治理):
    凡是写进 mart_per_stock_*_optimal 表的 sharpe / win_rate / avg_ret, **必须**是
    OOS 实测, 不许 in-sample fit, 不许公式估算. 所有阈值 / 上限 / 必填字段 走
    backend/config/optuna_config.yaml, 不许 hardcode.

⚠ 任何 Optuna 调参脚本入库前调 `enforce_pre_insert(record)`, 不通过 raise.
⚠ 改治理规则 → 改 optuna_config.yaml, 业务脚本不动.
"""
from __future__ import annotations

import math
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


def enforce_deflated_sharpe(
    observed_sharpe: float,
    cumulative_n_trials: int,
    n_observations: int,
    cfg: Optional[OptunaConfig] = None,
    skewness: float = 0.0,
    kurtosis: float = 3.0,
) -> float:
    """跨 study 多重测试守门 (Bailey & López de Prado 2014).

    Rule 7 防单 study leakage, Rule 8 强制 OOS, **但** 累积跑过 N 个 study 后,
    "最好那一个 study 的 OOS sharpe" 仍有 multiple testing selection bias.
    本函数算 Deflated SR p-value, p < cfg.deflated_sharpe.min_p_value → raise.

    用法 (Optuna 跑完后入业务表前):
        from services.optimization.governance import enforce_deflated_sharpe
        # cumulative_n_trials 从 fact_optuna_cumulative_trials 取项目至今累积 trials
        p = enforce_deflated_sharpe(
            observed_sharpe=best_oos_sharpe,
            cumulative_n_trials=N,
            n_observations=oos_n_traded,
        )
        record['deflated_sr_p_value'] = p
        record['deflated_sr_n_trials'] = N

    Returns:
        deflated_sr_p_value ∈ [0, 1]. Raise GovernanceViolation 当 p < min_p_value
        或 enabled=true 但输入数值病态 (NaN).
        当 cfg.deflated_sharpe.enabled=false → 返回 NaN, 不 raise (兼容旧路径).
    """
    from services.optimization.deflated_sharpe import deflated_sharpe_ratio

    cfg = cfg or get_optuna_config()
    if not cfg.deflated_sharpe.enabled:
        return float("nan")

    g = cfg.deflated_sharpe
    p = deflated_sharpe_ratio(
        observed_sharpe=observed_sharpe,
        n_trials=cumulative_n_trials,
        n_observations=n_observations,
        sharpe_variance=g.default_sharpe_variance,
        skewness=skewness,
        kurtosis=kurtosis,
    )
    if math.isnan(p):
        raise GovernanceViolation(
            f"Deflated SR p=NaN — 输入数值病态 "
            f"(kurtosis 过高 / sharpe 过大 / n_trials<2 / T<2). "
            f"observed={observed_sharpe:.4f} N={cumulative_n_trials} T={n_observations}"
        )
    if p < g.min_p_value:
        raise GovernanceViolation(
            f"Deflated SR p={p:.4f} < min={g.min_p_value}. "
            f"observed Sharpe={observed_sharpe:.4f} 在跨 study 累积 "
            f"N={cumulative_n_trials} trials × OOS T={n_observations} obs 下, "
            f"大概率是 multiple testing 噪音, 不是真 alpha. "
            f"参考 Bailey & López de Prado (2014)."
        )
    return p


class GovernanceViolation(ValueError):
    """Optuna 治理规则违反, 应该 raise 不 silent fallback (Rule 5)."""
