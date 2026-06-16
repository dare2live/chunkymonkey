"""experiment_position_reversal — F1→北极星: 位置-反转信号含成本 execution-aware backtest, 按 vol-regime cell。

F1 诊断发现 range_pos→fwd10 全样本 RankIC -0.043 (反转), 且随 vol-regime 翻转 (中盘高波 -0.054 反转 → 大盘低波 +0.011 动量)。
本实验验证 §4.5 铁律: IC≠可交易利润。低位=买 (signal = -range_pos, 高 signal=低位=反转买入), 复用 phaseD_signal_eval 含成本
execution-aware backtest (T+1 open / 涨跌停 / 非对称成本 / 容量), 分 overall + (cap×vol) cell 看哪个 cell 含成本绝对收益 tradable。

读: feature_store.fact_segment_panel(range_pos) + tushare_raw.raw_tushare_daily_basic(circ_mv) + market.price_kline_qfq_tushare(bars+fwd)。
PIT: range_pos/cap/rv 全 <=t; signal 同日截面; 留档走 experiment_store。

用法: PYTHONPATH=backend .venv/bin/python backend/scripts/experiment_position_reversal.py [--start 2023-01-01]
"""
from __future__ import annotations

import argparse
import logging
from pathlib import Path

import duckdb  # rule-compliance: ok evidence=实验runner需fetchnumpy批量+ATTACH多库; manifest路径; duckdb_connect_policy allowlist
import numpy as np

from services.database_manifest import get_database_manifest
from services.phaseD_signal_eval import evaluate_signal

log = logging.getLogger("experiment_position_reversal")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")

REBALANCE_DAYS = 20  # rule-compliance: ok evidence=与 fwd20 horizon 对齐, phaseD 默认月频调仓
TOP_K = 20           # rule-compliance: ok evidence=phaseD 默认 top-K 篮; 与既有 reversal_20 实验同口径可比


