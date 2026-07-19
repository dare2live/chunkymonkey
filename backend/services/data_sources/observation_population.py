"""``traded_on_observation_date`` resolver and accepted-source loaders.

Formal daily project population (MASTER §5.1):

1. accepted calendar proves observation date is open;
2. accepted nominal daily Kline supplies securities that actually traded;
3. accepted same-day ST membership is excluded;
4. venue/board prefixes come from one factory-owned UniversePolicy snapshot.

Trusted loaders read accepted partitions only.  Missing live partitions fail
closed with ``NOT_EVALUATED`` / ``BLOCKED``.  Legacy raw/dim/qfq surfaces cannot
satisfy this gate.  Callers may inject test loaders; production uses the live
trusted loaders below.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Literal

from services.data_sources.calendar_reader import (
    CalendarTruth,
    CalendarTruthUnavailable,
    open_calendar_truth,
)
from services.data_sources.calendar_schema import DATASET_ID as CALENDAR_DATASET_ID
from services.universe import UniversePolicy, verify_universe_policy

NOMINAL_KLINE_DATASET_ID = "tier0.market_data.nominal_ohlcv_daily"
ST_MEMBERSHIP_DATASET_ID = "tier0.security_identity.stock_st_daily"
ReadinessStatus = Literal["READY", "BLOCKED", "NOT_EVALUATED"]


class ObservationPopulationUnavailable(RuntimeError):
    """Project-universe observation population cannot be proved."""

    def __init__(self, status: ReadinessStatus, reason: str):
        if status not in {"READY", "BLOCKED", "NOT_EVALUATED"}:
            raise ValueError(f"invalid observation population status={status!r}")
        if status == "READY":
            raise ValueError("ObservationPopulationUnavailable cannot carry READY")
        self.status = status
        self.reason = reason
        super().__init__(f"{status}: {reason}")


@dataclass(frozen=True)
class AcceptedPartitionRef:
    """One accepted partition proof envelope for a security-day population source."""

    dataset_id: str
    partition_value: str
    batch_id: str
    contract_hash: str
    config_hash: str
    content_hash: str
    row_count: int
    available_at: datetime
    accepted_at: datetime

    @property
    def usable_at(self) -> datetime:
        return max(self.available_at, self.accepted_at)


@dataclass(frozen=True)
class ObservationMembership:
    """Immutable membership for one observation date under one policy snapshot."""

    observation_date: date
    decision_time: datetime
    ts_codes: tuple[str, ...]
    calendar_generation_id: str
    calendar_content_hash: str
    nominal_kline: AcceptedPartitionRef
    st_membership: AcceptedPartitionRef
    universe_policy_id: str
    universe_policy_version: int
    universe_policy_hash: str
    excluded_st_count: int
    excluded_board_count: int


@dataclass(frozen=True)
class ObservationPopulationReadiness:
    status: ReadinessStatus
    reasons: tuple[str, ...]
    calendar_dataset_id: str
    nominal_kline_dataset_id: str
    st_membership_dataset_id: str
    policy_id: str
    policy_version: int
    policy_hash: str
    observation_date: date | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "reasons": list(self.reasons),
            "calendar_dataset_id": self.calendar_dataset_id,
            "nominal_kline_dataset_id": self.nominal_kline_dataset_id,
            "st_membership_dataset_id": self.st_membership_dataset_id,
            "policy_id": self.policy_id,
            "policy_version": self.policy_version,
            "policy_hash": self.policy_hash,
            "observation_date": (
                None
                if self.observation_date is None
                else self.observation_date.strftime("%Y%m%d")
            ),
        }


CalendarLoader = Callable[[datetime, UniversePolicy], CalendarTruth]
PartitionLoader = Callable[
    [date, datetime, UniversePolicy], tuple[AcceptedPartitionRef, frozenset[str]]
]


def _as_aware(value: Any, *, field: str) -> datetime:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ObservationPopulationUnavailable(
                "BLOCKED", f"invalid_{field}={value!r}"
            ) from exc
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ObservationPopulationUnavailable(
            "BLOCKED", f"{field}_must_be_timezone_aware"
        )
    return value.astimezone(timezone.utc)


def _as_date(value: Any, *, field: str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        compact = value.strip().replace("-", "")
        if len(compact) == 8 and compact.isdigit():
            try:
                return datetime.strptime(compact, "%Y%m%d").date()
            except ValueError as exc:
                raise ObservationPopulationUnavailable(
                    "BLOCKED", f"invalid_{field}={value!r}"
                ) from exc
    raise ObservationPopulationUnavailable(
        "BLOCKED", f"{field}_must_be_a_valid_date"
    )


def _require_policy(policy: UniversePolicy) -> UniversePolicy:
    attested = verify_universe_policy(policy)
    if attested.trading_calendar_source != CALENDAR_DATASET_ID:
        raise ObservationPopulationUnavailable(
            "BLOCKED",
            "universe_policy_trading_calendar_source_mismatch "
            f"actual={attested.trading_calendar_source!r} "
            f"expected={CALENDAR_DATASET_ID!r}",
        )
    if attested.nominal_kline_source != NOMINAL_KLINE_DATASET_ID:
        raise ObservationPopulationUnavailable(
            "BLOCKED",
            "universe_policy_nominal_kline_source_mismatch "
            f"actual={attested.nominal_kline_source!r}",
        )
    if attested.st_membership_source != ST_MEMBERSHIP_DATASET_ID:
        raise ObservationPopulationUnavailable(
            "BLOCKED",
            "universe_policy_st_membership_source_mismatch "
            f"actual={attested.st_membership_source!r}",
        )
    return attested


def load_accepted_calendar(
    decision_time: datetime,
    policy: UniversePolicy,
) -> CalendarTruth:
    """Trusted calendar loader — never falls back to dim/raw."""

    _require_policy(policy)
    try:
        return open_calendar_truth(decision_time)
    except CalendarTruthUnavailable as exc:
        raise ObservationPopulationUnavailable(exc.status, exc.reason) from exc


def load_accepted_nominal_kline_membership(
    observation_date: date,
    decision_time: datetime,
    policy: UniversePolicy,
) -> tuple[AcceptedPartitionRef, frozenset[str]]:
    """Trusted nominal OHLCV loader — never falls back to raw/qfq/dim."""

    from services.data_sources.nominal_ohlcv_reader import (
        NominalOhlcvTruthUnavailable,
        open_accepted_nominal_ohlcv_membership,
    )

    _require_policy(policy)
    day = _as_date(observation_date, field="observation_date")
    cutoff = _as_aware(decision_time, field="decision_time")
    try:
        part = open_accepted_nominal_ohlcv_membership(day, cutoff)
    except NominalOhlcvTruthUnavailable as exc:
        raise ObservationPopulationUnavailable(exc.status, exc.reason) from exc
    return (
        AcceptedPartitionRef(
            dataset_id=part.dataset_id,
            partition_value=part.partition_value,
            batch_id=part.batch_id,
            contract_hash=part.contract_hash,
            config_hash=part.config_hash,
            content_hash=part.content_hash,
            row_count=part.row_count,
            available_at=part.available_at,
            accepted_at=part.accepted_at,
        ),
        part.ts_codes,
    )


def load_accepted_st_membership(
    observation_date: date,
    decision_time: datetime,
    policy: UniversePolicy,
) -> tuple[AcceptedPartitionRef, frozenset[str]]:
    """Trusted same-day ST loader — never falls back to raw exclude lists."""

    from services.data_sources.stock_st_reader import (
        StockStTruthUnavailable,
        open_accepted_stock_st_membership,
    )

    _require_policy(policy)
    day = _as_date(observation_date, field="observation_date")
    cutoff = _as_aware(decision_time, field="decision_time")
    try:
        part = open_accepted_stock_st_membership(day, cutoff)
    except StockStTruthUnavailable as exc:
        raise ObservationPopulationUnavailable(exc.status, exc.reason) from exc
    return (
        AcceptedPartitionRef(
            dataset_id=part.dataset_id,
            partition_value=part.partition_value,
            batch_id=part.batch_id,
            contract_hash=part.contract_hash,
            config_hash=part.config_hash,
            content_hash=part.content_hash,
            row_count=part.row_count,
            available_at=part.available_at,
            accepted_at=part.accepted_at,
        ),
        part.ts_codes,
    )


def _board_prefix_allowed(ts_code: str, policy: UniversePolicy) -> bool:
    code = str(ts_code or "").strip()
    if len(code) < 2:
        return False
    prefix = code[:2]
    return prefix in set(policy.allowed_board_prefixes)


def _prove_partition_usable(
    ref: AcceptedPartitionRef,
    *,
    expected_dataset_id: str,
    observation_date: date,
    decision_time: datetime,
) -> None:
    if ref.dataset_id != expected_dataset_id:
        raise ObservationPopulationUnavailable(
            "BLOCKED",
            f"accepted_partition_dataset_mismatch actual={ref.dataset_id!r} "
            f"expected={expected_dataset_id!r}",
        )
    if ref.partition_value != observation_date.strftime("%Y%m%d"):
        raise ObservationPopulationUnavailable(
            "BLOCKED",
            "accepted_partition_does_not_match_observation_date "
            f"partition={ref.partition_value!r} "
            f"observation={observation_date.isoformat()}",
        )
    if ref.row_count < 0:
        raise ObservationPopulationUnavailable(
            "BLOCKED", "accepted_partition_row_count_invalid"
        )
    if ref.usable_at > decision_time:
        raise ObservationPopulationUnavailable(
            "NOT_EVALUATED",
            "accepted_partition_not_visible_at_decision_time "
            f"usable_at={ref.usable_at.isoformat()} "
            f"decision_time={decision_time.isoformat()}",
        )
    for field_name, value in (
        ("contract_hash", ref.contract_hash),
        ("config_hash", ref.config_hash),
        ("content_hash", ref.content_hash),
        ("batch_id", ref.batch_id),
    ):
        if not isinstance(value, str) or not value.strip():
            raise ObservationPopulationUnavailable(
                "BLOCKED", f"accepted_partition_missing_{field_name}"
            )


def _prove_membership_parity(
    ref: AcceptedPartitionRef,
    members: frozenset[str],
    *,
    source_label: str,
) -> None:
    """Envelope row_count must equal distinct membership cardinality."""

    size = len(members)
    if int(ref.row_count) != size:
        raise ObservationPopulationUnavailable(
            "BLOCKED",
            f"accepted_{source_label}_row_count_membership_parity_failed "
            f"row_count={ref.row_count} membership_size={size}",
        )


def resolve_traded_on_observation_date(
    observation_date: date | str,
    decision_time: datetime | str,
    policy: UniversePolicy,
    *,
    calendar_loader: CalendarLoader | None = None,
    nominal_kline_loader: PartitionLoader | None = None,
    st_membership_loader: PartitionLoader | None = None,
) -> ObservationMembership:
    """Resolve one day's project-universe membership from accepted sources only."""

    policy = _require_policy(policy)
    day = _as_date(observation_date, field="observation_date")
    cutoff = _as_aware(decision_time, field="decision_time")
    if day > cutoff.date():
        raise ObservationPopulationUnavailable(
            "BLOCKED",
            "future_observation_date_rejected "
            f"observation={day.isoformat()} decision_date={cutoff.date().isoformat()}",
        )

    calendar = (calendar_loader or load_accepted_calendar)(cutoff, policy)
    if calendar.evidence.usable_at > cutoff:
        raise ObservationPopulationUnavailable(
            "NOT_EVALUATED",
            "accepted_calendar_not_visible_at_decision_time",
        )
    try:
        if not calendar.is_open(day):
            raise ObservationPopulationUnavailable(
                "BLOCKED",
                f"observation_date_is_not_an_open_trading_day date={day.isoformat()}",
            )
    except CalendarTruthUnavailable as exc:
        raise ObservationPopulationUnavailable(exc.status, exc.reason) from exc

    kline_ref, traded = (nominal_kline_loader or load_accepted_nominal_kline_membership)(
        day, cutoff, policy
    )
    st_ref, st_members = (st_membership_loader or load_accepted_st_membership)(
        day, cutoff, policy
    )
    _prove_partition_usable(
        kline_ref,
        expected_dataset_id=NOMINAL_KLINE_DATASET_ID,
        observation_date=day,
        decision_time=cutoff,
    )
    _prove_partition_usable(
        st_ref,
        expected_dataset_id=ST_MEMBERSHIP_DATASET_ID,
        observation_date=day,
        decision_time=cutoff,
    )
    _prove_membership_parity(kline_ref, traded, source_label="nominal_kline")
    _prove_membership_parity(st_ref, st_members, source_label="stock_st")
    if not traded:
        raise ObservationPopulationUnavailable(
            "BLOCKED",
            "accepted_nominal_kline_partition_has_zero_rows "
            f"date={day.isoformat()}",
        )
    if not st_members:
        # Zero-row accepted ST is not proof of "no ST names"; that needs an
        # explicit empty-partition attestation (not yet defined). Fail closed.
        raise ObservationPopulationUnavailable(
            "BLOCKED",
            "accepted_stock_st_partition_has_zero_rows "
            f"date={day.isoformat()}",
        )

    eligible: list[str] = []
    excluded_st = 0
    excluded_board = 0
    for ts_code in sorted(traded):
        code = str(ts_code).strip()
        if code in st_members:
            excluded_st += 1
            continue
        if not _board_prefix_allowed(code, policy):
            excluded_board += 1
            continue
        eligible.append(code)

    return ObservationMembership(
        observation_date=day,
        decision_time=cutoff,
        ts_codes=tuple(eligible),
        calendar_generation_id=calendar.evidence.generation_id,
        calendar_content_hash=calendar.evidence.content_hash,
        nominal_kline=kline_ref,
        st_membership=st_ref,
        universe_policy_id=policy.policy_id,
        universe_policy_version=policy.policy_version,
        universe_policy_hash=policy.config_hash,
        excluded_st_count=excluded_st,
        excluded_board_count=excluded_board,
    )


