"""Paper Sim v2 — Phase ψ.γ.2 per-stock × stage 参数加载单测.

验证 _load_per_stock_stage_optimal 返回结构 + 空输入安全 + min_n_traded 过滤.

Integration test (真 DB query) 等 Optuna 跑完不占锁后跑.
"""
from __future__ import annotations

from conftest import duck_mem
from services.paper_sim.selector import _load_per_stock_stage_optimal


def test_empty_input_returns_empty_dict():
    """空 pairs 不查 DB, 返回 {}."""
    # conn=None 也不报错 (因为短路)
    out = _load_per_stock_stage_optimal(conn=None, stock_stage_pairs=[])
    assert out == {}


def test_pairs_with_none_stage_filtered_out():
    """stage=None / '' 的 pairs 应被过滤 (无法 JOIN PK)."""
    out = _load_per_stock_stage_optimal(conn=None, stock_stage_pairs=[
        ("600000", None),    # type: ignore[arg-type]
        ("000001", ""),
    ])
    assert out == {}


def test_loads_best_rows_across_stages_in_one_query():
    """stage='1' 有 600000, stage='2' 有 000001, 且按 cutoff/oos 规则取 best."""
    conn = duck_mem()
    conn.executescript(
        """
        CREATE TABLE mart_per_stock_stage_strategy_optimal_pit (
            stock_code TEXT,
            stage_filter TEXT,
            formula_id TEXT,
            formula_variant TEXT,
            holding_days INTEGER,
            optimal_stop_pct DOUBLE,
            optimal_target_pct DOUBLE,
            optimal_trailing_pct DOUBLE,
            oos_sharpe DOUBLE,
            oos_n_traded INTEGER,
            cutoff_date TEXT
        );
        """
    )
    try:
        conn.executemany(
            "INSERT INTO mart_per_stock_stage_strategy_optimal_pit VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                ("600000", "1", "old_formula", "v1", 10, -0.05, 0.15, 0.04, 3.0, 10, "2025-12-31"),
                ("600000", "1", "reversal_1m_deep", "v1", 15, -0.08, 0.25, 0.06, 1.0, 10, "2026-01-10"),
                ("000001", "2", "macd_golden", "v1", 30, -0.12, 0.30, 0.08, 2.0, 8, "2026-01-05"),
                ("000002", "1", "too_few", "v1", 20, -0.10, 0.20, 0.05, 5.0, 2, "2026-01-05"),
            ],
        )
        pairs = [("600000", "1"), ("000001", "2"), ("000002", "1")]
        out = _load_per_stock_stage_optimal(conn, pairs, min_n_traded=5, signal_date="2026-01-31")
        assert ("600000", "1") in out
        assert ("000001", "2") in out
        assert ("000002", "1") not in out, "n_traded 不足不应返回"
        assert out[("600000", "1")]["hp"] == 15
        assert out[("600000", "1")]["stop_pct"] == -0.08
        assert out[("600000", "1")]["target_pct"] == 0.25
        assert out[("600000", "1")]["trailing_pct"] == 0.06
        assert out[("600000", "1")]["source_formula"] == "reversal_1m_deep"
    finally:
        conn.close()


def test_mock_conn_db_exception_returns_empty_for_that_stage():
    """DB query exception → 返回空结果, 不让 selector 主流程崩溃."""

    class _ErrorConn:
        def execute(self, sql, params):
            raise Exception("simulated DB error")

    out = _load_per_stock_stage_optimal(
        _ErrorConn(),
        [("600000", "1"), ("000001", "bad")],
        min_n_traded=5,
    )
    assert ("600000", "1") not in out
    assert ("000001", "bad") not in out
