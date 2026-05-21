from __future__ import annotations

import json

import scripts.audit_delivery_readiness as audit
from scripts.audit_delivery_readiness import (
    check_strategy_model,
    _load_p3_acceptance,
    _load_phase4_live_evidence,
    _load_msaf_challenger_oos_probes,
    _load_msaf_horizon_ladder,
    _load_msaf_phase4_probe_gates,
    _load_msaf_risk_overlay_probes,
    _summarize_source_weight_probes,
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


def test_msaf_horizon_ladder_loads_primary_and_probe(tmp_path):
    reports = tmp_path / "reports"
    reports.mkdir()
    primary = {
        "args": {"horizon": "20d"},
        "kpi": {
            "horizon": "20d",
            "n_obs": 22,
            "sharpe": 0.81,
            "max_dd": -0.2428,
            "ann_ret_cagr": 0.34,
            "ann_ret_median": 0.48,
            "hit_rate": 0.68,
        },
    }
    (reports / "msaf_ensemble_h5_probe_20260520.json").write_text(json.dumps({
        "args": {"horizon": "5d"},
        "kpi": {
            "horizon": "5d",
            "n_obs": 87,
            "sharpe": 0.872,
            "max_dd": -0.3461,
            "ann_ret_cagr": 0.3975,
            "ann_ret_median": 0.3274,
            "hit_rate": 0.5172,
        },
    }))

    rows = _load_msaf_horizon_ladder(reports, primary)

    assert [r["horizon"] for r in rows] == ["5d", "20d"]
    h5 = rows[0]
    assert h5["sample_ready"] is True
    assert h5["perfect_sample_ready"] is True
    assert h5["perfect_sharpe_ready"] is False
    assert h5["perfect_dd_ready"] is False
    assert h5["perfect_ladder_ready"] is False
    assert rows[1]["primary"] is True


def test_msaf_risk_overlay_probes_capture_delta_vs_primary(tmp_path):
    reports = tmp_path / "reports"
    reports.mkdir()
    primary = {
        "kpi": {
            "n_obs": 22,
            "sharpe": 0.81,
            "max_dd": -0.2428,
            "ann_ret_cagr": 0.34,
        },
    }
    (reports / "msaf_ensemble_regime_cash_probe_20260520.json").write_text(json.dumps({
        "cash_overlay": {
            "bull_cash_pct": 0.15,
            "neutral_cash_pct": 0.30,
            "bear_cash_pct": 0.80,
        },
        "volatility_target": {
            "target_ann_vol": 0.15,
            "vol_window": 3,
        },
        "kpi": {
            "n_obs": 22,
            "sharpe": 0.797,
            "max_dd": -0.1891,
            "ann_ret_cagr": 0.263,
            "ann_ret_median": 0.3388,
            "hit_rate": 0.6818,
            "avg_exposure": 0.92,
            "min_realized_exposure": 0.44,
        },
    }))

    rows = _load_msaf_risk_overlay_probes(reports, primary)

    assert len(rows) == 1
    row = rows[0]
    assert row["cash_overlay"]["neutral_cash_pct"] == 0.30
    assert row["volatility_target"]["target_ann_vol"] == 0.15
    assert row["avg_exposure"] == 0.92
    assert row["sample_ready"] is True
    assert row["perfect_dd_ready"] is True
    assert row["perfect_sharpe_ready"] is False
    assert row["perfect_ladder_ready"] is False
    assert round(row["delta_max_dd_vs_primary"], 4) == 0.0537
    assert round(row["delta_sharpe_vs_primary"], 3) == -0.013

    (reports / "msaf_ensemble_voltarget12_probe_20260520.json").write_text(json.dumps({
        "volatility_target": {"target_ann_vol": 0.12, "vol_window": 3},
        "kpi": {
            "n_obs": 22,
            "sharpe": 0.82,
            "max_dd": -0.16,
            "ann_ret_cagr": 0.19,
        },
    }))

    rows = _load_msaf_risk_overlay_probes(reports, primary)
    assert len(rows) == 2
    vol_row = [r for r in rows if r["volatility_target"].get("target_ann_vol") == 0.12][0]
    assert vol_row["perfect_dd_ready"] is True
    assert vol_row["perfect_sharpe_ready"] is False

    (reports / "msaf_ensemble_scorefloor50_probe_20260520.json").write_text(json.dumps({
        "score_filter": {"min_top_score": 0.50},
        "kpi": {
            "n_obs": 11,
            "n_skip": 11,
            "sharpe": 7.06,
            "max_dd": -0.0742,
            "ann_ret_cagr": 0.3433,
        },
    }))

    rows = _load_msaf_risk_overlay_probes(reports, primary)
    score_row = [r for r in rows if r["score_filter"].get("min_top_score") == 0.50][0]
    assert score_row["n_obs"] == 11
    assert score_row["n_skip"] == 11
    assert score_row["sample_ready"] is False
    assert score_row["perfect_dd_ready"] is True
    assert score_row["perfect_sharpe_ready"] is False
    assert score_row["perfect_ladder_ready"] is False

    (reports / "msaf_ensemble_scoreexposure45_60_probe_20260520.json").write_text(json.dumps({
        "score_exposure": {
            "score_exposure_floor": 0.45,
            "score_exposure_ceiling": 0.60,
            "score_min_exposure": 0.25,
        },
        "kpi": {
            "n_obs": 22,
            "n_skip": 0,
            "ann_ret_cagr": 0.10,
            "ann_ret_median": 0.12,
            "sharpe": 0.70,
            "max_dd": -0.12,
        },
    }))
    rows = _load_msaf_risk_overlay_probes(reports, primary)
    exposure_row = [
        r for r in rows
        if r["score_exposure"].get("score_exposure_floor") == 0.45
    ][0]
    assert exposure_row["n_obs"] == 22
    assert exposure_row["score_exposure"]["score_exposure_ceiling"] == 0.60
    assert exposure_row["perfect_dd_ready"] is True
    assert exposure_row["perfect_sharpe_ready"] is False

    (reports / "msaf_ensemble_lm75_sniper25_probe_20260520.json").write_text(json.dumps({
        "args": {"horizon": "10d"},
        "source_weight_override": {
            "lambdamart_weight": 0.75,
            "sniper_weight": 0.25,
            "institution_weight": 0.0,
        },
        "kpi": {
            "n_obs": 22,
            "n_skip": 0,
            "ann_ret_cagr": 0.6875,
            "ann_ret_median": 0.2867,
            "sharpe": 1.629,
            "max_dd": -0.1555,
        },
    }))
    rows = _load_msaf_risk_overlay_probes(reports, primary)
    weight_row = [
        r for r in rows
        if r["source_weight_override"].get("lambdamart_weight") == 0.75
    ][0]
    assert weight_row["sample_ready"] is True
    assert weight_row["horizon"] == "10d"
    assert weight_row["perfect_dd_ready"] is True
    assert weight_row["perfect_sharpe_ready"] is False


def test_msaf_source_weight_probes_link_dedicated_phase4_gate(tmp_path):
    reports = tmp_path / "reports"
    reports.mkdir()
    primary = {"kpi": {"n_obs": 22, "sharpe": 0.81, "max_dd": -0.2428, "ann_ret_cagr": 0.34}}
    (reports / "msaf_ensemble_gcp_v6_lm73_sniper27_h10_probe_20260520.json").write_text(json.dumps({
        "args": {
            "horizon": "10d",
            "lambdamart_model_id": "candidate_v6",
        },
        "source_weight_override": {
            "lambdamart_weight": 0.73,
            "sniper_weight": 0.27,
            "institution_weight": 0.0,
        },
        "kpi": {
            "horizon": "10d",
            "n_obs": 68,
            "n_skip": 0,
            "ann_ret_cagr": 0.9075,
            "ann_ret_median": 0.3608,
            "sharpe": 1.85,
            "max_dd": -0.1442,
        },
    }))
    (reports / "phase4_gate_msaf_gcp_v6_lm73_sniper27_h10_probe_20260520.json").write_text(json.dumps({
        "model_id": "candidate_v6",
        "primary_horizon": "10d",
        "source_weight_override": {
            "lambdamart_weight": 0.73,
            "sniper_weight": 0.27,
            "institution_weight": 0.0,
        },
        "ann_normal": 0.725,
        "ann_conservative": 0.710,
        "gate_result": {
            "all_pass": True,
            "promote_action": "warn_only_proxy",
            "pbo": {"passes": True, "reason": "PBO=0.110", "detail": {"pbo": 0.1096}},
            "dsr": {"passes": True},
            "conservative": {"passes": True},
            "is_oos": {"passes": True, "detail": {"proxy_mode": True}},
        },
    }))

    gates = _load_msaf_phase4_probe_gates(reports)
    rows = _load_msaf_risk_overlay_probes(reports, primary)
    summary = _summarize_source_weight_probes(rows)

    assert len(gates) == 1
    assert rows[0]["phase4_gate"]["all_pass"] is True
    assert rows[0]["phase4_gate"]["pbo_value"] == 0.1096
    assert summary["n_source_weight_probes"] == 1
    assert summary["n_gate_pass"] == 1
    assert summary["n_hard_promote_ready"] == 0
    assert summary["n_proxy_candidate_ready"] == 0
    assert summary["best_gate_pass"]["sharpe"] == 1.85
    assert summary["best_gate_pass"]["position_sizing"] == {}
    assert summary["best_gate_pass"]["cash_overlay"] == {}
    assert summary["best_hard_promote"] is None
    assert summary["best_proxy_candidate"] is None
    assert summary["best_strict_sharpe"] is None


def test_msaf_source_weight_proxy_candidate_is_not_hard_promote(tmp_path):
    reports = tmp_path / "reports"
    reports.mkdir()
    primary = {"kpi": {"n_obs": 22, "sharpe": 0.81, "max_dd": -0.2428, "ann_ret_cagr": 0.34}}
    (reports / "msaf_ensemble_gcp_v6_lm735_sniper265_h10_k3_neutralcash20_probe_20260521.json").write_text(json.dumps({
        "args": {
            "horizon": "10d",
            "lambdamart_model_id": "candidate_v6",
            "max_positions": 3,
        },
        "source_weight_override": {
            "lambdamart_weight": 0.735,
            "sniper_weight": 0.265,
            "institution_weight": 0.0,
        },
        "position_sizing": {"rank_decay": 0.82},
        "cash_overlay": {"neutral_cash_pct": 0.20},
        "kpi": {
            "horizon": "10d",
            "n_obs": 68,
            "ann_ret_cagr": 1.1859,
            "ann_ret_median": 0.3837,
            "sharpe": 2.09,
            "max_dd": -0.1998,
        },
    }))
    (reports / "phase4_gate_msaf_gcp_v6_lm735_sniper265_h10_k3_neutralcash20_probe_20260521.json").write_text(json.dumps({
        "model_id": "candidate_v6",
        "primary_horizon": "10d",
        "source_weight_override": {
            "lambdamart_weight": 0.735,
            "sniper_weight": 0.265,
            "institution_weight": 0.0,
        },
        "position_sizing": {"rank_decay": 0.82},
        "max_positions": 3,
        "cash_overlay": {"neutral_cash_pct": 0.20},
        "gate_result": {
            "all_pass": True,
            "promote_action": "warn_only_proxy",
            "pbo": {"passes": True, "reason": "PBO=0.119", "detail": {"pbo": 0.1192}},
            "dsr": {"passes": True},
            "conservative": {"passes": True},
            "is_oos": {"passes": True, "detail": {"proxy_mode": True}},
        },
    }))

    rows = _load_msaf_risk_overlay_probes(reports, primary)
    summary = _summarize_source_weight_probes(rows)

    assert rows[0]["perfect_ladder_ready"] is True
    assert summary["n_hard_promote_ready"] == 0
    assert summary["n_proxy_candidate_ready"] == 1
    assert summary["best_proxy_candidate"]["max_positions"] == 3
    assert summary["best_proxy_candidate"]["position_sizing"] == {"rank_decay": 0.82}
    assert summary["best_proxy_candidate"]["cash_overlay"] == {"neutral_cash_pct": 0.20}
    assert summary["best_proxy_candidate"]["phase4_gate"]["promote_action"] == "warn_only_proxy"
    assert summary["best_hard_promote"] is None


def test_msaf_source_weight_probe_needs_sixty_obs_for_perfect_ladder(tmp_path):
    reports = tmp_path / "reports"
    reports.mkdir()
    primary = {"kpi": {"n_obs": 22, "sharpe": 0.81, "max_dd": -0.2428, "ann_ret_cagr": 0.34}}
    (reports / "msaf_ensemble_gcp_v6_lm745_sniper255_h10_sniperfloor05_probe_20260520.json").write_text(json.dumps({
        "args": {
            "horizon": "10d",
            "lambdamart_model_id": "candidate_v6",
        },
        "source_weight_override": {
            "lambdamart_weight": 0.745,
            "sniper_weight": 0.255,
            "institution_weight": 0.0,
        },
        "score_filter": {"min_sniper_score": 0.05},
        "kpi": {
            "horizon": "10d",
            "n_obs": 41,
            "sharpe": 2.48,
            "max_dd": -0.092,
        },
    }))

    rows = _load_msaf_risk_overlay_probes(reports, primary)
    summary = _summarize_source_weight_probes(rows)

    assert rows[0]["sample_ready"] is True
    assert rows[0]["perfect_sample_ready"] is False
    assert rows[0]["perfect_ladder_ready"] is False
    assert summary["best_strict_sharpe"] is None


def test_msaf_challenger_oos_probes_are_separate_from_live_baseline(tmp_path):
    reports = tmp_path / "reports"
    reports.mkdir()
    (reports / "msaf_ensemble_gcp_v6_oos_probe_20260520.json").write_text(json.dumps({
        "args": {
            "lambdamart_model_id": "candidate_v6",
            "start": "2023-07-03",
            "end": "2026-04-14",
        },
        "prediction_table": "mart_p0b_lambdamart_v6_predictions",
        "kpi": {
            "n_obs": 34,
            "n_skip": 0,
            "ann_ret_cagr": 0.3377,
            "ann_ret_median": 0.0804,
            "sharpe": 0.875,
            "max_dd": -0.2196,
            "hit_rate": 0.5882,
        },
    }))

    rows = _load_msaf_challenger_oos_probes(reports)

    assert len(rows) == 1
    row = rows[0]
    assert row["model_id"] == "candidate_v6"
    assert row["prediction_table"] == "mart_p0b_lambdamart_v6_predictions"
    assert row["sample_ready"] is True
    assert row["perfect_sample_ready"] is False
    assert row["perfect_dd_ready"] is False
    assert row["perfect_ladder_ready"] is False


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
        "model_id": "model_a",
    }))

    evidence = _load_phase4_live_evidence(report, expected_model_id="model_a")

    assert evidence["pbo_passed"] is True
    assert evidence["dsr_passed"] is True
    assert evidence["dsr_conf"] == 0.9825001360177857
    assert evidence["conservative_passed"] is True
    assert evidence["is_oos_passed"] is False
    assert evidence["is_oos_proxy_mode"] is True
    assert evidence["phase4_promote_action"] == "block"
    assert evidence["phase4_model_id"] == "model_a"
    assert evidence["model_id_match"] is True


