#!/usr/bin/env python3
"""W4 Layer D：Qlib 事件级 LightGBM baseline + SHAP + KS/AUC 评估（§29.5 / §30）。

不走 qlib.workflow（截面 topk 策略不适用）。用 LightGBM + pandas 直接：
  1. 读 fact_event_features 全量
  2. 时序切分 train (80%) / holdout (20%) 按 notice_date
  3. 训练回归：label = label_gain_60d
  4. 评估：IC / RankIC（回归）+ AUC / KS（二分类：gain_60d ≥ 8%）
  5. 归因：LightGBM pred_contrib（等价 TreeSHAP）提取每预测的 top5 特征
  6. 落库：qlib_event_prediction + qlib_model_evaluation

用法：
  python -m backend.scripts.train_event_qlib                   # 默认全量
  python -m backend.scripts.train_event_qlib --label gain_30d  # 换 30d label
  python -m backend.scripts.train_event_qlib --dry-run         # 不落库
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
from sklearn.metrics import roc_auc_score, average_precision_score

from services.db import get_conn

logger = logging.getLogger("train_event_qlib")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s | %(message)s")


TABLE_DDL = """
CREATE TABLE IF NOT EXISTS qlib_event_prediction (
    model_id           TEXT NOT NULL,
    institution_id     TEXT NOT NULL,
    stock_code         TEXT NOT NULL,
    notice_date        TEXT NOT NULL,
    report_date        TEXT NOT NULL,
    event_action_score REAL,
    predicted_gain     REAL,
    confidence         REAL,
    shap_top5_json     TEXT,
    split              TEXT,
    predict_date       TEXT,
    created_at         TEXT,
    PRIMARY KEY (model_id, institution_id, stock_code, notice_date, report_date)
);
CREATE INDEX IF NOT EXISTS idx_qep_notice ON qlib_event_prediction(notice_date);

