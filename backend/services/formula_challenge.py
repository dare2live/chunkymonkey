"""Namespaced BestChoice formula challenge smoke (S3, not Phase G).

Loads frozen formula_engine.py via importlib without writing bytecode into
bestchoice/. Does not copy the engine into backend/, does not read Optuna
adoption CSV, and does not use vwap_tradable_v1.
"""
from __future__ import annotations

import hashlib
from datetime import date, timedelta
from itertools import combinations
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np

from services.one_name_pointer_bars import (
    OneNamePointerError,
    assert_replay_before_holdout as _assert_replay_before_holdout,
    load_one_name_pointer_bars as _load_one_name_pointer_bars,
    nominal_days_from_snapshot as _nominal_days_from_snapshot,
)
from services.snapshot_nominal_bind import (
    SnapshotNominalBindError,
    offline_fixture_bars,
    require_offline_fixture_bars,
)
from services.strategy_paper import SignalEvent, simulate_signal_hold_paper
from services.strategy_spec import (
    CHALLENGER_PNL_SOURCE,
    FORMULA_ENGINE_SHA256,
    FROZEN_FORMULA_IDS,
    REPO,
    StrategySpec,
    StrategySpecError,
    load_source_module,
    load_strategy_spec,
    verify_frozen_challenger,
)


CHALLENGER_CODE = "000001.SZ"
FROZEN_VECTORS = {
    "gs_pullback_confirm": (
        3,
        35,
        "b5212a23aba3be4481bd3b98f0a24198f22d5d69e130604757b559bf9b07b6d2",
    ),
    "gs_raw_buy": (
        36,
        35,
        "abf3d794547961b4240e5d687d5bd989d05bfb55fa1807238d2e591f1fcea512",
    ),
    "ma_base_breakout": (
        2,
        446,
        "1f164411cab00f41857348fce6e96c2bc185e0e13392a6266beac812692936ea",
    ),
    "activity_breakout": (
        195,
        86,
        "4c19463a7c12d5f7024a8e5c0b48d0e0c0d7f3206c3f3315ed080259531526e6",
    ),
    "volume_base_breakout": (
        63,
        465,
        "ad8a18a0f7513e207bc2ed93d69fd5470713142c34cbfda174978f40efe5d9a7",
    ),
}

_ENGINE: Any | None = None


class FormulaChallengeError(RuntimeError):
    """Formula challenge refused a leak, unknown id, or legacy paper source."""


def refuse_legacy_paper_source(source: str | Path) -> None:
    text = str(source).lower()
    if (
        "formula_local_optuna_batch_adoption.csv" in text
        or "execution_model" in text
        or "vwap_tradable_v1" in text
        or "optuna" in text
    ):
        raise FormulaChallengeError("legacy_optuna_or_vwap_is_not_paper")


def load_formula_engine(*, repo: Path | str | None = None) -> Any:
    """Import frozen formula_engine after hash + evidence verification."""

    global _ENGINE
    root = Path(repo) if repo else REPO
    if _ENGINE is not None and repo is None:
        return _ENGINE
    verify_frozen_challenger(repo=root)
    engine_path = root / "bestchoice" / "formula_engine.py"
    if not engine_path.is_file():
        raise FormulaChallengeError("missing_formula_engine")
    digest = hashlib.sha256(engine_path.read_bytes()).hexdigest()
    if digest != FORMULA_ENGINE_SHA256:
        raise FormulaChallengeError("formula_engine_sha256_mismatch")
    try:
        module = load_source_module("bestchoice_formula_engine", engine_path)
    except StrategySpecError as exc:
        raise FormulaChallengeError("formula_engine_unimportable") from exc
    if repo is None:
        _ENGINE = module
    return module


def frozen_ohlcv_fixture() -> dict[str, np.ndarray]:
    """Same 900-bar fixture as bestchoice/scripts/formula_engine_smoke.py."""

    rng = np.random.default_rng(10)
    size = 900
    close = 20.0 * np.exp(np.cumsum(rng.normal(0.0005, 0.025, size)))
    open_ = close * np.exp(rng.normal(0.0, 0.008, size))
    high = np.maximum(open_, close) * (1.0 + rng.uniform(0.002, 0.04, size))
    low = np.minimum(open_, close) * (1.0 - rng.uniform(0.002, 0.04, size))
    volume = np.exp(rng.normal(np.log(1_000_000.0), 0.65, size))
    return {
        "open_": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
        "amount": close * volume * 100.0,
    }


