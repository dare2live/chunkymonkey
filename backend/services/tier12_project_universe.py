"""Load project-universe nominal bars for Phase C Tier1/2 full-universe accept.

Membership comes only from ``resolve_traded_on_observation_date`` (accepted
calendar + nominal K + ST − board filter). Bars are read from accepted
canonical nominal OHLCV. Missing decision-day bars or empty lookback windows
are recorded as coverage exclusions — never silently padded.

Does not accept-publish, cut over consumers, or claim Phase C complete.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping, Sequence
from zoneinfo import ZoneInfo

from services.data_access.resolver import connect_ro
from services.data_sources.nominal_ohlcv_schema import (
    CANONICAL_TABLE,
    DATASET_ID,
)
from services.data_sources.observation_population import (
    ObservationMembership,
    ObservationPopulationUnavailable,
    resolve_traded_on_observation_date,
)
from services.tier12_nominal_canary import (
    CONTRACTUAL_AVAILABLE_AT_POLICY,
    RAW_ROW_AVAILABLE_AT_NOTE,
    AvailableAtMode,
    lookback_trading_days as _lookback_trading_days,
    require_accepted_nominal_partition,
    timed_inputs_from_nominal_rows,
)
from services.tier12_publish_writer import TimedInput
from services.universe import UniversePolicy, load_universe_policy

CST = ZoneInfo("Asia/Shanghai")


def _compact_day(value: Any) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())[:8]


@dataclass(frozen=True)
class CoverageExclusion:
    ts_code: str
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return {"ts_code": self.ts_code, "reason": self.reason}


@dataclass(frozen=True)
class ProjectUniverseNominalLoad:
    decision_date: str
    lookback_days: tuple[str, ...]
    membership_codes: tuple[str, ...]
    membership_size: int
    rows: tuple[dict[str, Any], ...]
    codes_with_decision_bar: tuple[str, ...]
    exclusions: tuple[CoverageExclusion, ...]
    available_at_mode: AvailableAtMode
    available_at_policy: str
    universe_policy_id: str
    universe_policy_hash: str
    population_kind: str
    decision_time: str
    notes: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": "tier12_project_universe_nominal_load",
            "decision_date": self.decision_date,
            "lookback_days": list(self.lookback_days),
            "membership_size": self.membership_size,
            "membership_codes_sample": list(self.membership_codes[:8]),
            "row_count": len(self.rows),
            "codes_with_decision_bar_count": len(self.codes_with_decision_bar),
            "coverage_excluded_count": len(self.exclusions),
            "exclusions_sample": [e.as_dict() for e in self.exclusions[:20]],
            "available_at_mode": self.available_at_mode,
            "available_at_policy": self.available_at_policy,
            "universe_policy_id": self.universe_policy_id,
            "universe_policy_hash": self.universe_policy_hash,
            "population_kind": self.population_kind,
            "decision_time": self.decision_time,
            "notes": list(self.notes),
            "dataset_id": DATASET_ID,
            "canonical_table": CANONICAL_TABLE,
        }

    def universe_attestation(self) -> dict[str, Any]:
        return {
            "population_kind": self.population_kind,
            "membership_size": self.membership_size,
            "universe_policy_hash": self.universe_policy_hash,
            "coverage_excluded_count": len(self.exclusions),
            "universe_policy_id": self.universe_policy_id,
            "decision_time": self.decision_time,
        }


def resolve_project_universe_membership(
    decision_date: str,
    *,
    decision_time: datetime | None = None,
    policy: UniversePolicy | None = None,
) -> ObservationMembership:
    """Fail-closed membership from accepted Tier0 facts only."""

    day = _compact_day(decision_date)
    if len(day) != 8:
        raise ValueError(f"invalid decision_date: {decision_date!r}")
    pol = policy or load_universe_policy()
    cutoff = decision_time or datetime.now(CST)
    if cutoff.tzinfo is None:
        cutoff = cutoff.replace(tzinfo=CST)
    try:
        mem = resolve_traded_on_observation_date(day, cutoff, pol)
    except ObservationPopulationUnavailable as exc:
        raise ValueError(
            f"project_universe_membership_unavailable status={exc.status} "
            f"reason={exc.reason}"
        ) from exc
    if not mem.ts_codes:
        raise ValueError(f"project_universe_membership_empty day={day}")
    return mem


def _fetch_nominal_rows(
    conn,
    *,
    days: Sequence[str],
    codes: Sequence[str],
) -> list[dict[str, Any]]:
    if not days or not codes:
        return []
    placeholders_d = ", ".join(["?"] * len(days))
    placeholders_c = ", ".join(["?"] * len(codes))
    sql = f"""
        SELECT ts_code,
               replace(CAST(trade_date AS VARCHAR), '-', '') AS trade_date,
               open, high, low, close, pct_chg, vol, amount,
               CAST(available_at AS VARCHAR) AS available_at
          FROM {CANONICAL_TABLE}
         WHERE replace(CAST(trade_date AS VARCHAR), '-', '') IN ({placeholders_d})
           AND ts_code IN ({placeholders_c})
         ORDER BY trade_date, ts_code
    """
    fetched = conn.execute(sql, list(days) + list(codes)).fetchall()
    rows: list[dict[str, Any]] = []
    for r in fetched:
        if hasattr(r, "keys"):
            rows.append(
                {
                    "ts_code": str(r["ts_code"]),
                    "trade_date": _compact_day(r["trade_date"]),
                    "open": r["open"],
                    "high": r["high"],
                    "low": r["low"],
                    "close": r["close"],
                    "pct_chg": r["pct_chg"],
                    "vol": r["vol"],
                    "amount": r["amount"],
                    "available_at": str(r["available_at"] or ""),
                }
            )
        else:
            rows.append(
                {
                    "ts_code": str(r[0]),
                    "trade_date": _compact_day(r[1]),
                    "open": r[2],
                    "high": r[3],
                    "low": r[4],
                    "close": r[5],
                    "pct_chg": r[6],
                    "vol": r[7],
                    "amount": r[8],
                    "available_at": str(r[9] or ""),
                }
            )
    return rows


def _coverage_exclusions(
    membership_codes: Sequence[str],
    rows: Sequence[Mapping[str, Any]],
    decision_date: str,
) -> tuple[tuple[str, ...], tuple[CoverageExclusion, ...]]:
    """Codes without a decision-day bar are excluded with an explicit reason."""

    day = _compact_day(decision_date)
    with_day: set[str] = set()
    for row in rows:
        if _compact_day(row.get("trade_date")) == day:
            with_day.add(str(row.get("ts_code") or "").strip())
    exclusions: list[CoverageExclusion] = []
    kept: list[str] = []
    for code in membership_codes:
        c = str(code).strip()
        if c in with_day:
            kept.append(c)
        else:
            exclusions.append(
                CoverageExclusion(ts_code=c, reason="missing_decision_day_bar")
            )
    return tuple(kept), tuple(exclusions)


def load_accepted_nominal_project_universe(
    decision_date: str,
    *,
    lookback_trading_days: int = 5,
    decision_time: datetime | None = None,
    policy: UniversePolicy | None = None,
    available_at_mode: AvailableAtMode = "contractual",
    conn=None,
) -> ProjectUniverseNominalLoad:
    """Read-only full-universe nominal load for Tier1/2 writer inputs."""

    day = _compact_day(decision_date)
    mem = resolve_project_universe_membership(
        day, decision_time=decision_time, policy=policy
    )
    own_conn = conn is None
    db = conn or connect_ro("tushare_raw")
    try:
        require_accepted_nominal_partition(db, day)
        days = _lookback_trading_days(db, day, lookback_trading_days)
        rows = _fetch_nominal_rows(db, days=days, codes=mem.ts_codes)
    finally:
        if own_conn:
            db.close()

    if not rows:
        raise ValueError(
            f"project_universe_zero_nominal_rows day={day} "
            f"membership={len(mem.ts_codes)}"
        )

    kept_codes, exclusions = _coverage_exclusions(mem.ts_codes, rows, day)
    # Drop bars for excluded codes so writer cannot invent decision-day rows.
    excluded_set = {e.ts_code for e in exclusions}
    if excluded_set:
        rows = [r for r in rows if str(r.get("ts_code") or "") not in excluded_set]

    notes = (
        "phase_c_project_universe_nominal",
        "population_kind=project_universe_pit",
        "not_canary_scale",
        "not_accepted_tier12_publish",
        "not_strategy_release",
        RAW_ROW_AVAILABLE_AT_NOTE,
    )
    policy_token = (
        CONTRACTUAL_AVAILABLE_AT_POLICY
        if available_at_mode == "contractual"
        else "raw_row_available_at"
    )
    return ProjectUniverseNominalLoad(
        decision_date=day,
        lookback_days=tuple(days),
        membership_codes=tuple(mem.ts_codes),
        membership_size=len(mem.ts_codes),
        rows=tuple(rows),
        codes_with_decision_bar=kept_codes,
        exclusions=exclusions,
        available_at_mode=available_at_mode,
        available_at_policy=policy_token,
        universe_policy_id=mem.universe_policy_id,
        universe_policy_hash=mem.universe_policy_hash,
        population_kind="project_universe_pit",
        decision_time=mem.decision_time.isoformat(),
        notes=notes,
    )


def timed_inputs_for_project_universe(
    load: ProjectUniverseNominalLoad,
) -> list[TimedInput]:
    """Map a project-universe load to TimedInput with contractual available_at."""

    return timed_inputs_from_nominal_rows(
        load.rows, available_at_mode=load.available_at_mode
    )


__all__ = [
    "CoverageExclusion",
    "ProjectUniverseNominalLoad",
    "load_accepted_nominal_project_universe",
    "resolve_project_universe_membership",
    "timed_inputs_for_project_universe",
]