def resolve_eligible_observation_date(
    decision_time: datetime,
    calendar: CalendarTruth,
) -> date:
    """Resolve the binding K+ST publishable observation date (no calendar-today).

    Uses accepted open sessions plus the typed ``availability_policy`` on the
    nominal OHLCV and stock_st domains.  The frontier is the earlier of the two
    eligible ends so population readiness never asks for an unpublished day.
    """

    from zoneinfo import ZoneInfo

    from services.data_sources.availability import resolve_availability_frontier
    from services.data_sources.nominal_ohlcv_schema import DOMAIN as DAILY_DOMAIN
    from services.data_sources.stock_st_schema import DOMAIN as ST_DOMAIN

    cutoff = _as_aware(decision_time, field="decision_time")
    now_local = cutoff.astimezone(ZoneInfo("Asia/Shanghai"))
    today = now_local.date()
    coverage_end = min(today, calendar.evidence.coverage_end)
    if coverage_end < calendar.evidence.coverage_start:
        raise ObservationPopulationUnavailable(
            "NOT_EVALUATED",
            "eligible_observation_date_outside_calendar_coverage "
            f"today={today.isoformat()} coverage="
            f"{calendar.evidence.coverage_start.isoformat()}.."
            f"{calendar.evidence.coverage_end.isoformat()}",
        )
    open_days = calendar.open_dates(calendar.evidence.coverage_start, coverage_end)
    trading_day_values = tuple(day.strftime("%Y%m%d") for day in open_days)
    if not trading_day_values:
        raise ObservationPopulationUnavailable(
            "NOT_EVALUATED",
            "eligible_observation_date_calendar_empty",
        )

    eligible_ends: list[str] = []
    for domain in (DAILY_DOMAIN, ST_DOMAIN):
        eligibility = resolve_availability_frontier(
            domain.availability_policy,
            now=now_local,
            trading_day_values=trading_day_values,
        )
        if eligibility.eligible_end is None:
            raise ObservationPopulationUnavailable(
                "NOT_EVALUATED",
                "no_eligible_observation_date "
                f"domain={domain.domain} reason={eligibility.reason}",
            )
        eligible_ends.append(eligibility.eligible_end)
    frontier = min(eligible_ends)
    return datetime.strptime(frontier, "%Y%m%d").date()


