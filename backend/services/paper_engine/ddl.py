"""Phase δ — Paper Engine 4 张新表 DDL。

Schema 灵感来自现有的:
  - mart_model_portfolio_curve (NAV 模板, 去掉 run_id/curve_id)
  - mart_synergy_policy_mtm_position (持仓模板, 去掉 synergy 专属字段)
  - mart_prediction_outcome (decision_outcome 雏形, 自带 ret_5/10/30d + IC)

我们建独立 mart_decision_outcome (带 paper_v1 model_id) 让 Phase ε 反馈层有清晰来源,
不直接依赖 ml_lifecycle 的 mart_prediction_outcome (避免耦合 ML 周期)。
"""
from __future__ import annotations


# =================================================================
# 1. mart_paper_nav — 每日 NAV 曲线 + 基准对比
# =================================================================
# 每跑一日产 1 行
MART_PAPER_NAV_DDL = """
CREATE TABLE IF NOT EXISTS mart_paper_nav (
    snapshot_date         TEXT NOT NULL,
    -- 主组合
    nav                   REAL NOT NULL,        -- 当日净值 (起点 1.0)
    nav_value             REAL NOT NULL,        -- 当日总市值 (initial_capital × nav)
    daily_ret             REAL,                  -- 当日日收益率
    cum_ret               REAL,                  -- 累计收益率
    -- 基准: HS300
    hs300_nav             REAL,
    hs300_cum_ret         REAL,
    vs_hs300_cum_ret      REAL,                  -- (组合-HS300) 超额累计
    -- 基准: 等权 (用同 universe topk 等权)
    eqw_nav               REAL,
    eqw_cum_ret           REAL,
    vs_eqw_cum_ret        REAL,
    -- 当日组合状态
    cash                  REAL,                  -- 现金余额
    position_count        INTEGER,               -- 持仓股数
    turnover_pct          REAL,                  -- 当日换手率
    drawdown              REAL,                  -- 当前回撤
    -- 配置
    model_id              TEXT NOT NULL DEFAULT 'v1',
    initial_capital       REAL,
    built_at              TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (snapshot_date, model_id)
);
CREATE INDEX IF NOT EXISTS idx_mpn_date ON mart_paper_nav(snapshot_date);
"""


# =================================================================
# 2. fact_paper_position — 持仓事件 (开仓 / 调仓 / 平仓)
# =================================================================
# 每次买入开仓 / 每次卖出平仓 各 1 行 (一笔交易 1 行, side='buy' or 'sell')
FACT_PAPER_POSITION_DDL = """
CREATE TABLE IF NOT EXISTS fact_paper_position (
    event_date            TEXT NOT NULL,
    stock_code            TEXT NOT NULL,
    side                  TEXT NOT NULL,        -- 'buy' / 'sell'
    qty                   REAL,
    ref_price             REAL,                  -- 参考价 (close 前复权)
    exec_price            REAL,                  -- 执行价 (含滑点)
    slip_bps              REAL,                  -- 滑点 bps
    notional              REAL,                  -- qty × exec_price
    -- 仅 sell 行: 计算盈亏
    entry_date            TEXT,                  -- 对应买入日 (sell 时填)
    entry_price           REAL,
    holding_days          INTEGER,
    gross_return          REAL,                  -- (exit - entry)/entry
    net_return            REAL,                  -- 扣滑点后
    -- 元
    model_id              TEXT NOT NULL DEFAULT 'v1',
    reason                TEXT,                  -- 'topk_new' / 'stop_hit' / 'target_hit' / 'rebalance' / 'horizon_exit'
    built_at              TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (event_date, stock_code, side, model_id)
);
CREATE INDEX IF NOT EXISTS idx_fpp_date  ON fact_paper_position(event_date);
CREATE INDEX IF NOT EXISTS idx_fpp_stock ON fact_paper_position(stock_code);
"""


# =================================================================
# 3. mart_signal_ic — 每公式每日 IC (Spearman)
# =================================================================
# 用法: 对每个公式, 取当日触发信号 + 5/10/30 日后 forward return → Spearman
MART_SIGNAL_IC_DDL = """
CREATE TABLE IF NOT EXISTS mart_signal_ic (
    snapshot_date         TEXT NOT NULL,
    formula_id            TEXT NOT NULL,
    formula_variant       TEXT,
    n_signals             INTEGER,
    ic_5d                 REAL,
    ic_10d                REAL,
    ic_30d                REAL,
    rank_ic_5d            REAL,                  -- alias for Spearman, 留向后兼容
    rank_ic_10d           REAL,
    rank_ic_30d           REAL,
    built_at              TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (snapshot_date, formula_id, formula_variant)
);
CREATE INDEX IF NOT EXISTS idx_msi_date    ON mart_signal_ic(snapshot_date);
CREATE INDEX IF NOT EXISTS idx_msi_formula ON mart_signal_ic(formula_id);
"""


# =================================================================
# 4. mart_decision_outcome — 每笔 BUY 决策的后续结果 (ε 反馈用)
# =================================================================
MART_DECISION_OUTCOME_DDL = """
CREATE TABLE IF NOT EXISTS mart_decision_outcome (
    decision_date         TEXT NOT NULL,
    stock_code            TEXT NOT NULL,
    model_id              TEXT NOT NULL DEFAULT 'paper_v1',
    decision_type         TEXT NOT NULL,         -- 'BUY' / 'SELL' / 'HOLD' (Phase δ 写 BUY)
    rank_in_date          INTEGER,
    pred_score            REAL,
    primary_formula_id    TEXT,
    industry_l1           TEXT,
    entry_price           REAL,
    -- 后续 5/10/30 日 forward return + 最大回撤
    fwd_ret_5d            REAL,
    fwd_ret_10d           REAL,
    fwd_ret_30d           REAL,
    fwd_max_dd_30d        REAL,
    -- 分类结果 (UI 显示)
    outcome_5d            TEXT,                  -- 'win' / 'loss' / 'flat' / 'active'
    outcome_10d           TEXT,
    outcome_30d           TEXT,
    -- 元
    n_similar             INTEGER DEFAULT 0,     -- Phase ε 后接 cohort
    built_at              TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (decision_date, stock_code, model_id)
);
CREATE INDEX IF NOT EXISTS idx_mdo_date    ON mart_decision_outcome(decision_date);
CREATE INDEX IF NOT EXISTS idx_mdo_formula ON mart_decision_outcome(primary_formula_id);
"""


def ensure_paper_tables(conn) -> None:
    """幂等建表 (4 张表)。"""
    conn.executescript(MART_PAPER_NAV_DDL)
    conn.executescript(FACT_PAPER_POSITION_DDL)
    conn.executescript(MART_SIGNAL_IC_DDL)
    conn.executescript(MART_DECISION_OUTCOME_DDL)
    conn.commit()
