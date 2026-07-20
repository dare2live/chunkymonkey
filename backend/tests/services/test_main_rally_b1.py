"""main_rally B1 measured stock-state conditioning vs B0 (Phase F / F2)."""
from __future__ import annotations

import ast
from datetime import date
from pathlib import Path

import pytest

from services.institution_follow_edge_gates import (
    REASON_HOLDOUT_LIFT_UNMET,
    evaluate_holdout_lift_vs_b0,
)
from services.main_rally_b0 import (
    BOUNDED_SCOPE,
    CANARY_SCOPE,
    CanaryScopeOverclaimError,
    REASON_PROTOCOL_READY_EDGE_UNMET,
    REQUIRED_SURFACE_STATUS,
    STRATEGY_PACKAGE,
    build_b0_run,
)
from services.main_rally_b1 import (
    BLOCK_ID,
    FEATURE_BLOCK_ID,
    REASON_B1_SCAFFOLD_NO_MEASURED_EDGE,
    build_b1_run,
    finalize_b1_verdict,
    run_b1_scaffold,
)
from services.main_rally_b0_measure import eligible_codes_by_signal_day
from services.main_rally_b1_measure import (
    REASON_B1_STATE_COVERAGE_INSUFFICIENT,
    eligible_by_day_from_state,
    measure_b1_paper,
    measure_stock_state_coverage,
    state_row_eligible,
)


def _weekday_compact_days(n_days: int, *, start: str = "20260102") -> list[str]:
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
        "snapshot_id": "main_rally_bounded_test_b1",
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
                "partitions": ["20260717", "20260720"],
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

    ``pivot_idx`` must clear ``warmup_bars`` (60) and leave room for
    ``pivot_low_window`` (20) confirmation + ``base_lookback_days`` (120)
    before it — mirrors the known-good fixture in test_main_rally_b0.py's
    ``test_pivot_signal_available_at_is_confirmation_not_bottom``.
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
    assert run.artifact_manifest["strategy_release"] is False
    assert run.artifact_manifest["optuna"] is False


def test_state_row_eligible_trend_or_breakout() -> None:
    assert state_row_eligible({"axis_trend": "up", "is_breakout_event": False})
    assert state_row_eligible({"axis_trend": "down", "is_breakout_event": True})
    assert not state_row_eligible(
        {"axis_trend": "down", "is_breakout_event": False}
    )


def test_candidate_generator_never_reads_gt_label_tables() -> None:
    """§8.2: B1 measure module must not read rally GT/negative label tables."""

    for name in ("main_rally_b1_measure.py", "main_rally_b1.py"):
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


def test_b1_coverage_insufficient_is_inconclusive_not_fake_improve() -> None:
    bars = _bars_with_setups()
    days = sorted(bars)
    # Only 1 day of state → coverage fails.
    thin_state = {days[0]: _full_state_for_bars({days[0]: bars[days[0]]})[days[0]]}
    cov = measure_stock_state_coverage(days, bars, thin_state)
    assert cov.sufficient is False

    b0 = build_b0_run(
        snapshot=_bounded_snapshot(),
        accepted_nominal_partitions=days,
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
    assert verdict.details["strategy_release"] is False


def test_eligible_intersection_restricts_to_setup_and_state() -> None:
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

    full_state = _full_state_for_bars(bars, trend="up")
    measured = measure_b1_paper(bars, b0_measured=b0.measured_b0, state_by_day=full_state)
    assert measured.coverage.sufficient is True
    # Full-eligible state must not add codes beyond B0's own setup-detector
    # eligibles for the day (B1 only ever narrows B0, never widens it).
    for day, codes in measured.eligible_by_day.items():
        assert set(codes).issubset(setup_eligible.get(day) or set())

    # Excluding 600000 (the only pivot-confirmed name) from state must
    # collapse B1 eligibility on its signal days to empty.
    excl_state = _full_state_for_bars(bars, trend="down")
    measured_excl = measure_b1_paper(bars, b0_measured=b0.measured_b0, state_by_day=excl_state)
    assert all(not codes for codes in measured_excl.eligible_by_day.values())


def test_b1_measured_vs_b0_reports_delta_and_rejects_on_edge_gates() -> None:
    bars = _bars_with_setups()
    days = sorted(bars)
    full_state = _full_state_for_bars(bars, trend="up")

    b0 = build_b0_run(
        snapshot=_bounded_snapshot(),
        accepted_nominal_partitions=days,
        bars_by_day=bars,
    )
    assert b0.measured_b0 is not None

    measured = measure_b1_paper(bars, b0_measured=b0.measured_b0, state_by_day=full_state)
    assert measured.coverage.sufficient is True
    assert measured.measured is not None
    assert measured.delta is not None
    assert "total_return" in measured.delta.as_dict()

    run, verdict = run_b1_scaffold(
        snapshot=_bounded_snapshot(),
        b0_run=b0,
        measure_b0_paper=False,
        bars_by_day=bars,
        state_by_day=full_state,
        requested_verdict="accept",
    )
    assert run.feature_block.status == "measured_conditioned"
    assert run.artifact_manifest["paper_fills"] == "measured"
    assert verdict.claimable is False
    assert verdict.details["strategy_release"] is False
    # Synthetic panel does not clear accept edge gates / holdout lift.
    assert verdict.verdict in {"reject", "inconclusive"}
    if measured.claimable:
        assert verdict.verdict == "reject"
        assert verdict.reason in {
            REASON_PROTOCOL_READY_EDGE_UNMET,
            REASON_HOLDOUT_LIFT_UNMET,
        }
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


def test_holdout_lift_stability_rejects_equal_b0_holdout() -> None:
    class _M:
        def __init__(self, ret: float) -> None:
            self.total_return = ret
            self.max_drawdown = 0.1
            self.n_trades_completed = 40

    # Mirrors the live-B0 suspicion pattern: block holdout identical to B0.
    stab = evaluate_holdout_lift_vs_b0(_M(-0.0545), _M(-0.0545))
    assert stab.passed is False
    assert stab.reason == REASON_HOLDOUT_LIFT_UNMET
