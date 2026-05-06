from __future__ import annotations

import json

import pytest

from conftest import duck_mem
from scripts import validate_synergy_policy_mark_to_market as subject


pytestmark = pytest.mark.pipeline


def _seed_mtm_inputs(conn, *, fallback: bool = False) -> None:
    conn.executescript(
        """
        CREATE TABLE dim_trading_calendar (
            trade_date TEXT PRIMARY KEY,
            is_trading INTEGER
        );
        INSERT INTO dim_trading_calendar VALUES
            ('2026-01-01', 1),
            ('2026-01-02', 1),
            ('2026-01-03', 1),
            ('2026-01-04', 1),
            ('2026-01-05', 1),
            ('2026-01-06', 1),
            ('2026-01-07', 1),
            ('2026-01-08', 1);

        CREATE TABLE mart_synergy_policy_candidate (
            run_id TEXT,
            source_run_id TEXT,
            label_name TEXT,
            objective_score DOUBLE,
            selected_features_json TEXT,
            selected_interactions_json TEXT,
            gate_status TEXT,
            notes_json TEXT,
            built_at TEXT
        );
        INSERT INTO mart_synergy_policy_candidate VALUES
            ('candidate_mtm_unit', 'temporal_mtm_unit', 'follow_net_return_5d', 1.5,
             '["signal_a","signal_b"]',
             '[{"feature_a":"signal_a","feature_b":"signal_b"}]',
             'research_only', '{}', '2026-05-07T00:00:00');

        CREATE TABLE mart_feature_temporal_relevance (
            run_id TEXT,
            label_name TEXT,
            feature_name TEXT,
            rank_ic DOUBLE
        );
        INSERT INTO mart_feature_temporal_relevance VALUES
            ('temporal_mtm_unit', 'follow_net_return_5d', 'signal_a', 0.8),
            ('temporal_mtm_unit', 'follow_net_return_5d', 'signal_b', 0.7);

        CREATE TABLE mart_temporal_research_panel (
            run_id TEXT,
            stock_code TEXT,
            date TEXT,
            signal_a DOUBLE,
            signal_b DOUBLE,
            follow_net_return_5d DOUBLE
        );

        CREATE TABLE kline_unit (
            code TEXT,
            date TEXT,
            freq TEXT,
            adjust TEXT,
            open DOUBLE,
            close DOUBLE,
            volume DOUBLE,
            amount DOUBLE,
            source_name TEXT,
            source_tier INTEGER,
            is_fallback BOOLEAN
        );
        """
    )
    panel_rows = []
    for trade_date in ("2026-01-01", "2026-01-02"):
        for idx, stock_code in enumerate(("000001", "000002", "000003")):
            panel_rows.append(
                (
                    "temporal_mtm_unit",
                    stock_code,
                    trade_date,
                    float(idx),
                    float(idx),
                    idx / 100.0,
                )
            )
    conn.executemany("INSERT INTO mart_temporal_research_panel VALUES (?, ?, ?, ?, ?, ?)", panel_rows)
    kline_rows = []
    source_name = "akshare" if fallback else "tdxhub"
    source_tier = 3 if fallback else 1
    for stock_code in ("000001", "000002", "000003"):
        base = 8.0 + int(stock_code[-1])
        for offset in range(8):
            close = base + offset + 1.0
            trade_date = f"2026-01-{offset + 1:02d}"
            kline_rows.append(
                (
                    stock_code,
                    trade_date,
                    "daily",
                    "qfq",
                    close - 0.5,
                    close,
                    100.0,
                    close * 100.0,
                    source_name,
                    source_tier,
                    fallback,
                )
            )
    conn.executemany("INSERT INTO kline_unit VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", kline_rows)


def test_mark_to_market_uses_signal_day_vwap_and_daily_path() -> None:
    with duck_mem() as conn:
        _seed_mtm_inputs(conn)

        result = subject.validate_synergy_policy_mark_to_market(
            conn,
            candidate_run_id="candidate_mtm_unit",
            run_id="mtm_unit",
            top_quantile=0.34,
            min_positions=1,
            min_active_days=1,
            min_total_return=-1.0,
            max_drawdown=0.99,
            kline_relation="kline_unit",
            progress=False,
        )

        position = conn.execute(
            "SELECT * FROM mart_synergy_policy_mtm_position WHERE run_id = 'mtm_unit'"
        ).fetchone()
        first_day = conn.execute(
            """
            SELECT *
              FROM mart_synergy_policy_mtm_daily_path
             WHERE run_id = 'mtm_unit'
             ORDER BY date
             LIMIT 1
            """
        ).fetchone()
        gate = conn.execute("SELECT * FROM mart_synergy_policy_mtm_gate WHERE run_id = 'mtm_unit'").fetchone()
        manifest = conn.execute(
            "SELECT gate_result, perf_summary_json FROM mart_pipeline_run_manifest WHERE run_id = 'mtm_unit'"
        ).fetchone()

        assert result["validation_status"] == "pass"
        assert result["promotion_status"] == "research_only"
        assert result["repeated_signal_suppressed_count"] == 1
        assert position["stock_code"] == "000003"
        assert position["entry_date"] == "2026-01-01"
        assert position["exit_date"] == "2026-01-06"
        assert position["entry_price"] == pytest.approx(12.0)
        assert position["entry_price_method"] == "signal_day_vwap_qfq"
        assert position["net_return"] == pytest.approx(17.0 / 12.0 - 1 - 0.001)
        assert first_day["daily_gross_return"] == pytest.approx(0.0)
        assert first_day["daily_cost_rate"] == pytest.approx(0.0005)
        assert gate["validation_status"] == "pass"
        assert gate["candidate_horizon_days"] == 5
        assert manifest["gate_result"] == "pass"
        summary = json.loads(manifest["perf_summary_json"])
        assert summary["thresholds"]["entry_price_mode"] == "signal_day_vwap_qfq"


def test_mark_to_market_blocks_non_tdxhub_kline_path() -> None:
    with duck_mem() as conn:
        _seed_mtm_inputs(conn, fallback=True)

        result = subject.validate_synergy_policy_mark_to_market(
            conn,
            candidate_run_id="candidate_mtm_unit",
            run_id="mtm_fallback_unit",
            top_quantile=0.34,
            min_positions=1,
            min_active_days=1,
            min_total_return=-1.0,
            max_drawdown=0.99,
            kline_relation="kline_unit",
            progress=False,
        )

        assert result["validation_status"] == "blocked"
        assert "non_tdxhub_kline_path" in result["blockers"]