def test_phase4_live_evidence_rejects_model_mismatch(tmp_path):
    report = tmp_path / "phase4_gate_result.json"
    report.write_text(json.dumps({
        "model_id": "rejected_challenger",
        "gate_result": {
            "promote_action": "block",
            "pbo": {"passes": True, "reason": "PBO=0.145"},
            "dsr": {"passes": True, "detail": {"p_conf": 0.99}},
            "conservative": {"passes": True},
            "is_oos": {"passes": True, "detail": {"proxy_mode": True}},
        },
    }))

    evidence = _load_phase4_live_evidence(report, expected_model_id="live_model")

    assert evidence["phase4_model_id"] == "rejected_challenger"
    assert evidence["model_id_match"] is False
    assert evidence["pbo_passed"] is False
    assert evidence["dsr_passed"] is False
    assert "model_id mismatch" in evidence["pbo_reason"]


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


def test_gcp_controlled_idle_status_makes_cost_control_pass(tmp_path, monkeypatch):
    status_dir = tmp_path / "data" / "reports" / "phase5_chain"
    status_dir.mkdir(parents=True)
    (status_dir / "status.json").write_text(json.dumps({
        "step": "gcp_disabled",
        "status": "TERMINATED",
    }))
    monkeypatch.setattr(audit, "REPO_ROOT", tmp_path)

    result = audit.check_gcp_cost_control()

    assert result["pct"] == 100
    assert result["alert_level"] == "CONTROLLED_USE_IDLE"
    assert result["policy"] == "controlled_use_requires_explicit_latch"
    assert result["vm_status"] == "TERMINATED"
    assert result["source"] == "phase5_chain_controlled_idle"


