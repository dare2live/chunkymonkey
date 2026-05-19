from __future__ import annotations

import json

from scripts.audit_delivery_readiness import (
    _load_p3_acceptance,
    _load_phase4_live_evidence,
    _score_live_ready,
)


def test_ship_baseline_scores_80_despite_perfect_ladder_misses():
    scored = _score_live_ready(
        p3_passed=True,
        n_obs=22,
        median_ann=0.48403779303239103,
        cagr_ann=0.34243001672660145,
        max_dd=-0.24283431349873352,
        sharpe=0.8089204380395046,
        pbo_passed=True,
        dsr_conf=0.9825001360177857,
    )

    assert scored["pct"] == 80
    assert scored["verdict"] == "PASS"
    assert scored["ship_baseline_passed"] is True
    assert scored["perfect_ladder_ready"] is False
    assert scored["effective_ann"] == 0.34243001672660145
    assert scored["blockers"] == []
    assert "sharpe 0.81 < 2.0 for perfect ladder" in scored["next_milestones"]


def test_ship_baseline_blocks_when_pbo_or_dsr_missing():
    scored = _score_live_ready(
        p3_passed=True,
        n_obs=22,
        median_ann=0.48,
        cagr_ann=0.34,
        max_dd=-0.24,
        sharpe=0.81,
        pbo_passed=False,
        dsr_conf=0.49,
    )

    assert scored["pct"] == 60
    assert scored["verdict"] == "WARN"
    assert scored["ship_baseline_passed"] is False
    assert "PBO not PASS" in scored["blockers"]
    assert "DSR p_conf 0.49 < 0.50" in scored["blockers"]


def test_perfect_ladder_can_reach_100():
    scored = _score_live_ready(
        p3_passed=True,
        n_obs=60,
        median_ann=0.16,
        cagr_ann=0.14,
        max_dd=-0.19,
        sharpe=2.1,
        pbo_passed=True,
        dsr_conf=0.80,
    )

    assert scored["pct"] == 100
    assert scored["verdict"] == "PASS"
    assert scored["ship_baseline_passed"] is True
    assert scored["perfect_ladder_ready"] is True
    assert scored["next_milestones"] == []


def test_phase4_live_evidence_parses_pbo_dsr_without_is_oos_block(tmp_path):
    report = tmp_path / "phase4_gate_result.json"
    report.write_text(json.dumps({
        "gate_result": {
            "promote_action": "block",
            "pbo": {"passes": True, "reason": "PBO=0.145"},
            "dsr": {
                "passes": True,
                "reason": "DSR p_conf=0.9825",
                "detail": {"p_conf": 0.9825001360177857},
            },
            "conservative": {"passes": True},
            "is_oos": {
                "passes": False,
                "detail": {"proxy_mode": True},
            },
        },
    }))

    evidence = _load_phase4_live_evidence(report)

    assert evidence["pbo_passed"] is True
    assert evidence["dsr_passed"] is True
    assert evidence["dsr_conf"] == 0.9825001360177857
    assert evidence["conservative_passed"] is True
    assert evidence["is_oos_passed"] is False
    assert evidence["is_oos_proxy_mode"] is True
    assert evidence["phase4_promote_action"] == "block"


def test_p3_acceptance_falls_back_to_pit_audit(tmp_path):
    pit_report = tmp_path / "pit_audit.json"
    pit_report.write_text(json.dumps({
        "tables": [
            {
                "table": "mart_p3_acceptance_result",
                "latest_pass_runs": [
                    {
                        "run_id": "p3_session_fixed",
                        "ann_ret": 0.30680555850503766,
                        "passed": True,
                    },
                ],
            },
        ],
    }))

    p3 = _load_p3_acceptance(tmp_path / "missing.duckdb", pit_report=pit_report)

    assert p3["found"] is True
    assert p3["passed"] is True
    assert p3["ann_ret"] == 0.30680555850503766
    assert p3["source"] == "pit_audit_fallback"
