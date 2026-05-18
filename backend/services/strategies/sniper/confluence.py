"""MSAF Scheme 6 Sniper confluence rules.

The engine is deliberately independent from ml_score.  Thresholds are
calibrated from rows strictly before the signal date, using the most recent
252 distinct PIT dates where dated history is available.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from math import isfinite
from typing import Any, Iterable, Mapping, Sequence


DEFAULT_TRIGGER_THRESHOLD = 5
DEFAULT_LOOKBACK_DAYS = 252


ALIASES: dict[str, tuple[str, ...]] = {
    "date": ("date", "trade_date", "signal_date", "calc_date", "report_date"),
    "ret_60d": ("ret_60d", "return_60d", "mom_60d", "pct_chg_60d", "ret60"),
    "lhb_inst_net_buy": (
        "lhb_inst_net_buy",
        "lhb_inst_net_buy_amount",
        "lhb_net_buy",
        "lhb_net_buy_amount",
        "lhb_net_buy_30d",
        "lhb_net_buy_pct_30d",
        "lhb_inst_buy_30d",
        "lhb_inst_buy_count_30d",
    ),
    "main_capital_net_inflow_5d": (
        "main_capital_net_inflow_5d",
        "main_net_inflow_5d",
        "main_capital_5d",
        "main_inflow_5d",
        "capital_net_inflow_5d",
        "net_main_inflow_5d",
        "north_main_inflow_5d",
    ),
    "sector_momentum": (
        "sector_momentum",
        "sector_ret_60d",
        "sector_excess_60d",
        "sm_ret_60d",
        "industry_ret_60d",
        "tdx_l1_ret_60d",
    ),
    "sue": ("sue", "SUE", "earnings_surprise", "standardized_unexpected_earnings"),
    "yesterday_limit_up": (
        "yesterday_limit_up",
        "prev_limit_up",
        "limit_up_yesterday",
        "yday_limit_up",
        "yesterday_is_limit_up",
        "prev_is_limit_up",
    ),
    "unlock_ratio": (
        "unlock_ratio",
        "unlock_ratio_180d",
        "future_unlock_ratio_180d",
        "upcoming_unlock_ratio",
        "unlock_pct",
    ),
    "pledge_ratio": (
        "pledge_ratio",
        "pledge_share_ratio",
        "pledged_ratio",
        "share_pledge_ratio",
    ),
}


@dataclass(frozen=True)
class ConfluenceResult:
    """One-stock confluence verdict for a single signal date."""

    confluence_score: int
    triggered: bool
    rule_hits: dict[str, bool | None] = field(default_factory=dict)
    thresholds: dict[str, float] = field(default_factory=dict)
    hard_excluded: bool = False
    missing_rules: tuple[str, ...] = ()


def _coerce_rows(rows: Any) -> list[Mapping[str, Any]]:
    if rows is None:
        return []
    if hasattr(rows, "to_dict"):
        try:
            return list(rows.to_dict("records"))
        except TypeError:
            return list(rows.to_dict())
    if isinstance(rows, Mapping):
        return [rows]
    return list(rows)


def _parse_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value)
    if not text:
        return None
    try:
        return datetime.strptime(text[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _date_of(row: Mapping[str, Any]) -> date | None:
    for key in ALIASES["date"]:
        if key in row:
            parsed = _parse_date(row.get(key))
            if parsed is not None:
                return parsed
    return None


def _as_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if not isfinite(out):
        return None
    return out


def _as_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        if not isfinite(float(value)):
            return None
        return bool(value)
    text = str(value).strip().lower()
    if text in {"1", "true", "t", "yes", "y", "涨停", "limit_up"}:
        return True
    if text in {"0", "false", "f", "no", "n"}:
        return False
    return None


def _first_value(row: Mapping[str, Any], canonical: str) -> Any:
    for key in ALIASES[canonical]:
        if key in row:
            return row.get(key)
    return None


def _feature_float(row: Mapping[str, Any], canonical: str) -> float | None:
    return _as_float(_first_value(row, canonical))


def _feature_bool(row: Mapping[str, Any], canonical: str) -> bool | None:
    return _as_bool(_first_value(row, canonical))


def _percentile(values: Sequence[float], q: float) -> float | None:
    clean = sorted(v for v in values if isfinite(v))
    if not clean:
        return None
    if len(clean) == 1:
        return clean[0]
    pos = (len(clean) - 1) * q
    lo = int(pos)
    hi = min(lo + 1, len(clean) - 1)
    frac = pos - lo
    return clean[lo] * (1.0 - frac) + clean[hi] * frac


def _pit_lookback(
    signal_date: str | date | datetime,
    rows: Iterable[Mapping[str, Any]],
    lookback_days: int,
) -> list[Mapping[str, Any]]:
    """Return history strictly before signal_date.

    If rows carry dates, keep the latest ``lookback_days`` distinct dates.  If
    the caller has already provided undated PIT history, keep the last
    ``lookback_days`` rows without trying to infer future/past membership.
    """
    signal = _parse_date(signal_date)
    dated: list[tuple[date, Mapping[str, Any]]] = []
    undated: list[Mapping[str, Any]] = []
    for row in rows:
        row_date = _date_of(row)
        if row_date is None or signal is None:
            undated.append(row)
            continue
        if row_date < signal:
            dated.append((row_date, row))

    if dated:
        unique_dates = sorted({d for d, _ in dated})
        selected_dates = set(unique_dates[-lookback_days:])
        start_floor = signal - timedelta(days=max(lookback_days * 2, 370))
        return [
            row for row_date, row in dated
            if row_date in selected_dates and row_date >= start_floor
        ]
    return undated[-lookback_days:]


def calibrate_thresholds(
    signal_date: str | date | datetime,
    history: Iterable[Mapping[str, Any]] | None,
    *,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
) -> dict[str, float]:
    """Calibrate all numeric thresholds from PIT-only rolling history."""
    rows = _pit_lookback(signal_date, _coerce_rows(history), lookback_days)
    specs = {
        "ret_60d": ("ret_60d", 0.90),
        "lhb_inst_net_buy": ("lhb_inst_net_buy", 0.75),
        "main_capital_net_inflow_5d": ("main_capital_net_inflow_5d", 0.75),
        "sector_momentum": ("sector_momentum", 0.75),
        "sue": ("sue", 0.70),
        "unlock_ratio": ("unlock_ratio", 0.90),
        "pledge_ratio": ("pledge_ratio", 0.90),
    }
    out: dict[str, float] = {}
    for key, (canonical, quantile) in specs.items():
        values = [
            value for value in (_feature_float(row, canonical) for row in rows)
            if value is not None
        ]
        threshold = _percentile(values, quantile)
        if threshold is not None:
            out[key] = threshold
    return out


def evaluate_confluence(
    signal_date: str | date | datetime,
    features: Mapping[str, Any],
    *,
    history: Iterable[Mapping[str, Any]] | None = None,
    threshold: int = DEFAULT_TRIGGER_THRESHOLD,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
) -> ConfluenceResult:
    """Evaluate Sniper Scheme 6 rules for one stock.

    ``history`` may be passed explicitly or embedded in ``features`` under
    ``history`` / ``calibration_rows``.  Only rows dated before ``signal_date``
    are used for threshold calibration.
    """
    calibration_rows = history
    if calibration_rows is None:
        calibration_rows = features.get("history") or features.get("calibration_rows")
    thresholds = calibrate_thresholds(
        signal_date, calibration_rows, lookback_days=lookback_days,
    )

    rule_hits: dict[str, bool | None] = {
        "r1_ret_60d_top_decile": None,
        "r2_lhb_inst_net_buy": None,
        "r3_main_capital_inflow_5d": None,
        "r4_sector_momentum_top_quartile": None,
        "r5_sue": None,
        "r6_yesterday_limit_up": None,
        "r7_unlock_pledge_ok": None,
    }

    numeric_rules = (
        ("r1_ret_60d_top_decile", "ret_60d", "ret_60d"),
        ("r2_lhb_inst_net_buy", "lhb_inst_net_buy", "lhb_inst_net_buy"),
        (
            "r3_main_capital_inflow_5d",
            "main_capital_net_inflow_5d",
            "main_capital_net_inflow_5d",
        ),
        ("r4_sector_momentum_top_quartile", "sector_momentum", "sector_momentum"),
        ("r5_sue", "sue", "sue"),
    )
    for rule_name, canonical, threshold_key in numeric_rules:
        value = _feature_float(features, canonical)
        cutoff = thresholds.get(threshold_key)
        if value is None or cutoff is None:
            continue
        rule_hits[rule_name] = value > cutoff

    limit_up = _feature_bool(features, "yesterday_limit_up")
    if limit_up is not None:
        rule_hits["r6_yesterday_limit_up"] = limit_up

    hard_excluded = False
    unlock = _feature_float(features, "unlock_ratio")
    pledge = _feature_float(features, "pledge_ratio")
    unlock_cutoff = thresholds.get("unlock_ratio")
    pledge_cutoff = thresholds.get("pledge_ratio")
    r7_known = False
    if unlock is not None and unlock_cutoff is not None:
        r7_known = True
        if unlock >= unlock_cutoff:
            hard_excluded = True
    if pledge is not None and pledge_cutoff is not None:
        r7_known = True
        if pledge >= pledge_cutoff:
            hard_excluded = True
    explicit_exclude = _as_bool(features.get("unlock_pledge_hard_exclude"))
    if explicit_exclude is not None:
        r7_known = True
        hard_excluded = hard_excluded or explicit_exclude
    if r7_known:
        rule_hits["r7_unlock_pledge_ok"] = not hard_excluded

    missing = tuple(k for k, v in rule_hits.items() if v is None)
    score = sum(1 for v in rule_hits.values() if v is True)
    if hard_excluded:
        return ConfluenceResult(
            confluence_score=0,
            triggered=False,
            rule_hits=rule_hits,
            thresholds=thresholds,
            hard_excluded=True,
            missing_rules=missing,
        )
    return ConfluenceResult(
        confluence_score=max(0, min(7, score)),
        triggered=score >= threshold,
        rule_hits=rule_hits,
        thresholds=thresholds,
        hard_excluded=False,
        missing_rules=missing,
    )


def confluence_score(
    signal_date: str | date | datetime,
    features: Mapping[str, Any],
    *,
    history: Iterable[Mapping[str, Any]] | None = None,
    threshold: int = DEFAULT_TRIGGER_THRESHOLD,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
) -> tuple[int, bool]:
    """Compatibility helper returning only ``(score, triggered)``."""
    verdict = evaluate_confluence(
        signal_date,
        features,
        history=history,
        threshold=threshold,
        lookback_days=lookback_days,
    )
    return verdict.confluence_score, verdict.triggered

