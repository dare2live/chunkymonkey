"""Phase ε+ §3.4 — 10 张基础设施表 DDL。"""
from __future__ import annotations


# 1. 涨跌停规则
DIM_PRICE_LIMIT_RULES_DDL = """
CREATE TABLE IF NOT EXISTS dim_price_limit_rules (
    rule_id           TEXT PRIMARY KEY,
    market_segment    TEXT NOT NULL,        -- 'main' / 'chinext' / 'star' / 'bj'
    is_st             BOOLEAN NOT NULL,
    is_new_listing    BOOLEAN NOT NULL,
    days_after_ipo    INTEGER,              -- 新股前 N 日适用
    limit_up_pct      REAL NOT NULL,        -- 0.10 / 0.20 / 0.30 / 0.05 / 0.44
    limit_down_pct    REAL NOT NULL,
    effective_from    TEXT NOT NULL,
    effective_to      TEXT,
    notes             TEXT
);
"""


# 2. 市场细分
DIM_MARKET_SEGMENT_DDL = """
CREATE TABLE IF NOT EXISTS dim_market_segment (
    segment_id        TEXT PRIMARY KEY,
    segment_name      TEXT NOT NULL,
    code_prefix       TEXT NOT NULL,        -- '600' '601' '603' '605' '00' '30' '688' '8' '4'
    code_pattern_re   TEXT,
    notes             TEXT
);
"""


# 3. 交易规则
DIM_TRADING_RULE_DDL = """
CREATE TABLE IF NOT EXISTS dim_trading_rule (
    rule_id           TEXT PRIMARY KEY,
    market_segment    TEXT NOT NULL,
    settlement_cycle  TEXT NOT NULL,        -- 'T+1'
    min_lot_size      INTEGER NOT NULL,     -- 100
    price_tick        REAL NOT NULL,        -- 0.01
    effective_from    TEXT NOT NULL
);
"""


# 4. 费用清单
DIM_FEE_SCHEDULE_DDL = """
CREATE TABLE IF NOT EXISTS dim_fee_schedule (
    fee_id            TEXT PRIMARY KEY,
    fee_type          TEXT NOT NULL,        -- 'commission' / 'stamp_tax' / 'transfer'
    rate_pct          REAL NOT NULL,
    min_amount        REAL,                 -- 最低收费 (元)
    side              TEXT NOT NULL,        -- 'buy' / 'sell' / 'both'
    market_segment    TEXT,
    effective_from    TEXT NOT NULL,
    notes             TEXT
);
"""


# 5. 盘口时段
DIM_TRADING_SESSION_DDL = """
CREATE TABLE IF NOT EXISTS dim_trading_session (
    session_id        TEXT PRIMARY KEY,
    session_name      TEXT NOT NULL,        -- '集合竞价开盘' / '连续竞价 上午'
    start_time        TEXT NOT NULL,        -- 'HH:MM'
    end_time          TEXT NOT NULL,
    allow_match       BOOLEAN NOT NULL,     -- 集合竞价是否撮合
    is_open_session   BOOLEAN,
    is_close_session  BOOLEAN
);
"""


# 6. 一字板 / 涨跌停 / 停牌 (推自 K 线 + price_limit_rules)
FACT_DAILY_PRICE_STATUS_DDL = """
CREATE TABLE IF NOT EXISTS fact_daily_price_status (
    stock_code             TEXT NOT NULL,
    date                   TEXT NOT NULL,
    is_one_word_limit_up   BOOLEAN NOT NULL,
    is_one_word_limit_down BOOLEAN NOT NULL,
    hit_limit_up           BOOLEAN NOT NULL,
    hit_limit_down         BOOLEAN NOT NULL,
    is_suspended           BOOLEAN NOT NULL,
    suspension_reason      TEXT,
    prev_close             REAL,
    limit_up_price         REAL,
    limit_down_price       REAL,
    actual_amount          REAL,
    rule_id_applied        TEXT,
    built_at               TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (stock_code, date)
);
CREATE INDEX IF NOT EXISTS idx_fdps_date ON fact_daily_price_status(date);
"""


