"""P0b 输出表 DDL.

`mart_p0b_oos_predictions`: 每行 = (stock_code, signal_date, score, label) — OOS 预测.
P0c selector 用此表 ORDER BY score DESC 取 top-K candidates.

`mart_p0b_walkforward_eval`: 每行 = 单 window 的 evaluation (train/test 范围 + RankIC).
"""
from __future__ import annotations


OOS_PREDICTIONS_DDL = """
CREATE TABLE IF NOT EXISTS mart_p0b_oos_predictions (
    stock_code        TEXT NOT NULL,
    signal_date       DATE NOT NULL,
    score             DOUBLE,
    fwd_cost_after_5d  DOUBLE,
    fwd_cost_after_10d DOUBLE,
    fwd_cost_after_20d DOUBLE,
    model_id          TEXT NOT NULL,
    model_version     TEXT NOT NULL,
    feature_version   TEXT NOT NULL,
    label_version     TEXT NOT NULL,
    walk_forward_mode TEXT NOT NULL,
    train_start       DATE,
    train_end         DATE,
    test_start        DATE,
    test_end          DATE,
    is_final_holdout  BOOLEAN,
    built_at          TEXT NOT NULL,
    PRIMARY KEY (stock_code, signal_date, model_id)
);
"""


WALKFORWARD_EVAL_DDL = """
CREATE TABLE IF NOT EXISTS mart_p0b_walkforward_eval (
    run_id            TEXT NOT NULL,
    window_idx        INTEGER NOT NULL,
    model_id          TEXT NOT NULL,
    model_version     TEXT NOT NULL,
    feature_version   TEXT NOT NULL,
    label_version     TEXT NOT NULL,
    walk_forward_mode TEXT NOT NULL,
    train_start       DATE,
    train_end         DATE,
    test_start        DATE,
    test_end          DATE,
    n_train           INTEGER,
    n_test            INTEGER,
    rank_ic           DOUBLE,
    rank_ic_ir        DOUBLE,
    is_final_holdout  BOOLEAN,
    built_at          TEXT NOT NULL,
    PRIMARY KEY (run_id, window_idx, model_id)
);
"""


OOS_PREDICTIONS_INDEX_DDL = """
CREATE INDEX IF NOT EXISTS idx_p0b_oos_signal_date ON mart_p0b_oos_predictions(signal_date);
CREATE INDEX IF NOT EXISTS idx_p0b_oos_stock      ON mart_p0b_oos_predictions(stock_code, signal_date);
"""


def create_p0b_ddl(conn) -> None:
    conn.execute(OOS_PREDICTIONS_DDL)
    conn.execute(WALKFORWARD_EVAL_DDL)
    conn.execute(OOS_PREDICTIONS_INDEX_DDL)