def evaluate_observation_population_readiness(
    policy: UniversePolicy,
    *,
    decision_time: datetime | None = None,
    observation_date: date | None = None,
    calendar_loader: CalendarLoader | None = None,
    nominal_kline_loader: PartitionLoader | None = None,
    st_membership_loader: PartitionLoader | None = None,
) -> ObservationPopulationReadiness:
    """Evaluate whether the three accepted sources can prove the daily gate.

    This is the honest live_readiness seam: it always runs the loaders.  Missing
    accepted K/ST writers currently yield ``NOT_EVALUATED``, never a false READY.

    When ``observation_date`` is omitted, the default is the eligible K+ST
    publication frontier from accepted calendar + typed availability policy —
    never a closed calendar-today (weekend/holiday) partition that cannot exist.
    """

    policy = _require_policy(policy)
    cutoff = decision_time or datetime.now(timezone.utc)
    if cutoff.tzinfo is None:
        cutoff = cutoff.replace(tzinfo=timezone.utc)
    day = observation_date
    reasons: list[str] = []
    status: ReadinessStatus = "READY"

    def _note(exc: ObservationPopulationUnavailable) -> None:
        nonlocal status
        reasons.append(f"{exc.status}:{exc.reason}")
        if status == "READY":
            status = exc.status
        elif status == "NOT_EVALUATED" and exc.status == "BLOCKED":
            # Prefer BLOCKED when any source is contradictory rather than absent.
            status = "BLOCKED"

    try:
        calendar = (calendar_loader or load_accepted_calendar)(cutoff, policy)
        if day is None:
            day = resolve_eligible_observation_date(cutoff, calendar)
        elif not calendar.is_open(day):
            _note(
                ObservationPopulationUnavailable(
                    "BLOCKED",
                    f"observation_date_is_not_an_open_trading_day date={day.isoformat()}",
                )
            )
    except CalendarTruthUnavailable as exc:
        _note(ObservationPopulationUnavailable(exc.status, exc.reason))
    except ObservationPopulationUnavailable as exc:
        _note(exc)

    if day is None:
        # Calendar/eligibility failed before a frontier could be named; keep
        # loaders fail-closed against the decision calendar date as last resort.
        day = cutoff.astimezone(timezone.utc).date()

    try:
        (nominal_kline_loader or load_accepted_nominal_kline_membership)(
            day, cutoff, policy
        )
    except ObservationPopulationUnavailable as exc:
        _note(exc)

    try:
        (st_membership_loader or load_accepted_st_membership)(day, cutoff, policy)
    except ObservationPopulationUnavailable as exc:
        _note(exc)

    if status == "READY" and not reasons:
        reasons = ("accepted_calendar_kline_st_sources_visible",)
    return ObservationPopulationReadiness(
        status=status,
        reasons=tuple(reasons),
        calendar_dataset_id=CALENDAR_DATASET_ID,
        nominal_kline_dataset_id=NOMINAL_KLINE_DATASET_ID,
        st_membership_dataset_id=ST_MEMBERSHIP_DATASET_ID,
        policy_id=policy.policy_id,
        policy_version=policy.policy_version,
        policy_hash=policy.config_hash,
        observation_date=day,
    )


def refuse_legacy_population_surface(name: str) -> None:
    """Hard wall against raw/dim/qfq substitutes for the formal gate."""

    raise ObservationPopulationUnavailable(
        "BLOCKED",
        f"legacy_population_surface_forbidden name={name!r}; "
        "require accepted calendar + nominal_ohlcv + stock_st partitions",
    )


__all__ = [
    "AcceptedPartitionRef",
    "NOMINAL_KLINE_DATASET_ID",
    "ObservationMembership",
    "ObservationPopulationReadiness",
    "ObservationPopulationUnavailable",
    "ST_MEMBERSHIP_DATASET_ID",
    "evaluate_observation_population_readiness",
    "load_accepted_calendar",
    "load_accepted_nominal_kline_membership",
    "load_accepted_st_membership",
    "resolve_eligible_observation_date",
    "refuse_legacy_population_surface",
    "resolve_traded_on_observation_date",
]
