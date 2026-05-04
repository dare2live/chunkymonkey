import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from conftest import duck_mem
from scripts import backtest_model_portfolio as subject


def test_simulate_curve_uses_record_inputs_and_turnover_cost():
    curve, summary = subject.simulate_curve(
        curve_id="model_top2_10bps",
        curve_type="model_top2",
        model_id="model_a",
        benchmark_id=None,
        trading_dates=["2026-01-01", "2026-01-02", "2026-01-03"],
        signal_dates=["2026-01-01"],
        select_codes=lambda _date, _idx: ["000001", "000002"],
        returns_by_date={
            "2026-01-02": {"000001": 0.10, "000002": 0.0},
            "2026-01-03": {"000001": 0.0, "000002": 0.10},
        },
        cost_bps=10.0,
        rebalance_days=20,
        built_at="2026-01-04T00:00:00",
    )

    assert len(curve) == 3
    assert curve[1]["turnover"] == 1.0
    assert curve[1]["daily_ret"] == pytest.approx(0.049)
    assert curve[-1]["nav"] == pytest.approx(1.1013975)
    assert summary["final_nav"] == pytest.approx(1.1013975)
    assert summary["avg_turnover"] == 1.0
    assert summary["rebalance_count"] == 1


def test_benchmark_curve_summarizes_record_rows():
    curve = subject.benchmark_510300_curve(
        [
            {"date": "2026-01-01", "close": 100.0},
            {"date": "2026-01-02", "close": 110.0},
            {"date": "2026-01-03", "close": 99.0},
        ],
        curve_id="benchmark_510300_etf_10bps",
        built_at="2026-01-04T00:00:00",
        cost_bps=10.0,
        rebalance_days=20,
    )
    summary = subject.summarize_curve(curve, [])

    assert [row["daily_ret"] for row in curve] == pytest.approx([0.0, 0.1, -0.1])
    assert curve[-1]["nav"] == pytest.approx(0.99)
    assert summary["total_return"] == pytest.approx(-0.01)


def test_write_results_persists_records_and_random_p90_column():
    conn = duck_mem()
    try:
        curve = [
            {
                "curve_id": "model_top2_10bps",
                "curve_type": "model_top2",
                "model_id": "model_a",
                "benchmark_id": None,
                "date": "2026-01-01",
                "nav": 1.15,
                "daily_ret": 0.15,
                "turnover": 1.0,
                "holdings_count": 2,
                "cost_bps": 10.0,
                "rebalance_days": 20,
                "built_at": "2026-01-04T00:00:00",
            }
        ]
        base_summary = {
            "run_id": "run_a",
            "model_id": "model_a",
            "benchmark_id": None,
            "start_date": "2026-01-01",
            "end_date": "2026-01-01",
            "cost_bps": 10.0,
            "rebalance_days": 20,
            "final_nav": 1.15,
            "annualized_return": None,
            "max_drawdown": 0.0,
            "sharpe": None,
            "avg_turnover": 1.0,
            "rebalance_count": 1,
            "notes": None,
            "built_at": "2026-01-04T00:00:00",
        }
        summaries = [
            {
                **base_summary,
                "curve_id": "model_top2_10bps",
                "curve_type": "model_top2",
                "total_return": 0.15,
            },
            {
                **base_summary,
                "curve_id": "benchmark_random_l1_seed_00_10bps",
                "curve_type": "random",
                "model_id": None,
                "benchmark_id": "benchmark_random_l1_seed_00",
                "total_return": 0.0,
            },
            {
                **base_summary,
                "curve_id": "benchmark_random_l1_seed_01_10bps",
                "curve_type": "random",
                "model_id": None,
                "benchmark_id": "benchmark_random_l1_seed_01",
                "total_return": 0.1,
            },
        ]

        subject.write_results(conn, "run_a", [curve], summaries, dry_run=False)

        curve_count = conn.execute("SELECT COUNT(*) AS n FROM mart_model_portfolio_curve").fetchone()
        summary_count = conn.execute("SELECT COUNT(*) AS n FROM mart_model_portfolio_summary").fetchone()
        stored = conn.execute(
            """
            SELECT vs_random_l1_p90_pp
            FROM mart_model_portfolio_summary
            WHERE curve_id = 'model_top2_10bps'
            """
        ).fetchone()

        assert curve_count["n"] == 1
        assert summary_count["n"] == 3
        assert stored["vs_random_l1_p90_pp"] == pytest.approx(6.0)
    finally:
        conn.close()
