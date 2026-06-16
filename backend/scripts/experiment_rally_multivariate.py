"""experiment_rally_multivariate — D3 crux: 多因子组合能否预测主升浪 (突破单因子 1.1x 天花板)?

D2/D3/D3-富 单因子全弱 (~1.1x)。本实验: 把量价+形态+资金+筹码弱因子组合 (LightGBM), purged k-fold
(embargo 180交易日 防主升浪标签 t+1..t+180 重叠泄露) 预测 is_true_rally, 报 OOS AUC + top-decile TRUE-lift。
泄露防线 (S3 教训: AUC0.779 栽在结局量当特征): (1) 特征全 <=t 构造 (2) embargo (3) **label-shuffle null**
(打乱 y 重跑, AUC 应掉到 ~0.5 = 管道无泄露) (4) §4.2 红线: 真 AUC>0.75 = 异常警报先疑泄露非兴奋。

窗: 2023-01..2024-08 (完整标签 fwd>=180 + cyq/moneyflow_dc 富因子覆盖)。结果倒推 (预测 D1 标签, 非信号正推)。
用法: PYTHONPATH=backend .venv/bin/python backend/scripts/experiment_rally_multivariate.py
"""
from __future__ import annotations

import logging

import duckdb  # rule-compliance: ok evidence=只读 crux 实验+ATTACH; manifest; allowlist
import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score

import lightgbm as lgb

from services.database_manifest import get_database_manifest
from services.experiment_store import open_store, record_verdict, record_ic_cell  # L4 留档 (experiment-discipline 门)

log = logging.getLogger("rally_multivariate")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")

EMBARGO_DAYS = 250  # rule-compliance: ok evidence=主升浪label用t+1..t+180交易日(~250日历日), embargo>=label期防fold重叠泄露(§4.1 purged CV)
TRAIN_END = "20240831"  # rule-compliance: ok evidence=完整标签(fwd>=180)最晚event_date=20240827, 截止保标签可信


def _traj_features(g: pd.DataFrame) -> pd.DataFrame:
    c = pd.Series(g["close"].to_numpy(float)); v = pd.Series(g["volume"].to_numpy(float))
    h = pd.Series(g["high"].to_numpy(float)); lo = pd.Series(g["low"].to_numpy(float))
    lr = np.log(c / c.shift(1))
    f = {
        "vol_dryup": v.rolling(20).mean() / v.rolling(60).mean(),
        "vol_contract": lr.rolling(20).std() / lr.rolling(60).std(),
        "max_vol_spike_120": (v / v.rolling(120).median()).rolling(120).max(),
        "ret_20": c / c.shift(20) - 1, "ret_60": c / c.shift(60) - 1, "ret_120": c / c.shift(120) - 1,
        "pullback_60": (c / c.rolling(60).max() - 1).rolling(60).min(),
        "recover_from_low_60": c / c.rolling(60).min() - 1,
        "dist_high_250": c / c.rolling(250).max() - 1,
        "range_tight_60": (h.rolling(60).max() - lo.rolling(60).min()) / c,
        "ma_align": ((c.rolling(5).mean() > c.rolling(20).mean()).astype(float) + (c.rolling(20).mean() > c.rolling(60).mean()).astype(float)),
    }
    df = pd.DataFrame(f); df["code"] = g["code"].iloc[0]; df["d8"] = [d.replace("-", "") for d in g["date"]]
    return df


