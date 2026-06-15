"""纪律化实验 harness — 把"事前 leakage 检查 + 事后异常核查"固化进流程 (非靠人记/文档)。

缘起 (2026-06-15 用户): 实验三段纪律必须工具化进系统:
  事前: 算 IC 前跑 leakage 工具 (pit_guard 行为门), 不过 BLOCK 不算。
  事后: 命中 §4.2 异常红线 (abs RankIC>0.3 / relative >+50%) -> 标 PENDING_ABLATION (既不直接用=真,
        也不直接弃), 记录必须做的 ablation (MC截面置换/PIT溯源)。
  留档: 落 L4 experiment_store (见 services/experiment_store.py)。
moth 断言 `experiment-*` 强制每个算 IC 的 experiment_*.py 三段都走 (想漏漏不掉)。

用法:
    from services.experiment_harness import leakage_gate, anomaly_verdict
    gate = leakage_gate(lambda b: extract_feature("reversal_short_term", b), sample_bars)
    if not gate["clean"]: print("[BLOCK] leakage"); return 1     # 事前: 不过不算 IC
    ...compute ic...
    av = anomaly_verdict(ic, baseline=0.064)                     # 事后: 异常须 ablation
    # av["verdict"] in CLEAN/UNKNOWN/ANOMALY_ABSOLUTE/ANOMALY_RELATIVE; av["action"] = 必做动作
"""
from __future__ import annotations

from typing import Any, Callable

from services.portfolio_walk_forward.pit_guard import assert_pit_clean

# §4.2 异常红线 (owner=CLAUDE.md §4.2 / goal.md 死亡条款感知死)
ABS_RANKIC_REDLINE = 0.30   # rule-compliance: ok evidence=CLAUDE §4.2 absolute leakage 红线 RankIC>0.3
REL_LIFT_REDLINE = 0.50     # rule-compliance: ok evidence=CLAUDE §4.2 relative >+50% vs baseline 红线


def leakage_gate(feature_fn: Callable[[dict], list], sample_bars: list[dict], *,
                 probe_points: int = 24, future_pad: int = 5) -> dict[str, Any]:
    """事前 leakage 门: 对一批样本股跑 pit_guard 行为门 (追加未来 bar 不改过去特征)。

    feature_fn(bars_dict)->list 对齐 bars; sample_bars=list[bars_dict] (抽样股 K线)。
    任一股泄漏 -> clean=False, 调用方**必须 BLOCK 不算 IC** (泄漏死)。
    返回 {clean, n_stocks, violation_stocks, n_checked, sample_violations}。
    """
    viol: list = []
    checked = 0
    for bars in sample_bars:
        rep = assert_pit_clean(feature_fn, bars, probe_points=probe_points, future_pad=future_pad)
        checked += rep.get("n_checked", 0)
        if not rep["clean"]:
            viol.append(rep["violations"][:1])
    return {"clean": not viol, "n_stocks": len(sample_bars), "violation_stocks": len(viol),
            "n_checked": checked, "sample_violations": viol[:3],
            "verdict": "PIT_CLEAN" if not viol else "LEAKAGE"}


def anomaly_verdict(ic: float | None, baseline: float | None = None) -> dict[str, Any]:
    """事后异常核查 (§4.2): 既不直接用(当真)也不直接弃, 命中红线 -> 标须 ablation。

    verdict: CLEAN(可信) / UNKNOWN(无值,标unknown不糊弄) / ANOMALY_ABSOLUTE(|IC|>0.3) /
             ANOMALY_RELATIVE(相对baseline >+50%)。action = 命中红线后**必做**的核查 (非直接用/弃)。
    """
    if ic is None:
        return {"verdict": "UNKNOWN", "action": "无值标 unknown, 不公式糊弄 (measured not estimated)"}
    if abs(ic) > ABS_RANKIC_REDLINE:
        return {"verdict": "ANOMALY_ABSOLUTE",
                "action": f"|RankIC|={abs(ic):.4f}>{ABS_RANKIC_REDLINE} leakage 警报: 必做 ablation "
                          "(MC截面置换 + PIT溯源 + 剔除可疑col群重跑), 不直接用不直接弃"}
    if baseline is not None and baseline > 0 and ic > baseline * (1 + REL_LIFT_REDLINE):
        lift = ic / baseline - 1
        return {"verdict": "ANOMALY_RELATIVE",
                "action": f"relative +{lift:.0%} vs baseline {baseline:.4f} (>{REL_LIFT_REDLINE:.0%}红线): "
                          "必做 ablation (MC截面置换 + 每col群PIT干净度), 不直接用不直接弃"}
    return {"verdict": "CLEAN", "action": ""}
