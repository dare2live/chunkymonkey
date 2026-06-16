"""analyze_episode_richfactors — D3-富因子: 资金/筹码因子对主升浪的判别力 (2023+ 富因子窗)。

D3 量价轨迹判别弱 (~1.1x)。本脚本测**富因子** (资金 moneyflow_dc + order-size moneyflow + 筹码 cyq) 在买点 t
能否判别 TRUE 主升浪 —— 量价之外的信息是否更强。结果倒推: D1 赢家标签上看富因子区分力, 非信号正推。
仅 2023+ episode (富因子起点; moneyflow_dc~2023-10/cyq 2023-01)。全特征 <=t (trailing/as-of, PIT)。

资金: mfdc_net_rate_20 (东财主力净流入率20日均) / mfdc_elg_rate_20 (特大单买入率) / mf_main_net_20 (order-size 大+特大净额率20日均)
筹码: cyq_px_pctile (价在成本5-95分位位置) / cyq_winner_rate (获利盘%, C0口径caveat) / cyq_concentration (筹码分散度) / cyq_px_vs_avg (价/平均成本)
源: smartmoney.fact_rally_ground_truth + market.price_kline_qfq_tushare(close) + tushare_raw.{cyq_perf, moneyflow_dc, moneyflow}
用法: PYTHONPATH=backend .venv/bin/python backend/scripts/analyze_episode_richfactors.py
"""
from __future__ import annotations

import logging

import duckdb  # rule-compliance: ok evidence=只读 D3 富因子判别; manifest 路径; allowlist
import numpy as np
import pandas as pd

from services.database_manifest import get_database_manifest

log = logging.getLogger("analyze_episode_richfactors")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")


def main():
    mf = get_database_manifest()
    con = duckdb.connect(str(mf.path_for("tushare_raw")), read_only=True)  # rule-compliance: ok evidence=只读+ATTACH; manifest; allowlist
    con.execute(f"ATTACH '{mf.path_for('smartmoney')}' AS sm (READ_ONLY)")
    con.execute(f"ATTACH '{mf.path_for('market')}' AS mk (READ_ONLY)")
    log.info("join 2023+ episode + 资金(mfdc/mf)trailing + 筹码(cyq at t) + close ...")
    df = con.execute(
        """
        WITH ep AS (
          SELECT stock_code AS code6, event_date AS d8, is_true_rally
          FROM sm.fact_rally_ground_truth WHERE event_date >= '20230101'
        ),
        mfdc AS (  -- 东财个股资金流 trailing 20d (ts_code 6位)
          SELECT SUBSTR(ts_code,1,6) code6, trade_date d8,
                 AVG(net_amount_rate) OVER w AS mfdc_net_rate_20,
                 AVG(buy_elg_amount_rate) OVER w AS mfdc_elg_rate_20
          FROM raw_tushare_moneyflow_dc
          WINDOW w AS (PARTITION BY ts_code ORDER BY trade_date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW)
        ),
        mf AS (  -- order-size 主力(大+特大)净额率 trailing 20d
          SELECT SUBSTR(ts_code,1,6) code6, trade_date d8,
                 AVG((buy_lg_amount+buy_elg_amount-sell_lg_amount-sell_elg_amount)/
                     NULLIF(buy_sm_amount+buy_md_amount+buy_lg_amount+buy_elg_amount
                            +sell_sm_amount+sell_md_amount+sell_lg_amount+sell_elg_amount,0)) OVER w AS mf_main_net_20
          FROM raw_tushare_moneyflow
          WINDOW w AS (PARTITION BY ts_code ORDER BY trade_date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW)
        ),
        cyq AS (  -- 筹码 at t
          SELECT SUBSTR(ts_code,1,6) code6, trade_date d8, cost_5pct, cost_50pct, cost_95pct, weight_avg, winner_rate
          FROM raw_tushare_cyq_perf
        ),
        px AS (  -- 买点 close
          SELECT code code6, REPLACE(date,'-','') d8, close FROM mk.price_kline_qfq_tushare
        )
        SELECT ep.is_true_rally,
               mfdc.mfdc_net_rate_20, mfdc.mfdc_elg_rate_20, mf.mf_main_net_20,
               cyq.winner_rate AS cyq_winner_rate,
               (px.close - cyq.cost_5pct) / NULLIF(cyq.cost_95pct - cyq.cost_5pct, 0) AS cyq_px_pctile,
               (cyq.cost_95pct - cyq.cost_5pct) / NULLIF(cyq.cost_50pct, 0) AS cyq_concentration,
               px.close / NULLIF(cyq.weight_avg, 0) - 1 AS cyq_px_vs_avg
        FROM ep
        LEFT JOIN mfdc ON mfdc.code6=ep.code6 AND mfdc.d8=ep.d8
        LEFT JOIN mf   ON mf.code6=ep.code6   AND mf.d8=ep.d8
        LEFT JOIN cyq  ON cyq.code6=ep.code6  AND cyq.d8=ep.d8
        LEFT JOIN px   ON px.code6=ep.code6   AND px.d8=ep.d8
        """
    ).df()
    con.close()
    base = df["is_true_rally"].mean()
    log.info("2023+ episode: %s 行, base TRUE-rate=%.1f%%", f"{len(df):,}", 100 * base)

    feats = ["mfdc_net_rate_20", "mfdc_elg_rate_20", "mf_main_net_20",
             "cyq_winner_rate", "cyq_px_pctile", "cyq_concentration", "cyq_px_vs_avg"]
    print(f"\n基准 TRUE-rate = {base:.1%}\n=== 富因子判别力 (买点三分位 TRUE-rate lift; 跨度>0.2=比量价1.1x强) ===")
    rows = []
    for ft in feats:
        sub = df.dropna(subset=[ft])
        if len(sub) < 200 or sub[ft].nunique() < 10:
            print(f"  {ft:20} n={len(sub):,} 样本/方差不足跳过")
            continue
        try:
            sub = sub.assign(bk=pd.qcut(sub[ft], 3, labels=["低", "中", "高"], duplicates="drop"))
        except ValueError:
            continue
        tr = sub.groupby("bk", observed=True)["is_true_rally"].mean()
        lo_l, hi_l = tr.get("低", np.nan) / base, tr.get("高", np.nan) / base
        rows.append((ft, lo_l, hi_l, abs(hi_l - lo_l), len(sub)))
    for ft, lo_l, hi_l, sp, n in sorted(rows, key=lambda x: -x[3]):
        flag = " <<< 比量价强" if sp >= 0.25 else ""
        print(f"  {ft:20} 低分位lift={lo_l:.2f} 高分位lift={hi_l:.2f} 跨度={sp:.2f} (n={n:,}){flag}")
    print("\n注: cyq_winner_rate 有 C0 口径 caveat (registry冻结说未复权坐标疑); px_pctile/concentration 用成本分位(价位, qfq对齐)更稳。")


if __name__ == "__main__":
    main()
