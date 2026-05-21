from __future__ import annotations

import json
from pathlib import Path

from scripts.audit_msaf_probe_frontier import load_msaf_probes, main, summarize_frontier


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_frontier_matches_phase4_gates_and_marks_no_promotable(tmp_path: Path) -> None:
    reports = tmp_path / "reports"
    reports.mkdir()
    _write_json(
        reports / "msaf_ensemble_gcp_v6_lm73_sniper27_h10_probe_20260520.json",
        {
            "args": {"horizon": "10d", "lambdamart_model_id": "m1"},
            "source_weight_override": {
                "lambdamart_weight": 0.73,
                "sniper_weight": 0.27,
                "institution_weight": 0.0,
            },
            "kpi": {"n_obs": 68, "sharpe": 1.85, "max_dd": -0.14, "ann_ret_cagr": 0.9},
        },
    )
    _write_json(
        reports / "msaf_ensemble_gcp_v6_lm73_sniper27_h10_scorefloor45_probe_20260520.json",
        {
            "args": {"horizon": "10d", "lambdamart_model_id": "m1"},
            "source_weight_override": {
                "lambdamart_weight": 0.73,
                "sniper_weight": 0.27,
                "institution_weight": 0.0,
            },
            "score_filter": {"min_top_score": 0.45},
            "kpi": {"n_obs": 62, "sharpe": 2.01, "max_dd": -0.14, "ann_ret_cagr": 1.0},
        },
    )
    _write_json(
        reports / "phase4_gate_msaf_gcp_v6_lm73_sniper27_h10_probe_20260520.json",
        {
            "model_id": "m1",
            "primary_horizon": "10d",
            "source_weight_override": {
                "lambdamart_weight": 0.73,
                "sniper_weight": 0.27,
                "institution_weight": 0.0,
            },
            "is_oos_proxy_mode": True,
            "gate_result": {
                "all_pass": True,
                "promote_action": "warn_only_proxy",
                "pbo": {"passes": True, "detail": {"pbo": 0.1}},
                "dsr": {"passes": True},
                "conservative": {"passes": True},
                "is_oos": {"passes": True},
            },
        },
    )
    _write_json(
        reports / "phase4_gate_msaf_gcp_v6_lm73_sniper27_h10_scorefloor45_probe_20260520.json",
        {
            "model_id": "m1",
            "primary_horizon": "10d",
            "source_weight_override": {
                "lambdamart_weight": 0.73,
                "sniper_weight": 0.27,
                "institution_weight": 0.0,
            },
            "score_filter": {"min_top_score": 0.45},
            "gate_result": {
                "all_pass": False,
                "promote_action": "block",
                "pbo": {"passes": False, "reason": "PBO=0.33", "detail": {"pbo": 0.33}},
            },
        },
    )
    _write_json(
        reports / "msaf_ensemble_gcp_v6_lm76_sniper24_h10_probe_20260520.json",
        {
            "args": {"horizon": "10d", "lambdamart_model_id": "m1"},
            "source_weight_override": {
                "lambdamart_weight": 0.76,
                "sniper_weight": 0.24,
                "institution_weight": 0.0,
            },
            "kpi": {"n_obs": 68, "sharpe": 2.02, "max_dd": -0.14, "ann_ret_cagr": 1.0},
        },
    )
    _write_json(
        reports / "phase4_gate_msaf_gcp_v6_lm76_sniper24_h10_probe_20260520.json",
        {
            "model_id": "m1",
            "primary_horizon": "10d",
            "source_weight_override": {
                "lambdamart_weight": 0.76,
                "sniper_weight": 0.24,
                "institution_weight": 0.0,
            },
            "gate_result": {
                "all_pass": False,
                "promote_action": "block",
                "pbo": {"passes": False, "reason": "PBO=0.38", "detail": {"pbo": 0.38}},
            },
        },
    )

    rows = load_msaf_probes(reports)
    summary = summarize_frontier(rows)

    assert summary["n_source_weight_probes"] == 3
    assert summary["n_gate_pass"] == 1
    assert summary["n_perfect_ladder_ready"] == 2
    assert summary["n_strict_blocked_by_pbo"] == 2
    assert summary["n_promotable"] == 0
    assert summary["n_proxy_candidate_ready"] == 0
    assert summary["gate_pass_nearest_strict_ladder"]["sharpe_gap_to_perfect"] == 0.1499999999999999
    assert summary["best_gate_pass"]["sharpe_gap_to_perfect"] == 0.1499999999999999
    assert summary["best_strict_ladder"]["phase4_gate"]["pbo_passed"] is False
    assert summary["best_strict_ladder"]["phase4_gate"]["pbo_value"] == 0.38
    assert summary["strict_nearest_pbo_pass"]["phase4_gate"]["pbo_value"] == 0.33
    assert summary["strict_min_pbo_gap_to_pass"] == 0.13


