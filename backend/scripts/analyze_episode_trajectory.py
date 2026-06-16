"""analyze_episode_trajectory — D3: 主升浪 episode 买点前多窗轨迹特征的判别力 (用户 2026-06-16)。

用户洞察: 光看买点 t 快照不够 (D2 形态 lift 仅 1.16), 要看 t 之前 1周/1月/3月/6月轨迹。
"放量回落只是举例, 要广泛探索" → 本脚本广扫买点前各类量价现象 (~25 多窗特征), 测哪些判别 TRUE 主升浪。
结果倒推: 在 D1 赢家标签 (is_true_rally) 上看特征区分力, 非造信号看 IC。全特征 <=t (PIT 干净)。

源: smartmoney.fact_rally_ground_truth (D1 episode) + market.price_kline_qfq_tushare。
用法: PYTHONPATH=backend .venv/bin/python backend/scripts/analyze_episode_trajectory.py
"""
from __future__ import annotations

import logging

import duckdb  # rule-compliance: ok evidence=只读 D3 特征判别; manifest 路径; allowlist
import numpy as np
import pandas as pd

from services.database_manifest import get_database_manifest

log = logging.getLogger("analyze_episode_trajectory")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")


def _features(g: pd.DataFrame) -> pd.DataFrame:
    """单股按日算多窗回看特征 (全部 rolling 至 t, <=t, PIT 干净)。g 含 code 列, 按 date 升序。"""
    c = pd.Series(g["close"].to_numpy(float))
    v = pd.Series(g["volume"].to_numpy(float))
    h = pd.Series(g["high"].to_numpy(float))
    lo = pd.Series(g["low"].to_numpy(float))
    lr = np.log(c / c.shift(1))
    f = {}
    # ── 量能 (放量/缩量/趋势/配合) ──
    f["vol_ratio_5"] = v / v.rolling(5).mean()
    f["vol_ratio_20"] = v / v.rolling(20).mean()
    f["vol_ratio_60"] = v / v.rolling(60).mean()
    f["vol_dryup_20_60"] = v.rolling(20).mean() / v.rolling(60).mean()        # <1 缩量(吸筹)
    f["max_vol_spike_60"] = (v / v.rolling(60).median()).rolling(60).max()    # 60日内最大单日放量倍数
    f["max_vol_spike_120"] = (v / v.rolling(120).median()).rolling(120).max()
    f["vol_trend_60"] = v.rolling(20).mean() / v.shift(40).rolling(20).mean() # 近20日量 vs 40日前20日量
    f["vol_price_confirm"] = lr.rolling(20).corr(v.pct_change())             # 量价配合 (涨随放量为正)
    # ── 价格涨幅轨迹 (run-up profile) ──
    f["ret_5"] = c / c.shift(5) - 1
    f["ret_20"] = c / c.shift(20) - 1
    f["ret_60"] = c / c.shift(60) - 1
    f["ret_120"] = c / c.shift(120) - 1
    f["ret_250"] = c / c.shift(250) - 1
    # ── 回调 / 底部 ──
    f["pullback_20"] = (c / c.rolling(20).max() - 1).rolling(20).min()       # 20日内最深回落
    f["pullback_60"] = (c / c.rolling(60).max() - 1).rolling(60).min()
    f["recover_from_low_60"] = c / c.rolling(60).min() - 1                    # 距60日低反弹幅度
    # ── 位置 / 极值 ──
    f["dist_high_120"] = c / c.rolling(120).max() - 1                        # 距120日高 (0=新高)
    f["dist_high_250"] = c / c.rolling(250).max() - 1
    f["range_pos_250"] = (c - c.rolling(250).min()) / (c.rolling(250).max() - c.rolling(250).min())
    # ── 横盘整理 / 波动收缩 (VCP) ──
    f["range_tight_20"] = (h.rolling(20).max() - lo.rolling(20).min()) / c
    f["range_tight_60"] = (h.rolling(60).max() - lo.rolling(60).min()) / c
    f["vol_contract_20_60"] = lr.rolling(20).std() / lr.rolling(60).std()    # <1 波动收缩
    f["rv_20"] = lr.rolling(20).std()
    f["prior_base_tight"] = ((h.rolling(60).max() - lo.rolling(60).min()) / c).shift(20)  # 突破前60日整理紧度
    # ── 趋势 / 均线 ──
    ma5, ma20, ma60 = c.rolling(5).mean(), c.rolling(20).mean(), c.rolling(60).mean()
    f["ma_align"] = ((ma5 > ma20).astype(float) + (ma20 > ma60).astype(float))  # 0-2 多头排列度
    f["dist_ma60"] = c / ma60 - 1
    f["ma60_slope"] = ma60 / ma60.shift(20) - 1
    # ── 形态 setup (布尔, 用户举例的放量回落 + 缩量回踩) ──
    spike_mid = (v / v.rolling(60).median()).shift(20).rolling(40).max()
    f["spike_then_pullback"] = ((spike_mid > 2.0) & (c / c.shift(20) - 1 < 0) & (f["vol_dryup_20_60"] < 1.0)).astype(float)
    f["lowvol_pullback"] = ((c / c.shift(20) - 1 < 0) & (f["vol_dryup_20_60"] < 0.8)).astype(float)  # 缩量回踩(健康)
    df = pd.DataFrame(f)
    df["code"] = g["code"].iloc[0]
    df["date"] = g["date"].to_numpy()
    return df


