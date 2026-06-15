"""Phase B ablation: Gate2 MC 截面置换 + 多重比较校正 — 裁决突破中(1.5) reversal +0.156 真伪。

owner=analysis/model_validation_reliability_design_20260614.md (Gate2)。真金白银统计裁决: 我跑了 ~30 cell,
单看最高 (+0.156) = selection bias 必虚高。两道校正:
  (a) MC 截面置换: 固定每个 OOS 交易日, 打乱 (股票 -> forward 收益) 配对, 重算该 cell OOS RankIC ->
      null 分布。real +0.156 是否超 null 95 分位? p = P(null >= real)。测"真有横截面技能"(非子集成分артефакт)。
  (b) 多重比较: Bonferroni p_adj = min(1, p * n_cells_tried)。30 cell 试出的最高, p 须乘 30 仍显著才算真。
预注册: real_ic 来自实验2 Stage1.5 reversal cell; null=shuffle; 判据 p_adj<0.05 -> 真 edge 解锁形态维; 否则噪声。
seed 固定可复现。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "backend"))

from scripts.experiment_l0_baseline import load_kline  # noqa: E402
from scripts.experiment_per_stage_ic import load_stage_map  # noqa: E402
from services.formula_engine.features import feature_reversal  # noqa: E402
from services.portfolio_walk_forward.oos_ic import (  # noqa: E402
    _month, cross_sectional_ic, expanding_monthly_windows, forward_returns,
)
from services.experiment_store import open_store, record_ic_cell, record_verdict, record_artifact  # noqa: E402
from services.experiment_harness import anomaly_verdict, leakage_gate  # noqa: E402  本脚本=事后异常核查工具 (ablation)

HORIZON = 5
EMBARGO = 5
N_PERM = 500       # rule-compliance: ok evidence=置换次数 (95分位/p稳定)
N_CELLS_TRIED = 30  # rule-compliance: ok evidence=实验1+2+3 试过的 cell 数 (Bonferroni 分母)
SEED = 20260615    # rule-compliance: ok evidence=可复现固定种子


def build_cell_by_date(stage: str, start: str) -> dict[str, tuple[np.ndarray, np.ndarray]]:
    """Stage=stage 的 reversal cell -> {date: (feat[], fwd[])} (横截面)。"""
    by_code = load_kline(start, None, 0)
    stage_map = load_stage_map(start)
    by_date: dict[str, tuple[list, list]] = {}
    for code, bars in by_code.items():
        closes, dates = bars["close"], bars["date"]
        if len(closes) < HORIZON + 60:
            continue
        fwd = forward_returns(dates, closes, HORIZON)
        feat = feature_reversal(closes, lookback=20)
        for i, date in enumerate(dates):
            if feat[i] is None or fwd[i] is None:
                continue
            if stage_map.get((code, date)) != stage:
                continue
            f, l = by_date.setdefault(date, ([], []))
            f.append(feat[i]); l.append(fwd[i])
    return {d: (np.asarray(f, float), np.asarray(l, float)) for d, (f, l) in by_date.items()}


def oos_test_dates(by_date: dict) -> list[str]:
    months = sorted({_month(d) for d in by_date})
    windows = expanding_monthly_windows(months, min_train_months=6, forward_months=1, min_total_months=12)
    out: list[str] = []
    for _train, test in windows:
        tset = set(test)
        tdates = sorted(d for d in by_date if _month(d) in tset)
        if EMBARGO > 0:
            tdates = tdates[:-EMBARGO] if len(tdates) > EMBARGO else []
        out.extend(tdates)
    return out


def mean_ic(by_date: dict, test_dates: list[str], rng: np.random.Generator | None = None) -> float | None:
    """OOS 日度截面 IC 均值; rng!=None 时每日打乱 fwd (置换 null)。"""
    ics = []
    for d in test_dates:
        f, l = by_date[d]
        if f.size < 3:  # rule-compliance: ok evidence=cross_sectional_ic 同口径 spearman 最小样本 3
            continue
        ll = l[rng.permutation(l.size)] if rng is not None else l
        ic = cross_sectional_ic(list(f), list(ll))
        if ic is not None:
            ics.append(ic)
    return float(np.mean(ics)) if ics else None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--stage", default="1.5")
    ap.add_argument("--start", default="2023-01-01")  # rule-compliance: ok evidence=对齐 L0 baseline 窗口起点
    args = ap.parse_args(argv)

    # 事前 leakage 门 (固化: 即便 ablate 已 gated cell, 也自含 pit_guard 行为门)
    gate = leakage_gate(lambda b: feature_reversal(b["close"], lookback=20), list(load_kline(args.start, None, 20).values()))
    if not gate["clean"]:
        print(f"[BLOCK] reversal 事前 leakage 门 FAIL: {gate['sample_violations']}"); return 1
    print(f"[leakage] 事前门 PASS (reversal x {gate['n_stocks']} 股)", flush=True)

    print(f"[build] Stage{args.stage} reversal cell ...", flush=True)
    by_date = build_cell_by_date(args.stage, args.start)
    test_dates = oos_test_dates(by_date)
    print(f"[build] {len(by_date)} 日, OOS test {len(test_dates)} 日", flush=True)

    real = mean_ic(by_date, test_dates)
    print(f"[real] Stage{args.stage} reversal OOS RankIC = {real:+.4f}")
    # 事后异常核查确认: 正因触 §4.2 红线才做本 ablation (不直接用/弃)
    av = anomaly_verdict(real, baseline=0.064)
    print(f"[anomaly] {av['verdict']}: {av['action'] or 'CLEAN'} -> 本脚本即该红线要求的 MC 截面置换 ablation")

    rng = np.random.default_rng(SEED)
    null = []
    for k in range(N_PERM):
        v = mean_ic(by_date, test_dates, rng=rng)
        if v is not None:
            null.append(v)
        if (k + 1) % 100 == 0:
            print(f"[perm] {k+1}/{N_PERM} ...", flush=True)
    null = np.asarray(null)
    p95 = float(np.percentile(null, 95))
    p_raw = float((null >= real).mean())
    p_adj = min(1.0, p_raw * N_CELLS_TRIED)
    verdict = "REAL_EDGE" if p_adj < 0.05 else "NOISE_OR_SELECTION_BIAS"

    print(f"\n===== Gate2 MC 截面置换 ablation (Stage{args.stage} reversal) =====")
    print(f"real OOS RankIC      = {real:+.4f}")
    print(f"null mean / std      = {null.mean():+.4f} / {null.std():.4f}")
    print(f"null 95 分位         = {p95:+.4f}")
    print(f"p_raw (null>=real)   = {p_raw:.4f}  ({N_PERM} 置换)")
    print(f"p_adj (Bonferroni x{N_CELLS_TRIED}) = {p_adj:.4f}")
    print(f"VERDICT              = {verdict}")
    print(f"  -> {'真横截面 edge, 解锁形态维' if verdict=='REAL_EDGE' else '可能噪声/选择偏差, 不解锁'}")

    out = {"experiment": "ablation_gate2_mc_permutation", "stage": args.stage,
           "real_oos_rank_ic": real, "null_mean": float(null.mean()), "null_std": float(null.std()),
           "null_p95": p95, "p_raw": p_raw, "n_perm": N_PERM, "n_cells_tried": N_CELLS_TRIED,
           "p_adj_bonferroni": p_adj, "verdict": verdict, "seed": SEED}
    out_path = REPO / "analysis" / f"ablation_gate2_stage{args.stage}_20260615.json"
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[out] {out_path}")

    # 留档 L4 experiment_store (固化进流程)
    run_id = f"phaseb_ablation_gate2_stage{args.stage}_20260615"
    with open_store() as st:
        record_ic_cell(st, run_id=run_id, data_snapshot=f"v_price_kline_qfq@{args.start}",
                       consumer_id=f"reversal_short_term|stage{args.stage}", metric="oos_rank_ic",
                       value=real, n_windows=len(test_dates))
        record_verdict(st, run_id=run_id, family="conditional_segment_ablation", verdict=verdict,
                       judges={"real_oos_rank_ic": real, "null_mean": float(null.mean()), "null_p95": p95,
                               "p_raw": p_raw, "p_adj_bonferroni": p_adj, "n_perm": N_PERM, "n_cells_tried": N_CELLS_TRIED},
                       confirmed_by_owner=1 if verdict == "REAL_EDGE" else 0)
        record_artifact(st, run_id=run_id, artifact_path=out_path)
    print(f"[store] experiment_store 留档 ablation verdict={verdict} (run_id={run_id})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
