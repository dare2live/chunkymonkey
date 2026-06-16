"""experiment_rally_multifactor_combo — 多因子非线性组合判别主升浪赢家 (主会话主导, 2026-06-17)。

owner: 用户 2026-06-16 "把所有你认为可能有alpha增益的因子扔进去(optuna/modal)组合" + architect REVISE。
前置: D3 单因子前兆筛选(experiment_rally_precursor_model)显示单因子均弱(最强AUC0.528, 主力净流入lift1.18/
筹码变化AUC0.528=真弱正信号)。本实验测**全因子非线性组合(LightGBM)能否抄出赢家答案**(单因子弱≠组合弱)。
严守防过拟合(单因子弱→组合易过拟合, §4.2): purged 时序 CV(按event_date分折+embargo) + label-shuffle null(组合
AUC须显著>shuffle分布) + train(<2024-07)/test(>=) hold-out + 特征重要性(看模型靠哪些因子)。
全 PIT(前兆特征<=event_date)。这是组合判别力裁决, 非可交易策略(那是下一步: 据组合分排名→鱼头入场/鱼尾出场→含成本OOS)。
源: 复用 experiment_rally_precursor_model 的 compute_features/event_features (DRY)。
用法: PYTHONPATH=backend .venv/bin/python backend/scripts/experiment_rally_multifactor_combo.py
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import duckdb  # rule-compliance: ok evidence=只读建面板; manifest; allowlist
import numpy as np
import pandas as pd

from services.database_manifest import get_database_manifest
from services.experiment_store import open_store, record_verdict
from scripts.experiment_rally_precursor_model import compute_features, event_features  # DRY 复用前兆特征

log = logging.getLogger("rally_combo")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")

TRAIN_TEST_SPLIT = "2024-07-01"  # rule-compliance: ok evidence=同前兆模型, hold-out切点
N_FOLDS = 5         # rule-compliance: ok evidence=purged时序CV折数, 统计常数
EMBARGO_DAYS = 180  # rule-compliance: ok evidence=label前向窗180天, 折间embargo>=label期防泄露(strategy_validation_contract)
N_SHUFFLE = 30      # rule-compliance: ok evidence=label-shuffle null重抽数, 统计常数
BASE_RATE = 0.1006  # rule-compliance: ok evidence=4345/43202赢家基线率, measured


def build_panel(mf):
    con = duckdb.connect(str(mf.path_for("market")), read_only=True)  # rule-compliance: ok evidence=只读; manifest; allowlist
    con.execute(f"ATTACH '{mf.path_for('tushare_raw')}' AS tr (READ_ONLY)")
    con.execute(f"ATTACH '{mf.path_for('smartmoney')}' AS sm (READ_ONLY)")
    events = con.execute("SELECT stock_code, event_date, is_true_rally FROM sm.fact_rally_ground_truth").df()
    q = """
    SELECT k.code, k.date, k.open, k.high, k.low, k.close, k.volume, k.amount,
           db.turnover_rate, db.volume_ratio, db.circ_mv,
           mf.buy_sm_amount, mf.buy_md_amount, mf.buy_lg_amount, mf.buy_elg_amount,
           mf.sell_sm_amount, mf.sell_md_amount, mf.sell_lg_amount, mf.sell_elg_amount,
           cy.winner_rate, cy.cost_5pct, cy.cost_50pct, cy.cost_95pct
    FROM price_kline_qfq_tushare k
    LEFT JOIN tr.raw_tushare_daily_basic db ON SUBSTR(db.ts_code,1,6)=k.code AND db.trade_date=REPLACE(k.date::VARCHAR,'-','')
    LEFT JOIN tr.raw_tushare_moneyflow mf ON SUBSTR(mf.ts_code,1,6)=k.code AND mf.trade_date=REPLACE(k.date::VARCHAR,'-','')
    LEFT JOIN tr.raw_tushare_cyq_perf cy ON SUBSTR(cy.ts_code,1,6)=k.code AND cy.trade_date=REPLACE(k.date::VARCHAR,'-','')
    WHERE k.date >= '2018-10-01' AND k.close>0 ORDER BY k.code, k.date
    """
    big = con.execute(q).df()
    con.close()
    ev_by_code = {code: g for code, g in events.groupby("stock_code")}
    rows = []
    for code, g in big.groupby("code"):
        if code not in ev_by_code or len(g) < 80:
            continue
        g = compute_features(g.reset_index(drop=True))
        d2i = {d: i for i, d in enumerate(g["date"].astype(str))}
        for _, ev in ev_by_code[code].iterrows():
            ed = str(ev["event_date"])
            if len(ed) == 8 and ed.isdigit():
                ed = f"{ed[:4]}-{ed[4:6]}-{ed[6:8]}"
            ei = d2i.get(ed)
            if ei is None:
                continue
            feat = event_features(g, ei)
            if feat is None:
                continue
            feat["is_win"] = int(ev["is_true_rally"])
            feat["event_date"] = ed
            rows.append(feat)
    return pd.DataFrame(rows)


def purged_cv_auc(model_ctor, X, y, dates, n_folds, embargo_days):
    """按event_date排序分时序折, 训练折与验证折间 embargo 天剔除, 返回各折验证AUC。"""
    order = np.argsort(dates)
    Xs, ys, ds = X[order], y[order], np.array(sorted(dates))
    fold_bounds = np.linspace(0, len(Xs), n_folds + 1).astype(int)
    aucs = []
    for f in range(n_folds):
        v0, v1 = fold_bounds[f], fold_bounds[f + 1]
        val_idx = np.arange(v0, v1)
        if len(val_idx) < 30:
            continue
        val_lo, val_hi = ds[v0], ds[v1 - 1]
        emb = pd.Timedelta(days=embargo_days)
        keep = []
        for i in range(len(Xs)):
            if v0 <= i < v1:
                continue
            di = pd.to_datetime(ds[i])
            if (pd.to_datetime(val_lo) - emb) <= di <= (pd.to_datetime(val_hi) + emb):
                continue  # embargo 区间剔除
            keep.append(i)
        tr_idx = np.array(keep)
        if len(tr_idx) < 100 or y[order][val_idx].sum() < 5:
            continue
        m = model_ctor()
        m.fit(Xs[tr_idx], ys[tr_idx])
        p = m.predict_proba(Xs[val_idx])[:, 1]
        aucs.append(_auc(p, ys[val_idx]))
    return aucs


def _auc(scores, labels):
    m = ~np.isnan(scores)
    s, yy = scores[m], labels[m]
    if yy.sum() == 0 or yy.sum() == len(yy):
        return np.nan
    order = np.argsort(s)
    ranks = np.empty(len(s)); ranks[order] = np.arange(1, len(s) + 1)
    np_, nn = yy.sum(), len(yy) - yy.sum()
    return float((ranks[yy == 1].sum() - np_ * (np_ + 1) / 2) / (np_ * nn))


def main():
    try:
        import lightgbm as lgb
    except ImportError:
        raise SystemExit("lightgbm 未安装")
    mf = get_database_manifest()
    log.info("建前兆面板 (复用 D3 特征)...")
    panel = build_panel(mf)
    log.info("面板 %s 事件 (%s 赢家)", f"{len(panel):,}", f"{int(panel['is_win'].sum()):,}")
    feat_cols = [c for c in panel.columns if c not in ("is_win", "event_date")]
    panel = panel.replace([np.inf, -np.inf], np.nan)
    X = panel[feat_cols].to_numpy(dtype=float)
    y = panel["is_win"].to_numpy()
    dates = panel["event_date"].to_numpy()

    def ctor():
        return lgb.LGBMClassifier(n_estimators=200, num_leaves=15, learning_rate=0.03, min_child_samples=80,
                                  subsample=0.8, colsample_bytree=0.7, reg_lambda=1.0, class_weight="balanced", verbose=-1)

    log.info("purged 时序 CV (%d折, embargo%d天)...", N_FOLDS, EMBARGO_DAYS)
    cv_aucs = purged_cv_auc(ctor, X, y, dates, N_FOLDS, EMBARGO_DAYS)
    cv_mean = float(np.nanmean(cv_aucs)) if cv_aucs else np.nan

    # train/test hold-out
    trm = dates < TRAIN_TEST_SPLIT
    m = ctor(); m.fit(X[trm], y[trm])
    p_te = m.predict_proba(X[~trm])[:, 1]
    auc_te = _auc(p_te, y[~trm])
    # top-decile lift on test
    thr = np.nanpercentile(p_te, 90)
    top = p_te >= thr
    lift_te = (y[~trm][top].mean() / BASE_RATE) if top.sum() > 5 else np.nan

    # shuffle-null: 打乱label重训, OOS AUC 分布 (组合AUC须显著>它)
    log.info("label-shuffle null (%d次)...", N_SHUFFLE)
    rng = np.random.RandomState(424242)  # rule-compliance: ok evidence=固定种子复现shuffle null
    null_aucs = []
    for _ in range(N_SHUFFLE):
        ysh = rng.permutation(y[trm])
        ms = ctor(); ms.fit(X[trm], ysh)
        null_aucs.append(_auc(ms.predict_proba(X[~trm])[:, 1], y[~trm]))
    null_mean, null_p95 = float(np.nanmean(null_aucs)), float(np.nanpercentile(null_aucs, 95))

    # 特征重要性
    imp = sorted(zip(feat_cols, m.feature_importances_), key=lambda x: -x[1])[:8]

    print(f"\n多因子非线性组合判别主升浪赢家 (LightGBM, {len(feat_cols)}因子, 全PIT)")
    print(f"  面板 {len(panel):,} 事件 / 赢家 {int(y.sum()):,} ({y.mean()*100:.1f}%)")
    print(f"  purged时序CV AUC = {cv_mean:.4f} (各折 {[round(a,3) for a in cv_aucs]})")
    print(f"  hold-out test(>={TRAIN_TEST_SPLIT}) AUC = {auc_te:.4f}  top十分位lift = {lift_te:.2f}")
    print(f"  shuffle-null AUC: 均值 {null_mean:.4f} / p95 {null_p95:.4f}")
    print(f"  特征重要性 top8: {[(f, int(i)) for f,i in imp]}")
    edge = auc_te - null_p95
    print(f"\n  --- 裁决 (组合能否抄出答案, 防过拟合: test AUC vs shuffle p95) ---")
    if not np.isnan(auc_te) and auc_te > null_p95 + 0.01 and cv_mean > 0.52:
        verdict = f"组合有真判别力: test AUC {auc_te:.3f} > shuffle-null p95 {null_p95:.3f} 且 CV {cv_mean:.3f}>0.52. 主力/筹码等弱因子组合后显著. 下一步: 据组合分top候选→鱼头入场/鱼尾出场→含成本OOS 2025-06+ paper_sim; 扩因子(板块热度/预期/调研)+Optuna/Modal调"
    elif not np.isnan(auc_te) and auc_te > null_p95:
        verdict = f"组合弱判别(边际): test AUC {auc_te:.3f} 略>shuffle p95 {null_p95:.3f}, CV {cv_mean:.3f}. 组合比单因子(0.528)略强但不强; 须扩因子(板块热度/资金流向/预期上调/调研)再判, 或edge主要在出场/分层非买点选股"
    else:
        verdict = f"组合无显著判别: test AUC {auc_te:.3f} 不超 shuffle p95 {null_p95:.3f} = 买点选股在现有因子下不可靠抄答案(印证). edge须转: 鱼身延续(确认后跟随)/出场择时/板块轮动, 而非买点预测哪个突破成主升浪"
    print(f"  → {verdict}")

    run_id = "rally_multifactor_combo_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    judges = {"n_events": len(panel), "cv_auc": round(cv_mean, 4) if not np.isnan(cv_mean) else None,
              "test_auc": round(auc_te, 4) if not np.isnan(auc_te) else None, "test_lift": round(lift_te, 3) if not np.isnan(lift_te) else None,
              "shuffle_null_mean": round(null_mean, 4), "shuffle_null_p95": round(null_p95, 4),
              "edge_vs_null": round(edge, 4) if not np.isnan(edge) else None,
              "top_features": [f for f, _ in imp], "summary": verdict[:150]}
    vlabel = "COMBO_REAL_EDGE" if (not np.isnan(auc_te) and auc_te > null_p95 + 0.01 and cv_mean > 0.52) else ("COMBO_MARGINAL" if (not np.isnan(auc_te) and auc_te > null_p95) else "COMBO_NO_EDGE")
    with open_store() as store:
        record_verdict(store, run_id=run_id, family="rally_multifactor_combo", verdict=vlabel, judges=judges, confirmed_by_owner=0)
    print(f"\n  [experiment_store] 已留档 family=rally_multifactor_combo verdict={vlabel}")


if __name__ == "__main__":
    main()
