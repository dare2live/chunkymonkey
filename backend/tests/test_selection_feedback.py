"""Phase ε D1 — feedback.py 单测。"""
from __future__ import annotations

import pytest

from services.selection.feedback import (
    HYSTERESIS,
    WEIGHT_MAX,
    WEIGHT_MIN,
    _clip_and_renormalize,
    _softmax,
    derive_formula_weights,
)


class TestSoftmax:
    def test_basic(self):
        out = _softmax([1.0, 2.0, 3.0], temperature=1.0)
        # 总和 = 1
        assert abs(sum(out) - 1.0) < 1e-9
        # 单调递增
        assert out[0] < out[1] < out[2]

    def test_empty_returns_empty(self):
        assert _softmax([], 1.0) == []

    def test_higher_temperature_smooths(self):
        low_t = _softmax([0, 1, 2], temperature=0.5)
        high_t = _softmax([0, 1, 2], temperature=10.0)
        # 高温更接近均匀
        diff_low = max(low_t) - min(low_t)
        diff_high = max(high_t) - min(high_t)
        assert diff_low > diff_high


class TestClipAndRenormalize:
    def test_no_clip_needed(self):
        out = _clip_and_renormalize([0.2, 0.3, 0.5], 0.02, 0.40)
        assert abs(sum(out) - 1.0) < 1e-9

    def test_clip_high(self):
        # 0.8 → clip 到 0.4; renormalize 后总和=1 (不强求每个 ≤ 0.4)
        out = _clip_and_renormalize([0.8, 0.1, 0.1], 0.02, 0.40)
        # 至少 clip 生效 (0.8 不会再保持 0.8)
        assert out[0] < 0.8
        # 总和 = 1
        assert abs(sum(out) - 1.0) < 1e-9

    def test_clip_low(self):
        out = _clip_and_renormalize([0.01, 0.49, 0.50], 0.02, 0.40)
        # 0.01 → 0.02
        assert all(w >= 0.019 for w in out)


@pytest.fixture
def conn_with_ic():
    from services.duck_adapter import connect as duck_connect
    from services.selection.ddl import ensure_selection_tables
    from services.paper_engine.ddl import ensure_paper_tables  # 为了 mart_signal_ic
    c = duck_connect(":memory:")
    ensure_selection_tables(c)
    ensure_paper_tables(c)
    # 种 IC 数据: macd 正 / turtle 负 / dynamic 中
    c.executescript("""
        INSERT INTO mart_signal_ic
          (snapshot_date, formula_id, formula_variant, n_signals,
           ic_5d, ic_10d, ic_30d, rank_ic_5d, rank_ic_10d, rank_ic_30d) VALUES
          ('2026-05-10', 'macd_golden_cross', 'macd_golden_cross', 100,
            0.05, 0.08, 0.10, 0.05, 0.08, 0.10),
          ('2026-05-10', 'turtle_breakout_20', 'turtle_breakout_20', 100,
            -0.05, -0.08, -0.10, -0.05, -0.08, -0.10),
          ('2026-05-10', 'dynamic_ma_iterative_cross', 'dynamic_ma_iterative_cross', 100,
            0.01, 0.02, 0.03, 0.01, 0.02, 0.03);
    """)
    c.commit()
    yield c
    c.close()


class TestDeriveFormulaWeights:
    def test_writes_one_row_per_formula(self, conn_with_ic):
        n = derive_formula_weights(conn_with_ic, "2026-05-10", ic_window_days=60)
        assert n == 3

    def test_weights_sum_to_one(self, conn_with_ic):
        derive_formula_weights(conn_with_ic, "2026-05-10")
        rows = conn_with_ic.execute(
            "SELECT weight FROM mart_formula_weight_history WHERE snapshot_date='2026-05-10'"
        ).fetchall()
        total = sum(float(r[0]) for r in rows)
        assert abs(total - 1.0) < 1e-3

    def test_macd_gets_more_weight_than_turtle(self, conn_with_ic):
        derive_formula_weights(conn_with_ic, "2026-05-10")
        macd = conn_with_ic.execute(
            "SELECT weight FROM mart_formula_weight_history WHERE formula_id='macd_golden_cross'"
        ).fetchone()
        turtle = conn_with_ic.execute(
            "SELECT weight FROM mart_formula_weight_history WHERE formula_id='turtle_breakout_20'"
        ).fetchone()
        assert macd[0] > turtle[0]

    def test_no_ic_data_returns_zero(self):
        from services.duck_adapter import connect as duck_connect
        from services.selection.ddl import ensure_selection_tables
        from services.paper_engine.ddl import ensure_paper_tables
        c = duck_connect(":memory:")
        ensure_selection_tables(c)
        ensure_paper_tables(c)
        try:
            n = derive_formula_weights(c, "2026-05-10")
            assert n == 0
        finally:
            c.close()
