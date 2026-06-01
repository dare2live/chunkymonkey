"""Formula Engine 专属 DDL — 不污染 services/db.py 中心 init_db。

每个 build 脚本启动时调用 ensure_formula_tables(conn) 幂等建表。
"""
from __future__ import annotations


FACT_TECHNICAL_TRIGGER_DDL = """
CREATE TABLE IF NOT EXISTS fact_technical_trigger (
    stock_code         TEXT NOT NULL,
    date               TEXT NOT NULL,
    formula_id         TEXT NOT NULL,
    formula_variant    TEXT NOT NULL,
    strength           REAL NOT NULL,
    state              TEXT,
    reason_codes_json  TEXT,
    built_at           TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (stock_code, date, formula_id)
);
CREATE INDEX IF NOT EXISTS idx_ftt_date    ON fact_technical_trigger(date);
CREATE INDEX IF NOT EXISTS idx_ftt_formula ON fact_technical_trigger(formula_id);
CREATE INDEX IF NOT EXISTS idx_ftt_code    ON fact_technical_trigger(stock_code);
"""


MART_MACD_STATE_HISTORY_DDL = """
CREATE TABLE IF NOT EXISTS mart_macd_state_history (
    stock_code         TEXT NOT NULL,
    date               TEXT NOT NULL,
    formula_id         TEXT NOT NULL,
    formula_variant    TEXT NOT NULL,
    state              TEXT NOT NULL,
    strength           REAL NOT NULL,
    reason_codes_json  TEXT,
    built_at           TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (stock_code, date, formula_id, formula_variant, state)
);
CREATE INDEX IF NOT EXISTS idx_mmdh_date    ON mart_macd_state_history(date);
CREATE INDEX IF NOT EXISTS idx_mmdh_formula ON mart_macd_state_history(formula_id);
CREATE INDEX IF NOT EXISTS idx_mmdh_code    ON mart_macd_state_history(stock_code);
"""


MART_FORMULA_HORIZON_EVIDENCE_DDL = """
CREATE TABLE IF NOT EXISTS mart_formula_horizon_evidence (
    formula_id            TEXT NOT NULL,
    formula_variant       TEXT NOT NULL,
    holding_days          INTEGER NOT NULL,
    eval_start_date       TEXT NOT NULL,
    eval_end_date         TEXT NOT NULL,
    n_signals             INTEGER NOT NULL,
    n_matured             INTEGER NOT NULL,
    win_rate              REAL,
    avg_ret               REAL,
    avg_dd                REAL,
    median_ret            REAL,
    calmar                REAL,
    sharpe                REAL,
    optimal_params_json   TEXT,
    last_optimized_at     TEXT,
    built_at              TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (formula_id, formula_variant, holding_days, eval_start_date, eval_end_date)
);
"""


FACT_STOCK_TECHNICAL_STAGE_DDL = """
CREATE TABLE IF NOT EXISTS fact_stock_technical_stage (
    stock_code   TEXT NOT NULL,
    date         TEXT NOT NULL,
    stage        TEXT NOT NULL,
    built_at     TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (stock_code, date)
);
CREATE INDEX IF NOT EXISTS idx_fsts_stage ON fact_stock_technical_stage(stage);
CREATE INDEX IF NOT EXISTS idx_fsts_date  ON fact_stock_technical_stage(date);
"""


MART_STAGE_FORMULA_FITNESS_DDL = """
CREATE TABLE IF NOT EXISTS mart_stage_formula_fitness (
    fundamental_stage TEXT NOT NULL,
    technical_stage   TEXT NOT NULL,
    formula_id        TEXT NOT NULL,
    formula_variant   TEXT NOT NULL,
    holding_days      INTEGER NOT NULL,
    n_signals         INTEGER NOT NULL,
    win_rate          REAL,
    avg_ret           REAL,
    avg_dd            REAL,
    calmar            REAL,
    sharpe            REAL,
    rank_in_stage     INTEGER,
    is_recommended    BOOLEAN,
    eval_start_date   TEXT,
    eval_end_date     TEXT,
    built_at          TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (fundamental_stage, technical_stage, formula_id, formula_variant, holding_days)
);
"""


def ensure_formula_tables(conn) -> None:
    """幂等建表。"""
    conn.executescript(FACT_TECHNICAL_TRIGGER_DDL)
    conn.executescript(MART_MACD_STATE_HISTORY_DDL)
    conn.executescript(MART_FORMULA_HORIZON_EVIDENCE_DDL)
    conn.executescript(FACT_STOCK_TECHNICAL_STAGE_DDL)
    conn.executescript(MART_STAGE_FORMULA_FITNESS_DDL)
    conn.commit()