def _load(start: str):
    mf = get_database_manifest()
    con = duckdb.connect(str(mf.path_for("market")), read_only=True)  # rule-compliance: ok evidence=只读批量+ATTACH; manifest路径; allowlist
    con.execute(f"ATTACH '{mf.path_for('feature_store')}' AS fs (READ_ONLY)")
    con.execute(f"ATTACH '{mf.path_for('tushare_raw')}' AS tr (READ_ONLY)")

    log.info("加载 bars + range_pos + cap + rv (start=%s)...", start)
    # bars + fwd20 + rv20
    k = con.execute(
        """
        WITH r AS (
          SELECT code, date, open, high, low, close, volume,
                 LN(close / NULLIF(LAG(close) OVER (PARTITION BY code ORDER BY date), 0)) AS logret
          FROM price_kline_qfq_tushare
        )
        SELECT code, date, open, high, low, close, volume,
               LEAD(close, 20) OVER (PARTITION BY code ORDER BY date) / close - 1.0 AS fwd20,
               STDDEV_SAMP(logret) OVER (PARTITION BY code ORDER BY date ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING) AS rv20
          FROM r
         WHERE date >= ?
         ORDER BY code, date
        """,
        [start],
    ).df()
    # range_pos
    p = con.execute(
        "SELECT stock_code AS code, date, range_pos FROM fs.fact_segment_panel WHERE date >= ? AND range_pos IS NOT NULL",
        [start],
    ).df()
    # circ_mv
    db = con.execute(
        """
        SELECT SUBSTR(ts_code,1,6) AS code,
               SUBSTR(trade_date,1,4)||'-'||SUBSTR(trade_date,5,2)||'-'||SUBSTR(trade_date,7,2) AS date,
               circ_mv
        FROM tr.raw_tushare_daily_basic WHERE trade_date >= ?
        """,
        [start.replace("-", "")],
    ).df()
    con.close()
    return k, p, db


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2023-01-01")  # rule-compliance: ok evidence=回测窗起点, 与 fact_feature_panel 2023+ 同口径可比, 可命令行覆盖
    ap.add_argument("--out-dir", default="analysis")
    args = ap.parse_args()

    k, p, db = main_load = _load(args.start)
    import pandas as pd

    # 合并: bars + range_pos + cap; 计算每日截面 cap 三分位 / vol 二分位
    m = k.merge(p, on=["code", "date"], how="inner").merge(db, on=["code", "date"], how="left")
    m = m.dropna(subset=["circ_mv", "rv20"])
    m["cap_t"] = m.groupby("date", observed=True)["circ_mv"].transform(
        lambda x: pd.qcut(x, 3, labels=["小", "中", "大"], duplicates="drop") if x.nunique() >= 3 else np.nan
    )
    m["vol_t"] = m.groupby("date", observed=True)["rv20"].transform(
        lambda x: pd.qcut(x, 2, labels=["低波", "高波"], duplicates="drop") if x.nunique() >= 2 else np.nan
    )
    m = m.dropna(subset=["cap_t", "vol_t"])
    calendar = sorted(m["date"].unique().tolist())
    log.info("合并后 %s 行, %s 交易日, %s 股", f"{len(m):,}", len(calendar), m["code"].nunique())

    # bars_by_code (全集, 所有 cell 共用) {code:{date:(o,h,l,c,v)}}
    bars_by_code: dict = {}
    for row in k.itertuples(index=False):
        bars_by_code.setdefault(row.code, {})[row.date] = (row.open, row.high, row.low, row.close, row.volume)
    # fwd_by_code (IC 快筛用) {code:{date:fwd20}}
    fwd_by_code: dict = {}
    for row in k.itertuples(index=False):
        if not (isinstance(row.fwd20, float) and np.isnan(row.fwd20)):
            fwd_by_code.setdefault(row.code, {})[row.date] = row.fwd20

    snapshot = f"segment_panel_{calendar[-1]}"
    scenarios = [
        ("overall", m),
        ("大盘低波", m[(m.cap_t == "大") & (m.vol_t == "低波")]),  # F1: 唯一动量(+0.011)cell, 大盘=可交易
        ("中盘高波", m[(m.cap_t == "中") & (m.vol_t == "高波")]),  # F1: 最强反转(-0.054), 测是否=崩盘cohort untradable
        ("小盘低波", m[(m.cap_t == "小") & (m.vol_t == "低波")]),  # F1: 反转≈0, 对照
    ]
    results = {}
    for name, sub in scenarios:
        # signal = -range_pos (高 signal = 低位 = 反转买入); 限定该 cell 的 (code,date)
        signal_by_code: dict = {}
        for row in sub.itertuples(index=False):
            signal_by_code.setdefault(row.code, {})[row.date] = -float(row.range_pos)
        n_obs = sum(len(v) for v in signal_by_code.values())
        log.info("=== 场景 %s: %s 股, %s (code,date) 信号点 ===", name, len(signal_by_code), f"{n_obs:,}")
        out = evaluate_signal(
            signal_by_code=signal_by_code, bars_by_code=bars_by_code, calendar=calendar,
            fwd_by_code=fwd_by_code, signal_name=f"position_reversal_{name}",
            run_id=f"posrev_{name}_{calendar[-1]}", family="position_reversal",
            snapshot=snapshot, out_path=Path(args.out_dir) / f"position_reversal_{name}.json",
            consumer_id=f"position_reversal_{name}", rebalance_days=REBALANCE_DAYS, top_k=TOP_K,
        )
        results[name] = (out.get("verdict"), out.get("tradability", {}).get("verdict"),
                         out.get("metrics", {}).get("annual_return"))

    print("\n" + "=" * 60)
    print("位置-反转 各 cell 含成本裁定汇总 (北极星: 含成本绝对收益)")
    print("=" * 60)
    for name, (v, trad, ann) in results.items():
        ann_s = f"{ann:+.1%}" if isinstance(ann, (int, float)) else "NA"
        print(f"  {name:8} 含成本年化={ann_s:>9}  R1={trad}  verdict={v}")


if __name__ == "__main__":
    main()
