#!/usr/bin/env python3
"""Phase 4: 每日 topK 推荐生成 (基于 mart_multidim_model 最佳模型)

输入
  - fact_feature_panel (features for scoring, 最新一天)
  - mart_multidim_model (best model_id from last training run)
  - data/qlib_follow_models/<model_id>.pkl

输出
  - mart_daily_recommendation (snapshot_date, stock_code, rank, pred_score, percentile,
                               regime_flag, key_features_json)
  - 可选: 按 regime × topK 分组输出, 满足用户 "down regime 高管增持" 等语义 topK
"""
from __future__ import annotations

import argparse
import json
import logging
import pickle
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from services.db import get_conn

logger = logging.getLogger("daily_topk")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s | %(message)s")


DDL = """
CREATE TABLE IF NOT EXISTS mart_daily_recommendation (
    snapshot_date TEXT NOT NULL,
    stock_code    TEXT NOT NULL,
    model_id      TEXT,
    rank_in_date  INTEGER,
    pred_score    REAL,
    percentile    REAL,
    regime_flag   TEXT,
    key_features_json TEXT,
    built_at      TEXT,
    PRIMARY KEY (snapshot_date, stock_code, model_id)
);
CREATE INDEX IF NOT EXISTS idx_dr_date ON mart_daily_recommendation(snapshot_date);
CREATE INDEX IF NOT EXISTS idx_dr_rank ON mart_daily_recommendation(snapshot_date, rank_in_date);
"""


FEATURE_COLS = [
    'ret_1d', 'ret_5d', 'ret_20d', 'ret_60d',
    'vol_z20d', 'ma_ratio_5', 'ma_ratio_20', 'ma_ratio_60', 'ma_ratio_250',
    'rz_balance', 'rz_chg_5d_pct',
    'inst_event_count_30d', 'inst_event_count_60d',
    'exec_buy_count_90d', 'exec_buy_ge1_count_90d',
    'lhb_inst_buy_count_30d', 'lhb_inst_buy_count_60d',
    'jgdy_count_60d', 'dzjy_count_60d',
    'days_since_exec_buy', 'days_since_lhb',
    'shareholder_count_qoq', 'inst_count_qoq',
    'fund_count_qoq', 'qfii_count_qoq',
    'yjyg_lower_pct', 'yjyg_upper_pct', 'roe', 'eps_basic',
    'hs300_ret_20d', 'hs300_ret_60d',
]


def load_model(model_id: str):
    model_dir = Path(__file__).resolve().parent.parent.parent / "data" / "qlib_follow_models"
    path = model_dir / f"{model_id}.pkl"
    if not path.exists():
        raise FileNotFoundError(path)
    with open(path, 'rb') as f:
        return pickle.load(f)


def load_latest_model_id(conn) -> str:
    row = conn.execute("""
        SELECT model_id FROM mart_multidim_model
        ORDER BY created_at DESC LIMIT 1
    """).fetchone()
    if not row:
        raise RuntimeError("mart_multidim_model 无记录, 请先训练")
    return row[0]