def _purged_cv_oos(X, y, dates_int, n_folds=5):
    """purged k-fold: 按日期连续分 fold, 训练集 purge 距 OOS fold 日期 < EMBARGO 的行。返回每行 OOS 预测。"""
    order = np.argsort(dates_int)
    folds = np.array_split(order, n_folds)
    oos_pred = np.full(len(y), np.nan)
    for i, te in enumerate(folds):
        te_lo, te_hi = dates_int[te].min(), dates_int[te].max()
        # purge: 训练行日期距 OOS fold 区间 >= EMBARGO (防 label t+1..t+180 重叠)
        tr_mask = np.ones(len(y), bool); tr_mask[te] = False
        too_close = (np.abs(dates_int - te_lo) < EMBARGO_DAYS) | (np.abs(dates_int - te_hi) < EMBARGO_DAYS) | \
                    ((dates_int >= te_lo) & (dates_int <= te_hi))
        tr_mask &= ~too_close
        if tr_mask.sum() < 200 or y[tr_mask].sum() < 20:
            continue
        m = lgb.LGBMClassifier(n_estimators=200, max_depth=4, learning_rate=0.03, num_leaves=15,
                               min_child_samples=50, subsample=0.8, colsample_bytree=0.8, verbose=-1, n_jobs=2)
        m.fit(X[tr_mask], y[tr_mask])
        oos_pred[te] = m.predict_proba(X[te])[:, 1]
    return oos_pred


