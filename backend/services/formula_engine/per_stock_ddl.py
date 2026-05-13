"""Phase η — 每股公式精细化 + 每日推荐 DDL。

两张新表 (与 mart_stage_formula_fitness 互补; 那张是全局聚合, 这两张是 per-stock):

1. mart_stock_formula_optuna
   每股 × 每公式 × 每持仓天数 × 每 5 维分桶 → 该股自己的胜率/收益/dd
   用于股票视图 + 公式视图

2. mart_daily_formula_buys
   每日盘后, 公式今天触发 → JOIN 该股该 variant 历史表现 → T+1 买入推荐
   字段对齐 user 需求: 买入价/持仓周期/卖出价/最大回撤
"""
from __future__ import annotations


# =================================================================
# 1. mart_stock_formula_optuna — 每股公式精细化
# =================================================================
MART_STOCK_FORMULA_OPTUNA_DDL = """
CREATE TABLE IF NOT EXISTS mart_stock_formula_optuna (
    stock_code        TEXT NOT NULL,
    formula_id        TEXT NOT NULL,
    formula_variant   TEXT NOT NULL,
    holding_days      INTEGER NOT NULL,
    -- 5 维上下文分桶 (来自 fact_signal_context)
    vol_bin           TEXT,                  -- '缩量' / '平量' / '温量' / '爆量'
    amt_bin           TEXT,
    price_pos_bin     TEXT,                  -- '深底' / '中位' / '高位' / '新高'
    stage_bin         TEXT,                  -- '1'/'1.5'/'2'/'3'/'4'/'?'
    -- 该股在该 (variant × hd × 5 桶) 下的回测 metrics
    n_signals         INTEGER NOT NULL,
    win_rate          REAL,
    avg_ret           REAL,
    median_ret        REAL,
    avg_dd            REAL,
    median_dd         REAL,
    sharpe            REAL,
    calmar            REAL,
    -- 标记
    is_best_hd        BOOLEAN DEFAULT FALSE, -- 该股该 (variant × 桶) 下最佳持仓
    is_high_conviction BOOLEAN DEFAULT FALSE, -- 满足胜率≥60% + n≥5
    eval_start_date   TEXT,
    eval_end_date     TEXT,
    built_at          TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (stock_code, formula_id, formula_variant, holding_days,
                 vol_bin, amt_bin, price_pos_bin, stage_bin)
);
CREATE INDEX IF NOT EXISTS idx_msfo_stock   ON mart_stock_formula_optuna(stock_code);
CREATE INDEX IF NOT EXISTS idx_msfo_formula ON mart_stock_formula_optuna(formula_id, formula_variant);
CREATE INDEX IF NOT EXISTS idx_msfo_best    ON mart_stock_formula_optuna(is_best_hd, is_high_conviction);
"""


# =================================================================
# 2. mart_daily_formula_buys — 每日盘后 T+1 买入推荐
# =================================================================
MART_DAILY_FORMULA_BUYS_DDL = """
CREATE TABLE IF NOT EXISTS mart_daily_formula_buys (
    signal_date       TEXT NOT NULL,         -- T (今日盘后, 公式触发的日)
    buy_date          TEXT NOT NULL,         -- T+1 (建议买入日)
    stock_code        TEXT NOT NULL,
    formula_id        TEXT NOT NULL,
    formula_variant   TEXT NOT NULL,
    -- 今日触发时的 context
    vol_bin           TEXT,
    amt_bin           TEXT,
    price_pos_bin     TEXT,
    stage_bin         TEXT,
    signal_strength   REAL,
    -- 历史相似配置 (来自 mart_stock_formula_optuna) — 这是推荐的 "依据"
    historical_win_rate    REAL,
    historical_avg_ret     REAL,
    historical_avg_dd      REAL,
    historical_sharpe      REAL,
    historical_n_signals   INTEGER,
    -- T+1 交易建议
    recommended_holding_days INTEGER,        -- 该股该配置最佳持仓
    signal_close_price       REAL,           -- T 日收盘 (信号当日)
    buy_price_est            REAL,           -- T+1 预估买入价 (= T close × 1.005, 保守估计)
    sell_target_price        REAL,           -- T+1+hd 预期卖出价 = buy × (1 + avg_ret)
    expected_max_dd_pct      REAL,           -- 历史平均最大回撤 (负数)
    expected_return_pct      REAL,           -- 历史平均收益率
    confidence_score         REAL,           -- 综合 (win_rate × log(n_signals))
    rank_in_date             INTEGER,        -- 当日所有推荐按 confidence 排名
    -- 元
    model_id          TEXT NOT NULL DEFAULT 'formula_v1',
    built_at          TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (signal_date, stock_code, formula_id, formula_variant, model_id)
);
CREATE INDEX IF NOT EXISTS idx_mdfb_date  ON mart_daily_formula_buys(signal_date);
CREATE INDEX IF NOT EXISTS idx_mdfb_stock ON mart_daily_formula_buys(stock_code);
CREATE INDEX IF NOT EXISTS idx_mdfb_rank  ON mart_daily_formula_buys(signal_date, rank_in_date);
"""


def ensure_per_stock_tables(conn) -> None:
    conn.executescript(MART_STOCK_FORMULA_OPTUNA_DDL)
    conn.executescript(MART_DAILY_FORMULA_BUYS_DDL)
    conn.commit()
