"""SEF Phase I schema migration.

幂等建表 + ALTER 扩列，执行前检查 PRAGMA 避免重复报错。
所有表命名遵循 docs/SEF_MASTER_PLAN.md §5。
"""

from __future__ import annotations

import logging
import sqlite3
from typing import Iterable

logger = logging.getLogger("cm-api.sef.schema")

SCHEMA_VERSION = "sef_phase1_v1"

# ---------------------------------------------------------------------------
# 1) ALTER 扩列
# ---------------------------------------------------------------------------

# fact_institution_event 扩列：chain 关联 + open/closed PnL 状态
_EVENT_EXTRA_COLUMNS = [
    ("chain_id", "INTEGER"),
    ("follow_pnl_to_eval", "REAL"),
    ("follow_maxdd_to_eval", "REAL"),
    ("inst_pnl_to_eval", "REAL"),
    ("eval_status", "TEXT"),
]

# research_holding_chains 扩列：DGTW 归因 + 衰减半衰期
_CHAIN_EXTRA_COLUMNS = [
    ("chain_alpha", "REAL"),
    ("chain_industry_beta", "REAL"),
    ("chain_style_beta_json", "TEXT"),
    ("chain_top_factors_json", "TEXT"),
    ("alpha_halflife_days", "REAL"),
]


def _existing_cols(conn: sqlite3.Connection, table: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {r[1] for r in rows}


def _add_columns_if_missing(
    conn: sqlite3.Connection, table: str, columns: Iterable[tuple[str, str]]
) -> list[str]:
    existing = _existing_cols(conn, table)
    added: list[str] = []
    for name, ctype in columns:
        if name not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ctype}")
            added.append(name)
    return added


# ---------------------------------------------------------------------------
# 2) 新建表
# ---------------------------------------------------------------------------

