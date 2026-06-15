"""Phase D Optuna 搜索: Regime/Timing × mf_trend 参数寻优 (R1 目标=含成本绝对收益, 非 IC)。

owner=docs/strategy_validation_contract.md (Optuna 治理) + analysis/design_deficiencies_extension2 (5轴/R1)。
用户指令 (2026-06-15): 充分利用 optuna / 该用就用 modal。第四轴 V0 已验 (max_dd-31%→-22.5%, Calmar 0.08→0.14)。
本搜索: optuna TPE 搜 (mf_window × regime_ma × top_k × rebalance × sizing), 目标 = 含成本 execution-aware backtest
  TRAIN 绝对年化 + max_dd 突破 KPI 惩罚 (R1, 绝不是 RankIC)。train(<=2024)/holdout(2025) disjoint (N20), best 在 holdout 复验 + DSR 去偏。

强制治理 (CLAUDE §3.6/§11.5 grill gate, 不 grill 不跑): 搜索空间非空门 (防 29/34 白跑); n_trials 50-500 band;
  目标含成本绝对收益非 IC (R1); train/holdout 不相交 (N20); DSR n_trials 去多重比较偏 (overfit 守门)。
计算: load-once + 按 window/ma 缓存信号 -> 每 trial 仅 1 次 backtest (秒级) -> 本地分钟级可行 (Modal 暂不需要, 守'该用就用')。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import optuna
import yaml

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "backend"))

from scripts.experiment_l0_baseline import load_kline  # noqa: E402
from scripts.experiment_moneyflow_trend_alpha import load_moneyflow, mf_trend_feature, in_universe  # noqa: E402
from scripts.experiment_phaseD_regime_timing import build_regime_ok  # noqa: E402
from services.portfolio_execbacktest import run_execution_backtest, ExecConfig  # noqa: E402
from services.optimization.deflated_sharpe import deflated_sharpe_ratio  # noqa: E402
from services.experiment_harness import tradability_verdict, kpi_verdict  # noqa: E402
from services.experiment_store import open_store, record_verdict, record_artifact  # noqa: E402

CFG = REPO / "backend" / "config" / "experiments" / "phaseD_search_space.yaml"


class PlanValidationError(RuntimeError):
    pass


def enforce_phaseD_search_nonempty(space: dict) -> int:
    """grill 门 (CLAUDE §3.6, 防 29/34 白跑): 每轴必须 >=1 选项, 组合数 >=1。返回组合数。"""
    axes = ["mf_window", "regime_ma", "top_k", "rebalance_days", "sizing"]
    empty = [a for a in axes if not space.get(a)]
    if empty:
        raise PlanValidationError(f"搜索空间轴为空 (寻参=空烧): {empty}")
    combos = 1
    for a in axes:
        combos *= len(space[a])
    if combos < 1:
        raise PlanValidationError("搜索空间组合数 < 1")
    return combos


def build_rebalances(signal, cal_window, rebal_days, top_k, regime_ok):
    rebs = []
    for gi in range(0, len(cal_window) - 1, rebal_days):
        t = cal_window[gi]
        if regime_ok is not None and not regime_ok.get(t, True):
            rebs.append((t, [])); continue
        cands = [(c, signal[c][t]) for c in signal if t in signal[c]]
        if not cands:
            continue
        cands.sort(key=lambda x: x[1], reverse=True)
        rebs.append((t, cands[:top_k]))
    return rebs


def r1_score(m: dict, dd_kpi: float, lam: float) -> float:
    """R1 目标: 含成本年化 + max_dd 突破 KPI 的惩罚 (dd 比 KPI 更差 -> 罚)。"""
    ann = m.get("annual_return")
    dd = m.get("max_drawdown")
    if ann is None or dd is None:
        return -9.99
    return float(ann + lam * min(0.0, dd - dd_kpi))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--start", default="2023-01-01")  # rule-compliance: ok evidence=对齐 L0/KPI 窗口
    ap.add_argument("--n-trials", type=int, default=None)  # rule-compliance: ok evidence=覆盖 config (smoke 用)
    args = ap.parse_args(argv)

    cfg = yaml.safe_load(CFG.read_text(encoding="utf-8"))
    space = cfg["phaseD_regime_mf_search"]
    obj_cfg, split_cfg, opt_cfg, dsr_cfg = cfg["objective"], cfg["split"], cfg["optuna"], cfg["dsr"]
    n_trials = args.n_trials or opt_cfg["n_trials"]

    # grill 门: 搜索空间非空 (不 grill 不跑)
    combos = enforce_phaseD_search_nonempty(space)
    if not (50 <= opt_cfg["n_trials"] <= 500):
        raise PlanValidationError(f"n_trials {opt_cfg['n_trials']} 越 governance band 50-500")
    print(f"[grill] 搜索空间非空 PASS: {combos} 组合, n_trials={n_trials} (band 50-500); 目标=含成本绝对收益(R1 非IC); train/holdout disjoint", flush=True)

    print("[load] K线(OHLCV) + moneyflow (load-once) ...", flush=True)
    by_code = load_kline(args.start, None, 0)
    mf = load_moneyflow(args.start)

    # bars + 按 mf_window 缓存 mf_trend 信号 (load-once, 每 trial 不重算)
    bars_by_code: dict[str, dict] = {}
    net_flow: dict[str, tuple] = {}
    for code, bars in by_code.items():
        if not in_universe(code) or code not in mf:
            continue
        dates = bars["date"]
        bb = {}
        for i, d in enumerate(dates):
            c = bars["close"][i]
            if c is not None:
                bb[d] = (bars["open"][i], bars["high"][i], bars["low"][i], c, bars["volume"][i])
        bars_by_code[code] = bb
        net_flow[code] = (dates, [mf[code].get(d, (None, None))[0] for d in dates],
                          [mf[code].get(d, (None, None))[1] for d in dates])
    all_dates = sorted({d for bb in bars_by_code.values() for d in bb})
    train_end = split_cfg["train_end"]
    train_cal = [d for d in all_dates if d <= train_end]
    holdout_cal = [d for d in all_dates if d > train_end]
    print(f"[load] {len(bars_by_code)} 股 | train {train_cal[0]}~{train_cal[-1]} ({len(train_cal)}日) | holdout {holdout_cal[0]}~{holdout_cal[-1]} ({len(holdout_cal)}日)", flush=True)

    sig_cache: dict[int, dict] = {}
    for w in space["mf_window"]:
        sc = {}
        for code, (dates, net_s, flow_s) in net_flow.items():
            tr = mf_trend_feature(net_s, flow_s, window=w)
            sc[code] = {d: tr[i] for i, d in enumerate(dates) if tr[i] is not None}
        sig_cache[w] = sc
    regime_cache: dict[int, dict] = {ma: build_regime_ok(bars_by_code, all_dates, ma=ma) for ma in space["regime_ma"]}
    print(f"[cache] mf_trend×{len(sig_cache)} windows + regime×{len(regime_cache)} MAs 预算完成", flush=True)

    dd_kpi, lam = obj_cfg["dd_kpi"], obj_cfg["dd_penalty_lambda"]
    ecfg = ExecConfig.load()

    def objective(trial: optuna.Trial) -> float:
        w = trial.suggest_categorical("mf_window", space["mf_window"])
        ma = trial.suggest_categorical("regime_ma", space["regime_ma"])
        tk = trial.suggest_categorical("top_k", space["top_k"])
        rb = trial.suggest_categorical("rebalance_days", space["rebalance_days"])
        sz = trial.suggest_categorical("sizing", space["sizing"])
        rebs = build_rebalances(sig_cache[w], train_cal, rb, tk, regime_cache[ma])
        res = run_execution_backtest(rebs, bars_by_code, train_cal, config=ecfg, sizing=sz, top_k=tk)
        if not res["nav"]:
            return -9.99
        s = r1_score(res["metrics"], dd_kpi, lam)
        trial.set_user_attr("train_annual", res["metrics"]["annual_return"])
        trial.set_user_attr("train_max_dd", res["metrics"]["max_drawdown"])
        return s

    storage = f"sqlite:///{REPO}/data/reports/optuna/phaseD_regime_mf_20260615.db"  # rule-compliance: ok evidence=F1 resilience optuna SQLite storage 路径
    study = optuna.create_study(direction="maximize", study_name="phaseD_regime_mf_20260615",
                                storage=storage, load_if_exists=True,
                                sampler=optuna.samplers.TPESampler(seed=opt_cfg["seed"]))
    optuna.logging.set_verbosity(optuna.logging.WARNING)
    print(f"[optuna] TPE 搜 {n_trials} trials (R1 目标=含成本年化-dd惩罚, TRAIN) ...", flush=True)
    study.optimize(objective, n_trials=n_trials)
    bp = study.best_params
    print(f"[optuna] best train score={study.best_value:+.4f} params={bp}", flush=True)

    # best 在 HOLDOUT 复验 (disjoint, 从未优化) + DSR 去偏
    rebs_h = build_rebalances(sig_cache[bp["mf_window"]], holdout_cal, bp["rebalance_days"], bp["top_k"], regime_cache[bp["regime_ma"]])
    hres = run_execution_backtest(rebs_h, bars_by_code, holdout_cal, config=ecfg, sizing=bp["sizing"], top_k=bp["top_k"])
    hm = hres["metrics"]
    # 全期 (train+holdout) best 复跑 (汇报用)
    rebs_f = build_rebalances(sig_cache[bp["mf_window"]], all_dates, bp["rebalance_days"], bp["top_k"], regime_cache[bp["regime_ma"]])
    fres = run_execution_backtest(rebs_f, bars_by_code, all_dates, config=ecfg, sizing=bp["sizing"], top_k=bp["top_k"])
    fm = fres["metrics"]

    # DSR: holdout 日 sharpe (annualized/sqrt252 近似 per-period) deflate by n_trials
    daily_sr = (hm["sharpe"] / np.sqrt(252)) if hm["sharpe"] else 0.0
    dsr_p = deflated_sharpe_ratio(observed_sharpe=daily_sr, n_trials=len(study.trials),
                                  n_observations=max(2, len(hres["nav"]) // 5))  # n_eff=days/horizon(N15 重叠校正)
    trad = tradability_verdict(None, hm["annual_return"])  # IC 非本搜目标, 传 None -> 看含成本年化
    kpi = kpi_verdict(hm)
    dsr_ok = dsr_p == dsr_p and dsr_p > dsr_cfg["min_p"]
    verdict = "PHASED_REGIME_KPI_PASS" if (kpi["verdict"] == "KPI_PASS" and dsr_ok) else (
        "PHASED_HOLDOUT_PASS_DSR_FAIL" if kpi["verdict"] == "KPI_PASS" else "PHASED_KPI_FAIL")

    def pct(x):
        return f"{x:+.2%}" if isinstance(x, (int, float)) else "None"

    print(f"\n===== Phase D Optuna best (mf_window={bp['mf_window']} regime_ma={bp['regime_ma']} top_k={bp['top_k']} rebal={bp['rebalance_days']} sizing={bp['sizing']}) =====")
    print(f"{'':<14}{'TRAIN(<=2024)':>16}{'HOLDOUT(2025+)':>16}{'全期':>12}")
    h_sc = f"{hm['sharpe']:.2f}/{hm['calmar']:.2f}"
    f_sc = f"{fm['sharpe']:.2f}/{fm['calmar']:.2f}"
    print(f"{'年化':<14}{pct(study.best_trial.user_attrs.get('train_annual')):>16}{pct(hm['annual_return']):>16}{pct(fm['annual_return']):>12}")
    print(f"{'max_dd':<14}{pct(study.best_trial.user_attrs.get('train_max_dd')):>16}{pct(hm['max_drawdown']):>16}{pct(fm['max_drawdown']):>12}")
    print(f"{'Sharpe/Calmar':<14}{'':>16}{h_sc:>16}{f_sc:>12}")
    print(f"DSR p={dsr_p:.3f} (n_trials={len(study.trials)}, min_p {dsr_cfg['min_p']}: {'PASS' if dsr_ok else 'FAIL'}) | holdout KPI {kpi['verdict']} | R1 {trad['verdict']}")
    print(f"VERDICT = {verdict}")

    out = {"experiment": "phaseD_optuna_regime_mf", "engine": "portfolio_execbacktest_20260615",
           "best_params": bp, "n_trials": len(study.trials), "best_train_score": study.best_value,
           "train": {"annual_return": study.best_trial.user_attrs.get("train_annual"), "max_dd": study.best_trial.user_attrs.get("train_max_dd")},
           "holdout": {**hm, "final_nav": hres["final_nav"], "cost_drag": hres["cost_drag"], "avg_turnover": hres["avg_turnover"]},
           "full_period": {"annual_return": fm["annual_return"], "max_drawdown": fm["max_drawdown"], "sharpe": fm["sharpe"], "calmar": fm["calmar"]},
           "dsr_p": dsr_p, "dsr_pass": dsr_ok, "holdout_kpi": kpi, "tradability": trad, "verdict": verdict,
           "objective": "含成本 annual + max_dd KPI 突破惩罚 (R1, 非 IC)", "split": split_cfg,
           "note": "Optuna TPE 搜 regime+mf; R1 目标含成本绝对收益; train/holdout disjoint(N20); DSR 去偏; best holdout 复验"}
    out_path = REPO / "analysis" / "phaseD_optuna_regime_mf_20260615.json"
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"[out] {out_path}")

    with open_store() as st:
        record_verdict(st, run_id="phaseD_optuna_regime_mf_20260615", family="phaseD_regime_timing", verdict=verdict,
                       judges={"best_params": bp, "holdout": out["holdout"], "tradability": trad,
                               "kpi_verdict": kpi, "dsr_p": dsr_p, "n_trials": len(study.trials)},
                       confirmed_by_owner=0)
        record_artifact(st, run_id="phaseD_optuna_regime_mf_20260615", artifact_path=out_path)
    print(f"[store] 留档 Optuna verdict={verdict}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
