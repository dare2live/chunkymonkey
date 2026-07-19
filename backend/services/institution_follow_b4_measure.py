"""Measured B4 institution/event block vs B0 (identical snapshot/folds/costs).

PIT
---
- Source: frozen disclosure ``DatasetSnapshot`` partitions of
  ``canonical_top10_float_holders_period`` (not pipeline ``accepted_at``).
- Exclude NULL ``notice_date`` (contract-level).
- Episode usable on trading day ``t`` only when
  ``notice_date <= t`` and ``available_at`` calendar date ``<= t``.
- Signal fires on the **first** trading day the episode becomes usable
  (typically notice_date when it is a trading day).
- Entry: next trade open after signal; chase up to ``max_chase_days`` on
  suspend / limit-up / missing bar (§8.1). No best-price look-ahead.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any, Mapping, Sequence

from services.data_sources.holders_top10_schema import CANONICAL_TABLE
from services.institution_follow_b0_measure import (
    B0Prereg,
    BareKPaperMetrics,
    MeasuredB0Result,
    UNKNOWN,
    WalkForwardPlan,
    evaluate_claimable,
    measure_b0_paper,
)
from services.institution_follow_edge_gates import (
    evaluate_accept_edge_gates,
    evaluate_holdout_lift_vs_b0,
)

DEFINITION_VERSION = "institution_event_holders_disclosure_v0"
METHOD_ID = "notice_available_increase_event_day"
POPULATION_KIND = "disclosure_snapshot_canonical_holders"
SOURCE_CANONICAL_HOLDERS = CANONICAL_TABLE
MAX_CHASE_DAYS = 3
# Sparse disclosures: require enough event days + unique namespaced stocks.
MIN_EVENT_DAY_FRACTION = 0.25
MIN_EVENT_DAYS = 10
MIN_UNIQUE_SIGNAL_STOCKS = 20
INCREASE_STATUSES = frozenset({"增持", "新进"})

REASON_B4_DISCLOSURE_COVERAGE_INSUFFICIENT = (
    "b4_disclosure_event_coverage_insufficient"
)
REASON_B4_NULL_NOTICE_EXCLUDED = "b4_null_notice_date_excluded"
REASON_B4_PAPER_MEASURED = "measured_b4_paper_institution_event"
REASON_B4_NO_B0_CONTEXT = "b4_requires_measured_b0_context"


def _norm_day(value: Any) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())[:8]


def _code6(ts_code: str) -> str:
    return str(ts_code or "").split(".", 1)[0]


def _available_compact(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.strftime("%Y%m%d")
    digits = _norm_day(value)
    return digits if len(digits) == 8 else None


@dataclass(frozen=True)
class DisclosureEpisode:
    """One PIT-safe holder increase event (stock × notice)."""

    stock_code: str
    notice_date: str
    available_at_date: str
    report_date: str
    score: float
    increase_holder_n: int
    details: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "stock_code": self.stock_code,
            "notice_date": self.notice_date,
            "available_at_date": self.available_at_date,
            "report_date": self.report_date,
            "score": self.score,
            "increase_holder_n": self.increase_holder_n,
            "details": dict(self.details),
        }


@dataclass(frozen=True)
class DisclosureEventCoverage:
    status: str
    trading_days: tuple[str, ...]
    event_days: int
    event_day_fraction: float
    unique_signal_stocks: int
    null_notice_excluded: int
    missing_available_at_excluded: int
    episode_count: int
    sufficient: bool
    reason: str
    details: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "trading_days": list(self.trading_days),
            "event_days": self.event_days,
            "event_day_fraction": self.event_day_fraction,
            "unique_signal_stocks": self.unique_signal_stocks,
            "null_notice_excluded": self.null_notice_excluded,
            "missing_available_at_excluded": self.missing_available_at_excluded,
            "episode_count": self.episode_count,
            "sufficient": self.sufficient,
            "reason": self.reason,
            "details": dict(self.details),
            "min_event_day_fraction": MIN_EVENT_DAY_FRACTION,
            "min_event_days": MIN_EVENT_DAYS,
            "min_unique_signal_stocks": MIN_UNIQUE_SIGNAL_STOCKS,
            "definition_version": DEFINITION_VERSION,
            "method": METHOD_ID,
            "population_kind": POPULATION_KIND,
            "max_chase_days": MAX_CHASE_DAYS,
        }


@dataclass(frozen=True)
class B0B4DeltaMetrics:
    total_return: float | None
    max_drawdown: float | None
    win_rate: float | None
    payoff_ratio: float | None
    turnover: float | None
    n_trades_completed: int | None
    holdout_total_return: float | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "total_return": self.total_return,
            "max_drawdown": self.max_drawdown,
            "win_rate": self.win_rate,
            "payoff_ratio": self.payoff_ratio,
            "turnover": self.turnover,
            "n_trades_completed": self.n_trades_completed,
            "holdout_total_return": self.holdout_total_return,
            "unit": "b4_minus_b0",
        }


@dataclass(frozen=True)
class MeasuredB4Result:
    coverage: DisclosureEventCoverage
    episodes: tuple[DisclosureEpisode, ...]
    eligible_by_day: dict[str, tuple[str, ...]]
    measured: MeasuredB0Result | None
    b0_metrics: BareKPaperMetrics | None
    b0_holdout_metrics: BareKPaperMetrics | None
    delta: B0B4DeltaMetrics | None
    claimable: bool
    reason: str
    edge_gates: dict[str, Any]
    holdout_lift_stability: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "coverage": self.coverage.as_dict(),
            "episode_count": len(self.episodes),
            "eligible_day_count": len(self.eligible_by_day),
            "measured": self.measured.as_dict() if self.measured else None,
            "b0_metrics": self.b0_metrics.as_dict() if self.b0_metrics else None,
            "b0_holdout_metrics": (
                self.b0_holdout_metrics.as_dict()
                if self.b0_holdout_metrics
                else None
            ),
            "delta_b4_minus_b0": self.delta.as_dict() if self.delta else None,
            "claimable": self.claimable,
            "reason": self.reason,
            "accept_edge_gates": dict(self.edge_gates),
            "holdout_lift_stability": dict(self.holdout_lift_stability),
            "paper_fills": "measured" if self.measured else "not_run",
            "definition_version": DEFINITION_VERSION,
            "method": METHOD_ID,
            "population_kind": POPULATION_KIND,
            "max_chase_days": MAX_CHASE_DAYS,
        }


def open_holders_conn():
    from services.data_access.resolver import connect_ro

    return connect_ro("smartmoney")


def _is_increase_row(row: Mapping[str, Any]) -> bool:
    if row.get("is_exit_row") is True or row.get("is_exit_row") == 1:
        return False
    status = str(row.get("change_status") or "").strip()
    if status in INCREASE_STATUSES:
        return True
    # 20260717 canary lacked enrichment — treat non-exit top holders as
    # disclosure event presence only when change_status is empty (thin PIT).
    return status == "" and not bool(row.get("is_exit_row"))


def _row_score(row: Mapping[str, Any]) -> float:
    change = row.get("hold_change_num")
    if change is not None:
        try:
            ch = float(change)
            if ch > 0:
                return ch
        except (TypeError, ValueError):
            pass
    ratio = row.get("hold_ratio_float")
    if ratio is None:
        return 1.0
    try:
        return max(float(ratio), 0.0)
    except (TypeError, ValueError):
        return 1.0


def episodes_from_holder_rows(
    rows: Sequence[Mapping[str, Any]],
) -> tuple[tuple[DisclosureEpisode, ...], int, int]:
    """Aggregate increase rows → stock×notice episodes; count exclusions."""

    null_notice = 0
    missing_avail = 0
    acc: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        notice = _norm_day(row.get("notice_date"))
        if len(notice) != 8:
            null_notice += 1
            continue
        avail = _available_compact(row.get("available_at"))
        if avail is None:
            # Fail closed to notice_date only when available_at missing —
            # still recorded as exclusion for audit; do not invent future.
            missing_avail += 1
            continue
        if not _is_increase_row(row):
            continue
        code = _code6(str(row.get("stock_code") or row.get("ts_code") or ""))
        if len(code) < 6:
            continue
        key = (code, notice)
        slot = acc.get(key)
        if slot is None:
            acc[key] = {
                "stock_code": code,
                "notice_date": notice,
                "available_at_date": avail,
                "report_date": _norm_day(row.get("report_date")),
                "score": _row_score(row),
                "increase_holder_n": 1,
            }
        else:
            # Keep earliest available_at; sum score.
            if avail < str(slot["available_at_date"]):
                slot["available_at_date"] = avail
            slot["score"] = float(slot["score"]) + _row_score(row)
            slot["increase_holder_n"] = int(slot["increase_holder_n"]) + 1

    episodes = tuple(
        DisclosureEpisode(
            stock_code=str(v["stock_code"]),
            notice_date=str(v["notice_date"]),
            available_at_date=str(v["available_at_date"]),
            report_date=str(v["report_date"] or ""),
            score=float(v["score"]),
            increase_holder_n=int(v["increase_holder_n"]),
        )
        for _, v in sorted(acc.items(), key=lambda kv: (kv[0][1], kv[0][0]))
    )
    return episodes, null_notice, missing_avail


def load_holder_rows_for_snapshot(
    conn,
    snapshot: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Load canonical holder rows for snapshot holders_top10 date_set only."""

    domains = snapshot.get("domains") or {}
    holders = domains.get("holders_top10") or {}
    date_set = [
        _norm_day(d)
        for d in (holders.get("date_set") or [])
        if len(_norm_day(d)) == 8
    ]
    if not date_set:
        return []
    placeholders = ", ".join("?" for _ in date_set)
    sql = f"""
        SELECT stock_code, report_date, notice_date, available_at,
               hold_ratio_float, hold_change_num, change_status, is_exit_row,
               holder_name, holder_rank
        FROM {CANONICAL_TABLE}
        WHERE replace(CAST(notice_date AS VARCHAR), '-', '') IN ({placeholders})
    """
    rows = conn.execute(sql, date_set).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        if hasattr(row, "keys"):
            out.append({k: row[k] for k in row.keys()})
        else:
            # positional fallback
            keys = [
                "stock_code",
                "report_date",
                "notice_date",
                "available_at",
                "hold_ratio_float",
                "hold_change_num",
                "change_status",
                "is_exit_row",
                "holder_name",
                "holder_rank",
            ]
            out.append(dict(zip(keys, row)))
    return out