def main():
    mf = get_database_manifest()
    con = duckdb.connect(str(mf.path_for("market")), read_only=True)  # rule-compliance: ok evidence=只读+ATTACH; manifest; allowlist
    con.execute(f"ATTACH '{mf.path_for('smartmoney')}' AS sm (READ_ONLY)")
    con.execute(f"ATTACH '{mf.path_for('feature_store')}' AS fs (READ_ONLY)")
    con.execute(f"ATTACH '{mf.path_for('tushare_raw')}' AS tr (READ_ONLY)")

    log.info("加载 K线 + 算量价轨迹特征 ...")
    k = con.execute("SELECT code, date, high, low, close, volume FROM price_kline_qfq_tushare WHERE date >= '2022-01-01' ORDER BY code, date").df()  # rule-compliance: ok evidence=轨迹回看预热(250日)需窗起点早于2023, 非钉死
    traj = pd.concat([_traj_features(g) for _, g in k.groupby("code", sort=False)], ignore_index=True)

    log.info("加载 episode(完整标签窗) + 形态/筹码/资金富因子 ...")
    rich = con.execute(
        f"""
        WITH ep AS (SELECT stock_code code, event_date d8, is_true_rally FROM sm.fact_rally_ground_truth
                    WHERE event_date >= '20230101' AND event_date <= '{TRAIN_END}' AND fwd_window_len >= 180),
        seg AS (SELECT stock_code code, REPLACE(date,'-','') d8, range_pos, CAST(macd_above_zero AS INT) macd_up, TRY_CAST(stage AS DOUBLE) stage_num FROM fs.fact_segment_panel),
        cyq AS (SELECT SUBSTR(ts_code,1,6) code, trade_date d8,
                       (mk.close - cost_5pct)/NULLIF(cost_95pct-cost_5pct,0) AS cyq_px_pctile,
                       (cost_95pct-cost_5pct)/NULLIF(cost_50pct,0) AS cyq_concentration, winner_rate AS cyq_winner
                FROM tr.raw_tushare_cyq_perf c2
                JOIN price_kline_qfq_tushare mk ON mk.code=SUBSTR(c2.ts_code,1,6) AND REPLACE(mk.date,'-','')=c2.trade_date),
        mfd AS (SELECT SUBSTR(ts_code,1,6) code, trade_date d8,
                       AVG(net_amount_rate) OVER (PARTITION BY ts_code ORDER BY trade_date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) mfdc_net_20
                FROM tr.raw_tushare_moneyflow_dc)
        SELECT ep.code, ep.d8, ep.is_true_rally, seg.range_pos, seg.macd_up, seg.stage_num,
               cyq.cyq_px_pctile, cyq.cyq_concentration, cyq.cyq_winner, mfd.mfdc_net_20
        FROM ep LEFT JOIN seg ON seg.code=ep.code AND seg.d8=ep.d8
                LEFT JOIN cyq ON cyq.code=ep.code AND cyq.d8=ep.d8
                LEFT JOIN mfd ON mfd.code=ep.code AND mfd.d8=ep.d8
        """
    ).df()
    con.close()

    m = rich.merge(traj, on=["code", "d8"], how="left")
    feat_cols = ["vol_dryup", "vol_contract", "max_vol_spike_120", "ret_20", "ret_60", "ret_120",
                 "pullback_60", "recover_from_low_60", "dist_high_250", "range_tight_60", "ma_align",
                 "range_pos", "macd_up", "stage_num", "cyq_px_pctile", "cyq_concentration", "cyq_winner", "mfdc_net_20"]
    m = m.dropna(subset=["is_true_rally"])
    X = m[feat_cols].to_numpy(float)
    y = m["is_true_rally"].astype(int).to_numpy()
    dates_int = m["d8"].astype(int).to_numpy()
    log.info("crux 矩阵: %s episode, %s 特征, TRUE-rate=%.1f%%", f"{len(m):,}", len(feat_cols), 100 * y.mean())

    oos = _purged_cv_oos(X, y, dates_int)
    valid = ~np.isnan(oos)
    auc = roc_auc_score(y[valid], oos[valid])
    # top-decile TRUE-lift
    thr = np.quantile(oos[valid], 0.9)
    top = oos[valid] >= thr
    base = y[valid].mean()
    top_lift = y[valid][top].mean() / base
    # label-shuffle null (管道泄露检查)
    rng = np.random.RandomState(20260616)  # rule-compliance: ok evidence=固定种子复现; null 对照非业务参数
    y_sh = y.copy(); rng.shuffle(y_sh)
    oos_sh = _purged_cv_oos(X, y_sh, dates_int)
    vsh = ~np.isnan(oos_sh)
    auc_sh = roc_auc_score(y_sh[vsh], oos_sh[vsh])

    print("\n" + "=" * 64)
    print("主升浪多因子可预测性 crux (purged CV embargo250d, LightGBM)")
    print("=" * 64)
    print(f"  样本={valid.sum():,} TRUE-rate={base:.1%} 特征={len(feat_cols)}")
    print(f"  OOS AUC = {auc:.4f}   (label-shuffle null AUC = {auc_sh:.4f})")
    print(f"  top-decile TRUE-rate = {y[valid][top].mean():.1%}  lift = {top_lift:.2f}x (单因子天花板~1.1x)")
    print("  --- 裁定 ---")
    if auc > 0.75:
        verdict = "ANOMALY_CHECK_LEAKAGE"; print(f"  ⚠ AUC>{0.75} 触 §4.2 异常红线 → 先疑泄露 (查特征 PIT), 非兴奋")
    elif auc - auc_sh < 0.02:
        verdict = "LOW_CEILING_UNPREDICTABLE"; print("  → 组合 AUC ≈ shuffle null = 多因子也无组合信号; 主升浪买点不可预测, 天花板低 → 重想策略形态")
    elif top_lift >= 2.0:
        verdict = "EDGE_CANDIDATE"; print(f"  → 多因子组合突破单因子天花板 (top-decile {top_lift:.1f}x), 主升浪猎手有实弹基础 → 含成本回测")
    else:
        verdict = "WEAK_SIGNAL"; print(f"  → 多因子组合有弱信号 (AUC {auc:.3f} > null {auc_sh:.3f}, top {top_lift:.1f}x) 但不强; 边际价值待含成本回测裁")

    # L4 留档 (experiment-discipline: 诊断也留档, 防散落; confirmed_by_owner=0 非转正, 不触 C-R1/C-LEAK 门)
    rid = f"rally_mv_crux_{TRAIN_END}"
    with open_store() as st:
        record_ic_cell(st, run_id=rid, data_snapshot=f"rally_gt_{TRAIN_END}", consumer_id="rally_multivariate",
                       metric="oos_auc", value=float(auc), n_windows=int(valid.sum()))
        record_verdict(st, run_id=rid, family="rally_predictability_crux", verdict=verdict,
                       judges={"oos_auc": float(auc), "shuffle_null_auc": float(auc_sh),
                               "top_decile_lift": float(top_lift), "n": int(valid.sum()), "n_features": len(feat_cols)},
                       confirmed_by_owner=0)
    print(f"  [store] 留档 rally_predictability_crux verdict={verdict} (run_id={rid})")


if __name__ == "__main__":
    main()
