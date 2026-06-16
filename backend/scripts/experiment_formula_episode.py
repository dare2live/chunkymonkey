#!/usr/bin/env python3
"""G1/B2 公式 episode × segment 刻画 (owner=docs/conditional_alpha_program.md §2 L4)。

验证用户命题: 公式在【对的形态×阶段 cell】里收益不是个位数 — 无条件全市场截面把真 edge 平均没了。
做法: 公式买入信号→死叉/止时 episode (services/episode_engine), 每 episode 打入场 segment
(technical_stage 形态×阶段 × MACD 零轴分层) → 按 (公式×segment cell) 聚合含成本 episode 统计,
对比无条件 (__ALL__)。本地 market.duckdb 价量, 零 tushare; 留档进 experiment_store (G3 散落死门合规)。

PIT/执行: 信号 close[i] 确认入场 T+1 open, 一字板剔 (buyable-only), 含非对称成本, stage[i] 只用 bars[:i]。
V0: formula=macd_golden_cross (买=金叉/卖=死叉, 两信号现成); hold_cap 兜底 60 日; 入场 >= 2020。
用法: python backend/scripts/experiment_formula_episode.py [--formula macd_golden_cross] [--hold-cap 60] [--start 2020-01-01]
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
from services.experiment_store import open_store, record_verdict, record_artifact
from services.duck_adapter import connect


KLINE_DB = REPO / "data" / "market.duckdb"   # rule-compliance: ok evidence=K线只读真相源 (PROJECT_INDEX §1 三库)
KLINE_TABLE = "price_kline_qfq_tushare"


def _load_kline() -> dict[str, dict]:
    """加载全市场 K 线 (read_only), 按 code 分组为 numpy 数组 (升序)。"""
    con = connect(str(KLINE_DB), read_only=True)
    try:
        rows = con.execute(
            f"SELECT code, date, open, high, low, close, volume FROM {KLINE_TABLE} ORDER BY code, date"
        ).fetchall()
    finally:
        con.close()
    by_code: dict[str, dict] = {}
    cur_code, buf = None, []
    def _flush(code, b):
        if not b:
            return
        arr = list(zip(*b))
        by_code[code] = {
            "dates": np.array(arr[0]),
            "open": np.array(arr[1], dtype=float), "high": np.array(arr[2], dtype=float),
            "low": np.array(arr[3], dtype=float), "close": np.array(arr[4], dtype=float),
            "volume": np.array(arr[5], dtype=float),
        }
    for code, d, o, h, l, c, v in rows:
        if code != cur_code:
            _flush(cur_code, buf)
            cur_code, buf = code, []
        buf.append((d, o, h, l, c, v))
    _flush(cur_code, buf)
    return by_code


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--formula", default="macd_golden_cross")
    p.add_argument("--hold-cap", type=int, default=60)
    p.add_argument("--start", default="2020-01-01")   # rule-compliance: ok evidence=2020 起 KPI OOS 窗 (goal.md North-Star)
    p.add_argument("--out-dir", default="analysis")
    args = p.parse_args(argv)

    cfg = ee.load_exec_cfg()
    macd = MacdGoldenCross()
    print(f"[load] {KLINE_TABLE} ...", flush=True)
    by_code = _load_kline()
    print(f"[load] {len(by_code)} 股", flush=True)

    all_eps: list[ee.Episode] = []
    skipped_short = 0
    for code, k in by_code.items():
        closes, volumes = k["close"], k["volume"]
        if len(closes) < 260:                # 不足 MA250 warmup → stage 全 unknown, 跳过
            skipped_short += 1
            continue
        dif, dea, crossed_up, crossed_down = macd._macd_components(closes)
        stages = classify_technical_stage(closes, volumes)
        eps = ee.build_episodes(
            code, k["dates"], k["open"], k["high"], k["low"], closes,
            crossed_up, crossed_down, stages, dif,
            cfg=cfg, hold_cap=args.hold_cap, start_date=args.start,
        )
        all_eps.extend(eps)

    print(f"[episodes] {len(all_eps):,} 个 (跳过 {skipped_short} 短史股)", flush=True)
    agg = ee.aggregate_by_cell(all_eps)

    # 打印: 无条件 vs 各 cell (按 n_episodes 降序)
    def _fmt(v, pct=False):
        if v is None:
            return "  NA  "
        return f"{v:+.2%}" if pct else f"{v:.2f}"
    print(f"\n===== {args.formula} episode × segment (含成本, buyable-only, hold_cap={args.hold_cap}, 入场>={args.start}) =====")
    print(f"{'cell(stage|零轴)':<18}{'n':>7}{'胜率':>8}{'均净收益':>10}{'中位':>9}{'盈亏比':>8}{'均持仓':>8}{'年化/单段':>11}")
    ordered = sorted(agg.items(), key=lambda kv: kv[1]["n_episodes"], reverse=True)
    for cell, s in ordered:
        print(f"{cell:<18}{s['n_episodes']:>7}{_fmt(s['win_rate'],1):>8}{_fmt(s['mean_net_return'],1):>10}"
              f"{_fmt(s['median_net_return'],1):>9}{_fmt(s['payoff_ratio']):>8}{_fmt(s['mean_hold_days']):>8}{_fmt(s['annualized_per_episode'],1):>11}")

    run_id = f"{args.formula}_episode_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    out_path = Path(args.out_dir) / f"{run_id}.json"
    out = {"experiment": "formula_episode_segment", "formula": args.formula, "engine": "episode_engine_v0",
           "hold_cap": args.hold_cap, "start": args.start, "n_episodes": len(all_eps),
           "note": "公式买→卖 episode × segment 含成本刻画; 验证条件化 vs 无条件 (用户方法论第4步); buyable-only/PIT/非对称成本",
           "cells": agg}
    Path(args.out_dir).mkdir(exist_ok=True)
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[out] {out_path}")

    # 留档进 experiment_store (G3 散落死门合规)
    with open_store() as st:
        record_verdict(st, run_id=run_id, family="formula_episode_segment", verdict="CHARACTERIZED",
                       judges={"formula": args.formula, "n_episodes": len(all_eps), "cells": agg,
                               "note": "episode×segment 刻画, 非 go/no-go; 看条件化 cell 含成本是否破个位数"},
                       confirmed_by_owner=0)
        record_artifact(st, run_id=run_id, artifact_path=str(out_path))
    print(f"[store] 留档 run_id={run_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
