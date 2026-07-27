"""main_rally B2 measured market-sensing gating vs B0 (Phase F / F3)."""
from __future__ import annotations

import ast
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest
from services.snapshot_nominal_bind import offline_fixture_bars

from services.institution_follow_edge_gates import (
    REASON_HOLDOUT_LIFT_UNMET,
    evaluate_holdout_lift_vs_b0,
)
from services.main_rally_b0 import (
    BOUNDED_SCOPE,
    CANARY_SCOPE,
    CanaryScopeOverclaimError,
    REASON_OFFLINE_FIXTURE_NOT_FORMAL,
    REASON_PROTOCOL_READY_EDGE_UNMET,
    REQUIRED_SURFACE_STATUS,
    STRATEGY_PACKAGE,
    build_b0_run,
)
from services.main_rally_b0_measure import eligible_codes_by_signal_day
from services.main_rally_b2 import (
    BLOCK_ID,
    FEATURE_BLOCK_ID,
    REASON_B2_SCAFFOLD_NO_MEASURED_EDGE,
    build_b2_run,
    finalize_b2_verdict,
    run_b2_scaffold,
)
from services.main_rally_b2_measure import (
    REASON_B2_CONTEXT_COVERAGE_INSUFFICIENT,
    REASON_B2_PULSE_UNTRUSTED,
    SOURCE_PULSE_MART,
    build_context_by_day,
    build_market_context_from_nominal_bars,
    eligible_by_day_from_context_and_setup,
    measure_b2_paper,
    measure_market_context_coverage,
    refuse_pulse_mart_as_market_context,
)


def _weekday_compact_days(n_days: int, *, start: str = "20240102") -> list[str]:
    y, m, d = int(start[:4]), int(start[4:6]), int(start[6:8])
    out: list[str] = []
    while len(out) < n_days:
        cur = date(y, m, d)
        if cur.weekday() < 5:
            out.append(cur.strftime("%Y%m%d"))
        nxt = date.fromordinal(cur.toordinal() + 1)
        y, m, d = nxt.year, nxt.month, nxt.day
    return out


def _bounded_snapshot(**overrides):
    base = {
        "snapshot_id": "main_rally_bounded_test_b2",
        "scope": BOUNDED_SCOPE,
        "phase_f_ablation": "bounded_scope_setup_entry_short_horizon",
        "cutover_allowed": True,
        "strategy_package": STRATEGY_PACKAGE,
        "domains": {
            "nominal_ohlcv": {
                "dataset_id": "tier0.market_data.nominal_ohlcv_daily",
                "date_set": _weekday_compact_days(40),
                "content_hash": "nomhash",
                "config_hash": "nomcfg",
            },
            "rally_gt": {
                "taxonomy_version": "v2_20260702",
                "config_hash": "gtcfg",
                "tables": {
                    "fact_rally_ground_truth": {
                        "row_count": 1,
                        "content_hash": "gthash",
                    },
                    "fact_rally_negative": {
                        "row_count": 1,
                        "content_hash": "neghash",
                    },
                    "fact_rally_strata": {
                        "row_count": 1,
                        "content_hash": "stratahash",
                    },
                },
            },
            "tier12_accepted": {
                "partitions": ["20250429", "20250430"],
                "artifact_dir": "data/lineage/tier12_publish_batches",
            },
        },
        "notes": ["test"],
    }
    base.update(overrides)
    return base


def _canary_snapshot(**overrides):
    base = _bounded_snapshot(
        scope=CANARY_SCOPE,
        phase_f_ablation="blocked_canary_scope_only",
    )
    base.update(overrides)
    return base


def _bars_with_setups(n_days: int = 180, pivot_idx: int = 130) -> dict[str, list[dict]]:
    """Synthetic panel with confirmable pivot-low + long base on 600000.SH.

    Mirrors the known-good fixture in test_main_rally_b1.py — all pct_chg
    values are non-negative so a nominal-bars MarketContextSnapshot for this
    panel is risk_on=True on every day by construction.
    """

    days = _weekday_compact_days(n_days)
    bars: dict[str, list[dict]] = {d: [] for d in days}
    win = 20
    codes = ["600000.SH", "000001.SZ", "300001.SZ", "688001.SH", "600519.SH"]
    for i, day in enumerate(days):
        for j, code in enumerate(codes):
            if code == "600000.SH":
                if i == pivot_idx:
                    low, high, close = 9.0, 10.2, 9.5
                elif abs(i - pivot_idx) <= win:
                    low, high, close = 10.0, 11.0, 10.5
                else:
                    low, high, close = 10.0, 10.8, 10.4
                pct = 2.0 if i == pivot_idx + win else 0.2
            else:
                low, high, close = 10.0 + j, 11.0 + j, 10.5 + j
                pct = 0.05 * (j + 1)
            bars[day].append(
                {
                    "ts_code": code,
                    "open": close,
                    "high": high,
                    "low": low,
                    "close": close,
                    "pre_close": 10.0 + j,
                    "pct_chg": pct,
                    "vol": 1_000_000.0,
                    "amount": 1_000_000.0 * close,
                }
            )
    return offline_fixture_bars(bars)