def synthetic_trading_days(count: int, *, start: str = "20200102") -> tuple[str, ...]:
    cursor = date(int(start[:4]), int(start[4:6]), int(start[6:8]))
    days: list[str] = []
    while len(days) < count:
        if cursor.weekday() < 5:
            days.append(cursor.strftime("%Y%m%d"))
        cursor += timedelta(days=1)
    return tuple(days)


def fixture_bars(
    fixture: Mapping[str, np.ndarray] | None = None,
    *,
    ts_code: str = CHALLENGER_CODE,
) -> tuple[dict[str, list[dict[str, object]]], tuple[str, ...]]:
    arrays = dict(fixture or frozen_ohlcv_fixture())
    n = len(arrays["close"])
    days = synthetic_trading_days(n)
    bars: dict[str, list[dict[str, object]]] = {}
    prev_close = float(arrays["close"][0])
    for i, day in enumerate(days):
        open_px = float(arrays["open_"][i])
        close_px = float(arrays["close"][i])
        bars[day] = [
            {
                "ts_code": ts_code,
                "open": open_px,
                "high": float(arrays["high"][i]),
                "low": float(arrays["low"][i]),
                "close": close_px,
                "vol": float(arrays["volume"][i]),
                "pre_close": prev_close,
            }
        ]
        prev_close = close_px
    return bars, days


def compute_formula_vectors(
    formula_id: str,
    arrays: Mapping[str, np.ndarray],
    *,
    repo: Path | str | None = None,
) -> dict[str, np.ndarray]:
    if formula_id not in FROZEN_FORMULA_IDS:
        raise FormulaChallengeError(f"unknown_formula_id:{formula_id}")
    engine = load_formula_engine(repo=repo)
    result = engine.compute_formula_signals(formula_id, **dict(arrays))
    return {
        "entry": np.asarray(result["entry"], dtype=np.bool_),
        "exit": np.asarray(result["exit"], dtype=np.bool_),
    }


def vector_fingerprint(entry: np.ndarray, exit_: np.ndarray) -> tuple[int, int, str]:
    digest = hashlib.sha256(
        np.packbits(np.concatenate((entry, exit_))).tobytes()
    ).hexdigest()
    return int(entry.sum()), int(exit_.sum()), digest


def signals_to_events(
    *,
    ts_code: str,
    days: Sequence[str],
    entry: np.ndarray,
    exit_: np.ndarray,
) -> tuple[SignalEvent, ...]:
    if len(days) != len(entry) or len(days) != len(exit_):
        raise FormulaChallengeError("signal_length_mismatch")
    events: list[SignalEvent] = []
    for i, day in enumerate(days):
        if bool(entry[i]):
            events.append(SignalEvent(ts_code, day, "entry"))
        if bool(exit_[i]):
            events.append(SignalEvent(ts_code, day, "exit"))
    return tuple(events)


UNIVERSE_FREEZE_DAY_COUNT = 1553


def _compact_day(value: object) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())[:8]


def _raise_pointer(exc: OneNamePointerError) -> None:
    raise FormulaChallengeError(str(exc)) from exc


def assert_replay_before_holdout(
    days: Sequence[str],
    *,
    holdout_start: str | None = None,
) -> None:
    try:
        _assert_replay_before_holdout(days, holdout_start=holdout_start)
    except OneNamePointerError as exc:
        _raise_pointer(exc)


def refuse_universe_freeze_replay(*, n_codes: int, n_days: int) -> None:
    if int(n_codes) > 1 and int(n_days) >= UNIVERSE_FREEZE_DAY_COUNT:
        raise FormulaChallengeError("universe_freeze_replay_not_this_knife")


def _typed_named_bars(
    bars_by_day: Mapping[str, Sequence[Mapping[str, object]]],
) -> dict[str, list[Any]]:
    try:
        return require_offline_fixture_bars(bars_by_day)
    except SnapshotNominalBindError as exc:
        raise FormulaChallengeError("named_bars_must_be_offline_fixture") from exc


def nominal_days_from_snapshot(snapshot: Mapping[str, Any]) -> tuple[str, ...]:
    try:
        return _nominal_days_from_snapshot(snapshot)
    except OneNamePointerError as exc:
        _raise_pointer(exc)
        raise


def simulate_formula_on_snapshot(
    formula_id: str,
    snapshot: Mapping[str, Any],
    bars_by_day: Mapping[str, Sequence[Mapping[str, object]]],
    *,
    ts_code: str = CHALLENGER_CODE,
    spec: StrategySpec | None = None,
    holdout_start: str | None = None,
    repo: Path | str | None = None,
):
    """Replay one name on snapshot day membership. Not live generation bind."""

    frozen_days = set(nominal_days_from_snapshot(snapshot))
    bars = _typed_named_bars(bars_by_day)
    extra = {
        _compact_day(day)
        for day in bars
        if len(_compact_day(day)) == 8 and _compact_day(day) not in frozen_days
    }
    if extra:
        raise FormulaChallengeError("bars_not_in_snapshot")
    return simulate_formula_on_named_bars(
        formula_id,
        offline_fixture_bars(bars),
        ts_code=ts_code,
        spec=spec,
        holdout_start=holdout_start,
        repo=repo,
    )


