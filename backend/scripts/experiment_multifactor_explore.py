#!/usr/bin/env python3
"""多因子组合探索 runner — 读 L2 fact_feature_panel, 含成本 execution-aware backtest R1 裁决。

owner=analysis/prereg_multifactor_exploration_20260615.md (冻结预注册) + docs/strategy_validation_contract.md (C-R1/R2/WinReturn)。
缘起 (/loop): 对主升浪猎手及其他公式做多因子探索, 找含成本 OOS 绝对收益最优组合 (R1, 非 IC)。

数据流 (写锁隔离): 信号读 **L2 feature_store fact_feature_panel** (不读 L0 raw, moth feature-layer-l2-bypass-ratchet 守);
  价格读 market price_kline_qfq_tushare (T+1 open / 涨跌停 / 容量)。复用 phaseD_signal_eval.evaluate_signal
  (IC necessary 快筛 -> 含成本 execution-aware backtest -> tradability/kpi 裁决 -> trailing -> experiment_store 留档)。

PIT: panel 因子建库时已 PIT (bars[:t+1]/盘后t-1/ann_date<=t); 本 runner 的组合 = **同日截面** z-score 加权和
  (PARTITION BY date, 只用当日横截面), 无跨期 lookahead。方向 (sign): --auto-sign 用 **train 窗** (start..train_end)
  每因子 OOS IC 符号定向 (训练窗内定符号, 不碰 holdout), 默认 single 模式不定向 (raw 高=做多, IC 符号自报)。

模式:
  single   : 逐因子单独跑 (sign+1, 长高因子篮), 含成本基线 — 最诚实无拟合的首测 (默认)。
  composite: 选定因子子集 z-score 加权 (signed) 合成单信号, 跑一次。

用法:
  # 5 因子各自含成本基线 (smoke 小窗先验逻辑):
  .venv/bin/python backend/scripts/experiment_multifactor_explore.py --mode single --start 2024-01-01
  # 全因子等权 composite, train 窗定向:
  .venv/bin/python backend/scripts/experiment_multifactor_explore.py --mode composite --auto-sign --start 2019-01-01
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "backend"))

from services.duck_adapter import connect as duck_connect  # noqa: E402  L2 panel 读
from services.leakage_detect import check_split_discipline  # noqa: E402  事前切分纪律门
from services.portfolio_walk_forward.oos_ic import PanelRow, forward_returns, oos_rank_ic  # noqa: E402
from services.phaseD_signal_eval import evaluate_signal  # noqa: E402  共享含成本裁决 harness
from scripts.experiment_l0_baseline import load_kline  # noqa: E402  只读 market K线
from scripts.experiment_phaseD_regime_timing import build_regime_ok  # noqa: E402  纯K线市场代理 regime

FEATURE_DB = REPO / "data" / "feature_store.duckdb"  # rule-compliance: ok evidence=L2 panel 独立库 (database_manifest feature_store)
PANEL = "fact_feature_panel"
ALL_FACTORS = ["mom_60", "reversal_20", "vol_20", "mf_trend_20", "roe_dt_asof"]  # rule-compliance: ok evidence=fact_feature_panel 5因子列 (build_feature_panel)
BOARD_PREFIXES = ("60", "00", "30", "68")  # rule-compliance: ok evidence=universe 主板/创业/科创 (与各实验同口径)
HORIZON = 5          # rule-compliance: ok evidence=IC 快筛 forward 窗 (与 phaseD 同)
EMBARGO = 5          # rule-compliance: ok evidence=IC purge embargo (>=horizon)
REBALANCE_DAYS = 20  # rule-compliance: ok evidence=pre-reg 月度调仓
TOP_K = 20           # rule-compliance: ok evidence=pre-reg 固定选股数
REGIME_MA = 60       # rule-compliance: ok evidence=pre-reg 市场代理趋势均线
BASELINE_IC = 0.064  # rule-compliance: ok evidence=L0 标尺 reversal +0.064 对照


def in_universe(code: str) -> bool:
    return code[:2] in BOARD_PREFIXES


def load_signal(factors: list[str], weights: list[float], signs: list[float], start: str) -> dict[str, dict]:
    """L2 panel -> 同日截面 z-score 加权 signed 合成 {code:{date:composite}} (PIT: PARTITION BY date 只用当日横截面)。

    composite = Σ_f w_f · s_f · COALESCE(z_f, 0); z_f=(f-avg(f) OVER date)/stddev(f) OVER date; 缺因子=0(中性)。
    """
    # 每因子一个 z-score 表达式 (同日横截面标准化), 缺值 COALESCE 0 = 中性贡献 (如 roe 47% 覆盖)
    terms, zcols = [], []
    for f, w, s in zip(factors, weights, signs):
        z = (f"(({f} - avg({f}) OVER (PARTITION BY date)) / "
             f"NULLIF(stddev_samp({f}) OVER (PARTITION BY date), 0))")
        zcols.append(f"{z} AS z_{f}")
        terms.append(f"{w * s} * COALESCE(z_{f}, 0)")
    sql = (f"WITH z AS (SELECT code, date, {', '.join(zcols)} FROM {PANEL} WHERE date >= ?) "
           f"SELECT code, date, ({' + '.join(terms)}) AS composite FROM z "
           f"WHERE composite IS NOT NULL")
    conn = duck_connect(str(FEATURE_DB), read_only=True)
    try:
        rows = conn.execute(sql, [start]).fetchall()
    finally:
        conn.close()
    sig: dict[str, dict] = {}
    for code, date, comp in rows:
        if comp is None or not in_universe(code):
            continue
        sig.setdefault(code, {})[date] = comp
    return sig


def train_ic_sign(factor: str, signal_one: dict, fwd_by_code: dict, train_end: str) -> float:
    """train 窗 (date<=train_end) 该因子 OOS RankIC 符号 (定向用, 不碰 holdout)。IC>=0 -> +1, <0 -> -1。"""
    panel = [PanelRow(date=d, code=c, feature=signal_one[c][d], fwd_ret=fwd_by_code[c][d])
             for c in signal_one for d in signal_one[c]
             if d <= train_end and d in fwd_by_code.get(c, {})]
    if not panel:
        return 1.0
    ic = oos_rank_ic(panel, embargo_days=EMBARGO).get("oos_rank_ic")
    return -1.0 if (ic is not None and ic < 0) else 1.0


def build_bars_fwd(by_code: dict) -> tuple[dict, dict, list]:
    """K线 -> bars_by_code {code:{date:(o,h,l,c,v)}} + fwd_by_code {code:{date:fwd5}} + calendar。"""
    bars_by_code, fwd_by_code = {}, {}
    for code, bars in by_code.items():
        if not in_universe(code):
            continue
        dates, closes = bars["date"], bars["close"]
        fwd = forward_returns(dates, closes, HORIZON)
        bb = {}
        for i, d in enumerate(dates):
            c = closes[i]
            if c is not None:
                bb[d] = (bars["open"][i], bars["high"][i], bars["low"][i], c, bars["volume"][i])
        bars_by_code[code] = bb
        fwd_by_code[code] = {d: fwd[i] for i, d in enumerate(dates) if fwd[i] is not None}
    calendar = sorted({d for bb in bars_by_code.values() for d in bb})
    return bars_by_code, fwd_by_code, calendar


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--start", default="2019-01-01")  # rule-compliance: ok evidence=L2 panel 起点 (全多regime窗)
    ap.add_argument("--mode", choices=["single", "composite"], default="single")
    ap.add_argument("--factors", default=",".join(ALL_FACTORS), help="逗号分隔因子子集")
    ap.add_argument("--weights", default="", help="composite 权重 (逗号, 默认等权)")
    ap.add_argument("--auto-sign", action="store_true", help="train 窗 IC 符号定向 composite")
    ap.add_argument("--train-end", default="2023-12-31")  # rule-compliance: ok evidence=pre-reg train/holdout 切点 (定向只用train窗)
    ap.add_argument("--top-k", type=int, default=TOP_K)
    ap.add_argument("--rebal", type=int, default=REBALANCE_DAYS)
    ap.add_argument("--sizing", choices=["equal", "rank", "inverse_vol"], default="equal")
    ap.add_argument("--regime", action="store_true", help="加 regime/timing 门")
    args = ap.parse_args(argv)

    factors = [f.strip() for f in args.factors.split(",") if f.strip()]
    for f in factors:
        if f not in ALL_FACTORS:
            print(f"[ERR] 未知因子 {f}; 可选 {ALL_FACTORS}"); return 2

    print(f"[load] K线(market price_kline_qfq_tushare) {args.start}+ ...", flush=True)
    by_code = load_kline(args.start, None, 0)
    bars_by_code, fwd_by_code, calendar = build_bars_fwd(by_code)
    print(f"[load] {len(bars_by_code)} 股 in_universe, calendar {calendar[0]}~{calendar[-1]} ({len(calendar)} 日)", flush=True)

    # 事前切分纪律门 (embargo>=horizon + 时间切): FAIL -> BLOCK 不算 IC (泄漏死)
    split = check_split_discipline(label_horizon_days=HORIZON, embargo_days=EMBARGO, split_mode="time")
    if split.get("verdict") == "FAIL":
        print(f"[BLOCK] 切分纪律门 FAIL: {split['problems']}"); return 3
    print(f"[gate] 切分纪律 clean (embargo={EMBARGO}>=horizon={HORIZON}, 时间切)", flush=True)

    regime_ok = None
    tag = "no_regime"
    if args.regime:
        regime_ok = build_regime_ok(bars_by_code, calendar, ma=REGIME_MA)
        on = sum(1 for d in calendar if regime_ok[d])
        print(f"[regime] 市场代理 MA{REGIME_MA}: {on}/{len(calendar)} 日 risk-on", flush=True)
        tag = "regime"

    gate = {"clean": True, "check": "split_discipline+panel_PIT",
            "note": "因子 L2 建库已 PIT; composite=同日截面 z-score 加权无跨期 lookahead"}

    if args.mode == "single":
        # 逐因子单独含成本基线 (sign+1, 长高因子篮; IC 符号自报方向) — 最诚实无拟合首测
        for f in factors:
            sig = load_signal([f], [1.0], [1.0], args.start)
            print(f"\n########## 单因子 {f} (sign+1 长高因子篮, {len(sig)} 股) ##########", flush=True)
            evaluate_signal(
                signal_by_code=sig, bars_by_code=bars_by_code, calendar=calendar, fwd_by_code=fwd_by_code,
                signal_name=f"mf_single_{f}_{tag}", run_id=f"mf_explore_single_{f}_{tag}_20260615",
                family="multifactor_explore", snapshot=f"L2_panel@{args.start}",
                out_path=REPO / "analysis" / f"mf_single_{f}_{tag}_20260615.json",
                consumer_id=f"L2_panel|single_{f}", ic_baseline=BASELINE_IC, gate=gate,
                rebalance_days=args.rebal, top_k=args.top_k, sizing=args.sizing, embargo=EMBARGO,
                regime_ok=regime_ok, extra={"mode": "single", "factor": f, "sign": "+1(raw)"})
        return 0

    # composite: 子集 z-score 加权 signed 合成
    weights = ([float(w) for w in args.weights.split(",")] if args.weights
               else [1.0 / len(factors)] * len(factors))
    if len(weights) != len(factors):
        print(f"[ERR] weights {len(weights)} != factors {len(factors)}"); return 2
    signs = [1.0] * len(factors)
    if args.auto_sign:
        # train 窗每因子 IC 符号定向 (PIT: 只用 date<=train_end)
        for i, f in enumerate(factors):
            sig_f = load_signal([f], [1.0], [1.0], args.start)
            signs[i] = train_ic_sign(f, sig_f, fwd_by_code, args.train_end)
        print(f"[auto-sign] train(<= {args.train_end}) IC 符号: {dict(zip(factors, signs))}", flush=True)

    sig = load_signal(factors, weights, signs, args.start)
    fstr = "+".join(factors)
    print(f"\n########## composite [{fstr}] w={weights} sign={signs} ({len(sig)} 股) ##########", flush=True)
    evaluate_signal(
        signal_by_code=sig, bars_by_code=bars_by_code, calendar=calendar, fwd_by_code=fwd_by_code,
        signal_name=f"mf_composite_{tag}", run_id=f"mf_explore_composite_{tag}_20260615",
        family="multifactor_explore", snapshot=f"L2_panel@{args.start}",
        out_path=REPO / "analysis" / f"mf_composite_{tag}_20260615.json",
        consumer_id=f"L2_panel|composite_{fstr}", ic_baseline=BASELINE_IC, gate=gate,
        rebalance_days=args.rebal, top_k=args.top_k, sizing=args.sizing, embargo=EMBARGO,
        regime_ok=regime_ok, extra={"mode": "composite", "factors": factors, "weights": weights, "signs": signs})
    return 0


if __name__ == "__main__":
    sys.exit(main())
