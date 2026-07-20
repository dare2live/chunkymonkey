"""Pulse/UI Tier1 form + attestation via Phase C production-read boundary.

Owner-opted cutover ON resolves to ACCEPTED_CUTOVER when accept matches;
missing accept / blocked gates fail closed to ``fact_stock_form_daily``.
Callers must invoke ``resolve_tier12_production_read`` before treating
accepted partitions as production truth — silent ``accepted_*.json`` reads
are forbidden. Drill must not dual-read legacy SQL then overwrite accepted.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, MutableMapping, Sequence

from services.tier12_consumer_cutover import (
    Tier12ConsumerCutoverConfig,
    Tier12ProductionRead,
    resolve_tier12_production_read,
    stock_states_from_accepted_payload,
)


def _compact_day(value: Any) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())[:8]


def _code6(value: Any) -> str:
    return str(value or "").split(".", 1)[0].strip()


def attest_pulse_tier12_production_read(
    decision_date: str,
    *,
    config: Tier12ConsumerCutoverConfig | Mapping[str, Any] | None = None,
    artifact_root: Path | str | None = None,
    config_path: Path | str | None = None,
) -> dict[str, Any]:
    """UI-facing attestation: always crosses the production-read boundary."""

    day = _compact_day(decision_date)
    if len(day) != 8:
        return {
            "kind": "tier12_production_read",
            "decision_date": day,
            "status": "LEGACY",
            "source": "legacy_scaffold",
            "uses_legacy": True,
            "cutover_allowed": False,
            "claim_project_universe": False,
            "reasons": ["invalid_or_missing_decision_date"],
            "notes": [
                "pulse_ui_attestation",
                "accepted_json_not_production_truth",
            ],
        }

    art = Path(artifact_root) if artifact_root is not None else None
    read = resolve_tier12_production_read(
        day,
        config=config,
        artifact_root=art,
        config_path=config_path,
    )
    return {
        "kind": "tier12_production_read",
        "decision_date": read.decision_date,
        "status": read.status,
        "source": read.source,
        "uses_legacy": read.uses_legacy,
        "cutover_allowed": bool(read.cutover_decision.cutover_allowed),
        "claim_project_universe": bool(read.claim_project_universe),
        "reasons": list(read.reasons),
        "notes": list(read.notes) + ["pulse_ui_attestation"],
    }


def overlay_pulse_form_from_production_read(
    rows: Sequence[MutableMapping[str, Any]],
    as_of: str,
    *,
    config: Tier12ConsumerCutoverConfig | Mapping[str, Any] | None = None,
    artifact_root: Path | str | None = None,
    config_path: Path | str | None = None,
) -> tuple[list[dict[str, Any]], Tier12ProductionRead | None]:
    """Apply Tier1 form fields via production-read boundary.

    LEGACY / BLOCKED: leave ``form_name`` / ``is_breakout_event`` from the
    legacy SQL join (``fact_stock_form_daily``) unchanged.
    ACCEPTED_CUTOVER / CANARY_SCOPED: overlay from accepted stock_states
    (never silent JSON).
    """

    out = [dict(r) for r in rows]
    day = _compact_day(as_of)
    if len(day) != 8 or not out:
        return out, None

    art = Path(artifact_root) if artifact_root is not None else None
    read = resolve_tier12_production_read(
        day,
        config=config,
        artifact_root=art,
        config_path=config_path,
    )
    if (
        read.uses_legacy
        or read.source != "accepted_partition"
        or read.accepted_payload is None
    ):
        return out, read

    by_code = stock_states_from_accepted_payload(read.accepted_payload)
    for row in out:
        code = _code6(row.get("stock_code") or row.get("ts_code"))
        state = by_code.get(code)
        if state is None:
            row["form_name"] = None
            row["is_breakout_event"] = None
            continue
        row["form_name"] = state.get("form_name")
        row["is_breakout_event"] = state.get("is_breakout_event")
        row["tier12_form_source"] = "accepted_partition"
    return out, read


__all__ = [
    "attest_pulse_tier12_production_read",
    "overlay_pulse_form_from_production_read",
]
