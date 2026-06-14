"""裸K线公式网格寻参 — 受治理 (L0 Tier-1 best-OOS-params, owner=l0_bare_kline_baseline_spec §5)。

防过拟合治理 (用户 #1 约束):
  - 搜索空间非空闸 (plan_validator) 先于 RUN; 网格刻意小 (维度少 = 拟合噪声少)。
  - 目标 = OOS RankIC (oos_ic, walk-forward expanding_monthly, embargo, 只读 OOS 不看 train)。
  - 选最佳后 DSR deflate (Bailey-LdP, n_trials=组合数): "最佳 IC_IR 是真 alpha 还是试错噪音?"
  - 全网格穷举 (可复现无 sampler); 不裸调 study.optimize, 经本治理层。

PIT: extract_feature 是 PIT-clean 函数 (period 参数只改窗大小不引入前瞻; 函数级 PIT 由 pit_guard
在驱动门1 核证, 与具体 period 无关)。本 runner 只搜 IC, 假设特征已 PIT-clean。
"""
from __future__ import annotations

from itertools import product

import numpy as np

from services.formula_engine.features import extract_feature
from services.optimization.deflated_sharpe import deflated_sharpe_ratio
from services.optimization.plan_validator import enforce_search_space_nonempty, load_search_spaces
from services.portfolio_walk_forward.oos_ic import PanelRow, forward_returns, oos_rank_ic


def _set_dotted(d: dict, key: str, value) -> None:
    """'ma.long' -> d['ma']['long']=value (嵌套展开)。"""
    parts = key.split(".")
    cur = d
    for p in parts[:-1]:
        cur = cur.setdefault(p, {})
    cur[parts[-1]] = value


def expand_grid(axes: dict) -> list[dict]:
    """轴 {param:[values]} -> 笛卡尔积组合列表 (dotted key 展开嵌套)。"""
    if not axes:
        return []
    keys = list(axes)
    combos: list[dict] = []
    for values in product(*[axes[k] for k in keys]):
        params: dict = {}
        for k, v in zip(keys, values):
            _set_dotted(params, k, v)
        combos.append(params)
    return combos


def build_panel(formula: str, by_code: dict[str, dict], horizon: int, params: dict) -> list[PanelRow]:
    """给定 params 构 (date,code,feature,fwd_ret) 面板 (feature PIT, fwd_ret 前向)。"""
    panel: list[PanelRow] = []
    for code, bars in by_code.items():
        if len(bars["close"]) < horizon + 2:
            continue
        feat = extract_feature(formula, bars, params)
        fwd = forward_returns(bars["date"], bars["close"], horizon)
        for i, date in enumerate(bars["date"]):
            if feat[i] is not None and fwd[i] is not None:
                panel.append(PanelRow(date=date, code=code, feature=feat[i], fwd_ret=fwd[i]))
    return panel


def search_formula(
    formula: str, by_code: dict[str, dict], *, horizon: int = 5, embargo: int = 5,
    axes: dict | None = None,
) -> dict:
    """网格寻参 single formula -> best-OOS-params。受 plan_validator 闸 + DSR deflate。

    返回 {formula, n_trials, best_params, best_oos_rank_ic, best_ic_ir, dsr_pvalue, all_results}。
    """
    spaces = load_search_spaces()
    enforce_search_space_nonempty([formula], spaces)  # 搜索空间非空闸 (raise on empty)
    grid = axes if axes is not None else spaces[formula]
    combos = expand_grid(grid)

    results = []
    for params in combos:
        ic = oos_rank_ic(build_panel(formula, by_code, horizon, params), embargo_days=embargo)
        results.append({"params": params, "oos_rank_ic": ic["oos_rank_ic"],
                        "ic_ir": ic["ic_ir"], "n_days": ic["n_days"], "n_windows": ic["n_windows"]})

    scored = [r for r in results if r["oos_rank_ic"] is not None]
    if not scored:
        return {"formula": formula, "n_trials": len(combos), "best_params": None,
                "best_oos_rank_ic": None, "best_ic_ir": None, "dsr_pvalue": None,
                "all_results": results, "reason": "no_valid_combo"}
    # 选最佳 by OOS RankIC 绝对值 (反转类负相关也是信号; 标尺取最强可预测性)
    best = max(scored, key=lambda r: abs(r["oos_rank_ic"]))
    # DSR deflate: best IC_IR 在试了 n_trials 组合后是否真显著 (多重比较校正)。
    # sharpe_variance 用 trials 间 IC_IR **实测方差** 校准 (Bailey-LdP: 有 trials 数据取 V[SR],
    # 默认 1.0 是无 prior 粗近似 — 对 IC_IR 小尺度 [~0.3] 严重过严致 p=0 假阴)。
    dsr = None
    ic_irs = [abs(r["ic_ir"]) for r in scored if r["ic_ir"] is not None]
    if best["ic_ir"] is not None and best["n_days"] and best["n_days"] >= 2:
        sharpe_var = float(np.var(ic_irs, ddof=1)) if len(ic_irs) > 1 else 1.0
        dsr = deflated_sharpe_ratio(abs(best["ic_ir"]), n_trials=len(combos),
                                    n_observations=best["n_days"], sharpe_variance=max(sharpe_var, 1e-9))
    return {"formula": formula, "n_trials": len(combos), "best_params": best["params"],
            "best_oos_rank_ic": best["oos_rank_ic"], "best_ic_ir": best["ic_ir"],
            "dsr_pvalue": dsr, "all_results": results}
