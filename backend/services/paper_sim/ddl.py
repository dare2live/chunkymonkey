"""Paper Sim v2 — 4 张专用表 (跟旧 paper_engine 物理隔离).

设计意图:
- mart_paper_sim_nav        日级 NAV + HS300 + 超额
- fact_paper_sim_position   持仓快照 (open & close 都用同一表, position_id 自然键)
- fact_paper_sim_trade      交易日志 (BUY / SELL / SWAP_OUT / SWAP_IN), 每个动作一行
- mart_paper_sim_kpi        每次完整 walk-forward 跑完后写 1 行 KPI summary

旧 paper_engine 的 mart_paper_nav (5-04-13 停了) 不动, 保留历史回放.
新 paper_sim_* 表跟新 swap-driven 规则绑定, 干净起步.
"""
from __future__ import annotations


DDL = """
CREATE TABLE IF NOT EXISTS mart_paper_sim_nav (
    sim_run_id        TEXT NOT NULL,             -- 一次 walk-forward 唯一 ID
    date              TEXT NOT NULL,
    trade_date_dt     DATE,
    total_value       DOUBLE NOT NULL,           -- 现金 + 持仓市值
    cash              DOUBLE NOT NULL,
    positions_value   DOUBLE NOT NULL,
    n_positions       INTEGER NOT NULL,
    hs300_nav         DOUBLE,                    -- 基准 NAV (归一化)
    daily_ret         DOUBLE,
    excess_daily      DOUBLE,                    -- 当日策略 - HS300
    cum_excess        DOUBLE,
    regime            TEXT,                      -- bull / bear / sideways (HS300 60d)
    built_at          TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (sim_run_id, date)
);
CREATE INDEX IF NOT EXISTS idx_msn_date ON mart_paper_sim_nav(date);

CREATE TABLE IF NOT EXISTS fact_paper_sim_position (
    position_id       TEXT NOT NULL,             -- f"{stock}_{open_date}_{run_id}"
    sim_run_id        TEXT NOT NULL,
    stock_code        TEXT NOT NULL,
    formula_id        TEXT NOT NULL,
    formula_variant   TEXT NOT NULL,
    stage_at_buy      TEXT,                      -- 买入时 technical_stage
    -- Optuna 9-dim 寻优参数 (买入时锁定, 不变)
    optimal_hp        INTEGER NOT NULL,
    optimal_stop_pct  DOUBLE,
    optimal_target_pct DOUBLE,
    optimal_trailing_pct DOUBLE,
    -- 入场
    open_date         TEXT NOT NULL,
    open_price        DOUBLE NOT NULL,
    shares            INTEGER NOT NULL,
    buy_cost          DOUBLE NOT NULL,           -- 含手续费 + 滑点
    -- 出场 (持仓中为 NULL)
    close_date        TEXT,
    close_price       DOUBLE,
    sell_revenue      DOUBLE,                    -- 扣完税费滑点
    close_reason      TEXT,                      -- target / stop / trailing / hp / stage / swap
    pnl               DOUBLE,                    -- sell_revenue - buy_cost
    pnl_pct           DOUBLE,                    -- pnl / buy_cost
    days_held         INTEGER,
    -- 锁定的预期值 (用来算达成率)
    expected_target_pct DOUBLE NOT NULL,         -- = optimal_target_pct (锁定时的)
    -- Phase 1a Option C (Codex round 4 MAJOR): exit params 来源 'pit' (PIT 表 INNER JOIN) 或 'fallback' (Option C 弱 default 缺 PIT)
    exit_source       TEXT DEFAULT 'pit',
    -- Trailing 状态 (跨日跟踪, portfolio_backtest 同款逻辑)
    trailing_armed    BOOLEAN NOT NULL DEFAULT FALSE,  -- target hit 后变 True
    high_since_arm    DOUBLE,                          -- arm 后的最高价
    -- 元
    is_open           BOOLEAN NOT NULL DEFAULT TRUE,
    built_at          TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (position_id)
);
CREATE INDEX IF NOT EXISTS idx_fpsp_run   ON fact_paper_sim_position(sim_run_id);
CREATE INDEX IF NOT EXISTS idx_fpsp_stock ON fact_paper_sim_position(stock_code);
CREATE INDEX IF NOT EXISTS idx_fpsp_open  ON fact_paper_sim_position(is_open);

CREATE TABLE IF NOT EXISTS fact_paper_sim_trade (
    trade_id          TEXT PRIMARY KEY,          -- uuid
    sim_run_id        TEXT NOT NULL,
    position_id       TEXT NOT NULL,
    date              TEXT NOT NULL,
    type              TEXT NOT NULL,             -- BUY / SELL / SWAP_OUT / SWAP_IN
    reason            TEXT NOT NULL,             -- 详细触发原因
    price             DOUBLE NOT NULL,
    shares            INTEGER NOT NULL,
    gross_amount      DOUBLE NOT NULL,
    tx_cost           DOUBLE NOT NULL,
    net_amount        DOUBLE NOT NULL,           -- gross +/- tx_cost
    -- Swap counterfactual (仅 SWAP_OUT 行填)
    swap_uplift_estimate DOUBLE,                 -- 估算的 swap 净收益增量
    -- Phase 1a Option C (Codex round 4 MAJOR): 继承自 position.exit_source ('pit' / 'fallback')
    exit_source       TEXT DEFAULT 'pit',
    built_at          TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_fpst_run  ON fact_paper_sim_trade(sim_run_id);
CREATE INDEX IF NOT EXISTS idx_fpst_date ON fact_paper_sim_trade(date);
CREATE INDEX IF NOT EXISTS idx_fpst_pos  ON fact_paper_sim_trade(position_id);

CREATE TABLE IF NOT EXISTS mart_paper_sim_kpi (
    sim_run_id        TEXT PRIMARY KEY,
    variant           TEXT NOT NULL,             -- baseline / swap_v1 / swap_optuna
    period_start      TEXT NOT NULL,
    period_end        TEXT NOT NULL,
    n_days            INTEGER NOT NULL,

    -- A. 用户终极标准
    annual_return     DOUBLE,
    max_dd            DOUBLE,
    sharpe            DOUBLE,
    calmar            DOUBLE,
    monthly_win_rate  DOUBLE,
    total_return      DOUBLE,
    excess_vs_hs300   DOUBLE,
    information_ratio DOUBLE,
    user_criteria_pass BOOLEAN,

    -- B. Anti-churn
    avg_holding_days  DOUBLE,
    annual_turnover   DOUBLE,                    -- 全年成交额 / 初始资金
    tx_cost_total     DOUBLE,
    tx_cost_pct_of_gross_pnl DOUBLE,
    swap_count        INTEGER,
    swap_uplift_total DOUBLE,                    -- Σ (换上 Y 实际剩余收益 - 反事实 A 剩余收益)
    anti_churn_pass   BOOLEAN,

    -- C. Robustness
    rolling_ir_60d_median DOUBLE,
    rolling_ir_60d_p25    DOUBLE,
    rolling_annual_90d_median DOUBLE,
    regime_bull_return    DOUBLE,
    regime_bear_return    DOUBLE,
    regime_sideways_return DOUBLE,
    robustness_pass   BOOLEAN,

    -- 综合
    all_kpi_pass      BOOLEAN,
    config_snapshot   TEXT,                      -- JSON of full PaperSimConfig (审计 + 复现)
    lineage_url       TEXT,                      -- local file:// report or future /v3/lineage/<sim_run_id>
    -- Phase 1a Option C (Codex round 6 MAJOR #1): exit_source 分层 attribution (按 closed position)
    pit_count         INTEGER,
    pit_pnl           DOUBLE,
    pit_pnl_pct       DOUBLE,                    -- pit pnl / pit buy_cost
    fallback_count    INTEGER,
    fallback_pnl      DOUBLE,
    fallback_pnl_pct  DOUBLE,                    -- fallback pnl / fallback buy_cost
    built_at          TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_mpsk_variant ON mart_paper_sim_kpi(variant);
"""


