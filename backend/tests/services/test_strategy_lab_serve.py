"""Compact strategy-lab observation projection — no partition dumps, never claimable."""
from __future__ import annotations

import json

from services.strategy_lab_serve import (
    compact_coverage,
    compact_verdict,
    get_experiment,
    list_experiments,
    list_packages,
    overview_payload,
    release_projection,
    snapshot_cards,
    status_payload,
)


def test_compact_coverage_omits_partition_list():
    out = compact_coverage(
        {
            "accepted_nominal_day_count": 3,
            "accepted_nominal_partitions": ["20190102", "20190103", "20190104"],
            "status": "ok",
            "reason": "bounded",
        }
    )
    assert out["partitions_omitted"] is True
    assert out["accepted_nominal_day_count"] == 3
    assert out["window_start"] == "20190102"
    assert out["window_end"] == "20190104"
    assert "accepted_nominal_partitions" not in out


def test_compact_verdict_drops_run_and_keeps_reject():
    payload = {
        "block": "b0",
        "blocked": True,
        "claimable": True,  # even if a file lied, projection must force false
        "experiment_id": "institution_follow_v1:B0:x",
        "kind": "phase_e_experiment_verdict",
        "reason": "measured_protocol_ready_edge_gates_unmet",
        "snapshot_id": "disclosure_bounded",
        "snapshot_hash": "abc",
        "strategy_release": True,
        "verdict": "reject",
        "run": {"huge": list(range(1000))},
        "verdict_full": {"coverage": {"accepted_nominal_partitions": ["20190102"]}},
        "metrics_summary": {
            "accept_edge_gates": {
                "passed": False,
                "reason": "accept_edge_gates_unmet",
                "checks": {
                    "eval_total_return": -0.5,
                    "holdout_net_return": 0.01,
                    "max_drawdown": 0.2,
                    "n_trades_completed": 10,
                },
            },
            "coverage": {
                "accepted_nominal_day_count": 2,
                "accepted_nominal_partitions": ["20190102", "20250530"],
            },
            "metrics": {"total_return": -0.5, "details": {"x": 1}},
        },
        "notes": ["ablation_only"],
    }
    row = compact_verdict(payload)
    blob = json.dumps(row)
    assert "accepted_nominal_partitions" not in blob
    assert "huge" not in blob
    assert row["claimable"] is False
    assert row["strategy_release"] is False
    assert row["not_strategy_spec"] is True
    assert row["family"] == "institution_follow_v1"
    assert row["verdict"] == "reject"
    assert row["experiment_id"] == "institution_follow_v1:b0"
    assert row["coverage"]["window_start"] == "20190102"
    assert row["coverage"]["partitions_omitted"] is True
    assert "verdict_full" not in row


def test_live_experiments_are_compact_and_unclaimable():
    rows = list_experiments()
    assert len(rows) >= 7
    blob = json.dumps(rows)
    assert "accepted_nominal_partitions" not in blob
    assert rows[0]["claimable"] is False
    families = {row["family"] for row in rows}
    assert "institution_follow_v1" in families
    assert "main_rally_v1" in families
    e0 = get_experiment("institution_follow_v1", "b0")
    assert e0 is not None
    assert e0["verdict"] in {"reject", "inconclusive"}
    assert e0["claimable"] is False
    assert e0["role"] == "ablation_only"
    assert e0["experiment_id"] == "institution_follow_v1:b0"
    assert "," not in str(e0["experiment_id"])
    f0 = get_experiment("main_rally_v1", "b0")
    assert f0 is not None
    assert f0["verdict"] == "reject"


def test_packages_load_three_families_unclaimable():
    payload = list_packages()
    assert payload["loaded"] is True
    assert payload["claimable"] is False
    ids = {item["package_id"] for item in payload["packages"]}
    assert ids == {"formulas", "institution_follow_v1", "main_rally_v1"}
    follow = next(item for item in payload["packages"] if item["package_id"] == "institution_follow_v1")
    layers = {row["layer"] for row in follow["layers"]}
    assert "profile_alpha" in layers
    assert "follow_spec_paper" in layers
    assert "phase_e_ablation" in layers


def test_snapshots_omit_domain_accepted_rows():
    payload = snapshot_cards()
    blob = json.dumps(payload)
    assert '"accepted":' not in blob
    kinds = {card["kind"] for card in payload["cards"]}
    assert kinds == {"disclosure_snapshot", "main_rally_snapshot", "holdout_seal"}
    seal = next(card for card in payload["cards"] if card["kind"] == "holdout_seal")
    assert seal["opaque"] is True
    assert seal["partitions_omitted"] is True


def test_release_projection_never_accepts():
    status = status_payload()
    rows = list_experiments()
    snaps = snapshot_cards()
    release = release_projection(status, rows, snaps)
    assert release["claimable"] is False
    assert release["strategy_release"] is False
    assert release["any_accept"] is False
    assert release["n_accept"] == 0
    by_id = {gate["id"]: gate for gate in release["gates"]}
    assert by_id["accept"]["state"] == "fail"
    assert by_id["pit"]["state"] == "unknown"
    assert by_id["leakage"]["state"] == "unknown"
    assert by_id["monitor"]["state"] == "blocked"


def test_overview_envelope_locks_claimable():
    payload = overview_payload()
    assert payload["claimable"] is False
    assert payload["strategy_release"] is False
    assert payload["surface_status"] == "tier3_research_evidence_only"
    assert payload["framework"]["claimable"] is False