def _risk_off_context(bars: dict[str, list[dict]], days: list[str]) -> dict:
    """Fully-covered but risk-off context (never widens/leaks eligibility)."""

    ready = build_context_by_day(bars, days)
    return {d: replace(ctx, risk_on=False) for d, ctx in ready.items()}


def test_b2_declares_market_sensing_feature_block() -> None:
    run = build_b2_run(
        snapshot=_bounded_snapshot(),
        measure_b0_paper=False,
        measure_b2_paper_flag=False,
    )
    assert run.block == BLOCK_ID
    assert run.feature_block.block_id == FEATURE_BLOCK_ID
    assert run.feature_block.status == "declared_scaffold"
    assert run.surface_status == REQUIRED_SURFACE_STATUS
    assert "b2_market_sensing_block" in run.notes
    assert run.artifact_manifest["strategy_release"] is False
    assert run.artifact_manifest["optuna"] is False


def test_pulse_mart_refused_fail_closed() -> None:
    snap = refuse_pulse_mart_as_market_context("20260717")
    assert snap.trust_status == "UNTRUSTED"
    assert snap.risk_on is None
    assert snap.refuse_reason == REASON_B2_PULSE_UNTRUSTED
    assert snap.available_at is None


def test_nominal_breadth_sets_available_at_and_risk_on() -> None:
    rows = [
        {"ts_code": "600000.SH", "pct_chg": 1.0},
        {"ts_code": "000001.SZ", "pct_chg": 0.5},
        {"ts_code": "300001.SZ", "pct_chg": -0.2},
    ]
    ctx = build_market_context_from_nominal_bars("20260717", rows)
    assert ctx.trust_status == "READY"
    assert ctx.available_at == "20260717"
    assert ctx.adv_n == 2
    assert ctx.dec_n == 1
    assert ctx.risk_on is True


def test_candidate_generator_never_reads_gt_label_tables() -> None:
    """§8.2: B2 measure module must not read rally GT/negative label tables."""

    for name in ("main_rally_b2_measure.py", "main_rally_b2.py"):
        path = Path(__file__).resolve().parents[2] / "services" / name
        src = path.read_text(encoding="utf-8")
        tree = ast.parse(src)
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
        assert not any("rally_gt" in mod for mod in imported)
        assert "fact_rally_ground_truth" not in src
        assert "fact_rally_negative" not in src


def test_b2_coverage_insufficient_is_inconclusive_not_fake_improve() -> None:
    bars = _bars_with_setups()
    days = sorted(bars)
    # Only 1 day of context → coverage fails.
    thin_ctx = build_context_by_day({days[0]: bars[days[0]]}, [days[0]])
    cov = measure_market_context_coverage(days, thin_ctx)
    assert cov.sufficient is False

    b0 = build_b0_run(
        snapshot=_bounded_snapshot(),
        accepted_nominal_partitions=days,
        bars_by_day=bars,
    )
    run, verdict = run_b2_scaffold(
        snapshot=_bounded_snapshot(),
        b0_run=b0,
        measure_b0_paper=False,
        bars_by_day=bars,
        context_by_day=thin_ctx,
        requested_verdict="accept",
    )
    assert run.measured_b2 is not None
    assert run.measured_b2.measured is None
    assert verdict.verdict == "inconclusive"
    assert verdict.claimable is False
    assert verdict.reason == REASON_B2_CONTEXT_COVERAGE_INSUFFICIENT
    assert verdict.details["metrics"] == "market_context_coverage_insufficient"
    assert verdict.details["strategy_release"] is False


def test_b2_pulse_source_inconclusive_not_silent_fallback() -> None:
    bars = _bars_with_setups()
    days = sorted(bars)
    b0 = build_b0_run(
        snapshot=_bounded_snapshot(),
        accepted_nominal_partitions=days,
        bars_by_day=bars,
    )
    run, verdict = run_b2_scaffold(
        snapshot=_bounded_snapshot(),
        b0_run=b0,
        measure_b0_paper=False,
        bars_by_day=bars,
        source=SOURCE_PULSE_MART,
        requested_verdict="accept",
    )
    assert run.measured_b2 is not None
    assert run.measured_b2.measured is None
    assert run.feature_block.status == "pulse_untrusted_fail_closed"
    assert verdict.verdict == "inconclusive"
    assert verdict.claimable is False
    assert verdict.reason == REASON_B2_PULSE_UNTRUSTED


