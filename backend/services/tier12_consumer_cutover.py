"""Phase C Tier1/2 explicit consumer cutover gate (fail-closed).

Research/UI must call ``resolve_tier12_production_read`` (which always invokes
``resolve_tier12_consumer_cutover``) before treating an accepted partition as
production truth. Default config keeps ``cutover_allowed=false`` so consumers
stay on the legacy/scaffold path.

Enabling cutover requires all of:
- typed config explicit opt-in (``consumer_cutover.cutover_allowed=true``);
- accepted publish for the day/scope with ``published=true`` / ``ACCEPTED``;
- matching ``definition_version`` + ``config_hash``;
- non-canary accept, OR explicit ``acknowledge_canary_scope`` that forbids
  claiming project-universe.

Silent reads of ``accepted_*.json`` as production truth are rejected.
Wired consumers:
- ``institution_follow_b1_measure.load_stock_state_by_day``
- pulse/UI via ``market_pulse_tier12_read`` (sentiment attestation + drill form)
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Mapping

import yaml

from services.tier12_publish_accept import Tier12AcceptedPublish

_DEFAULT_CFG = Path(__file__).resolve().parents[1] / "config" / "tier12_publish.yaml"

CutoverSource = Literal["legacy_scaffold", "accepted_partition"]
CutoverStatus = Literal["LEGACY", "BLOCKED", "CANARY_SCOPED", "ACCEPTED_CUTOVER"]

_CANARY_NOTES = frozenset(
    {
        "not_full_universe",
        "canary_or_fixture_scale_ok",
    }
)
_PROJECT_UNIVERSE_NOTES = frozenset(
    {
        "project_universe_scope",
        "full_universe_attested",
        "full_universe_attested_fixture",
    }
)


class Tier12ConsumerCutoverError(ValueError):
    """Consumer cutover / production-truth load rejected (fail closed)."""


def _compact_day(value: Any) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())[:8]


@dataclass(frozen=True)
class Tier12ConsumerCutoverConfig:
    """Typed consumer cutover policy (defaults fail closed)."""

    cutover_allowed: bool = False
    expected_definition_version: str = ""
    expected_config_hash: str = ""
    acknowledge_canary_scope: bool = False
    claim_project_universe: bool = False
    artifact_dir: str = "data/lineage/tier12_publish_batches"
    raw: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> Tier12ConsumerCutoverConfig:
        if not isinstance(raw, Mapping):
            raise ValueError("consumer cutover config must be a mapping")
        section = raw.get("consumer_cutover")
        if section is None:
            # Fall back to publish.allow_consumer_cutover hard-gate default.
            publish = raw.get("publish") or {}
            section = {
                "cutover_allowed": bool(publish.get("allow_consumer_cutover", False)),
            }
        if not isinstance(section, Mapping):
            raise ValueError("consumer_cutover must be a mapping")
        return cls(
            cutover_allowed=bool(section.get("cutover_allowed", False)),
            expected_definition_version=str(
                section.get("expected_definition_version") or ""
            ).strip(),
            expected_config_hash=str(section.get("expected_config_hash") or "").strip(),
            acknowledge_canary_scope=bool(
                section.get("acknowledge_canary_scope", False)
            ),
            claim_project_universe=bool(section.get("claim_project_universe", False)),
            artifact_dir=str(
                section.get("artifact_dir")
                or (raw.get("publish") or {}).get("artifact_dir")
                or "data/lineage/tier12_publish_batches"
            ),
            raw=dict(section),
        )


@dataclass(frozen=True)
class Tier12ConsumerCutoverDecision:
    """Resolved consumer read decision for one decision_date."""

    decision_date: str
    cutover_allowed: bool
    source: CutoverSource
    status: CutoverStatus
    claim_project_universe: bool
    reasons: tuple[str, ...]
    notes: tuple[str, ...]
    accepted_payload: Mapping[str, Any] | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": "tier12_consumer_cutover_decision",
            "decision_date": self.decision_date,
            "cutover_allowed": self.cutover_allowed,
            "source": self.source,
            "status": self.status,
            "claim_project_universe": self.claim_project_universe,
            "reasons": list(self.reasons),
            "notes": list(self.notes),
            "accepted_payload": (
                dict(self.accepted_payload) if self.accepted_payload is not None else None
            ),
        }


def load_tier12_consumer_cutover_config(
    path: str | Path | None = None,
) -> Tier12ConsumerCutoverConfig:
    cfg_path = Path(path) if path is not None else _DEFAULT_CFG
    raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    if not isinstance(raw, Mapping):
        raise ValueError("tier12 publish config root must be a mapping")
    return Tier12ConsumerCutoverConfig.from_mapping(raw)


def _as_accepted_mapping(
    accepted: Tier12AcceptedPublish | Mapping[str, Any] | Path | str | None,
) -> Mapping[str, Any] | None:
    if accepted is None:
        return None
    if isinstance(accepted, Tier12AcceptedPublish):
        return accepted.as_dict()
    if isinstance(accepted, Mapping):
        return dict(accepted)
    path = Path(accepted)
    if not path.is_file():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise Tier12ConsumerCutoverError("accepted_artifact_not_mapping")
    return dict(payload)


def _resolve_artifact_root(
    config: Tier12ConsumerCutoverConfig,
    artifact_root: Path | None,
) -> Path:
    if artifact_root is not None:
        root = Path(artifact_root)
        return root if root.is_absolute() else Path(__file__).resolve().parents[2] / root
    root = Path(config.artifact_dir)
    if root.is_absolute():
        return root
    return Path(__file__).resolve().parents[2] / root


def _load_accepted_for_day(
    day: str,
    *,
    config: Tier12ConsumerCutoverConfig,
    accepted: Tier12AcceptedPublish | Mapping[str, Any] | Path | str | None,
    artifact_root: Path | None,
) -> Mapping[str, Any] | None:
    mapped = _as_accepted_mapping(accepted)
    if mapped is not None:
        return mapped
    root = _resolve_artifact_root(config, artifact_root)
    path = root / f"accepted_{day}.json"
    if not path.is_file():
        return None
    return _as_accepted_mapping(path)


def _is_canary_accept(payload: Mapping[str, Any]) -> bool:
    notes = {str(n) for n in (payload.get("notes") or ())}
    # Canary notes always win — forged project_universe scope cannot clear them.
    if notes & _CANARY_NOTES:
        return True
    scope = str(payload.get("publish_scope") or "").strip()
    if scope == "project_universe" or notes & _PROJECT_UNIVERSE_NOTES:
        return False
    if scope == "canary":
        return True
    # Defensive: tiny row counts without full-universe attestation stay canary.
    try:
        rows = int(payload.get("stock_row_count") or 0)
    except (TypeError, ValueError):
        rows = 0
    if rows > 0 and rows < 100:
        return True
    return False


def _legacy(
    day: str,
    *,
    status: CutoverStatus,
    reasons: tuple[str, ...],
    notes: tuple[str, ...] = (),
) -> Tier12ConsumerCutoverDecision:
    return Tier12ConsumerCutoverDecision(
        decision_date=day,
        cutover_allowed=False,
        source="legacy_scaffold",
        status=status,
        claim_project_universe=False,
        reasons=reasons,
        notes=notes,
        accepted_payload=None,
    )


def resolve_tier12_consumer_cutover(
    decision_date: str,
    *,
    config: Tier12ConsumerCutoverConfig | Mapping[str, Any] | None = None,
    accepted: Tier12AcceptedPublish | Mapping[str, Any] | Path | str | None = None,
    artifact_root: Path | None = None,
    config_path: str | Path | None = None,
) -> Tier12ConsumerCutoverDecision:
    """Single resolver: research/UI must call this for Tier1/2 cutover.

    Returns ``cutover_allowed=False`` / ``legacy_scaffold`` unless every gate
    passes and typed config explicitly opts in.
    """

    day = _compact_day(decision_date)
    if len(day) != 8:
        return _legacy(
            day or str(decision_date),
            status="BLOCKED",
            reasons=("invalid_decision_date",),
        )

    if config is None:
        cfg = load_tier12_consumer_cutover_config(config_path)
    elif isinstance(config, Tier12ConsumerCutoverConfig):
        cfg = config
    elif isinstance(config, Mapping):
        cfg = Tier12ConsumerCutoverConfig.from_mapping(
            config if "consumer_cutover" in config else {"consumer_cutover": config}
        )
    else:
        raise TypeError(f"unsupported cutover config type: {type(config)!r}")

    if not cfg.cutover_allowed:
        return _legacy(
            day,
            status="LEGACY",
            reasons=("config_cutover_allowed_false",),
            notes=("consumers_stay_on_legacy_scaffold", "default_fail_closed"),
        )

    payload = _load_accepted_for_day(
        day, config=cfg, accepted=accepted, artifact_root=artifact_root
    )
    if payload is None:
        return _legacy(
            day,
            status="BLOCKED",
            reasons=("missing_accept", "no_accepted_partition_for_day"),
            notes=("config_opt_in_without_accept_fail_closed",),
        )

    reasons: list[str] = []
    if str(payload.get("kind") or "") != "tier12_accepted_partition":
        reasons.append("not_tier12_accepted_partition")
    if str(payload.get("status") or "") != "ACCEPTED":
        reasons.append(f"status_not_accepted:{payload.get('status')}")
    if payload.get("published") is not True:
        reasons.append("published_false")
    payload_day = _compact_day(payload.get("decision_date"))
    if payload_day != day:
        reasons.append(f"decision_date_mismatch:{payload_day}")

    def_v = str(payload.get("definition_version") or "").strip()
    cfg_hash = str(payload.get("config_hash") or "").strip()
    if not cfg.expected_definition_version:
        reasons.append("missing_expected_definition_version")
    elif def_v != cfg.expected_definition_version:
        reasons.append(
            f"definition_version_mismatch:{def_v}!={cfg.expected_definition_version}"
        )
    if not cfg.expected_config_hash:
        reasons.append("missing_expected_config_hash")
    elif cfg_hash != cfg.expected_config_hash:
        reasons.append("config_hash_mismatch")

    canary = _is_canary_accept(payload)
    if canary:
        if cfg.claim_project_universe:
            reasons.append("canary_accept_forbids_project_universe_claim")
            reasons.append("canary_not_full_universe")
        if not cfg.acknowledge_canary_scope:
            reasons.append("canary_accept_requires_acknowledge_canary_scope")
            reasons.append("canary_not_full_universe_cutover")
    else:
        if cfg.claim_project_universe is False and cfg.acknowledge_canary_scope:
            # Non-canary with canary ack is allowed but not required; no block.
            pass

    if reasons:
        return _legacy(
            day,
            status="BLOCKED",
            reasons=tuple(reasons),
            notes=("fail_closed_consumer_cutover",),
        )

    if canary:
        return Tier12ConsumerCutoverDecision(
            decision_date=day,
            cutover_allowed=True,
            source="accepted_partition",
            status="CANARY_SCOPED",
            claim_project_universe=False,
            reasons=("gates_passed_canary_scoped",),
            notes=(
                "config_explicit_opt_in",
                "canary_scope_acknowledged",
                "forbids_project_universe_claim",
                "not_phase_c_complete",
            ),
            accepted_payload=dict(payload),
        )

    return Tier12ConsumerCutoverDecision(
        decision_date=day,
        cutover_allowed=True,
        source="accepted_partition",
        status="ACCEPTED_CUTOVER",
        claim_project_universe=bool(cfg.claim_project_universe),
        reasons=("gates_passed",),
        notes=(
            "config_explicit_opt_in",
            "accepted_publish_matched",
            "definition_version_and_config_hash_matched",
            "not_strategy_release",
        ),
        accepted_payload=dict(payload),
    )


def load_accepted_partition_as_production_truth(
    decision_date: str,
    *,
    artifact_root: Path | None = None,
    config: Tier12ConsumerCutoverConfig | Mapping[str, Any] | None = None,
    config_path: str | Path | None = None,
) -> Mapping[str, Any]:
    """Load accepted partition only when the cutover gate allows it.

    Direct file reads that skip the resolver must not be used as production
    truth — this helper exists to make that contract enforceable in tests and
    call sites.
    """

    read = resolve_tier12_production_read(
        decision_date,
        config=config,
        artifact_root=artifact_root,
        config_path=config_path,
    )
    if read.uses_legacy or read.accepted_payload is None:
        raise Tier12ConsumerCutoverError(
            "refused_silent_accepted_file_read_as_production_truth; "
            "call resolve_tier12_consumer_cutover / "
            "resolve_tier12_production_read gate first "
            f"(status={read.status} reasons={list(read.reasons)})"
        )
    return dict(read.accepted_payload)


@dataclass(frozen=True)
class Tier12ProductionRead:
    """Single production-read boundary result for Tier1/2 consumers.

    Callers must use this (or ``load_accepted_partition_as_production_truth``)
    instead of silently ``json.load``-ing ``accepted_*.json``.
    """

    decision_date: str
    status: CutoverStatus
    source: CutoverSource
    claim_project_universe: bool
    reasons: tuple[str, ...]
    notes: tuple[str, ...]
    accepted_payload: Mapping[str, Any] | None
    cutover_decision: Tier12ConsumerCutoverDecision

    @property
    def uses_legacy(self) -> bool:
        return self.source == "legacy_scaffold" or not self.cutover_decision.cutover_allowed

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": "tier12_production_read",
            "decision_date": self.decision_date,
            "status": self.status,
            "source": self.source,
            "claim_project_universe": self.claim_project_universe,
            "reasons": list(self.reasons),
            "notes": list(self.notes),
            "uses_legacy": self.uses_legacy,
            "accepted_payload": (
                dict(self.accepted_payload) if self.accepted_payload is not None else None
            ),
            "cutover_decision": self.cutover_decision.as_dict(),
        }


def _code6(value: Any) -> str:
    return str(value or "").split(".", 1)[0].strip()


def stock_states_from_accepted_payload(
    payload: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    """Project accepted partition stock_states → code6 → B1-shaped fields."""

    out: dict[str, dict[str, Any]] = {}
    for row in payload.get("stock_states") or ():
        if not isinstance(row, Mapping):
            continue
        code = _code6(row.get("stock_code") or row.get("entity_id"))
        if not code:
            continue
        details = row.get("details") if isinstance(row.get("details"), Mapping) else {}
        out[code] = {
            "axis_trend": row.get("axis_trend"),
            "axis_pos": row.get("axis_pos"),
            "form_name": row.get("form_name")
            if row.get("form_name") is not None
            else details.get("form_name"),
            "is_breakout_event": row.get("is_breakout_event"),
            "source": "accepted_partition",
        }
    return out


def resolve_tier12_production_read(
    decision_date: str,
    *,
    config: Tier12ConsumerCutoverConfig | Mapping[str, Any] | None = None,
    accepted: Tier12AcceptedPublish | Mapping[str, Any] | Path | str | None = None,
    artifact_root: Path | None = None,
    config_path: str | Path | None = None,
) -> Tier12ProductionRead:
    """Single read boundary: always resolve cutover before Tier1/2 truth.

    - ``LEGACY`` / ``BLOCKED`` → legacy scaffold path; ``accepted_payload=None``
      (never treat accepted JSON as production truth).
    - ``CANARY_SCOPED`` → accepted payload allowed only as canary; never
      ``claim_project_universe``.
    - ``ACCEPTED_CUTOVER`` → may expose accepted partition (yaml still false).
    """

    decision = resolve_tier12_consumer_cutover(
        decision_date,
        config=config,
        accepted=accepted,
        artifact_root=artifact_root,
        config_path=config_path,
    )

    if (
        decision.status in ("LEGACY", "BLOCKED")
        or not decision.cutover_allowed
        or decision.accepted_payload is None
    ):
        return Tier12ProductionRead(
            decision_date=decision.decision_date,
            status=decision.status,
            source="legacy_scaffold",
            claim_project_universe=False,
            reasons=decision.reasons,
            notes=tuple(decision.notes)
            + ("production_read_boundary_legacy", "accepted_json_not_production_truth"),
            accepted_payload=None,
            cutover_decision=decision,
        )

    if decision.status == "CANARY_SCOPED":
        return Tier12ProductionRead(
            decision_date=decision.decision_date,
            status="CANARY_SCOPED",
            source="accepted_partition",
            claim_project_universe=False,
            reasons=decision.reasons,
            notes=tuple(decision.notes)
            + (
                "production_read_boundary_canary_scoped",
                "forbids_project_universe_claim",
            ),
            accepted_payload=dict(decision.accepted_payload),
            cutover_decision=decision,
        )

    return Tier12ProductionRead(
        decision_date=decision.decision_date,
        status="ACCEPTED_CUTOVER",
        source="accepted_partition",
        claim_project_universe=bool(decision.claim_project_universe),
        reasons=decision.reasons,
        notes=tuple(decision.notes) + ("production_read_boundary_accepted_cutover",),
        accepted_payload=dict(decision.accepted_payload),
        cutover_decision=decision,
    )


__all__ = [
    "Tier12ConsumerCutoverConfig",
    "Tier12ConsumerCutoverDecision",
    "Tier12ConsumerCutoverError",
    "Tier12ProductionRead",
    "load_accepted_partition_as_production_truth",
    "load_tier12_consumer_cutover_config",
    "resolve_tier12_consumer_cutover",
    "resolve_tier12_production_read",
    "stock_states_from_accepted_payload",
]
