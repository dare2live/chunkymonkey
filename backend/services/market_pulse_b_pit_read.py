"""Pulse/UI B-pit mart attestation via explicit cutover gate.

Default ``mart_cutover.cutover_allowed=false`` keeps pulse on legacy mart
breadth numbers. Callers must invoke ``resolve_b_pit_mart_production_read``
before treating ``project_universe_pit`` breadth as production mart truth.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from services.b_pit_mart_cutover import (
    BPitMartCutoverConfig,
    resolve_b_pit_mart_production_read,
)


def _compact_day(value: Any) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())[:8]


def attest_pulse_b_pit_mart_production_read(
    trade_date: str,
    *,
    config: BPitMartCutoverConfig | Mapping[str, Any] | None = None,
    artifact_root: Path | str | None = None,
    config_path: Path | str | None = None,
) -> dict[str, Any]:
    """UI-facing attestation: always crosses the B-pit mart production-read boundary."""

    day = _compact_day(trade_date)
    if len(day) != 8:
        return {
            "kind": "b_pit_mart_production_read",
            "trade_date": day,
            "status": "LEGACY",
            "source": "legacy_mart",
            "uses_legacy": True,
            "cutover_allowed": False,
            "reasons": ["invalid_or_missing_trade_date"],
            "notes": [
                "pulse_ui_attestation",
                "project_universe_pit_not_mart_truth",
            ],
        }

    art = Path(artifact_root) if artifact_root is not None else None
    read = resolve_b_pit_mart_production_read(
        day,
        config=config,
        artifact_root=art,
        config_path=config_path,
    )
    return {
        "kind": "b_pit_mart_production_read",
        "trade_date": read.trade_date,
        "status": read.status,
        "source": read.source,
        "uses_legacy": read.uses_legacy,
        "cutover_allowed": bool(read.cutover_allowed),
        "reasons": list(read.reasons),
        "notes": list(read.notes) + ["pulse_ui_attestation"],
    }


__all__ = ["attest_pulse_b_pit_mart_production_read"]
