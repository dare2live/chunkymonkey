"""experiment_factor_in_cell — 通用 cell 条件化因子评估器 (F1→F2 方法论核心检验)。

检验方法论核心主张: "无条件截面撞 R1 墙的因子, 在 (市值×波动) cell 内条件化后含成本能否赚钱"。
对一个因子: train 窗 IC 定 long 方向 (无 look-ahead) → 全期 overall + 各 (cap×vol) cell 跑含成本 execution-aware backtest (复用 phaseD_signal_eval)。

因子源 (live 可得, 不依赖被 reset 清掉的 fact_feature_panel):
  turnover  <- daily_basic.turnover_rate (换手, 方法论早层)
  range_pos <- fact_segment_panel (位置, 已知反转但 untradable — 作 sanity)

PIT: 因子同日截面 <=t; cap/vol regime 当日截面; fwd/backtest 走 phaseD (T+1 open/涨跌停/成本)。
用法: PYTHONPATH=backend .venv/bin/python backend/scripts/experiment_factor_in_cell.py --factor turnover [--start 2023-01-01]
"""
from __future__ import annotations

import argparse
import logging
from pathlib import Path

import duckdb  # rule-compliance: ok evidence=实验runner需.df()批量+ATTACH多库; manifest路径; allowlist
import numpy as np
import pandas as pd

from services.database_manifest import get_database_manifest
from services.phaseD_signal_eval import evaluate_signal

log = logging.getLogger("experiment_factor_in_cell")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")

REBALANCE_DAYS = 20  # rule-compliance: ok evidence=phaseD 默认月频, 与 fwd20 对齐
TOP_K = 20           # rule-compliance: ok evidence=phaseD 默认 top-K 篮
TRAIN_END = "2024-12-31"  # rule-compliance: ok evidence=sign 仅用 train 窗 IC 定向防 look-ahead, 与方法论 train<=2025-06 一致


def _load(con, factor: str, start: str) -> pd.DataFrame:
    # bars + fwd20 + rv20 (全因子共用)
    base = con.execute(
        """
        WITH r AS (
          SELECT code, date, open, high, low, close, volume,
                 LN(close / NULLIF(LAG(close) OVER (PARTITION BY code ORDER BY date), 0)) AS logret
          FROM price_kline_qfq_tushare WHERE date >= ?
        )
        SELECT code, date, open, high, low, close, volume,
               LEAD(close,20) OVER (PARTITION BY code ORDER BY date)/close - 1.0 AS fwd20,
               STDDEV_SAMP(logret) OVER (PARTITION BY code ORDER BY date ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING) AS rv20
        FROM r
        """,
        [start],
    ).df()
    db = con.execute(
        """
        SELECT SUBSTR(ts_code,1,6) code,
               SUBSTR(trade_date,1,4)||'-'||SUBSTR(trade_date,5,2)||'-'||SUBSTR(trade_date,7,2) date,
               circ_mv, turnover_rate
        FROM tr.raw_tushare_daily_basic WHERE trade_date >= ?
        """,
        [start.replace("-", "")],
    ).df()
    m = base.merge(db, on=["code", "date"], how="left")
    if factor == "turnover":
        m["factor"] = m["turnover_rate"]
    elif factor == "range_pos":
        p = con.execute("SELECT stock_code code, date, range_pos FROM fs.fact_segment_panel WHERE date >= ?", [start]).df()
        m = m.merge(p, on=["code", "date"], how="left")
        m["factor"] = m["range_pos"]
    elif factor == "moneyflow":
        # 资金因子 = 主力(大单+特大单)净流入率 trailing-20d 均 (smart money 持续吸筹); 盘后t-1, PIT (signal@t, 入场t+1)
        mf = con.execute(
            """
            WITH r AS (
              SELECT SUBSTR(ts_code,1,6) code,
                     SUBSTR(trade_date,1,4)||'-'||SUBSTR(trade_date,5,2)||'-'||SUBSTR(trade_date,7,2) date,
                     (buy_lg_amount+buy_elg_amount-sell_lg_amount-sell_elg_amount) /
                       NULLIF(buy_sm_amount+buy_md_amount+buy_lg_amount+buy_elg_amount
                              +sell_sm_amount+sell_md_amount+sell_lg_amount+sell_elg_amount,0) AS main_ratio
              FROM tr.raw_tushare_moneyflow WHERE trade_date >= ?
            )
            SELECT code, date,
                   AVG(main_ratio) OVER (PARTITION BY code ORDER BY date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) AS factor
            FROM r
            """,
            [start.replace("-", "")],
        ).df()
        m = m.merge(mf, on=["code", "date"], how="left")
    else:
        raise SystemExit(f"未知 factor: {factor}")
    return m.dropna(subset=["factor", "circ_mv", "rv20", "fwd20"])


