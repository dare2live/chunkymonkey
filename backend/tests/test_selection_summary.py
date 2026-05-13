"""Phase ε D1 — summary.py 单测。"""
from __future__ import annotations

import pytest


@pytest.fixture
def conn_with_data():
    """主 conn 种 selection_log + outcome 数据 (2 股 × 多日)。"""
    from services.duck_adapter import connect as duck_connect
    from services.selection.ddl import ensure_selection_tables
    c = duck_connect(":memory:")
    ensure_selection_tables(c)

    # 600519 被选 5 次 (近 30 日 3 次)
    c.executescript("""
        INSERT INTO fact_stock_selection_log (select_date, stock_code, select_source, source_id, horizon_days) VALUES
          ('2026-05-01', '600519', 'formula', 'macd_golden_cross', 20),
          ('2026-04-25', '600519', 'formula', 'macd_golden_cross', 20),
          ('2026-04-15', '600519', 'daily_topk', 'champion', 20),
          ('2026-03-15', '600519', 'formula', 'turtle_breakout_20', 20),
          ('2026-02-01', '600519', 'formula', 'macd_golden_cross', 20),
          ('2026-04-30', '000001', 'daily_topk', 'champion', 20),
          ('2026-04-20', '000001', 'formula', 'macd_golden_cross', 20);

        -- 7 个 log → 7 个 outcome 行 (部分 active, 部分 settled)
        INSERT INTO mart_stock_selection_outcome
          (select_date, stock_code, select_source, source_id,
           entry_price, fwd_ret_5d, fwd_ret_10d, fwd_ret_30d, fwd_max_dd_30d,
           days_to_t1, outcome_5d, outcome_10d, outcome_30d, horizon_days)
        VALUES
          -- 600519
          ('2026-05-01', '600519', 'formula', 'macd_golden_cross', 100, 0.05, 0.08, NULL, -0.02, 1, 'win', 'win', 'active', 20),
          ('2026-04-25', '600519', 'formula', 'macd_golden_cross', 95, 0.03, 0.07, 0.10, -0.03, 2, 'win', 'win', 'win', 20),
          ('2026-04-15', '600519', 'daily_topk', 'champion', 90, -0.02, -0.04, -0.05, -0.08, NULL, 'loss', 'loss', 'loss', 20),
          ('2026-03-15', '600519', 'formula', 'turtle_breakout_20', 88, 0.02, 0.05, 0.08, -0.02, 3, 'win', 'win', 'win', 20),
          ('2026-02-01', '600519', 'formula', 'macd_golden_cross', 85, 0.01, 0.02, 0.04, -0.04, NULL, 'flat', 'win', 'win', 20),
          -- 000001
          ('2026-04-30', '000001', 'daily_topk', 'champion', 50, -0.02, NULL, NULL, -0.03, NULL, 'loss', 'active', 'active', 20),
          ('2026-04-20', '000001', 'formula', 'macd_golden_cross', 48, 0.04, 0.08, 0.10, -0.01, 1, 'win', 'win', 'win', 20);
    """)
    c.commit()
    yield c
    c.close()


class TestRecomputeAllSummaries:
    def test_two_stocks_rolled_up(self, conn_with_data):
        from services.selection.summary import recompute_all_summaries
        n = recompute_all_summaries(conn_with_data, "2026-05-10")
        assert n == 2

    def test_600519_stats(self, conn_with_data):
        from services.selection.summary import recompute_all_summaries
        recompute_all_summaries(conn_with_data, "2026-05-10")
        r = conn_with_data.execute(
            """SELECT n_total, n_30d, n_90d, win_rate, last_select_date, last_outcome
                 FROM mart_stock_selection_summary WHERE stock_code='600519'"""
        ).fetchone()
        # n_total=5, n_30d (since 2026-04-10) = 3, n_90d (since 2026-02-09) = 4
        assert r[0] == 5
        assert r[1] == 3
        assert r[2] == 4
        # win_rate: 5 个 outcome, 4 win + 1 loss → 4/5 = 0.8 (active 不算)
        # 实际: outcome_30d 5 行中, 'win'=3 ('04-25', '03-15', '02-01'), 'loss'=1 ('04-15'), 'active'=1 ('05-01')
        # win_rate = 3/(3+1) = 0.75
        assert abs(r[3] - 0.75) < 1e-6
        # last_select_date = 最新
        assert r[4] == "2026-05-01"
        # last_outcome: 最新这天 outcome_30d='active'
        assert r[5] == "active"

    def test_000001_stats(self, conn_with_data):
        from services.selection.summary import recompute_all_summaries
        recompute_all_summaries(conn_with_data, "2026-05-10")
        r = conn_with_data.execute(
            "SELECT n_total, n_30d FROM mart_stock_selection_summary WHERE stock_code='000001'"
        ).fetchone()
        assert r[0] == 2
        assert r[1] == 2  # 两次都在 30 日内