CREATE TABLE IF NOT EXISTS qlib_model_evaluation (
    model_id           TEXT NOT NULL,
    eval_date          TEXT NOT NULL,
    eval_dataset       TEXT NOT NULL,
    n_samples          INTEGER,
    ic                 REAL,
    rank_ic            REAL,
    ks_statistic       REAL,
    ks_pvalue          REAL,
    auc_roc            REAL,
    auc_pr             REAL,
    binary_positive_rate REAL,
    feature_importance_json TEXT,
    notes              TEXT,
    created_at         TEXT,
    PRIMARY KEY (model_id, eval_date, eval_dataset)
);
"""


# 排除非特征列（id/date/text/label/派生）
EXCLUDE_COLS = {
    "institution_id", "stock_code", "notice_date", "report_date",
    "event_type", "tdx_l1_name", "tdx_l2_name",
    "premium_bucket", "inst_l2_verdict",
    "computed_at",
    "label_gain_30d", "label_gain_60d",
    "label_max_drawdown_30d", "label_max_drawdown_60d",
    # report_to_notice_lag_days：披露滞后天数，和 train/holdout 时段的报告期分布耦合，
    # 可能引入时序泄漏伪相关（W4 首测中此列 importance 占 1e6，高度可疑）
    "report_to_notice_lag_days",
}


def load_data(conn) -> pd.DataFrame:
    df = pd.read_sql_query("SELECT * FROM fact_event_features", conn)
    logger.info("加载 fact_event_features：%d 行", len(df))
    return df


def train_and_evaluate(df: pd.DataFrame, label_col: str = "label_gain_60d",
                       follow_threshold: float = 8.0) -> dict:
    """训练并返回所有评估结果 + predictions DataFrame + 特征重要性"""
    mask_lbl = df[label_col].notna()
    data = df[mask_lbl].copy().reset_index(drop=True)
    logger.info("有 %s 的样本：%d", label_col, len(data))

    numeric_cols = [c for c in data.columns
                    if c not in EXCLUDE_COLS and pd.api.types.is_numeric_dtype(data[c])]
    logger.info("数值特征列 %d 个: %s", len(numeric_cols), numeric_cols[:8])

    data = data.sort_values("notice_date").reset_index(drop=True)
    cut = int(len(data) * 0.8)
    train_df = data.iloc[:cut].reset_index(drop=True)
    hold_df = data.iloc[cut:].reset_index(drop=True)
    logger.info("时序切分：train=%d（%s~%s）, holdout=%d（%s~%s）",
                len(train_df), train_df["notice_date"].min(), train_df["notice_date"].max(),
                len(hold_df), hold_df["notice_date"].min(), hold_df["notice_date"].max())

    X_train = train_df[numeric_cols].values
    y_train = train_df[label_col].values
    X_hold = hold_df[numeric_cols].values
    y_hold = hold_df[label_col].values

    # LightGBM 回归 baseline（§29.5 单头，§19 KS 等评估后续再换分类头）
    # 强正则（金融小样本高噪声，LightGBM 默认参数会过拟合）
    params = dict(
        objective="regression",
        metric="mse",
        learning_rate=0.02,         # 低学习率
        num_leaves=15,              # 浅树
        min_data_in_leaf=100,       # 大叶子
        feature_fraction=0.7,
        bagging_fraction=0.7,
        bagging_freq=5,
        lambda_l1=0.1,
        lambda_l2=0.5,
        max_depth=5,
        verbosity=-1,
        seed=42,
    )
    dtrain = lgb.Dataset(X_train, label=y_train, feature_name=numeric_cols)
    dvalid = lgb.Dataset(X_hold, label=y_hold, feature_name=numeric_cols, reference=dtrain)
    model = lgb.train(params, dtrain, num_boost_round=500,
                      valid_sets=[dtrain, dvalid], valid_names=["train", "holdout"],
                      callbacks=[lgb.early_stopping(stopping_rounds=30), lgb.log_evaluation(period=0)])

    pred_train = model.predict(X_train, num_iteration=model.best_iteration)
    pred_hold = model.predict(X_hold, num_iteration=model.best_iteration)

    # ---------- 评估 ----------
    def _metrics(y_true, y_pred, dataset_name):
        mask = ~(np.isnan(y_true) | np.isnan(y_pred))
        yt = y_true[mask]; yp = y_pred[mask]
        # IC / RankIC
        ic = float(stats.pearsonr(yt, yp)[0]) if len(yt) > 1 else np.nan
        rank_ic = float(stats.spearmanr(yt, yp)[0]) if len(yt) > 1 else np.nan
        # 分类：follow = y > threshold
        y_bin = (yt > follow_threshold).astype(int)
        pos_rate = float(y_bin.mean())
        auc_roc, auc_pr, ks_stat, ks_p = (np.nan,) * 4
        if 0 < pos_rate < 1:
            auc_roc = float(roc_auc_score(y_bin, yp))
            auc_pr = float(average_precision_score(y_bin, yp))
            # KS：pred 在正负样本分布的最大 CDF 差
            ks = stats.ks_2samp(yp[y_bin == 1], yp[y_bin == 0])
            ks_stat = float(ks.statistic)
            ks_p = float(ks.pvalue)
        logger.info(
            "[%s] n=%d  IC=%.4f  RankIC=%.4f  AUC=%.3f  KS=%.3f  AP=%.3f  posRate=%.1f%%",
            dataset_name, len(yt), ic, rank_ic, auc_roc, ks_stat, auc_pr, pos_rate * 100,
        )
        return dict(n=int(len(yt)), ic=ic, rank_ic=rank_ic,
                    auc_roc=auc_roc, auc_pr=auc_pr, ks_stat=ks_stat, ks_p=ks_p,
                    positive_rate=pos_rate)

    metrics_train = _metrics(y_train, pred_train, "train")
    metrics_hold = _metrics(y_hold, pred_hold, "holdout")

    # ---------- 特征重要性 ----------
    importance = model.feature_importance(importance_type="gain")
    feat_imp = sorted(
        [(numeric_cols[i], float(importance[i])) for i in range(len(numeric_cols))],
        key=lambda x: x[1], reverse=True,
    )
    logger.info("=== 特征重要性 Top 10 (gain) ===")
    for f, v in feat_imp[:10]:
        logger.info("  %-35s %.1f", f, v)

    # ---------- SHAP（LightGBM 原生 pred_contrib）----------
    # 对 holdout 每条样本取 top 5 贡献特征
    shap_raw = model.predict(X_hold, num_iteration=model.best_iteration, pred_contrib=True)
    # shap_raw shape = (n_hold, n_features + 1) — 最后一列是 bias
    shap_values = shap_raw[:, :-1]
    hold_shap_top5 = []
    for i in range(len(hold_df)):
        contribs = shap_values[i]
        top_idx = np.argsort(np.abs(contribs))[::-1][:5]
        hold_shap_top5.append([
            {"feature": numeric_cols[j], "value": _safe_num(hold_df.iloc[i][numeric_cols[j]]),
             "contribution": round(float(contribs[j]), 4)}
            for j in top_idx
        ])

    # ---------- event_action_score: 预测收益映射到 0-100 分 ----------
    # 用训练集分位做校准（holdout 的预测分在训练分布里的 percentile）
    train_sorted = np.sort(pred_train)
    hold_scores = np.searchsorted(train_sorted, pred_hold) * 100.0 / max(len(train_sorted), 1)
    train_scores = np.searchsorted(train_sorted, pred_train) * 100.0 / max(len(train_sorted), 1)

    # 置信度：用分数到 50 的绝对距离（极端预测置信度高）
    conf_train = 2 * np.abs(train_scores - 50) / 100.0
    conf_hold = 2 * np.abs(hold_scores - 50) / 100.0

    # ---------- 构造 predictions DataFrame ----------
    model_id = datetime.utcnow().strftime("lgb_event_%Y%m%d_%H%M%S")
    created = datetime.utcnow().isoformat()

    def _pred_rows(events_df, pred, score, conf, split, shap_list=None):
        rows = []
        for i in range(len(events_df)):
            rows.append(dict(
                model_id=model_id,
                institution_id=events_df.iloc[i]["institution_id"],
                stock_code=events_df.iloc[i]["stock_code"],
                notice_date=events_df.iloc[i]["notice_date"],
                report_date=events_df.iloc[i]["report_date"],
                event_action_score=round(float(score[i]), 2),
                predicted_gain=round(float(pred[i]), 3),
                confidence=round(float(conf[i]), 3),
                shap_top5_json=json.dumps(shap_list[i], ensure_ascii=False) if shap_list else None,
                split=split,
                predict_date=events_df.iloc[i]["notice_date"],
                created_at=created,
            ))
        return rows

    pred_rows = _pred_rows(train_df, pred_train, train_scores, conf_train, "train")
    pred_rows += _pred_rows(hold_df, pred_hold, hold_scores, conf_hold, "holdout", hold_shap_top5)

    return dict(
        model_id=model_id,
        metrics_train=metrics_train,
        metrics_hold=metrics_hold,
        feature_importance=feat_imp,
        predictions=pred_rows,
        n_features=len(numeric_cols),
        num_round_best=model.best_iteration,
    )


def _safe_num(v):
    if pd.isna(v):
        return None
    if isinstance(v, (np.integer, np.int64)):
        return int(v)
    if isinstance(v, (np.floating, np.float64)):
        return round(float(v), 4)
    return v


def persist(conn, result: dict, label_col: str, follow_threshold: float, notes: str = ""):
    conn.executescript(TABLE_DDL)
    model_id = result["model_id"]
    created = datetime.utcnow().isoformat()

    # predictions
    cols = ["model_id","institution_id","stock_code","notice_date","report_date",
            "event_action_score","predicted_gain","confidence","shap_top5_json",
            "split","predict_date","created_at"]
    sql = f"INSERT OR REPLACE INTO qlib_event_prediction ({','.join(cols)}) VALUES ({','.join('?'*len(cols))})"
    records = [tuple(r.get(c) for c in cols) for r in result["predictions"]]
    conn.executemany(sql, records)

    # evaluation (train + holdout)
    def _eval_row(dataset, m):
        fi = {k: round(v, 2) for k, v in result["feature_importance"][:20]}
        return (model_id, created, dataset, m["n"], m["ic"], m["rank_ic"],
                m["ks_stat"], m["ks_p"], m["auc_roc"], m["auc_pr"], m["positive_rate"],
                json.dumps(fi, ensure_ascii=False),
                notes or f"label={label_col}, follow_threshold={follow_threshold}, features={result['n_features']}, best_iter={result['num_round_best']}",
                created)

    eval_rows = [
        _eval_row("train", result["metrics_train"]),
        _eval_row("holdout", result["metrics_hold"]),
    ]
    conn.executemany(
        "INSERT OR REPLACE INTO qlib_model_evaluation "
        "(model_id,eval_date,eval_dataset,n_samples,ic,rank_ic,ks_statistic,ks_pvalue,"
        "auc_roc,auc_pr,binary_positive_rate,feature_importance_json,notes,created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        eval_rows,
    )
    conn.commit()
    logger.info("落库完成：model_id=%s, predictions=%d 行, eval_rows=%d", model_id, len(records), len(eval_rows))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", default="gain_60d", choices=["gain_30d", "gain_60d"])
    parser.add_argument("--follow-threshold", type=float, default=8.0,
                        help="二分类正样本阈值（label_gain_60d > 阈值 = follow）")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    conn = get_conn()
    try:
        df = load_data(conn)
        label_col = f"label_{args.label}"
        result = train_and_evaluate(df, label_col, args.follow_threshold)
        if not args.dry_run:
            persist(conn, result, label_col, args.follow_threshold)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
