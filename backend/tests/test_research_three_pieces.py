"""Phase ε+ §6.5 三件套: composite_score / edge_flags / reflection_log 单测。"""
from __future__ import annotations

import pytest


# ===================== composite_score =====================
class TestCompositeScore:
    def test_normal_model_positive(self):
        from services.research.composite_score import compute_composite_score
        m = compute_composite_score(
            wf_rank_ic_avg=0.05, paper_sharpe=0.8,
            paper_max_drawdown=-0.08, n_paper_trades=80,
        )
        assert m["edge_guard"] == 1.0
        # composite = 0.05×100 × 1/1.08 × 60/60 × 1 = 4.63
        assert abs(m["composite_score"] - 4.63) < 0.05

    def test_edge_guard_kills_high_dd(self):
        from services.research.composite_score import compute_composite_score
        m = compute_composite_score(
            wf_rank_ic_avg=0.05, paper_sharpe=1.0,
            paper_max_drawdown=-0.30, n_paper_trades=80,
        )
        assert m["edge_guard"] == 0.0
        assert m["composite_score"] == 0.0

    def test_edge_guard_kills_few_trades(self):
        from services.research.composite_score import compute_composite_score
        m = compute_composite_score(
            wf_rank_ic_avg=0.05, paper_sharpe=1.0,
            paper_max_drawdown=-0.05, n_paper_trades=10,
        )
        assert m["edge_guard"] == 0.0

    def test_edge_guard_kills_negative_sharpe(self):
        from services.research.composite_score import compute_composite_score
        m = compute_composite_score(
            wf_rank_ic_avg=0.05, paper_sharpe=-0.5,
            paper_max_drawdown=-0.05, n_paper_trades=80,
        )
        assert m["edge_guard"] == 0.0

    def test_trade_penalty(self):
        from services.research.composite_score import compute_composite_score
        # n_trades=30 → penalty = 30/60 = 0.5
        m = compute_composite_score(
            wf_rank_ic_avg=0.10, paper_sharpe=1.0,
            paper_max_drawdown=-0.05, n_paper_trades=30,
        )
        assert abs(m["trade_penalty"] - 0.5) < 1e-6

    def test_build_composite_batches_model_metrics(self, conn):
        from services.research.composite_score import build_composite_for_all_models
        conn.executescript(
            """
            CREATE TABLE mart_paper_nav (
                model_id TEXT,
                snapshot_date INTEGER,
                daily_ret DOUBLE,
                drawdown DOUBLE
            );
            CREATE TABLE fact_paper_position (
                model_id TEXT,
                side TEXT
            );
            CREATE TABLE mart_signal_ic (
                snapshot_date INTEGER,
                ic_10d DOUBLE
            );
            """
        )
        conn.executemany(
            "INSERT INTO mart_paper_nav VALUES (?, ?, ?, ?)",
            [
                ("m_a", 1, 0.01, -0.01),
                ("m_a", 2, 0.02, -0.02),
                ("m_b", 1, -0.01, -0.30),
                ("m_b", 2, 0.01, -0.30),
            ],
        )
        conn.executemany("INSERT INTO fact_paper_position VALUES (?, ?)", [("m_a", "sell")] * 40 + [("m_b", "sell")] * 40)
        conn.executemany("INSERT INTO mart_signal_ic VALUES (?, ?)", [(i, 0.05) for i in range(1, 71)])

        assert build_composite_for_all_models(conn, "2026-05-21") == 2
        rows = conn.execute(
            """
            SELECT model_id, n_paper_trades, edge_guard
            FROM mart_model_composite_score
            WHERE eval_date = '2026-05-21'
            ORDER BY model_id
            """
        ).fetchall()
        assert [(r[0], r[1], r[2]) for r in rows] == [("m_a", 40, 1.0), ("m_b", 40, 0.0)]