def test_eligible_intersection_restricts_to_setup_and_risk_on() -> None:
    bars = _bars_with_setups()
    days = sorted(bars)
    b0 = build_b0_run(
        snapshot=_bounded_snapshot(),
        accepted_nominal_partitions=days,
        bars_by_day=bars,
    )
    assert b0.measured_b0 is not None
    setup_eligible = eligible_codes_by_signal_day(bars)
    assert setup_eligible, "expected at least one pivot-confirmed setup day"

    ready_ctx = build_context_by_day(bars, days)
    measured = measure_b2_paper(bars, b0_measured=b0.measured_b0, context_by_day=ready_ctx)
    assert measured.coverage.sufficient is True
    # Risk-on days must not widen B0's own setup-detector eligibles.
    for day, codes in measured.eligible_by_day.items():
        assert set(codes).issubset(setup_eligible.get(day) or set())

    # Fully risk-off context must collapse B2 eligibility to empty everywhere.
    risk_off_ctx = _risk_off_context(bars, days)
    measured_off = measure_b2_paper(bars, b0_measured=b0.measured_b0, context_by_day=risk_off_ctx)
    assert all(not codes for codes in measured_off.eligible_by_day.values())


def test_eligible_by_day_from_context_and_setup_never_widens() -> None:
    setup = {"20260101": {"600000"}, "20260102": {"600000", "000001"}}
    ready = {
        "20260101": build_market_context_from_nominal_bars(
            "20260101", [{"ts_code": "600000.SH", "pct_chg": 1.0}]
        ),
        "20260102": build_market_context_from_nominal_bars(
            "20260102", [{"ts_code": "600000.SH", "pct_chg": -1.0}]
        ),
    }
    out = eligible_by_day_from_context_and_setup(setup, ready)
    assert out["20260101"] == {"600000"}
    # Single decliner with no advancer → risk_on False → empty, never widened.
    assert out["20260102"] == set()


def test_b2_measured_vs_b0_reports_delta_and_rejects_on_edge_gates() -> None:
    bars = _bars_with_setups()
    days = sorted(bars)
    ctx = build_context_by_day(bars, days)

    b0 = build_b0_run(
        snapshot=_bounded_snapshot(),
        accepted_nominal_partitions=days,
        bars_by_day=bars,
    )
    assert b0.measured_b0 is not None

    measured = measure_b2_paper(bars, b0_measured=b0.measured_b0, context_by_day=ctx)
    assert measured.coverage.sufficient is True
    assert measured.measured is not None
    assert measured.delta is not None
    assert "total_return" in measured.delta.as_dict()

    run, verdict = run_b2_scaffold(
        snapshot=_bounded_snapshot(),
        b0_run=b0,
        measure_b0_paper=False,
        bars_by_day=bars,
        context_by_day=ctx,
        requested_verdict="accept",
    )
    assert run.feature_block.status == "measured_gated"
    assert run.artifact_manifest["paper_fills"] == "measured"
    assert verdict.claimable is False
    assert verdict.details["strategy_release"] is False
    assert verdict.verdict == "inconclusive"
    assert verdict.reason == REASON_OFFLINE_FIXTURE_NOT_FORMAL
    assert verdict.details["delta_b2_minus_b0"] is not None
    assert verdict.details["b0_metrics"] is not None
    assert verdict.details["metrics"]["n_trades_completed"] >= 0


def test_b2_scaffold_without_measure_never_accepts() -> None:
    run, verdict = run_b2_scaffold(
        snapshot=_bounded_snapshot(),
        requested_verdict="accept",
        measure_b0_paper=False,
        measure_b2_paper_flag=False,
    )
    assert verdict.verdict == "inconclusive"
    assert verdict.claimable is False
    assert verdict.reason == REASON_B2_SCAFFOLD_NO_MEASURED_EDGE


def test_b2_canary_overclaim_raises() -> None:
    run = build_b2_run(
        snapshot=_canary_snapshot(),
        measure_b0_paper=False,
        measure_b2_paper_flag=False,
    )
    with pytest.raises(CanaryScopeOverclaimError):
        finalize_b2_verdict(run, requested_verdict="accept")


def test_holdout_lift_stability_rejects_equal_b0_holdout() -> None:
    class _M:
        def __init__(self, ret: float) -> None:
            self.total_return = ret
            self.max_drawdown = 0.1
            self.n_trades_completed = 40

    # Mirrors the live-B0/B1 suspicion pattern: block holdout identical to B0.
    stab = evaluate_holdout_lift_vs_b0(_M(-0.0545), _M(-0.0545))
    assert stab.passed is False
    assert stab.reason == REASON_HOLDOUT_LIFT_UNMET
