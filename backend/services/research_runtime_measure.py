"""Phase D runtime-owned offline measured path (not a strategy package).

Produces ExperimentVerdict with claimable=false from PIT-truncated observations
carrying explicit T+1/T+2 nominal open legs. No strategy-package imports.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence
from uuid import uuid4

from services.research_runtime import (
    REASON_PHASE_D_OFFLINE_MEASURED,
    REASON_PIT_FUTURE_AVAILABLE_AT,
    REASON_PIT_LEAK_OR_EMPTY,
    REASON_SNAPSHOT_BINDING_VIOLATED,
    DatasetSnapshot,
    ExperimentPrereg,
    ExperimentRun,
    ExperimentVerdict,
    FoldEmbargoHooks,
    OfflineMeasureResult,
    ResearchObservation,
    ResearchRuntimeError,
    _compact_day,
    pit_truncate_observations,
)
from services.research_runtime_loop import (
    assert_snapshot_binding,
    build_experiment_prereg,
)

# Runtime-owned cost model (mirrors strategy_validation nominal T+1 paper costs).
_OFFLINE_COMMISSION_RATE = 0.00025
_OFFLINE_STAMP_TAX_RATE = 0.001
_OFFLINE_SLIPPAGE_RATE = 0.0005


def _parse_px(value: Any) -> float | None:
    try:
        px = float(value)
    except (TypeError, ValueError):
        return None
    if px <= 0.0:
        return None
    return px


def _net_return_nominal(entry_px: float, exit_px: float) -> tuple[float, float]:
    """Gross + net open-to-open return with buy/sell costs (runtime-owned)."""

    buy_cost = _OFFLINE_COMMISSION_RATE + _OFFLINE_SLIPPAGE_RATE
    sell_cost = (
        _OFFLINE_COMMISSION_RATE + _OFFLINE_STAMP_TAX_RATE + _OFFLINE_SLIPPAGE_RATE
    )
    gross = exit_px / entry_px - 1.0
    net = (exit_px * (1.0 - sell_cost)) / (entry_px * (1.0 + buy_cost)) - 1.0
    return gross, net


def _compound_metrics(
    fills: Sequence[Mapping[str, Any]],
) -> tuple[float | None, float | None, int, int]:
    """Equal-notional daily bags → compound total_return + max_drawdown."""

    completed = [f for f in fills if f.get("status") == "filled"]
    unfilled = sum(1 for f in fills if f.get("status") != "filled")
    if not completed:
        return None, None, 0, unfilled
    by_entry: dict[str, list[float]] = {}
    for f in completed:
        entry_day = str(f.get("entry_date") or f.get("signal_date") or "")
        by_entry.setdefault(entry_day, []).append(float(f["net_return"]))
    daily = [sum(v) / len(v) for _, v in sorted(by_entry.items())]
    nav = 1.0
    peak = 1.0
    max_dd = 0.0
    for r in daily:
        nav *= 1.0 + r
        peak = max(peak, nav)
        dd = (peak - nav) / peak if peak > 0 else 0.0
        max_dd = max(max_dd, dd)
    return nav - 1.0, max_dd, len(completed), unfilled


def measure_observations_offline(
    snapshot: DatasetSnapshot,
    observations: Sequence[ResearchObservation],
    *,
    decision_date: str,
    prereg: ExperimentPrereg,
) -> OfflineMeasureResult:
    """PIT-truncate then measure nominal open-to-open fills from payloads.

    Observations must carry ``entry_px`` / ``exit_px`` (T+1 / T+2 nominal opens).
    Missing/non-positive prices → unfilled (unknown, not 0). Runtime-owned —
    does not call strategy-package measure harnesses.
    """

    assert_snapshot_binding(snapshot, prereg=prereg, decision_date=decision_date)
    day = _compact_day(decision_date)
    kept = pit_truncate_observations(observations, day)
    if not kept:
        return OfflineMeasureResult(
            status="empty_after_pit",
            decision_date=day,
            kept_observation_count=0,
            input_observation_count=len(tuple(observations)),
            details={
                "strategy_release": False,
                "optuna": False,
                "snapshot_id": snapshot.snapshot_id,
                "universe_id": snapshot.universe_id,
            },
            total_return=None,
            max_drawdown=None,
            paper_fills="measured",
            n_trades_completed=0,
            n_unfilled=0,
        )

    fills: list[dict[str, Any]] = []
    for obs in kept:
        payload = dict(obs.payload or {})
        entry_px = _parse_px(payload.get("entry_px"))
        exit_px = _parse_px(payload.get("exit_px"))
        entry_date = _compact_day(payload.get("entry_date") or obs.event_date)
        fold_role = str(payload.get("fold_role") or "undeclared")
        if entry_px is None or exit_px is None:
            fills.append(
                {
                    "entity_id": obs.entity_id,
                    "signal_date": _compact_day(obs.event_date),
                    "entry_date": entry_date,
                    "fold_role": fold_role,
                    "status": "unfilled",
                    "reason": "missing_or_non_positive_px",
                    "entry_px": entry_px,
                    "exit_px": exit_px,
                    "gross_return": None,
                    "net_return": None,
                }
            )
            continue
        gross, net = _net_return_nominal(entry_px, exit_px)
        fills.append(
            {
                "entity_id": obs.entity_id,
                "signal_date": _compact_day(obs.event_date),
                "entry_date": entry_date,
                "fold_role": fold_role,
                "status": "filled",
                "reason": "ok",
                "entry_px": entry_px,
                "exit_px": exit_px,
                "gross_return": gross,
                "net_return": net,
            }
        )

    total_return, max_dd, n_completed, n_unfilled = _compound_metrics(fills)
    return OfflineMeasureResult(
        status="measured",
        decision_date=day,
        kept_observation_count=len(kept),
        input_observation_count=len(tuple(observations)),
        details={
            "strategy_release": False,
            "optuna": False,
            "snapshot_id": snapshot.snapshot_id,
            "universe_id": snapshot.universe_id,
            "prereg_primary_metric": prereg.primary_metric,
            "fold_embargo": prereg.fold_embargo.as_dict(),
            "cost_model": {
                "commission_rate": _OFFLINE_COMMISSION_RATE,
                "stamp_tax_rate": _OFFLINE_STAMP_TAX_RATE,
                "slippage_rate": _OFFLINE_SLIPPAGE_RATE,
                "entry": "payload_entry_px_as_t1_nominal_open",
                "exit": "payload_exit_px_as_t2_nominal_open",
            },
            "fills": fills,
        },
        total_return=total_return,
        max_drawdown=max_dd,
        paper_fills="measured",
        n_trades_completed=n_completed,
        n_unfilled=n_unfilled,
    )


def run_offline_measured_loop(
    snapshot: DatasetSnapshot,
    observations: Sequence[ResearchObservation],
    *,
    decision_date: str,
    block: str = "B0",
    strategy_package: str = "phase_d_offline",
    hypothesis: str = "offline_measured_nominal_fills_no_edge_claim",
    fold_embargo: FoldEmbargoHooks | None = None,
    require_kept_rows: bool = False,
    observed_universe_id: str | None = None,
) -> tuple[ExperimentRun, ExperimentVerdict]:
    """End-to-end offline measured path: prereg → bind → measure → ExperimentVerdict.

    Owned by ``research_runtime`` (not a strategy package). Always
    ``claimable=false``. Does not emit StrategyRelease or run Optuna.
    """

    prereg = build_experiment_prereg(
        snapshot,
        strategy_package=strategy_package,
        block=block,
        hypothesis=hypothesis,
        fold_embargo=fold_embargo,
    )
    experiment_id = (
        f"{strategy_package}:{block}:{snapshot.snapshot_id}:meas:{uuid4().hex[:12]}"
    )

    try:
        assert_snapshot_binding(
            snapshot,
            prereg=prereg,
            decision_date=decision_date,
            observed_universe_id=observed_universe_id,
        )
        measured = measure_observations_offline(
            snapshot,
            observations,
            decision_date=decision_date,
            prereg=prereg,
        )
        assert_snapshot_binding(snapshot, prereg=prereg, decision_date=decision_date)
    except ResearchRuntimeError as exc:
        run = ExperimentRun(
            experiment_id=experiment_id,
            strategy_package=strategy_package,
            block=block,
            snapshot_id=snapshot.snapshot_id,
            snapshot_content_hash=snapshot.content_hash,
            config_hash=snapshot.config_hash,
            universe_id=snapshot.universe_id,
            decision_date=_compact_day(decision_date),
            kept_observation_count=0,
            pit_ok=False,
            prereg=prereg,
            artifact_manifest={
                "kind": "phase_d_offline_measured",
                "research_runtime_snapshot": snapshot.boundary_dict(),
                "prereg": prereg.as_dict(),
                "strategy_release": False,
                "error": str(exc)[:300],
            },
            notes=("snapshot_binding_fail_closed", "no_strategy_release", "no_optuna"),
        )
        verdict = ExperimentVerdict(
            verdict="reject",
            reason=REASON_SNAPSHOT_BINDING_VIOLATED,
            blocked=True,
            experiment_id=experiment_id,
            block=block,
            claimable=False,
            details={
                "strategy_release": False,
                "optuna": False,
                "error": str(exc)[:300],
                "prereg": prereg.as_dict(),
            },
        )
        return run, verdict
    except ValueError as exc:
        run = ExperimentRun(
            experiment_id=experiment_id,
            strategy_package=strategy_package,
            block=block,
            snapshot_id=snapshot.snapshot_id,
            snapshot_content_hash=snapshot.content_hash,
            config_hash=snapshot.config_hash,
            universe_id=snapshot.universe_id,
            decision_date=_compact_day(decision_date),
            kept_observation_count=0,
            pit_ok=False,
            prereg=prereg,
            artifact_manifest={
                "kind": "phase_d_offline_measured",
                "research_runtime_snapshot": snapshot.boundary_dict(),
                "prereg": prereg.as_dict(),
                "strategy_release": False,
                "error": str(exc)[:300],
            },
            notes=("pit_fail_closed", "no_strategy_release", "no_optuna"),
        )
        verdict = ExperimentVerdict(
            verdict="reject",
            reason=REASON_PIT_FUTURE_AVAILABLE_AT,
            blocked=True,
            experiment_id=experiment_id,
            block=block,
            claimable=False,
            details={
                "strategy_release": False,
                "optuna": False,
                "error": str(exc)[:300],
            },
        )
        return run, verdict

    pit_ok = True
    if require_kept_rows and measured.kept_observation_count == 0:
        pit_ok = False
    if measured.status == "empty_after_pit" and require_kept_rows:
        pit_ok = False

    run = ExperimentRun(
        experiment_id=experiment_id,
        strategy_package=strategy_package,
        block=block,
        snapshot_id=snapshot.snapshot_id,
        snapshot_content_hash=snapshot.content_hash,
        config_hash=snapshot.config_hash,
        universe_id=snapshot.universe_id,
        decision_date=measured.decision_date,
        kept_observation_count=measured.kept_observation_count,
        pit_ok=pit_ok,
        prereg=prereg,
        artifact_manifest={
            "kind": "phase_d_offline_measured",
            "research_runtime_snapshot": snapshot.boundary_dict(),
            "prereg": prereg.as_dict(),
            "measure": measured.as_dict(),
            "strategy_release": False,
            "optuna": False,
        },
        notes=(
            "phase_d_offline_measured_loop",
            "prereg_before_measure",
            "runtime_owned_not_strategy_package",
            "no_strategy_release",
            "no_optuna",
            "claimable_false",
        ),
    )
    if not pit_ok:
        verdict = ExperimentVerdict(
            verdict="reject",
            reason=REASON_PIT_LEAK_OR_EMPTY,
            blocked=True,
            experiment_id=experiment_id,
            block=block,
            claimable=False,
            details={
                "strategy_release": False,
                "optuna": False,
                "measure": measured.as_dict(),
                "prereg": prereg.as_dict(),
            },
        )
        return run, verdict

    verdict = ExperimentVerdict(
        verdict="inconclusive",
        reason=REASON_PHASE_D_OFFLINE_MEASURED,
        blocked=False,
        experiment_id=experiment_id,
        block=block,
        claimable=False,
        details={
            "strategy_release": False,
            "optuna": False,
            "measure": measured.as_dict(),
            "prereg": prereg.as_dict(),
            "snapshot_id": snapshot.snapshot_id,
            "universe_id": snapshot.universe_id,
            "config_hash": snapshot.config_hash,
            "available_at_lower": snapshot.available_at_lower,
            "available_at_upper": snapshot.available_at_upper,
            "runtime_owned": True,
        },
    )
    return run, verdict


__all__ = [
    "measure_observations_offline",
    "run_offline_measured_loop",
]
