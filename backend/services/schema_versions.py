"""派生层 schema 版本登记 — P0.1 (2026-04-28).

为什么需要:
- chunky-monkey-v2 有 70 张派生表 (fact 23 + mart 28 + dim 19 + 2 view)
- 升级表结构 (加列/改列/重建 view) 时容易踩"schema drift"雷:
    例如 mart_model_validation_fold view = SELECT * FROM 底表, 底表加列后
    DuckDB 拒绝查 view (BinderException). 表面无改动, 但 API 500.
- 没有版本号机制 → 升级 schema 后旧代码读旧 schema, 新代码读新 schema, 沉默崩.

设计:
1. SCHEMA_VERSIONS 字典 (本文件) — 代码声明每张派生表当前期望的版本号
2. dim_schema_version 表 (DuckDB) — 记录每张表实际数据是哪个版本生成的
3. init_db 启动时:
   - ensure_schema_version_table 建表
   - recreate_views 自动 DROP + CREATE 所有 view (防底表 schema drift)
   - detect_drift 报告 expected != actual 的表
4. build_xxx / rebuild_xxx 完成时调 record_actual_version 更新 actual

升级流程:
- 改表结构 → SCHEMA_VERSIONS[table] += 1 (v1 → v2)
- 启动 backend → 看到 [schema drift] WARN
- 工作台 / 系统页 看到 expected≠actual → 触发对应 rebuild
- rebuild_xxx 函数末尾 record_actual_version(conn, table) → drift 消失

注意:
- 不是 ORM, 不强制. 主要是发现机制 + 元数据.
- 没接到所有 build 函数 (70 张表代价大). 用户重算后系统页有"全部标记为最新"按钮.
"""
from __future__ import annotations

import logging

logger = logging.getLogger("cm-api.schema_versions")


# ===========================================================================
# 派生层版本声明 (代码即真相)
# ===========================================================================

# fact_* (事件/快照层, 23 张)
FACT_VERSIONS = {
    "fact_institution_event": "v1",          # 机构事件 (核心)
    "fact_setup_snapshot": "v1",             # setup 快照
    "fact_chain_alpha_truth": "v1",          # 链路 alpha 真值
    "fact_feature_panel": "v1",              # 特征面板 (Qlib alpha158)
    "fact_feature_panel_candidate": "v1",    # 候选特征面板 (不替换 champion)
    "fact_dzjy_event": "v1",                 # 大宗交易事件
    "fact_executive_trade_event": "v1",      # 高管交易事件
    "fact_jgdy_event": "v1",                 # 机构调研事件
    "fact_lhb_event": "v1",                  # 龙虎榜事件
    "fact_hsgt_daily": "v1",                 # 沪深港通日度
    "fact_financial_derived": "v1",          # 财务派生
    "fact_financial_indicator_ak": "v1",     # akshare 财务指标
    "fact_fundamental_quarterly": "v1",      # 基本面季度
    "fact_institution_follow_backtest": "v1",  # 机构跟随回测
    "fact_policy_equity_curve": "v1",        # 策略 equity curve
    "fact_policy_eval": "v1",                # 策略评估
    "fact_policy_trade": "v1",               # 策略交易明细
    "fact_regime_state": "v1",               # 市场 regime
    "fact_stock_archetype": "v1",            # 股票原型
    "fact_stock_attention_snapshot": "v1",   # 关注度快照
    "fact_stock_industry_context": "v1",     # 行业上下文
    "fact_stock_quality_features": "v1",     # 质量特征
    "fact_stock_stage_features": "v1",       # 阶段特征
    "fact_stock_turtle_features": "v1",      # 海龟特征
    "fact_risk_factors": "v1",               # P1.6 风险因子 (vol/sharpe/dd/mom/skew/kurt)
}

