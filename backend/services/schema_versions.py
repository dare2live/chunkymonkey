"""派生层 schema 版本登记 — P0.1 (2026-04-28).

为什么需要:
- chunkymonkey 有多张派生表和少量可选兼容 view.
- 升级表结构 (加列/改列/重建 view) 时容易踩"schema drift"雷:
    兼容 view 若长期存在, 底表加列后 DuckDB 可能拒绝查询旧 view.
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
    "fact_institution_event": "v2",          # 机构事件 (公告日 source lineage)
    "fact_chain_alpha_truth": "v1",          # 链路 alpha 真值
    "fact_feature_panel": "v4",              # 特征面板 (含 follow_net_return_* 标签 + K线来源血缘)
    "fact_dzjy_event": "v1",                 # 大宗交易事件
    "fact_executive_trade_event": "v1",      # 高管交易事件
    "fact_jgdy_event": "v1",                 # 机构调研事件
    "fact_lhb_event": "v1",                  # 龙虎榜事件
    "fact_financial_derived": "v1",          # 财务派生
    "fact_financial_indicator_ak": "v1",     # akshare 财务指标
    "fact_fundamental_quarterly": "v1",      # 基本面季度
    "fact_institution_follow_backtest": "v2",  # 机构跟随回测 (pricing_policy_hash)
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
    # Phase β/γ/δ/ε 新增 (2026-05-12)
    "fact_technical_trigger": "v1",          # Phase β: 公式触发信号
    "fact_stock_technical_stage": "v1",      # Phase β: Stan Weinstein 4 stage
    "fact_stock_fundamental_stage_daily": "v1",  # Phase γ: 基本面阶段 PIT 日快照
    "fact_stock_type_daily": "v1",           # Phase γ: 5 状态股票类型
    "fact_paper_position": "v1",             # Phase δ: 虚拟交易事件
    "fact_stock_selection_log": "v1",        # Phase ε: 选股事件 PIT log
    "fact_signal_context": "v1",             # Phase ε++: 每日每股 5 维上下文 (vol/amt/price_pos/stage)
    # §3.4 十项基础设施 fact 类
    "fact_daily_price_status": "v1",         # 一字板 / 涨跌停 / 停牌
    "fact_stock_liquidity_daily": "v1",      # 流动性
    "fact_stock_style_daily": "v1",          # 风格因子 z-score
    "fact_stock_market_cap_daily": "v1",     # 市值
}

# mart_* (集市层, 可重算, 32 张)
MART_VERSIONS = {
    "mart_current_relationship": "v1",       # 当前持仓关系 ⭐
    "mart_daily_recommendation": "v1",       # 每日推荐 topK
    "mart_daily_recommendation_risk": "v1",
    "mart_daily_topk_view_cache": "v1",
    "mart_model_ablation_run": "v1",
    "mart_feature_association_stat": "v2",
    "mart_feature_correlation_cluster": "v1",
    "mart_feature_association_fold": "v1",
    "mart_feature_rank_matrix_proxy_stat": "v1",
    "mart_feature_rank_matrix_benchmark": "v1",
    "mart_feature_rank_matrix_cache_manifest": "v1",
    "mart_feature_search_space": "v1",
    "mart_feature_search_space_summary": "v1",
    "mart_optuna_feature_space_trial": "v1",
    "mart_model_stability_search_trial": "v6",
    "mart_model_stability_search_summary": "v1",
    "mart_model_stability_context_diagnostic": "v1",
    "mart_model_stability_context_summary": "v2",
    "mart_drift_safe_candidate_feature": "v1",
    "mart_drift_safe_candidate_summary": "v1",
    "mart_drift_safe_candidate_batch_eval": "v1",
    "mart_drift_safe_candidate_batch_summary": "v1",
    "mart_feature_drift_root_cause": "v1",
    "mart_feature_drift_root_cause_summary": "v1",
    "mart_feature_drift_mitigation_panel_build": "v1",
    "mart_hybrid_feature_panel_build": "v1",
    "mart_stock_horizon_profile": "v3",
    "mart_stock_horizon_feature_effect": "v2",
    "mart_stock_horizon_selection": "v1",
    "mart_feature_pit_coverage_summary": "v1",
    "mart_feature_panel_validation": "v1",
    "mart_feature_panel_prune_run": "v1",
    "mart_feature_candidate_coverage": "v1",
    "mart_feature_drift_histogram": "v1",
    "mart_challenger_evidence_bundle": "v1",
    "mart_champion_candidate_evaluation": "v1",
    "mart_tdx_f10_capability_matrix": "v1",
    "mart_tdx_f10_source_date_section_audit": "v3",
    "mart_shareholder_plan_initial_event": "v1",
    "mart_shareholder_plan_initial_feature_panel": "v1",
    "mart_shareholder_plan_initial_feature_panel_quality": "v1",
    "mart_shareholder_plan_feature_family_eval": "v1",
    "mart_shareholder_plan_family_walkforward": "v1",
    "mart_shareholder_plan_family_walkforward_summary": "v1",
    "mart_feature_catalog_current": "v1",
    "mart_feature_pit_join_plan": "v1",
    "mart_feature_exclusion_reason": "v1",
    "mart_model_explanation": "v1",
    "mart_daily_recommendation_explanation": "v1",
    "mart_temporal_research_panel": "v1",
    "mart_temporal_research_panel_quality": "v1",
    "mart_feature_temporal_relevance": "v1",
    "mart_feature_bucket_effect": "v1",
    "mart_feature_relevance_stability": "v1",
    "mart_feature_pair_synergy": "v1",
    "mart_feature_interaction_candidate": "v1",
    "mart_feature_conditional_synergy": "v1",
    "mart_feature_redundancy_pair": "v1",
    "mart_feature_cluster_redundancy": "v1",
    "mart_optuna_synergy_trial": "v1",
    "mart_optuna_synergy_study_summary": "v1",
    "mart_synergy_policy_candidate": "v1",
    "mart_synergy_policy_walkforward": "v2",
    "mart_synergy_policy_gate": "v2",
    "mart_synergy_policy_evidence_bundle": "v1",
    "mart_synergy_policy_mtm_position": "v1",
    "mart_synergy_policy_mtm_daily_path": "v1",
    "mart_synergy_policy_mtm_gate": "v1",
    "mart_synergy_policy_mtm_evidence_bundle": "v1",
    "mart_synergy_policy_mtm_rerank": "v1",
    "mart_synergy_policy_mtm_rerank_summary": "v1",
    "mart_synergy_policy_mtm_strategy_sweep": "v1",
    "mart_synergy_policy_mtm_strategy_sweep_summary": "v1",
    "mart_research_schedule_plan": "v1",
    "mart_architecture_inventory_asset": "v1",
    "mart_architecture_dependency_edge": "v1",
    "mart_architecture_inventory_summary": "v1",
    "mart_architecture_cleanup_plan": "v1",
    "mart_data_source_failure_queue": "v1",
    "mart_pipeline_lock": "v1",
    "mart_pricing_label_policy": "v1",
    "mart_pricing_label_policy_gate": "v1",
    "mart_pricing_label_data_readiness_gate": "v1",
    "mart_global_data_quality_gate": "v1",
    "mart_global_data_quality_detail": "v1",
    "mart_feature_null_policy": "v1",
    "mart_candidate_feature_set_contract": "v1",
    "mart_feature_availability_contract": "v1",
    "mart_data_processing_tool_run": "v1",
    "mart_data_processing_tool_issue": "v1",
    "mart_data_deletion_record": "v1",
    "mart_follow_return_label_build": "v1",
    "mart_follow_return_label_quality": "v1",
    "mart_data_deprecation_record": "v1",
    "mart_model_portfolio_curve": "v1",
    "mart_model_portfolio_summary": "v1",    # 模型组合表现 (cost/return/sharpe)
    "mart_model_walkforward_fold": "v1",     # walkforward 切分 ⭐
    "mart_model_walkforward_portfolio_summary": "v1",
    "mart_model_walkforward_prediction": "v1",
    "mart_multidim_model": "v1",             # 多维模型注册
    "mart_multidim_prediction": "v1",        # 多维模型预测
    "mart_step_fingerprint": "v1",           # step 指纹 (增量驱动)
    "mart_stock_survey_activity": "v1",      # 调研活动
    "mart_prediction_outcome": "v1",         # P2.8 预测 outcome tracker
    "mart_today_signal_cache": "v2",         # signals_v2 read cache summary row
    "mart_today_signal_cache_signal": "v1",  # signals_v2 bounded per-signal cache rows
    # Phase β/γ/δ/ε 新增 (2026-05-12)
    "mart_formula_horizon_evidence": "v1",   # Phase β: 公式 horizon 胜率证据
    "mart_stage_formula_fitness": "v1",      # Phase β: stage × formula × hd 适配
    "mart_stock_picture_daily": "v1",        # Phase γ: 5,512 股全画像 fan-out
    "mart_stock_trade_plan": "v1",           # Phase γ: 8 字段 trade plan
    "mart_paper_nav": "v1",                  # Phase δ: 虚拟 NAV 日序列
    "mart_signal_ic": "v1",                  # Phase δ: 公式 Spearman IC
    "mart_decision_outcome": "v1",           # Phase δ: BUY 决策 outcome
    "mart_stock_selection_outcome": "v1",    # Phase ε: 选股事件 forward return
    "mart_stock_selection_summary": "v1",    # Phase ε: 每股 rolling 统计
    "mart_formula_weight_history": "v1",     # Phase ε: 反馈环公式权重
    "mart_daily_blended_recommendation": "v1",  # Phase ε+: 反馈环融合后的 daily-topk
    "mart_model_composite_score": "v1",      # §6.5.1 Risk-adjusted composite
    "mart_model_edge_flags": "v1",           # §6.5.2 OVERFIT/RISKY/DEAD 三道防线
    "mart_research_reflection_log": "v1",    # §6.5.3 GEPA 反思日志
    "mart_stock_formula_optuna": "v1",       # Phase η: per-stock 公式 grid search
    "mart_daily_formula_buys": "v1",         # Phase η: 每日 T+1 公式推荐
    "mart_per_stock_optuna_best": "v1",      # Phase η+: per-stock 真 Optuna 最佳配置
    "mart_daily_position_recommendation": "v4",  # Phase ζ: 加 optimal_stop_pct/target/trailing 暴露寻优明细
    "mart_stock_survey_features": "v1",          # Phase η++++: 调研热度桶 (IC 60d=0.086 实测验证)
    "mart_stock_formula_optuna_v2": "v1",        # Phase ε.4: 真实回测 (含 stop/trailing/target + 成本 + 一字板)
    "mart_per_stock_strategy_optimal": "v3",     # Phase η++++++: 加 K线形态阈值 + 多目标 metrics
    "mart_stock_formula_buy_signal_daily": "v2", # Phase η+++++ 修正: 8 因子全维 (含 archetype/primary_type)
    "mart_stage_formula_fitness": "v2",          # Phase η++++++: 重建 6 fund × 6 tech × 5 formula × 7 hp
    # Phase v3.2 PLAN_V3 (ML ranking + paper_sim ML score)
    "mart_p0a_label_panel": "v1",                # P0a T+1 VWAP forward 5/10/20d cost-after labels + mask
    "mart_p0a_feature_label_panel": "v1",        # P0a JOIN alpha158 + risk + financial + events × label
    "mart_p0a_feature_label_panel_v2": "v1",     # P0a v2: + 6 formula_trigger dummies (stage_opt 删除 Codex Q1 leakage)
    "mart_p0a_feature_label_panel_v3": "v1",     # P0a v3: + survey 4 + valuation_z 4 + sector 5 + inst_path_a 5 (Codex 7-day plan Day 2-3)
    "mart_p0b_oos_predictions": "v1",            # P0b walk-forward OOS predictions (1 row per stock×signal_date×model_id)
    "mart_p0b_lambdamart_v6_predictions": "v1",  # P0b LambdaMART v6 weekly retrain OOS predictions
    "mart_p0b_walkforward_eval": "v1",           # P0b 每窗 RankIC + IC IR + n_train/n_test
    "mart_p1_ablation_result": "v1",             # P1 feature group ablation (baseline + drop_one + only_one)
    "mart_p2_composite_result": "v1",            # P2 composite weight grid search (ret/dd/hp/turnover/cost/capacity)
    "mart_p3_acceptance_result": "v1",           # P3 final holdout 4 硬验收 + KPI snapshot
    "mart_champion_model": "v1",                 # P4c 单冠军 + KPI 完整性 Gate (Bailey-LdP deflated SR)
    "mart_paper_sim_lambdamart_v6_kpi_compare": "v1",  # MSAF Phase 2.1 v6 versus v4 paper_sim KPI table
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
    # Phase γ 新增 (2026-05-12)
    "dim_stock_stage_days": "v1",            # Phase γ: 基本面/技术面阶段持续天数
    # §3.4 十项基础设施 dim 类
    "dim_price_limit_rules": "v1",           # 涨跌停规则
    "dim_market_segment": "v1",              # 市场细分
    "dim_trading_rule": "v1",                # T+1 / 手数 / tick
    "dim_fee_schedule": "v1",                # 佣金 / 印花税
    "dim_trading_session": "v1",             # 盘口时段
    "dim_liquidity_threshold": "v1",         # 流动性阈值
    "dim_listing_status": "v1",              # 退市状态
    "dim_style_factor": "v1",                # 风格因子定义
}

# 合并: 业务派生表全集 (raw_* / dim_active_a_stock / dim_trading_calendar 等不进, 它们靠 sync_raw 维护)
SCHEMA_VERSIONS = {**FACT_VERSIONS, **MART_VERSIONS, **DIM_DERIVED_VERSIONS}


# ===========================================================================
# View: 启动时 DROP + CREATE OR REPLACE, 防底表 schema drift.
# Keep this list empty unless a current production read path still needs a
# compatibility view. Historical shims should move through architecture cleanup.
# ===========================================================================

RECREATE_VIEWS: dict[str, str] = {}


# ===========================================================================
# DB 操作
# ===========================================================================

def ensure_schema_version_table(conn, commit: bool = True) -> None:
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
    if commit:
        conn.commit()


def recreate_views(conn) -> dict[str, str]:
    """启动时 DROP + CREATE 所有声明的 view. 返回 {view_name: 'ok' | 'fail: msg'}."""
    out = {view_name: "ok" for view_name in RECREATE_VIEWS}
    if not RECREATE_VIEWS:
        conn.commit()
        return out
    script = "\n".join(
        f"DROP VIEW IF EXISTS {view_name};\nCREATE VIEW {view_name} AS {sql};"
        for view_name, sql in RECREATE_VIEWS.items()
    )
    try:
        conn.executescript(script)
    except Exception as exc:
        for view_name in RECREATE_VIEWS:
            out[view_name] = f"fail: {exc}"
        logger.warning(f"[schema_version] view batch rebuild failed: {exc}")
    conn.commit()
    return out


def record_actual_version(conn, table_name: str, version: str | None = None) -> None:
    """升级 dim_schema_version 的 actual_version + rebuilt_at.

    build_xxx / rebuild_xxx 函数末尾调用. version=None 时取 SCHEMA_VERSIONS[table_name].
    """
    expected = SCHEMA_VERSIONS.get(table_name, "v1")
    actual = version or expected
    ensure_schema_version_table(conn, commit=False)
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
    table_names = list(SCHEMA_VERSIONS)
    if not table_names:
        return 0
    placeholders = ", ".join("?" for _ in table_names)
    existing_rows = conn.execute(
        f"""
        SELECT table_name
          FROM information_schema.tables
         WHERE table_name IN ({placeholders})
        """,
        table_names,
    ).fetchall()
    existing = {row[0] for row in existing_rows}
    rows = [
        (table_name, expected, expected)
        for table_name, expected in SCHEMA_VERSIONS.items()
        if table_name in existing
    ]
    if rows:
        ensure_schema_version_table(conn, commit=False)
        conn.executemany(
            """
            INSERT INTO dim_schema_version (table_name, expected_version, actual_version, rebuilt_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(table_name) DO UPDATE SET
                expected_version = excluded.expected_version,
                actual_version   = excluded.actual_version,
                rebuilt_at       = excluded.rebuilt_at
            """,
            rows,
        )
    conn.commit()
    return len(rows)


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
