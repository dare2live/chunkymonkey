"""Champion deployment summary read-model helpers."""
from __future__ import annotations

from typing import Any


def build_champion_deployment_summary(
    *,
    lifecycle: dict[str, Any],
    gates: list[dict[str, Any]],
    topk: dict[str, Any],
) -> dict[str, Any]:
    champions = lifecycle.get("champions") or []
    champion_id = champions[0].get("model_id") if champions else None
    latest_promotion_gate = None
    latest_self_check = None
    for gate in gates:
        challenger = gate.get("challenger_model_id")
        previous_champion = gate.get("champion_model_id")
        if champion_id and challenger == champion_id and previous_champion != champion_id and not latest_promotion_gate:
            latest_promotion_gate = gate
        if champion_id and challenger == champion_id and previous_champion == champion_id and not latest_self_check:
            latest_self_check = gate

    blockers: list[str] = []
    if not champion_id:
        blockers.append("missing_lifecycle_champion")
    if champion_id and not latest_promotion_gate:
        blockers.append("missing_passing_promotion_gate")
    if latest_promotion_gate and str(latest_promotion_gate.get("promotion_status") or "").upper() != "PASS":
        blockers.append("latest_promotion_gate_not_pass")
    if champion_id and topk.get("model_id") != champion_id:
        blockers.append("primary_topk_model_mismatch")
    if champion_id and not topk.get("count"):
        blockers.append("missing_primary_topk")

    status = "deployed" if not blockers else "needs_attention"
    return {
        "status": status,
        "champion_model_id": champion_id,
        "primary_topk_model_id": topk.get("model_id"),
        "primary_topk_count": topk.get("count"),
        "latest_promotion_gate": latest_promotion_gate,
        "latest_self_check": latest_self_check,
        "blockers": blockers,
    }
