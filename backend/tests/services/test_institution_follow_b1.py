"""institution_follow B1 scaffold — stock-state block cannot claim accept."""
from __future__ import annotations

import pytest

from services.institution_follow_b0 import (
    BOUNDED_SCOPE,
    CANARY_ABLATION,
    CANARY_SCOPE,
    CanaryScopeOverclaimError,
    REASON_PROTOCOL_READY_EDGE_UNMET,
    REQUIRED_SURFACE_STATUS,
)
from services.institution_follow_b1 import (
    BLOCK_ID,
    FEATURE_BLOCK_ID,
    REASON_B1_SCAFFOLD_NO_MEASURED_EDGE,
    build_b1_run,
    finalize_b1_verdict,
    run_b1_scaffold,
)


def _canary_snapshot(**overrides):
    base = {
        "snapshot_id": "disclosure_e0_test_canary_b1",
        "scope": CANARY_SCOPE,
        "phase_e_ablation": CANARY_ABLATION,
        "cutover_allowed": True,
        "domains": {
            "holders_top10": {"partition": "20260717", "date_set": ["20260717"]},
            "org_holding": {"partition": "20190430", "date_set": ["20190430"]},
            "stk_holdertrade": {"partition": "20260706", "date_set": ["20260706"]},
        },
    }
    base.update(overrides)
    return base


def _bounded_snapshot(**overrides):
    base = {
        "snapshot_id": "disclosure_e_bounded_b1",
        "scope": BOUNDED_SCOPE,
        "phase_e_ablation": "bounded_scope_measured_b0_short_window",
        "cutover_allowed": True,
        "domains": {
            "holders_top10": {
                "partition": "20260717",
                "date_set": ["20260619", "20260713", "20260714", "20260717"],
            },
            "org_holding": {
                "partition": "20260430",
                "date_set": ["20190430", "20260430"],
            },
            "stk_holdertrade": {
                "partition": "20260713",
                "date_set": ["20260518", "20260608", "20260706", "20260713"],
            },
        },
    }
    base.update(overrides)
    return base


def test_b1_declares_stock_state_feature_block() -> None:
    run = build_b1_run(
        snapshot=_bounded_snapshot(),
        measure_b0_paper=False,
    )
    assert run.block == BLOCK_ID
    assert run.feature_block.block_id == FEATURE_BLOCK_ID
    assert run.feature_block.status == "declared_scaffold"
    assert run.surface_status == REQUIRED_SURFACE_STATUS
    assert "b1_stock_state_scaffold" in run.notes


def test_b1_scaffold_never_accepts_under_bounded_scope() -> None:
    run, verdict = run_b1_scaffold(
        snapshot=_bounded_snapshot(),
        requested_verdict="accept",
        measure_b0_paper=False,
    )
    assert verdict.verdict == "inconclusive"
    assert verdict.claimable is False
    assert verdict.blocked is True
    assert verdict.reason in {
        REASON_B1_SCAFFOLD_NO_MEASURED_EDGE,
        REASON_PROTOCOL_READY_EDGE_UNMET,
    }
    assert verdict.details["metrics"] == "unknown"
    assert verdict.details["paper_fills"] == "not_run"
    assert run.b0.block == "B0"


def test_b1_canary_overclaim_raises() -> None:
    run = build_b1_run(snapshot=_canary_snapshot(), measure_b0_paper=False)
    with pytest.raises(CanaryScopeOverclaimError):
        finalize_b1_verdict(run, requested_verdict="accept")
