#!/usr/bin/env python3
"""C0 PIT 模型训练 + 三段切分评估（§1 P0.B 核心交付）。

数据源：fact_event_features_pit（仅 EV + MG + PX 三族，全 PIT）
切分：按 notice_date 严格时序切 70 / 15 / 15 = train / valid / holdout
训练：LightGBM baseline（首版不上 Optuna，下轮 P0.2 扩展）
评估：
  - train：诊断用
  - valid：early stopping 依据
  - holdout：一次性评估，不参与选参

落表：qlib_model_evaluation 三行 eval_dataset in (train, valid, holdout)
model_id 前缀 `lgb_event_pit_baseline_`
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd
import lightgbm as lgb
from scipy import stats
from sklearn.metrics import roc_auc_score

from services.db import get_conn

logger = logging.getLogger("train_event_qlib_pit")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s | %(message)s")


EXCLUDE_COLS = {
    "institution_id", "stock_code", "notice_date", "report_date",
    "event_type", "tdx_l1_name", "tdx_l2_name",
    "ev_premium_bucket",  # 字符串字段
    "ref_trade_date", "computed_at",
    "label_gain_30d", "label_gain_60d",
    "label_max_drawdown_30d", "label_max_drawdown_60d",
}


def compute_eval(y_true, y_pred, follow_thresh=8.0):
    mask = ~(np.isnan(y_true) | np.isnan(y_pred))
    yt = y_true[mask]; yp = y_pred[mask]
    if len(yt) < 2:
        return dict(n=int(len(yt)), ic=None, rank_ic=None, auc_roc=None, ks=None, ks_p=None, pos_rate=None)
    ic = float(stats.pearsonr(yt, yp)[0])
    rank_ic = float(stats.spearmanr(yt, yp)[0])
    y_bin = (yt > follow_thresh).astype(int)
    pos_rate = float(y_bin.mean())
    auc = ks = ks_p = None
    if 0 < pos_rate < 1:
        auc = float(roc_auc_score(y_bin, yp))
        k = stats.ks_2samp(yp[y_bin == 1], yp[y_bin == 0])
        ks = float(k.statistic); ks_p = float(k.pvalue)
    return dict(n=int(len(yt)), ic=ic, rank_ic=rank_ic, auc_roc=auc, ks=ks, ks_p=ks_p, pos_rate=pos_rate)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", default="gain_60d", choices=["gain_30d", "gain_60d"])
    parser.add_argument("--follow-threshold", type=float, default=8.0)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    conn = get_conn()
    try:
        df = pd.read_sql_query("SELECT * FROM fact_event_features_pit", conn)
        logger.info("fact_event_features_pit %d 行 x %d 列", len(df), len(df.columns))

        label_col = f"label_{args.label}"
        data = df[df[label_col].notna()].copy().sort_values("notice_date").reset_index(drop=True)
        logger.info("有 %s 的样本 %d", label_col, len(data))

        numeric_cols = [c for c in data.columns
                        if c not in EXCLUDE_COLS and pd.api.types.is_numeric_dtype(data[c])]
        logger.info("PIT 特征 %d 个：%s", len(numeric_cols), numeric_cols)

        # 三段切分 70/15/15
        n = len(data)
        cut1 = int(n * 0.70)
        cut2 = int(n * 0.85)
        train_df = data.iloc[:cut1].reset_index(drop=True)
        valid_df = data.iloc[cut1:cut2].reset_index(drop=True)
        hold_df = data.iloc[cut2:].reset_index(drop=True)
        logger.info("切分：train=%d (%s~%s), valid=%d (%s~%s), holdout=%d (%s~%s)",
                    len(train_df), train_df["notice_date"].min(), train_df["notice_date"].max(),
                    len(valid_df), valid_df["notice_date"].min(), valid_df["notice_date"].max(),
                    len(hold_df), hold_df["notice_date"].min(), hold_df["notice_date"].max())

        X_train = train_df[numeric_cols].values; y_train = train_df[label_col].values
        X_valid = valid_df[numeric_cols].values; y_valid = valid_df[label_col].values
        X_hold = hold_df[numeric_cols].values; y_hold = hold_df[label_col].values

        # LightGBM baseline（强正则，防小样本/高噪声过拟合）
        params = dict(
            objective="regression", metric="mse",
            learning_rate=0.02, num_leaves=15, min_data_in_leaf=100,
            max_depth=5, feature_fraction=0.7, bagging_fraction=0.7,
            bagging_freq=5, lambda_l1=0.1, lambda_l2=0.5,
            verbosity=-1, seed=42,
        )
        dtrain = lgb.Dataset(X_train, label=y_train, feature_name=numeric_cols)
        dvalid = lgb.Dataset(X_valid, label=y_valid, feature_name=numeric_cols, reference=dtrain)
        model = lgb.train(
            params, dtrain, num_boost_round=1000,
            valid_sets=[dtrain, dvalid], valid_names=["train", "valid"],
            callbacks=[lgb.early_stopping(stopping_rounds=50), lgb.log_evaluation(period=0)],
        )
        logger.info("best_iteration=%d", model.best_iteration)

        pred_train = model.predict(X_train, num_iteration=model.best_iteration)
        pred_valid = model.predict(X_valid, num_iteration=model.best_iteration)
        pred_hold = model.predict(X_hold, num_iteration=model.best_iteration)

        m_train = compute_eval(y_train, pred_train, args.follow_threshold)
        m_valid = compute_eval(y_valid, pred_valid, args.follow_threshold)
        m_hold = compute_eval(y_hold, pred_hold, args.follow_threshold)

        def _fmt(m):
            return (f"IC={m['ic']:.4f if m['ic'] is not None else 0:.4f} "
                    f"RankIC={m['rank_ic']:.4f if m['rank_ic'] is not None else 0:.4f} "
                    f"AUC={(m['auc_roc'] or 0):.3f} KS={(m['ks'] or 0):.3f} n={m['n']}")

        logger.info("[train]   IC=%.4f RankIC=%.4f AUC=%.3f KS=%.3f n=%d pos=%.1f%%",
                    m_train['ic'], m_train['rank_ic'], m_train['auc_roc'] or 0, m_train['ks'] or 0, m_train['n'], (m_train['pos_rate'] or 0)*100)
        logger.info("[valid]   IC=%.4f RankIC=%.4f AUC=%.3f KS=%.3f n=%d pos=%.1f%%",
                    m_valid['ic'], m_valid['rank_ic'], m_valid['auc_roc'] or 0, m_valid['ks'] or 0, m_valid['n'], (m_valid['pos_rate'] or 0)*100)
        logger.info("[holdout] IC=%.4f RankIC=%.4f AUC=%.3f KS=%.3f n=%d pos=%.1f%%",
                    m_hold['ic'], m_hold['rank_ic'], m_hold['auc_roc'] or 0, m_hold['ks'] or 0, m_hold['n'], (m_hold['pos_rate'] or 0)*100)

        importance = model.feature_importance(importance_type="gain")
        feat_imp = sorted(
            [(numeric_cols[i], float(importance[i])) for i in range(len(numeric_cols))],
            key=lambda x: x[1], reverse=True,
        )
        logger.info("=== 特征重要性 Top 10 ===")
        for f, v in feat_imp[:10]:
            logger.info("  %-30s %.0f", f, v)

        if not args.dry_run:
            model_id = datetime.utcnow().strftime("lgb_event_pit_baseline_%Y%m%d_%H%M%S")
            created = datetime.utcnow().isoformat()
            fi_json = {k: round(v, 1) for k, v in feat_imp[:20]}

            # 写 qlib_model_evaluation 三行
            def _row(ds, m):
                return (model_id, created, ds, m["n"], m["ic"], m["rank_ic"],
                        m["ks"], m["ks_p"], m["auc_roc"], None, m["pos_rate"],
                        json.dumps(fi_json, ensure_ascii=False),
                        f"PIT C0 baseline | label={label_col} | features={len(numeric_cols)} | best_iter={model.best_iteration} | no_lookahead=true",
                        created)
            conn.executemany(
                "INSERT OR REPLACE INTO qlib_model_evaluation "
                "(model_id,eval_date,eval_dataset,n_samples,ic,rank_ic,ks_statistic,ks_pvalue,"
                "auc_roc,auc_pr,binary_positive_rate,feature_importance_json,notes,created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                [_row("train", m_train), _row("valid", m_valid), _row("holdout", m_hold)],
            )
            conn.commit()
            logger.info("落库 qlib_model_evaluation 3 行 model_id=%s", model_id)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
