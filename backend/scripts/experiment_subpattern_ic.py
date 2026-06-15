"""Phase B S1: 技术形态细分子型 IC — 本地方向性 V0 (用户"低位多种细分")。

owner=analysis/segment_taxonomy_design_20260615.md。把"低位"拆成行为不同的子型, 验 reversal IC 在哪种
子型更强 (实验2: 突破中 1.5 +0.156 / 底部 1 仅 +0.004 → 底部需细分)。

防泄露 (用户铁律: 验证前先跑防泄露工具): 所有子型判据 + expanding 历史分位**只用 closes/volumes[:i+1]**;
内置 PIT 自检 (在 [:n] 与 [:n+pad] 上算, 断言过去标签不变); 不过自检不算 IC。

子型 (config 化候选, 此处 V0 hardcode 带 evidence; 转 Optuna 搜参时进 technical_stage.yaml):
  低位横盘 / 冲高回落后相对低位横盘 / 突破回踩 / 历史低分位 / 低位放量。
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "backend"))

from scripts.experiment_l0_baseline import _db  # noqa: E402  rule-compliance: ok evidence=reuse market-db path resolver
from services.duck_adapter import connect as duck_connect  # noqa: E402  central adapter (db-boundary ok)
from services.formula_engine.base import sma  # noqa: E402
from services.formula_engine.features import feature_reversal  # noqa: E402
from services.portfolio_walk_forward.oos_ic import PanelRow, forward_returns, oos_rank_ic  # noqa: E402
from services.experiment_store import open_store, record_ic_cells, record_pit_check, record_verdict, record_artifact  # noqa: E402
from services.experiment_harness import anomaly_verdict  # noqa: E402  事后异常核查 (pit_selfcheck=事前 leakage 门)


def load_kline_vol(start: str) -> dict:
    """v_price_kline_qfq -> {code: {date,close,high,low,volume}} (含量能, 按 code,date 升序 PIT)。"""
    conn = duck_connect(str(_db("market")), read_only=True)
    try:
        rows = conn.execute(
            f"SELECT code, date, close, high, low, volume FROM v_price_kline_qfq WHERE date >= '{start}' "
            "ORDER BY code, date"
        ).fetchall()
    finally:
        conn.close()
    by_code: dict[str, dict] = defaultdict(lambda: {"date": [], "close": [], "high": [], "low": [], "volume": []})
    for code, date, close, high, low, vol in rows:
        d = by_code[code]
        d["date"].append(date); d["close"].append(close)
        d["high"].append(high); d["low"].append(low); d["volume"].append(vol)
    return dict(by_code)

HORIZON = 5
EMBARGO = 5
# 子型阈值 (V0 hardcode, evidence=segment_taxonomy_design S1; 转正走 Optuna search space)
RANGE_LB = 120        # rule-compliance: ok evidence=60周区间位置近似(日线120)
SLOPE_LB = 10         # rule-compliance: ok evidence=MA30 走平判定回看
MA_MID = 30           # rule-compliance: ok evidence=Weinstein MA30
RECENT_HIGH_LB = 60   # rule-compliance: ok evidence=冲高回落 近高点窗
LOW_POS = 0.30        # rule-compliance: ok evidence=低位分界 range_pos
FLAT_EPS = 0.02       # rule-compliance: ok evidence=走平 |slope| 上限
PULLBACK_DD = -0.20   # rule-compliance: ok evidence=冲高回落 跌幅下限
LOW_VOL = 0.90        # rule-compliance: ok evidence=缩量 vol_ratio 上限
HIGH_VOL = 1.50       # rule-compliance: ok evidence=放量 vol_ratio 下限
HIST_LOW_PCT = 0.25   # rule-compliance: ok evidence=历史低分位 expanding


def classify_subpattern(closes: list[float], volumes: list[float]) -> list[str | None]:
    """每日子型标签 (可重叠时取优先序第一个); PIT: 只用 [:i+1]。"""
    n = len(closes)
    out: list[str | None] = [None] * n
    c = np.asarray(closes, dtype=float)
    v = np.asarray(volumes, dtype=float)
    ma_mid = sma(closes, MA_MID)
    vol_ma = sma(volumes, 20)
    for i in range(MA_MID + SLOPE_LB, n):
        mm = ma_mid[i]
        if mm is None or ma_mid[i - SLOPE_LB] is None:
            continue
        slope = (mm - ma_mid[i - SLOPE_LB]) / max(ma_mid[i - SLOPE_LB], 1e-9)
        win = c[max(0, i - RANGE_LB):i + 1]           # 过去 RANGE_LB+今天
        lo, hi = win.min(), win.max()
        range_pos = (c[i] - lo) / (hi - lo) if hi > lo else 0.5
        vr = c[i] * 0 + (v[i] / vol_ma[i] if vol_ma[i] not in (None, 0) and not np.isnan(vol_ma[i]) else 1.0)
        recent_high = c[max(0, i - RECENT_HIGH_LB):i + 1].max()
        dd = c[i] / recent_high - 1.0 if recent_high > 0 else 0.0
        exp_pct = float((c[:i + 1] <= c[i]).sum()) / (i + 1)   # expanding 分位, 严格 <=i
        # 优先序: 冲高回落后低位横盘 > 低位横盘 > 低位放量 > 突破回踩 > 历史低分位
        if dd < PULLBACK_DD and range_pos < 0.5 and abs(slope) < FLAT_EPS * 1.5:
            out[i] = "pullback_low_flat"
        elif range_pos < LOW_POS and abs(slope) < FLAT_EPS and vr < LOW_VOL:
            out[i] = "low_flat"
        elif range_pos < LOW_POS and vr > HIGH_VOL:
            out[i] = "low_vol_spike"
        elif 0.95 * mm <= c[i] <= 1.05 * mm and c[max(0, i - 10):i].max() > mm:
            out[i] = "breakout_pullback"
        elif exp_pct < HIST_LOW_PCT:
            out[i] = "hist_low_pct"
    return out


def pit_selfcheck(by_code: dict, n_probe: int = 30, pad: int = 5) -> dict:
    """防泄露行为门: 在 closes[:m] 与 [:m+pad] 上算子型, 断言过去标签不变 (追加未来 bar 不改历史)。"""
    checked = violations = 0
    codes = list(by_code)[:n_probe]
    for code in codes:
        closes, vols = by_code[code]["close"], by_code[code]["volume"]
        m = len(closes) - pad
        if m < MA_MID + SLOPE_LB + 20:
            continue
        full = classify_subpattern(closes[:m + pad], vols[:m + pad])
        trunc = classify_subpattern(closes[:m], vols[:m])
        checked += 1
        # 过去 [:m] 的标签必须一致 (留 warmup 余量比末段)
        for i in range(MA_MID + SLOPE_LB, m - pad):
            if full[i] != trunc[i]:
                violations += 1
                break
    return {"checked_stocks": checked, "violation_stocks": violations,
            "verdict": "PIT_CLEAN" if violations == 0 else "LEAKAGE"}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--start", default="2023-01-01")  # rule-compliance: ok evidence=对齐L0窗
    args = ap.parse_args(argv)

    print("[load] K线 ...", flush=True)
    by_code = load_kline_vol(args.start)
    print(f"[load] {len(by_code)} 股", flush=True)

    print("[pit] 防泄露自检 (子型分类器 追加未来bar 不改过去) ...", flush=True)
    pit = pit_selfcheck(by_code)
    print(f"[pit] {pit}")
    if pit["verdict"] != "PIT_CLEAN":
        print("[BLOCK] 子型分类器 PIT 自检 FAIL — 不算 IC (泄漏死)。"); return 1

    panels: dict[str, list[PanelRow]] = defaultdict(list)
    for code, bars in by_code.items():
        closes, vols, dates = bars["close"], bars["volume"], bars["date"]
        if len(closes) < MA_MID + SLOPE_LB + HORIZON + 20:
            continue
        fwd = forward_returns(dates, closes, HORIZON)
        feat = feature_reversal(closes, lookback=20)   # reversal 主特征
        sub = classify_subpattern(closes, vols)
        for i, date in enumerate(dates):
            if feat[i] is None or fwd[i] is None:
                continue
            row = PanelRow(date=date, code=code, feature=feat[i], fwd_ret=fwd[i])
            panels["ALL"].append(row)
            if sub[i] is not None:
                panels[sub[i]].append(row)

    print("[ic] reversal IC per 子型 ...", flush=True)
    print(f"\n===== reversal_short_term OOS RankIC per 技术形态子型 (h=5,emb=5) =====")
    print(f"{'子型':<22}{'OOS RankIC':>12}{'IC_IR':>9}{'n_days':>8}{'n_rows':>12}")
    cells = {}
    for seg in ["ALL", "pullback_low_flat", "low_flat", "low_vol_spike", "breakout_pullback", "hist_low_pct"]:
        if seg not in panels:
            continue
        r = oos_rank_ic(panels[seg], embargo_days=EMBARGO)
        cells[seg] = {"oos_rank_ic": r.get("oos_rank_ic"), "ic_ir": r.get("ic_ir"),
                      "n_days": r.get("n_days"), "n_rows": len(panels[seg])}
        ic, ir = r.get("oos_rank_ic"), r.get("ic_ir")
        ic_s = f"{ic:+.4f}" if ic is not None else "None"
        ir_s = f"{ir:+.3f}" if ir is not None else "None"
        print(f"{seg:<22}{ic_s:>12}{ir_s:>9}{r.get('n_days',0):>8}{len(panels[seg]):>12}")

    # 事后异常核查 (固化: 每 cell 过 anomaly_verdict §4.2, 命中 -> 须 ablation 不直接用/弃)
    anomalies = {seg: anomaly_verdict(c.get("oos_rank_ic"), baseline=0.064) for seg, c in cells.items()}
    flags = {seg: a for seg, a in anomalies.items() if a["verdict"] not in ("CLEAN", "UNKNOWN")}
    print(f"[flags] 事后异常核查: {flags or '全 CLEAN (无 cell 触 §4.2 红线)'}")
    out = {"experiment": "subpattern_ic", "pit_selfcheck": pit, "baseline_ref": {"stage1": 0.0038, "stage1.5": 0.1559, "ALL": 0.064},
           "cells": cells, "anomaly_check": flags, "note": "directional V0; 高cell须DSR多重比较校正+独立holdout才转正"}
    out_path = REPO / "analysis" / "subpattern_ic_20260615.json"
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[ref] 对照: 底部Stage1=+0.004, 突破中Stage1.5=+0.156, 市场ALL=+0.064")
    print(f"[out] {out_path}")

    # 留档 L4 experiment_store (固化进流程)
    run_id = "phaseb_subpattern_ic_20260615"
    with open_store() as st:
        record_pit_check(st, run_id=run_id, step="pit_selfcheck", check_name="subpattern_classifier",
                         passed=pit["verdict"] == "PIT_CLEAN", detail=pit)
        n = record_ic_cells(st, run_id=run_id, data_snapshot=f"v_price_kline_qfq@{args.start}", cells=cells)
        record_verdict(st, run_id=run_id, family="conditional_segment", verdict="LOW_SUBPATTERN_NEGATIVE",
                       judges={"finding": "低位5子型 reversal IC 全 < 市场 +0.064, edge 在突破非低位",
                               "best_low": "low_vol_spike +0.047"})
        record_artifact(st, run_id=run_id, artifact_path=out_path)
    print(f"[store] experiment_store 留档 {n} IC cells + PIT门 + verdict (run_id={run_id})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