def first_signal_day(
    episode: DisclosureEpisode,
    trading_days: Sequence[str],
) -> str | None:
    """First trading day ``t`` with notice/available <= t (strict PIT)."""

    earliest = max(episode.notice_date, episode.available_at_date)
    for day in trading_days:
        d = _norm_day(day)
        if d >= earliest:
            return d
    return None


def eligible_by_day_from_episodes(
    episodes: Sequence[DisclosureEpisode],
    trading_days: Sequence[str],
) -> dict[str, set[str]]:
    days = [_norm_day(d) for d in trading_days if len(_norm_day(d)) == 8]
    out: dict[str, set[str]] = {d: set() for d in days}
    for ep in episodes:
        sig = first_signal_day(ep, days)
        if sig is None:
            continue
        out.setdefault(sig, set()).add(ep.stock_code)
    return out


def measure_disclosure_event_coverage(
    trading_days: Sequence[str],
    eligible_by_day: Mapping[str, set[str]],
    *,
    null_notice_excluded: int,
    missing_available_at_excluded: int,
    episode_count: int,
) -> DisclosureEventCoverage:
    days = tuple(_norm_day(d) for d in trading_days if len(_norm_day(d)) == 8)
    if not days:
        return DisclosureEventCoverage(
            status="EMPTY",
            trading_days=(),
            event_days=0,
            event_day_fraction=0.0,
            unique_signal_stocks=0,
            null_notice_excluded=null_notice_excluded,
            missing_available_at_excluded=missing_available_at_excluded,
            episode_count=episode_count,
            sufficient=False,
            reason=REASON_B4_DISCLOSURE_COVERAGE_INSUFFICIENT,
            details={"note": "no_trading_days"},
        )
    event_days = sum(1 for d in days if eligible_by_day.get(d))
    stocks: set[str] = set()
    for d in days:
        stocks |= set(eligible_by_day.get(d) or ())
    frac = event_days / len(days)
    sufficient = (
        event_days >= MIN_EVENT_DAYS
        and frac >= MIN_EVENT_DAY_FRACTION
        and len(stocks) >= MIN_UNIQUE_SIGNAL_STOCKS
    )
    reason = (
        "disclosure_event_coverage_ready"
        if sufficient
        else REASON_B4_DISCLOSURE_COVERAGE_INSUFFICIENT
    )
    if null_notice_excluded:
        # Audit flag only — exclusions already applied.
        pass
    return DisclosureEventCoverage(
        status="MEASURED",
        trading_days=days,
        event_days=event_days,
        event_day_fraction=frac,
        unique_signal_stocks=len(stocks),
        null_notice_excluded=null_notice_excluded,
        missing_available_at_excluded=missing_available_at_excluded,
        episode_count=episode_count,
        sufficient=sufficient,
        reason=reason,
        details={
            "source_table": SOURCE_CANONICAL_HOLDERS,
            "increase_statuses": sorted(INCREASE_STATUSES),
            "pit_rule": "notice_date_and_available_at_date_le_signal_day",
            "null_notice_policy": REASON_B4_NULL_NOTICE_EXCLUDED,
        },
    )


