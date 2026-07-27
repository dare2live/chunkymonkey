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
    REASON_PROTOCOL_READY_EDGE_UNMET,
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


def _with_nominal(snapshot: dict, days: list[str]) -> dict:
    """Attach frozen nominal_ohlcv.date_set (pre-holdout training window)."""
    out = dict(snapshot)
    domains = dict(out.get("domains") or {})
    compact = sorted(
        {"".join(ch for ch in str(d) if ch.isdigit())[:8] for d in days}
    )
    domains["nominal_ohlcv"] = {
        "dataset_id": "tier0.market_data.nominal_ohlcv_daily",
        "date_set": compact,
        "partition": compact[-1] if compact else "",
        "training_cutoff": "20250531",
        "holdout_bound": True,
    }
    out["domains"] = domains
    return out


def _canary_snapshot(**overrides):
    base = {
        "snapshot_id": "disclosure_e0_test_canary",
        "scope": CANARY_SCOPE,
        "phase_e_ablation": CANARY_ABLATION,
        "cutover_allowed": True,
        "domains": {
            "holders_top10": {"partition": "20250508", "date_set": ["20250508"]},
            "org_holding": {"partition": "20190430", "date_set": ["20190430"]},
            "stk_holdertrade": {"partition": "20250506", "date_set": ["20250506"]},
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
                "partition": "20250516",
                "date_set": [
                    "20250408",
                    "20250416",
                    "20250418",
                    "20250423",
                    "20250503",
                    "20250509",
                    "20250510",
                    "20250513",
                    "20250514",
                    "20250516",
                ],
            },
            "org_holding": {
                "partition": "20250430",
                "date_set": ["20190430", "20250430"],
            },
            "stk_holdertrade": {
                "partition": "20250513",
                "date_set": ["20250418", "20250508", "20250506", "20250513"],
            },
        },
    }
    base.update(overrides)
    return base


def _weekday_compact_days(n_days: int, *, start: str = "20250401") -> list[str]:
    from datetime import date

    y, m, d = int(start[:4]), int(start[4:6]), int(start[6:8])
    out: list[str] = []
    while len(out) < n_days:
        cur = date(y, m, d)
        if cur.weekday() < 5:
            out.append(cur.strftime("%Y%m%d"))
        nxt = date.fromordinal(cur.toordinal() + 1)
        y, m, d = nxt.year, nxt.month, nxt.day
    return out


def _synthetic_window_bars(
    n_days: int = 8, *, days: list[str] | None = None
) -> dict[str, list[dict]]:
    """Build a tiny tradable panel for paper-fill unit tests."""

    if days is None:
        days = _weekday_compact_days(n_days, start="20250508")
    else:
        days = list(days)[:n_days]
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
    assert verdict.claimable is False
    if is_canary_scope(frozen):
        assert verdict.verdict == "inconclusive"
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
            assert verdict.blocked is True
            assert verdict.claimable is False
            if run.measured_b0.claimable:
                # Protocol power ok but edge gates fail on live short window.
                assert verdict.verdict == "reject"
                assert verdict.reason == REASON_PROTOCOL_READY_EDGE_UNMET
                assert verdict.details["accept_edge_gates"]["passed"] is False
            else:
                assert verdict.verdict == "inconclusive"
                assert verdict.reason == REASON_MEASURED_SHORT_WINDOW
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
            assert verdict.verdict == "inconclusive"
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

    snap = _with_nominal(_bounded_snapshot(), ["20250516"])  # thinner than MIN days
    assert is_bounded_scope(snap)
    assert not is_canary_scope(snap)

    run = build_b0_run(snapshot=snap)
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
    days = _weekday_compact_days(7, start="20250508")
    cov = measure_bare_k_coverage(_with_nominal(_bounded_snapshot(), days))
    assert cov.sufficient_for_measured_b0 is True
    assert cov.accepted_nominal_day_count == 7
    assert cov.reason == "measured_nominal_window_ready"
    assert cov.details["nominal_source"] == "snapshot_domains.nominal_ohlcv.date_set"


def test_b0_rejects_snapshot_nominal_past_holdout() -> None:
    """Actual frozen nominal past holdout must fail closed (not declared-only)."""
    snap = _with_nominal(_bounded_snapshot(), ["20250530", "20250602"])
    with pytest.raises(HoldoutBoundaryViolation, match="actual_data_end"):
        build_b0_run(snapshot=snap, measure_coverage=False)


def test_b0_rejects_snapshot_nominal_past_declared_end() -> None:
    snap = _with_nominal(_bounded_snapshot(), ["20250520", "20250528"])
    with pytest.raises(HoldoutBoundaryViolation, match="exceeds declared"):
        build_b0_run(
            snapshot=snap,
            data_end_date="20250525",
            measure_coverage=False,
        )