def load_one_name_pointer_bars(
    snapshot: Mapping[str, Any],
    conn: Any,
    ts_code: str,
    *,
    days: Sequence[str] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Pointer preflight, then one-name canonical bars. Not full-day hash bind."""

    try:
        return _load_one_name_pointer_bars(snapshot, conn, ts_code, days=days)
    except OneNamePointerError as exc:
        _raise_pointer(exc)
        raise


def simulate_formula_on_live_pointer(
    formula_id: str,
    snapshot: Mapping[str, Any],
    conn: Any,
    *,
    ts_code: str = CHALLENGER_CODE,
    days: Sequence[str] | None = None,
    spec: StrategySpec | None = None,
    holdout_start: str | None = None,
    repo: Path | str | None = None,
):
    bars = load_one_name_pointer_bars(
        snapshot, conn, ts_code, days=days
    )
    return _simulate_formula_from_bars(
        formula_id,
        bars,
        ts_code=ts_code,
        spec=spec,
        holdout_start=holdout_start,
        repo=repo,
    )


def named_ohlcv_to_arrays(
    bars_by_day: Mapping[str, Sequence[Mapping[str, object]]],
    ts_code: str,
) -> tuple[dict[str, np.ndarray], tuple[str, ...], int]:
    by_day: dict[str, dict[str, Mapping[str, object]]] = {}
    codes: set[str] = set()
    for day, rows in bars_by_day.items():
        compact = _compact_day(day)
        if len(compact) != 8:
            continue
        for row in rows:
            code = str(row.get("ts_code") or "")
            if code:
                codes.add(code)
                by_day.setdefault(compact, {})[code] = row
    days = tuple(sorted(by_day))
    opens: list[float] = []
    highs: list[float] = []
    lows: list[float] = []
    closes: list[float] = []
    volumes: list[float] = []
    amounts: list[float] = []
    used: list[str] = []
    for day in days:
        bar = (by_day.get(day) or {}).get(ts_code)
        if bar is None:
            continue
        close_px = float(bar.get("close") or 0.0)
        vol = float(bar.get("vol") or bar.get("volume") or 0.0)
        amount = bar.get("amount")
        used.append(day)
        opens.append(float(bar.get("open") or bar.get("open_") or 0.0))
        highs.append(float(bar.get("high") or 0.0))
        lows.append(float(bar.get("low") or 0.0))
        closes.append(close_px)
        volumes.append(vol)
        amounts.append(float(amount) if amount not in (None, "") else close_px * vol * 100.0)
    if not used:
        raise FormulaChallengeError("named_bars_missing_code")
    return (
        {
            "open_": np.asarray(opens, dtype=float),
            "high": np.asarray(highs, dtype=float),
            "low": np.asarray(lows, dtype=float),
            "close": np.asarray(closes, dtype=float),
            "volume": np.asarray(volumes, dtype=float),
            "amount": np.asarray(amounts, dtype=float),
        },
        tuple(used),
        len(codes),
    )


def simulate_formula_on_named_bars(
    formula_id: str,
    bars_by_day: Mapping[str, Sequence[Mapping[str, object]]],
    *,
    ts_code: str = CHALLENGER_CODE,
    spec: StrategySpec | None = None,
    holdout_start: str | None = None,
    repo: Path | str | None = None,
):
    refuse_legacy_paper_source(formula_id)
    loaded = spec or load_strategy_spec(f"formulas:{formula_id}", repo=repo)
    if loaded.package_id != "formulas":
        raise FormulaChallengeError("formula_paper_requires_formulas_spec")
    if loaded.pnl_source != CHALLENGER_PNL_SOURCE:
        raise FormulaChallengeError("legacy_optuna_or_vwap_is_not_paper")
    bars = _typed_named_bars(bars_by_day)
    return _simulate_formula_from_bars(
        formula_id,
        bars,
        ts_code=ts_code,
        spec=loaded,
        holdout_start=holdout_start,
        repo=repo,
    )


def _simulate_formula_from_bars(
    formula_id: str,
    bars_by_day: Mapping[str, Sequence[Mapping[str, object]]],
    *,
    ts_code: str,
    spec: StrategySpec | None,
    holdout_start: str | None,
    repo: Path | str | None,
):
    refuse_legacy_paper_source(formula_id)
    loaded = spec or load_strategy_spec(f"formulas:{formula_id}", repo=repo)
    if loaded.package_id != "formulas":
        raise FormulaChallengeError("formula_paper_requires_formulas_spec")
    if loaded.pnl_source != CHALLENGER_PNL_SOURCE:
        raise FormulaChallengeError("legacy_optuna_or_vwap_is_not_paper")
    arrays, days, n_codes = named_ohlcv_to_arrays(bars_by_day, ts_code)
    refuse_universe_freeze_replay(n_codes=n_codes, n_days=len(days))
    assert_replay_before_holdout(days, holdout_start=holdout_start)
    vectors = compute_formula_vectors(formula_id, arrays, repo=repo)
    events = signals_to_events(
        ts_code=ts_code,
        days=days,
        entry=vectors["entry"],
        exit_=vectors["exit"],
    )
    return simulate_signal_hold_paper(
        bars_by_day,
        events,
        loaded,
        pnl_source=CHALLENGER_PNL_SOURCE,
        event_exit_reason="formula_exit",
    )


def simulate_formula_hold_paper(
    formula_id: str,
    *,
    spec: StrategySpec | None = None,
    arrays: Mapping[str, np.ndarray] | None = None,
    repo: Path | str | None = None,
):
    fixture = dict(arrays or frozen_ohlcv_fixture())
    bars, _days = fixture_bars(fixture)
    return simulate_formula_on_named_bars(
        formula_id,
        offline_fixture_bars(bars),
        ts_code=CHALLENGER_CODE,
        spec=spec,
        repo=repo,
    )


def prove_formula_pit_truncation(
    formula_id: str,
    *,
    cutoff: int = 400,
    extra: int = 50,
    repo: Path | str | None = None,
) -> None:
    base = frozen_ohlcv_fixture()
    prefix = {key: value[:cutoff] for key, value in base.items()}
    rng = np.random.default_rng(99)
    tail_close = 25.0 * np.exp(np.cumsum(rng.normal(0.0, 0.03, extra)))
    tail_volume = np.exp(rng.normal(np.log(800_000.0), 0.4, extra))
    tail = {
        "open_": tail_close * np.exp(rng.normal(0.0, 0.01, extra)),
        "high": tail_close * 1.02,
        "low": tail_close * 0.98,
        "close": tail_close,
        "volume": tail_volume,
        "amount": tail_close * tail_volume * 100.0,
    }
    extended = {key: np.concatenate([base[key], tail[key]]) for key in base}
    before = compute_formula_vectors(formula_id, prefix, repo=repo)
    after = compute_formula_vectors(formula_id, extended, repo=repo)
    if not np.array_equal(before["entry"], after["entry"][:cutoff]):
        raise FormulaChallengeError("formula_pit_entry_changed")
    if not np.array_equal(before["exit"], after["exit"][:cutoff]):
        raise FormulaChallengeError("formula_pit_exit_changed")


def entry_overlap_diagnostics(
    *,
    arrays: Mapping[str, np.ndarray] | None = None,
    repo: Path | str | None = None,
) -> dict[str, Any]:
    fixture = dict(arrays or frozen_ohlcv_fixture())
    vectors = {
        formula_id: compute_formula_vectors(formula_id, fixture, repo=repo)["entry"]
        for formula_id in FROZEN_FORMULA_IDS
    }
    pairs = []
    for left, right in combinations(FROZEN_FORMULA_IDS, 2):
        intersection = int(np.logical_and(vectors[left], vectors[right]).sum())
        pairs.append({"a": left, "b": right, "intersection": intersection})
    return {
        "claimable": False,
        "role": "diagnostic_overlap_only",
        "pairs": pairs,
    }


__all__ = [
    "CHALLENGER_CODE",
    "FROZEN_VECTORS",
    "FormulaChallengeError",
    "UNIVERSE_FREEZE_DAY_COUNT",
    "assert_replay_before_holdout",
    "compute_formula_vectors",
    "entry_overlap_diagnostics",
    "fixture_bars",
    "frozen_ohlcv_fixture",
    "load_formula_engine",
    "load_one_name_pointer_bars",
    "named_ohlcv_to_arrays",
    "nominal_days_from_snapshot",
    "prove_formula_pit_truncation",
    "refuse_legacy_paper_source",
    "refuse_universe_freeze_replay",
    "signals_to_events",
    "simulate_formula_hold_paper",
    "simulate_formula_on_live_pointer",
    "simulate_formula_on_named_bars",
    "simulate_formula_on_snapshot",
    "synthetic_trading_days",
    "vector_fingerprint",
]