def _delta(
    b4: BareKPaperMetrics,
    b0: BareKPaperMetrics,
    *,
    b4_holdout: BareKPaperMetrics,
    b0_holdout: BareKPaperMetrics,
) -> B0B4DeltaMetrics:
    def sub(a: float | None, b: float | None) -> float | None:
        if a is None or b is None:
            return None
        return float(a) - float(b)

    return B0B4DeltaMetrics(
        total_return=sub(b4.total_return, b0.total_return),
        max_drawdown=sub(b4.max_drawdown, b0.max_drawdown),
        win_rate=sub(b4.win_rate, b0.win_rate),
        payoff_ratio=sub(b4.payoff_ratio, b0.payoff_ratio),
        turnover=sub(b4.turnover, b0.turnover),
        n_trades_completed=(
            int(b4.n_trades_completed) - int(b0.n_trades_completed)
        ),
        holdout_total_return=sub(
            b4_holdout.total_return, b0_holdout.total_return
        ),
    )


def measure_b4_paper(
    bars_by_day: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    b0_measured: MeasuredB0Result,
    episodes: Sequence[DisclosureEpisode],
    null_notice_excluded: int = 0,
    missing_available_at_excluded: int = 0,
    prereg: B0Prereg | None = None,
    st_codes: Sequence[str] | None = None,
) -> MeasuredB4Result:
    """Gate B0 top-K on disclosure event-day eligibility + chase entry."""

    cfg = prereg or replace(
        b0_measured.prereg, max_chase_days=MAX_CHASE_DAYS
    )
    if cfg.max_chase_days <= 0:
        cfg = replace(cfg, max_chase_days=MAX_CHASE_DAYS)
    plan: WalkForwardPlan = b0_measured.walk_forward
    days = list(plan.trading_days)
    eligible = eligible_by_day_from_episodes(episodes, days)
    coverage = measure_disclosure_event_coverage(
        days,
        eligible,
        null_notice_excluded=null_notice_excluded,
        missing_available_at_excluded=missing_available_at_excluded,
        episode_count=len(episodes),
    )
    if not coverage.sufficient:
        return MeasuredB4Result(
            coverage=coverage,
            episodes=tuple(episodes),
            eligible_by_day={k: tuple(sorted(v)) for k, v in eligible.items()},
            measured=None,
            b0_metrics=b0_measured.metrics,
            b0_holdout_metrics=b0_measured.holdout_metrics,
            delta=None,
            claimable=False,
            reason=coverage.reason,
            edge_gates={},
            holdout_lift_stability={},
        )

    measured = measure_b0_paper(
        bars_by_day,
        days,
        prereg=cfg,
        st_codes=st_codes,
        eligible_by_day=eligible,
        walk_forward=plan,
    )
    claimable, _ = evaluate_claimable(
        measured.walk_forward, measured.metrics, prereg=cfg
    )
    edge = evaluate_accept_edge_gates(
        measured.walk_forward,
        measured.metrics,
        measured.holdout_metrics,
        prereg=cfg,
    )
    stability = evaluate_holdout_lift_vs_b0(
        measured.holdout_metrics,
        b0_measured.holdout_metrics,
    )
    delta = _delta(
        measured.metrics,
        b0_measured.metrics,
        b4_holdout=measured.holdout_metrics,
        b0_holdout=b0_measured.holdout_metrics,
    )
    reason = REASON_B4_PAPER_MEASURED if claimable else measured.reason
    return MeasuredB4Result(
        coverage=coverage,
        episodes=tuple(episodes),
        eligible_by_day={k: tuple(sorted(v)) for k, v in eligible.items()},
        measured=measured,
        b0_metrics=b0_measured.metrics,
        b0_holdout_metrics=b0_measured.holdout_metrics,
        delta=delta,
        claimable=claimable,
        reason=reason,
        edge_gates=edge.as_dict(),
        holdout_lift_stability=stability.as_dict(),
    )


__all__ = [
    "DEFINITION_VERSION",
    "INCREASE_STATUSES",
    "MAX_CHASE_DAYS",
    "METHOD_ID",
    "MIN_EVENT_DAY_FRACTION",
    "MIN_EVENT_DAYS",
    "MIN_UNIQUE_SIGNAL_STOCKS",
    "POPULATION_KIND",
    "REASON_B4_DISCLOSURE_COVERAGE_INSUFFICIENT",
    "REASON_B4_NO_B0_CONTEXT",
    "REASON_B4_NULL_NOTICE_EXCLUDED",
    "REASON_B4_PAPER_MEASURED",
    "SOURCE_CANONICAL_HOLDERS",
    "UNKNOWN",
    "B0B4DeltaMetrics",
    "DisclosureEpisode",
    "DisclosureEventCoverage",
    "MeasuredB4Result",
    "eligible_by_day_from_episodes",
    "episodes_from_holder_rows",
    "first_signal_day",
    "load_holder_rows_for_snapshot",
    "measure_b4_paper",
    "measure_disclosure_event_coverage",
    "open_holders_conn",
]
