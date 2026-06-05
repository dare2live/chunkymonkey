from __future__ import annotations

import json

from services.workbench_delivery_read import build_workbench_delivery_readiness


def test_delivery_readiness_payload_combines_live_and_rejected_challenger(tmp_path):
    reports = tmp_path / "data" / "reports"
    reports.mkdir(parents=True)
    (reports / "delivery_readiness.json").write_text(json.dumps({
        "avg_pct": 92.83,
        "ready_for_delivery": False,
        "criteria": [
            {"criterion": "策略模型管理", "pct": 90, "sources_wired": {"institution": False},
             "sources_available": {"institution": True},
             "source_evaluations": {"institution": {
                 "production_decision": "hold_reject",
                 "reject_reasons": ["max_dd -39% worse than -25%"],
             }}},
            {"criterion": "backtester gate", "pct": 87, "phase4_promote_action": "block"},
            {"criterion": "计算后端控制", "pct": 100, "active_backends": ["local"],
             "planned_backends": ["modal"], "job_families": ["model_training"]},
            {"criterion": "实盘 GO/NO-GO", "pct": 80, "verdict": "PASS",
             "ship_baseline_passed": True, "perfect_ladder_ready": False,
             "msaf_n_obs": 22, "msaf_sharpe": 0.81, "msaf_max_dd": -0.2428,
             "phase4_pbo_passed": True, "phase4_dsr_conf": 0.9825,
             "horizon_ladder": [{"horizon": "5d", "n_obs": 87, "sharpe": 0.87}],
             "risk_overlay_probes": [{"max_dd": -0.1891, "sharpe": 0.797}],
             "source_weight_probe_summary": {
                 "best_gate_pass": {"sharpe": 1.85, "phase4_gate": {"all_pass": True}},
                 "best_strict_sharpe": {"sharpe": 2.02, "phase4_gate": {"all_pass": False}},
             },
             "challenger_oos_probes": [{"model_id": "model_x", "n_obs": 34, "sharpe": 0.875}],
             "next_milestones": ["n_obs 22 < 30 for 85%"]},
        ],
    }))
    (reports / "phase4_gate_result.json").write_text(json.dumps({
        "model_id": "live_model",
        "gate_result": {"promote_action": "block", "all_pass": False,
                         "pbo": {"passes": True, "reason": "PBO=0.145"}},
    }))
    (reports / "phase4_gate_model_x.json").write_text(json.dumps({
        "model_id": "model_x",
        "challenger_id": "challenger_x",
        "n_obs_20d": 34,
        "n_obs_5d": 135,
        "gate_result": {"promote_action": "block", "all_pass": False,
                         "pbo": {"passes": False, "reason": "PBO=0.626"}},
    }))
    (reports / "decision_model_x.json").write_text(json.dumps({
        "model_id": "model_x",
        "decision": "hold_reject",
        "production_status": "candidate_hold_reject",
        "fail_reasons": ["pbo: PBO=0.626"],
    }))

    payload = build_workbench_delivery_readiness(repo_root=tmp_path, challenger_model_id="model_x")

    assert payload["ready_for_delivery"] is False
    assert payload["verdict"] == "NOT_READY"
    assert payload["live_go_no_go"]["ship_baseline_passed"] is True
    assert payload["live_go_no_go"]["horizon_ladder"][0]["horizon"] == "5d"
    assert payload["live_go_no_go"]["risk_overlay_probes"][0]["max_dd"] == -0.1891
    assert payload["live_go_no_go"]["source_weight_probe_summary"]["best_gate_pass"]["sharpe"] == 1.85
    assert payload["live_go_no_go"]["challenger_oos_probes"][0]["n_obs"] == 34
    assert payload["challenger"]["decision"]["decision"] == "hold_reject"
    assert payload["challenger"]["gate"]["pbo"]["passes"] is False
    assert payload["sources"]["available"]["institution"] is True
    assert payload["sources"]["institution_evaluation"]["production_decision"] == "hold_reject"
    assert payload["compute_backend"]["active_backends"] == ["local"]
    assert {row["scope"] for row in payload["blockers"]} == {"milestone", "challenger", "institution"}