# mart_* (集市层, 可重算, 32 张)
MART_VERSIONS = {
    "mart_audit_snapshot_state": "v1",
    "mart_current_relationship": "v1",       # 当前持仓关系 ⭐
    "mart_daily_recommendation": "v1",       # 每日推荐 topK
    "mart_daily_recommendation_risk": "v1",
    "mart_dual_confirm": "v1",               # 双重确认
    "mart_etf_snapshot_latest": "v1",
    "mart_etf_snapshot_state": "v1",
    "mart_institution_industry_stat": "v1",  # 机构行业统计
    "mart_institution_profile": "v1",        # 机构画像 ⭐
    "mart_model_ablation_run": "v1",
    "mart_feature_candidate_score": "v1",
    "mart_feature_group_ablation": "v1",
    "mart_feature_pit_audit": "v1",
    "mart_candidate_walkforward_eval": "v1",
    "mart_feature_retention_decision": "v1",
    "mart_model_selection_run": "v1",
    "mart_tdx_challenger_report": "v1",
    "mart_data_deprecation_record": "v1",
    "mart_model_portfolio_curve": "v1",
    "mart_model_portfolio_summary": "v1",    # 模型组合表现 (cost/return/sharpe)
    "mart_model_walkforward_fold": "v1",     # walkforward 切分 ⭐
    "mart_model_walkforward_portfolio_summary": "v1",
    "mart_model_walkforward_prediction": "v1",
    "mart_multidim_model": "v1",             # 多维模型注册
    "mart_multidim_prediction": "v1",        # 多维模型预测
    "mart_sector_momentum": "v1",            # 板块动量
    "mart_step_fingerprint": "v1",           # step 指纹 (增量驱动)
    "mart_stock_screening": "v1",            # 选股结果
    "mart_stock_survey_activity": "v1",      # 调研活动
    "mart_stock_trend": "v1",                # 股票趋势 ⭐
    "mart_prediction_outcome": "v1",         # P2.8 预测 outcome tracker
    "mart_ensemble_signals": "v1",           # P3.11 多策略 ensemble
}

# dim_* 派生类 (静态/缓存型, 不含 raw dim, 12 张)
DIM_DERIVED_VERSIONS = {
    "dim_capital_behavior_latest": "v1",
    "dim_financial_indicator_latest": "v1",
    "dim_financial_latest": "v1",
    "dim_stock_archetype_latest": "v1",
    "dim_stock_attention_latest": "v1",
    "dim_stock_industry_context_latest": "v1",
    "dim_stock_quality_latest": "v1",
    "dim_stock_stage_latest": "v1",
    "dim_stock_turtle_latest": "v1",
}

# 合并: 业务派生表全集 (raw_* / dim_active_a_stock / dim_trading_calendar 等不进, 它们靠 sync_raw 维护)
SCHEMA_VERSIONS = {**FACT_VERSIONS, **MART_VERSIONS, **DIM_DERIVED_VERSIONS}


# ===========================================================================
# View: 启动时 DROP + CREATE OR REPLACE, 防底表 schema drift
# (model-performance 500 那次的 mart_model_validation_fold view 就是这种问题)
# ===========================================================================

RECREATE_VIEWS = {
    "mart_model_validation_fold": "SELECT * FROM mart_model_walkforward_fold",
    # 加 view: { name: SELECT 语句 }
}


# ===========================================================================
# DB 操作
# ===========================================================================

def ensure_schema_version_table(conn) -> None:
    """幂等建 dim_schema_version 表."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS dim_schema_version (
            table_name        TEXT PRIMARY KEY,
            expected_version  TEXT NOT NULL,
            actual_version    TEXT,
            rebuilt_at        TIMESTAMP,
            notes             TEXT
        )
    """)
    conn.commit()


def recreate_views(conn) -> dict[str, str]:
    """启动时 DROP + CREATE 所有声明的 view. 返回 {view_name: 'ok' | 'fail: msg'}."""
    out = {}
    for view_name, sql in RECREATE_VIEWS.items():
        try:
            conn.execute(f"DROP VIEW IF EXISTS {view_name}")
            conn.execute(f"CREATE VIEW {view_name} AS {sql}")
            out[view_name] = "ok"
        except Exception as exc:
            out[view_name] = f"fail: {exc}"
            logger.warning(f"[schema_version] view {view_name} 重建失败: {exc}")
    conn.commit()
    return out


