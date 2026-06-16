"""measure_form_separation — F1 决定性诊断: 形态/位置轴的前瞻分离度, 全样本 vs 条件化。

验证 F0 的 RankIC≈0 到底是:
  (a) "没条件化估计"的方法假象 (Cooper 2004 JF: 同因子在不同 state 符号相反, 全样本平均抵消成 0) — 则条件化后 IC 会回来 → F1 重设计有救; 还是
  (b) 形态在 A股真无前瞻信息 — 则条件化也救不回 → 该砍形态轴换波动率 regime。

度量: range_pos(位置, George-Hwang 学术轴) → fwd10 的每日截面 Spearman RankIC, 分三档口径:
  1. 全样本 (基线, F0 测得 ≈0.008)
  2. 市值中性 (per 日×市值三分位, 剥离市值 beta — 调研#1 头号嫌疑)
  3. 市值×波动regime (per 日×市值三分位×波动二分位, 双重条件化)

源: feature_store.fact_segment_panel + tushare_raw.raw_tushare_daily_basic(circ_mv) + market.price_kline_qfq_tushare(fwd+vol)。
PIT: fwd10 仅评估不入特征; range_pos/circ_mv/rv20 全 <=t (rv20 用 [t-20,t-1])。

用法: PYTHONPATH=backend .venv/bin/python backend/scripts/measure_form_separation.py
"""
from __future__ import annotations

import logging

import duckdb  # DB-boundary: 只读诊断脚本, 走 ATTACH 多库 read_only, 无写
import numpy as np
import pandas as pd

from services.database_manifest import get_database_manifest

log = logging.getLogger("measure_form_separation")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")


def _daily_ic(df: pd.DataFrame, group_cols: list[str]) -> dict:
    """per (date[, cell]) 截面 Spearman(range_pos, fwd10), 再跨组平均。返回 mean_ic/ir/pos_rate/n_groups。"""
    def _sp(g: pd.DataFrame) -> float:
        if len(g) < 20:
            return np.nan
        return g["range_pos"].corr(g["fwd10"], method="spearman")

    ics = df.groupby(group_cols, observed=True).apply(_sp, include_groups=False).dropna()
    if len(ics) == 0:
        return {"mean_ic": np.nan, "ir": np.nan, "pos_rate": np.nan, "n_groups": 0}
    return {
        "mean_ic": float(ics.mean()),
        "ir": float(ics.mean() / ics.std()) if ics.std() > 0 else np.nan,
        "pos_rate": float((ics > 0).mean()),
        "n_groups": int(len(ics)),
    }


