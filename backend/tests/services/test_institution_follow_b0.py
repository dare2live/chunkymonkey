"""institution_follow B0 — canary overclaim block + measured coverage honesty."""
from __future__ import annotations

import pytest

from routers import institution_profile as inst_router
from services.holdout_guard import HoldoutBoundaryViolation, load_policy
from services.institution_follow_b0 import (
    BLOCK_ID,
    BOUNDED_SCOPE,
    CANARY_ABLATION,
    CANARY_SCOPE,
    CanaryScopeOverclaimError,
    MIN_ACCEPTED_NOMINAL_DAYS_FOR_MEASURED_B0,
    REASON_CANARY_SCOPE_ONLY,
    REASON_MEASURED_COVERAGE_INSUFFICIENT,
    REQUIRED_SURFACE_STATUS,
    STRATEGY_PACKAGE,
    build_b0_run,
    finalize_b0_verdict,
    is_bounded_scope,
    is_canary_scope,
    load_frozen_disclosure_snapshot,
    measure_bare_k_coverage,
    run_b0_scaffold,
)


def _canary_snapshot(**overrides):
    base = {
        "snapshot_id": "disclosure_e0_test_canary",
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
        "snapshot_id": "disclosure_bounded_test",
        "scope": BOUNDED_SCOPE,
        "phase_e_ablation": "bounded_scope_wf_paper_still_blocked",
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


class _FakeNominalConn:
    def __init__(self, partitions: list[str]):
        self._partitions = partitions

    def execute(self, sql, params=None):  # noqa: ANN001
        class _R:
            def __init__(self, rows):
                self._rows = rows

            def fetchall(self):
                return self._rows

        if "accepted_partition" in sql:
            return _R([(p,) for p in self._partitions])
        return _R([])

    def close(self) -> None:
        return None


def test_required_surface_status_matches_research_router() -> None:
    assert REQUIRED_SURFACE_STATUS == inst_router.SURFACE_STATUS
    assert REQUIRED_SURFACE_STATUS == "tier3_research_evidence_only"


def test_b0_scaffold_consumes_frozen_snapshot_and_surface_status() -> None:
    frozen = load_frozen_disclosure_snapshot()
    # Live freeze may be canary or bounded; both are honest non-accept scopes.
    assert frozen.get("cutover_allowed") is True

    run, verdict = run_b0_scaffold(snapshot=frozen)
    assert run.strategy_package == STRATEGY_PACKAGE
    assert run.block == BLOCK_ID
    assert run.snapshot_id == frozen["snapshot_id"]
    assert run.surface_status == REQUIRED_SURFACE_STATUS
    assert run.holdout.status == "exercised"
    assert {h.name for h in run.pit_hooks} >= {
        "decision_time_truncation",
        "availability_cutoff",
        "nominal_execution_truth",
    }
    assert all(h.status.startswith("declared") for h in run.pit_hooks)
    assert run.artifact_manifest["paper_fills"] == "not_run"
    assert verdict.verdict == "inconclusive"
    assert verdict.claimable is False
    if is_canary_scope(frozen):
        assert verdict.blocked is True
        assert verdict.reason == REASON_CANARY_SCOPE_ONLY
        assert "canary_scope_blocks_claimable_verdict" in run.notes
    else:
        assert is_bounded_scope(frozen)
        assert run.bare_k_coverage is not None
        if run.bare_k_coverage.sufficient_for_measured_b0:
            # Short accepted nominal window ready; still no WF/paper edge.
            assert verdict.reason == "scaffold_no_measured_edge"
            assert verdict.blocked is False
            assert run.bare_k_coverage.accepted_nominal_day_count >= (
                MIN_ACCEPTED_NOMINAL_DAYS_FOR_MEASURED_B0
            )
            assert run.artifact_manifest["metrics"] == "coverage_measured_ready"
        else:
            assert verdict.blocked is True
            assert verdict.reason == REASON_MEASURED_COVERAGE_INSUFFICIENT
            assert run.bare_k_coverage.sufficient_for_measured_b0 is False


def test_b0_canary_default_verdict_never_accept() -> None:
    run = build_b0_run(snapshot=_canary_snapshot(), measure_coverage=False)
    verdict = finalize_b0_verdict(run)
    assert verdict.verdict == "inconclusive"
    assert verdict.blocked is True
    assert verdict.reason == REASON_CANARY_SCOPE_ONLY
    assert verdict.as_dict()["verdict"] != "accept"


def test_b0_overclaim_accept_raises_canary_block() -> None:
    run = build_b0_run(snapshot=_canary_snapshot(), measure_coverage=False)
    with pytest.raises(CanaryScopeOverclaimError, match=REASON_CANARY_SCOPE_ONLY):
        finalize_b0_verdict(run, requested_verdict="accept")
    with pytest.raises(CanaryScopeOverclaimError, match=REASON_CANARY_SCOPE_ONLY):
        finalize_b0_verdict(run, force_accept=True)


def test_b0_reject_under_canary_still_blocked_inconclusive_not_accept() -> None:
    run = build_b0_run(snapshot=_canary_snapshot(), measure_coverage=False)
    verdict = finalize_b0_verdict(run, requested_verdict="reject")
    assert verdict.verdict == "inconclusive"
    assert verdict.blocked is True
    assert verdict.reason == REASON_CANARY_SCOPE_ONLY


def test_b0_holdout_hook_rejects_training_into_holdout() -> None:
    hs = str(load_policy()["holdout_start"]).replace("-", "")[:8]
    with pytest.raises(HoldoutBoundaryViolation):
        build_b0_run(
            snapshot=_canary_snapshot(),
            data_end_date=hs,
            measure_coverage=False,
        )


def test_b0_wrong_surface_status_fails_closed() -> None:
    from services.institution_follow_b0 import InstitutionFollowB0Error

    with pytest.raises(InstitutionFollowB0Error, match="surface_status"):
        build_b0_run(
            snapshot=_canary_snapshot(),
            surface_status="released_strategy",
            measure_coverage=False,
        )


def test_b0_bounded_thin_nominal_coverage_inconclusive_never_accept() -> None:
    """A3 single-day accepted K → measured but insufficient; never accept."""

    snap = _bounded_snapshot()
    assert is_bounded_scope(snap)
    assert not is_canary_scope(snap)

    fake = _FakeNominalConn(["20260717"])  # thinner than MIN days
    run = build_b0_run(snapshot=snap, nominal_conn=fake)
    assert run.bare_k_coverage is not None
    assert run.bare_k_coverage.accepted_nominal_day_count == 1
    assert run.bare_k_coverage.sufficient_for_measured_b0 is False
    assert (
        run.bare_k_coverage.accepted_nominal_day_count
        < MIN_ACCEPTED_NOMINAL_DAYS_FOR_MEASURED_B0
    )

    verdict = finalize_b0_verdict(run, requested_verdict="accept")
    assert verdict.verdict == "inconclusive"
    assert verdict.blocked is True
    assert verdict.reason == REASON_MEASURED_COVERAGE_INSUFFICIENT
    assert verdict.claimable is False
    assert verdict.details["bare_k_coverage"]["accepted_nominal_day_count"] == 1


def test_measure_bare_k_coverage_ready_when_enough_days() -> None:
    days = [f"202607{str(i).zfill(2)}" for i in range(1, 8)]
    cov = measure_bare_k_coverage(
        _bounded_snapshot(),
        nominal_conn=_FakeNominalConn(days),
    )
    assert cov.sufficient_for_measured_b0 is True
    assert cov.accepted_nominal_day_count == 7
    assert cov.reason == "measured_nominal_window_ready"


def test_b0_ready_coverage_still_cannot_accept_without_paper() -> None:
    days = [f"202607{str(i).zfill(2)}" for i in range(1, 8)]
    run = build_b0_run(
        snapshot=_bounded_snapshot(),
        nominal_conn=_FakeNominalConn(days),
    )
    verdict = finalize_b0_verdict(run, requested_verdict="accept")
    assert verdict.verdict == "inconclusive"
    assert verdict.blocked is True
    assert verdict.reason == "scaffold_metrics_unknown"
    assert verdict.claimable is False


def test_b0_broader_scope_scaffold_still_cannot_accept_without_metrics() -> None:
    snap = _canary_snapshot(
        scope="broader_accepted_partitions",
        phase_e_ablation="eligible",
        snapshot_id="disclosure_broader_test",
    )
    assert not is_canary_scope(snap)
    run = build_b0_run(
        snapshot=snap,
        measure_coverage=False,
    )
    verdict = finalize_b0_verdict(run, requested_verdict="accept")
    assert verdict.verdict == "inconclusive"
    assert verdict.blocked is True
    assert verdict.reason == "scaffold_metrics_unknown"
    assert verdict.claimable is False
