"""Phase C scaffolding: Tier1/2 publish lineage contracts (fail-closed).

Defines ``StockStateDaily`` and ``MarketContextPublishEnvelope`` with the
MASTER-required lineage fields. Attestation proves scaffold publishability
only — it does **not** write accepted partitions, cut over consumers, or
claim Phase C publish-complete.

Legacy ``fact_stock_form_daily`` / research ``MarketContextSnapshot`` rows
bridge in without inventing lineage; missing fields stay NOT_PUBLISHABLE.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

REQUIRED_STOCK_STATE_FIELDS = (
    "definition_version",
    "config_hash",
    "input_snapshot_id",
    "eligible_universe_id",
    "available_at",
)
REQUIRED_MARKET_CONTEXT_FIELDS = (
    "definition_version",
    "config_hash",
    "input_snapshot_id",
    "eligible_universe_id",
    "available_at",
)


def config_hash_for(config: Mapping[str, Any]) -> str:
    """Stable SHA-256 over a sorted JSON canonicalization of typed config."""

    if not isinstance(config, Mapping):
        raise ValueError("config_hash_for requires a mapping")
    payload = json.dumps(dict(config), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class StockStateDaily:
    """Tier1 stock-state row with optional Phase C lineage (+ form enrich)."""

    stock_code: str
    trade_date: str
    axis_trend: str | None = None
    axis_pos: str | None = None
    form_name: str | None = None
    is_breakout_event: bool | None = None
    definition_version: str | None = None
    config_hash: str | None = None
    input_snapshot_id: str | None = None
    eligible_universe_id: str | None = None
    available_at: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "stock_code": self.stock_code,
            "trade_date": self.trade_date,
            "axis_trend": self.axis_trend,
            "axis_pos": self.axis_pos,
            "form_name": self.form_name,
            "is_breakout_event": self.is_breakout_event,
            "definition_version": self.definition_version,
            "config_hash": self.config_hash,
            "input_snapshot_id": self.input_snapshot_id,
            "eligible_universe_id": self.eligible_universe_id,
            "available_at": self.available_at,
            "details": dict(self.details),
        }


@dataclass(frozen=True)
class MarketContextPublishEnvelope:
    """Tier2 market-context publish envelope (scaffold, not mart cutover)."""

    decision_time: str
    available_at: str | None
    definition_version: str | None
    config_hash: str | None
    input_snapshot_id: str | None
    eligible_universe_id: str | None
    trust_status: str
    risk_on: bool | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "decision_time": self.decision_time,
            "available_at": self.available_at,
            "definition_version": self.definition_version,
            "config_hash": self.config_hash,
            "input_snapshot_id": self.input_snapshot_id,
            "eligible_universe_id": self.eligible_universe_id,
            "trust_status": self.trust_status,
            "risk_on": self.risk_on,
            "details": dict(self.details),
        }


@dataclass(frozen=True)
class PublishLineageReport:
    status: str
    publishable: bool
    published: bool
    missing_fields: tuple[str, ...]
    notes: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "publishable": self.publishable,
            "published": self.published,
            "missing_fields": list(self.missing_fields),
            "notes": list(self.notes),
        }


def _missing(obj: Any, required: Sequence[str]) -> tuple[str, ...]:
    missing: list[str] = []
    for name in required:
        value = getattr(obj, name, None)
        if value is None or (isinstance(value, str) and not value.strip()):
            missing.append(name)
    return tuple(missing)


def attest_stock_state_publishable(row: StockStateDaily) -> PublishLineageReport:
    missing = _missing(row, REQUIRED_STOCK_STATE_FIELDS)
    if missing:
        return PublishLineageReport(
            status="NOT_PUBLISHABLE",
            publishable=False,
            published=False,
            missing_fields=missing,
            notes=("phase_c_scaffold_lineage_incomplete",),
        )
    return PublishLineageReport(
        status="PUBLISHABLE_SCAFFOLD",
        publishable=True,
        published=False,
        missing_fields=(),
        notes=(
            "phase_c_scaffold_only",
            "not_accepted_partition",
            "not_consumer_cutover",
        ),
    )


def attest_market_context_publishable(
    envelope: MarketContextPublishEnvelope,
) -> PublishLineageReport:
    missing = _missing(envelope, REQUIRED_MARKET_CONTEXT_FIELDS)
    if missing:
        return PublishLineageReport(
            status="NOT_PUBLISHABLE",
            publishable=False,
            published=False,
            missing_fields=missing,
            notes=("phase_c_scaffold_lineage_incomplete",),
        )
    if str(envelope.trust_status or "").upper() != "READY":
        return PublishLineageReport(
            status="NOT_PUBLISHABLE",
            publishable=False,
            published=False,
            missing_fields=(),
            notes=("trust_status_not_ready", str(envelope.trust_status)),
        )
    return PublishLineageReport(
        status="PUBLISHABLE_SCAFFOLD",
        publishable=True,
        published=False,
        missing_fields=(),
        notes=(
            "phase_c_scaffold_only",
            "not_accepted_partition",
            "not_pulse_mart_cutover",
        ),
    )


def stock_state_from_form_row(row: Mapping[str, Any]) -> StockStateDaily:
    """Bridge legacy form row without inventing Phase C lineage fields."""

    return StockStateDaily(
        stock_code=str(row.get("stock_code") or row.get("code") or ""),
        trade_date="".join(ch for ch in str(row.get("trade_date") or "") if ch.isdigit())[
            :8
        ],
        axis_trend=(
            str(row["axis_trend"]) if row.get("axis_trend") is not None else None
        ),
        axis_pos=(str(row["axis_pos"]) if row.get("axis_pos") is not None else None),
        form_name=(
            str(row["form_name"]) if row.get("form_name") is not None else None
        ),
        is_breakout_event=(
            bool(row["is_breakout_event"])
            if row.get("is_breakout_event") is not None
            else None
        ),
        definition_version=None,
        config_hash=None,
        input_snapshot_id=None,
        eligible_universe_id=None,
        available_at=None,
        details={"source_table": "fact_stock_form_daily", "bridge": "legacy_form_row"},
    )


__all__ = [
    "REQUIRED_MARKET_CONTEXT_FIELDS",
    "REQUIRED_STOCK_STATE_FIELDS",
    "MarketContextPublishEnvelope",
    "PublishLineageReport",
    "StockStateDaily",
    "attest_market_context_publishable",
    "attest_stock_state_publishable",
    "config_hash_for",
    "stock_state_from_form_row",
]
