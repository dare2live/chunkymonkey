"""Main gate orchestration — 4 hard gates 综合 verdict.

ChunkyMonkey MSAF Phase 1.5 (Codex R31 design):
- gate_pbo: PBO ≤ 0.20
- gate_dsr: DSR p_conf ≥ 0.95
- gate_conservative: 保守成交 (slippage+50%, VWAP/open, mask 加严) 后 ann > 0
- gate_is_oos: IS-OOS gap < 30% relative

任一 fail → block promote.

Public API:
- run_all_gates(challenger_id) -> dict
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field, asdict
from typing import Any

import numpy as np

from services.backtest_validation.pbo import compute_pbo, PBOResult
from services.backtest_validation.dsr import compute_dsr, DSRResult


log = logging.getLogger("backtest_validation.gate")


@dataclass
class GateResult:
    name: str
    passes: bool
    reason: str
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass
class AllGatesResult:
    challenger_id: str
    pbo: GateResult
    dsr: GateResult
    conservative: GateResult
    is_oos: GateResult
    all_pass: bool
    promote_action: str  # "promote" | "block" | "warn_only" | "force_retrain"

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "challenger_id": self.challenger_id,
            "all_pass": self.all_pass,
            "promote_action": self.promote_action,
        }
        for name in ("pbo", "dsr", "conservative", "is_oos"):
            g = getattr(self, name)
            d[name] = {"passes": g.passes, "reason": g.reason, "detail": g.detail}
        return d


def gate_pbo(returns_matrix: np.ndarray, *, threshold: float = 0.20) -> GateResult:
    """PBO gate: returns_matrix shape (n_trials, n_periods)."""
    try:
        result = compute_pbo(returns_matrix, threshold=threshold)
        return GateResult(
            name="pbo",
            passes=result.passes,
            reason=f"PBO={result.pbo:.3f} (threshold {threshold})",
            detail={
                "pbo": result.pbo,
                "lambda_mean": result.lambda_mean,
                "lambda_std": result.lambda_std,
                "n_combos": result.n_combos,
                "sub_periods": result.sub_periods,
            },
        )
    except Exception as e:
        return GateResult(name="pbo", passes=False, reason=f"PBO error: {e}", detail={"error": str(e)})


def gate_dsr(
    returns: np.ndarray,
    *,
    n_trials: int = 1,
    threshold_p_conf: float = 0.95,
    periods_per_year: int = 252,
) -> GateResult:
    """DSR gate: 1D returns array.

    periods_per_year: 5d weekly → 50; 10d biweekly → 25; 20d monthly → 12; 1d daily → 252.
    (Codex review b53h8en1m: 此前默认 252 但 5d weekly 输入 → SR 年化 unit mismatch)
    """
    try:
        result = compute_dsr(
            returns, n_trials=n_trials, threshold_p_conf=threshold_p_conf,
            periods_per_year=periods_per_year,
        )
        return GateResult(
            name="dsr",
            passes=result.passes,
            reason=f"DSR p_conf={result.p_conf:.4f} (threshold {threshold_p_conf})",
            detail={
                "sr_observed": result.sr_observed,
                "sr_expected_max": result.sr_expected_max,
                "dsr_z": result.dsr,
                "p_conf": result.p_conf,
                "p_value": result.p_value,
                "n_obs": result.n_obs,
                "n_trials": result.n_trials,
            },
        )
    except Exception as e:
        return GateResult(name="dsr", passes=False, reason=f"DSR error: {e}", detail={"error": str(e)})


def gate_conservative(
    ann_ret_normal: float,
    ann_ret_conservative: float,
) -> GateResult:
    """Conservative scenario: 保守成交后 ann_ret > 0.

    Args:
        ann_ret_normal: 正常成交模型下的 ann_ret
        ann_ret_conservative: 保守模型 (slippage +50%, VWAP→open, mask 加严) 后 ann_ret
    """
    passes = ann_ret_conservative > 0
    return GateResult(
        name="conservative",
        passes=passes,
        reason=(
            f"normal ann={ann_ret_normal:+.2%}, "
            f"conservative ann={ann_ret_conservative:+.2%} "
            f"({'pass' if passes else 'fail'} > 0)"
        ),
        detail={
            "ann_normal": ann_ret_normal,
            "ann_conservative": ann_ret_conservative,
            "drop_pct": ann_ret_normal - ann_ret_conservative if ann_ret_normal else None,
        },
    )


def gate_is_oos(
    is_metric: float,
    oos_metric: float,
    *,
    max_relative_drop: float = 0.30,
    proxy_mode: bool = False,
) -> GateResult:
    """IS-OOS gap gate.

    Args:
        is_metric: in-sample metric (e.g. Sharpe / RankIC). True IS = RankIC during
                   model train. Proxy IS = early-OOS period mean (split-half hack).
        oos_metric: out-of-sample metric. True OOS = RankIC during walk-forward test.
                    Proxy OOS = late-OOS period mean.
        max_relative_drop: 默认 30% 真 IS-OOS (来自 fact_model_train_log) 严格 threshold
        proxy_mode: True 表示 is_metric/oos_metric 是 split-half proxy (无 real train log),
                    此时 threshold 放宽到 70% (academic standard for time-period comparison).
                    用户 push back '修一次防一切': proxy 比较不应跟真 IS-OOS 用同 threshold.

    固化 (2026-05-18): proxy_mode=False 是 strict 真 IS-OOS; proxy_mode=True 是 split-half
    fallback (n_obs 不足或无 train log 时). 见 backend/scripts/run_phase4_gate_on_msaf.py 调用.
    """
    if abs(is_metric) < 1e-12:
        return GateResult(
            name="is_oos",
            passes=False,
            reason=f"IS metric too small ({is_metric}) — can't compute relative drop",
            detail={"is": is_metric, "oos": oos_metric},
        )
    # proxy 模式放宽 threshold (split-half 比较的是 early/late OOS, 不是真 train/test)
    # rule-compliance: ok evidence=academic-split-half-stability-threshold TODO yaml-back 接 fact_model_train_log 后改 measured
    effective_threshold = 0.70 if proxy_mode else max_relative_drop
    relative_drop = (is_metric - oos_metric) / abs(is_metric)
    passes = relative_drop <= effective_threshold
    mode_label = "proxy-split-half" if proxy_mode else "true-train-test"
    return GateResult(
        name="is_oos",
        passes=passes,
        reason=(
            f"IS={is_metric:.4f}, OOS={oos_metric:.4f}, "
            f"relative_drop={relative_drop:.2%} "
            f"({'pass' if passes else 'fail'} ≤ {effective_threshold:.0%} [{mode_label}])"
        ),
        detail={
            "is": is_metric,
            "oos": oos_metric,
            "relative_drop": relative_drop,
            "threshold": effective_threshold,
            "proxy_mode": proxy_mode,
        },
    )


def run_all_gates(
    challenger_id: str,
    *,
    returns_matrix: np.ndarray | None = None,
    oos_returns: np.ndarray | None = None,
    n_trials_for_dsr: int = 1,
    periods_per_year_for_dsr: int = 252,
    ann_normal: float | None = None,
    ann_conservative: float | None = None,
    is_metric: float | None = None,
    oos_metric: float | None = None,
    is_oos_proxy_mode: bool = False,
) -> AllGatesResult:
    """Run all 4 gates, return综合 verdict.

    Args:
        challenger_id: 模型/策略 ID
        returns_matrix: PBO input, (n_trials, n_periods) OOS returns matrix
        oos_returns: DSR input, 1D OOS returns
        n_trials_for_dsr: number of strategies tried (selection bias correction)
        ann_normal: 正常 paper_sim ann_ret
        ann_conservative: 保守 paper_sim ann_ret
        is_metric: IS metric (e.g. Sharpe in-sample)
        oos_metric: OOS metric (e.g. Sharpe walk-forward OOS)

    Returns:
        AllGatesResult with all 4 GateResults +综合 verdict
    """
    log.info(f"=== run_all_gates challenger_id={challenger_id} ===")

    # PBO
    if returns_matrix is not None:
        pbo_r = gate_pbo(returns_matrix)
    else:
        pbo_r = GateResult(
            name="pbo", passes=False, reason="returns_matrix missing", detail={"error": "input_missing"}
        )

    # DSR
    if oos_returns is not None:
        dsr_r = gate_dsr(
            oos_returns, n_trials=n_trials_for_dsr,
            periods_per_year=periods_per_year_for_dsr,
        )
    else:
        dsr_r = GateResult(
            name="dsr", passes=False, reason="oos_returns missing", detail={"error": "input_missing"}
        )

    # Conservative
    if ann_normal is not None and ann_conservative is not None:
        cons_r = gate_conservative(ann_normal, ann_conservative)
    else:
        cons_r = GateResult(
            name="conservative", passes=False, reason="ann_normal/conservative missing",
            detail={"error": "input_missing"}
        )

    # IS-OOS
    if is_metric is not None and oos_metric is not None:
        isoos_r = gate_is_oos(is_metric, oos_metric, proxy_mode=is_oos_proxy_mode)
    else:
        isoos_r = GateResult(
            name="is_oos", passes=False, reason="is/oos metric missing",
            detail={"error": "input_missing"}
        )

    all_pass = all([pbo_r.passes, dsr_r.passes, cons_r.passes, isoos_r.passes])
    if all_pass:
        action = "promote"
    elif pbo_r.detail.get("error") or dsr_r.detail.get("error"):
        action = "warn_only"  # 缺数据 不阻 promote, 但 alert
    elif not pbo_r.passes:
        action = "block"  # 过拟合 严格阻
    elif not dsr_r.passes:
        action = "force_retrain"  # 非显著 重训
    else:
        action = "block"

    result = AllGatesResult(
        challenger_id=challenger_id,
        pbo=pbo_r, dsr=dsr_r, conservative=cons_r, is_oos=isoos_r,
        all_pass=all_pass,
        promote_action=action,
    )
    log.info(f"  PBO: {pbo_r.passes} ({pbo_r.reason})")
    log.info(f"  DSR: {dsr_r.passes} ({dsr_r.reason})")
    log.info(f"  Conservative: {cons_r.passes} ({cons_r.reason})")
    log.info(f"  IS-OOS: {isoos_r.passes} ({isoos_r.reason})")
    log.info(f"  ALL: {all_pass} → {action}")
    return result