# ===================== edge_flags =====================
class TestEdgeFlags:
    def test_risky_high_drawdown(self):
        from services.research.edge_flags import classify_edge_flag
        f = classify_edge_flag(
            n_paper_trades=100, paper_max_drawdown=-0.30,
            single_day_max_loss=-0.02, rolling_ic_4w_change=0.01,
        )
        assert f["flag_type"] == "RISKY"
        assert f["trigger_metric"] == "paper_max_drawdown"

    def test_risky_single_day_loss(self):
        from services.research.edge_flags import classify_edge_flag
        f = classify_edge_flag(
            n_paper_trades=100, paper_max_drawdown=-0.10,
            single_day_max_loss=-0.07, rolling_ic_4w_change=0.01,
        )
        assert f["flag_type"] == "RISKY"

    def test_overfit_few_trades(self):
        from services.research.edge_flags import classify_edge_flag
        f = classify_edge_flag(
            n_paper_trades=15, paper_max_drawdown=-0.10,
            single_day_max_loss=-0.02, rolling_ic_4w_change=0.01,
        )
        assert f["flag_type"] == "OVERFIT"

    def test_dead_no_ic_change(self):
        from services.research.edge_flags import classify_edge_flag
        f = classify_edge_flag(
            n_paper_trades=100, paper_max_drawdown=-0.10,
            single_day_max_loss=-0.02, rolling_ic_4w_change=0.001,
        )
        assert f["flag_type"] == "DEAD"

    def test_normal_passes(self):
        from services.research.edge_flags import classify_edge_flag
        f = classify_edge_flag(
            n_paper_trades=100, paper_max_drawdown=-0.10,
            single_day_max_loss=-0.02, rolling_ic_4w_change=0.02,
        )
        assert f["flag_type"] == "NORMAL"

    def test_priority_risky_over_overfit(self):
        from services.research.edge_flags import classify_edge_flag
        # 同时满足 RISKY 和 OVERFIT
        f = classify_edge_flag(
            n_paper_trades=10, paper_max_drawdown=-0.30,
            single_day_max_loss=-0.02, rolling_ic_4w_change=0.001,
        )
        assert f["flag_type"] == "RISKY"

    def test_build_edge_flags_batches_model_metrics(self, conn):
        from services.research.edge_flags import build_edge_flags_for_all_models
        conn.executescript(
            """
            CREATE TABLE mart_paper_nav (
                model_id TEXT,
                snapshot_date INTEGER,
                daily_ret DOUBLE,
                drawdown DOUBLE
            );
            CREATE TABLE fact_paper_position (
                model_id TEXT,
                side TEXT
            );
            CREATE TABLE mart_signal_ic (
                snapshot_date INTEGER,
                ic_10d DOUBLE
            );
            """
        )
        conn.executemany(
            "INSERT INTO mart_paper_nav VALUES (?, ?, ?, ?)",
            [
                ("m_a", 1, 0.01, -0.01),
                ("m_a", 2, 0.02, -0.02),
                ("m_b", 1, -0.01, -0.30),
                ("m_b", 2, 0.01, -0.30),
            ],
        )
        conn.executemany("INSERT INTO fact_paper_position VALUES (?, ?)", [("m_a", "sell")] * 40 + [("m_b", "sell")] * 40)
        conn.executemany("INSERT INTO mart_signal_ic VALUES (?, ?)", [(i, 0.01 + i * 0.001) for i in range(1, 71)])

        assert build_edge_flags_for_all_models(conn, "2026-05-21") == 2
        rows = conn.execute(
            """
            SELECT model_id, flag_type, trigger_metric
            FROM mart_model_edge_flags
            WHERE eval_date = '2026-05-21'
            ORDER BY model_id
            """
        ).fetchall()
        assert [(r[0], r[1], r[2]) for r in rows] == [
            ("m_a", "NORMAL", None),
            ("m_b", "RISKY", "paper_max_drawdown"),
        ]


