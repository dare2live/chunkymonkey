"""Phase C Tier1/2 accepted publish boundary (fail-closed).

Takes a ``WRITTEN_UNPUBLISHED`` writer batch that already attests
``PUBLISHABLE_SCAFFOLD`` and atomically records an accepted-partition
equivalent attestation. ``published=True`` is set only after acceptance
succeeds.

Hard gates:
- never silently upgrade smoke summaries to accepted;
- never auto-cutover consumers (``cutover_allowed`` stays false);
- missing lineage / PIT-poisoned outputs / already-published batches fail closed.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from services.tier12_publish_contract import (
    MarketContextPublishEnvelope,
    PublishLineageReport,
    StockStateDaily,
)
from services.tier12_publish_writer import Tier12WriteBatch

DATASET_ID_STOCK = "tier12_stock_state"
DATASET_ID_MARKET = "tier12_market_context"
CONTRACT_VERSION = "tier12_accepted_publish_v0"
WRITER_ID = "tier12_publish_accept"


class Tier12AcceptError(ValueError):
    """Accepted publish rejected (fail closed)."""


def _compact_day(value: Any) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())[:8]


def _available_day(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    digits = "".join(ch for ch in text if ch.isdigit())
    if len(digits) >= 8:
        return digits[:8]
    return ""


def _sha256_canonical(payload: Mapping[str, Any]) -> str:
    blob = json.dumps(dict(payload), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(dict(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    except Exception:
        # Best-effort temp cleanup; original accept failure must still propagate.
        with suppress(OSError):
            os.unlink(tmp_name)
        raise


def _report_from_mapping(raw: Mapping[str, Any] | None) -> PublishLineageReport | None:
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise Tier12AcceptError("invalid_attestation_mapping")
    return PublishLineageReport(
        status=str(raw.get("status") or ""),
        publishable=bool(raw.get("publishable")),
        published=bool(raw.get("published")),
        missing_fields=tuple(str(x) for x in (raw.get("missing_fields") or ())),
        notes=tuple(str(x) for x in (raw.get("notes") or ())),
    )


def _stock_from_mapping(raw: Mapping[str, Any]) -> StockStateDaily:
    return StockStateDaily(
        stock_code=str(raw.get("stock_code") or ""),
        trade_date=_compact_day(raw.get("trade_date")),
        axis_trend=(
            str(raw["axis_trend"]) if raw.get("axis_trend") is not None else None
        ),
        is_breakout_event=(
            bool(raw["is_breakout_event"])
            if raw.get("is_breakout_event") is not None
            else None
        ),
        definition_version=(
            str(raw["definition_version"])
            if raw.get("definition_version") is not None
            else None
        ),
        config_hash=(
            str(raw["config_hash"]) if raw.get("config_hash") is not None else None
        ),
        input_snapshot_id=(
            str(raw["input_snapshot_id"])
            if raw.get("input_snapshot_id") is not None
            else None
        ),
        eligible_universe_id=(
            str(raw["eligible_universe_id"])
            if raw.get("eligible_universe_id") is not None
            else None
        ),
        available_at=(
            str(raw["available_at"]) if raw.get("available_at") is not None else None
        ),
        details=dict(raw.get("details") or {}),
    )


def _market_from_mapping(raw: Mapping[str, Any] | None) -> MarketContextPublishEnvelope | None:
    if raw is None:
        return None
    return MarketContextPublishEnvelope(
        decision_time=str(raw.get("decision_time") or ""),
        available_at=(
            str(raw["available_at"]) if raw.get("available_at") is not None else None
        ),
        definition_version=(
            str(raw["definition_version"])
            if raw.get("definition_version") is not None
            else None
        ),
        config_hash=(
            str(raw["config_hash"]) if raw.get("config_hash") is not None else None
        ),
        input_snapshot_id=(
            str(raw["input_snapshot_id"])
            if raw.get("input_snapshot_id") is not None
            else None
        ),
        eligible_universe_id=(
            str(raw["eligible_universe_id"])
            if raw.get("eligible_universe_id") is not None
            else None
        ),
        trust_status=str(raw.get("trust_status") or ""),
        risk_on=(
            bool(raw["risk_on"]) if raw.get("risk_on") is not None else None
        ),
        details=dict(raw.get("details") or {}),
    )


def load_tier12_write_batch(path: str | Path | Mapping[str, Any]) -> Tier12WriteBatch:
    """Load a writer batch JSON (not a smoke summary) into ``Tier12WriteBatch``."""

    if isinstance(path, Mapping):
        raw = dict(path)
    else:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(payload, Mapping):
            raise Tier12AcceptError("batch_artifact_not_mapping")
        raw = dict(payload)

    kind = str(raw.get("kind") or "")
    if kind.startswith("tier12_writer_smoke") or "smoke" in kind:
        raise Tier12AcceptError(
            "smoke_summary_cannot_be_accepted; pass writer batch, not smoke"
        )
    if "stock_states" not in raw or "status" not in raw:
        raise Tier12AcceptError("not_a_write_batch")

    stocks = tuple(_stock_from_mapping(r) for r in (raw.get("stock_states") or ()))
    market = _market_from_mapping(raw.get("market_context"))
    stock_atts = tuple(
        _report_from_mapping(a)  # type: ignore[misc]
        for a in (raw.get("stock_attestations") or ())
        if a is not None
    )
    market_att = _report_from_mapping(raw.get("market_attestation"))
    return Tier12WriteBatch(
        decision_date=_compact_day(raw.get("decision_date")),
        stock_states=stocks,
        market_context=market,
        stock_attestations=stock_atts,  # type: ignore[arg-type]
        market_attestation=market_att,
        pit_excluded_count=int(raw.get("pit_excluded_count") or 0),
        status=str(raw.get("status") or ""),
        published=bool(raw.get("published")),
        notes=tuple(str(n) for n in (raw.get("notes") or ())),
    )


def _require_write_batch(batch: Any) -> Tier12WriteBatch:
    if isinstance(batch, Tier12WriteBatch):
        return batch
    if isinstance(batch, Mapping):
        kind = str(batch.get("kind") or "")
        if kind.startswith("tier12_writer_smoke") or "smoke" in kind:
            raise Tier12AcceptError(
                "smoke_summary_cannot_be_accepted; not_a_write_batch"
            )
        # Allow dict shaped like a write batch.
        if "stock_states" in batch and "status" in batch:
            return load_tier12_write_batch(batch)
        raise Tier12AcceptError("not_a_write_batch")
    raise Tier12AcceptError("not_a_write_batch")


def _validate_prerequisites(batch: Tier12WriteBatch) -> None:
    if batch.published is True:
        raise Tier12AcceptError(
            "already_published_without_accept_path "
            "(reject forged published=true; only accept sets published)"
        )
    if batch.status != "WRITTEN_UNPUBLISHED":
        raise Tier12AcceptError(
            f"require_WRITTEN_UNPUBLISHED got status={batch.status!r}"
        )
    if not batch.stock_states:
        raise Tier12AcceptError("empty_stock_states")
    if batch.market_context is None:
        raise Tier12AcceptError("missing_market_context")
    if len(batch.stock_attestations) != len(batch.stock_states):
        raise Tier12AcceptError("stock_attestation_count_mismatch")
    if batch.market_attestation is None:
        raise Tier12AcceptError("missing_market_attestation")

    for idx, att in enumerate(batch.stock_attestations):
        if not att.publishable or att.status != "PUBLISHABLE_SCAFFOLD":
            missing = ",".join(att.missing_fields) or att.status
            raise Tier12AcceptError(
                f"missing_lineage stock[{idx}] status={att.status} "
                f"NOT_PUBLISHABLE fields={missing}"
            )
        if att.published:
            raise Tier12AcceptError(
                f"already_published stock attestation[{idx}] without accept"
            )

    matt = batch.market_attestation
    if not matt.publishable or matt.status != "PUBLISHABLE_SCAFFOLD":
        missing = ",".join(matt.missing_fields) or matt.status
        raise Tier12AcceptError(
            f"missing_lineage market status={matt.status} "
            f"NOT_PUBLISHABLE fields={missing}"
        )
    if matt.published:
        raise Tier12AcceptError("already_published market attestation without accept")

    day = _compact_day(batch.decision_date)
    for idx, row in enumerate(batch.stock_states):
        avail = _available_day(row.available_at)
        if not avail:
            raise Tier12AcceptError(f"missing_lineage stock[{idx}] available_at")
        if avail > day:
            raise Tier12AcceptError(
                f"pit_poison stock[{idx}] available_at={row.available_at!r} "
                f"> decision_date={day}"
            )
        for field in (
            "definition_version",
            "config_hash",
            "input_snapshot_id",
            "eligible_universe_id",
        ):
            if not getattr(row, field, None):
                raise Tier12AcceptError(f"missing_lineage stock[{idx}] {field}")

    market = batch.market_context
    m_avail = _available_day(market.available_at)
    if not m_avail:
        raise Tier12AcceptError("missing_lineage market available_at")
    if m_avail > day:
        raise Tier12AcceptError(
            f"pit_poison market available_at={market.available_at!r} "
            f"> decision_date={day}"
        )
    if str(market.trust_status or "").upper() != "READY":
        raise Tier12AcceptError(
            f"market_trust_not_ready trust_status={market.trust_status!r}"
        )


@dataclass(frozen=True)
class Tier12AcceptedPublish:
    """Immutable accepted-partition equivalent attestation for Tier1/2."""

    decision_date: str
    status: str
    published: bool
    cutover_allowed: bool
    batch_id: str
    dataset_ids: tuple[str, ...]
    contract_version: str
    contract_hash: str
    definition_version: str
    config_hash: str
    input_snapshot_id: str
    available_at: str
    accepted_at: str
    stock_row_count: int
    content_hash: str
    stock_states: tuple[StockStateDaily, ...]
    market_context: MarketContextPublishEnvelope
    notes: tuple[str, ...]
    source_writer_status: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": "tier12_accepted_partition",
            "decision_date": self.decision_date,
            "status": self.status,
            "published": self.published,
            "cutover_allowed": self.cutover_allowed,
            "batch_id": self.batch_id,
            "dataset_ids": list(self.dataset_ids),
            "contract_version": self.contract_version,
            "contract_hash": self.contract_hash,
            "definition_version": self.definition_version,
            "config_hash": self.config_hash,
            "input_snapshot_id": self.input_snapshot_id,
            "available_at": self.available_at,
            "accepted_at": self.accepted_at,
            "stock_row_count": self.stock_row_count,
            "content_hash": self.content_hash,
            "stock_states": [r.as_dict() for r in self.stock_states],
            "market_context": self.market_context.as_dict(),
            "notes": list(self.notes),
            "source_writer_status": self.source_writer_status,
            "writer_id": WRITER_ID,
            "partitions": [
                {
                    "dataset_id": DATASET_ID_STOCK,
                    "partition_value": self.decision_date,
                    "row_count": self.stock_row_count,
                },
                {
                    "dataset_id": DATASET_ID_MARKET,
                    "partition_value": self.decision_date,
                    "row_count": 1,
                },
            ],
        }


def accept_tier12_batch(
    batch: Tier12WriteBatch | Mapping[str, Any],
    *,
    allow_consumer_cutover: bool = False,
    emit_artifact: bool = False,
    artifact_root: Path | None = None,
    accepted_at: str | None = None,
) -> Tier12AcceptedPublish:
    """Atomically accept a writer batch into a Tier1/2 accepted attestation.

    Requires ``WRITTEN_UNPUBLISHED`` + ``PUBLISHABLE_SCAFFOLD``. Sets
    ``published=True`` only on success. ``cutover_allowed`` remains false
    even when ``allow_consumer_cutover`` is requested (Phase C hard gate).
    """

    write_batch = _require_write_batch(batch)
    _validate_prerequisites(write_batch)

    day = _compact_day(write_batch.decision_date)
    market = write_batch.market_context
    assert market is not None  # validated

    body = {
        "decision_date": day,
        "stock_states": [r.as_dict() for r in write_batch.stock_states],
        "market_context": market.as_dict(),
        "pit_excluded_count": write_batch.pit_excluded_count,
        "contract_version": CONTRACT_VERSION,
    }
    content_hash = _sha256_canonical(body)
    contract_hash = _sha256_canonical(
        {
            "contract_version": CONTRACT_VERSION,
            "dataset_ids": [DATASET_ID_STOCK, DATASET_ID_MARKET],
            "writer_id": WRITER_ID,
        }
    )
    # Stock-side lineage anchors the accepted partition; market hashes are
    # retained inside market_context for consumers that need them.
    first = write_batch.stock_states[0]
    batch_id = f"tier12_accept:{day}:{content_hash[:16]}"
    ts = accepted_at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    notes = (
        "phase_c_accepted_publish",
        "accepted_partition_equivalent",
        "not_consumer_cutover",
        "not_strategy_release",
        "not_pulse_mart_cutover",
        "not_full_universe",
        "canary_or_fixture_scale_ok",
    )
    if allow_consumer_cutover:
        notes = notes + ("allow_consumer_cutover_ignored_hard_gate",)

    accepted = Tier12AcceptedPublish(
        decision_date=day,
        status="ACCEPTED",
        published=True,
        cutover_allowed=False,
        batch_id=batch_id,
        dataset_ids=(DATASET_ID_STOCK, DATASET_ID_MARKET),
        contract_version=CONTRACT_VERSION,
        contract_hash=contract_hash,
        definition_version=str(first.definition_version),
        config_hash=str(first.config_hash),
        input_snapshot_id=str(first.input_snapshot_id),
        available_at=str(first.available_at),
        accepted_at=ts,
        stock_row_count=len(write_batch.stock_states),
        content_hash=content_hash,
        stock_states=write_batch.stock_states,
        market_context=market,
        notes=notes,
        source_writer_status=write_batch.status,
    )

    if emit_artifact:
        root = artifact_root
        if root is None:
            root = (
                Path(__file__).resolve().parents[2]
                / "data"
                / "lineage"
                / "tier12_publish_batches"
            )
        elif not root.is_absolute():
            root = Path(__file__).resolve().parents[2] / root
        _atomic_write_json(root / f"accepted_{day}.json", accepted.as_dict())

    return accepted


__all__ = [
    "CONTRACT_VERSION",
    "DATASET_ID_MARKET",
    "DATASET_ID_STOCK",
    "Tier12AcceptError",
    "Tier12AcceptedPublish",
    "accept_tier12_batch",
    "load_tier12_write_batch",
]