def test_gcp_running_cost_summary_overrides_legacy_controlled_idle(tmp_path, monkeypatch):
    status_dir = tmp_path / "data" / "reports" / "phase5_chain"
    status_dir.mkdir(parents=True)
    (status_dir / "status.json").write_text(json.dumps({
        "step": "gcp_disabled",
        "status": "TERMINATED",
    }))
    reports_dir = tmp_path / "data" / "reports"
    (reports_dir / "gcp_cost_summary.json").write_text(json.dumps({
        "checked_at": "2026-05-21T14:11:09+08:00",
        "vm_status": "RUNNING",
        "alert_level": "OK",
        "pct_of_budget": 63.3,
        "projected_month_cost": 6.3302,
        "remaining_budget_usd": 5.9154,
        "remaining_hours_at_spot": 15.73,
    }))
    monkeypatch.setattr(audit, "REPO_ROOT", tmp_path)

    result = audit.check_gcp_cost_control()

    assert result["pct"] == 100
    assert result["alert_level"] == "OK"
    assert result["vm_status"] == "RUNNING"
    assert result["pct_of_budget"] == 63.3
    assert result["source"] == "gcp_cost_summary"


def test_daily_automation_uses_active_cost_report_over_legacy_idle(tmp_path, monkeypatch):
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir(parents=True)
    (scripts_dir / "daily_update.sh").write_text(
        "\n".join([
            "Step 0: GCP cost tracker",
            "run_phase4_gate_on_msaf.py",
            "Step 2c: alpha158",
            "STEP6_GATE_OK",
            "run_msaf_ensemble_paper_sim.py --compute-kpi",
            "backend/scripts/promote_champion.py --p3-run-id p3_latest",
        ])
    )
    status_dir = tmp_path / "data" / "reports" / "phase5_chain"
    status_dir.mkdir(parents=True)
    (status_dir / "status.json").write_text(json.dumps({
        "step": "gcp_disabled",
        "status": "TERMINATED",
    }))
    reports_dir = tmp_path / "data" / "reports"
    (reports_dir / "gcp_cost_summary.json").write_text(json.dumps({
        "vm_status": "RUNNING",
        "alert_level": "OK",
    }))
    monkeypatch.setattr(audit, "REPO_ROOT", tmp_path)

    import subprocess

    def fake_run(cmd, capture_output, text, timeout):
        if cmd == ["launchctl", "list"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        if cmd == ["crontab", "-l"]:
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="")
        raise AssertionError(cmd)

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = audit.check_daily_automation()

    assert result["gcp_controlled_idle"] is False
    assert result["gcp_cost_report_active"] is True
    assert result["gcp_cost_report_alert"] == "OK"
    assert result["gcp_cost_report_vm_status"] == "RUNNING"
    assert result["cost_loaded"] is False
    assert result["pct"] >= 90


