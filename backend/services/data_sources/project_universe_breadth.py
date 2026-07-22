"""B-pit: project-universe breadth from traded_on_observation_date membership.

Computes advance/decline counts only over accepted observation membership and
explicit per-security nominal bars.  Does not read ``raw_tushare_daily``, does
not rewrite market_pulse marts, and does not authorize consumer cutover.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from services.data_sources.observation_population import ObservationMembership

PopulationKind = Literal["project_universe_pit"]


class ProjectUniverseBreadthUnavailable(RuntimeError):
    """Breadth cannot be proved from project-universe membership + bars."""


@dataclass(frozen=True)
class ProjectUniverseBreadthReport:
    observation_date: str
    population_kind: PopulationKind
    adv_n: int
    dec_n: int
    flat_n: int
    adv_dec_ratio: float | None
    row_count_used: int
    ignored_outside_membership: int
    universe_policy_hash: str
    nominal_kline_content_hash: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "observation_date": self.observation_date,
            "population_kind": self.population_kind,
            "adv_n": self.adv_n,
            "dec_n": self.dec_n,
            "flat_n": self.flat_n,
            "adv_dec_ratio": self.adv_dec_ratio,
            "row_count_used": self.row_count_used,
            "ignored_outside_membership": self.ignored_outside_membership,
            "universe_policy_hash": self.universe_policy_hash,
            "nominal_kline_content_hash": self.nominal_kline_content_hash,
        }


def _pct(row: Mapping[str, Any]) -> float | None:
    if "pct_chg" in row and row["pct_chg"] is not None:
        return float(row["pct_chg"])
    close = row.get("close")
    pre = row.get("pre_close")
    if close is None or pre is None:
        return None
    pre_f = float(pre)
    if pre_f == 0.0:
        return None
    return (float(close) / pre_f - 1.0) * 100.0


def compute_project_universe_breadth(
    membership: ObservationMembership,
    *,
    rows: Sequence[Mapping[str, Any]],
) -> ProjectUniverseBreadthReport:
    """Compute breadth strictly inside ``membership.ts_codes``.

    Every membership code must have exactly one usable bar.  Rows outside the
    membership are ignored and counted, never used.
    """

    codes = tuple(membership.ts_codes)
    if not codes:
        raise ProjectUniverseBreadthUnavailable("empty_membership")

    wanted = set(codes)
    by_code: dict[str, float] = {}
    ignored = 0
    for row in rows:
        code = str(row.get("ts_code") or "").strip()
        if not code:
            continue
        if code not in wanted:
            ignored += 1
            continue
        pct = _pct(row)
        if pct is None:
            raise ProjectUniverseBreadthUnavailable(
                f"membership_bar_missing_pct_chg ts_code={code}"
            )
        if code in by_code:
            raise ProjectUniverseBreadthUnavailable(
                f"duplicate_membership_bar ts_code={code}"
            )
        by_code[code] = pct

    missing = sorted(wanted - set(by_code))
    if missing:
        raise ProjectUniverseBreadthUnavailable(
            "incomplete_membership_bars missing="
            + ",".join(missing[:8])
            + (f"+{len(missing) - 8}" if len(missing) > 8 else "")
        )

    adv = dec = flat = 0
    for pct in by_code.values():
        if pct > 0:
            adv += 1
        elif pct < 0:
            dec += 1
        else:
            flat += 1

    ratio = (float(adv) / float(dec)) if dec else None
    day = membership.observation_date.strftime("%Y%m%d")
    return ProjectUniverseBreadthReport(
        observation_date=day,
        population_kind="project_universe_pit",
        adv_n=adv,
        dec_n=dec,
        flat_n=flat,
        adv_dec_ratio=ratio,
        row_count_used=len(by_code),
        ignored_outside_membership=ignored,
        universe_policy_hash=membership.universe_policy_hash,
        nominal_kline_content_hash=membership.nominal_kline.content_hash,
    )


def refuse_legacy_raw_daily_as_project_universe_breadth(
    claim: Mapping[str, Any] | str,
) -> None:
    """Hard wall: raw daily breadth cannot satisfy project_universe_pit."""

    label = (
        str(claim.get("population_kind") or claim.get("kind") or claim)
        if isinstance(claim, Mapping)
        else str(claim)
    )
    if label == "project_universe_pit":
        raise RuntimeError(
            "legacy_raw_daily_breadth_cannot_satisfy_project_universe_pit; "
            "use compute_project_universe_breadth with observation membership"
        )


@dataclass(frozen=True)
class BreadthShadowCompareReport:
    """Read-only baseline vs project-universe breadth — never authorizes cutover.

    MATCH baseline for B-pit shadow is ``membership_restricted_proxy`` (same
    bars filtered to ``ObservationMembership``), not full unfiltered canonical.
    Unfiltered vs PIT remains a diagnostic semantic delta only.
    """

    trade_date: str
    baseline_kind: str
    baseline_adv_dec_ratio: float | None
    project_adv_dec_ratio: float | None
    ratio_delta: float | None
    ratios_match: bool
    cutover_allowed: bool
    issues: tuple[str, ...]

    @property
    def legacy_adv_dec_ratio(self) -> float | None:
        """Compat alias — baseline ratio under the active shadow contract."""
        return self.baseline_adv_dec_ratio

    def as_dict(self) -> dict[str, Any]:
        return {
            "trade_date": self.trade_date,
            "baseline_kind": self.baseline_kind,
            "baseline_adv_dec_ratio": self.baseline_adv_dec_ratio,
            "legacy_adv_dec_ratio": self.baseline_adv_dec_ratio,
            "project_adv_dec_ratio": self.project_adv_dec_ratio,
            "ratio_delta": self.ratio_delta,
            "ratios_match": self.ratios_match,
            "cutover_allowed": self.cutover_allowed,
            "issues": list(self.issues),
        }


def compare_baseline_vs_project_universe_breadth(
    *,
    trade_date: str,
    baseline_adv_dec_ratio: float | None,
    project: ProjectUniverseBreadthReport,
    baseline_kind: str = "membership_restricted_proxy",
) -> BreadthShadowCompareReport:
    """Shadow-compare a typed baseline ratio to project-universe breadth.

    Matching ratios alone never set ``cutover_allowed`` — serve cutover needs
    accepted live partitions + explicit gate evidence beyond this helper.
    """

    day = str(trade_date or "").replace("-", "")
    kind = str(baseline_kind or "membership_restricted_proxy")
    issues = [
        "breadth_shadow_compare_only",
        "cutover_requires_accepted_live_partitions_and_gate",
        f"match_baseline={kind}",
    ]
    proj = project.adv_dec_ratio
    if baseline_adv_dec_ratio is None or proj is None:
        issues.append("ratio_unavailable_for_compare")
        return BreadthShadowCompareReport(
            trade_date=day,
            baseline_kind=kind,
            baseline_adv_dec_ratio=baseline_adv_dec_ratio,
            project_adv_dec_ratio=proj,
            ratio_delta=None,
            ratios_match=False,
            cutover_allowed=False,
            issues=tuple(issues),
        )
    delta = float(baseline_adv_dec_ratio) - float(proj)
    match = abs(delta) <= 1e-9
    if not match:
        issues.append("baseline_ratio_diverges_from_project_universe")
        if kind in {"legacy_raw", "accepted_canonical_unfiltered_proxy"}:
            issues.append("legacy_raw_ratio_diverges_from_project_universe")
    return BreadthShadowCompareReport(
        trade_date=day,
        baseline_kind=kind,
        baseline_adv_dec_ratio=float(baseline_adv_dec_ratio),
        project_adv_dec_ratio=float(proj),
        ratio_delta=delta,
        ratios_match=match,
        cutover_allowed=False,
        issues=tuple(issues),
    )


def compare_legacy_vs_project_universe_breadth(
    *,
    trade_date: str,
    legacy_adv_dec_ratio: float | None,
    project: ProjectUniverseBreadthReport,
) -> BreadthShadowCompareReport:
    """Compat wrapper: treat caller ratio as an untyped legacy/unfiltered baseline."""

    return compare_baseline_vs_project_universe_breadth(
        trade_date=trade_date,
        baseline_adv_dec_ratio=legacy_adv_dec_ratio,
        project=project,
        baseline_kind="legacy_raw",
    )


@dataclass(frozen=True)
class UnfilteredBreadthCounts:
    """Accepted-canonical unfiltered proxy (not project_universe_pit)."""

    adv_n: int
    dec_n: int
    flat_n: int
    row_count_used: int
    adv_dec_ratio: float | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "population_kind": "accepted_canonical_unfiltered_proxy",
            "adv_n": self.adv_n,
            "dec_n": self.dec_n,
            "flat_n": self.flat_n,
            "row_count_used": self.row_count_used,
            "adv_dec_ratio": self.adv_dec_ratio,
        }


def unfiltered_breadth_from_rows(
    rows: Sequence[Mapping[str, Any]],
) -> UnfilteredBreadthCounts:
    """Count adv/dec over all rows with usable pct — no universe filter."""

    adv = dec = flat = used = 0
    for row in rows:
        pct = _pct(row)
        if pct is None:
            continue
        used += 1
        if pct > 0:
            adv += 1
        elif pct < 0:
            dec += 1
        else:
            flat += 1
    ratio = (float(adv) / float(dec)) if dec else None
    return UnfilteredBreadthCounts(
        adv_n=adv,
        dec_n=dec,
        flat_n=flat,
        row_count_used=used,
        adv_dec_ratio=ratio,
    )


def _rows_in_membership(
    membership: ObservationMembership,
    rows: Sequence[Mapping[str, Any]],
) -> list[Mapping[str, Any]]:
    wanted = set(membership.ts_codes)
    out: list[Mapping[str, Any]] = []
    for row in rows:
        code = str(row.get("ts_code") or "").strip()
        if code and code in wanted:
            out.append(row)
    return out


@dataclass(frozen=True)
class BreadthShadowDayMeasure:
    """One-day shadow: MATCH = project ≡ membership-restricted proxy.

    ``unfiltered`` is diagnostic only — BSE/excluded-board (and off-whitelist)
    rows make it diverge from project_universe_pit by definition; ST A-shares
    belong in the project whitelist and are not a semantic-delta driver.
    """

    trade_date: str
    project: ProjectUniverseBreadthReport
    membership_proxy: UnfilteredBreadthCounts
    unfiltered: UnfilteredBreadthCounts
    compare: BreadthShadowCompareReport
    semantic_delta_vs_unfiltered: float | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "trade_date": self.trade_date,
            "match_baseline_kind": "membership_restricted_proxy",
            "project": self.project.as_dict(),
            "membership_proxy": {
                **self.membership_proxy.as_dict(),
                "population_kind": "membership_restricted_proxy",
            },
            "unfiltered": self.unfiltered.as_dict(),
            "semantic_delta_vs_unfiltered": self.semantic_delta_vs_unfiltered,
            "compare": self.compare.as_dict(),
        }


def measure_breadth_shadow_day(
    membership: ObservationMembership,
    *,
    rows: Sequence[Mapping[str, Any]],
) -> BreadthShadowDayMeasure:
    """One-day PIT self-consistency shadow (cutover stays false).

    MATCH compares project_universe_pit to an independent unfiltered count over
    the same membership-restricted bars. Full accepted-canonical unfiltered is
    retained only as ``semantic_delta_vs_unfiltered`` (expected to differ).
    """

    project = compute_project_universe_breadth(membership, rows=rows)
    membership_rows = _rows_in_membership(membership, rows)
    membership_proxy = unfiltered_breadth_from_rows(membership_rows)
    unfiltered = unfiltered_breadth_from_rows(rows)
    compare = compare_baseline_vs_project_universe_breadth(
        trade_date=project.observation_date,
        baseline_adv_dec_ratio=membership_proxy.adv_dec_ratio,
        project=project,
        baseline_kind="membership_restricted_proxy",
    )
    semantic_delta: float | None = None
    if unfiltered.adv_dec_ratio is not None and project.adv_dec_ratio is not None:
        semantic_delta = float(unfiltered.adv_dec_ratio) - float(project.adv_dec_ratio)
    return BreadthShadowDayMeasure(
        trade_date=project.observation_date,
        project=project,
        membership_proxy=membership_proxy,
        unfiltered=unfiltered,
        compare=compare,
        semantic_delta_vs_unfiltered=semantic_delta,
    )


@dataclass(frozen=True)
class BreadthShadowWindowReport:
    """Window aggregate — never authorizes mart cutover from shadow alone."""

    window_start: str
    window_end: str
    day_count: int
    match_day_count: int
    diverge_day_count: int
    error_day_count: int
    ratios_match_all: bool
    cutover_allowed: bool
    mean_abs_ratio_delta: float | None
    max_abs_ratio_delta: float | None
    frontier_day: str | None
    issues: tuple[str, ...]
    days: tuple[dict[str, Any], ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": "b_pit_breadth_shadow_window",
            "window_start": self.window_start,
            "window_end": self.window_end,
            "day_count": self.day_count,
            "match_day_count": self.match_day_count,
            "diverge_day_count": self.diverge_day_count,
            "error_day_count": self.error_day_count,
            "ratios_match_all": self.ratios_match_all,
            "cutover_allowed": self.cutover_allowed,
            "mean_abs_ratio_delta": self.mean_abs_ratio_delta,
            "max_abs_ratio_delta": self.max_abs_ratio_delta,
            "frontier_day": self.frontier_day,
            "issues": list(self.issues),
            "days": list(self.days),
        }


def aggregate_breadth_shadow_window(
    measures: Sequence[BreadthShadowDayMeasure],
    *,
    errors: Sequence[Mapping[str, Any]] = (),
) -> BreadthShadowWindowReport:
    """Aggregate day measures; ``cutover_allowed`` stays false even on full MATCH."""

    issues = [
        "breadth_shadow_window_remeasure_only",
        "cutover_requires_accepted_live_partitions_and_gate",
        "match_alone_insufficient_for_cutover",
    ]
    err_list = tuple(dict(e) for e in errors)
    if not measures and not err_list:
        issues.append("empty_window")
        return BreadthShadowWindowReport(
            window_start="",
            window_end="",
            day_count=0,
            match_day_count=0,
            diverge_day_count=0,
            error_day_count=0,
            ratios_match_all=False,
            cutover_allowed=False,
            mean_abs_ratio_delta=None,
            max_abs_ratio_delta=None,
            frontier_day=None,
            issues=tuple(issues),
            days=(),
        )

    match_n = sum(1 for m in measures if m.compare.ratios_match)
    diverge_n = sum(1 for m in measures if not m.compare.ratios_match)
    deltas = [
        abs(float(m.compare.ratio_delta))
        for m in measures
        if m.compare.ratio_delta is not None
    ]
    days_sorted = sorted(measures, key=lambda m: m.trade_date)
    start = days_sorted[0].trade_date if days_sorted else ""
    end = days_sorted[-1].trade_date if days_sorted else ""
    if err_list:
        err_days = sorted(
            str(e.get("trade_date") or "") for e in err_list if e.get("trade_date")
        )
        if err_days:
            start = min(x for x in (start, err_days[0]) if x) if start else err_days[0]
            end = max(x for x in (end, err_days[-1]) if x) if end else err_days[-1]
    ratios_match_all = bool(measures) and diverge_n == 0 and not err_list
    if not ratios_match_all:
        issues.append("window_ratios_diverge_or_errors")
    day_dicts: list[dict[str, Any]] = [m.as_dict() for m in days_sorted]
    day_dicts.extend(dict(e) for e in err_list)
    day_dicts.sort(key=lambda d: str(d.get("trade_date") or ""))
    return BreadthShadowWindowReport(
        window_start=start,
        window_end=end,
        day_count=len(measures) + len(err_list),
        match_day_count=match_n,
        diverge_day_count=diverge_n,
        error_day_count=len(err_list),
        ratios_match_all=ratios_match_all,
        cutover_allowed=False,
        mean_abs_ratio_delta=(
            (sum(deltas) / float(len(deltas))) if deltas else None
        ),
        max_abs_ratio_delta=max(deltas) if deltas else None,
        frontier_day=end or None,
        issues=tuple(issues),
        days=tuple(day_dicts),
    )


__all__ = [
    "BreadthShadowCompareReport",
    "BreadthShadowDayMeasure",
    "BreadthShadowWindowReport",
    "ProjectUniverseBreadthReport",
    "ProjectUniverseBreadthUnavailable",
    "UnfilteredBreadthCounts",
    "aggregate_breadth_shadow_window",
    "compare_baseline_vs_project_universe_breadth",
    "compare_legacy_vs_project_universe_breadth",
    "compute_project_universe_breadth",
    "measure_breadth_shadow_day",
    "refuse_legacy_raw_daily_as_project_universe_breadth",
    "unfiltered_breadth_from_rows",
]
