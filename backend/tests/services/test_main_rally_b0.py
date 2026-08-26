"""Phase F main_rally F0+F1 — DatasetSnapshot freeze + B0 setup-entry short-horizon."""
from __future__ import annotations

import ast
from datetime import date
from pathlib import Path

import pytest
from services.snapshot_nominal_bind import offline_fixture_bars

from services.main_rally_b0 import (
    BLOCK_ID,
    BOUNDED_SCOPE,
    CANARY_SCOPE,
    REASON_CANARY_SCOPE_ONLY,
    REASON_MEASURED_COVERAGE_INSUFFICIENT,
    REASON_OFFLINE_FIXTURE_NOT_FORMAL,
    REASON_PROTOCOL_READY_EDGE_UNMET,
    STRATEGY_PACKAGE,
    CanaryScopeOverclaimError,
    MainRallyB0Error,
    build_b0_run,
    finalize_b0_verdict,
    is_canary_scope,
    run_b0_scaffold,
)
from services.main_rally_b0_measure import (
    detect_setup_signals,
    eligible_codes_by_signal_day,
    measure_main_rally_b0_paper,
)
from services.main_rally_dataset_snapshot import (
    MAIN_RALLY_SNAPSHOT_RELPATH,
    dataset_snapshot_from_main_rally,
    default_snapshot_path,
    load_frozen_main_rally_snapshot,
)
from services.research_runtime import (
    ExperimentVerdict,
    ResearchObservation,
    assert_snapshot_binding,
    build_experiment_prereg,
    pit_truncate_observations,
    prove_pit_truncation_invariance,
)
from services.rally_detect import is_pivot_low


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


def _bars_for_pivot_setup(
    *,
    n_days: int = 180,
    pivot_idx: int = 130,
    code: str = "600000.SH",
) -> dict[str, list[dict]]:
    """Synthetic nominal panel with one confirmable pivot-low + long base."""

    days = _weekday_compact_days(n_days)
    win = 20
    assert pivot_idx + win < len(days)
    bars: dict[str, list[dict]] = {d: [] for d in days}
    # Flat base around low=10, then dip to make pivot at pivot_idx, confirm at +win.
    for i, day in enumerate(days):
        if i == pivot_idx:
            low = 9.0
            high = 10.2
            close = 9.5
        elif abs(i - pivot_idx) <= win:
            # Surrounding window higher than pivot low.
            low = 10.0
            high = 11.0
            close = 10.5
        else:
            low = 10.0
            high = 10.8
            close = 10.4
        open_px = close
        bars[day].append(
            {
                "ts_code": code,
                "open": open_px,
                "high": high,
                "low": low,
                "close": close,
                "pre_close": 10.0,
                "pct_chg": 1.0 if i == pivot_idx + win else 0.1,
                "vol": 1_000_000.0,
                "amount": 1_000_000.0 * close,
            }
        )
    return offline_fixture_bars(bars)


def _bounded_snapshot(**overrides):
    # Training-window dates only — never spill past declared holdout (20250531).
    date_set = _weekday_compact_days(40, start="20240102")
    base = {
        "snapshot_id": "main_rally_bounded_test",
        "scope": BOUNDED_SCOPE,
        "phase_f_ablation": "bounded_scope_setup_entry_short_horizon",
        "cutover_allowed": True,
        "strategy_package": STRATEGY_PACKAGE,
        "domains": {
            "nominal_ohlcv": {
                "dataset_id": "tier0.market_data.nominal_ohlcv_daily",
                "date_set": date_set,
                "content_hash": "nomhash",
                "config_hash": "nomcfg",
                "accepted": [
                    {
                        "partition": d,
                        "content_hash": f"hash-{d}",
                        "row_count": 1,
                    }
                    for d in date_set
                ],
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
                "partitions": ["20250401", "20250415"],
                "artifact_dir": "data/lineage/tier12_publish_batches",
            },
        },
        "notes": ["test"],
    }
    base.update(overrides)
    return base