def test_strategy_model_reports_institution_evaluated_but_not_active(tmp_path, monkeypatch):
    reports_dir = tmp_path / "data" / "reports"
    reports_dir.mkdir(parents=True)
    (reports_dir / "msaf_ensemble_run.json").write_text(json.dumps({
        "args": {"with_institution": False, "no_institution": False},
        "kpi": {
            "ann_ret_median": 0.48,
            "ann_ret_cagr": 0.34,
            "sharpe": 0.81,
            "max_dd": -0.24,
            "hit_rate": 0.68,
            "n_obs": 22,
        },
    }))
    (reports_dir / "msaf_ensemble_with_institution_eval_20260520.json").write_text(json.dumps({
        "args": {"with_institution": True, "no_institution": False},
        "kpi": {
            "ann_ret_median": -0.09,
            "ann_ret_cagr": -0.04,
            "sharpe": 0.09,
            "max_dd": -0.39,
            "hit_rate": 0.36,
            "n_obs": 22,
        },
    }))

    scripts_dir = tmp_path / "backend" / "scripts"
    scripts_dir.mkdir(parents=True)
    (scripts_dir / "run_msaf_ensemble_paper_sim.py").write_text(
        "mart_sniper_score_daily\nmart_institution_score_daily\n"
    )

    import duckdb

    db_path = tmp_path / "data" / "smartmoney.duckdb"
    conn = duckdb.connect(str(db_path))
    conn.execute("CREATE TABLE mart_sniper_score_daily(signal_date DATE)")
    conn.execute("CREATE TABLE mart_institution_score_daily(signal_date DATE)")
    conn.close()

    monkeypatch.setattr(audit, "REPO_ROOT", tmp_path)

    result = check_strategy_model()

    assert result["sources_available"]["institution"] is True
    assert result["sources_wired"]["institution"] is False
    institution = result["source_evaluations"]["institution"]
    assert institution["status"] == "evaluated"
    assert institution["production_decision"] == "hold_reject"
