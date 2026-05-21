from __future__ import annotations

import json

import pytest

from scripts.record_phase5_decision import build_decision_payload, record_phase5_decision


def test_build_decision_payload_rejects_blocked_gate():
    payload = build_decision_payload(
        model_id="model_a",
        gate_payload={
            "model_id": "model_a",
            "challenger_id": "challenger_a",
            "n_obs_20d": 34,
            "gate_result": {
                "promote_action": "block",
                "all_pass": False,
                "pbo": {"passes": False, "reason": "PBO=0.626"},
                "dsr": {"passes": True},
                "conservative": {"passes": True},
                "is_oos": {"passes": True},
            },
        },
    )

    assert payload["decision"] == "hold_reject"
    assert payload["production_status"] == "candidate_hold_reject"
    assert payload["next_action"] == "keep current champion; do not promote this challenger"
    assert payload["fail_reasons"] == ["pbo: PBO=0.626"]


def test_build_decision_payload_allows_promote_ready_only_when_gate_promotes():
    payload = build_decision_payload(
        model_id="model_a",
        gate_payload={
            "model_id": "model_a",
            "challenger_id": "challenger_a",
            "gate_result": {"promote_action": "promote", "all_pass": True},
        },
    )

    assert payload["decision"] == "promote_ready"
    assert payload["next_action"] == "run promote_champion only after final human review"


def test_record_phase5_decision_rejects_model_mismatch(tmp_path):
    phase4 = tmp_path / "phase4.json"
    phase4.write_text(json.dumps({
        "model_id": "other_model",
        "gate_result": {"promote_action": "block", "all_pass": False},
    }))

    with pytest.raises(ValueError, match="model_id mismatch"):
        record_phase5_decision(
            model_id="model_a",
            phase4_json=phase4,
            output_json=tmp_path / "decision.json",
        )