def record_actual_version(conn, table_name: str, version: str | None = None) -> None:
    """升级 dim_schema_version 的 actual_version + rebuilt_at.

    build_xxx / rebuild_xxx 函数末尾调用. version=None 时取 SCHEMA_VERSIONS[table_name].
    """
    expected = SCHEMA_VERSIONS.get(table_name, "v1")
    actual = version or expected
    conn.execute("""
        INSERT INTO dim_schema_version (table_name, expected_version, actual_version, rebuilt_at)
        VALUES (?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(table_name) DO UPDATE SET
            expected_version = excluded.expected_version,
            actual_version   = excluded.actual_version,
            rebuilt_at       = excluded.rebuilt_at
    """, [table_name, expected, actual])


def record_all_baselines(conn) -> int:
    """首次启动: 把所有 SCHEMA_VERSIONS 写为 actual=expected (假定当前 DB 数据是 v1).

    用户重算派生层后也调用. 返回写入行数.
    """
    n = 0
    for table_name, expected in SCHEMA_VERSIONS.items():
        # 只在表存在时记录 (避免污染)
        try:
            row = conn.execute(
                "SELECT 1 FROM information_schema.tables WHERE table_name = ?",
                [table_name],
            ).fetchone()
            if row:
                record_actual_version(conn, table_name, expected)
                n += 1
        except Exception as exc:
            logger.debug(f"[schema_version] 跳过 {table_name}: {exc}")
    conn.commit()
    return n


def detect_drift(conn) -> list[dict]:
    """返回所有 expected != actual 或 actual 为空的表."""
    try:
        db_rows = conn.execute("""
            SELECT table_name, expected_version, actual_version,
                   CAST(rebuilt_at AS VARCHAR) AS rebuilt_at
            FROM dim_schema_version
        """).fetchall()
    except Exception:
        # 表还没建
        return [
            {"table_name": t, "expected": v, "actual": None, "drift_type": "table_missing",
             "rebuilt_at": None}
            for t, v in SCHEMA_VERSIONS.items()
        ]

    db_state = {r[0]: {"expected": r[1], "actual": r[2], "rebuilt_at": r[3]} for r in db_rows}
    drifts = []
    for table, expected in SCHEMA_VERSIONS.items():
        st = db_state.get(table)
        if not st or not st["actual"]:
            # 没有记录: 可能是表还没建, 或者首次启动
            # 看表是否存在 — 存在则报 never_recorded, 否则跳过
            try:
                exists = conn.execute(
                    "SELECT 1 FROM information_schema.tables WHERE table_name = ?",
                    [table],
                ).fetchone()
            except Exception:
                exists = None
            if exists:
                drifts.append({
                    "table_name": table,
                    "expected": expected,
                    "actual": None,
                    "drift_type": "never_recorded",
                    "rebuilt_at": None,
                })
        elif st["actual"] != expected:
            drifts.append({
                "table_name": table,
                "expected": expected,
                "actual": st["actual"],
                "drift_type": "version_mismatch",
                "rebuilt_at": st["rebuilt_at"],
            })
    return drifts


def list_all_versions(conn) -> list[dict]:
    """系统页 UI 用 — 列出所有 expected 表 + 实际状态."""
    try:
        db_rows = conn.execute("""
            SELECT table_name, expected_version, actual_version,
                   CAST(rebuilt_at AS VARCHAR) AS rebuilt_at
            FROM dim_schema_version
        """).fetchall()
    except Exception:
        db_rows = []
    db_state = {r[0]: r for r in db_rows}

    out = []
    for table, expected in SCHEMA_VERSIONS.items():
        layer = (
            "fact" if table.startswith("fact_")
            else "mart" if table.startswith("mart_")
            else "dim_derived" if table.startswith("dim_")
            else "?"
        )
        st = db_state.get(table)
        actual = st[2] if st else None
        rebuilt_at = st[3] if st else None
        drift = (st is None) or (actual != expected)
        out.append({
            "table_name": table,
            "layer": layer,
            "expected_version": expected,
            "actual_version": actual,
            "rebuilt_at": rebuilt_at,
            "drift": drift,
        })
    return out


# ===========================================================================
# Public summary
# ===========================================================================

def summary() -> dict:
    return {
        "n_fact": len(FACT_VERSIONS),
        "n_mart": len(MART_VERSIONS),
        "n_dim_derived": len(DIM_DERIVED_VERSIONS),
        "n_views": len(RECREATE_VIEWS),
        "total": len(SCHEMA_VERSIONS),
    }
