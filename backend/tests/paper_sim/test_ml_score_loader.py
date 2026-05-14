"""P0c ML score loader 单测.

mock DuckDB + mart_p0b_oos_predictions + mart_per_stock_stage_strategy_optimal,
验 load_today_candidates_ml_score 输出 ORDER BY score DESC, LEFT JOIN exit params,
返回兼容 CandidateRow 结构.
"""
from __future__ import annotations

import duckdb
import pytest

from services.paper_sim.ml_score_loader import load_today_candidates_ml_score


def _make_conn() -> duckdb.DuckDBPyConnection:
    conn = duckdb.connect(":memory:")
    # mart_p0b_oos_predictions schema (与 ddl.py 一致)
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
    # mart_per_stock_stage_strategy_optimal schema (subset of real)
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


def test_basic_loads_top_k_by_score():
    conn = _make_conn()
    # 5 stocks with descending scores
    for i, code in enumerate(["600001", "600002", "600003", "600004", "600005"]):
        conn.execute(
            "INSERT INTO mart_p0b_oos_predictions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [code, "2024-06-17", 1.0 - i * 0.1,
             0.01, 0.02, 0.03,
             "lgbm_baseline_v1", "p0b_v1", "p0a_v1", "p0a_v1",
             "expanding_monthly", "2024-01-01", "2024-05-31", "2024-06-01", "2024-06-30",
             False, "2026-05-14T11:00:00"]
        )
    # exit params for some
    for code, hp, target in [("600001", 5, 0.10), ("600003", 15, 0.20)]:
        conn.execute(
            "INSERT INTO mart_per_stock_stage_strategy_optimal VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [code, "turtle_20", "v1", "stage_2", hp, target, -0.05, 0.03, 1.5, 1.2, 0.08, 0.07, 10]
        )

    rows = load_today_candidates_ml_score(conn, "2024-06-17", max_candidates=3)
    assert len(rows) == 3
    # ORDER BY score DESC
    assert rows[0].stock_code == "600001" and rows[0].score == 1.0
    assert rows[1].stock_code == "600002" and abs(rows[1].score - 0.9) < 1e-9
    assert rows[2].stock_code == "600003" and abs(rows[2].score - 0.8) < 1e-9
    # 600001 / 600003 has exit params
    assert rows[0].optimal_hp == 5 and abs(rows[0].optimal_target_pct - 0.10) < 1e-9
    assert rows[2].optimal_hp == 15 and abs(rows[2].optimal_target_pct - 0.20) < 1e-9
    # 600002 has no exit params → default hp=10
    assert rows[1].optimal_hp == 10
    assert rows[1].formula_id == "ml_default"
    # All tier="ML_RANK"
    for r in rows: assert r.tier == "ML_RANK"


def test_min_score_filter():
    conn = _make_conn()
    for i, code in enumerate(["600001", "600002", "600003"]):
        conn.execute(
            "INSERT INTO mart_p0b_oos_predictions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [code, "2024-06-17", 1.0 - i * 0.4,  # 1.0, 0.6, 0.2
             None, None, None,
             "lgbm_baseline_v1", "p0b_v1", "p0a_v1", "p0a_v1",
             "expanding_monthly", "2024-01-01", "2024-05-31", "2024-06-01", "2024-06-30",
             False, "ts"]
        )
    rows = load_today_candidates_ml_score(conn, "2024-06-17", min_score=0.5)
    assert len(rows) == 2  # only 600001 (1.0) and 600002 (0.6)


def test_model_id_filter():
    conn = _make_conn()
    conn.execute(
        "INSERT INTO mart_p0b_oos_predictions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ["600001", "2024-06-17", 0.9, None, None, None,
         "lgbm_baseline_v1", "p0b_v1", "p0a_v1", "p0a_v1",
         "expanding_monthly", "2024-01-01", "2024-05-31", "2024-06-01", "2024-06-30",
         False, "ts"]
    )
    conn.execute(
        "INSERT INTO mart_p0b_oos_predictions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ["600002", "2024-06-17", 0.8, None, None, None,
         "lambdamart_v1", "p0b_v1", "p0a_v1", "p0a_v1",
         "expanding_monthly", "2024-01-01", "2024-05-31", "2024-06-01", "2024-06-30",
         False, "ts"]
    )
    rows_lgbm = load_today_candidates_ml_score(conn, "2024-06-17", model_id="lgbm_baseline_v1")
    rows_lm = load_today_candidates_ml_score(conn, "2024-06-17", model_id="lambdamart_v1")
    assert len(rows_lgbm) == 1 and rows_lgbm[0].stock_code == "600001"
    assert len(rows_lm) == 1 and rows_lm[0].stock_code == "600002"


def test_empty_date_returns_empty():
    conn = _make_conn()
    rows = load_today_candidates_ml_score(conn, "2024-12-31")
    assert rows == []


def test_exit_params_picks_best_oos_sharpe():
    """同 stock 多个 stage_filter rows, 取 oos_sharpe 最高的 exit params."""
    conn = _make_conn()
    conn.execute(
        "INSERT INTO mart_p0b_oos_predictions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ["600001", "2024-06-17", 0.9, None, None, None,
         "lgbm_baseline_v1", "p0b_v1", "p0a_v1", "p0a_v1",
         "expanding_monthly", None, None, None, None,
         False, "ts"]
    )
    # 3 stage rows, oos_sharpe diff
    for stage, oos_sh, hp in [("stage_1", 0.5, 5), ("stage_2", 1.8, 20), ("stage_3", 1.0, 15)]:
        conn.execute(
            "INSERT INTO mart_per_stock_stage_strategy_optimal VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ["600001", "f1", "v1", stage, hp, 0.10, -0.05, 0.03, 0.8, oos_sh, 0.05, 0.04, 10]
        )
    rows = load_today_candidates_ml_score(conn, "2024-06-17")
    assert len(rows) == 1
    # Best oos_sharpe=1.8 (stage_2, hp=20)
    assert rows[0].optimal_hp == 20
    assert rows[0].stage == "stage_2"


def test_n_traded_filter():
    """n_traded < 5 的 exit params 应被排除."""
    conn = _make_conn()
    conn.execute(
        "INSERT INTO mart_p0b_oos_predictions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ["600001", "2024-06-17", 0.9, None, None, None,
         "lgbm_baseline_v1", "p0b_v1", "p0a_v1", "p0a_v1",
         "expanding_monthly", None, None, None, None,
         False, "ts"]
    )
    # n_traded=3 应被排除
    conn.execute(
        "INSERT INTO mart_per_stock_stage_strategy_optimal VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ["600001", "f1", "v1", "stage_2", 20, 0.10, -0.05, 0.03, 1.5, 1.5, 0.05, 0.05, 3]
    )
    rows = load_today_candidates_ml_score(conn, "2024-06-17")
    assert len(rows) == 1
    # Exit params 被 n_traded < 5 filter 排除 → 用 default
    assert rows[0].formula_id == "ml_default"
    assert rows[0].optimal_hp == 10
