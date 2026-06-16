#!/usr/bin/env python3
"""G1/B2 公式 episode × segment × 时间窗 刻画 (owner=docs/conditional_alpha_program.md §2 L4)。

验证用户命题: 公式在【对的形态×阶段 cell】里收益不是个位数 — 无条件全市场截面把真 edge 平均没了;
并按 trailing 时间窗 (近 3/6/9/12/18/24/36 月) 看 edge 是稳还是衰减/集中某段 (mio #6 分层非平均)。

公式买入信号→卖出信号/止时 episode (services/episode_engine), 每 episode 打入场 segment
(technical_stage 形态阶段 × MACD 零轴分层) + 入场日期 → 按 (公式×segment cell) + trailing 窗聚合。
本地 market.duckdb 价量零 tushare; 留档进 experiment_store (G3 散落死门合规)。

可跑公式 (有清晰 trigger; 其余 5 个 yaml 公式生成器随 reset wipe, 待重建):
  macd_golden_cross  买=金叉/卖=死叉 (Python compute_signals)
  ma_base_breakout   买=上穿MA145/卖=跌破MA145 (feature_ma_distance 上穿下穿 0)
  turtle_breakout    买=20日通道新高突破/卖=hold_cap (feature_turtle_position 上穿 0.99)
segment 轴 (形态阶段 + MACD 零轴) 公式无关, 每股算一次复用。
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "backend"))

from services import episode_engine as ee
from services.formula_engine.macd_golden_cross import MacdGoldenCross
from services.formula_engine.technical_stage import classify_technical_stage
from services.formula_engine.features import feature_ma_distance, feature_turtle_position
from services.experiment_store import open_store, record_verdict, record_artifact
from services.duck_adapter import connect

KLINE_DB = REPO / "data" / "market.duckdb"   # rule-compliance: ok evidence=K线只读真相源 (PROJECT_INDEX §1 三库)
KLINE_TABLE = "price_kline_qfq_tushare"
_MACD = MacdGoldenCross()


def _to_arr(series: list) -> np.ndarray:
    return np.array([np.nan if x is None else x for x in series], dtype=float)


def _cross_up(arr: np.ndarray, th: float) -> np.ndarray:
    out = np.zeros(len(arr), dtype=bool)
    out[1:] = (arr[:-1] < th) & (arr[1:] >= th)
    return out


def _cross_down(arr: np.ndarray, th: float) -> np.ndarray:
    out = np.zeros(len(arr), dtype=bool)
    out[1:] = (arr[:-1] >= th) & (arr[1:] < th)
    return out


def formula_signals(fid: str, closes, highs, lows):
    """返回 (buy_mask, sell_mask) bool 数组。trigger 为 V0 口径 (formula yaml 全逻辑更richer, 此处取核心)。"""
    cl = list(closes)
    if fid == "macd_golden_cross":
        _, _, cu, cd = _MACD._macd_components(closes)
        return cu, cd
    if fid == "ma_base_breakout":
        mad = _to_arr(feature_ma_distance(cl, long_period=145))   # rule-compliance: ok evidence=from yaml formula_ma_base_breakout MA145
        return _cross_up(mad, 0.0), _cross_down(mad, 0.0)          # 上穿/跌破 MA145
    if fid == "turtle_breakout":
        pos = _to_arr(feature_turtle_position(list(highs), list(lows), cl, channel=20))   # rule-compliance: ok evidence=from yaml formula_turtle_breakout 20日通道
        buy = _cross_up(pos, 0.99)                                 # 通道新高突破; 卖=hold_cap (海龟出场口径另议)
        return buy, np.zeros(len(closes), dtype=bool)
    raise ValueError(f"未配 trigger 的 formula: {fid}")


FORMULAS = ("macd_golden_cross", "ma_base_breakout", "turtle_breakout")


def _load_kline() -> dict[str, dict]:
    con = connect(str(KLINE_DB), read_only=True)
    try:
        rows = con.execute(
            f"SELECT code, date, open, high, low, close, volume FROM {KLINE_TABLE} ORDER BY code, date"
        ).fetchall()
    finally:
        con.close()
    by_code, cur, buf = {}, None, []
    def _flush(code, b):
        if not b:
            return
        a = list(zip(*b))
        by_code[code] = {"dates": np.array(a[0]), "open": np.array(a[1], float), "high": np.array(a[2], float),
                         "low": np.array(a[3], float), "close": np.array(a[4], float), "volume": np.array(a[5], float)}
    for code, d, o, h, l, c, v in rows:
        if code != cur:
            _flush(cur, buf); cur, buf = code, []
        buf.append((d, o, h, l, c, v))
    _flush(cur, buf)
    return by_code


def _fmt(v, pct=False):
    return "  NA  " if v is None else (f"{v:+.2%}" if pct else f"{v:.2f}")


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--hold-cap", type=int, default=60)
    p.add_argument("--start", default="2020-01-01")   # rule-compliance: ok evidence=2020 起 KPI OOS 窗 (goal.md North-Star)
    p.add_argument("--out-dir", default="analysis")
    args = p.parse_args(argv)

    cfg = ee.load_exec_cfg()
    print(f"[load] {KLINE_TABLE} ...", flush=True)
    by_code = _load_kline()
    print(f"[load] {len(by_code)} 股", flush=True)

    eps_by_formula: dict[str, list] = {f: [] for f in FORMULAS}
    skipped = 0
    for code, k in by_code.items():
        closes, volumes = k["close"], k["volume"]
        if len(closes) < 260:
            skipped += 1
            continue
        stages = classify_technical_stage(closes, volumes)
        dif, _, _, _ = _MACD._macd_components(closes)            # 零轴分层轴 (公式无关)
        for fid in FORMULAS:
            buy, sell = formula_signals(fid, closes, k["high"], k["low"])
            eps = ee.build_episodes(code, k["dates"], k["open"], k["high"], k["low"], closes,
                                    buy, sell, stages, dif, cfg=cfg, hold_cap=args.hold_cap, start_date=args.start)
            eps_by_formula[fid].extend(eps)

    run_ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    summary = {}
    for fid in FORMULAS:
        eps = eps_by_formula[fid]
        agg = ee.aggregate_by_cell(eps)
        tw_all = ee.trailing_windows(eps)
        # 头部 cell 的 trailing (看 edge 时间稳定性)
        top_cells = sorted([c for c in agg if c != "__ALL__"], key=lambda c: agg[c]["n_episodes"], reverse=True)[:4]
        tw_cells = {c: ee.trailing_windows([e for e in eps if f"{e.entry_stage}|{e.entry_zero_axis}" == c]) for c in top_cells}
        summary[fid] = {"n_episodes": len(eps), "cells": agg, "trailing_all": tw_all, "trailing_top_cells": tw_cells}

        print(f"\n===== {fid}: {len(eps):,} episode (含成本/buyable-only/hold_cap={args.hold_cap}/入场>={args.start}) =====")
        print(f"{'cell(stage|零轴)':<16}{'n':>7}{'胜率':>7}{'均净':>8}{'盈亏比':>7}{'均持仓':>7}")
        for cell, s in sorted(agg.items(), key=lambda kv: kv[1]["n_episodes"], reverse=True):
            print(f"{cell:<16}{s['n_episodes']:>7}{_fmt(s['win_rate'],1):>7}{_fmt(s['mean_net_return'],1):>8}{_fmt(s['payoff_ratio']):>7}{_fmt(s['mean_hold_days']):>7}")
        print(f"  [trailing 近N月 均净/胜率 — 无条件]")
        wins = [w for w in ["3m","6m","9m","12m","18m","24m","36m"] if w in tw_all]
        print("   " + "".join(f"{w:>9}" for w in wins))
        print("   净" + "".join(f"{_fmt(tw_all[w]['mean_net_return'],1):>9}" for w in wins))
        print("   胜" + "".join(f"{_fmt(tw_all[w]['win_rate'],1):>9}" for w in wins))
        for c in top_cells[:2]:
            print(f"  [{c}] 净" + "".join(f"{_fmt(tw_cells[c][w]['mean_net_return'],1):>9}" for w in wins))

    out_path = Path(args.out_dir) / f"formula_episode_matrix_{run_ts}.json"
    Path(args.out_dir).mkdir(exist_ok=True)
    out_path.write_text(json.dumps({"experiment": "formula_episode_segment_time", "formulas": list(FORMULAS),
                                    "hold_cap": args.hold_cap, "start": args.start, "summary": summary,
                                    "note": "公式×segment×trailing窗 含成本刻画; buyable-only/PIT/非对称成本; 年化口径单 episode 非组合 NAV"},
                                   ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[out] {out_path}")

    with open_store() as st:
        for fid in FORMULAS:
            run_id = f"{fid}_episode_{run_ts}"
            record_verdict(st, run_id=run_id, family="formula_episode_segment", verdict="CHARACTERIZED",
                           judges={"formula": fid, "n_episodes": summary[fid]["n_episodes"],
                                   "cells": summary[fid]["cells"], "trailing_all": summary[fid]["trailing_all"],
                                   "note": "episode×segment×trailing 刻画, 非 go/no-go; 单 episode 口径非组合 NAV"},
                           confirmed_by_owner=0)
        record_artifact(st, run_id=f"formula_episode_matrix_{run_ts}", artifact_path=str(out_path))
    print(f"[store] 留档 {len(FORMULAS)} formula verdict + matrix (ts={run_ts})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
