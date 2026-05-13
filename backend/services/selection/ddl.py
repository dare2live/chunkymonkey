"""Phase ε — 选股追踪 + 反馈闭环 DDL (4 张表)。

Schema 灵感:
  - mart_decision_outcome (Phase δ) → mart_stock_selection_outcome 是 superset
  - mart_signal_ic (Phase δ) → mart_formula_weight_history 是其聚合下游
"""
from __future__ import annotations


# =================================================================
# 1. fact_stock_selection_log — PIT append-only 选股事件日志
# =================================================================
# 每个 "选中" 事件 1 行:
#   - daily_topk 选中: select_source='daily_topk', source_id=model_id, rank+pred_score
#   - 公式触发:    select_source='formula',   source_id=formula_id, strength+state
FACT_STOCK_SELECTION_LOG_DDL = """
CREATE TABLE IF NOT EXISTS fact_stock_selection_log (
    select_date           TEXT NOT NULL,
    stock_code            TEXT NOT NULL,
    select_source         TEXT NOT NULL,         -- 'daily_topk' | 'formula' | 'manual'
    source_id             TEXT NOT NULL,         -- model_id 或 formula_id
    -- 选择时的相关元数据
    rank_in_date          INTEGER,               -- 仅 daily_topk
    pred_score            REAL,                  -- 仅 daily_topk
    strength              REAL,                  -- 仅 formula
    state                 TEXT,                  -- 仅 formula (e.g. 'just_crossed')
    horizon_days          INTEGER,               -- 期望持仓天数
    -- 元
    built_at              TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (select_date, stock_code, select_source, source_id)
);
CREATE INDEX IF NOT EXISTS idx_fssl_date   ON fact_stock_selection_log(select_date);
CREATE INDEX IF NOT EXISTS idx_fssl_code   ON fact_stock_selection_log(stock_code);
CREATE INDEX IF NOT EXISTS idx_fssl_source ON fact_stock_selection_log(select_source, source_id);
"""


# =================================================================
# 2. mart_stock_selection_outcome — 每选股事件的 5/10/30d 实际结果
# =================================================================
# v3-data.jsx CMV3.SELECTION_HISTORY 形状对齐:
#   {selectDate, formula, horizon, retPct, ddPct, daysToT1, outcome}
MART_STOCK_SELECTION_OUTCOME_DDL = """
CREATE TABLE IF NOT EXISTS mart_stock_selection_outcome (
    select_date           TEXT NOT NULL,
    stock_code            TEXT NOT NULL,
    select_source         TEXT NOT NULL,
    source_id             TEXT NOT NULL,
    entry_price           REAL,
    -- forward returns
    fwd_ret_5d            REAL,
    fwd_ret_10d           REAL,
    fwd_ret_30d           REAL,
    fwd_max_dd_30d        REAL,
    -- 到达 target_1 用时 (天) — Phase ε 简化用 hit_5pct_days
    days_to_t1            INTEGER,
    -- outcome 分类
    outcome_5d            TEXT,                  -- 'win' / 'loss' / 'flat' / 'active'
    outcome_10d           TEXT,
    outcome_30d           TEXT,
    -- 关联 fitness 表查 horizon 推荐
    horizon_days          INTEGER,
    -- 元
    built_at              TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (select_date, stock_code, select_source, source_id)
);
CREATE INDEX IF NOT EXISTS idx_msso_date   ON mart_stock_selection_outcome(select_date);
CREATE INDEX IF NOT EXISTS idx_msso_code   ON mart_stock_selection_outcome(stock_code);
CREATE INDEX IF NOT EXISTS idx_msso_source ON mart_stock_selection_outcome(select_source, source_id);
"""


# =================================================================
# 3. mart_stock_selection_summary — 每股 rolling 统计
# =================================================================
# v3-data.jsx CMV3.SELECTION_BOARD 形状对齐:
#   {code, name, n30, n_total, win, avg_ret, last_outcome, last_date, last_formula}
MART_STOCK_SELECTION_SUMMARY_DDL = """
CREATE TABLE IF NOT EXISTS mart_stock_selection_summary (
    stock_code            TEXT NOT NULL,
    snapshot_date         TEXT NOT NULL,
    -- 计数
    n_total               INTEGER,               -- 全历史选中次数
    n_30d                 INTEGER,               -- 最近 30 日选中次数
    n_90d                 INTEGER,               -- 最近 90 日选中次数
    -- 胜率/收益 (基于已 settled 的 outcome)
    win_rate              REAL,                  -- 全历史
    win_rate_30d          REAL,
    win_rate_90d          REAL,
    avg_ret               REAL,                  -- 全历史平均 ret_10d
    avg_ret_30d           REAL,
    avg_dd                REAL,                  -- 平均 max_dd
    -- 最新一次
    last_select_date      TEXT,
    last_formula          TEXT,                  -- last select source_id, formula 类用 formula_id, topk 用 'daily_topk'
    last_outcome          TEXT,                  -- 最新一次的 10d outcome, active 表示未到期
    built_at              TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (stock_code, snapshot_date)
);
CREATE INDEX IF NOT EXISTS idx_msss_date ON mart_stock_selection_summary(snapshot_date);
"""


# =================================================================
# 4. mart_formula_weight_history — 反馈环: IC → 公式权重
# =================================================================
# 每日每公式 1 行 (snapshot_date), weight 取决于 rolling_ic_60d
# 消费者: run_daily_topk.py 读 latest row 当公式 ensemble 权重
MART_FORMULA_WEIGHT_HISTORY_DDL = """
CREATE TABLE IF NOT EXISTS mart_formula_weight_history (
    formula_id            TEXT NOT NULL,
    formula_variant       TEXT,
    snapshot_date         TEXT NOT NULL,
    weight                REAL NOT NULL,         -- normalized [0, 1], 全公式 sum=1
    rolling_ic_30d        REAL,
    rolling_ic_60d        REAL,
    n_obs                 INTEGER,
    is_active             BOOLEAN DEFAULT TRUE,
    built_at              TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (formula_id, formula_variant, snapshot_date)
);
CREATE INDEX IF NOT EXISTS idx_mfwh_date ON mart_formula_weight_history(snapshot_date);
"""


def ensure_selection_tables(conn) -> None:
    """幂等建表 (4 张表)。"""
    conn.executescript(FACT_STOCK_SELECTION_LOG_DDL)
    conn.executescript(MART_STOCK_SELECTION_OUTCOME_DDL)
    conn.executescript(MART_STOCK_SELECTION_SUMMARY_DDL)
    conn.executescript(MART_FORMULA_WEIGHT_HISTORY_DDL)
    conn.commit()