_DDL = [
    # ---- α 真相层（Layer 1 核心输入） ----
    """
    CREATE TABLE IF NOT EXISTS fact_chain_alpha_truth (
        chain_id              INTEGER PRIMARY KEY AUTOINCREMENT,
        institution_id        TEXT NOT NULL,
        stock_code            TEXT NOT NULL,
        research_chain_id     INTEGER NOT NULL,  -- research_holding_chains.chain_id
        entry_date            TEXT NOT NULL,
        exit_date             TEXT,
        eval_date             TEXT NOT NULL,
        status                TEXT NOT NULL CHECK(status IN ('closed','open')),

        entry_price           REAL,
        eval_price            REAL,
        entry_inst_cost       REAL,
        exit_inst_cost        REAL,

        chain_inst_pnl        REAL,
        chain_follow_pnl      REAL,
        chain_follow_max_dd   REAL,
        chain_days            INTEGER,

        tb_upper_hit          INTEGER DEFAULT 0,
        tb_lower_hit          INTEGER DEFAULT 0,
        tb_time_hit           INTEGER DEFAULT 0,
        tb_label              TEXT,
        tb_upper_level        REAL,
        tb_lower_level        REAL,
        tb_time_horizon_days  INTEGER,
        tb_trigger_date       TEXT,

        dgtw_selection_alpha  REAL,
        dgtw_timing_alpha     REAL,
        dgtw_style_alpha      REAL,

        industry_l1           TEXT,
        industry_l2           TEXT,
        updated_at            TEXT
    )
    """,
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_chain_alpha_truth_research ON fact_chain_alpha_truth(institution_id, stock_code, research_chain_id)",
    "CREATE INDEX IF NOT EXISTS idx_chain_alpha_truth_inst ON fact_chain_alpha_truth(institution_id)",
    "CREATE INDEX IF NOT EXISTS idx_chain_alpha_truth_stock ON fact_chain_alpha_truth(stock_code)",
    "CREATE INDEX IF NOT EXISTS idx_chain_alpha_truth_date ON fact_chain_alpha_truth(eval_date)",
    # ---- Layer 1 产出：机构能力 ----
    """
    CREATE TABLE IF NOT EXISTS mart_institution_capability (
        institution_id        TEXT NOT NULL,
        industry_level        TEXT NOT NULL CHECK(industry_level IN ('L1','L2')),
        industry_code         TEXT NOT NULL,
        alpha_median          REAL,
        alpha_se              REAL,
        alpha_ci_lower_90     REAL,
        sample_count          INTEGER,
        sharpe                REAL,
        max_dd_median         REAL,
        expert_level          INTEGER,
        alpha_halflife_days   REAL,
        alpha_decay_tau_star  INTEGER,
        last_updated          TEXT,
        PRIMARY KEY (institution_id, industry_level, industry_code)
    )
    """,
    # ---- Layer 2B 产出：机构风格 ----
    """
    CREATE TABLE IF NOT EXISTS mart_institution_style (
        institution_id        TEXT PRIMARY KEY,
        style_exposure_json   TEXT,
        style_alpha_pure      REAL,
        style_r2              REAL,
        drift_flag            INTEGER DEFAULT 0,
        drift_psi             REAL,
        drift_ks_pvalue       REAL,
        last_updated          TEXT
    )
    """,
    # ---- Layer 2A 产出：股性嵌入 ----
    """
    CREATE TABLE IF NOT EXISTS fact_stock_character (
        stock_code            TEXT PRIMARY KEY,
        embedding_json        TEXT,
        beta_inst_entry       REAL,
        beta_holder_decline   REAL,
        beta_margin_surge     REAL,
        beta_survey_surge     REAL,
        noise_floor           REAL,
        info_lag_days         REAL,
        elasticity_sector     REAL,
        last_updated          TEXT
    )
    """,
    # ---- Layer 6 闭环：信号日志 ----
    """
    CREATE TABLE IF NOT EXISTS model_signals_log (
        signal_id             INTEGER PRIMARY KEY AUTOINCREMENT,
        signal_date           TEXT NOT NULL,
        stock_code            TEXT NOT NULL,
        institution_id        TEXT,
        source                TEXT,
        predicted_alpha       REAL,
        predicted_sigma       REAL,
        predicted_holddays    INTEGER,
        confidence            REAL,
        model_version         TEXT,
        feature_snapshot_json TEXT,
        recommended_weight    REAL,
        tag                   TEXT,
        created_at            TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_sig_date ON model_signals_log(signal_date)",
    "CREATE INDEX IF NOT EXISTS idx_sig_stock ON model_signals_log(stock_code, signal_date)",
    "CREATE INDEX IF NOT EXISTS idx_sig_inst ON model_signals_log(institution_id, signal_date)",
    """
    CREATE TABLE IF NOT EXISTS model_signals_realized (
        signal_id             INTEGER PRIMARY KEY,
        realized_pnl_1d       REAL,
        realized_pnl_5d       REAL,
        realized_pnl_20d      REAL,
        realized_pnl_60d      REAL,
        realized_pnl_to_now   REAL,
        realized_maxdd_to_now REAL,
        exit_trigger          TEXT,
        closed                INTEGER DEFAULT 0,
        last_updated          TEXT,
        FOREIGN KEY (signal_id) REFERENCES model_signals_log(signal_id)
    )
    """,
    # ---- 模型版本管理 ----
    """
    CREATE TABLE IF NOT EXISTS model_state (
        model_id              TEXT PRIMARY KEY,
        model_type            TEXT NOT NULL,
        version               INTEGER NOT NULL,
        train_start           TEXT,
        train_end             TEXT,
        train_samples         INTEGER,
        hyperparams_json      TEXT,
        valid_ic              REAL,
        valid_ir              REAL,
        valid_sharpe          REAL,
        status                TEXT CHECK(status IN ('training','active','shadow','retired')),
        model_path            TEXT,
        created_at            TEXT,
        activated_at          TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_model_state_type ON model_state(model_type, status)",
    # ---- Regime 状态（Layer 0） ----
    """
    CREATE TABLE IF NOT EXISTS fact_regime_state (
        trade_date            TEXT PRIMARY KEY,
        regime_id             INTEGER,
        regime_label          TEXT,
        regime_prob_json      TEXT,
        transition_signal     INTEGER DEFAULT 0
    )
    """,
    # ---- 漂移监控 ----
    """
    CREATE TABLE IF NOT EXISTS institution_drift_log (
        institution_id        TEXT,
        eval_date             TEXT,
        psi                   REAL,
        ks_pvalue             REAL,
        confidence_mult       REAL,
        alert_level           TEXT CHECK(alert_level IN ('stable','mild','severe')),
        PRIMARY KEY (institution_id, eval_date)
    )
    """,
    # ---- Walk-Forward 回测记录 ----
    """
    CREATE TABLE IF NOT EXISTS backtest_walk_forward (
        model_id              TEXT,
        fold_id               INTEGER,
        fold_start            TEXT,
        fold_end              TEXT,
        n_samples             INTEGER,
        oos_ic                REAL,
        oos_rank_ic           REAL,
        oos_sharpe            REAL,
        oos_maxdd             REAL,
        oos_hit_rate          REAL,
        oos_turnover          REAL,
        oos_ir                REAL,
        PRIMARY KEY (model_id, fold_id)
    )
    """,
    # ---- Layer 4 输出：每日组合 ----
    """
    CREATE TABLE IF NOT EXISTS portfolio_recommendation_daily (
        signal_date           TEXT,
        stock_code            TEXT,
        weight                REAL,
        expected_alpha        REAL,
        expected_sigma        REAL,
        sector                TEXT,
        rationale_json        TEXT,
        PRIMARY KEY (signal_date, stock_code)
    )
    """,
    # ---- 防幸存者偏差：全量曾上市股 ----
    """
    CREATE TABLE IF NOT EXISTS dim_all_ever_listed (
        stock_code            TEXT PRIMARY KEY,
        stock_name            TEXT,
        first_seen_date       TEXT,
        last_seen_date        TEXT,
        is_active             INTEGER DEFAULT 1,
        delisted_date         TEXT,
        source                TEXT,
        updated_at            TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_all_ever_active ON dim_all_ever_listed(is_active)",
    # ---- schema 版本记录 ----
    """
    CREATE TABLE IF NOT EXISTS sef_schema_version (
        version_id            TEXT PRIMARY KEY,
        applied_at            TEXT
    )
    """,
]


def migrate_phase1(conn: sqlite3.Connection) -> dict:
    """幂等执行 Phase I 全部 schema 变更。返回变更报告。"""
    report: dict = {"altered": {}, "created": [], "indexes": 0, "version": SCHEMA_VERSION}

    added = _add_columns_if_missing(conn, "fact_institution_event", _EVENT_EXTRA_COLUMNS)
    if added:
        report["altered"]["fact_institution_event"] = added

    added = _add_columns_if_missing(conn, "research_holding_chains", _CHAIN_EXTRA_COLUMNS)
    if added:
        report["altered"]["research_holding_chains"] = added

    for stmt in _DDL:
        stmt_clean = stmt.strip()
        conn.execute(stmt_clean)
        if stmt_clean.upper().startswith("CREATE TABLE"):
            table_name = stmt_clean.split()[5].strip("(").strip()
            report["created"].append(table_name)
        elif stmt_clean.upper().startswith("CREATE INDEX") or stmt_clean.upper().startswith(
            "CREATE UNIQUE INDEX"
        ):
            report["indexes"] += 1

    conn.execute(
        "INSERT OR REPLACE INTO sef_schema_version(version_id, applied_at) VALUES (?, datetime('now'))",
        (SCHEMA_VERSION,),
    )
    conn.commit()

    logger.info("[SEF] Phase I schema migrated: %s", report)
    return report
