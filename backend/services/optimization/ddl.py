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
    # Phase ψ.α: 改成幂等 CREATE (不 DROP). 业务表跨多次 Optuna run 累积:
    # 增量场景 (--formula reversal_*) 不应清掉历史 momentum 行.
    # 全量重建场景由 entry script 显式 DELETE FROM (在 INSERT 前).
    return f"""
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


def _per_formula_stage_optimal_ddl(table_name: str) -> str:
    """Phase ψ.α B — per-formula × stage × train_end_date 严格 walk-forward 表.

    设计原则 (CLAUDE.md Rule 7 + 用户原话 "真金白银, 不是数字游戏"):
      - PK = (formula_id, formula_variant, stage_filter, train_end_date)
      - 一行 = 在 train_end_date 当时, 用 < train_end_date 所有信号 train Optuna
              选出的 best params + 在 [train_end_date, +60d] forward 窗 OOS 验证
      - paper_sim 在历史时刻 t 选股: WHERE train_end_date <= t ORDER BY train_end_date DESC
        → 取"在 t 那天能算出的最近 train 版本", 0 selection leakage
      - 同 (formula × stage) 多行 (~每月底 1 行 = ~34 行), 跨所有股票合并 — 适合稀疏信号
    """
    return f"""
CREATE TABLE IF NOT EXISTS {table_name} (
    formula_id         TEXT NOT NULL,
    formula_variant    TEXT NOT NULL,
    stage_filter       TEXT NOT NULL,
    train_end_date     TEXT NOT NULL,            -- Phase ψ.α B: 严格 walk-forward, paper_sim 按此切片
    -- 5 维 strategy params (供 paper_sim 用)
    optimal_hp         INTEGER NOT NULL,
    optimal_stop_pct   REAL NOT NULL,
    optimal_target_pct REAL NOT NULL,
    optimal_trailing_pct REAL NOT NULL,
    optimal_buy_offset INTEGER NOT NULL DEFAULT 1,
    -- 4 维 K 线形态阈值
    optimal_body_ratio_min      REAL DEFAULT 0.0,
    optimal_lower_shadow_min    REAL DEFAULT 0.0,
    optimal_close_position_min  REAL DEFAULT 0.0,
    optimal_volume_relative_min REAL DEFAULT 0.0,
    -- in-sample (train, 全市场合并) metrics
    in_sample_n_traded INTEGER,
    in_sample_win_rate REAL,
    in_sample_avg_ret  REAL,
    in_sample_sharpe   REAL,
    in_sample_calmar   REAL,
    in_sample_avg_max_dd REAL,
    -- OOS metrics (业务真值)
    walk_forward_mode  TEXT,
    train_n_signals    INTEGER,
    test_n_signals     INTEGER,
    oos_sharpe         REAL,
    oos_win_rate       REAL,
    oos_avg_ret        REAL,
    oos_n_traded       INTEGER,
    oos_period_start   TEXT,
    oos_period_end     TEXT,
    oos_n_windows      INTEGER,
    oos_monthly_sharpe_std REAL,
    -- Optuna 元信息
    optuna_score       REAL,
    optuna_n_trials    INTEGER,
    n_signals_input    INTEGER,
    n_stocks_input     INTEGER,            -- 该 (formula × stage) 涵盖多少股
    -- 元
    execution_model_version TEXT,
    eval_start_date    TEXT,
    eval_end_date      TEXT,
    forward_days       INTEGER,                  -- OOS 窗大小 (默认 60)
    built_at           TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (formula_id, formula_variant, stage_filter, train_end_date)
);
CREATE INDEX IF NOT EXISTS idx_{table_name}_train_end ON {table_name}(train_end_date);
CREATE INDEX IF NOT EXISTS idx_{table_name}_formula ON {table_name}(formula_id);
CREATE INDEX IF NOT EXISTS idx_{table_name}_oos_sharpe ON {table_name}(oos_sharpe);
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


def _table_has_column(conn, table_name: str, column_name: str) -> bool:
    """检测表是否有指定列 (用于 schema 演化探测)."""
    try:
        rows = conn.execute(
            f"PRAGMA table_info('{table_name}')"
        ).fetchall()
        return any(r[1] == column_name for r in rows)
    except Exception:
        return False


def _table_exists(conn, table_name: str) -> bool:
    try:
        conn.execute(f"SELECT 1 FROM {table_name} LIMIT 0").fetchall()
        return True
    except Exception:
        return False


def ensure_optuna_tables(
    conn,
    cfg: Optional[OptunaConfig] = None,
    with_per_formula: bool = False,
) -> None:
    """幂等建 Optuna 治理 / 输出表 (含 schema 演化探测).

    Args:
        with_per_formula: True 时额外建 mart_per_formula_stage_optimal (Phase ψ.α)

    Schema 演化策略 (Rule 9.6):
      - per_formula 表 schema 改了 (Phase ψ.α B 加 train_end_date), 旧版表会缺列
      - 用 PRAGMA table_info 检测, 缺关键列就 DROP + REBUILD (数据期, 允许)
      - 一旦稳定上线后改成 ALTER TABLE 增量迁移
    """
    cfg = cfg or get_optuna_config()
    conn.executescript(_stage_optimal_ddl(cfg.output.stage_optimal_table))
    conn.executescript(_governance_log_ddl(cfg.output.governance_log_table))
    if with_per_formula:
        table = "mart_per_formula_stage_optimal"
        if _table_exists(conn, table) and not _table_has_column(conn, table, "train_end_date"):
            # 老 schema, schema 演化期 — DROP + REBUILD
            conn.execute(f"DROP TABLE {table}")
        conn.executescript(_per_formula_stage_optimal_ddl(table))


def log_governance_violations(
    conn,
    run_id: str,
    violations: list[dict],
    cfg: Optional[OptunaConfig] = None,
    manage_txn: bool = True,
) -> int:
    """把 governance.enforce_pre_insert 抛出的 GovernanceViolation 批量写审计表.

    violations: list of dicts with keys:
        stock_code, formula_id, formula_variant, stage_filter, reason, record_json

    manage_txn: True (默认) 自起 BEGIN/COMMIT 独立提交. False = 调用方已在事务内,
        本函数只发 INSERT 不碰事务边界, 让 governance 写与业务表写**同事务原子提交/回滚**
        (防 orphan governance: 业务写回滚而 governance 已落 = 审计与结果不一致).
        DuckDB 不支持嵌套事务, 故同事务场景必须传 manage_txn=False.

    Returns: 写入行数.
    """
    if not violations:
        return 0
    cfg = cfg or get_optuna_config()
    table = cfg.output.governance_log_table

    if manage_txn:
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
        if manage_txn:
            conn.execute("COMMIT")
    except BaseException:
        if manage_txn:
            try:
                conn.execute("ROLLBACK")
            except Exception: pass  # best-effort ROLLBACK; 原异常经下方 raise 保留 (同 caller 风格)
        raise
    return len(violations)
