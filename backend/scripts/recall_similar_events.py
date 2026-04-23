#!/usr/bin/env python3
"""W5：相似事件召回（LightGBM leaf embedding 最近邻）。

非黑盒第二支柱：给定一条待预测事件，从训练集召回 Top-5 最相似历史事件，
展示它们的实际 gain_60d，让用户看到"类似情形过去赚/亏多少"。

相似度定义：用 LightGBM 的 pred_leaf 得到每棵树的叶子 ID 向量，
两事件相似度 = 在同一棵树落到同一叶子的棵数 / 总树数（0-1 区间）。

落表：fact_similar_events（每 holdout 事件 × 5 条召回）
    PRIMARY KEY (model_id, query_event_uid, rank)
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

from services.db import get_conn
from scripts.train_event_qlib import EXCLUDE_COLS, load_data

logger = logging.getLogger("recall_similar_events")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s | %(message)s")


TABLE_DDL = """
CREATE TABLE IF NOT EXISTS fact_similar_events (
    model_id           TEXT NOT NULL,
    query_institution  TEXT NOT NULL,
    query_stock        TEXT NOT NULL,
    query_notice_date  TEXT NOT NULL,
    query_report_date  TEXT NOT NULL,
    rank               INTEGER NOT NULL,     -- 1..5
    similarity         REAL,
    similar_institution TEXT,
    similar_stock      TEXT,
    similar_notice_date TEXT,
    similar_report_date TEXT,
    similar_gain_60d   REAL,
    similar_maxdd_60d  REAL,
    computed_at        TEXT,
    PRIMARY KEY (model_id, query_institution, query_stock, query_notice_date, query_report_date, rank)
);
CREATE INDEX IF NOT EXISTS idx_fse_query ON fact_similar_events(query_institution, query_stock, query_notice_date);
"""


def train_retrieval_model(train_df, hold_df, numeric_cols, label_col, best_params):
    """用最佳超参重训一次仅用于提取 leaf embedding（同 W5 模型）"""
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
    X_train = train_df[numeric_cols].values
    y_train = train_df[label_col].values
    X_hold = hold_df[numeric_cols].values
    y_hold = hold_df[label_col].values
    dtrain = lgb.Dataset(X_train, label=y_train, feature_name=numeric_cols)
    dvalid = lgb.Dataset(X_hold, label=y_hold, feature_name=numeric_cols, reference=dtrain)
    model = lgb.train(
        params, dtrain, num_boost_round=best_params["num_boost_round"],
        valid_sets=[dvalid], valid_names=["holdout"],
        callbacks=[lgb.early_stopping(stopping_rounds=30), lgb.log_evaluation(period=0)],
    )
    return model


def compute_similarities(model, train_df, hold_df, numeric_cols, top_k=5, batch_size=500):
    """对每条 holdout 事件找 train 里 Top-K 相似事件。

    leaf embedding: 每棵树一个整数叶子 ID；相似度 = 叶子相同比例。
    为避免 N*M 爆炸（5937 × 23748 ≈ 1.4 亿对），采用批处理 + numpy 广播。
    """
    X_train = train_df[numeric_cols].values
    X_hold = hold_df[numeric_cols].values
    # shape (n_train, n_trees) / (n_hold, n_trees)
    leaf_train = model.predict(X_train, pred_leaf=True, num_iteration=model.best_iteration)
    leaf_hold = model.predict(X_hold, pred_leaf=True, num_iteration=model.best_iteration)
    n_trees = leaf_train.shape[1]
    logger.info("leaf embedding: train=%s, hold=%s, trees=%d", leaf_train.shape, leaf_hold.shape, n_trees)

    recalls = []
    n_hold = leaf_hold.shape[0]
    for start in range(0, n_hold, batch_size):
        end = min(start + batch_size, n_hold)
        batch = leaf_hold[start:end]  # (B, T)
        # matches: (B, N_train) 每个 holdout 事件 vs 每条 train 的叶子匹配数
        # 广播 (B,1,T) vs (1,N,T) → (B,N,T) 内存爆。用循环按 tree 累加：
        matches = np.zeros((batch.shape[0], leaf_train.shape[0]), dtype=np.int32)
        for t in range(n_trees):
            matches += (batch[:, t][:, None] == leaf_train[:, t][None, :]).astype(np.int32)
        sims = matches / float(n_trees)  # (B, N_train)
        # 每行取 top_k
        top_idx = np.argpartition(-sims, top_k, axis=1)[:, :top_k]
        for i in range(batch.shape[0]):
            row_sims = sims[i, top_idx[i]]
            order = np.argsort(-row_sims)
            for rank_, k in enumerate(order, start=1):
                train_pos = top_idx[i][k]
                recalls.append({
                    "query_row": start + i,
                    "train_row": int(train_pos),
                    "rank": rank_,
                    "similarity": float(row_sims[k]),
                })
        if (start // batch_size) % 5 == 0:
            logger.info("  进度 %d/%d", end, n_hold)
    return recalls


def persist(conn, model_id, train_df, hold_df, recalls):
    conn.executescript(TABLE_DDL)
    created = datetime.utcnow().isoformat()
    rows = []
    for r in recalls:
        q = hold_df.iloc[r["query_row"]]
        s = train_df.iloc[r["train_row"]]
        rows.append((
            model_id,
            q["institution_id"], q["stock_code"], q["notice_date"], q["report_date"],
            r["rank"], round(r["similarity"], 4),
            s["institution_id"], s["stock_code"], s["notice_date"], s["report_date"],
            None if pd.isna(s["label_gain_60d"]) else round(float(s["label_gain_60d"]), 3),
            None if pd.isna(s["label_max_drawdown_60d"]) else round(float(s["label_max_drawdown_60d"]), 3),
            created,
        ))
    conn.execute("DELETE FROM fact_similar_events WHERE model_id = ?", (model_id,))
    conn.executemany(
        "INSERT OR REPLACE INTO fact_similar_events VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        rows,
    )
    conn.commit()
    logger.info("落库：%d 行相似事件召回（model_id=%s）", len(rows), model_id)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", default="gain_60d", choices=["gain_30d", "gain_60d"])
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--study-name", default="event_qlib_ic_tuning")
    args = parser.parse_args()

    conn = get_conn()
    try:
        df = load_data(conn)
        label_col = f"label_{args.label}"
        mask = df[label_col].notna()
        data = df[mask].copy().sort_values("notice_date").reset_index(drop=True)
        numeric_cols = [c for c in data.columns
                        if c not in EXCLUDE_COLS and pd.api.types.is_numeric_dtype(data[c])]
        cut = int(len(data) * 0.8)
        train_df = data.iloc[:cut].reset_index(drop=True)
        hold_df = data.iloc[cut:].reset_index(drop=True)

        # 从 Optuna study 拿最佳超参
        import optuna
        optuna.logging.set_verbosity(optuna.logging.WARNING)
        storage = f"sqlite:///{Path(__file__).parent.parent.parent}/data/optuna_event_qlib.db"
        study = optuna.load_study(study_name=args.study_name, storage=storage)
        best = study.best_params
        logger.info("使用 Optuna 最佳参数（IC=%.4f）: %s", study.best_value, best)

        model = train_retrieval_model(train_df, hold_df, numeric_cols, label_col, best)

        # 从最新 qlib_event_prediction 找 model_id
        row = conn.execute(
            "SELECT model_id FROM qlib_model_evaluation "
            "WHERE eval_dataset='holdout' ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        model_id = row["model_id"] if row else f"lgb_event_tuned_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"
        logger.info("关联 model_id=%s", model_id)

        recalls = compute_similarities(model, train_df, hold_df, numeric_cols, top_k=args.top_k)
        persist(conn, model_id, train_df, hold_df, recalls)

        # 样例展示
        sample_q = hold_df.iloc[0]
        q_recalls = [r for r in recalls if r["query_row"] == 0]
        logger.info("\n=== 样例：Query = %s / %s (%s) ===",
                    sample_q["institution_id"], sample_q["stock_code"], sample_q["notice_date"])
        for r in q_recalls:
            s = train_df.iloc[r["train_row"]]
            logger.info("  rank %d sim=%.3f  %s/%s (%s)  actual gain_60d=%s",
                        r["rank"], r["similarity"],
                        s["institution_id"], s["stock_code"], s["notice_date"],
                        None if pd.isna(s["label_gain_60d"]) else round(float(s["label_gain_60d"]), 2))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