def get_top_features(model, feature_cols: list[str], top_k: int = 5) -> list[tuple[str, float]]:
    imp = model.feature_importance(importance_type='gain')
    pairs = sorted(zip(feature_cols, imp.tolist()), key=lambda x: x[1], reverse=True)
    return pairs[:top_k]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--model-id', default=None, help='指定 model_id (默认取最新)')
    parser.add_argument('--date', default=None, help='YYYY-MM-DD, 默认 panel 最新')
    parser.add_argument('--top-k', type=int, default=50)
    parser.add_argument('--by-regime', action='store_true',
                        help='按 regime_flag 分组各出 top-K')
    args = parser.parse_args()

    conn = get_conn()
    conn.executescript(DDL)

    model_id = args.model_id or load_latest_model_id(conn)
    logger.info("使用模型 %s", model_id)
    model = load_model(model_id)

    # 取 panel 的目标日期
    if args.date:
        target_date = args.date
    else:
        row = conn.execute("SELECT MAX(date) FROM fact_feature_panel").fetchone()
        target_date = row[0]
    logger.info("target_date=%s", target_date)

    df = pd.read_sql_query(
        "SELECT * FROM fact_feature_panel WHERE date = ?",
        conn, params=(target_date,),
    )
    if df.empty:
        logger.error("fact_feature_panel 里没有 %s 的行", target_date)
        return
    logger.info("panel rows %d for %s", len(df), target_date)

    # 训练使用了 regime-aware (one-hot), 评分需对齐
    if 'regime_flag' in df.columns:
        for flag in ['up', 'flat', 'down']:
            df[f'regime_{flag}'] = (df['regime_flag'] == flag).astype(int)

    # 按训练时的固定顺序构造 X (LightGBM 若用 ndarray 训练,
    # feature_name 会是 Column_0..N, 无法回推真实列名, 只能按 train 顺序)
    n_trained = model.num_feature() if hasattr(model, 'num_feature') else None
    trained_feat_names = list(model.feature_name()) if hasattr(model, 'feature_name') else []
    is_generic_names = all(n.startswith('Column_') for n in trained_feat_names) if trained_feat_names else True

    if is_generic_names:
        # 训练时的顺序: FEATURE_COLS (按存在性过滤) + regime one-hot (如训练用了 regime_aware)
        candidate = [c for c in FEATURE_COLS if c in df.columns]
        regime_onehot = [f for f in ['regime_up', 'regime_flat', 'regime_down'] if f in df.columns]
        # 先不加 one-hot; 对齐训练时特征数
        if n_trained == len(candidate):
            feature_cols = candidate
        elif n_trained == len(candidate) + len(regime_onehot):
            feature_cols = candidate + regime_onehot
        else:
            logger.warning("训练特征数 %s ≠ 候选 %d (含 regime %d)", n_trained, len(candidate), len(regime_onehot))
            feature_cols = candidate + regime_onehot
    else:
        feature_cols = trained_feat_names

    logger.info("使用 %d 特征评分 (model n_feat=%s)", len(feature_cols), n_trained)
    X = df[feature_cols].values
    # LightGBM 对 Column_N 模型 predict 时忽略 feature_name, 按顺序吃 X
    df['pred_score'] = model.predict(X, predict_disable_shape_check=False)
    df = df.sort_values('pred_score', ascending=False).reset_index(drop=True)
    df['rank_in_date'] = df.index + 1
    df['percentile'] = df['pred_score'].rank(pct=True)

    # top features (模型级, 所有股共享)
    top_feats = get_top_features(model, feature_cols, top_k=8)
    features_json = json.dumps({'model_top_features': [{'name': n, 'importance': v} for n, v in top_feats]},
                                 ensure_ascii=False)

    # 写入
    output = df[['stock_code', 'rank_in_date', 'pred_score', 'percentile', 'regime_flag']].copy()
    output['snapshot_date'] = target_date
    output['model_id'] = model_id
    output['key_features_json'] = features_json
    output['built_at'] = datetime.utcnow().isoformat()

    # 限制 top_k
    if not args.by_regime:
        output = output.head(args.top_k)
    else:
        by_reg = output.groupby('regime_flag', group_keys=False).apply(lambda g: g.head(args.top_k))
        output = by_reg.reset_index(drop=True)

    logger.info("写入 %d 条推荐", len(output))
    # INSERT OR REPLACE
    for _, r in output.iterrows():
        conn.execute(
            """INSERT OR REPLACE INTO mart_daily_recommendation
               (snapshot_date, stock_code, model_id, rank_in_date, pred_score, percentile,
                regime_flag, key_features_json, built_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (r['snapshot_date'], r['stock_code'], r['model_id'],
             int(r['rank_in_date']), float(r['pred_score']), float(r['percentile']),
             r.get('regime_flag'), r['key_features_json'], r['built_at']),
        )
    conn.commit()

    # 输出 top-20 预览
    logger.info("=" * 60)
    logger.info("Top 20 推荐 (snapshot=%s, regime=%s):", target_date,
                df['regime_flag'].iloc[0] if 'regime_flag' in df.columns else 'n/a')
    for i, r in output.head(20).iterrows():
        logger.info("  [%d] %s  score=%.4f  pct=%.3f  regime=%s",
                    r['rank_in_date'], r['stock_code'],
                    r['pred_score'], r['percentile'], r.get('regime_flag') or '-')

    conn.close()


if __name__ == "__main__":
    main()
