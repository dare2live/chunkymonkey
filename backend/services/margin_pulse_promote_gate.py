"""Margin pulse promote gate — typed path toward rzrqye trust (F4 / 1c).

Serve cutover: when ``pulse_source_accepted`` is opt-in and accepted SSE+SZSE
rows exist, the pulse builder reads ``canonical_margin_exchange_daily`` (not
raw BSE-inclusive sum).  ``promote_allowed`` is a separate owner opt-in; both
are required for field status READY as ``external_aggregate``.

Fail closed when evidence is missing: stay UNTRUSTED with typed ``remaining``.
Never invent TRUSTED / project_universe_pit.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml

from services.market_pulse_shadow_reconcile import (
    CORE_MARGIN_VENUES,
    PulseShadowReconcileReport,
    PulseShadowVerdict,
)

PromoteGateStatus = Literal[
    "BLOCKED",
    "CRITERIA_PENDING",
    "SHADOW_EXTERNAL_HONEST",
    "PENDING_SERVE_CUTOVER",
    "READY_TO_PROMOTE",
    "PROMOTED",
]

_DEFAULT_CFG = (
    Path(__file__).resolve().parents[1] / "config" / "margin_pulse_promote.yaml"
)
# Test hook: production keeps default path.
_CONFIG_PATH: Path | None = None
_CONFIG_OVERRIDE: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class MarginPulsePromoteConfig:
    """Typed promote / serve-source policy (defaults fail closed)."""

    pulse_source_accepted: bool = False
    promote_allowed: bool = False
    contract_version: str = "3"
    raw: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> MarginPulsePromoteConfig:
        if not isinstance(raw, Mapping):
            raise ValueError("margin pulse promote config must be a mapping")
        section = raw.get("margin_pulse_promote")
        if section is None:
            section = raw
        if not isinstance(section, Mapping):
            raise ValueError("margin_pulse_promote must be a mapping")
        return cls(
            pulse_source_accepted=bool(section.get("pulse_source_accepted", False)),
            promote_allowed=bool(section.get("promote_allowed", False)),
            contract_version=str(section.get("contract_version") or "3").strip(),
            raw=dict(section),
        )


def load_margin_pulse_promote_config(
    path: Path | str | None = None,
    *,
    raw: Mapping[str, Any] | None = None,
) -> MarginPulsePromoteConfig:
    """Load promote config. Missing file / unset flags → fail closed (False)."""
    if raw is not None:
        return MarginPulsePromoteConfig.from_mapping(raw)
    if _CONFIG_OVERRIDE is not None:
        return MarginPulsePromoteConfig.from_mapping(_CONFIG_OVERRIDE)
    cfg_path = Path(path) if path is not None else (_CONFIG_PATH or _DEFAULT_CFG)
    if not cfg_path.is_file():
        return MarginPulsePromoteConfig()
    payload = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, Mapping):
        raise ValueError(f"margin pulse promote config must be a mapping: {cfg_path}")
    return MarginPulsePromoteConfig.from_mapping(payload)


@dataclass(frozen=True)
class MarginPulsePromoteGateReport:
    trade_date: str
    status: PromoteGateStatus
    population_kind: str
    product_trust_would_be: str
    criteria: dict[str, bool]
    remaining: tuple[str, ...]
    notes: tuple[str, ...]
    shadow_verdict: str | None
    accepted_rzrqye: float | None
    honest_external_rzrqye: float | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "trade_date": self.trade_date,
            "status": self.status,
            "population_kind": self.population_kind,
            "product_trust_would_be": self.product_trust_would_be,
            "criteria": dict(self.criteria),
            "remaining": list(self.remaining),
            "notes": list(self.notes),
            "shadow_verdict": self.shadow_verdict,
            "accepted_rzrqye": self.accepted_rzrqye,
            "honest_external_rzrqye": self.honest_external_rzrqye,
        }


def _accepted_balances(
    accepted_rows: Sequence[Mapping[str, Any]],
) -> dict[str, float]:
    out: dict[str, float] = {}
    for row in accepted_rows:
        venue = str(row.get("exchange_id") or "").upper()
        if not venue:
            continue
        raw = row.get("rzrqye")
        if raw is None:
            continue
        out[venue] = float(raw)
    return out


def evaluate_margin_pulse_promote_gate(
    trade_date: str,
    *,
    shadow: PulseShadowReconcileReport | Mapping[str, Any] | None,
    accepted_margin_rows: Sequence[Mapping[str, Any]] = (),
    pulse_source_accepted: bool = False,
    promote_allowed: bool = False,
) -> MarginPulsePromoteGateReport:
    """Evaluate promote gate for one observation date. Never invents TRUSTED."""

    day = str(trade_date or "").replace("-", "")
    notes: list[str] = [
        "rzrqye_stays_external_aggregate",
        "no_silent_product_thaw",
        "project_universe_pit_still_refused",
    ]
    if len(day) != 8 or not day.isdigit():
        return MarginPulsePromoteGateReport(
            trade_date=str(trade_date),
            status="BLOCKED",
            population_kind="external_aggregate",
            product_trust_would_be="UNTRUSTED",
            criteria={},
            remaining=("invalid_trade_date",),
            notes=tuple(notes + ["invalid_trade_date"]),
            shadow_verdict=None,
            accepted_rzrqye=None,
            honest_external_rzrqye=None,
        )

    if shadow is None:
        shadow_dict: dict[str, Any] = {}
        verdict = None
        honest = None
    elif isinstance(shadow, PulseShadowReconcileReport):
        shadow_dict = shadow.as_dict()
        verdict = shadow.verdict.value
        honest = shadow.honest_external_rzrqye
    else:
        shadow_dict = dict(shadow)
        verdict = shadow_dict.get("verdict")
        honest = shadow_dict.get("honest_external_rzrqye")

    balances = _accepted_balances(accepted_margin_rows)
    core_ok = CORE_MARGIN_VENUES <= set(balances)
    accepted_sum = (
        float(balances["SSE"] + balances["SZSE"]) if core_ok else None
    )
    if balances and not core_ok:
        notes.append(f"accepted_incomplete_core venues={sorted(balances)}")
    if "BSE" in balances:
        notes.append("accepted_includes_BSE_unexpected_for_v3")

    shadow_honest = verdict == PulseShadowVerdict.EXTERNAL_HONEST_SHADOW.value
    accepted_present = accepted_sum is not None
    # Accepted SSE+SZSE is the honest external_aggregate target (v3). Legacy raw
    # shadow may lag or include BSE; accepted-ready alone advances past
    # CRITERIA_PENDING without inventing TRUSTED.
    honest_enough = shadow_honest or accepted_present
    criteria = {
        "accepted_core_venues_present": accepted_present,
        "shadow_external_honest": shadow_honest,
        "accepted_or_shadow_honest": honest_enough,
        "pulse_source_accepted": bool(pulse_source_accepted),
        "promote_allowed": bool(promote_allowed),
        "cutover_allowed_false_in_shadow": not bool(
            shadow_dict.get("cutover_allowed", False)
        ),
    }

    remaining: list[str] = []
    if not criteria["accepted_core_venues_present"]:
        remaining.append("need_accepted_sse_szse_for_day")
    if not criteria["accepted_or_shadow_honest"]:
        remaining.append("need_accepted_or_shadow_external_honest")
    if not criteria["pulse_source_accepted"]:
        remaining.append("need_pulse_serve_accepted_margin_not_raw_bse")
    if not criteria["promote_allowed"]:
        remaining.append("need_explicit_promote_allowed_config")

    all_ready = (
        accepted_present
        and honest_enough
        and bool(pulse_source_accepted)
        and bool(promote_allowed)
    )

    if not accepted_present and not shadow_honest:
        status: PromoteGateStatus = "CRITERIA_PENDING"
    elif honest_enough and not pulse_source_accepted:
        status = "PENDING_SERVE_CUTOVER"
        if accepted_present and not shadow_honest:
            notes.append("accepted_v3_ready_legacy_raw_shadow_not_honest")
    elif all_ready:
        # Cutover knife consumed: READY as external_aggregate (not TRUSTED /
        # project_universe_pit). Fail closed if a later day loses accepted rows.
        status = "PROMOTED"
        notes.append("promoted_external_aggregate_sse_szse_accepted")
    elif (
        accepted_present
        and pulse_source_accepted
        and not promote_allowed
    ):
        status = "READY_TO_PROMOTE"
        notes.append("serve_cutover_on_awaiting_explicit_promote_allowed")
    elif shadow_honest:
        status = "SHADOW_EXTERNAL_HONEST"
    else:
        status = "CRITERIA_PENDING"

    if status == "PROMOTED":
        product_trust = "READY"
    else:
        product_trust = "UNTRUSTED"

    return MarginPulsePromoteGateReport(
        trade_date=day,
        status=status,
        population_kind="external_aggregate",
        product_trust_would_be=product_trust,
        criteria=criteria,
        remaining=tuple(remaining),
        notes=tuple(notes),
        shadow_verdict=str(verdict) if verdict is not None else None,
        accepted_rzrqye=accepted_sum,
        honest_external_rzrqye=float(honest) if honest is not None else None,
    )


__all__ = [
    "MarginPulsePromoteConfig",
    "MarginPulsePromoteGateReport",
    "PromoteGateStatus",
    "evaluate_margin_pulse_promote_gate",
    "load_margin_pulse_promote_config",
]