def test_pivot_signal_available_at_is_confirmation_not_bottom() -> None:
    """PIT: never enter at bottom_date; signal after +pivot_low_window bars."""

    pivot_idx = 130
    win = 20
    bars = _bars_for_pivot_setup(n_days=180, pivot_idx=pivot_idx)
    days = sorted(bars)
    lows = [float(bars[d][0]["low"]) for d in days]
    assert is_pivot_low(lows, pivot_idx, win)

    signals = detect_setup_signals(bars)
    assert signals, "expected at least one confirmed setup"
    bottom_day = days[pivot_idx]
    confirm_day = days[pivot_idx + win]
    matched = [s for s in signals if s.bottom_date == bottom_day]
    assert matched, f"no signal for bottom={bottom_day}"
    sig = matched[0]
    assert sig.signal_date == confirm_day
    assert sig.available_at == confirm_day
    assert sig.signal_date != sig.bottom_date
    assert sig.available_at != bottom_day


def test_candidate_generator_never_reads_gt_label_tables() -> None:
    """§8.2: label tables must not be read by the candidate generator."""

    measure_path = (
        Path(__file__).resolve().parents[2]
        / "services"
        / "main_rally_b0_measure.py"
    )
    src = measure_path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    assert not any("rally_gt" in name for name in imported)
    assert "fact_rally_ground_truth" not in src
    assert "fact_rally_negative" not in src
    # Function that builds eligible sets must not mention GT tables either.
    assert "eligible_codes_by_signal_day" in src


def test_pit_truncation_invariance_on_setup_observations() -> None:
    bars = _bars_for_pivot_setup(n_days=180, pivot_idx=130)
    signals = detect_setup_signals(bars)
    assert signals
    base_obs = tuple(
        ResearchObservation(
            entity_id=s.ts_code,
            event_date=s.signal_date,
            available_at=s.available_at,
            payload={"bottom_date": s.bottom_date, "base_days": s.base_days},
        )
        for s in signals
    )
    decision = sorted({o.available_at for o in base_obs})[-1]
    future = (
        ResearchObservation(
            entity_id="999999.SH",
            event_date=decision,
            available_at="20990101",
            payload={"bottom_date": decision, "base_days": 99},
        ),
    )
    prove_pit_truncation_invariance(base_obs, future, decision_date=decision)
    kept = pit_truncate_observations(base_obs + future, decision)
    assert all(o.available_at.replace("-", "")[:8] <= decision for o in kept)
    assert not any(o.entity_id == "999999.SH" for o in kept)


def test_eligible_by_day_keys_are_confirmation_dates() -> None:
    bars = _bars_for_pivot_setup(n_days=180, pivot_idx=130)
    eligible = eligible_codes_by_signal_day(bars)
    signals = detect_setup_signals(bars)
    for s in signals:
        assert s.ts_code in (eligible.get(s.signal_date) or set())
        assert s.ts_code not in (eligible.get(s.bottom_date) or set()) or (
            s.signal_date == s.bottom_date
        )


def test_canary_overclaim_raises() -> None:
    snap = _bounded_snapshot(scope=CANARY_SCOPE, phase_f_ablation="canary_only")
    assert is_canary_scope(snap)
    run = build_b0_run(snapshot=snap, measure_coverage=False, measure_paper=False)
    with pytest.raises(CanaryScopeOverclaimError, match=REASON_CANARY_SCOPE_ONLY):
        finalize_b0_verdict(run, requested_verdict="accept")


def test_thin_coverage_inconclusive_claimable_false() -> None:
    snap = _bounded_snapshot()
    # Inject thin nominal date_set into coverage path via fake conn-less override:
    # build with measure_paper False and force coverage via empty bars window.
    thin_day = snap["domains"]["nominal_ohlcv"]["date_set"][0]
    run = build_b0_run(
        snapshot=snap,
        measure_coverage=True,
        measure_paper=False,
        accepted_nominal_partitions=[thin_day],
    )
    assert run.setup_coverage is not None
    assert run.setup_coverage.sufficient_for_measured_b0 is False
    verdict = finalize_b0_verdict(run, requested_verdict="accept")
    assert verdict.verdict == "inconclusive"
    assert verdict.claimable is False
    assert verdict.blocked is True
    assert verdict.reason == REASON_MEASURED_COVERAGE_INSUFFICIENT


