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

# North-Star KPI (owner=goal.md; 全 AND, 单项不构成放行). 胜率是 KPI 之一但被法典 C-WinReturn 定为
# 诊断/辅助量, 收益率+max_dd 才是目标量 — 见 docs/strategy_validation_contract.md "判断法典 C-WinReturn"。
DEFAULT_KPI = {
    "annual_return": 0.30,    # rule-compliance: ok evidence=goal.md North-Star 年化>=30%
    "max_drawdown": -0.20,    # rule-compliance: ok evidence=goal.md North-Star max_dd>=-20%
    "monthly_win_rate": 0.55, # rule-compliance: ok evidence=goal.md North-Star 月胜率>=55%
}


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


def tradability_verdict(ic: float | None, net_annual_return: float | None) -> dict[str, Any]:
    """R1 对称可交易性门 (法典 C-R1): 排序 edge (IC) 真不真 != long-only 含成本能不能赚钱。

    `anomaly_verdict` 是**单边**门 — 只抓 "IC 太高"(leakage 警报), 数学上抓不到 Phase B 铁证那类失败:
    IC 真 (置换显著) 但 cohort 整体崩盘 -> long-only 含成本绝对收益为负。本门补上这个对称缺口。

    缘起 (2026-06-15 8-lens 对抗复审, 根因 R1): 每日截面 spearman 减掉了 cohort 绝对漂移, long-only 赚的
    恰是这个被减掉的水平 -> 验证空间(rank)⟂ 盈利空间(绝对NAV)。实证: Stage1.5×小盘×高换手 IC +0.195 但
    含成本 gross -34.6%; 全市场 Stage1.5 IC +0.156 但 net -2.8%。IC>0 而 net<=0 = 不可交易, 不许采信。

    verdict: TRADABLE(IC>0 且含成本绝对收益>0) / IC_POSITIVE_BUT_UNTRADABLE(IC>0 但净收益<=0, R1 命中) /
             NO_EDGE(IC<=0) / UNKNOWN(缺含成本 backtest, 不糊弄)。
    """
    if ic is None:
        return {"verdict": "UNKNOWN", "action": "IC 无值, 标 unknown (measured not estimated)"}
    if net_annual_return is None:
        return {"verdict": "UNKNOWN",
                "action": "缺含成本 backtest 绝对收益: IC 单独不构成 edge 证据 (C-R1), 必跑 execution-aware backtest"}
    if ic <= 0:
        return {"verdict": "NO_EDGE", "action": "IC<=0, 无排序 edge"}
    if net_annual_return <= 0:
        return {"verdict": "IC_POSITIVE_BUT_UNTRADABLE",
                "action": f"IC={ic:+.4f}>0 但含成本年化={net_annual_return:+.2%}<=0 (R1: 排序真但 long-only 不赚钱): "
                          "崩盘 cohort, 不许凭 IC 转正; 选 cell/因子按含成本绝对收益不按 IC"}
    return {"verdict": "TRADABLE",
            "action": f"IC={ic:+.4f}>0 且含成本年化={net_annual_return:+.2%}>0: 排序 edge 可交易 (仍须过 KPI 联合门)"}


def kpi_verdict(metrics: dict[str, Any], kpi: dict[str, float] | None = None) -> dict[str, Any]:
    """C-WinReturn 联合验收门: 胜率是诊断量, 收益率+max_dd 是目标量, 全 AND, 单项不构成放行。

    法典 C-WinReturn (2026-06-15 用户: "除了考核胜率还要考核收益率, 最终目的不是证明策略有效而是真能赚钱"):
      - binding (目标量): annual_return>=kpi AND max_drawdown>=kpi (含成本 OOS)。
      - 胜率 (诊断量): monthly_win_rate>=kpi 是 goal.md KPI 之一, 但单独高胜率不构成放行 —— 必须与盈亏比联立。
      - 盈亏比/期望 (若引擎提供 avg_win/avg_loss 或 payoff_ratio): expectancy>0 (胜率×盈亏比联合)。
        40% 胜率×3:1 盈亏 完胜 60% 胜率×0.5:1 —— 胜率脱离盈亏比无意义。

    metrics 期望键: annual_return, max_drawdown, monthly_win_rate(可None), 可选 payoff_ratio / avg_win+avg_loss。
    返回 {verdict: KPI_PASS/KPI_FAIL, passes:{...}, diagnostics:{win_rate, payoff_ratio, expectancy}}。
    """
    k = kpi or DEFAULT_KPI
    ann = metrics.get("annual_return")
    mdd = metrics.get("max_drawdown")
    mwr = metrics.get("monthly_win_rate")
    # 盈亏比 / 期望 (诊断量, 胜率×盈亏比联立)
    payoff = metrics.get("payoff_ratio")
    if payoff is None and metrics.get("avg_win") is not None and metrics.get("avg_loss"):
        al = abs(metrics["avg_loss"])
        payoff = (metrics["avg_win"] / al) if al > 1e-12 else None
    # 期望值 (per-trade): 用 trade 级胜率 win_rate 若有, 否则用月胜率近似 (标注)
    wr = metrics.get("win_rate", mwr)
    expectancy = None
    if payoff is not None and wr is not None:
        expectancy = wr * payoff - (1 - wr)   # 单位: 以平均亏损为 1 计的期望盈亏 (>0 即正期望)

    passes = {
        "annual_return": ann is not None and ann >= k["annual_return"],          # 目标量
        "max_drawdown": mdd is not None and mdd >= k["max_drawdown"],            # 目标量
        "monthly_win_rate": mwr is not None and mwr >= k["monthly_win_rate"],    # 诊断量 (KPI 之一)
    }
    if expectancy is not None:
        passes["positive_expectancy"] = expectancy > 0                          # 胜率×盈亏比联合
    verdict = "KPI_PASS" if all(passes.values()) else "KPI_FAIL"
    return {"verdict": verdict, "passes": passes,
            "diagnostics": {"win_rate": wr, "payoff_ratio": payoff, "expectancy": expectancy,
                            "note": "胜率为诊断量, 收益率+max_dd 为目标量 (C-WinReturn)"}}