def main():
    mf = get_database_manifest()
    con = duckdb.connect(str(mf.path_for("market")), read_only=True)  # rule-compliance: ok evidence=只读+ATTACH; manifest; allowlist
    con.execute(f"ATTACH '{mf.path_for('smartmoney')}' AS sm (READ_ONLY)")
    log.info("加载 K线 (2018+, 给6月回看预热) + episode 标签 ...")
    k = con.execute("SELECT code, date, high, low, close, volume FROM price_kline_qfq_tushare WHERE date >= '2018-06-01' ORDER BY code, date").df()  # rule-compliance: ok evidence=回看窗预热起点(6月回看需 episode 最早2019-04 前 ~250 交易日), 非钉死规避bug
    ep = con.execute(
        "SELECT stock_code code, SUBSTR(event_date,1,4)||'-'||SUBSTR(event_date,5,2)||'-'||SUBSTR(event_date,7,2) date, "
        "is_true_rally FROM sm.fact_rally_ground_truth").df()
    con.close()

    log.info("按股算 ~25 多窗轨迹特征 (%s 股, groupby 迭代避 include_groups bug) ...", k["code"].nunique())
    parts = [_features(g) for _, g in k.groupby("code", sort=False)]
    feats = pd.concat(parts, ignore_index=True)
    m = ep.merge(feats, on=["code", "date"], how="inner")
    log.info("episode×轨迹特征 join: %s 行", f"{len(m):,}")

    base = m["is_true_rally"].mean()
    print(f"\n基准 TRUE-rate (突破→真主升浪) = {base:.1%} (n={len(m):,})\n")
    bool_feats = ["spike_then_pullback", "lowvol_pullback", "ma_align"]
    num_feats = [c for c in feats.columns if c not in ("code", "date") and c not in bool_feats]

    print("=== 数值特征判别力广扫 (按特征三分位 TRUE-rate lift; 跨度大=判别强; 排序) ===")
    rows = []
    for ft in num_feats:
        sub = m.dropna(subset=[ft])
        if len(sub) < 200 or sub[ft].nunique() < 10:
            continue
        try:
            sub = sub.assign(bk=pd.qcut(sub[ft], 3, labels=["低", "中", "高"], duplicates="drop"))
        except ValueError:
            continue
        tr = sub.groupby("bk", observed=True)["is_true_rally"].mean()
        lo_l, hi_l = tr.get("低", np.nan) / base, tr.get("高", np.nan) / base
        if np.isnan(lo_l) or np.isnan(hi_l):
            continue
        rows.append((ft, lo_l, hi_l, abs(hi_l - lo_l)))
    for ft, lo_l, hi_l, spread in sorted(rows, key=lambda x: -x[3]):
        flag = " <<<" if spread >= 0.25 else ""
        print(f"  {ft:20} 低分位lift={lo_l:.2f} 高分位lift={hi_l:.2f} 跨度={spread:.2f}{flag}")

    print("\n=== 形态 setup / 多头排列 (布尔/序数) ===")
    for ft in bool_feats:
        sub = m.dropna(subset=[ft])
        for val, gg in sub.groupby(ft, observed=True):
            tr = gg["is_true_rally"].mean()
            print(f"  {ft}={val}: TRUE-rate={tr:.1%} lift={tr/base:.2f} (n={len(gg):,})")


if __name__ == "__main__":
    main()