# 7. 流动性
DIM_LIQUIDITY_THRESHOLD_DDL = """
CREATE TABLE IF NOT EXISTS dim_liquidity_threshold (
    threshold_id      TEXT PRIMARY KEY,
    market_segment    TEXT NOT NULL,
    min_amount_20d    REAL NOT NULL,        -- 元, 20 日平均成交额下限
    min_turnover_pct  REAL,                 -- 换手率下限
    effective_from    TEXT NOT NULL,
    notes             TEXT
);
"""


FACT_STOCK_LIQUIDITY_DAILY_DDL = """
CREATE TABLE IF NOT EXISTS fact_stock_liquidity_daily (
    stock_code        TEXT NOT NULL,
    date              TEXT NOT NULL,
    amount_20d_mean   REAL,                 -- 20 日均成交额
    turnover_pct_20d  REAL,                 -- 20 日换手率均值
    is_liquid         BOOLEAN,              -- 通过流动性阈值
    threshold_id_applied TEXT,
    built_at          TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (stock_code, date)
);
CREATE INDEX IF NOT EXISTS idx_fsld_date ON fact_stock_liquidity_daily(date);
"""


# 8. 退市风险
DIM_LISTING_STATUS_DDL = """
CREATE TABLE IF NOT EXISTS dim_listing_status (
    stock_code        TEXT PRIMARY KEY,
    listing_status    TEXT NOT NULL,        -- 'normal' / 'st' / 'star_st' / 'pt' / 'delisting' / 'suspended'
    status_reason     TEXT,
    flag_from_date    TEXT,
    detected_at       TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


# 9. 风格因子
DIM_STYLE_FACTOR_DDL = """
CREATE TABLE IF NOT EXISTS dim_style_factor (
    factor_id         TEXT PRIMARY KEY,
    factor_name       TEXT NOT NULL,        -- 'size' / 'value' / 'momentum' / 'quality' / 'volatility' / 'liquidity'
    formula_text      TEXT,
    notes             TEXT
);
"""


FACT_STOCK_STYLE_DAILY_DDL = """
CREATE TABLE IF NOT EXISTS fact_stock_style_daily (
    stock_code        TEXT NOT NULL,
    date              TEXT NOT NULL,
    factor_id         TEXT NOT NULL,
    raw_value         REAL,
    z_score           REAL,                 -- 截面 z-score
    cluster_id        INTEGER,              -- 风格聚类 (可选)
    built_at          TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (stock_code, date, factor_id)
);
CREATE INDEX IF NOT EXISTS idx_fssd_date   ON fact_stock_style_daily(date);
CREATE INDEX IF NOT EXISTS idx_fssd_factor ON fact_stock_style_daily(factor_id);
"""


# 10. 流通/总市值
FACT_STOCK_MARKET_CAP_DAILY_DDL = """
CREATE TABLE IF NOT EXISTS fact_stock_market_cap_daily (
    stock_code        TEXT NOT NULL,
    date              TEXT NOT NULL,
    close_price       REAL,
    total_shares      REAL,                 -- 总股本 (亿股)
    float_shares      REAL,                 -- 流通股本
    total_market_cap  REAL,                 -- 元
    float_market_cap  REAL,
    built_at          TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (stock_code, date)
);
CREATE INDEX IF NOT EXISTS idx_fsmc_date ON fact_stock_market_cap_daily(date);
"""


def ensure_primitives_tables(conn) -> None:
    """幂等建表 (10 张表 + 2 个 fact 副表 = 共 12 张, 但 §3.4 计数为 10 项)。"""
    conn.executescript(DIM_PRICE_LIMIT_RULES_DDL)
    conn.executescript(DIM_MARKET_SEGMENT_DDL)
    conn.executescript(DIM_TRADING_RULE_DDL)
    conn.executescript(DIM_FEE_SCHEDULE_DDL)
    conn.executescript(DIM_TRADING_SESSION_DDL)
    conn.executescript(FACT_DAILY_PRICE_STATUS_DDL)
    conn.executescript(DIM_LIQUIDITY_THRESHOLD_DDL)
    conn.executescript(FACT_STOCK_LIQUIDITY_DAILY_DDL)
    conn.executescript(DIM_LISTING_STATUS_DDL)
    conn.executescript(DIM_STYLE_FACTOR_DDL)
    conn.executescript(FACT_STOCK_STYLE_DAILY_DDL)
    conn.executescript(FACT_STOCK_MARKET_CAP_DAILY_DDL)
    conn.commit()
