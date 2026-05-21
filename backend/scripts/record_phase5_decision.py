"""Record a Phase 5 challenger decision from the local Phase 4 gate result."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]


def gate_fail_reasons(gate_result: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    for gate_name in ("pbo", "dsr", "conservative", "is_oos"):
        gate = gate_result.get(gate_name) or {}
        if gate.get("passes") is False:
            reason = gate.get("reason") or "failed"
            reasons.append(f"{gate_name}: {reason}")
    return reasons


def build_decision_payload(
    *,
    model_id: str,
    gate_payload: dict[str, Any],
) -> dict[str, Any]:
    gate_model_id = gate_payload.get("model_id")
    if gate_model_id != model_id:
        raise ValueError(f"phase4 gate model_id mismatch: expected {model_id}, got {gate_model_id}")

    gate_result = gate_payload.get("gate_result") or {}
    promote_action = gate_result.get("promote_action")
    all_pass = bool(gate_result.get("all_pass"))
    fail_reasons = gate_fail_reasons(gate_result)

    if promote_action == "promote" and all_pass:
        decision = "promote_ready"
        production_status = "candidate_promote_ready"
        next_action = "run promote_champion only after final human review"
    elif promote_action == "force_retrain":
        decision = "retrain_required"
        production_status = "candidate_retrain_required"
        next_action = "keep current champion; retrain or retune locally"
    else:
        decision = "hold_reject"
        production_status = "candidate_hold_reject"
        next_action = "keep current champion; do not promote this challenger"

    return {
        "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "model_id": model_id,
        "challenger_id": gate_payload.get("challenger_id"),
        "decision": decision,
        "production_status": production_status,
        "next_action": next_action,
        "promote_action": promote_action,
        "all_pass": all_pass,
        "fail_reasons": fail_reasons,
        "phase4_gate": {
            "run_at": gate_payload.get("run_at"),
            "n_obs_20d": gate_payload.get("n_obs_20d"),
            "n_obs_10d": gate_payload.get("n_obs_10d"),
            "n_obs_5d": gate_payload.get("n_obs_5d"),
            "ann_normal": gate_payload.get("ann_normal"),
            "ann_conservative": gate_payload.get("ann_conservative"),
            "is_oos_proxy_mode": gate_payload.get("is_oos_proxy_mode"),
        },
    }


def record_phase5_decision(
    *,
    model_id: str,
    phase4_json: str | Path,
    output_json: str | Path,
) -> dict[str, Any]:
    gate_path = Path(phase4_json)
    gate_payload = json.loads(gate_path.read_text())
    payload = build_decision_payload(model_id=model_id, gate_payload=gate_payload)
    out_path = Path(output_json)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-id", required=True)
    parser.add_argument(
        "--phase4-json",
        default=str(REPO_ROOT / "data" / "reports" / "phase4_gate_result.json"),
    )
    parser.add_argument("--output-json", default=None)
    args = parser.parse_args()

    output_json = args.output_json or str(REPO_ROOT / "data" / "reports" / f"decision_{args.model_id}.json")
    payload = record_phase5_decision(
        model_id=args.model_id,
        phase4_json=args.phase4_json,
        output_json=output_json,
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