def _ic_sign(sub: pd.DataFrame) -> float:
    """train 窗每日截面 spearman(factor, fwd20) 均值的符号 (定 long 方向, 无 look-ahead)。"""
    tr = sub[sub.date <= TRAIN_END]
    ics = tr.groupby("date", observed=True).apply(
        lambda g: g["factor"].corr(g["fwd20"], method="spearman") if len(g) >= 20 else np.nan,
        include_groups=False,
    ).dropna()
    return float(np.sign(ics.mean())) if len(ics) and ics.mean() != 0 else 1.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--factor", default="turnover")
    ap.add_argument("--start", default="2023-01-01")  # rule-compliance: ok evidence=回测窗起点, 可命令行覆盖
    ap.add_argument("--out-dir", default="analysis")
    args = ap.parse_args()

    mf = get_database_manifest()
    con = duckdb.connect(str(mf.path_for("market")), read_only=True)  # rule-compliance: ok evidence=只读批量+ATTACH; manifest; allowlist
    con.execute(f"ATTACH '{mf.path_for('feature_store')}' AS fs (READ_ONLY)")
    con.execute(f"ATTACH '{mf.path_for('tushare_raw')}' AS tr (READ_ONLY)")
    log.info("加载 factor=%s start=%s ...", args.factor, args.start)
    m = _load(con, args.factor, args.start)
    con.close()

    m["cap_t"] = m.groupby("date", observed=True)["circ_mv"].transform(
        lambda x: pd.qcut(x, 3, labels=["小", "中", "大"], duplicates="drop") if x.nunique() >= 3 else np.nan)
    m["vol_t"] = m.groupby("date", observed=True)["rv20"].transform(
        lambda x: pd.qcut(x, 2, labels=["低波", "高波"], duplicates="drop") if x.nunique() >= 2 else np.nan)
    m = m.dropna(subset=["cap_t", "vol_t"])

    sign = _ic_sign(m)
    log.info("train(<=%s) IC 定向: sign=%+.0f (signal = sign*%s, 高=买)", TRAIN_END, sign, args.factor)
    m["signal"] = sign * m["factor"]
    calendar = sorted(m["date"].unique().tolist())

    # 全因子共用 bars/fwd
    bars_by_code, fwd_by_code = {}, {}
    for row in m.itertuples(index=False):
        bars_by_code.setdefault(row.code, {})[row.date] = (row.open, row.high, row.low, row.close, row.volume)
        fwd_by_code.setdefault(row.code, {})[row.date] = row.fwd20

    scenarios = [("overall", m)] + [
        (f"{cap}{vol}", m[(m.cap_t == cap) & (m.vol_t == vol)])
        for cap in ["大", "小"] for vol in ["低波", "高波"]
    ]
    results = {}
    for name, sub in scenarios:
        sig = {}
        for row in sub.itertuples(index=False):
            sig.setdefault(row.code, {})[row.date] = float(row.signal)
        log.info("=== %s: %s 股 ===", name, len(sig))
        out = evaluate_signal(
            signal_by_code=sig, bars_by_code=bars_by_code, calendar=calendar, fwd_by_code=fwd_by_code,
            signal_name=f"{args.factor}_{name}", run_id=f"fic_{args.factor}_{name}_{calendar[-1]}",
            family=f"factor_in_cell_{args.factor}", snapshot=f"cell_{calendar[-1]}",
            out_path=Path(args.out_dir) / f"factor_in_cell_{args.factor}_{name}.json",
            consumer_id=f"factor_in_cell_{args.factor}_{name}", rebalance_days=REBALANCE_DAYS, top_k=TOP_K)
        results[name] = (out.get("metrics", {}).get("annual_return"),
                         out.get("tradability", {}).get("verdict"), out.get("verdict"))

    print("\n" + "=" * 60)
    print(f"因子 {args.factor} (sign={sign:+.0f}) 各 cell 含成本裁定")
    print("=" * 60)
    for name, (ann, trad, v) in results.items():
        ann_s = f"{ann:+.1%}" if isinstance(ann, (int, float)) else "NA"
        print(f"  {name:8} 含成本年化={ann_s:>9}  R1={trad}  verdict={v}")


if __name__ == "__main__":
    main()
