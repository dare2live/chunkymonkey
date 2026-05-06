import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from conftest import duck_mem
from scripts import backtest_walkforward_portfolio as subject


def test_fold_portfolio_accepts_record_rows():
    returns_by_date = subject.build_returns_by_date(
        [
            {"date": "2026-01-01", "code": "000001", "ret_1d": None},
            {"date": "2026-01-02", "code": "000001", "ret_1d": 0.10},
            {"date": "2026-01-02", "code": "000002", "ret_1d": -0.05},
        ]
    )

    curve, summary = subject.fold_portfolio(
        fold_row={
            "fold_id": 1,
            "model_id": "model_a",
            "test_start": "2026-01-01",
            "test_end": "2026-01-02",
        },
        candidates_fold=[
            {"date": "2026-01-01", "stock_code": "000002", "rank_in_date": 2},
            {"date": "2026-01-01", "stock_code": "000001", "rank_in_date": 1},
            {"date": "2026-01-02", "stock_code": "000002", "rank_in_date": 1},
        ],
        returns_by_date=returns_by_date,
        cost_bps=0.0,
        top_size=1,
        rebalance_days=20,
        built_at="2026-01-03T00:00:00",
    )

    assert curve[-1]["curve_id"] == "fold01_top1_0bps"
    assert curve[-1]["nav"] == pytest.approx(1.1)
    assert summary["total_return"] == pytest.approx(0.1)


def test_benchmark_total_return_filters_record_window():
    ret = subject.benchmark_total_return(
        [
            {"date": "2025-12-31", "close": 80.0},
            {"date": "2026-01-01", "close": 100.0},
            {"date": "2026-01-03", "close": 110.0},
            {"date": "2026-01-04", "close": 90.0},
        ],
        "2026-01-01",
        "2026-01-03",
    )

    assert ret == pytest.approx(0.1)


def test_load_fold_inputs_uses_records_without_duckdb_registration(monkeypatch):
    conn = duck_mem()
    try:
        monkeypatch.setattr(subject, "ensure_attached", lambda _duck: None)
        conn.executescript(
            """
            CREATE SCHEMA market;
            CREATE TABLE mart_model_walkforward_fold (
                run_id TEXT,
                fold_id INTEGER,
                model_id TEXT,
                test_start TEXT,
                test_end TEXT,
                test_market_state TEXT,
                test_rank_ic REAL
            );
            CREATE TABLE mart_model_walkforward_prediction (
                run_id TEXT,
                fold_id INTEGER,
                stock_code TEXT,
                date TEXT,
                pred_score REAL,
                rank_in_date INTEGER,
                percentile REAL
            );
            CREATE TABLE market.price_kline_tdxhub (
                code TEXT,
                date TEXT,
                close REAL,
                amount REAL,
                freq TEXT,
                adjust TEXT
            );
            CREATE VIEW market.v_price_kline_qfq AS
                SELECT code, date, freq, adjust, NULL::REAL AS open, NULL::REAL AS high,
                       NULL::REAL AS low, close, NULL::REAL AS volume, amount,
                       'tdxhub' AS source_name, 1::SMALLINT AS source_tier,
                       FALSE AS is_fallback, NULL::TEXT AS batch_id, NULL::TEXT AS ingested_at
                FROM market.price_kline_tdxhub;
            """
        )
        conn.execute(
            "INSERT INTO mart_model_walkforward_fold VALUES (?, ?, ?, ?, ?, ?, ?)",
            ["wf1", 1, "model_a", "2026-01-01", "2026-01-03", "bull", 0.03],
        )
        conn.executemany(
            "INSERT INTO mart_model_walkforward_prediction VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                ["wf1", 1, "000001", "2026-01-01", 0.8, 1, 0.99],
                ["wf1", 1, "000002", "2026-01-01", 0.7, 2, 0.98],
                ["wf1", 1, "000001", "2026-01-02", 0.6, 1, 0.99],
            ],
        )
        conn.executemany(
            "INSERT INTO market.price_kline_tdxhub VALUES (?, ?, ?, ?, ?, ?)",
            [
                ["000001", "2026-01-01", 10.0, 30_000_000, "daily", "qfq"],
                ["000001", "2026-01-02", 11.0, 31_000_000, "daily", "qfq"],
                ["000002", "2026-01-01", 20.0, 40_000_000, "daily", "qfq"],
                ["000002", "2026-01-02", 19.0, 41_000_000, "daily", "qfq"],
                ["510300", "2026-01-01", 4.0, 100_000_000, "daily", "qfq"],
                ["510300", "2026-01-03", 4.4, 100_000_000, "daily", "qfq"],
            ],
        )

        folds, candidates, prices, benchmark = subject.load_fold_inputs(conn, "wf1", 0)

        assert folds[0]["model_id"] == "model_a"
        assert {row["stock_code"] for row in candidates} == {"000001", "000002"}
        assert any(row["ret_1d"] == pytest.approx(0.1) for row in prices)
        assert [row["close"] for row in benchmark] == pytest.approx([4.0, 4.4])
    finally:
        conn.close()