# ===================== reflection_log =====================
@pytest.fixture
def conn():
    from services.duck_adapter import connect as duck_connect
    from services.research.ddl import ensure_research_tables
    c = duck_connect(":memory:")
    ensure_research_tables(c)
    yield c
    c.close()


class TestReflection:
    def test_basic_write(self, conn):
        from services.research.reflection import write_reflection
        out = write_reflection(
            conn,
            run_date="2026-05-12",
            model_id_before="champion_v3",
            model_id_after="champion_v4",
            hypothesis="去掉 lookahead 特征后 IC 会提升",
            changed_params={"feature_set": "v2_no_lookahead"},
            score_before=0.04, score_after=0.07,
            reflection="IC 从 0.04 → 0.07, 验证假设",
            next_hypothesis="尝试增加 holding_days 到 30",
        )
        assert out["cycle_number"] == 1
        assert out["is_meta_reflection"] is False

    def test_empty_reflection_raises(self, conn):
        from services.research.reflection import write_reflection
        with pytest.raises(ValueError, match="reflection 不能为空"):
            write_reflection(
                conn,
                run_date="2026-05-12",
                model_id_before=None, model_id_after=None,
                hypothesis="test",
                changed_params={},
                score_before=None, score_after=None,
                reflection="",
            )

    def test_duplicate_reflection_flagged(self, conn):
        from services.research.reflection import write_reflection
        write_reflection(
            conn, run_date="2026-05-12",
            model_id_before="m1", model_id_after="m2",
            hypothesis="h1", changed_params={"x": 1},
            score_before=0.04, score_after=0.05,
            reflection="尝试新参数, 没什么改变",
        )
        out = write_reflection(
            conn, run_date="2026-05-13",
            model_id_before="m2", model_id_after="m3",
            hypothesis="h2", changed_params={"y": 2},
            score_before=0.05, score_after=0.045,
            reflection="尝试新参数, 没什么改变",  # 重复
        )
        assert out["duplicate_reflection"] is True

    def test_meta_reflection_at_cycle_5(self, conn):
        from services.research.reflection import write_reflection
        for i in range(5):
            out = write_reflection(
                conn, run_date=f"2026-05-{i+1:02d}",
                model_id_before=f"m{i}", model_id_after=f"m{i+1}",
                hypothesis=f"hypothesis {i}",
                changed_params={"param": i},
                score_before=0.04+i*0.001, score_after=0.04+i*0.001+0.001,
                reflection=f"reflection {i}",
            )
        assert out["cycle_number"] == 5
        assert out["is_meta_reflection"] is True

    def test_run_meta_reflection(self, conn):
        from services.research.reflection import write_reflection, run_meta_reflection
        # 写 5 条
        for i in range(5):
            write_reflection(
                conn, run_date="2026-05-12",
                model_id_before=f"m{i}", model_id_after=f"m{i+1}",
                hypothesis=f"h{i}",
                changed_params={"learning_rate": 0.001 * (i+1)},  # 全调 learning_rate
                score_before=0.04, score_after=0.041,
                reflection=f"reflection {i}",
            )
        meta = run_meta_reflection(conn, run_date="2026-05-12")
        assert meta is not None
        assert meta["is_meta_reflection"] is True
        # 写库后应该能查到
        n = conn.execute(
            "SELECT COUNT(*) FROM mart_research_reflection_log WHERE is_meta_reflection=TRUE"
        ).fetchone()[0]
        assert n >= 1


# ===================== DDL =====================
class TestDDL:
    def test_ensure_creates_three_tables(self):
        from services.duck_adapter import connect as duck_connect
        from services.research.ddl import ensure_research_tables
        c = duck_connect(":memory:")
        try:
            ensure_research_tables(c)
            names = {r[0] for r in c.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema='main'"
            ).fetchall()}
            assert "mart_model_composite_score" in names
            assert "mart_model_edge_flags" in names
            assert "mart_research_reflection_log" in names
        finally:
            c.close()