def test_measured_b0_on_synthetic_setup_claimable_false() -> None:
    days = _weekday_compact_days(80, start="20240102")
    # Build multi-code panel; inject one confirmable setup on 600000.
    bars = {d: [] for d in days}
    pivot_idx = 50
    win = 20
    for i, day in enumerate(days):
        for j, code in enumerate(
            ["600000.SH", "000001.SZ", "300001.SZ", "688001.SH", "600519.SH"]
        ):
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

    measured = measure_main_rally_b0_paper(bars, days)
    assert measured.walk_forward.one_touch_holdout is True
    assert measured.prereg.signal == "rally_setup_pivot_confirmed_base_days"
    # Short window or thin setups → non-claimable protocol / edge unmet path.
    # Snapshot date_set must include measured days and stay ≤ declared holdout.
    snap = _bounded_snapshot(
        domains={
            "nominal_ohlcv": {
                "dataset_id": "tier0.market_data.nominal_ohlcv_daily",
                "date_set": days,
                "content_hash": "nomhash",
                "config_hash": "nomcfg",
                "accepted": [
                    {
                        "partition": d,
                        "content_hash": f"hash-{d}",
                        "row_count": 1,
                    }
                    for d in days
                ],
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
                "partitions": ["20250401"],
                "artifact_dir": "data/lineage/tier12_publish_batches",
            },
        }
    )
    run = build_b0_run(
        snapshot=snap,
        measure_coverage=True,
        measure_paper=True,
        accepted_nominal_partitions=days,
        bars_by_day=offline_fixture_bars(bars),
    )
    assert run.strategy_package == STRATEGY_PACKAGE
    assert run.block == BLOCK_ID
    assert run.measured_b0 is not None
    verdict = finalize_b0_verdict(run)
    assert isinstance(verdict, ExperimentVerdict)
    assert verdict.claimable is False
    assert verdict.details.get("strategy_release") is False
    assert verdict.verdict in {"reject", "inconclusive"}


def test_dataset_snapshot_adapter_and_binding() -> None:
    payload = _bounded_snapshot()
    snap = dataset_snapshot_from_main_rally(payload)
    assert snap.snapshot_id == payload["snapshot_id"]
    assert snap.universe_id == "traded_on_observation_date"
    assert snap.source_kind == "main_rally_freeze"
    assert snap.inputs
    prereg = build_experiment_prereg(
        snap,
        strategy_package=STRATEGY_PACKAGE,
        block="B0",
        hypothesis="setup_entry_short_horizon",
        register_store=False,
    )
    assert prereg.claimable_target is False
    assert_snapshot_binding(snap, prereg=prereg)


def test_frozen_snapshot_file_adapts_when_present() -> None:
    path = default_snapshot_path()
    if not path.is_file():
        pytest.skip("F0 freeze artifact not written yet")
    assert MAIN_RALLY_SNAPSHOT_RELPATH in str(path)
    payload = load_frozen_main_rally_snapshot(path)
    snap = dataset_snapshot_from_main_rally(payload)
    assert snap.snapshot_id
    assert payload.get("strategy_package") == STRATEGY_PACKAGE
    assert payload.get("cutover_allowed") is True
    dates = [
        "".join(ch for ch in str(d) if ch.isdigit())[:8]
        for d in (payload.get("domains") or {}).get("nominal_ohlcv", {}).get("date_set")
        or ()
    ]
    dates = [d for d in dates if len(d) == 8]
    assert dates
    assert max(dates) < "20250601"
    assert all(not item.dataset_id.startswith("tier3.") for item in snap.inputs)


