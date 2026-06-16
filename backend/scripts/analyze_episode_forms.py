"""analyze_episode_forms — D2: 给主升浪赢家 episode 的买点打形态标签+分层 (监督式 episode-first 第2步)。

D1 已落 fact_rally_ground_truth (43k 突破事件, 4345 TRUE 主升浪, 买点=event_date=突破日 t)。
D2 = 用 fact_segment_panel 给每个 episode 的**买点 t** 打形态(stage/range_pos/MACD零轴/board)+分层(市值/波动),
看 **TRUE 主升浪买点聚在哪些 (形态×分层)** —— TRUE-rate lift 高的 cell = 突破易成真主升浪的形态/分层。
这是"结果倒推": 先有赢家(D1 标签), 看它们的形态共性, 不是造信号看 IC。

读: smartmoney.fact_rally_ground_truth + feature_store.fact_segment_panel + tushare_raw.daily_basic + market K线(rv)。
PIT: 形态/cap/vol 全 buy 点 t 当日 (<=t); is_true_rally 是 D1 标签(目标, t+1..t+180 后验, 合法)。
用法: PYTHONPATH=backend .venv/bin/python backend/scripts/analyze_episode_forms.py
"""
from __future__ import annotations

import logging

import duckdb  # rule-compliance: ok evidence=只读 D2 profiling, ATTACH 多库; manifest 路径; allowlist
import numpy as np
import pandas as pd

from services.database_manifest import get_database_manifest

log = logging.getLogger("analyze_episode_forms")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")


def _lift_table(df: pd.DataFrame, dim: str, base: float) -> pd.DataFrame:
    g = df.groupby(dim, observed=True).agg(
        n=("is_true_rally", "size"),
        true_n=("is_true_rally", "sum"),
        median_gain=("gain_to_peak_pct", lambda x: x[df.loc[x.index, "is_true_rally"]].median() if df.loc[x.index, "is_true_rally"].any() else np.nan),
    )
    g["true_rate"] = g["true_n"] / g["n"]
    g["lift"] = g["true_rate"] / base
    return g.sort_values("lift", ascending=False)


def main():
    mf = get_database_manifest()
    con = duckdb.connect(str(mf.path_for("market")), read_only=True)  # rule-compliance: ok evidence=只读+ATTACH; manifest; allowlist
    con.execute(f"ATTACH '{mf.path_for('smartmoney')}' AS sm (READ_ONLY)")
    con.execute(f"ATTACH '{mf.path_for('feature_store')}' AS fs (READ_ONLY)")
    con.execute(f"ATTACH '{mf.path_for('tushare_raw')}' AS tr (READ_ONLY)")

    log.info("join episode ground-truth + 形态面板 + 市值 + 波动 ...")
    ep = con.execute(
        """
        WITH gt AS (
          SELECT stock_code AS code,
                 SUBSTR(event_date,1,4)||'-'||SUBSTR(event_date,5,2)||'-'||SUBSTR(event_date,7,2) AS date,
                 is_true_rally, gain_to_peak_pct, fwd_window_len
          FROM sm.fact_rally_ground_truth
        ),
        lr AS (
          SELECT code, date, LN(close/NULLIF(LAG(close) OVER (PARTITION BY code ORDER BY date),0)) AS logret
          FROM price_kline_qfq_tushare
        ),
        rv AS (
          SELECT code, date,
                 STDDEV_SAMP(logret) OVER (PARTITION BY code ORDER BY date ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING) AS rv20
          FROM lr
        ),
        db AS (
          SELECT SUBSTR(ts_code,1,6) code,
                 SUBSTR(trade_date,1,4)||'-'||SUBSTR(trade_date,5,2)||'-'||SUBSTR(trade_date,7,2) date, circ_mv
          FROM tr.raw_tushare_daily_basic
        )
        SELECT gt.code, gt.date, gt.is_true_rally, gt.gain_to_peak_pct, gt.fwd_window_len,
               seg.stage, seg.range_pos, seg.macd_above_zero, seg.board,
               db.circ_mv, rv.rv20
        FROM gt
        JOIN fs.fact_segment_panel seg ON seg.stock_code=gt.code AND seg.date=gt.date
        LEFT JOIN db ON db.code=gt.code AND db.date=gt.date
        LEFT JOIN rv ON rv.code=gt.code AND rv.date=gt.date
        """
    ).df()
    con.close()
    log.info("episode×形态 join: %s 行 (segment 面板 2020+ 覆盖)", f"{len(ep):,}")

    base = ep["is_true_rally"].mean()
    print(f"\n基准 TRUE-rate (突破→真主升浪) = {base:.1%}  (n={len(ep):,})")

    # 位置桶 + 市值/波动 per-day 三/二分位
    ep["pos_bucket"] = pd.cut(ep["range_pos"], [-9, 0.33, 0.66, 9], labels=["低位", "中位", "高位"])
    ep["cap_t"] = ep.groupby("date", observed=True)["circ_mv"].transform(
        lambda x: pd.qcut(x, 3, labels=["小", "中", "大"], duplicates="drop") if x.nunique() >= 3 else np.nan)
    ep["vol_t"] = ep.groupby("date", observed=True)["rv20"].transform(
        lambda x: pd.qcut(x, 2, labels=["低波", "高波"], duplicates="drop") if x.nunique() >= 2 else np.nan)

    for dim, label in [("stage", "形态stage"), ("pos_bucket", "位置"), ("macd_above_zero", "MACD零轴上"),
                       ("board", "板块"), ("cap_t", "市值"), ("vol_t", "波动")]:
        sub = ep.dropna(subset=[dim])
        print(f"\n=== TRUE-rate × {label} (lift=相对基准{base:.1%}; >1=突破更易成主升浪) ===")
        t = _lift_table(sub, dim, base)
        for idx, row in t.iterrows():
            print(f"  {str(idx):8} n={int(row['n']):>6,} TRUE率={row['true_rate']:.1%} lift={row['lift']:.2f} 中位涨幅={row['median_gain'] if not np.isnan(row['median_gain']) else 0:.0f}%")

    # 关键 2-way: 位置×波动 (F1 发现 vol-regime 重要)
    print(f"\n=== TRUE-rate × (位置×波动) 关键组合 ===")
    cc = ep.dropna(subset=["pos_bucket", "vol_t"])
    t2 = cc.groupby(["pos_bucket", "vol_t"], observed=True).agg(n=("is_true_rally","size"), true_n=("is_true_rally","sum"))
    t2["true_rate"] = t2["true_n"]/t2["n"]; t2["lift"]=t2["true_rate"]/base
    for idx,row in t2.sort_values("lift",ascending=False).iterrows():
        print(f"  {str(idx[0])}×{str(idx[1])}: n={int(row['n']):,} TRUE率={row['true_rate']:.1%} lift={row['lift']:.2f}")


if __name__ == "__main__":
    main()
