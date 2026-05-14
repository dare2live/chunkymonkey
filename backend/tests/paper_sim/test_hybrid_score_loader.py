"""P0c-ext hybrid score loader 单测 (Codex 7-day plan Day 6).

测试覆盖:
1. 基本: ml × stage INNER JOIN → hybrid_score DESC
2. w=0: 退化为 pure stage rank
3. w=1: 退化为 pure ML rank
4. q60_min_stage=True: 仅 stage_oos_sharpe >= q60 入候选
5. q60_min_stage=False: 全 stock 入候选
6. 缺 stage 寻优数据 (n_traded < 5): INNER JOIN drop 该 stock
7. NULL ml_score: 不入候选
"""
from __future__ import annotations

import duckdb

from services.paper_sim.hybrid_score_loader import load_today_candidates_hybrid


def _make_conn() -> duckdb.DuckDBPyConnection:
    conn = duckdb.connect(":memory:")
    conn.execute("""
        CREATE TABLE mart_p0b_oos_predictions (
            stock_code TEXT, signal_date DATE, score DOUBLE,
            fwd_cost_after_5d DOUBLE, fwd_cost_after_10d DOUBLE, fwd_cost_after_20d DOUBLE,
            model_id TEXT, model_version TEXT, feature_version TEXT, label_version TEXT,
            walk_forward_mode TEXT,
            train_start DATE, train_end DATE, test_start DATE, test_end DATE,
            is_final_holdout BOOLEAN, built_at TEXT,
            PRIMARY KEY (stock_code, signal_date, model_id)
        )
    """)
    conn.execute("""
        CREATE TABLE mart_per_stock_stage_strategy_optimal (
            stock_code TEXT, formula_id TEXT, formula_variant TEXT,
            stage_filter TEXT, holding_days INTEGER,
            optimal_target_pct DOUBLE, optimal_stop_pct DOUBLE, optimal_trailing_pct DOUBLE,
            sharpe DOUBLE, oos_sharpe DOUBLE,
            avg_ret DOUBLE, oos_avg_ret DOUBLE,
            n_traded INTEGER
        )
    """)
    return conn


def _insert_ml(conn, stock_code, signal_date, score, model_id="lgbm_v1"):
    conn.execute(
        "INSERT INTO mart_p0b_oos_predictions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [stock_code, signal_date, score,
         0.01, 0.02, 0.03,
         model_id, "v1", "v1", "v1",
         "expanding_monthly",
         "2024-01-01", "2024-05-31", "2024-06-01", "2024-06-30",
         False, "2024-06-30T00:00:00"]
    )


def _insert_stage(conn, stock_code, oos_sharpe, n_traded=10,
                  formula_id="macd", formula_variant="default", hp=5):
    conn.execute(
        "INSERT INTO mart_per_stock_stage_strategy_optimal VALUES "
        "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [stock_code, formula_id, formula_variant, "stage_2", hp,
         0.05, -0.03, 0.02,
         oos_sharpe, oos_sharpe,
         0.02, 0.02,
         n_traded]
    )


def test_basic_hybrid_orders_by_blend_score():
    """4 stock: ml + stage 双方向, w=0.2 应得到 stage 主导 + ML 微调的排序.

    Codex MINOR (a0b7c84f) fix: 不只 assert 集合, 加 exact 排序 + hybrid_score 值.

    setup:
      600001: ml=1, stage=4 → s_ml=-1 (rank 0), s_stage=1 (rank 1) → blend=0.8*1+0.2*(-1)=0.6
      600002: ml=2, stage=3 → s_ml=-0.333, s_stage=0.333 → 0.8*0.333+0.2*(-0.333)=0.2
      (600003, 600004 filtered by q60: sharpe 2,1 < q60≈2.8)
    """
    conn = _make_conn()
    for i, code in enumerate(["600001", "600002", "600003", "600004"]):
        _insert_ml(conn, code, "2024-06-30", float(i + 1))
        _insert_stage(conn, code, float(4 - i), n_traded=10)
    rows = load_today_candidates_hybrid(
        conn, "2024-06-30", model_id="lgbm_v1", w_ml=0.2, max_candidates=10, q60_min_stage=True
    )
    # exact 排序 (Codex MINOR): 600001 score=0.6 first, 600002 score=-0.6 second
    # (eligible 集合只剩 2 行, ranked 内部 percent_rank: 0/1 → 各自 [-1,1] 极端)
    assert len(rows) == 2
    assert rows[0].stock_code == "600001"  # higher blended score
    assert rows[1].stock_code == "600002"
    # eligible 集只 2 行: PERCENT_RANK(ml=1,2) → 0/1, PERCENT_RANK(stage=4,3) → 1/0
    # 600001: s_ml = 2*0-1 = -1, s_stage = 2*1-1 = 1 → 0.8*1 + 0.2*(-1) = 0.6
    # 600002: s_ml = 2*1-1 = 1, s_stage = 2*0-1 = -1 → 0.8*(-1) + 0.2*1 = -0.6
    assert abs(rows[0].score - 0.6) < 1e-6
    assert abs(rows[1].score - (-0.6)) < 1e-6