def main():
    mf = get_database_manifest()  # manifest 路径, 不硬编码 .duckdb 字面量
    con = duckdb.connect(str(mf.path_for("market")), read_only=True)  # rule-compliance: ok evidence=只读诊断需.df()+ATTACH多库; manifest路径; duckdb_connect_policy allowlist
    con.execute(f"ATTACH '{mf.path_for('feature_store')}' AS fs (READ_ONLY)")
    con.execute(f"ATTACH '{mf.path_for('tushare_raw')}' AS tr (READ_ONLY)")

    log.info("join panel + kline(fwd10,rv20) + daily_basic(circ_mv)...")
    df = con.execute(
        """
        WITH r AS (
          SELECT code, date, close,
                 LN(close / NULLIF(LAG(close) OVER (PARTITION BY code ORDER BY date), 0)) AS logret
          FROM price_kline_qfq_tushare
        ),
        k AS (
          SELECT code, date, close,
                 LEAD(close, 10) OVER (PARTITION BY code ORDER BY date) / close - 1.0 AS fwd10,
                 STDDEV_SAMP(logret) OVER (PARTITION BY code ORDER BY date ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING) AS rv20
          FROM r
        ),
        db AS (
          SELECT SUBSTR(ts_code, 1, 6) AS code,
                 SUBSTR(trade_date,1,4)||'-'||SUBSTR(trade_date,5,2)||'-'||SUBSTR(trade_date,7,2) AS date,
                 circ_mv
          FROM tr.raw_tushare_daily_basic
        )
        SELECT p.date, p.stock_code AS code, p.stage, p.range_pos,
               k.fwd10, k.rv20, db.circ_mv
        FROM fs.fact_segment_panel p
        JOIN k ON k.code = p.stock_code AND k.date = p.date
        LEFT JOIN db ON db.code = p.stock_code AND db.date = p.date
        WHERE k.fwd10 IS NOT NULL AND p.range_pos IS NOT NULL
        """
    ).df()
    con.close()
    log.info("行数=%s, circ_mv 缺失=%s (%.1f%%)", f"{len(df):,}",
             f"{df['circ_mv'].isna().sum():,}", 100 * df['circ_mv'].isna().mean())

    # 全样本基线
    full = _daily_ic(df, ["date"])

    # 市值中性: per 日 circ_mv 三分位
    d = df.dropna(subset=["circ_mv", "rv20"]).copy()
    d["cap_t"] = d.groupby("date", observed=True)["circ_mv"].transform(
        lambda x: pd.qcut(x, 3, labels=["小", "中", "大"], duplicates="drop") if x.nunique() >= 3 else np.nan
    )
    d["vol_t"] = d.groupby("date", observed=True)["rv20"].transform(
        lambda x: pd.qcut(x, 2, labels=["低波", "高波"], duplicates="drop") if x.nunique() >= 2 else np.nan
    )
    d = d.dropna(subset=["cap_t", "vol_t"])

    cap_neutral = _daily_ic(d, ["date", "cap_t"])
    cap_vol = _daily_ic(d, ["date", "cap_t", "vol_t"])

    # 分 cell 看哪个 cell 的位置轴最有信号 (市值×波动 6 cell)
    print("\n" + "=" * 72)
    print("F1 决定性诊断: range_pos(位置) → fwd10 截面 RankIC")
    print("=" * 72)
    print(f"{'口径':28}{'mean_IC':>10}{'IR':>8}{'>0占比':>9}{'组数':>9}")
    for name, r in [("① 全样本(基线)", full), ("② 市值中性(per日×市值3)", cap_neutral), ("③ 市值×波动(per日×3×2)", cap_vol)]:
        print(f"{name:28}{r['mean_ic']:>10.4f}{r['ir']:>8.2f}{r['pos_rate']:>9.1%}{r['n_groups']:>9,}")

    print("\n--- 各 (市值×波动) cell 的位置轴 RankIC (找哪个 cell 有信号) ---")
    cells = []
    for (cap, vol), g in d.groupby(["cap_t", "vol_t"], observed=True):
        r = _daily_ic(g, ["date"])
        cells.append((f"{cap}盘×{vol}", r["mean_ic"], r["ir"], r["pos_rate"], len(g)))
    for c in sorted(cells, key=lambda x: -abs(x[1]) if not np.isnan(x[1]) else 0):
        print(f"  {c[0]:14} IC={c[1]:+.4f} IR={c[2]:+.2f} >0={c[3]:.0%} n={c[4]:,}")

    print("\n--- 裁定 (三问, 不靠单一阈值拍板) ---")
    base_ic = full["mean_ic"]
    base_mag = abs(base_ic)
    cell_ics = [c[1] for c in cells if not np.isnan(c[1])]
    sign_spread = (max(cell_ics) - min(cell_ics)) if cell_ics else 0.0
    sign_flip = (max(cell_ics) > 0.005 and min(cell_ics) < -0.005) if cell_ics else False
    # 干净参考: §4.2 实测干净 PIT RankIC 0.011-0.020; |IC|>=0.02 视为有信号(注意符号)
    CLEAN_REF = 0.02
    print(f"  Q1 位置轴是信号吗? 全样本 IC={base_ic:+.4f} ({'反转' if base_ic<0 else '动量'}方向) — "
          f"{'是, |IC|≥0.02 超干净参考' if base_mag>=CLEAN_REF else '弱, |IC|<0.02'}")
    print(f"  Q2 regime 让它翻转吗? cell IC 跨度={sign_spread:.4f}, 符号翻转={'是' if sign_flip else '否'} — "
          f"{'条件化揭示 regime 依赖, 该按 regime 切 cell' if sign_flip or sign_spread>=0.03 else '各 cell 同向'}")
    print(f"  Q3 异常高警报? |IC|={base_mag:.4f} vs §4.2 红线 0.3 — {'无 leakage 嫌疑' if base_mag<0.3 else '⚠ 查 PIT'}")
    print("  注: IC≠可交易利润 (§4.5); 反转信号买低位=可能接刀, 须含成本 OOS backtest 才能转正。")


if __name__ == "__main__":
    main()
