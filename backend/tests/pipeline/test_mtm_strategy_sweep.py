from __future__ import annotations

import json

import pytest

from conftest import duck_mem
from scripts import sweep_synergy_mtm_strategy as subject


pytestmark = pytest.mark.pipeline


def test_strategy_sweep_records_variants_summary_and_manifest(monkeypatch) -> None:
    calls = []

    def fake_mtm(_conn, *, run_id: str, min_market_hs300_ret_20d, **kwargs):
        calls.append((run_id, min_market_hs300_ret_20d))
        threshold = min_market_hs300_ret_20d
        if threshold == 0.03:
            return {
                "run_id": run_id,
                "candidate_run_id": kwargs["candidate_run_id"],
                "source_run_id": "temporal_unit",
                "label_name": "follow_net_return_60d",
                "validation_status": "pass",
                "promotion_status": "research_only",
                "production_eligible": False,
                "blockers": [],
                "signal_count": 100,
                "market_filter_removed_signal_count": 40,
                "daily_top_k_filtered_count": 0,
                "position_count": 50,
                "total_return": 0.40,
                "annualized_return": 0.20,
                "max_drawdown": -0.12,
                "sharpe": 1.10,
                "avg_active_positions": 20.0,
                "missing_entry_price_count": 0,
                "missing_exit_price_count": 0,
                "missing_path_price_count": 0,
            }
        return {
            "run_id": run_id,
            "candidate_run_id": kwargs["candidate_run_id"],
            "source_run_id": "temporal_unit",
            "label_name": "follow_net_return_60d",
            "validation_status": "blocked",
            "promotion_status": "research_only",
            "production_eligible": False,
            "blockers": ["excessive_mark_to_market_drawdown"],
            "signal_count": 200,
            "market_filter_removed_signal_count": 0,
            "daily_top_k_filtered_count": 0,
            "position_count": 80,
            "total_return": 0.20,
            "annualized_return": 0.10,
            "max_drawdown": -0.40,
            "sharpe": 0.50,
            "avg_active_positions": 50.0,
            "missing_entry_price_count": 0,
            "missing_exit_price_count": 0,
            "missing_path_price_count": 0,
        }

    monkeypatch.setattr(subject, "validate_synergy_policy_mark_to_market", fake_mtm)

    with duck_mem() as conn:
        result = subject.sweep_synergy_mtm_strategy(
            conn,
            candidate_run_id="candidate_unit",
            run_id="strategy_sweep_unit",
            market_hs300_ret_20d_grid=[None, 0.03],
            progress=False,
        )

        rows = conn.execute(
            """
            SELECT variant_id, validation_status, min_market_hs300_ret_20d
              FROM mart_synergy_policy_mtm_strategy_sweep
             WHERE run_id = 'strategy_sweep_unit'
             ORDER BY variant_id
            """
        ).fetchall()
        summary = conn.execute(
            """
            SELECT best_variant_id, best_validation_status, best_blockers_json,
                   config_json
              FROM mart_synergy_policy_mtm_strategy_sweep_summary
             WHERE run_id = 'strategy_sweep_unit'
            """
        ).fetchone()
        manifest = conn.execute(
            """
            SELECT gate_result, perf_summary_json
              FROM mart_pipeline_run_manifest
             WHERE run_id = 'strategy_sweep_unit'
            """
        ).fetchone()

        assert result["evaluated_variants"] == 2
        assert result["best"]["variant_id"] == "hs20_0p03__hs60_none"
        assert len(calls) == 2
        assert [row["variant_id"] for row in rows] == [
            "hs20_0p03__hs60_none",
            "hs20_none__hs60_none",
        ]
        assert rows[0]["validation_status"] == "pass"
        assert summary["best_variant_id"] == "hs20_0p03__hs60_none"
        assert summary["best_validation_status"] == "pass"
        assert json.loads(summary["best_blockers_json"]) == []
        assert json.loads(summary["config_json"])["market_hs300_ret_20d_grid"] == [None, 0.03]
        assert manifest["gate_result"] == "pass"
        assert "stage_timings" in json.loads(manifest["perf_summary_json"])
