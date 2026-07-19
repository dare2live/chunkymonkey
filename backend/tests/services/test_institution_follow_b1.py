"""institution_follow B1 measured stock-state vs B0 — honest gates."""
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
from services.institution_follow_b1 import (
    BLOCK_ID,
    FEATURE_BLOCK_ID,
    REASON_B1_SCAFFOLD_NO_MEASURED_EDGE,
    build_b1_run,
    finalize_b1_verdict,
    run_b1_scaffold,
)
from services.institution_follow_b1_measure import (
    REASON_B1_STATE_COVERAGE_INSUFFICIENT,
    eligible_by_day_from_state,
    measure_b1_paper,
    measure_stock_state_coverage,
    state_row_eligible,
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


def _full_state_for_bars(
    bars: dict[str, list[dict]],
    *,
    trend: str = "up",
    breakout: bool = False,
) -> dict[str, dict[str, dict]]:
    out: dict[str, dict[str, dict]] = {}
    for day, rows in bars.items():
        out[day] = {
            str(r["ts_code"]).split(".", 1)[0]: {
                "axis_trend": trend,
                "axis_pos": "mid",
                "form_name": "test",
                "is_breakout_event": breakout,
            }
            for r in rows
        }
    return out


def test_b1_declares_stock_state_feature_block() -> None:
    run = build_b1_run(
        snapshot=_bounded_snapshot(),
        measure_b0_paper=False,
        measure_b1_paper_flag=False,
    )
    assert run.block == BLOCK_ID
    assert run.feature_block.block_id == FEATURE_BLOCK_ID
    assert run.feature_block.status == "declared_scaffold"
    assert run.surface_status == REQUIRED_SURFACE_STATUS
    assert "b1_stock_state_block" in run.notes


def test_state_row_eligible_trend_or_breakout() -> None:
    assert state_row_eligible({"axis_trend": "up", "is_breakout_event": False})
    assert state_row_eligible({"axis_trend": "down", "is_breakout_event": True})
    assert not state_row_eligible(
        {"axis_trend": "down", "is_breakout_event": False}
    )


def test_b1_coverage_insufficient_is_inconclusive_not_fake_improve() -> None:
    days = _weekday_compact_days(MIN_DAYS_FULL_PURGED_WF)
    bars = _synthetic_window_bars(len(days), days=days)
    # Only 1 day of state → coverage fails.
    thin_state = {days[0]: _full_state_for_bars({days[0]: bars[days[0]]})[days[0]]}
    cov = measure_stock_state_coverage(days, bars, thin_state)
    assert cov.sufficient is False

    b0 = build_b0_run(
        snapshot=_bounded_snapshot(),
        nominal_conn=_FakeNominalConn(days),
        bars_by_day=bars,
    )
    run, verdict = run_b1_scaffold(
        snapshot=_bounded_snapshot(),
        b0_run=b0,
        measure_b0_paper=False,
        bars_by_day=bars,
        state_by_day=thin_state,
        requested_verdict="accept",
    )
    assert run.measured_b1 is not None
    assert run.measured_b1.measured is None
    assert verdict.verdict == "inconclusive"
    assert verdict.claimable is False
    assert verdict.reason == REASON_B1_STATE_COVERAGE_INSUFFICIENT
    assert verdict.details["metrics"] == "state_coverage_insufficient"


def test_b1_measured_vs_b0_reports_delta_and_rejects_on_edge_gates() -> None:
    days = _weekday_compact_days(MIN_DAYS_FULL_PURGED_WF)
    bars = _synthetic_window_bars(len(days), days=days)
    # Only allow mid-rank names via state so B1 differs from B0 selection.
    state: dict[str, dict[str, dict]] = {}
    for day, rows in bars.items():
        state[day] = {}
        for r in rows:
            code6 = str(r["ts_code"]).split(".", 1)[0]
            # Keep 000001 / 300001 eligible; exclude strongest momentum names.
            eligible = code6 in {"000001", "300001"}
            state[day][code6] = {
                "axis_trend": "up" if eligible else "down",
                "axis_pos": "mid",
                "form_name": "test",
                "is_breakout_event": False,
            }
    assert eligible_by_day_from_state(state)[days[0]]

    b0 = build_b0_run(
        snapshot=_bounded_snapshot(),
        nominal_conn=_FakeNominalConn(days),
        bars_by_day=bars,
    )
    assert b0.measured_b0 is not None
    assert b0.measured_b0.claimable is True

    measured = measure_b1_paper(
        bars, b0_measured=b0.measured_b0, state_by_day=state
    )
    assert measured.coverage.sufficient is True
    assert measured.measured is not None
    assert measured.delta is not None
    assert "total_return" in measured.delta.as_dict()

    run, verdict = run_b1_scaffold(
        snapshot=_bounded_snapshot(),
        b0_run=b0,
        measure_b0_paper=False,
        bars_by_day=bars,
        state_by_day=state,
        requested_verdict="accept",
    )
    assert run.feature_block.status == "measured_conditioned"
    assert run.artifact_manifest["paper_fills"] == "measured"
    assert verdict.claimable is False
    # Synthetic panel does not meet accept edge gates.
    assert verdict.verdict in {"reject", "inconclusive"}
    if measured.claimable:
        assert verdict.verdict == "reject"
        assert verdict.reason == REASON_PROTOCOL_READY_EDGE_UNMET
    assert verdict.details["delta_b1_minus_b0"] is not None
    assert verdict.details["b0_metrics"] is not None
    assert verdict.details["metrics"]["n_trades_completed"] >= 0


def test_b1_scaffold_without_measure_never_accepts() -> None:
    run, verdict = run_b1_scaffold(
        snapshot=_bounded_snapshot(),
        requested_verdict="accept",
        measure_b0_paper=False,
        measure_b1_paper_flag=False,
    )
    assert verdict.verdict == "inconclusive"
    assert verdict.claimable is False
    assert verdict.reason == REASON_B1_SCAFFOLD_NO_MEASURED_EDGE


def test_b1_canary_overclaim_raises() -> None:
    run = build_b1_run(
        snapshot=_canary_snapshot(),
        measure_b0_paper=False,
        measure_b1_paper_flag=False,
    )
    with pytest.raises(CanaryScopeOverclaimError):
        finalize_b1_verdict(run, requested_verdict="accept")
