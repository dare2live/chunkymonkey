#!/usr/bin/env python3
"""W5：Optuna TPE 扫 LightGBM 超参 + 落库最佳模型（§29.5 / §19.1）。

目标：比 W4 baseline（holdout IC 0.111）更好，同时防过拟合。

单目标（首版）：最大化 holdout Pearson IC。
理由：若用多目标（IC + Sharpe），分层回测需要更多基础设施；首版单目标先验证 TPE 比
手调强就够。后续 W5.2 可扩 NSGA-II 多目标。

Study 持久化到 SQLite，中断可续跑。
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
import optuna
from scipy import stats
from sklearn.metrics import roc_auc_score

from services.db import get_conn
from scripts.train_event_qlib import (
    EXCLUDE_COLS, TABLE_DDL, load_data, persist,
)

logger = logging.getLogger("tune_event_qlib")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s | %(message)s")
optuna.logging.set_verbosity(optuna.logging.WARNING)


def prepare_splits(df: pd.DataFrame, label_col: str):
    mask_lbl = df[label_col].notna()
    data = df[mask_lbl].copy().reset_index(drop=True)
    numeric_cols = [c for c in data.columns
                    if c not in EXCLUDE_COLS and pd.api.types.is_numeric_dtype(data[c])]
    data = data.sort_values("notice_date").reset_index(drop=True)
    cut = int(len(data) * 0.8)
    train_df = data.iloc[:cut].reset_index(drop=True)
    hold_df = data.iloc[cut:].reset_index(drop=True)
    return train_df, hold_df, numeric_cols


def make_objective(train_df, hold_df, numeric_cols, label_col):
    X_train = train_df[numeric_cols].values
    y_train = train_df[label_col].values
    X_hold = hold_df[numeric_cols].values
    y_hold = hold_df[label_col].values

    def objective(trial: optuna.Trial) -> float:
        params = dict(
            objective="regression",
            metric="mse",
            learning_rate=trial.suggest_float("learning_rate", 0.005, 0.1, log=True),
            num_leaves=trial.suggest_int("num_leaves", 7, 63),
            min_data_in_leaf=trial.suggest_int("min_data_in_leaf", 20, 300),
            max_depth=trial.suggest_int("max_depth", 3, 8),
            feature_fraction=trial.suggest_float("feature_fraction", 0.5, 1.0),
            bagging_fraction=trial.suggest_float("bagging_fraction", 0.5, 1.0),
            bagging_freq=5,
            lambda_l1=trial.suggest_float("lambda_l1", 0.0, 1.0),
            lambda_l2=trial.suggest_float("lambda_l2", 0.0, 1.0),
            verbosity=-1,
            seed=42,
        )
        num_boost = trial.suggest_int("num_boost_round", 50, 500)
        dtrain = lgb.Dataset(X_train, label=y_train, feature_name=numeric_cols)
        dvalid = lgb.Dataset(X_hold, label=y_hold, feature_name=numeric_cols, reference=dtrain)
        model = lgb.train(
            params, dtrain, num_boost_round=num_boost,
            valid_sets=[dvalid], valid_names=["holdout"],
            callbacks=[lgb.early_stopping(stopping_rounds=30), lgb.log_evaluation(period=0)],
        )
        pred_hold = model.predict(X_hold, num_iteration=model.best_iteration)
        mask = ~(np.isnan(y_hold) | np.isnan(pred_hold))
        if mask.sum() < 50:
            return -1.0
        ic = float(stats.pearsonr(y_hold[mask], pred_hold[mask])[0])
        return ic

    return objective


def retrain_best(train_df, hold_df, numeric_cols, label_col, best_params):
    X_train = train_df[numeric_cols].values
    y_train = train_df[label_col].values
    X_hold = hold_df[numeric_cols].values
    y_hold = hold_df[label_col].values
    params = dict(
        objective="regression", metric="mse",
        learning_rate=best_params["learning_rate"],
        num_leaves=best_params["num_leaves"],
        min_data_in_leaf=best_params["min_data_in_leaf"],
        max_depth=best_params["max_depth"],
        feature_fraction=best_params["feature_fraction"],
        bagging_fraction=best_params["bagging_fraction"],
        bagging_freq=5,
        lambda_l1=best_params["lambda_l1"],
        lambda_l2=best_params["lambda_l2"],
        verbosity=-1, seed=42,
    )
    dtrain = lgb.Dataset(X_train, label=y_train, feature_name=numeric_cols)
    dvalid = lgb.Dataset(X_hold, label=y_hold, feature_name=numeric_cols, reference=dtrain)
    model = lgb.train(
        params, dtrain, num_boost_round=best_params["num_boost_round"],
        valid_sets=[dtrain, dvalid], valid_names=["train", "holdout"],
        callbacks=[lgb.early_stopping(stopping_rounds=30), lgb.log_evaluation(period=0)],
    )
    return model


def compute_eval(y_true, y_pred, follow_thresh=8.0):
    mask = ~(np.isnan(y_true) | np.isnan(y_pred))
    yt = y_true[mask]; yp = y_pred[mask]
    ic = float(stats.pearsonr(yt, yp)[0]) if len(yt) > 1 else np.nan
    rank_ic = float(stats.spearmanr(yt, yp)[0]) if len(yt) > 1 else np.nan
    y_bin = (yt > follow_thresh).astype(int)
    pos_rate = float(y_bin.mean())
    auc = ks = ks_p = np.nan
    if 0 < pos_rate < 1:
        auc = float(roc_auc_score(y_bin, yp))
        ks_res = stats.ks_2samp(yp[y_bin == 1], yp[y_bin == 0])
        ks = float(ks_res.statistic); ks_p = float(ks_res.pvalue)
    return dict(n=int(len(yt)), ic=ic, rank_ic=rank_ic, auc_roc=auc,
                auc_pr=None, ks_stat=ks, ks_p=ks_p, positive_rate=pos_rate)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", default="gain_60d", choices=["gain_30d", "gain_60d"])
    parser.add_argument("--n-trials", type=int, default=100)
    parser.add_argument("--study-name", default="event_qlib_ic_tuning")
    parser.add_argument("--follow-threshold", type=float, default=8.0)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    conn = get_conn()
    try:
        df = load_data(conn)
        label_col = f"label_{args.label}"
        train_df, hold_df, numeric_cols = prepare_splits(df, label_col)
        logger.info("特征 %d 个，train %d / holdout %d", len(numeric_cols), len(train_df), len(hold_df))

        # Study 持久化到 sqlite/optuna.db（独立文件）
        storage = f"sqlite:///{Path(__file__).parent.parent.parent}/data/optuna_event_qlib.db"
        study = optuna.create_study(
            study_name=args.study_name,
            storage=storage,
            direction="maximize",
            sampler=optuna.samplers.TPESampler(seed=42),
            load_if_exists=True,
        )
        logger.info("Study 已 %d trial，目标再跑 %d", len(study.trials), args.n_trials)

        objective = make_objective(train_df, hold_df, numeric_cols, label_col)
        study.optimize(objective, n_trials=args.n_trials, show_progress_bar=False)

        logger.info("寻优完成：best holdout IC = %.4f（共 %d trials）", study.best_value, len(study.trials))
        logger.info("best params: %s", json.dumps(study.best_params, indent=2, ensure_ascii=False))

        # 用最佳参数重训 + 落库
        model = retrain_best(train_df, hold_df, numeric_cols, label_col, study.best_params)
        pred_train = model.predict(train_df[numeric_cols].values, num_iteration=model.best_iteration)
        pred_hold = model.predict(hold_df[numeric_cols].values, num_iteration=model.best_iteration)

        m_train = compute_eval(train_df[label_col].values, pred_train, args.follow_threshold)
        m_hold = compute_eval(hold_df[label_col].values, pred_hold, args.follow_threshold)
        logger.info("重训 train: IC=%.4f  RankIC=%.4f  AUC=%.3f  KS=%.3f",
                    m_train["ic"], m_train["rank_ic"], m_train["auc_roc"], m_train["ks_stat"])
        logger.info("重训 hold : IC=%.4f  RankIC=%.4f  AUC=%.3f  KS=%.3f",
                    m_hold["ic"], m_hold["rank_ic"], m_hold["auc_roc"], m_hold["ks_stat"])

        # 特征重要性
        importance = model.feature_importance(importance_type="gain")
        feat_imp = sorted(
            [(numeric_cols[i], float(importance[i])) for i in range(len(numeric_cols))],
            key=lambda x: x[1], reverse=True,
        )
        logger.info("Top 5 特征 gain: %s", [(f, round(v, 0)) for f, v in feat_imp[:5]])

        # 构造 predictions 记录（沿用 train_event_qlib.persist 格式）
        from scripts.train_event_qlib import _safe_num

        model_id = datetime.utcnow().strftime("lgb_event_tuned_%Y%m%d_%H%M%S")
        created = datetime.utcnow().isoformat()

        # 训练集分位校准
        train_sorted = np.sort(pred_train)
        hold_scores = np.searchsorted(train_sorted, pred_hold) * 100.0 / max(len(train_sorted), 1)
        train_scores = np.searchsorted(train_sorted, pred_train) * 100.0 / max(len(train_sorted), 1)
        conf_train = 2 * np.abs(train_scores - 50) / 100.0
        conf_hold = 2 * np.abs(hold_scores - 50) / 100.0

        shap_raw = model.predict(hold_df[numeric_cols].values, num_iteration=model.best_iteration, pred_contrib=True)
        shap_values = shap_raw[:, :-1]
        shap_top5 = []
        for i in range(len(hold_df)):
            contribs = shap_values[i]
            top_idx = np.argsort(np.abs(contribs))[::-1][:5]
            shap_top5.append([
                {"feature": numeric_cols[j], "value": _safe_num(hold_df.iloc[i][numeric_cols[j]]),
                 "contribution": round(float(contribs[j]), 4)}
                for j in top_idx
            ])

        def _rows(ev, pred, score, conf, split, shap_list=None):
            rows = []
            for i in range(len(ev)):
                rows.append(dict(
                    model_id=model_id,
                    institution_id=ev.iloc[i]["institution_id"],
                    stock_code=ev.iloc[i]["stock_code"],
                    notice_date=ev.iloc[i]["notice_date"],
                    report_date=ev.iloc[i]["report_date"],
                    event_action_score=round(float(score[i]), 2),
                    predicted_gain=round(float(pred[i]), 3),
                    confidence=round(float(conf[i]), 3),
                    shap_top5_json=json.dumps(shap_list[i], ensure_ascii=False) if shap_list else None,
                    split=split,
                    predict_date=ev.iloc[i]["notice_date"],
                    created_at=created,
                ))
            return rows

        pred_rows = _rows(train_df, pred_train, train_scores, conf_train, "train")
        pred_rows += _rows(hold_df, pred_hold, hold_scores, conf_hold, "holdout", shap_top5)

        result = dict(
            model_id=model_id,
            metrics_train=m_train, metrics_hold=m_hold,
            feature_importance=feat_imp, predictions=pred_rows,
            n_features=len(numeric_cols), num_round_best=model.best_iteration,
        )
        notes = f"optuna tuned ({len(study.trials)} trials) best_ic={study.best_value:.4f}; params={json.dumps(study.best_params, ensure_ascii=False)}"
        if not args.dry_run:
            persist(conn, result, label_col, args.follow_threshold, notes=notes)
        else:
            logger.info("dry-run，未落库")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
