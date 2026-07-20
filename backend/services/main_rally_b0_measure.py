"""Phase F main_rally B0 measure: pivot-confirmed setup → E-style paper fills.

Candidate generator uses **accepted nominal K only** + ``rally_detect`` primitives
and ``rally_gt.yaml`` thresholds. It does **not** read GT/negative label tables.
Labels come from paper fills (T+1/T+2 nominal opens), not episode outcomes.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

import yaml

from services import rally_detect as rd
from services.institution_follow_b0_measure import (
    B0Prereg,
    MeasuredB0Result,
    REASON_PAPER_MEASURED,
    REASON_SHORT_WINDOW,
    evaluate_claimable,
    plan_walk_forward,
)
from services.institution_follow_paper import (
    metrics_from_fills as _metrics_from_fills,
    simulate_paper_fills,
)

_CFG_PATH = Path(__file__).resolve().parent.parent / "config" / "rally_gt.yaml"


@dataclass(frozen=True)
class SetupSignal:
    """Decision-time-visible rally setup (confirmation day, not bottom day)."""

    ts_code: str
    bottom_date: str
    signal_date: str
    available_at: str
    base_days: int
    pivot_low_window: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "ts_code": self.ts_code,
            "bottom_date": self.bottom_date,
            "signal_date": self.signal_date,
            "available_at": self.available_at,
            "base_days": self.base_days,
            "pivot_low_window": self.pivot_low_window,
        }


def load_setup_thresholds() -> dict[str, Any]:
    """Read setup thresholds from rally_gt.yaml (never re-tune here)."""

    cfg = yaml.safe_load(_CFG_PATH.read_text(encoding="utf-8")) or {}
    ep = cfg.get("episode") or {}
    return {
        "pivot_low_window": int(ep["pivot_low_window"]),
        "base_min_days": int(ep["base_min_days"]),
        "base_lookback_days": int(ep["base_lookback_days"]),
        "base_band_low": float(ep["base_band_low"]),
        "base_band_high": float(ep["base_band_high"]),
        "warmup_bars": int(ep["warmup_bars"]),
        "taxonomy_version": str(cfg.get("taxonomy_version") or ""),
    }


def _norm_day(value: Any) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())[:8]


def _bar_index(
    bars_by_day: Mapping[str, Sequence[Mapping[str, Any]]],
) -> dict[str, dict[str, dict[str, Any]]]:
    out: dict[str, dict[str, dict[str, Any]]] = {}
    for day, rows in bars_by_day.items():
        d = _norm_day(day)
        by_code: dict[str, dict[str, Any]] = {}
        for row in rows:
            code = str(row.get("ts_code") or "")
            if code:
                by_code[code] = dict(row)
        out[d] = by_code
    return out


def _series_by_code(
    bars_by_day: Mapping[str, Sequence[Mapping[str, Any]]],
) -> dict[str, tuple[list[str], list[float], list[float], list[float]]]:
    """Per-code chronologically ordered (dates, highs, lows, closes)."""

    days = sorted({_norm_day(d) for d in bars_by_day if len(_norm_day(d)) == 8})
    by_day = _bar_index(bars_by_day)
    codes: set[str] = set()
    for d in days:
        codes.update(by_day.get(d, {}))
    out: dict[str, tuple[list[str], list[float], list[float], list[float]]] = {}
    for code in codes:
        ds: list[str] = []
        highs: list[float] = []
        lows: list[float] = []
        closes: list[float] = []
        for d in days:
            bar = (by_day.get(d) or {}).get(code)
            if bar is None:
                continue
            try:
                h = float(bar["high"])
                lo = float(bar["low"])
                c = float(bar["close"])
            except (KeyError, TypeError, ValueError):
                continue
            if lo <= 0 or c <= 0:
                continue
            ds.append(d)
            highs.append(h)
            lows.append(lo)
            closes.append(c)
        if ds:
            out[code] = (ds, highs, lows, closes)
    return out


def detect_setup_signals(
    bars_by_day: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    thresholds: Mapping[str, Any] | None = None,
) -> list[SetupSignal]:
    """Emit pivot-confirmed + long-base setups from nominal bars only.

    Signal ``available_at`` = confirmation day (bottom index + pivot_low_window),
    never the bottom date itself.
    """

    thr = dict(thresholds) if thresholds is not None else load_setup_thresholds()
    win = int(thr["pivot_low_window"])
    base_min = int(thr["base_min_days"])
    lookback = int(thr["base_lookback_days"])
    band_lo = float(thr["base_band_low"])
    band_hi = float(thr["base_band_high"])
    warmup = int(thr["warmup_bars"])

    signals: list[SetupSignal] = []
    for code, (dates, _highs, lows, closes) in _series_by_code(bars_by_day).items():
        n = len(dates)
        if n < max(win + 1, warmup, base_min + 1):
            continue
        # Confirmation requires index i+win to exist → scan bottoms up to n-win-1.
        i = max(win, warmup)
        while i + win < n:
            if not rd.is_pivot_low(lows, i, win):
                i += 1
                continue
            base = rd.base_days_count(
                closes, i, float(lows[i]), lookback, band_lo, band_hi
            )
            if base < base_min:
                i += 1
                continue
            confirm_idx = i + win
            bottom_date = dates[i]
            signal_date = dates[confirm_idx]
            signals.append(
                SetupSignal(
                    ts_code=code,
                    bottom_date=bottom_date,
                    signal_date=signal_date,
                    available_at=signal_date,
                    base_days=int(base),
                    pivot_low_window=win,
                )
            )
            # Skip forward past confirmation to avoid dense re-fires on same trough.
            i = confirm_idx + 1
        # loop continues
    signals.sort(key=lambda s: (s.signal_date, s.ts_code))
    return signals


def eligible_codes_by_signal_day(
    bars_by_day: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    thresholds: Mapping[str, Any] | None = None,
) -> dict[str, set[str]]:
    """Map confirmation (signal) day → eligible ts_codes for paper fills."""

    out: dict[str, set[str]] = {}
    for sig in detect_setup_signals(bars_by_day, thresholds=thresholds):
        out.setdefault(sig.signal_date, set()).add(sig.ts_code)
    return out


def default_main_rally_b0_prereg() -> B0Prereg:
    """Same costs / T+1 / holdout floors as E; signal name is rally setup."""

    return B0Prereg(signal="rally_setup_pivot_confirmed_base_days")


def measure_main_rally_b0_paper(
    bars_by_day: Mapping[str, Sequence[Mapping[str, Any]]],
    trading_days: Sequence[str] | None = None,
    *,
    prereg: B0Prereg | None = None,
    st_codes: Sequence[str] | None = None,
    thresholds: Mapping[str, Any] | None = None,
) -> MeasuredB0Result:
    """Purged WF + paper fills restricted to pivot-confirmed setup eligibles."""

    cfg = prereg or default_main_rally_b0_prereg()
    days = (
        list(trading_days)
        if trading_days is not None
        else sorted({_norm_day(d) for d in bars_by_day})
    )
    plan = plan_walk_forward(days, prereg=cfg)
    eligible = eligible_codes_by_signal_day(bars_by_day, thresholds=thresholds)
    fills = simulate_paper_fills(
        bars_by_day,
        plan,
        prereg=cfg,
        st_codes=st_codes,
        eligible_by_day=eligible,
    )
    metrics = _metrics_from_fills(
        fills, trading_day_count=len(plan.trading_days), top_k=cfg.top_k
    )
    holdout_fills = tuple(f for f in fills if f.fold_role == "one_touch_holdout")
    holdout_metrics = _metrics_from_fills(
        holdout_fills,
        trading_day_count=len(plan.holdout_dates) or 1,
        top_k=cfg.top_k,
    )
    claimable, reason = evaluate_claimable(plan, metrics, prereg=cfg)
    if not claimable:
        reason = REASON_SHORT_WINDOW
    else:
        reason = REASON_PAPER_MEASURED
    return MeasuredB0Result(
        prereg=cfg,
        walk_forward=plan,
        fills=fills,
        metrics=metrics,
        holdout_metrics=holdout_metrics,
        claimable=claimable,
        reason=reason,
    )


__all__ = [
    "SetupSignal",
    "default_main_rally_b0_prereg",
    "detect_setup_signals",
    "eligible_codes_by_signal_day",
    "load_setup_thresholds",
    "measure_main_rally_b0_paper",
]
