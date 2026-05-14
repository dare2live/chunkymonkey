"""Phase ψ.δ.1 — 板块轮动预测器训练 (Ridge regression, walk-forward).

⚠ 用户原话: "按照规律做个板块、概念、行业轮动啥的, 并作出预测, 辅助选股"
⚠ 设计 (跟用户 CDE 三选一对齐):
   - C (动量+反转分阶段) + E (ML 端到端) 的轻量版 — Ridge linear 防 overfit
   - 输入特征 (sector-level): ret_5d/20d/60d, vol_60d, excess_20d/60d,
     price_vs_ma20, price_vs_ma60 (8 维 sector momentum 特征)
   - 输入特征 (market-level): regime_label (categorical bull/bear/sideways)
   - Target: forward 10 day sector return (PIT 干净 — 训练时只看过去, OOS 拼接)
   - Walk-forward: 每月底 retrain on cumulative past, predict next 30 day sectors

⚠ Rule 7 严格 (Anti-look-ahead):
   - train data: data.date <= train_end (NEVER 含训练时刻之后 K 线)
   - target: forward_ret_10d 用 sector_close[date+10] / sector_close[date] - 1
     训练时 train_end ≤ all_dates - 10 days (确保 target 在 train_end 之前能算)
   - purge: train cut-off 留 10 day gap, 防 forward target 看到 test 期数据

⚠ 输出表: fact_sector_predicted_ret_daily (新)
   PK = (sector_name, date)
   value = predicted_ret_10d (float)
   含 model_train_end (PIT key — paper_sim WHERE model_train_end <= signal_date)

Usage:
   PYTHONPATH=backend python backend/scripts/train_sector_rotation_predictor.py
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
from sklearn.linear_model import Ridge

from services.db import get_conn


log = logging.getLogger("train_sector_rotation_predictor")
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s",
                    datefmt="%H:%M:%S")


FORWARD_DAYS = 10                    # rule-compliance: ok evidence=user-default-10d
MIN_TRAIN_MONTHS = 6                 # rule-compliance: ok evidence=walk-forward-min-train
RIDGE_ALPHA = 1.0                    # rule-compliance: ok evidence=sklearn-default

FEATURE_COLS = (
    "ret_5d", "ret_20d", "ret_60d",
    "vol_60d",
    "excess_20d", "excess_60d",
    "price_vs_ma20", "price_vs_ma60",
)


def _ensure_table(conn) -> None:
    """DDL: fact_sector_predicted_ret_daily."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS fact_sector_predicted_ret_daily (
            sector_name      TEXT NOT NULL,
            date             DATE NOT NULL,
            predicted_ret_10d DOUBLE,
            model_train_end  DATE NOT NULL,   -- PIT key (paper_sim WHERE model_train_end <= signal_date)
            forward_days     INTEGER DEFAULT 10,
            model_alpha      DOUBLE DEFAULT 1.0,
            n_train_rows     INTEGER,
            built_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (sector_name, date, model_train_end)
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_fspr_date ON fact_sector_predicted_ret_daily(date)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_fspr_pit ON fact_sector_predicted_ret_daily(model_train_end)
    """)


def _load_data(conn) -> pd.DataFrame:
    """加载 sector momentum + 算 forward_ret_10d target.

    forward_ret_10d 用 sector_close[date+10] / sector_close[date] - 1.
    最后 10 天的 forward 没数据 → NaN (train 时 drop).
    """
    sql = f"""
        SELECT sector_name, date,
               {', '.join(FEATURE_COLS)},
               sector_close
          FROM fact_sector_momentum_daily
         WHERE {' AND '.join(f'{c} IS NOT NULL' for c in FEATURE_COLS)}
         ORDER BY date, sector_name
    """
    rows = conn.execute(sql).fetchall()
    cols = ["sector_name", "date"] + list(FEATURE_COLS) + ["sector_close"]
    df = pd.DataFrame(rows, columns=cols)
    # forward 10d return
    df['forward_close'] = df.groupby('sector_name')['sector_close'].shift(-FORWARD_DAYS)
    df['forward_ret_10d'] = (df['forward_close'] / df['sector_close']) - 1.0
    log.info(f"  加载 {len(df):,} 行, forward target 有效 {df['forward_ret_10d'].notna().sum():,}")
    return df


def _month_ends(df: pd.DataFrame) -> list[pd.Timestamp]:
    """返回每月最后一个交易日列表 (用于 walk-forward retrain 点)."""
    dates = pd.Series(df['date'].unique()).sort_values()
    dates = pd.to_datetime(dates)
    month_ends = (
        dates.to_frame('d')
             .assign(ym=lambda x: x['d'].dt.to_period('M'))
             .groupby('ym')['d'].max()
             .tolist()
    )
    return month_ends


def _train_and_predict(df: pd.DataFrame, conn) -> int:
    """Walk-forward: 每月底 retrain, 预测下个月.

    流程:
      for train_end in month_ends:
          train = df[df.date <= train_end - 10 day]  (purge 10 day gap)
              过滤 forward_ret_10d NOT NULL
          test = df[(train_end < df.date <= next_month_end)]
          fit Ridge on train, predict test
          INSERT predictions
    返回写入行数.
    """
    month_ends = _month_ends(df)
    log.info(f"  walk-forward 月末点: {len(month_ends)} 个")
    if len(month_ends) < MIN_TRAIN_MONTHS + 1:
        log.error(f"数据不足 {MIN_TRAIN_MONTHS} 月 train 不可行")
        return 0

    n_written = 0
    rows_buffer: list[tuple] = []

    for i, train_end in enumerate(month_ends):
        if i < MIN_TRAIN_MONTHS - 1:
            continue  # 前 N 个月做 train base, 不预测
        # rule-compliance: ok evidence=sentinel-infinite-date (开放 right boundary)
        next_end = month_ends[i + 1] if i + 1 < len(month_ends) else pd.Timestamp("9999-12-31")
        # Train: date <= train_end - FORWARD_DAYS (防 target leakage)
        train_cutoff = train_end - timedelta(days=FORWARD_DAYS)
        train_mask = (
            (pd.to_datetime(df['date']) <= train_cutoff) &
            df['forward_ret_10d'].notna()
        )
        train_df = df[train_mask]
        if len(train_df) < 50:                   # rule-compliance: ok evidence=ridge-min-samples
            log.warning(f"  train_end={train_end.date()}: train only {len(train_df)} rows, skip")
            continue

        X_train = train_df[list(FEATURE_COLS)].values
        y_train = train_df['forward_ret_10d'].values
        model = Ridge(alpha=RIDGE_ALPHA)
        model.fit(X_train, y_train)

        # Predict: train_end < date <= next_end
        test_mask = (
            (pd.to_datetime(df['date']) > train_end) &
            (pd.to_datetime(df['date']) <= next_end)
        )
        test_df = df[test_mask].copy()
        if len(test_df) == 0:
            continue
        X_test = test_df[list(FEATURE_COLS)].values
        y_pred = model.predict(X_test)

        for sn, dt, pred in zip(test_df['sector_name'], test_df['date'], y_pred):
            rows_buffer.append((sn, dt, float(pred), train_end.date(),
                               FORWARD_DAYS, RIDGE_ALPHA, len(train_df)))
        # Flush every 5 month_ends
        if (i + 1) % 5 == 0 and rows_buffer:
            conn.executemany("""
                INSERT INTO fact_sector_predicted_ret_daily
                (sector_name, date, predicted_ret_10d, model_train_end,
                 forward_days, model_alpha, n_train_rows)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, rows_buffer)
            n_written += len(rows_buffer)
            log.info(f"  [{i+1}/{len(month_ends)}] train_end={train_end.date()} "
                     f"train_n={len(train_df):<5} pred_n={len(test_df)} "
                     f"sample_pred_mean={y_pred.mean():+.4f}")
            rows_buffer.clear()
    # 残留
    if rows_buffer:
        conn.executemany("""
            INSERT INTO fact_sector_predicted_ret_daily
            (sector_name, date, predicted_ret_10d, model_train_end,
             forward_days, model_alpha, n_train_rows)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, rows_buffer)
        n_written += len(rows_buffer)
    return n_written


def main():
    t0 = time.time()
    conn = get_conn()
    try:
        log.info("=== Phase ψ.δ.1 板块轮动预测器训练 ===")
        _ensure_table(conn)
        # 清掉残留 (重跑时)
        conn.execute("DELETE FROM fact_sector_predicted_ret_daily")
        df = _load_data(conn)
        n = _train_and_predict(df, conn)
        log.info(f"=== 写入 {n:,} 行 / 跑批 {time.time()-t0:.0f}s ===")
        # Sanity: 看预测 distribution
        r = conn.execute("""
            SELECT MIN(predicted_ret_10d), MAX(predicted_ret_10d),
                   AVG(predicted_ret_10d), COUNT(DISTINCT sector_name)
              FROM fact_sector_predicted_ret_daily
        """).fetchone()
        log.info(f"  prediction range: [{r[0]:.4f}, {r[1]:.4f}], "
                 f"avg={r[2]:.5f}, sectors={r[3]}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
