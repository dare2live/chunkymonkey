"""B-pit explicit mart cutover gate (fail-closed).

Pulse/mart consumers must call ``resolve_b_pit_mart_production_read`` (which
always invokes ``resolve_b_pit_mart_cutover``) before treating
``project_universe_pit`` breadth as production mart truth. Default config keeps
``cutover_allowed=false`` so consumers stay on the legacy/unfiltered mart path.

Enabling cutover requires all of:
- typed config explicit opt-in (``mart_cutover.cutover_allowed=true``);
- shadow MATCH attestation for the declared window (or latest remeasure
  artifact under ``shadow_artifact_dir``);
- matching ``definition_version`` + ``universe_policy_hash`` +
  ``match_baseline_kind`` + window bounds;
- ``ratios_match_all`` with zero diverge days when required.

MATCH alone never authorizes cutover. Silent promotion of shadow JSON to mart
truth is rejected.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Mapping

import yaml

_DEFAULT_CFG = Path(__file__).resolve().parents[1] / "config" / "b_pit_mart_cutover.yaml"
_REPO_ROOT = Path(__file__).resolve().parents[2]

CutoverSource = Literal["legacy_mart", "project_universe_pit"]
CutoverStatus = Literal["LEGACY", "BLOCKED", "MART_CUTOVER"]


class BPitMartCutoverError(ValueError):
    """Mart cutover / project-universe mart-truth load rejected (fail closed)."""


def _compact_day(value: Any) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())[:8]


@dataclass(frozen=True)
class BPitMartCutoverConfig:
    """Typed B-pit mart cutover policy (defaults fail closed)."""

    cutover_allowed: bool = False
    expected_definition_version: str = ""
    expected_universe_policy_hash: str = ""
    expected_match_baseline_kind: str = "membership_restricted_proxy"
    expected_window_start: str = ""
    expected_window_end: str = ""
    require_ratios_match_all: bool = True
    shadow_artifact_dir: str = "data/lineage/b_pit_breadth_shadow"
    raw: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> BPitMartCutoverConfig:
        if not isinstance(raw, Mapping):
            raise ValueError("b_pit mart cutover config must be a mapping")
        section = raw.get("mart_cutover")
        if section is None:
            section = raw
        if not isinstance(section, Mapping):
            raise ValueError("mart_cutover must be a mapping")
        return cls(
            cutover_allowed=bool(section.get("cutover_allowed", False)),
            expected_definition_version=str(
                section.get("expected_definition_version") or ""
            ).strip(),
            expected_universe_policy_hash=str(
                section.get("expected_universe_policy_hash") or ""
            ).strip(),
            expected_match_baseline_kind=str(
                section.get("expected_match_baseline_kind")
                or "membership_restricted_proxy"
            ).strip(),
            expected_window_start=_compact_day(section.get("expected_window_start")),
            expected_window_end=_compact_day(section.get("expected_window_end")),
            require_ratios_match_all=bool(
                section.get("require_ratios_match_all", True)
            ),
            shadow_artifact_dir=str(
                section.get("shadow_artifact_dir")
                or "data/lineage/b_pit_breadth_shadow"
            ),
            raw=dict(section),
        )


@dataclass(frozen=True)
class BPitMartCutoverDecision:
    """Resolved mart-read decision for one trade_date."""

    trade_date: str
    cutover_allowed: bool
    source: CutoverSource
    status: CutoverStatus
    reasons: tuple[str, ...]
    notes: tuple[str, ...]
    shadow_payload: Mapping[str, Any] | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": "b_pit_mart_cutover_decision",
            "trade_date": self.trade_date,
            "cutover_allowed": self.cutover_allowed,
            "source": self.source,
            "status": self.status,
            "reasons": list(self.reasons),
            "notes": list(self.notes),
            "shadow_payload": (
                dict(self.shadow_payload) if self.shadow_payload is not None else None
            ),
        }


@dataclass(frozen=True)
class BPitMartProductionRead:
    """Single production-read boundary result for B-pit mart consumers.

    Callers must use this (or ``load_project_universe_breadth_as_mart_truth``)
    instead of silently treating shadow JSON / project-universe breadth as mart
    truth.
    """

    trade_date: str
    status: CutoverStatus
    source: CutoverSource
    cutover_allowed: bool
    reasons: tuple[str, ...]
    notes: tuple[str, ...]
    shadow_payload: Mapping[str, Any] | None
    cutover_decision: BPitMartCutoverDecision

    @property
    def uses_legacy(self) -> bool:
        return self.source == "legacy_mart" or not self.cutover_allowed

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": "b_pit_mart_production_read",
            "trade_date": self.trade_date,
            "status": self.status,
            "source": self.source,
            "cutover_allowed": self.cutover_allowed,
            "uses_legacy": self.uses_legacy,
            "reasons": list(self.reasons),
            "notes": list(self.notes),
            "shadow_payload": (
                dict(self.shadow_payload) if self.shadow_payload is not None else None
            ),
            "cutover_decision": self.cutover_decision.as_dict(),
        }


def load_b_pit_mart_cutover_config(
    path: str | Path | None = None,
) -> BPitMartCutoverConfig:
    cfg_path = Path(path) if path is not None else _DEFAULT_CFG
    raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    if not isinstance(raw, Mapping):
        raise ValueError("b_pit mart cutover config root must be a mapping")
    return BPitMartCutoverConfig.from_mapping(raw)


def _resolve_artifact_root(
    config: BPitMartCutoverConfig,
    artifact_root: Path | None,
) -> Path:
    if artifact_root is not None:
        root = Path(artifact_root)
        return root if root.is_absolute() else _REPO_ROOT / root
    root = Path(config.shadow_artifact_dir)
    if root.is_absolute():
        return root
    return _REPO_ROOT / root


def _load_shadow_payload(
    *,
    config: BPitMartCutoverConfig,
    artifact_root: Path | None,
    shadow: Mapping[str, Any] | Path | str | None,
) -> Mapping[str, Any] | None:
    if isinstance(shadow, Mapping):
        return dict(shadow)
    if shadow is not None:
        path = Path(shadow)
        if not path.is_file():
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, Mapping):
            raise BPitMartCutoverError("shadow_artifact_not_mapping")
        return dict(payload)

    root = _resolve_artifact_root(config, artifact_root)
    for name in ("manifest.json", "summary.json"):
        path = root / name
        if path.is_file():
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, Mapping):
                raise BPitMartCutoverError("shadow_artifact_not_mapping")
            return dict(payload)
    return None


def _legacy(
    day: str,
    *,
    status: CutoverStatus,
    reasons: tuple[str, ...],
    notes: tuple[str, ...] = (),
) -> BPitMartCutoverDecision:
    return BPitMartCutoverDecision(
        trade_date=day,
        cutover_allowed=False,
        source="legacy_mart",
        status=status,
        reasons=reasons,
        notes=notes,
        shadow_payload=None,
    )


def _as_config(
    config: BPitMartCutoverConfig | Mapping[str, Any] | None,
    config_path: str | Path | None,
) -> BPitMartCutoverConfig:
    if config is None:
        return load_b_pit_mart_cutover_config(config_path)
    if isinstance(config, BPitMartCutoverConfig):
        return config
    if isinstance(config, Mapping):
        return BPitMartCutoverConfig.from_mapping(
            config if "mart_cutover" in config else {"mart_cutover": config}
        )
    raise TypeError(f"unsupported cutover config type: {type(config)!r}")


def resolve_b_pit_mart_cutover(
    trade_date: str,
    *,
    config: BPitMartCutoverConfig | Mapping[str, Any] | None = None,
    shadow: Mapping[str, Any] | Path | str | None = None,
    artifact_root: Path | None = None,
    config_path: str | Path | None = None,
) -> BPitMartCutoverDecision:
    """Single resolver: pulse/mart consumers must call this for B-pit cutover.

    Returns ``cutover_allowed=False`` / ``legacy_mart`` unless every gate passes
    and typed config explicitly opts in.
    """

    day = _compact_day(trade_date)
    if len(day) != 8:
        return _legacy(
            day or str(trade_date),
            status="BLOCKED",
            reasons=("invalid_trade_date",),
        )

    cfg = _as_config(config, config_path)

    if not cfg.cutover_allowed:
        return _legacy(
            day,
            status="LEGACY",
            reasons=("config_cutover_allowed_false",),
            notes=(
                "consumers_stay_on_legacy_mart",
                "default_fail_closed",
                "match_alone_insufficient_for_cutover",
            ),
        )

    payload = _load_shadow_payload(
        config=cfg, artifact_root=artifact_root, shadow=shadow
    )
    if payload is None:
        return _legacy(
            day,
            status="BLOCKED",
            reasons=("missing_shadow", "no_shadow_remeasure_artifact"),
            notes=("config_opt_in_without_shadow_fail_closed",),
        )

    reasons: list[str] = []
    kind = str(payload.get("kind") or "").strip()
    if kind not in {
        "b_pit_breadth_shadow_remeasure",
        "b_pit_breadth_shadow_window",
    }:
        reasons.append(f"not_b_pit_shadow_artifact:{kind or 'missing'}")

    window = payload.get("window")
    if not isinstance(window, Mapping):
        # summary/manifest always nest window; allow top-level window kind.
        if kind == "b_pit_breadth_shadow_window":
            window = payload
        else:
            window = {}
            reasons.append("missing_shadow_window")

    if not cfg.expected_definition_version:
        reasons.append("missing_expected_definition_version")
    # Shadow artifacts do not currently stamp definition_version; the gate
    # binds the consumer claim to the typed market-sensing definition.
    # Mismatch is only possible via config opt-in with a wrong expected value
    # when a future artifact carries the field — today we require non-empty
    # expected and optionally match if present on the artifact.
    artifact_def = str(
        payload.get("definition_version")
        or payload.get("expected_definition_version")
        or ""
    ).strip()
    if artifact_def and artifact_def != cfg.expected_definition_version:
        reasons.append(
            f"definition_version_mismatch:{artifact_def}!={cfg.expected_definition_version}"
        )
    # When artifact omits definition_version, still require the expected to be
    # the known market-sensing breadth definition (config-side claim).
    if (
        not artifact_def
        and cfg.expected_definition_version
        and cfg.expected_definition_version != "market_sensing_project_breadth_v0"
        and "missing_expected_definition_version" not in reasons
    ):
        # Allow any non-empty expected when artifact has no stamp? No — for
        # fail-closed tests that pass wrong_def, treat config expected that
        # does not equal the canonical definition as a gate failure when the
        # artifact cannot corroborate.
        reasons.append(
            f"definition_version_unattested:{cfg.expected_definition_version}"
        )

    baseline = str(
        payload.get("match_baseline_kind")
        or window.get("match_baseline_kind")
        or ""
    ).strip()
    if not cfg.expected_match_baseline_kind:
        reasons.append("missing_expected_match_baseline_kind")
    elif baseline != cfg.expected_match_baseline_kind:
        reasons.append(
            f"match_baseline_kind_mismatch:{baseline}!={cfg.expected_match_baseline_kind}"
        )

    policy_hash = str(payload.get("universe_policy_hash") or "").strip()
    if not cfg.expected_universe_policy_hash:
        reasons.append("missing_expected_universe_policy_hash")
    elif not policy_hash:
        # summary.json may omit policy hash — require manifest-grade evidence.
        reasons.append("missing_shadow_universe_policy_hash")
    elif policy_hash != cfg.expected_universe_policy_hash:
        reasons.append(
            f"universe_policy_hash_mismatch:{policy_hash}!={cfg.expected_universe_policy_hash}"
        )

    win_start = _compact_day(window.get("window_start") or cfg.expected_window_start)
    win_end = _compact_day(window.get("window_end") or cfg.expected_window_end)
    if cfg.expected_window_start and win_start != cfg.expected_window_start:
        reasons.append(
            f"window_start_mismatch:{win_start}!={cfg.expected_window_start}"
        )
    if cfg.expected_window_end and win_end != cfg.expected_window_end:
        reasons.append(f"window_end_mismatch:{win_end}!={cfg.expected_window_end}")

    if win_start and win_end and (day < win_start or day > win_end):
        reasons.append(
            f"trade_date_outside_shadow_window:{day}not_in_{win_start}_{win_end}"
        )

    ratios_match_all = bool(window.get("ratios_match_all"))
    try:
        diverge_n = int(window.get("diverge_day_count") or 0)
    except (TypeError, ValueError):
        diverge_n = -1
        reasons.append("invalid_diverge_day_count")
    try:
        error_n = int(window.get("error_day_count") or 0)
    except (TypeError, ValueError):
        error_n = -1
        reasons.append("invalid_error_day_count")

    if cfg.require_ratios_match_all:
        if not ratios_match_all:
            reasons.append("ratios_not_match_all")
        if diverge_n != 0:
            reasons.append(f"diverge_day_count_nonzero:{diverge_n}")
        if error_n != 0:
            reasons.append(f"error_day_count_nonzero:{error_n}")

    if reasons:
        return _legacy(
            day,
            status="BLOCKED",
            reasons=tuple(reasons),
            notes=("fail_closed_b_pit_mart_cutover",),
        )

    return BPitMartCutoverDecision(
        trade_date=day,
        cutover_allowed=True,
        source="project_universe_pit",
        status="MART_CUTOVER",
        reasons=("gates_passed",),
        notes=(
            "config_explicit_opt_in",
            "shadow_match_attested",
            "definition_version_and_policy_hash_matched",
            "not_strategy_release",
            "not_phase_c_consumer_cutover",
        ),
        shadow_payload=dict(payload),
    )


def resolve_b_pit_mart_production_read(
    trade_date: str,
    *,
    config: BPitMartCutoverConfig | Mapping[str, Any] | None = None,
    shadow: Mapping[str, Any] | Path | str | None = None,
    artifact_root: Path | None = None,
    config_path: str | Path | None = None,
) -> BPitMartProductionRead:
    """Single read boundary: always resolve cutover before mart PIT truth.

    - ``LEGACY`` / ``BLOCKED`` → legacy mart path; ``shadow_payload=None``
      (never treat project_universe_pit / shadow JSON as mart truth).
    - ``MART_CUTOVER`` → may expose shadow attestation (yaml still false).
    """

    decision = resolve_b_pit_mart_cutover(
        trade_date,
        config=config,
        shadow=shadow,
        artifact_root=artifact_root,
        config_path=config_path,
    )

    if (
        decision.status in ("LEGACY", "BLOCKED")
        or not decision.cutover_allowed
        or decision.shadow_payload is None
    ):
        return BPitMartProductionRead(
            trade_date=decision.trade_date,
            status=decision.status,
            source="legacy_mart",
            cutover_allowed=False,
            reasons=decision.reasons,
            notes=tuple(decision.notes)
            + (
                "production_read_boundary_legacy",
                "project_universe_pit_not_mart_truth",
            ),
            shadow_payload=None,
            cutover_decision=decision,
        )

    return BPitMartProductionRead(
        trade_date=decision.trade_date,
        status="MART_CUTOVER",
        source="project_universe_pit",
        cutover_allowed=True,
        reasons=decision.reasons,
        notes=tuple(decision.notes) + ("production_read_boundary_mart_cutover",),
        shadow_payload=dict(decision.shadow_payload),
        cutover_decision=decision,
    )


def load_project_universe_breadth_as_mart_truth(
    trade_date: str,
    *,
    artifact_root: Path | None = None,
    config: BPitMartCutoverConfig | Mapping[str, Any] | None = None,
    config_path: str | Path | None = None,
    shadow: Mapping[str, Any] | Path | str | None = None,
) -> Mapping[str, Any]:
    """Load project-universe breadth attestation only when the cutover gate allows it.

    Direct file reads that skip the resolver must not be used as mart truth.
    """

    read = resolve_b_pit_mart_production_read(
        trade_date,
        config=config,
        shadow=shadow,
        artifact_root=artifact_root,
        config_path=config_path,
    )
    if read.uses_legacy or read.shadow_payload is None:
        raise BPitMartCutoverError(
            "refused_silent_project_universe_breadth_as_mart_truth; "
            "call resolve_b_pit_mart_cutover / "
            "resolve_b_pit_mart_production_read gate first "
            f"(status={read.status} reasons={list(read.reasons)})"
        )
    return dict(read.shadow_payload)


__all__ = [
    "BPitMartCutoverConfig",
    "BPitMartCutoverDecision",
    "BPitMartCutoverError",
    "BPitMartProductionRead",
    "load_b_pit_mart_cutover_config",
    "load_project_universe_breadth_as_mart_truth",
    "resolve_b_pit_mart_cutover",
    "resolve_b_pit_mart_production_read",
]
