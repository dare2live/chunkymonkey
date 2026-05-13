"""Paper Sim v2 — ddl 建表幂等测试."""
from __future__ import annotations

from services.duck_adapter import connect
from services.paper_sim.ddl import ensure_paper_sim_tables


def test_ensure_creates_four_tables():
    conn = connect(":memory:")
    try:
        ensure_paper_sim_tables(conn)
        names = {r[0] for r in conn.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema='main'"
        ).fetchall()}
        assert "mart_paper_sim_nav" in names
        assert "fact_paper_sim_position" in names
        assert "fact_paper_sim_trade" in names
        assert "mart_paper_sim_kpi" in names
    finally:
        conn.close()


def test_idempotent():
    """重复 ensure 不抛 + 不重复建表."""
    conn = connect(":memory:")
    try:
        for _ in range(3):
            ensure_paper_sim_tables(conn)
        n = conn.execute(
            "SELECT COUNT(*) FROM information_schema.tables WHERE table_name='mart_paper_sim_nav'"
        ).fetchone()[0]
        assert n == 1
    finally:
        conn.close()


def test_position_schema_includes_optuna_params():
    """达成率公式依赖 optimal_hp + optimal_target_pct, 必须落入 schema."""
    conn = connect(":memory:")
    try:
        ensure_paper_sim_tables(conn)
        cols = {r[0] for r in conn.execute("DESCRIBE fact_paper_sim_position").fetchall()}
        assert "optimal_hp" in cols
        assert "optimal_target_pct" in cols
        assert "optimal_stop_pct" in cols
        assert "expected_target_pct" in cols   # 达成率分母
        assert "is_open" in cols                # filter open positions
    finally:
        conn.close()


def test_trade_log_has_swap_uplift_column():
    """KPI B8 swap_uplift_total 累加 swap_uplift_estimate 字段."""
    conn = connect(":memory:")
    try:
        ensure_paper_sim_tables(conn)
        cols = {r[0] for r in conn.execute("DESCRIBE fact_paper_sim_trade").fetchall()}
        assert "swap_uplift_estimate" in cols
        assert "type" in cols
    finally:
        conn.close()