def ensure_paper_sim_tables(conn) -> None:
    """幂等建 4 张 paper_sim 专用表 + ALTER 加 exit_source / partition KPI cols (Phase 1a Codex round 4+6 MAJOR)."""
    conn.executescript(DDL)
    # Phase 1a Option C migration: 已存表 ALTER 加 exit_source / partition KPI cols
    # CREATE TABLE IF NOT EXISTS 不会修改已存表 schema, 需 ALTER 单独跑.
    # DuckDB ALTER ADD COLUMN IF NOT EXISTS 不支持, 用 try/except + duplicate-column 收窄 (Codex round 6 MINOR #5).
    _migrations = [
        ("mart_paper_sim_nav",      "trade_date_dt DATE"),
        ("fact_paper_sim_position", "exit_source TEXT DEFAULT 'pit'"),
        ("fact_paper_sim_trade",    "exit_source TEXT DEFAULT 'pit'"),
        ("mart_paper_sim_kpi",      "pit_count INTEGER"),
        ("mart_paper_sim_kpi",      "pit_pnl DOUBLE"),
        ("mart_paper_sim_kpi",      "pit_pnl_pct DOUBLE"),
        ("mart_paper_sim_kpi",      "fallback_count INTEGER"),
        ("mart_paper_sim_kpi",      "fallback_pnl DOUBLE"),
        ("mart_paper_sim_kpi",      "fallback_pnl_pct DOUBLE"),
    ]
    for tbl, coldef in _migrations:
        try:
            conn.execute(f"ALTER TABLE {tbl} ADD COLUMN {coldef}")
        except Exception as _e:
            # 只吞 duplicate-column 错 (column 已存在), 其他 migration 错 raise
            if "already exists" not in str(_e).lower() and "duplicate" not in str(_e).lower():
                raise
    try:
        conn.execute("ALTER TABLE mart_paper_sim_kpi ADD COLUMN IF NOT EXISTS lineage_url TEXT")
    except Exception as _e:
        # duplicate-column 安全忽略 (DuckDB 某些版本 IF NOT EXISTS 仍抛); 其他错 raise
        if "already exists" not in str(_e).lower() and "duplicate" not in str(_e).lower():
            raise
    conn.execute(
        """
        UPDATE mart_paper_sim_nav
           SET trade_date_dt = CAST(date AS DATE)
         WHERE trade_date_dt IS NULL
           AND date IS NOT NULL
        """
    )