def test_snapshot_date_set_past_holdout_fails_closed() -> None:
    from services.holdout_guard import HoldoutBoundaryViolation

    spill = _weekday_compact_days(5, start="20250602")
    snap = _bounded_snapshot(
        domains={
            "nominal_ohlcv": {
                "dataset_id": "tier0.market_data.nominal_ohlcv_daily",
                "date_set": spill,
                "content_hash": "nomhash",
                "config_hash": "nomcfg",
                "accepted": [
                    {
                        "partition": d,
                        "content_hash": f"hash-{d}",
                        "row_count": 1,
                    }
                    for d in spill
                ],
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
                "partitions": [],
                "artifact_dir": "data/lineage/tier12_publish_batches",
            },
        }
    )
    with pytest.raises(HoldoutBoundaryViolation, match="actual_data_end"):
        build_b0_run(snapshot=snap, measure_coverage=False, measure_paper=False)


def test_fixture_b0_does_not_consume_formal_single_touch(tmp_path, monkeypatch) -> None:
    """Synthetic fixture measurement cannot create formal holdout evidence."""

    monkeypatch.setattr(
        "services.research_prereg_store.DEFAULT_STORE_DIR",
        tmp_path,
    )
    monkeypatch.setattr(
        "services.research_prereg_store.default_store_dir",
        lambda: tmp_path,
    )
    days = _weekday_compact_days(80, start="20240102")
    bars = {d: [] for d in days}
    pivot_idx = 50
    win = 20
    for i, day in enumerate(days):
        for code in ["600000.SH", "000001.SZ", "300001.SZ", "688001.SH", "600519.SH"]:
            if code == "600000.SH":
                if i == pivot_idx:
                    low, high, close = 9.0, 10.2, 9.5
                elif abs(i - pivot_idx) <= win:
                    low, high, close = 10.0, 11.0, 10.5
                else:
                    low, high, close = 10.0, 10.8, 10.4
            else:
                low, high, close = 10.0, 10.5, 10.2
            bars[day].append(
                {
                    "ts_code": code,
                    "open": close,
                    "high": high,
                    "low": low,
                    "close": close,
                    "pre_close": 10.0,
                    "pct_chg": 1.0 if i == pivot_idx + win and code == "600000.SH" else 0.1,
                    "vol": 1_000_000.0,
                    "amount": 1_000_000.0 * close,
                }
            )
    snap = _bounded_snapshot(
        domains={
            "nominal_ohlcv": {
                "dataset_id": "tier0.market_data.nominal_ohlcv_daily",
                "date_set": days,
                "content_hash": "nomhash",
                "config_hash": "nomcfg",
                "accepted": [
                    {"partition": d, "content_hash": f"hash-{d}", "row_count": 1}
                    for d in days
                ],
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
                "partitions": ["20250401"],
                "artifact_dir": "data/lineage/tier12_publish_batches",
            },
        }
    )
    run = build_b0_run(
        snapshot=snap,
        measure_coverage=True,
        measure_paper=True,
        accepted_nominal_partitions=days,
        bars_by_day=offline_fixture_bars(bars),
    )
    assert run.measured_b0 is not None
    assert run.measured_b0.walk_forward.holdout_dates
    verdict = finalize_b0_verdict(run, requested_verdict="accept")
    assert verdict.verdict == "inconclusive"
    assert verdict.claimable is False
    assert verdict.reason == REASON_OFFLINE_FIXTURE_NOT_FORMAL
    assert run.measurement_source == "offline_fixture"
    assert run.prereg_registered is False
    assert run.holdout_consumed is False
    assert list(tmp_path.iterdir()) == []


def test_formal_b0_rejects_nominal_partition_override() -> None:
    snap = _bounded_snapshot()
    with pytest.raises(MainRallyB0Error, match="override is fixture-only"):
        build_b0_run(
            snapshot=snap,
            measure_coverage=True,
            measure_paper=True,
            accepted_nominal_partitions=["20250401"],
            nominal_conn=object(),
        )