def test_w_0_degenerates_to_pure_stage_rank():
    """w_ml=0: 排序 = stage_oos_sharpe DESC (ML 不参与)."""
    conn = _make_conn()
    # ML 倒序 vs stage 正序 → 验证 w=0 跟 ML 无关
    for i, code in enumerate(["600001", "600002", "600003", "600004"]):
        _insert_ml(conn, code, "2024-06-30", float(4 - i))  # ML: 4,3,2,1
        _insert_stage(conn, code, float(i + 1), n_traded=10)  # stage: 1,2,3,4
    rows = load_today_candidates_hybrid(
        conn, "2024-06-30", model_id="lgbm_v1", w_ml=0.0, max_candidates=10, q60_min_stage=False
    )
    # 按 stage_oos_sharpe DESC: 600004 (4), 600003 (3), 600002 (2), 600001 (1)
    assert [r.stock_code for r in rows] == ["600004", "600003", "600002", "600001"]


def test_w_1_degenerates_to_pure_ml_rank():
    """w_ml=1: 排序 = ml_score DESC (stage 不参与)."""
    conn = _make_conn()
    for i, code in enumerate(["600001", "600002", "600003", "600004"]):
        _insert_ml(conn, code, "2024-06-30", float(i + 1))  # ML: 1,2,3,4
        _insert_stage(conn, code, float(4 - i), n_traded=10)  # stage: 4,3,2,1
    rows = load_today_candidates_hybrid(
        conn, "2024-06-30", model_id="lgbm_v1", w_ml=1.0, max_candidates=10, q60_min_stage=False
    )
    # 按 ml_score DESC: 600004 (4), 600003 (3), 600002 (2), 600001 (1)
    assert [r.stock_code for r in rows] == ["600004", "600003", "600002", "600001"]


def test_q60_min_stage_filter():
    """q60_min_stage=True 仅取 stage_oos_sharpe >= q60 stock."""
    conn = _make_conn()
    sharpes = [0.5, 1.0, 1.5, 2.0, 2.5]  # 5 stocks, q60 = 1.9 (DuckDB QUANTILE_CONT)
    for i, code in enumerate(["600001", "600002", "600003", "600004", "600005"]):
        _insert_ml(conn, code, "2024-06-30", 1.0)
        _insert_stage(conn, code, sharpes[i], n_traded=10)
    rows = load_today_candidates_hybrid(
        conn, "2024-06-30", model_id="lgbm_v1", w_ml=0.3, max_candidates=10, q60_min_stage=True
    )
    # 2 stocks above q60 ≈ 1.9: sharpe=2.0 (600004), sharpe=2.5 (600005)
    assert len(rows) == 2
    assert set(r.stock_code for r in rows) == {"600004", "600005"}


def test_q60_min_stage_disabled_keeps_all():
    """q60_min_stage=False 不 filter, 全 stock 入候选."""
    conn = _make_conn()
    for i, code in enumerate(["600001", "600002", "600003"]):
        _insert_ml(conn, code, "2024-06-30", 1.0)
        _insert_stage(conn, code, float(i + 1), n_traded=10)
    rows = load_today_candidates_hybrid(
        conn, "2024-06-30", model_id="lgbm_v1", w_ml=0.2, max_candidates=10, q60_min_stage=False
    )
    assert len(rows) == 3


def test_low_n_traded_dropped_from_stage():
    """n_traded < 5 的 stock 不入 stage_per_stock → INNER JOIN 跟 ml drop."""
    conn = _make_conn()
    _insert_ml(conn, "600001", "2024-06-30", 1.0)
    _insert_ml(conn, "600002", "2024-06-30", 1.0)
    _insert_stage(conn, "600001", 2.0, n_traded=10)  # ok
    _insert_stage(conn, "600002", 2.0, n_traded=3)  # filtered out
    rows = load_today_candidates_hybrid(
        conn, "2024-06-30", model_id="lgbm_v1", w_ml=0.2, max_candidates=10, q60_min_stage=False
    )
    assert len(rows) == 1
    assert rows[0].stock_code == "600001"


def test_null_ml_score_filtered():
    """NULL ml_score 不入 candidate."""
    conn = _make_conn()
    _insert_ml(conn, "600001", "2024-06-30", 1.0)
    conn.execute(
        "INSERT INTO mart_p0b_oos_predictions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ["600002", "2024-06-30", None,  # NULL score
         0.01, 0.02, 0.03,
         "lgbm_v1", "v1", "v1", "v1",
         "expanding_monthly",
         "2024-01-01", "2024-05-31", "2024-06-01", "2024-06-30",
         False, "2024-06-30T00:00:00"]
    )
    _insert_stage(conn, "600001", 1.0, n_traded=10)
    _insert_stage(conn, "600002", 2.0, n_traded=10)
    rows = load_today_candidates_hybrid(
        conn, "2024-06-30", model_id="lgbm_v1", w_ml=0.2, max_candidates=10, q60_min_stage=False
    )
    assert len(rows) == 1
    assert rows[0].stock_code == "600001"


def test_invalid_w_raises():
    """w_ml ∉ [0, 1] → ValueError."""
    conn = _make_conn()
    import pytest
    with pytest.raises(ValueError):
        load_today_candidates_hybrid(conn, "2024-06-30", w_ml=1.5)
    with pytest.raises(ValueError):
        load_today_candidates_hybrid(conn, "2024-06-30", w_ml=-0.1)


def test_match_tier_marks_w_value():
    """match_tier 字段记录 w_ml 数值."""
    conn = _make_conn()
    _insert_ml(conn, "600001", "2024-06-30", 1.0)
    _insert_stage(conn, "600001", 1.0, n_traded=10)
    rows = load_today_candidates_hybrid(
        conn, "2024-06-30", model_id="lgbm_v1", w_ml=0.25, max_candidates=10, q60_min_stage=False
    )
    assert rows[0].match_tier == "hybrid_w0.25"
    assert rows[0].tier == "HYBRID_ML_STAGE"
