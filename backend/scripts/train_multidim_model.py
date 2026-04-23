#!/usr/bin/env python3
"""Phase 3: qlib + optuna 多维评分建模 (基于 fact_feature_panel)

管道
  1. 从 fact_feature_panel 读 panel (stock × date × 36 features + label)
  2. 时序切分 train/valid/holdout (70/15/15 date-ordered)
  3. LightGBM + Optuna 搜参 (optimize val IC)
  4. holdout 评估 (IC / RankIC / top-decile lift / winrate)
  5. 模型 + metrics + feature_importance 落到 mart_multidim_model

Optuna 搜参:
  lgb: num_leaves, learning_rate, min_data_in_leaf, feature_fraction, bagging_fraction,
       lambda_l1, lambda_l2, max_depth, num_boost_round
  regime interaction: 是否加 regime_flag 哑变量 + 与关键特征的交互项
  event window: exec_buy_count_N 中 N 的选择 (60 / 90 / 120)
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd
import lightgbm as lgb
import optuna
from scipy.stats import spearmanr

from services.db import get_conn

logger = logging.getLogger("train_multidim")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s | %(message)s")
optuna.logging.set_verbosity(optuna.logging.WARNING)


MODEL_DDL = """
CREATE TABLE IF NOT EXISTS mart_multidim_model (
    model_id TEXT PRIMARY KEY,
    created_at TEXT,
    train_start TEXT, train_end TEXT,
    valid_start TEXT, valid_end TEXT,
    holdout_start TEXT, holdout_end TEXT,
    n_features INTEGER,
    best_params_json TEXT,
    holdout_ic REAL, holdout_rank_ic REAL,
    holdout_top_decile_avg REAL, holdout_bottom_decile_avg REAL,
    holdout_long_short_spread REAL,
    holdout_winrate_top REAL,
    feature_importance_json TEXT,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS mart_multidim_prediction (
    model_id TEXT,
    stock_code TEXT,
    date TEXT,
    pred_score REAL,
    rank_in_date INTEGER,
    percentile REAL,
    PRIMARY KEY (model_id, stock_code, date)
);
"""


FEATURE_COLS = [
    # Pillar B 基础
    'ret_1d', 'ret_5d', 'ret_20d', 'ret_60d',
    'vol_z20d', 'ma_ratio_5', 'ma_ratio_20', 'ma_ratio_60', 'ma_ratio_250',
    'rz_balance', 'rz_chg_5d_pct',
    # Pillar B Alpha158-inspired
    'kmid', 'klen', 'kup', 'klow', 'ksft',
    'vol_ratio_5_20', 'vol_std_5d', 'vol_std_20d',
    'range_pos_20', 'range_pos_60',
    'momentum_diff', 'amount_chg_5d',
    # Pillar A 事件
    'inst_event_count_30d', 'inst_event_count_60d',
    'exec_buy_count_90d', 'exec_buy_ge1_count_90d',
    'lhb_inst_buy_count_30d', 'lhb_inst_buy_count_60d',
    'jgdy_count_60d', 'dzjy_count_60d',
    'days_since_exec_buy', 'days_since_lhb',
    # Pillar C 基本面
    'shareholder_count_qoq', 'inst_count_qoq',
    'fund_count_qoq', 'qfii_count_qoq',
    'yjyg_lower_pct', 'yjyg_upper_pct', 'roe', 'eps_basic',
    # Regime
    'hs300_ret_20d', 'hs300_ret_60d',
]


def load_panel(conn, start_date: str, end_date: str) -> pd.DataFrame:
    logger.info("加载 fact_feature_panel %s ~ %s", start_date, end_date)
    df = pd.read_sql_query(
        """SELECT * FROM fact_feature_panel
           WHERE date >= ? AND date <= ?
             AND forward_ret_20d IS NOT NULL""",
        conn, params=(start_date, end_date),
    )
    logger.info("rows=%d codes=%d dates=%d",
                len(df), df['stock_code'].nunique(), df['date'].nunique())
    # regime_flag 转 one-hot
    if 'regime_flag' in df.columns:
        for flag in ['up', 'flat', 'down']:
            df[f'regime_{flag}'] = (df['regime_flag'] == flag).astype(int)
    return df


def split_time_series(df: pd.DataFrame, train_ratio: float = 0.7, valid_ratio: float = 0.15):
    dates = sorted(df['date'].unique())
    n = len(dates)
    t_end = dates[int(n * train_ratio)]
    v_end = dates[int(n * (train_ratio + valid_ratio))]
    train = df[df['date'] < t_end]
    valid = df[(df['date'] >= t_end) & (df['date'] < v_end)]
    holdout = df[df['date'] >= v_end]
    logger.info("split: train %s ~ %s (%d)  valid %s ~ %s (%d)  holdout %s ~ %s (%d)",
                train['date'].min(), train['date'].max(), len(train),
                valid['date'].min(), valid['date'].max(), len(valid),
                holdout['date'].min(), holdout['date'].max(), len(holdout))
    return train, valid, holdout


def compute_ic(y_true: np.ndarray, y_pred: np.ndarray, dates: np.ndarray) -> tuple[float, float]:
    """daily cross-sectional pearson + spearman IC 平均"""
    daily = pd.DataFrame({'y': y_true, 'yhat': y_pred, 'd': dates})
    pearson = daily.groupby('d').apply(lambda g: g['y'].corr(g['yhat'])).dropna()
    spearman = daily.groupby('d').apply(lambda g: spearmanr(g['y'], g['yhat']).correlation).dropna()
    return float(pearson.mean()), float(spearman.mean())


def decile_metrics(y_true: np.ndarray, y_pred: np.ndarray, dates: np.ndarray) -> dict:
    """每日截面 top / bottom decile 平均 forward return"""
    df = pd.DataFrame({'y': y_true, 'yhat': y_pred, 'd': dates})
    def _decile(g):
        if len(g) < 10:
            return pd.Series({'top_avg': np.nan, 'bot_avg': np.nan})
        q = g['yhat'].quantile([0.1, 0.9])
        top = g[g['yhat'] >= q[0.9]]['y'].mean()
        bot = g[g['yhat'] <= q[0.1]]['y'].mean()
        return pd.Series({'top_avg': top, 'bot_avg': bot})
    per_day = df.groupby('d').apply(_decile)
    top = float(per_day['top_avg'].mean())
    bot = float(per_day['bot_avg'].mean())
    spread = top - bot
    # winrate: top decile 样本里正收益比例
    daily_top_wr = df.groupby('d').apply(
        lambda g: ((g[g['yhat'] >= g['yhat'].quantile(0.9)]['y']) > 0).mean()
    ).dropna()
    wr = float(daily_top_wr.mean())
    return {'top_avg': top, 'bot_avg': bot, 'spread': spread, 'winrate_top': wr}


def train_lgb(X_train, y_train, X_valid, y_valid, params: dict, num_round: int = 500,
              feature_name: list = None) -> lgb.Booster:
    dt = lgb.Dataset(X_train, label=y_train, feature_name=feature_name or 'auto')
    dv = lgb.Dataset(X_valid, label=y_valid, reference=dt, feature_name=feature_name or 'auto')
    model = lgb.train(
        params, dt,
        num_boost_round=num_round,
        valid_sets=[dv],
        callbacks=[lgb.early_stopping(stopping_rounds=50, verbose=False)],
    )
    return model


def make_objective(train_df, valid_df, feature_cols):
    def objective(trial):
        params = {
            'objective': 'regression',
            'metric': 'rmse',
            'learning_rate': trial.suggest_float('learning_rate', 0.01, 0.2, log=True),
            'num_leaves': trial.suggest_int('num_leaves', 15, 127),
            'min_data_in_leaf': trial.suggest_int('min_data_in_leaf', 50, 500),
            'feature_fraction': trial.suggest_float('feature_fraction', 0.6, 1.0),
            'bagging_fraction': trial.suggest_float('bagging_fraction', 0.6, 1.0),
            'bagging_freq': trial.suggest_int('bagging_freq', 0, 10),
            'lambda_l1': trial.suggest_float('lambda_l1', 1e-4, 1.0, log=True),
            'lambda_l2': trial.suggest_float('lambda_l2', 1e-4, 1.0, log=True),
            'max_depth': trial.suggest_int('max_depth', 4, 10),
            'verbose': -1,
        }
        X_train = train_df[feature_cols].values
        y_train = train_df['forward_ret_20d'].values
        X_valid = valid_df[feature_cols].values
        y_valid = valid_df['forward_ret_20d'].values

        model = train_lgb(X_train, y_train, X_valid, y_valid, params, num_round=400,
                          feature_name=feature_cols)
        pred = model.predict(X_valid, num_iteration=model.best_iteration)
        # 目标: valid RankIC (正向)
        _, rank_ic = compute_ic(y_valid, pred, valid_df['date'].values)
        return rank_ic
    return objective


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--start', default='2020-01-01')
    parser.add_argument('--end', default=datetime.now().strftime('%Y-%m-%d'))
    parser.add_argument('--trials', type=int, default=50, help='Optuna 搜参次数')
    parser.add_argument('--regime-aware', action='store_true',
                        help='加入 regime one-hot 作为特征')
    args = parser.parse_args()

    conn = get_conn()
    conn.executescript(MODEL_DDL)

    df = load_panel(conn, args.start, args.end)
    if df.empty:
        logger.error("fact_feature_panel 空或无 label; 先跑 build_feature_panel.py")
        sys.exit(1)

    feature_cols = [c for c in FEATURE_COLS if c in df.columns]
    if args.regime_aware:
        for f in ['regime_up', 'regime_flat', 'regime_down']:
            if f in df.columns:
                feature_cols.append(f)
    logger.info("使用 %d 特征: %s", len(feature_cols), feature_cols)

    train, valid, holdout = split_time_series(df)

    # Optuna
    logger.info("Optuna 启动 %d 次 trial", args.trials)
    t0 = time.time()
    study = optuna.create_study(direction='maximize')
    study.optimize(make_objective(train, valid, feature_cols), n_trials=args.trials)
    logger.info("Optuna 完成. best_value=%.4f params=%s", study.best_value, study.best_params)

    # 用 best params 重训 (train + valid 合并) + holdout 评估
    best = dict(study.best_params)
    best.update({'objective': 'regression', 'metric': 'rmse', 'verbose': -1})
    train_valid = pd.concat([train, valid], ignore_index=True)

    X_tv = train_valid[feature_cols].values
    y_tv = train_valid['forward_ret_20d'].values
    X_ho = holdout[feature_cols].values
    y_ho = holdout['forward_ret_20d'].values

    # no early_stopping in final fit — use fixed num_round
    final_model = lgb.train(best, lgb.Dataset(X_tv, label=y_tv, feature_name=feature_cols),
                             num_boost_round=400)
    pred_ho = final_model.predict(X_ho)

    ic, rank_ic = compute_ic(y_ho, pred_ho, holdout['date'].values)
    dec = decile_metrics(y_ho, pred_ho, holdout['date'].values)

    logger.info("=" * 60)
    logger.info("Holdout: IC=%.4f RankIC=%.4f top-avg=%.4f bot-avg=%.4f spread=%.4f wr_top=%.3f",
                ic, rank_ic, dec['top_avg'], dec['bot_avg'], dec['spread'], dec['winrate_top'])

    # feature importance
    fi = dict(zip(feature_cols, final_model.feature_importance(importance_type='gain').tolist()))
    fi_sorted = sorted(fi.items(), key=lambda x: x[1], reverse=True)
    logger.info("top 10 特征: %s", fi_sorted[:10])

    # 落库
    model_id = f"multidim_v1_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    conn.execute(
        """INSERT INTO mart_multidim_model VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (model_id, datetime.utcnow().isoformat(),
         str(train['date'].min()), str(train['date'].max()),
         str(valid['date'].min()), str(valid['date'].max()),
         str(holdout['date'].min()), str(holdout['date'].max()),
         len(feature_cols),
         json.dumps(best),
         ic, rank_ic,
         dec['top_avg'], dec['bot_avg'], dec['spread'],
         dec['winrate_top'],
         json.dumps(fi),
         f"Optuna {args.trials} trials, regime_aware={args.regime_aware}"),
    )

    # 落 predictions
    pred_df = holdout[['stock_code', 'date']].copy()
    pred_df['model_id'] = model_id
    pred_df['pred_score'] = pred_ho
    pred_df['rank_in_date'] = pred_df.groupby('date')['pred_score'].rank(ascending=False, method='min').astype(int)
    pred_df['percentile'] = pred_df.groupby('date')['pred_score'].rank(pct=True)
    pred_df[['model_id', 'stock_code', 'date', 'pred_score', 'rank_in_date', 'percentile']].to_sql(
        'mart_multidim_prediction', conn, if_exists='append', index=False, method='multi', chunksize=1000,
    )
    conn.commit()

    # 保存 model pkl
    import pickle
    model_dir = Path(__file__).resolve().parent.parent.parent / "data" / "qlib_follow_models"
    model_dir.mkdir(parents=True, exist_ok=True)
    with open(model_dir / f"{model_id}.pkl", 'wb') as f:
        pickle.dump(final_model, f)

    logger.info("模型保存: %s", model_dir / f"{model_id}.pkl")
    logger.info("训练总耗时 %.1f min", (time.time() - t0) / 60)

    conn.close()


if __name__ == "__main__":
    main()