def test_main_writes_frontier_json_and_returns_nonzero_without_promotable(tmp_path: Path) -> None:
    reports = tmp_path / "reports"
    reports.mkdir()
    _write_json(
        reports / "msaf_ensemble_lm70_sniper30_probe_20260520.json",
        {
            "args": {"horizon": "20d", "lambdamart_model_id": "m1"},
            "source_weight_override": {"lambdamart_weight": 0.7, "sniper_weight": 0.3},
            "kpi": {"n_obs": 34, "sharpe": 1.2, "max_dd": -0.19},
        },
    )
    out = tmp_path / "frontier.json"

    assert main(["--reports-dir", str(reports), "--json-out", str(out)]) == 1
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["summary"]["verdict"] == "NO_PROMOTABLE_PROBE"


def test_proxy_gate_strict_probe_is_candidate_not_hard_promote(tmp_path: Path) -> None:
    reports = tmp_path / "reports"
    reports.mkdir()
    _write_json(
        reports / "msaf_ensemble_lm735_sniper265_h10_k3_neutralcash20_probe_20260521.json",
        {
            "args": {"horizon": "10d", "lambdamart_model_id": "m1", "max_positions": 3},
            "source_weight_override": {"lambdamart_weight": 0.735, "sniper_weight": 0.265},
            "position_sizing": {"rank_decay": 0.82},
            "cash_overlay": {"neutral_cash_pct": 0.20},
            "kpi": {"n_obs": 68, "sharpe": 2.09, "max_dd": -0.1998},
        },
    )
    _write_json(
        reports / "phase4_gate_msaf_lm735_sniper265_h10_k3_neutralcash20_probe_20260521.json",
        {
            "model_id": "m1",
            "primary_horizon": "10d",
            "source_weight_override": {"lambdamart_weight": 0.735, "sniper_weight": 0.265},
            "position_sizing": {"rank_decay": 0.82},
            "max_positions": 3,
            "cash_overlay": {"neutral_cash_pct": 0.20},
            "is_oos_proxy_mode": True,
            "gate_result": {
                "all_pass": True,
                "promote_action": "warn_only_proxy",
                "pbo": {"passes": True, "detail": {"pbo": 0.12}},
                "dsr": {"passes": True},
                "conservative": {"passes": True},
                "is_oos": {"passes": True},
            },
        },
    )
    out = tmp_path / "frontier.json"

    assert main(["--reports-dir", str(reports), "--json-out", str(out)]) == 1
    payload = json.loads(out.read_text(encoding="utf-8"))
    summary = payload["summary"]

    assert summary["verdict"] == "PROXY_READY_NEEDS_TRUE_IS_OOS"
    assert summary["n_promotable"] == 0
    assert summary["n_proxy_candidate_ready"] == 1
    assert summary["best_proxy_candidate_ready"]["gate_proxy_pass"] is True
    assert summary["best_proxy_candidate_ready"]["promotable"] is False


def test_hard_promote_strict_probe_is_promotable(tmp_path: Path) -> None:
    reports = tmp_path / "reports"
    reports.mkdir()
    _write_json(
        reports / "msaf_ensemble_lm735_sniper265_h10_k3_probe_20260521.json",
        {
            "args": {"horizon": "10d", "lambdamart_model_id": "m1", "max_positions": 3},
            "source_weight_override": {"lambdamart_weight": 0.735, "sniper_weight": 0.265},
            "kpi": {"n_obs": 68, "sharpe": 2.09, "max_dd": -0.19},
        },
    )
    _write_json(
        reports / "phase4_gate_msaf_lm735_sniper265_h10_k3_probe_20260521.json",
        {
            "model_id": "m1",
            "primary_horizon": "10d",
            "source_weight_override": {"lambdamart_weight": 0.735, "sniper_weight": 0.265},
            "max_positions": 3,
            "gate_result": {
                "all_pass": True,
                "promote_action": "promote",
                "pbo": {"passes": True, "detail": {"pbo": 0.12}},
                "dsr": {"passes": True},
                "conservative": {"passes": True},
                "is_oos": {"passes": True},
            },
        },
    )

    summary = summarize_frontier(load_msaf_probes(reports))

    assert summary["verdict"] == "PASS"
    assert summary["n_promotable"] == 1
    assert summary["n_proxy_candidate_ready"] == 0


def test_strict_probe_without_gate_does_not_crash_or_count_as_pbo_block(tmp_path: Path) -> None:
    reports = tmp_path / "reports"
    reports.mkdir()
    _write_json(
        reports / "msaf_ensemble_lm75_sniper25_h10_scorefloor45_probe_20260520.json",
        {
            "args": {"horizon": "10d", "lambdamart_model_id": "m1"},
            "source_weight_override": {
                "lambdamart_weight": 0.75,
                "sniper_weight": 0.25,
                "institution_weight": 0.0,
            },
            "score_filter": {"min_top_score": 0.45},
            "kpi": {"n_obs": 62, "sharpe": 2.01, "max_dd": -0.14},
        },
    )

    summary = summarize_frontier(load_msaf_probes(reports))

    assert summary["n_perfect_ladder_ready"] == 1
    assert summary["n_strict_blocked_by_pbo"] == 0
    assert summary["n_promotable"] == 0
