"""institution_follow B0 scaffold + canary overclaim block."""
from __future__ import annotations

import pytest

from routers import institution_profile as inst_router
from services.holdout_guard import HoldoutBoundaryViolation, load_policy
from services.institution_follow_b0 import (
    BLOCK_ID,
    CANARY_ABLATION,
    CANARY_SCOPE,
    CanaryScopeOverclaimError,
    REASON_CANARY_SCOPE_ONLY,
    REQUIRED_SURFACE_STATUS,
    STRATEGY_PACKAGE,
    build_b0_run,
    finalize_b0_verdict,
    is_canary_scope,
    load_frozen_disclosure_snapshot,
    run_b0_scaffold,
)


def _canary_snapshot(**overrides):
    base = {
        "snapshot_id": "disclosure_e0_test_canary",
        "scope": CANARY_SCOPE,
        "phase_e_ablation": CANARY_ABLATION,
        "cutover_allowed": True,
        "domains": {
            "holders_top10": {"partition": "20260717"},
            "org_holding": {"partition": "20190430"},
            "stk_holdertrade": {"partition": "20260706"},
        },
    }
    base.update(overrides)
    return base


def test_required_surface_status_matches_research_router() -> None:
    assert REQUIRED_SURFACE_STATUS == inst_router.SURFACE_STATUS
    assert REQUIRED_SURFACE_STATUS == "tier3_research_evidence_only"


def test_b0_scaffold_consumes_frozen_snapshot_and_surface_status() -> None:
    frozen = load_frozen_disclosure_snapshot()
    assert is_canary_scope(frozen)
    assert frozen.get("phase_e_ablation") == CANARY_ABLATION

    run, verdict = run_b0_scaffold(snapshot=frozen)
    assert run.strategy_package == STRATEGY_PACKAGE
    assert run.block == BLOCK_ID
    assert run.snapshot_id == frozen["snapshot_id"]
    assert run.snapshot_scope == CANARY_SCOPE
    assert run.surface_status == REQUIRED_SURFACE_STATUS
    assert run.holdout.status == "exercised"
    assert {h.name for h in run.pit_hooks} >= {
        "decision_time_truncation",
        "availability_cutoff",
        "nominal_execution_truth",
    }
    assert all(h.status.startswith("declared") for h in run.pit_hooks)
    assert "canary_scope_blocks_claimable_verdict" in run.notes
    assert run.artifact_manifest["metrics"] == "unknown"
    assert run.artifact_manifest["paper_fills"] == "not_run"

    assert verdict.verdict == "inconclusive"
    assert verdict.blocked is True
    assert verdict.reason == REASON_CANARY_SCOPE_ONLY
    assert verdict.claimable is False


def test_b0_canary_default_verdict_never_accept() -> None:
    run = build_b0_run(snapshot=_canary_snapshot())
    verdict = finalize_b0_verdict(run)
    assert verdict.verdict == "inconclusive"
    assert verdict.blocked is True
    assert verdict.reason == REASON_CANARY_SCOPE_ONLY
    assert verdict.as_dict()["verdict"] != "accept"


def test_b0_overclaim_accept_raises_canary_block() -> None:
    run = build_b0_run(snapshot=_canary_snapshot())
    with pytest.raises(CanaryScopeOverclaimError, match=REASON_CANARY_SCOPE_ONLY):
        finalize_b0_verdict(run, requested_verdict="accept")
    with pytest.raises(CanaryScopeOverclaimError, match=REASON_CANARY_SCOPE_ONLY):
        finalize_b0_verdict(run, force_accept=True)


def test_b0_reject_under_canary_still_blocked_inconclusive_not_accept() -> None:
    """Canary may request reject/inconclusive wording, but accept is forbidden.

    Explicit reject under canary is coerced to inconclusive+blocked — the
    canary day cannot produce a claimable reject of a full-history baseline
    either; only smoke scaffolding exists.
    """

    run = build_b0_run(snapshot=_canary_snapshot())
    # requested reject is allowed as non-accept path → still canary-blocked
    verdict = finalize_b0_verdict(run, requested_verdict="reject")
    assert verdict.verdict == "inconclusive"
    assert verdict.blocked is True
    assert verdict.reason == REASON_CANARY_SCOPE_ONLY


def test_b0_holdout_hook_rejects_training_into_holdout() -> None:
    hs = str(load_policy()["holdout_start"]).replace("-", "")[:8]
    with pytest.raises(HoldoutBoundaryViolation):
        build_b0_run(snapshot=_canary_snapshot(), data_end_date=hs)


def test_b0_wrong_surface_status_fails_closed() -> None:
    from services.institution_follow_b0 import InstitutionFollowB0Error

    with pytest.raises(InstitutionFollowB0Error, match="surface_status"):
        build_b0_run(
            snapshot=_canary_snapshot(),
            surface_status="released_strategy",
        )


def test_b0_broader_scope_scaffold_still_cannot_accept_without_metrics() -> None:
    snap = _canary_snapshot(
        scope="broader_accepted_partitions",
        phase_e_ablation="eligible",
        snapshot_id="disclosure_broader_test",
    )
    assert not is_canary_scope(snap)
    run = build_b0_run(snapshot=snap)
    verdict = finalize_b0_verdict(run, requested_verdict="accept")
    assert verdict.verdict == "inconclusive"
    assert verdict.blocked is True
    assert verdict.reason == "scaffold_metrics_unknown"
    assert verdict.claimable is False
