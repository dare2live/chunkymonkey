"""Phase η+++++ — buy_signal schema DDL (单一定义)."""
from __future__ import annotations


MART_STOCK_FORMULA_BUY_SIGNAL_DAILY_DDL = """
DROP TABLE IF EXISTS mart_stock_formula_buy_signal_daily;
CREATE TABLE IF NOT EXISTS mart_stock_formula_buy_signal_daily (
    signal_date            TEXT NOT NULL,
    stock_code             TEXT NOT NULL,
    formula_id             TEXT NOT NULL,
    formula_variant        TEXT NOT NULL,

    -- 综合判定
    score                  REAL NOT NULL,       -- 0-100
    tier                   TEXT NOT NULL,       -- NO_SIGNAL / WATCH / BUY / STRONG_BUY
    reasoning              TEXT,                -- 人类可读理由 ≤120 字

    -- 8 个因子原始 0-1 分数 (Phase η+++++ 修正: 拆 stage_fitness + 加 archetype + primary_type)
    factor_trigger         REAL,
    factor_bucket_match    REAL,
    factor_historical_alpha REAL,
    factor_stage_fitness   REAL,  -- 数据驱动 (mart_stage_formula_fitness)
    factor_fundamental_stage REAL,
    factor_sentiment       REAL,
    factor_stock_archetype REAL,  -- 新: 股票原型 × 公式
    factor_primary_type    REAL,  -- 新: 股票类型 (业绩/价值/周期/事件/技术)

    -- 各因子贡献
    contrib_trigger        REAL,
    contrib_bucket_match   REAL,
    contrib_historical_alpha REAL,
    contrib_stage_fitness  REAL,
    contrib_fundamental_stage REAL,
    contrib_sentiment      REAL,
    contrib_stock_archetype REAL,
    contrib_primary_type   REAL,

    -- 当日参考数据
    today_technical_stage  TEXT,
    today_fundamental_stage TEXT,
    today_stock_archetype  TEXT,
    today_primary_type     TEXT,
    today_survey_bin       TEXT,
    today_vol_bin          TEXT,
    today_amt_bin          TEXT,
    today_p60_bin          TEXT,

    -- 历史寻优数据 (引用 mart_per_stock_strategy_optimal)
    historical_sharpe      REAL,
    historical_win_rate    REAL,
    historical_n_traded    INTEGER,
    optimal_hp             INTEGER,
    optimal_stop_pct       REAL,
    optimal_target_pct     REAL,
    optimal_trailing_pct   REAL,
    optimal_buy_offset     INTEGER,

    built_at               TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (signal_date, stock_code, formula_variant)
);
CREATE INDEX IF NOT EXISTS idx_msfbsd_date  ON mart_stock_formula_buy_signal_daily(signal_date);
CREATE INDEX IF NOT EXISTS idx_msfbsd_tier  ON mart_stock_formula_buy_signal_daily(signal_date, tier);
CREATE INDEX IF NOT EXISTS idx_msfbsd_score ON mart_stock_formula_buy_signal_daily(signal_date, score);
CREATE INDEX IF NOT EXISTS idx_msfbsd_stock ON mart_stock_formula_buy_signal_daily(stock_code);
"""
