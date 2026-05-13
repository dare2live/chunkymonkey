"""Phase ψ — Optuna 治理 / 输出表 DDL (Config-driven, 单一职责).

⚠ 表名 走 backend/config/optuna_config.yaml.output. Rule 7: 不许 hardcode 表名.
⚠ Optuna 入口脚本 (optimize_per_stock_*.py) 跑前调 ensure_optuna_tables(conn).

表设计:
  1. mart_per_stock_stage_strategy_optimal   stage-aware 寻优结果 (含 OOS 列)
  2. mart_per_stock_strategy_optimal         cross-stage 兜底 (含 OOS 列)
  3. fact_optuna_governance_log              治理 reject 审计 (谁/啥时/啥理由被 reject)
"""
from __future__ import annotations

from typing import Optional

from services.optimization.config import OptunaConfig, get_optuna_config


# 共用 OOS 列定义 (两张 optimal 表都用)
_OOS_COLUMNS_SQL = """
    -- ── Phase ψ OOS metrics (业务表只读这些, 不读 in-sample fit 字段) ──
    walk_forward_mode  TEXT,                       -- 'holdout' / 'expanding' / 'expanding_monthly' / 'none'(禁)
    train_n_signals    INTEGER,
    test_n_signals     INTEGER,
    oos_sharpe         REAL,
    oos_win_rate       REAL,
    oos_avg_ret        REAL,
    oos_n_traded       INTEGER,
    oos_period_start   TEXT,
    oos_period_end     TEXT,
    -- expanding_monthly 模式下: 每月 OOS 拼起来的统计
    oos_n_windows      INTEGER,                    -- 多少个月窗口贡献了 OOS
    oos_monthly_sharpe_std REAL                    -- 月度 sharpe std (越小越稳)
"""


def _stage_optimal_ddl(table_name: str) -> str:
    return f"""
DROP TABLE IF EXISTS {table_name};
CREATE TABLE IF NOT EXISTS {table_name} (
    stock_code         TEXT NOT NULL,
    formula_id         TEXT NOT NULL,
    formula_variant    TEXT NOT NULL,
    stage_filter       TEXT NOT NULL,
    optimal_hp         INTEGER NOT NULL,
    optimal_stop_pct   REAL NOT NULL,
    optimal_target_pct REAL NOT NULL,
    optimal_trailing_pct REAL NOT NULL,
    optimal_buy_offset INTEGER NOT NULL DEFAULT 1,
    optimal_body_ratio_min      REAL DEFAULT 0.0,
    optimal_lower_shadow_min    REAL DEFAULT 0.0,
    optimal_close_position_min  REAL DEFAULT 0.0,
    optimal_volume_relative_min REAL DEFAULT 0.0,
    -- ── in-sample (train 集) metrics — 仅描述, 业务代码不读 ──
    optimal_calmar       REAL,
    optimal_sortino      REAL,
    optimal_pain_index   REAL,
    optimal_ulcer_index  REAL,
    optimal_tail_risk    REAL,
    optimal_stability    REAL,
    n_signals_input    INTEGER,
    n_traded           INTEGER,
    n_blocked          INTEGER,
    win_rate           REAL,
    avg_ret            REAL,
    median_ret         REAL,
    avg_max_dd         REAL,
    avg_holding_days   REAL,
    sharpe             REAL,
    calmar             REAL,
    pct_exit_stop      REAL,
    pct_exit_trailing  REAL,
    pct_exit_target    REAL,
    pct_exit_hp        REAL,
    pct_exit_blocked   REAL,
    pct_exit_truncated REAL,
    optuna_score       REAL,
    optuna_n_trials    INTEGER,
    {_OOS_COLUMNS_SQL},
    execution_model_version TEXT,
    eval_start_date    TEXT,
    eval_end_date      TEXT,
    built_at           TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (stock_code, formula_id, formula_variant, stage_filter)
);
CREATE INDEX IF NOT EXISTS idx_{table_name}_oos_sharpe ON {table_name}(oos_sharpe);
CREATE INDEX IF NOT EXISTS idx_{table_name}_sc_var ON {table_name}(stock_code, formula_variant);
CREATE INDEX IF NOT EXISTS idx_{table_name}_stage ON {table_name}(stage_filter);
"""


def _governance_log_ddl(table_name: str) -> str:
    return f"""
CREATE TABLE IF NOT EXISTS {table_name} (
    run_id            TEXT NOT NULL,           -- 一次 Optuna 跑的 ID (UUID)
    rejected_at       TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    stock_code        TEXT,
    formula_id        TEXT,
    formula_variant   TEXT,
    stage_filter      TEXT,
    reason            TEXT NOT NULL,           -- governance.GovernanceViolation message
    -- 拒绝时的 record 全量 (JSON 序列化, 方便审计)
    record_json       TEXT,
    PRIMARY KEY (run_id, stock_code, formula_id, formula_variant, stage_filter, reason)
);
CREATE INDEX IF NOT EXISTS idx_{table_name}_run ON {table_name}(run_id);
CREATE INDEX IF NOT EXISTS idx_{table_name}_reason ON {table_name}(reason);
"""


def ensure_optuna_tables(
    conn,
    cfg: Optional[OptunaConfig] = None,
) -> None:
    """幂等建 3 张 Optuna 治理 / 输出表.

    用法 (在 optimize_per_stock_stage_strategy.py):
        from services.optimization.ddl import ensure_optuna_tables
        ensure_optuna_tables(conn)
    """
    cfg = cfg or get_optuna_config()
    conn.executescript(_stage_optimal_ddl(cfg.output.stage_optimal_table))
    # cross-stage 兜底表: 跟 stage_aware 共用 DDL (差别仅 PK 不含 stage_filter, 这里简化 也放 stage='')
    # 实际旧表 mart_per_stock_strategy_optimal 已存在不同 schema, 暂只确保 stage 表 + governance log.
    conn.executescript(_governance_log_ddl(cfg.output.governance_log_table))


def log_governance_violations(
    conn,
    run_id: str,
    violations: list[dict],
    cfg: Optional[OptunaConfig] = None,
) -> int:
    """把 governance.enforce_pre_insert 抛出的 GovernanceViolation 批量写审计表.

    violations: list of dicts with keys:
        stock_code, formula_id, formula_variant, stage_filter, reason, record_json

    Returns: 写入行数.
    """
    if not violations:
        return 0
    cfg = cfg or get_optuna_config()
    table = cfg.output.governance_log_table

    conn.execute("BEGIN TRANSACTION")
    try:
        conn.executemany(
            f"""INSERT OR IGNORE INTO {table}
                (run_id, stock_code, formula_id, formula_variant, stage_filter,
                 reason, record_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)""",
            [(run_id,
              v.get("stock_code", ""), v.get("formula_id", ""),
              v.get("formula_variant", ""), v.get("stage_filter", ""),
              v.get("reason", ""), v.get("record_json", ""))
             for v in violations],
        )
        conn.execute("COMMIT")
    except BaseException:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        raise
    return len(violations)
