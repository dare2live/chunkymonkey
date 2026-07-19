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
    """Read-only legacy vs project-universe breadth delta — never authorizes cutover."""

    trade_date: str
    legacy_adv_dec_ratio: float | None
    project_adv_dec_ratio: float | None
    ratio_delta: float | None
    ratios_match: bool
    cutover_allowed: bool
    issues: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "trade_date": self.trade_date,
            "legacy_adv_dec_ratio": self.legacy_adv_dec_ratio,
            "project_adv_dec_ratio": self.project_adv_dec_ratio,
            "ratio_delta": self.ratio_delta,
            "ratios_match": self.ratios_match,
            "cutover_allowed": self.cutover_allowed,
            "issues": list(self.issues),
        }


def compare_legacy_vs_project_universe_breadth(
    *,
    trade_date: str,
    legacy_adv_dec_ratio: float | None,
    project: ProjectUniverseBreadthReport,
) -> BreadthShadowCompareReport:
    """Shadow-compare raw/legacy ratio to project-universe breadth.

    Matching ratios alone never set ``cutover_allowed`` — serve cutover needs
    accepted live partitions + explicit gate evidence beyond this helper.
    """

    day = str(trade_date or "").replace("-", "")
    issues = [
        "breadth_shadow_compare_only",
        "cutover_requires_accepted_live_partitions_and_gate",
    ]
    proj = project.adv_dec_ratio
    if legacy_adv_dec_ratio is None or proj is None:
        issues.append("ratio_unavailable_for_compare")
        return BreadthShadowCompareReport(
            trade_date=day,
            legacy_adv_dec_ratio=legacy_adv_dec_ratio,
            project_adv_dec_ratio=proj,
            ratio_delta=None,
            ratios_match=False,
            cutover_allowed=False,
            issues=tuple(issues),
        )
    delta = float(legacy_adv_dec_ratio) - float(proj)
    match = abs(delta) <= 1e-9
    if not match:
        issues.append("legacy_raw_ratio_diverges_from_project_universe")
    return BreadthShadowCompareReport(
        trade_date=day,
        legacy_adv_dec_ratio=float(legacy_adv_dec_ratio),
        project_adv_dec_ratio=float(proj),
        ratio_delta=delta,
        ratios_match=match,
        cutover_allowed=False,
        issues=tuple(issues),
    )


__all__ = [
    "BreadthShadowCompareReport",
    "ProjectUniverseBreadthReport",
    "ProjectUniverseBreadthUnavailable",
    "compare_legacy_vs_project_universe_breadth",
    "compute_project_universe_breadth",
    "refuse_legacy_raw_daily_as_project_universe_breadth",
]
