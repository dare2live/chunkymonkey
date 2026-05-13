"""Phase ε+ 反馈环融合 daily-topk 测试。"""
from __future__ import annotations

import pytest


@pytest.fixture
def conn():
    from services.duck_adapter import connect as duck_connect
    from services.formula_engine.ddl import ensure_formula_tables
    from services.paper_engine.ddl import ensure_paper_tables
    from services.selection.ddl import ensure_selection_tables
    from services.selection.blended_recommendation import ensure_blended_table
    c = duck_connect(":memory:")
    ensure_formula_tables(c)
    ensure_selection_tables(c)
    ensure_paper_tables(c)
    ensure_blended_table(c)
    # seed mart_daily_recommendation
    c.executescript("""
        CREATE TABLE mart_daily_recommendation (
          snapshot_date TEXT, stock_code TEXT, rank_in_date BIGINT, pred_score DOUBLE
        );
        INSERT INTO mart_daily_recommendation VALUES
          ('2026-05-12', 'A', 1, 0.90),
          ('2026-05-12', 'B', 2, 0.80),
          ('2026-05-12', 'C', 3, 0.70);
    """)
    c.commit()
    yield c
    c.close()


class TestBuildBlendedForDate:
    def test_no_formula_triggers_keeps_base_rank(self, conn):
        """无公式信号时, blended = base, rank 不变。"""
        from services.selection.blended_recommendation import build_blended_for_date
        n = build_blended_for_date(conn, "2026-05-12", top_k=10)
        assert n == 3
        rows = conn.execute(
            "SELECT stock_code, rank_in_date, base_rank_in_date, formula_bonus FROM mart_daily_blended_recommendation ORDER BY rank_in_date"
        ).fetchall()
        # rank 不变
        for r in rows:
            assert r[1] == r[2]
            assert r[3] == 0.0

    def test_positive_ic_formula_boosts(self, conn):
        """正 IC 公式 + 高 strength → bonus 正 → blended_score 涨。"""
        # 种 fact_technical_trigger (signal_date < 2026-05-12)
        conn.executescript("""
            INSERT INTO fact_technical_trigger
              (stock_code, date, formula_id, formula_variant, strength, state, reason_codes_json)
            VALUES
              ('B', '2026-05-11', 'macd_golden_cross', 'macd_golden_cross', 1.0, 'just_crossed', NULL);
            INSERT INTO mart_formula_weight_history
              (snapshot_date, formula_id, formula_variant, weight, rolling_ic_60d, n_obs, is_active)
            VALUES
              ('2026-05-12', 'macd_golden_cross', 'macd_golden_cross', 0.5, 0.05, 100, TRUE);
        """)
        conn.commit()
        from services.selection.blended_recommendation import build_blended_for_date
        build_blended_for_date(conn, "2026-05-12", top_k=10)
        b = conn.execute(
            "SELECT formula_bonus, blended_score, base_pred_score FROM mart_daily_blended_recommendation WHERE stock_code='B'"
        ).fetchone()
        # bonus = 0.5 × +1 × 1.0 = 0.5; blended = 0.8 × 1.5 = 1.2
        assert abs(b[0] - 0.5) < 1e-6
        assert abs(b[1] - 1.2) < 1e-6

    def test_negative_ic_formula_sign_flips(self, conn):
        """负 IC 公式 → sign-flip → bonus 是 -weight × strength → blended 跌。"""
        conn.executescript("""
            INSERT INTO fact_technical_trigger
              (stock_code, date, formula_id, formula_variant, strength, state, reason_codes_json)
            VALUES
              ('B', '2026-05-11', 'turtle_breakout_20', 'turtle_breakout_20', 1.0, 'fresh', NULL);
            INSERT INTO mart_formula_weight_history
              (snapshot_date, formula_id, formula_variant, weight, rolling_ic_60d, n_obs, is_active)
            VALUES
              ('2026-05-12', 'turtle_breakout_20', 'turtle_breakout_20', 0.3, -0.08, 200, TRUE);
        """)
        conn.commit()
        from services.selection.blended_recommendation import build_blended_for_date
        build_blended_for_date(conn, "2026-05-12", top_k=10)
        b = conn.execute(
            "SELECT formula_bonus, base_rank_in_date, rank_in_date FROM mart_daily_blended_recommendation WHERE stock_code='B'"
        ).fetchone()
        # sign-flip: bonus = 0.3 × -1 × 1.0 = -0.3
        assert abs(b[0] - (-0.3)) < 1e-6
        # B (base rank 2) 被惩罚 → 新 rank > 2 (跌到 C 后面)
        assert b[2] > b[1]

    def test_reranks_top_position(self, conn):
        """A 被负 IC 公式打压 → 跌; C 没被打压 → 升到第一。"""
        conn.executescript("""
            INSERT INTO fact_technical_trigger
              (stock_code, date, formula_id, formula_variant, strength, state, reason_codes_json)
            VALUES
              ('A', '2026-05-11', 'turtle_breakout_20', 'turtle_breakout_20', 1.0, 'fresh', NULL);
            INSERT INTO mart_formula_weight_history
              (snapshot_date, formula_id, formula_variant, weight, rolling_ic_60d, n_obs, is_active)
            VALUES
              ('2026-05-12', 'turtle_breakout_20', 'turtle_breakout_20', 0.5, -0.1, 100, TRUE);
        """)
        conn.commit()
        from services.selection.blended_recommendation import build_blended_for_date
        build_blended_for_date(conn, "2026-05-12", top_k=10)
        new_top = conn.execute(
            "SELECT stock_code FROM mart_daily_blended_recommendation ORDER BY rank_in_date LIMIT 1"
        ).fetchone()
        # A base=0.9, blended = 0.9 × (1 - 0.5) = 0.45
        # B base=0.8, blended = 0.8
        # C base=0.7, blended = 0.7
        # 新 top 应该是 B (0.8 > 0.7 > 0.45)
        assert new_top[0] == "B"


class TestEndpoint:
    @pytest.fixture(scope="class")
    def client(self):
        import sys
        from pathlib import Path
        backend_dir = Path(__file__).resolve().parent.parent
        if str(backend_dir) not in sys.path:
            sys.path.insert(0, str(backend_dir))
        from main import app
        from fastapi.testclient import TestClient
        return TestClient(app)

    def test_blended_endpoint_basic(self, client):
        r = client.get("/api/v3/selection/blended?limit=5")
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        # 真实 DB 有 50 行 (Phase ε+ 已 build)
        if body["data"]:
            row = body["data"][0]
            required = {"stock_code", "rank_in_date", "base_rank_in_date",
                        "base_pred_score", "formula_bonus", "blended_score"}
            assert required.issubset(set(row.keys()))
            # 排序 ascending by rank_in_date
            ranks = [d["rank_in_date"] for d in body["data"]]
            for i in range(len(ranks) - 1):
                assert ranks[i] <= ranks[i + 1]
