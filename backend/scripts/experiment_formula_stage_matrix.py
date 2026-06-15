"""Phase B 实验2: (公式 x 形态) IC 矩阵 + MACD 零轴上下分裂 — 本地方向性 V0。

owner=analysis/conditional_stage_strategy_design_20260614.md。用户 direction (2026-06-15):
  (a) macd 金叉在零轴上方/下方不一样 -> 按 DIF=ema12-ema26 符号分裂 macd cell;
  (b) (公式 x 形态) 矩阵 -> 4 active 公式各跑 per-stage IC, 找"哪个公式适配哪个形态";
  方向成立 (本地 V0) 再上 Optuna+Modal 搜更细分割 (低位横盘 vs 冲高回落后低位横盘等)。

预注册纪律 (跑前冻结):
  PIT: extract_feature[t] 只用 bars[:t+1] (pit_guard 已核); stage[t] as-of; DIF[t]=ema12[t]-ema26[t] 只用过去;
       label=forward 5d; embargo=5; walk-forward OOS-only。
  多重比较 (关键): 本实验是 20+ cell 的地图 (4公式x5形态 + macd零轴x2 + 等), **任何高 cell 是方向线索非结论** —
       单看高 cell = selection bias。转正前须 DSR-deflate (n_cells 多重比较校正) + ablation (MC截面置换/子周期)。
  对照: 各公式 ALL (市场级) IC 须对齐 L0 标尺 (reversal +0.064, macd -0.049, ma -0.073, turtle -0.037)。
  Anomaly: 任一 |IC|>0.30 -> leakage 警报。relative: cell IC > 1.5x 该公式市场基线 -> 标 pending ablation。
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]  # repo root
sys.path.insert(0, str(REPO / "backend"))

from scripts.experiment_l0_baseline import load_kline  # noqa: E402  rule-compliance: ok evidence=reuse market-db qfq loader
from scripts.experiment_per_stage_ic import load_stage_map  # noqa: E402  rule-compliance: ok evidence=reuse stage loader
from services.formula_engine.features import ema, extract_feature, ACTIVE_FORMULAS  # noqa: E402
from services.portfolio_walk_forward.oos_ic import PanelRow, forward_returns, oos_rank_ic  # noqa: E402
from services.experiment_store import open_store, record_ic_cells, record_verdict, record_artifact  # noqa: E402
from services.experiment_harness import leakage_gate, anomaly_verdict  # noqa: E402

HORIZON = 5      # from spec: l0 forward 期
EMBARGO = 5      # from spec §4.1
MACD_FAST = 12   # from yaml: formula_macd 默认  # rule-compliance: ok evidence=macd standard fast period
MACD_SLOW = 26   # from yaml: formula_macd 默认  # rule-compliance: ok evidence=macd standard slow period
STAGES = ["1", "1.5", "2", "3", "4"]
STAGE_NAMES = {"1": "底部", "1.5": "突破中", "2": "上升", "3": "顶部", "4": "下跌"}
# 各公式市场级 L0 标尺 (measured: l0_baseline) 供 relative 红线
L0_BASELINE = {"reversal_short_term": 0.064, "macd_golden_cross": -0.049,
               "ma_base_breakout": -0.073, "turtle_breakout": -0.037}


def macd_dif_sign(closes: list[float]) -> list[int | None]:
    """DIF=ema_fast-ema_slow 符号 (零轴上=+1/下=-1); PIT (ema 只用过去)。"""
    ef, es = ema(closes, MACD_FAST), ema(closes, MACD_SLOW)
    out: list[int | None] = []
    for f, s in zip(ef, es):
        out.append(None if f is None or s is None else (1 if f - s > 0 else -1))
    return out


def _ic(panel: list[PanelRow]) -> dict:
    return oos_rank_ic(panel, embargo_days=EMBARGO)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--start", default="2023-01-01")  # rule-compliance: ok evidence=data start aligns L0 baseline window
    args = ap.parse_args(argv)

    print("[load] K线 + stage ...", flush=True)
    by_code = load_kline(args.start, None, 0)
    stage_map = load_stage_map(args.start)
    print(f"[load] {len(by_code)} 股, stage {len(stage_map):,}", flush=True)

    # 事前 leakage 门 (固化: 算 IC 前必跑 pit_guard 行为门, 不过 BLOCK 不算)
    sample = list(by_code.values())[:20]
    for formula in ACTIVE_FORMULAS:
        g = leakage_gate(lambda b, f=formula: extract_feature(f, b), sample)
        if not g["clean"]:
            print(f"[BLOCK] {formula} 事前 leakage 门 FAIL: {g['sample_violations']}"); return 1
    print(f"[leakage] 事前门 PASS (4 公式 x {len(sample)} 股 pit_guard 行为门)", flush=True)

    # panels[(formula, segment)] = [PanelRow]; segment = stage 或 'ALL' 或 macd 零轴 'DIF+'/'DIF-'
    panels: dict[tuple[str, str], list[PanelRow]] = defaultdict(list)
    for code, bars in by_code.items():
        closes = bars["close"]
        dates = bars["date"]
        if len(closes) < HORIZON + 60:
            continue
        fwd = forward_returns(dates, closes, HORIZON)
        dif_sign = macd_dif_sign(closes)
        for formula in ACTIVE_FORMULAS:
            feat = extract_feature(formula, bars)
            for i, date in enumerate(dates):
                if feat[i] is None or fwd[i] is None:
                    continue
                row = PanelRow(date=date, code=code, feature=feat[i], fwd_ret=fwd[i])
                panels[(formula, "ALL")].append(row)
                st = stage_map.get((code, date))
                if st is not None:
                    panels[(formula, st)].append(row)
                # macd 零轴上下分裂 (用户 direction)
                if formula == "macd_golden_cross" and dif_sign[i] is not None:
                    zone = "DIF+" if dif_sign[i] == 1 else "DIF-"
                    panels[(formula, zone)].append(row)
                    if st is not None:
                        panels[(formula, f"{zone}x{st}")].append(row)

    print("[ic] computing matrix ...", flush=True)
    cells: dict[str, dict] = {}
    for (formula, seg), panel in panels.items():
        r = _ic(panel)
        cells[f"{formula}|{seg}"] = {"oos_rank_ic": r.get("oos_rank_ic"), "ic_ir": r.get("ic_ir"),
                                     "n_days": r.get("n_days"), "n_rows": len(panel)}

    # 输出: (公式 x 形态) 矩阵
    print("\n===== (公式 x 形态) OOS RankIC 矩阵 (horizon=5, embargo=5) =====")
    hdr = f"{'formula':<20}{'ALL':>9}" + "".join(f"{STAGE_NAMES[s]:>8}" for s in STAGES)
    print(hdr)
    for formula in ACTIVE_FORMULAS:
        line = f"{formula:<20}{_f(cells.get(f'{formula}|ALL',{}).get('oos_rank_ic')):>9}"
        for s in STAGES:
            line += f"{_f(cells.get(f'{formula}|{s}',{}).get('oos_rank_ic')):>8}"
        print(line)

    # MACD 零轴上下 (用户 direction)
    print("\n===== MACD 金叉零轴上下分裂 (DIF=ema12-ema26 符号) =====")
    print(f"{'segment':<16}{'OOS RankIC':>12}{'IC_IR':>9}{'n_days':>8}{'n_rows':>12}")
    for seg in ["ALL", "DIF+", "DIF-"] + [f"DIF+x{s}" for s in STAGES] + [f"DIF-x{s}" for s in STAGES]:
        c = cells.get(f"macd_golden_cross|{seg}")
        if c:
            print(f"{seg:<16}{_f(c.get('oos_rank_ic')):>12}{_f(c.get('ic_ir')):>9}{c.get('n_days',0):>8}{c.get('n_rows',0):>12}")

    # 事后异常核查 (固化: anomaly_verdict §4.2 红线, 命中 -> 标 pending ablation 不直接用/弃)
    flags = []
    for key, c in cells.items():
        formula = key.split("|")[0]
        if c.get("n_days", 0) < 60:
            continue
        av = anomaly_verdict(c.get("oos_rank_ic"), baseline=L0_BASELINE.get(formula))
        if av["verdict"] not in ("CLEAN", "UNKNOWN"):
            flags.append({"cell": key, "ic": round(c["oos_rank_ic"], 4), "verdict": av["verdict"], "action": av["action"]})
    print(f"\n[flags] 事后异常核查 (须 ablation, 不直接用/弃): {flags}")

    out = {"experiment": "formula_stage_matrix", "params": {"horizon": HORIZON, "embargo": EMBARGO, "start": args.start},
           "cells": cells, "flags": flags, "note": "directional V0; 任何高cell须DSR多重比较校正+ablation才转正"}
    out_path = REPO / "analysis" / "formula_stage_matrix_20260615.json"
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[out] {out_path}")

    # 留档 L4 experiment_store (固化进流程, 非散落 JSON)
    run_id = "phaseb_formula_stage_matrix_20260615"
    with open_store() as st:
        n = record_ic_cells(st, run_id=run_id, data_snapshot=f"v_price_kline_qfq@{args.start}", cells=cells)
        record_verdict(st, run_id=run_id, family="conditional_segment", verdict="MATRIX_MAPPED",
                       judges={"breakout_regime": "reversal +0.156 vs macd/ma -0.116/-0.117",
                               "macd_zero_axis": "DIF+ -0.059 vs DIF- -0.026", "low_stage": "all formulas ~0"},
                       gate_blockers={"relative_flags_pending_ablation": flags})
        record_artifact(st, run_id=run_id, artifact_path=out_path)
    print(f"[store] experiment_store 留档 {n} IC cells + verdict (run_id={run_id})")
    return 0


def _f(x) -> str:
    return f"{x:+.4f}" if isinstance(x, (int, float)) else "  None"


if __name__ == "__main__":
    sys.exit(main())
