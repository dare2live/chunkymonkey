"""institution_follow B2 measured market-sensing vs B0 — honest gates."""
from __future__ import annotations

import pytest

from services.institution_follow_b0 import (
    BOUNDED_SCOPE,
    CANARY_ABLATION,
    CANARY_SCOPE,
    CanaryScopeOverclaimError,
    REASON_PROTOCOL_READY_EDGE_UNMET,
    REQUIRED_SURFACE_STATUS,
    build_b0_run,
)
from services.institution_follow_b0_measure import MIN_DAYS_FULL_PURGED_WF
from services.institution_follow_b2 import (
    BLOCK_ID,
    FEATURE_BLOCK_ID,
    REASON_B2_SCAFFOLD_NO_MEASURED_EDGE,
    build_b2_run,
    finalize_b2_verdict,
    run_b2_scaffold,
)
from services.institution_follow_b2_measure import (
    REASON_B2_CONTEXT_COVERAGE_INSUFFICIENT,
    REASON_B2_PULSE_UNTRUSTED,
    SOURCE_PULSE_MART,
    build_context_by_day,
    build_market_context_from_nominal_bars,
    eligible_by_day_from_context,
    measure_b2_paper,
    measure_market_context_coverage,
    refuse_pulse_mart_as_market_context,
)


def _canary_snapshot(**overrides):
    base = {
        "snapshot_id": "disclosure_e0_test_canary_b2",
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
        "snapshot_id": "disclosure_e_bounded_b2",
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


def _weekday_compact_days(n_days: int, *, start: str = "20260401") -> list[str]:
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
    if days is None:
        days = _weekday_compact_days(n_days, start="20260708")
    else:
        days = list(days)[:n_days]
    codes = ["600000.SH", "000001.SZ", "300001.SZ", "688001.SH", "600519.SH"]
    bars: dict[str, list[dict]] = {}
    for i, day in enumerate(days):
        rows = []
        for j, code in enumerate(codes):
            # Alternate risk-on (more +pct) vs risk-off (more -pct) by day.
            if i % 2 == 0:
                pct = float((j + 1) * 0.5)  # all positive → risk_on
            else:
                pct = float(-(j + 1) * 0.5)  # all negative → risk_off
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
    assert run.feature_block.as_dict()["b_pit_cutover_allowed"] is False


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
    assert ctx.population_kind.endswith("shadow")


def test_b2_pulse_source_inconclusive_not_silent_fallback() -> None:
    days = _weekday_compact_days(MIN_DAYS_FULL_PURGED_WF)
    bars = _synthetic_window_bars(len(days), days=days)
    b0 = build_b0_run(
        snapshot=_bounded_snapshot(),
        nominal_conn=_FakeNominalConn(days),
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


def test_b2_coverage_insufficient_is_inconclusive() -> None:
    days = _weekday_compact_days(MIN_DAYS_FULL_PURGED_WF)
    bars = _synthetic_window_bars(len(days), days=days)
    # Only one READY day → coverage fails.
    thin_ctx = build_context_by_day(
        {days[0]: bars[days[0]]}, [days[0]]
    )
    cov = measure_market_context_coverage(days, thin_ctx)
    assert cov.sufficient is False
    assert cov.reason == REASON_B2_CONTEXT_COVERAGE_INSUFFICIENT

    b0 = build_b0_run(
        snapshot=_bounded_snapshot(),
        nominal_conn=_FakeNominalConn(days),
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
    assert verdict.verdict == "inconclusive"
    assert verdict.claimable is False
    assert verdict.reason == REASON_B2_CONTEXT_COVERAGE_INSUFFICIENT


def test_b2_measured_vs_b0_reports_delta_and_rejects_on_edge_gates() -> None:
    days = _weekday_compact_days(MIN_DAYS_FULL_PURGED_WF)
    bars = _synthetic_window_bars(len(days), days=days)
    ctx = build_context_by_day(bars, days)
    assert measure_market_context_coverage(days, ctx).sufficient is True
    eligible = eligible_by_day_from_context(bars, ctx)
    # Risk-off days must be empty.
    assert any(len(v) == 0 for v in eligible.values())
    assert any(len(v) > 0 for v in eligible.values())

    b0 = build_b0_run(
        snapshot=_bounded_snapshot(),
        nominal_conn=_FakeNominalConn(days),
        bars_by_day=bars,
    )
    assert b0.measured_b0 is not None
    assert b0.measured_b0.claimable is True

    measured = measure_b2_paper(
        bars, b0_measured=b0.measured_b0, context_by_day=ctx
    )
    assert measured.coverage.sufficient is True
    assert measured.measured is not None
    assert measured.delta is not None

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
    assert verdict.verdict in {"reject", "inconclusive"}
    if measured.claimable:
        assert verdict.verdict == "reject"
        assert verdict.reason == REASON_PROTOCOL_READY_EDGE_UNMET
    assert verdict.details["delta_b2_minus_b0"] is not None
    assert verdict.details["b_pit_cutover_allowed"] is False


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
