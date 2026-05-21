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
    trade_date_dt     DATE,
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


LAMBDAMART_V6_PREDICTIONS_TABLE = "mart_p0b_lambdamart_v6_predictions"


LAMBDAMART_V6_PREDICTIONS_DDL = """
CREATE TABLE IF NOT EXISTS mart_p0b_lambdamart_v6_predictions (
    stock_code        TEXT NOT NULL,
    signal_date       DATE NOT NULL,
    trade_date_dt     DATE,
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


LAMBDAMART_V6_PREDICTIONS_INDEX_DDL = """
CREATE INDEX IF NOT EXISTS idx_p0b_lm_v6_signal_date
    ON mart_p0b_lambdamart_v6_predictions(signal_date);
CREATE INDEX IF NOT EXISTS idx_p0b_lm_v6_stock
    ON mart_p0b_lambdamart_v6_predictions(stock_code, signal_date);
"""


FACT_MODEL_TRAIN_LOG_TABLE = "fact_model_train_log"
FACT_MODEL_TRAIN_LOG_WINDOW_TABLE = "fact_model_train_log_window"


FACT_MODEL_TRAIN_LOG_DDL = """
CREATE TABLE IF NOT EXISTS fact_model_train_log (
    model_id           TEXT NOT NULL,
    run_id             TEXT NOT NULL,
    model_version      TEXT,
    feature_version    TEXT,
    label_version      TEXT,
    train_start        DATE,
    train_end          DATE,
    n_train_rows       INTEGER,
    n_features         INTEGER,
    is_rank_ic         DOUBLE,
    is_rank_ic_ir      DOUBLE,
    is_ndcg5           DOUBLE,
    is_ndcg10          DOUBLE,
    is_ndcg20          DOUBLE,
    oos_rank_ic_avg    DOUBLE,
    oos_rank_ic_ir     DOUBLE,
    seed               INTEGER,
    n_trials           INTEGER,
    n_windows          INTEGER,
    optuna_best_value  DOUBLE,
    walk_forward_mode  TEXT,
    metrics_json       TEXT,
    built_at           TEXT NOT NULL,
    PRIMARY KEY (model_id, run_id)
);
"""


FACT_MODEL_TRAIN_LOG_INDEX_DDL = """
CREATE INDEX IF NOT EXISTS idx_fact_model_train_log_model_built
    ON fact_model_train_log(model_id, built_at);
"""


FACT_MODEL_TRAIN_LOG_WINDOW_DDL = """
CREATE TABLE IF NOT EXISTS fact_model_train_log_window (
    model_id           TEXT NOT NULL,
    replay_id          TEXT NOT NULL,
    params_hash        TEXT NOT NULL,
    window_idx         INTEGER NOT NULL,
    window_key         TEXT NOT NULL,
    model_version      TEXT,
    feature_version    TEXT,
    label_version      TEXT,
    walk_forward_mode  TEXT,
    train_start        DATE NOT NULL,
    train_end          DATE NOT NULL,
    test_start         DATE NOT NULL,
    test_end           DATE NOT NULL,
    n_train_rows       INTEGER NOT NULL,
    n_test_rows        INTEGER NOT NULL,
    n_features         INTEGER,
    train_metrics_json TEXT NOT NULL,
    oos_metrics_json   TEXT NOT NULL,
    metrics_json       TEXT NOT NULL,
    checkpoint_status  TEXT NOT NULL,
    built_at           TEXT NOT NULL,
    PRIMARY KEY (model_id, replay_id, window_key)
);
"""


FACT_MODEL_TRAIN_LOG_WINDOW_INDEX_DDL = """
CREATE INDEX IF NOT EXISTS idx_fact_model_train_log_window_model_params
    ON fact_model_train_log_window(model_id, params_hash, checkpoint_status);
"""


def _ensure_trade_date_dt_column(conn, table_name: str, source_column: str) -> None:
    """Phase A fallback: DuckDB cannot ALTER ADD STORED generated columns."""

    try:
        conn.execute(f"ALTER TABLE {table_name} ADD COLUMN IF NOT EXISTS trade_date_dt DATE")
    except Exception as exc:
        msg = str(exc).lower()
        if "already exists" not in msg and "duplicate" not in msg:
            raise
    conn.execute(
        f"""
        UPDATE {table_name}
           SET trade_date_dt = CAST({source_column} AS DATE)
         WHERE trade_date_dt IS NULL
           AND {source_column} IS NOT NULL
        """
    )


def create_p0b_ddl(conn) -> None:
    conn.execute(OOS_PREDICTIONS_DDL)
    conn.execute(WALKFORWARD_EVAL_DDL)
    conn.execute(OOS_PREDICTIONS_INDEX_DDL)
    _ensure_trade_date_dt_column(conn, "mart_p0b_oos_predictions", "signal_date")


def create_lambdamart_v6_predictions_ddl(conn) -> None:
    conn.execute(LAMBDAMART_V6_PREDICTIONS_DDL)
    conn.execute(LAMBDAMART_V6_PREDICTIONS_INDEX_DDL)
    _ensure_trade_date_dt_column(conn, LAMBDAMART_V6_PREDICTIONS_TABLE, "signal_date")


def create_fact_model_train_log_ddl(conn) -> None:
    conn.execute(FACT_MODEL_TRAIN_LOG_DDL)
    conn.execute(FACT_MODEL_TRAIN_LOG_INDEX_DDL)


def create_fact_model_train_log_window_ddl(conn) -> None:
    conn.execute(FACT_MODEL_TRAIN_LOG_WINDOW_DDL)
    conn.execute(FACT_MODEL_TRAIN_LOG_WINDOW_INDEX_DDL)