def test_short_window_uses_honest_minimal_wf_protocol() -> None:
    days = [
        "20250508",
        "20250509",
        "20250512",
        "20250513",
        "20250514",
        "20250515",
        "20250516",
        "20250519",
    ]
    assert len(days) < MIN_DAYS_FULL_PURGED_WF
    plan = plan_walk_forward(days)
    assert plan.protocol == "honest_minimal_short_window"
    assert plan.claimable_protocol is False
    assert plan.one_touch_holdout is True
    assert plan.embargo_days >= 1
    assert len(plan.holdout_dates) == 2
    assert plan.reason == REASON_MEASURED_SHORT_WINDOW


def test_full_purged_wf_at_40_days_is_claimable_protocol() -> None:
    """Prereg floor (40 trading days) must leave room for 3 purged eval folds."""

    days = _weekday_compact_days(MIN_DAYS_FULL_PURGED_WF)
    assert len(days) == MIN_DAYS_FULL_PURGED_WF
    plan = plan_walk_forward(days)
    assert plan.protocol == "purged_walk_forward"
    assert plan.claimable_protocol is True
    assert len(plan.folds) >= 3
    assert plan.reason == "purged_walk_forward_ready"
    for fold in plan.folds:
        assert fold.eval_dates
        assert fold.embargo_dates


def test_paper_fills_t1_nominal_with_costs_and_limit_stubs() -> None:
    days = _weekday_compact_days(8, start="20250508")
    bars = _synthetic_window_bars(8, days=days)
    # Force limit-up buy block on 600000 entry day after first signal.
    entry_day = days[1]
    for row in bars[entry_day]:
        if row["ts_code"] == "600000.SH":
            row["pre_close"] = 10.0
            row["open"] = 11.0  # +10% main-board limit-up open
            row["vol"] = 1_000_000.0
    # Suspend one name on an exit day.
    for row in bars[days[2]]:
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
    days = _weekday_compact_days(8, start="20250508")
    bars = _synthetic_window_bars(8, days=days)
    run = build_b0_run(
        snapshot=_with_nominal(_bounded_snapshot(), days),
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


def test_measured_40d_protocol_ready_still_not_accept() -> None:
    days = _weekday_compact_days(MIN_DAYS_FULL_PURGED_WF)
    assert days[-1] < str(load_policy()["holdout_start"]).replace("-", "")[:8]
    bars = _synthetic_window_bars(len(days), days=days)
    run = build_b0_run(
        snapshot=_with_nominal(_bounded_snapshot(), days),
        bars_by_day=bars,
    )
    assert run.measured_b0 is not None
    assert run.measured_b0.claimable is True
    assert run.measured_b0.walk_forward.protocol == "purged_walk_forward"
    verdict = finalize_b0_verdict(run, requested_verdict="accept")
    # Wired edge gates: synthetic momentum loses money / breaches DD → reject.
    assert verdict.verdict == "reject"
    assert verdict.claimable is False
    assert verdict.blocked is True
    assert verdict.reason == REASON_PROTOCOL_READY_EDGE_UNMET
    gates = verdict.details["accept_edge_gates"]
    assert gates["passed"] is False
    assert "holdout_ok" in gates["checks"]
    assert "drawdown_ok" in gates["checks"]
    assert "eval_ok" in gates["checks"]
    assert "trades_ok" in gates["checks"]


def test_accept_edge_gates_pass_only_when_all_thresholds_met() -> None:
    from services.institution_follow_b0_measure import (
        B0Prereg,
        BareKPaperMetrics,
        evaluate_accept_edge_gates,
        plan_walk_forward,
    )

    days = _weekday_compact_days(MIN_DAYS_FULL_PURGED_WF)
    plan = plan_walk_forward(days)
    assert plan.claimable_protocol is True
    good = BareKPaperMetrics(
        total_return=0.05,
        max_drawdown=0.10,
        win_rate=0.55,
        payoff_ratio=1.2,
        turnover=1.0,
        n_signals=30,
        n_trades_completed=40,
        n_unfilled=0,
        n_incomplete_exit=0,
    )
    holdout = BareKPaperMetrics(
        total_return=0.02,
        max_drawdown=0.05,
        win_rate=0.6,
        payoff_ratio=1.1,
        turnover=0.5,
        n_signals=2,
        n_trades_completed=5,
        n_unfilled=0,
        n_incomplete_exit=0,
    )
    edge = evaluate_accept_edge_gates(plan, good, holdout, prereg=B0Prereg())
    assert edge.passed is True

    bad_dd = BareKPaperMetrics(
        total_return=0.05,
        max_drawdown=0.40,
        win_rate=0.55,
        payoff_ratio=1.2,
        turnover=1.0,
        n_signals=30,
        n_trades_completed=40,
        n_unfilled=0,
        n_incomplete_exit=0,
    )
    edge_bad = evaluate_accept_edge_gates(
        plan, bad_dd, holdout, prereg=B0Prereg()
    )
    assert edge_bad.passed is False
    assert edge_bad.checks["drawdown_ok"] is False


def test_b0_ready_coverage_without_paper_still_scaffold() -> None:
    days = _weekday_compact_days(7, start="20250508")
    run = build_b0_run(
        snapshot=_with_nominal(_bounded_snapshot(), days),
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
