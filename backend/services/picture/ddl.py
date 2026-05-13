"""Phase γ 画像子包 DDL — 5 张新表, 全部 PIT (point-in-time)。

设计原则:
  - 不污染 services/db.py 中心 init_db (与 formula_engine/ddl.py 同款)
  - build 脚本启动时调用 ensure_picture_tables(conn) 幂等建表
  - 所有表带 built_at 用于 PIT 审计

表清单 (来源: Phase γ Plan agent + D1 audit):
  1. fact_stock_fundamental_stage_daily — 基本面阶段日快照 (6 状态)
  2. fact_stock_type_daily               — 股票类型日快照 (5 状态)
  3. dim_stock_stage_days                — 基本面 + 技术面持续天数
  4. mart_stock_picture_daily            — fan-out: 每股每日 1 行 + 全画像字段
  5. mart_stock_trade_plan               — 8 字段交易计划 (Phase γ D4 实施)
"""
from __future__ import annotations


# =================================================================
# 1. fact_stock_fundamental_stage_daily
# =================================================================
# 来源: dim_stock_stage_latest (3,355 stocks, 每日全量重算)
# 派生逻辑见 services/picture/fundamental_stage.py
# 6 状态: 未充分演绎 / 温和验证 / 已充分演绎 / 失效破坏 / 周期复苏 / 中性
FACT_STOCK_FUNDAMENTAL_STAGE_DAILY_DDL = """
CREATE TABLE IF NOT EXISTS fact_stock_fundamental_stage_daily (
    stock_code         TEXT NOT NULL,
    date               TEXT NOT NULL,
    fundamental_stage  TEXT NOT NULL,
    stage_score_v1     REAL,
    stage_reason       TEXT,
    stock_gate         TEXT,
    built_at           TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (stock_code, date)
);
CREATE INDEX IF NOT EXISTS idx_fsfsd_date  ON fact_stock_fundamental_stage_daily(date);
CREATE INDEX IF NOT EXISTS idx_fsfsd_stage ON fact_stock_fundamental_stage_daily(fundamental_stage);
"""


# =================================================================
# 2. fact_stock_type_daily
# =================================================================
# 5 状态 primary_type: 事件驱动 / 业绩驱动 / 价值修复 / 技术突破 / 周期复苏
# secondary_types_json 是 list[str], type_score 0-100
FACT_STOCK_TYPE_DAILY_DDL = """
CREATE TABLE IF NOT EXISTS fact_stock_type_daily (
    stock_code           TEXT NOT NULL,
    date                 TEXT NOT NULL,
    primary_type         TEXT NOT NULL,
    secondary_types_json TEXT,
    type_score           REAL,
    reason_codes_json    TEXT,
    built_at             TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (stock_code, date)
);
CREATE INDEX IF NOT EXISTS idx_fstd_date         ON fact_stock_type_daily(date);
CREATE INDEX IF NOT EXISTS idx_fstd_primary_type ON fact_stock_type_daily(primary_type);
"""


# =================================================================
# 3. dim_stock_stage_days
# =================================================================
# 当前 (snapshot_date) 时刻, 该股票在 fundamental_stage / technical_stage 已持续多少天
# 算法: 从 snapshot_date 反向扫描 fact_stock_fundamental_stage_daily + fact_stock_technical_stage
# 直到 stage 发生变化, 计数 = stage_days
DIM_STOCK_STAGE_DAYS_DDL = """
CREATE TABLE IF NOT EXISTS dim_stock_stage_days (
    stock_code               TEXT NOT NULL,
    snapshot_date            TEXT NOT NULL,
    fundamental_stage        TEXT,
    fundamental_stage_days   INTEGER,
    technical_stage          TEXT,
    technical_stage_days     INTEGER,
    built_at                 TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (stock_code, snapshot_date)
);
CREATE INDEX IF NOT EXISTS idx_dssd_date ON dim_stock_stage_days(snapshot_date);
"""


# =================================================================
# 4. mart_stock_picture_daily
# =================================================================
# 终端 fan-out, 每股每日 1 行, v3-data-live.jsx STOCKS 卡片直接消费。
# 字段对齐 design/v3-data.jsx 的 mock 字段 (STOCKS = [{code, name, price, chg_pct, primary_type, ...}])
MART_STOCK_PICTURE_DAILY_DDL = """
CREATE TABLE IF NOT EXISTS mart_stock_picture_daily (
    stock_code              TEXT NOT NULL,
    snapshot_date           TEXT NOT NULL,
    -- K 线 (最新)
    latest_close            REAL,
    chg_pct                 REAL,
    -- 双 stage 体系
    fundamental_stage       TEXT,
    fundamental_stage_days  INTEGER,
    technical_stage         TEXT,
    technical_stage_days    INTEGER,
    -- 股票类型 (主 + 副)
    primary_type            TEXT,
    secondary_types_json    TEXT,
    -- 估值
    valuation_pe            REAL,
    valuation_pe_pctile     REAL,
    valuation_upside_pct    REAL,
    -- 机构信号 (聚合)
    institution_score       REAL,
    institution_n_insts     INTEGER,
    institution_top_json    TEXT,
    -- 公式触发汇总
    formulas_hit_json       TEXT,
    -- 元
    stock_archetype         TEXT,
    built_at                TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (stock_code, snapshot_date)
);
CREATE INDEX IF NOT EXISTS idx_mspd_date          ON mart_stock_picture_daily(snapshot_date);
CREATE INDEX IF NOT EXISTS idx_mspd_primary_type  ON mart_stock_picture_daily(primary_type);
"""


# =================================================================
# 5. mart_stock_trade_plan
# =================================================================
# Phase γ D4 实施, DDL 先建好
# 来源: 包装 fact_stock_turtle_features 的 ATR + entry_level + stop_level
MART_STOCK_TRADE_PLAN_DDL = """
CREATE TABLE IF NOT EXISTS mart_stock_trade_plan (
    stock_code              TEXT NOT NULL,
    plan_date               TEXT NOT NULL,
    model_id                TEXT NOT NULL DEFAULT 'v1',
    -- 入场 3 价
    entry_target_price      REAL,
    entry_aggressive_price  REAL,
    entry_max_price         REAL,
    -- 出场 3 价
    exit_target_1_price     REAL,
    exit_target_2_price     REAL,
    exit_stop_price         REAL,
    -- 风险报酬 + 持仓预期
    risk_reward_ratio       REAL,
    expected_horizon_days   INTEGER,
    -- 计算原料
    atr_14                  REAL,
    entry_basis             TEXT,
    reason_codes_json       TEXT,
    built_at                TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (stock_code, plan_date, model_id)
);
CREATE INDEX IF NOT EXISTS idx_mstp_date ON mart_stock_trade_plan(plan_date);
"""


def ensure_picture_tables(conn) -> None:
    """幂等建表 (与 ensure_formula_tables 同款)。"""
    conn.executescript(FACT_STOCK_FUNDAMENTAL_STAGE_DAILY_DDL)
    conn.executescript(FACT_STOCK_TYPE_DAILY_DDL)
    conn.executescript(DIM_STOCK_STAGE_DAYS_DDL)
    conn.executescript(MART_STOCK_PICTURE_DAILY_DDL)
    conn.executescript(MART_STOCK_TRADE_PLAN_DDL)
    conn.commit()
