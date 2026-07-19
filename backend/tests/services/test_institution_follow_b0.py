"""institution_follow B0 — canary block + measured coverage + short-window paper."""
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
    REASON_MEASURED_SHORT_WINDOW,
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
from services.institution_follow_b0_measure import (
    MIN_DAYS_FULL_PURGED_WF,
    UNKNOWN,
    measure_b0_paper,
    plan_walk_forward,
    simulate_paper_fills,
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


def _synthetic_window_bars(n_days: int = 8) -> dict[str, list[dict]]:
    """Build a tiny tradable panel for paper-fill unit tests."""

    # Eight calendar-like compact dates in July 2026.
    days = [
        "20260708",
        "20260709",
        "20260710",
        "20260713",
        "20260714",
        "20260715",
        "20260716",
        "20260717",
    ][:n_days]
    codes = ["600000.SH", "000001.SZ", "300001.SZ", "688001.SH", "600519.SH"]
    bars: dict[str, list[dict]] = {}
    for i, day in enumerate(days):
        rows = []
        for j, code in enumerate(codes):
            # Deterministic momentum ranks that flip across days.
            pct = float((j + 1) * 0.5 - i * 0.1)
            pre = 10.0 + j
            close = pre * (1.0 + pct / 100.0)
            open_px = pre * (1.0 + pct / 200.0)
            rows.append(
                {
                    "ts_code": code,
                    "open": open_px,
                    "high": max(open_px, close),
                    "low": min(open_px, close),
                    "close": close,
                    "pre_close": pre,
                    "pct_chg": pct,
                    "vol": 1_000_000.0,
                    "amount": 1_000_000.0 * close,
                }
            )
        bars[day] = rows
    return bars


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
    assert verdict.verdict == "inconclusive"
    assert verdict.claimable is False
    if is_canary_scope(frozen):
        assert verdict.blocked is True
        assert verdict.reason == REASON_CANARY_SCOPE_ONLY
        assert "canary_scope_blocks_claimable_verdict" in run.notes
        assert run.artifact_manifest["paper_fills"] == "not_run"
    else:
        assert is_bounded_scope(frozen)
        assert run.bare_k_coverage is not None
        if run.bare_k_coverage.sufficient_for_measured_b0:
            assert run.measured_b0 is not None
            assert run.artifact_manifest["paper_fills"] == "measured"
            assert verdict.reason == REASON_MEASURED_SHORT_WINDOW
            assert verdict.blocked is True
            assert run.bare_k_coverage.accepted_nominal_day_count >= (
                MIN_ACCEPTED_NOMINAL_DAYS_FOR_MEASURED_B0
            )
            metrics = verdict.details["metrics"]
            assert "total_return" in metrics
            assert "max_drawdown" in metrics
            assert "win_rate" in metrics
            assert "payoff_ratio" in metrics
            assert "turnover" in metrics
            assert metrics["capacity"] == UNKNOWN
            assert metrics["annualized_return"] == UNKNOWN
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
    assert run.measured_b0 is None

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


def test_short_window_uses_honest_minimal_wf_protocol() -> None:
    days = [
        "20260708",
        "20260709",
        "20260710",
        "20260713",
        "20260714",
        "20260715",
        "20260716",
        "20260717",
    ]
    assert len(days) < MIN_DAYS_FULL_PURGED_WF
    plan = plan_walk_forward(days)
    assert plan.protocol == "honest_minimal_short_window"
    assert plan.claimable_protocol is False
    assert plan.one_touch_holdout is True
    assert plan.embargo_days >= 1
    assert len(plan.holdout_dates) == 2
    assert plan.reason == REASON_MEASURED_SHORT_WINDOW


def test_paper_fills_t1_nominal_with_costs_and_limit_stubs() -> None:
    bars = _synthetic_window_bars(8)
    # Force limit-up buy block on 600000 entry day after first signal.
    entry_day = "20260709"
    for row in bars[entry_day]:
        if row["ts_code"] == "600000.SH":
            row["pre_close"] = 10.0
            row["open"] = 11.0  # +10% main-board limit-up open
            row["vol"] = 1_000_000.0
    # Suspend one name on an exit day.
    for row in bars["20260710"]:
        if row["ts_code"] == "000001.SZ":
            row["vol"] = 0.0

    plan = plan_walk_forward(sorted(bars))
    fills = simulate_paper_fills(bars, plan)
    assert fills
    statuses = {f.status for f in fills}
    assert "filled" in statuses or "unfilled" in statuses
    assert any(f.reason == "limit_up_buy_blocked_stub" for f in fills)
    assert any(
        f.reason in {"suspended_exit_stub", "suspended_entry_stub"} for f in fills
    )
    completed = [f for f in fills if f.status == "filled"]
    if completed:
        assert all(f.entry_date > f.signal_date for f in completed)
        assert all(f.net_return is not None for f in completed)
        # Costs make net < gross when prices move.
        assert all(
            f.net_return is not None
            and f.gross_return is not None
            and f.net_return <= f.gross_return + 1e-12
            for f in completed
        )


def test_metrics_report_unknowns_explicit() -> None:
    bars = _synthetic_window_bars(8)
    result = measure_b0_paper(bars)
    assert result.claimable is False
    assert result.reason == REASON_MEASURED_SHORT_WINDOW
    m = result.metrics
    assert m.capacity == UNKNOWN
    assert m.annualized_return == UNKNOWN
    assert m.sharpe == UNKNOWN
    assert m.excess_return == UNKNOWN
    assert "total_return" in m.as_dict()
    assert "max_drawdown" in m.as_dict()
    assert "win_rate" in m.as_dict()
    assert "turnover" in m.as_dict()


def test_measured_short_window_verdict_inconclusive_not_claimable() -> None:
    days = sorted(_synthetic_window_bars(8))
    fake = _FakeNominalConn(days)
    bars = _synthetic_window_bars(8)
    run = build_b0_run(
        snapshot=_bounded_snapshot(),
        nominal_conn=fake,
        bars_by_day=bars,
    )
    assert run.measured_b0 is not None
    assert run.artifact_manifest["paper_fills"] == "measured"
    verdict = finalize_b0_verdict(run, requested_verdict="accept")
    assert verdict.verdict == "inconclusive"
    assert verdict.claimable is False
    assert verdict.blocked is True
    assert verdict.reason == REASON_MEASURED_SHORT_WINDOW
    assert verdict.details["metrics"]["capacity"] == UNKNOWN


def test_b0_ready_coverage_without_paper_still_scaffold() -> None:
    days = [f"202607{str(i).zfill(2)}" for i in range(1, 8)]
    run = build_b0_run(
        snapshot=_bounded_snapshot(),
        nominal_conn=_FakeNominalConn(days),
        measure_paper=False,
    )
    assert run.measured_b0 is None
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
        measure_paper=False,
    )
    verdict = finalize_b0_verdict(run, requested_verdict="accept")
    assert verdict.verdict == "inconclusive"
    assert verdict.blocked is True
    assert verdict.reason == "scaffold_metrics_unknown"
    assert verdict.claimable is False
