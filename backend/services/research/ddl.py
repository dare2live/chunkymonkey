"""Phase ε+ §6.5 三件套 DDL。"""
from __future__ import annotations


MART_MODEL_COMPOSITE_SCORE_DDL = """
CREATE TABLE IF NOT EXISTS mart_model_composite_score (
    model_id              TEXT NOT NULL,
    eval_date             TEXT NOT NULL,
    wf_rank_ic_avg        REAL,
    paper_sharpe          REAL,
    paper_max_drawdown    REAL,
    n_paper_trades        INTEGER,
    risk_adjust_factor    REAL,
    trade_penalty         REAL,
    edge_guard            REAL,
    composite_score       REAL,
    composite_rank        INTEGER,
    built_at              TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (model_id, eval_date)
);
CREATE INDEX IF NOT EXISTS idx_mmcs_date ON mart_model_composite_score(eval_date);
"""


MART_MODEL_EDGE_FLAGS_DDL = """
CREATE TABLE IF NOT EXISTS mart_model_edge_flags (
    model_id          TEXT NOT NULL,
    eval_date         TEXT NOT NULL,
    flag_type         TEXT NOT NULL,
    trigger_metric    TEXT,
    trigger_value     REAL,
    trigger_threshold REAL,
    auto_action       TEXT,
    is_resolved       BOOLEAN DEFAULT FALSE,
    resolved_at       TEXT,
    built_at          TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (model_id, eval_date)
);
CREATE INDEX IF NOT EXISTS idx_mmef_date ON mart_model_edge_flags(eval_date);
CREATE INDEX IF NOT EXISTS idx_mmef_flag ON mart_model_edge_flags(flag_type);
"""


MART_RESEARCH_REFLECTION_LOG_DDL = """
CREATE TABLE IF NOT EXISTS mart_research_reflection_log (
    log_id            TEXT PRIMARY KEY,
    cycle_number      INTEGER,
    run_date          TEXT NOT NULL,
    model_id_before   TEXT,
    model_id_after    TEXT,
    hypothesis        TEXT NOT NULL,
    changed_params    TEXT NOT NULL,
    score_before      REAL,
    score_after       REAL,
    sharpe_before     REAL,
    sharpe_after      REAL,
    drawdown_before   REAL,
    drawdown_after    REAL,
    edge_flags_json   TEXT,
    reflection        TEXT NOT NULL,
    next_hypothesis   TEXT,
    is_meta_reflection BOOLEAN DEFAULT FALSE,
    meta_blind_spots  TEXT,
    built_at          TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_mrrl_date  ON mart_research_reflection_log(run_date);
CREATE INDEX IF NOT EXISTS idx_mrrl_cycle ON mart_research_reflection_log(cycle_number);
"""


def ensure_research_tables(conn) -> None:
    conn.executescript(MART_MODEL_COMPOSITE_SCORE_DDL)
    conn.executescript(MART_MODEL_EDGE_FLAGS_DDL)
    conn.executescript(MART_RESEARCH_REFLECTION_LOG_DDL)
    conn.commit()
