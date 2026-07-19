"""Minimal disclosure DatasetSnapshot freeze for Phase E gate.

Points at accepted canary partitions + config/content hashes for the three E0
domains.  Scope is intentionally narrow (``canary_accepted_partitions``);
institution_follow ablation stays blocked until a broader snapshot exists.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from services.data_sources.accepted_schema import ACCEPTED_TABLE
from services.data_sources.disclosure_boundaries import (
    DisclosureBoundaryError,
    disclosure_domains,
    refuse_accepted_publication_claim,
)
from services.data_sources.disclosure_research_read import cutover_allowed_from_shadow
from services.data_sources.holders_top10_schema import DATASET_ID as HOLDERS_DATASET
from services.data_sources.org_holding_schema import DATASET_ID as ORG_DATASET
from services.data_sources.stk_holdertrade_schema import DATASET_ID as STK_DATASET

DISCLOSURE_SNAPSHOT_RELPATH = "data/lineage/disclosure_dataset_snapshot.json"

_DATASET_BY_DOMAIN = {
    "holders_top10": HOLDERS_DATASET,
    "org_holding": ORG_DATASET,
    "stk_holdertrade": STK_DATASET,
}


@dataclass(frozen=True)
class DisclosureDatasetSnapshot:
    snapshot_id: str
    frozen_at: str
    scope: str
    cutover_allowed: bool
    domains: dict[str, dict[str, Any]]
    shadow_overall_status: str
    phase_e_ablation: str
    relpath: str
    notes: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "snapshot_id": self.snapshot_id,
            "frozen_at": self.frozen_at,
            "scope": self.scope,
            "cutover_allowed": self.cutover_allowed,
            "domains": self.domains,
            "shadow_overall_status": self.shadow_overall_status,
            "phase_e_ablation": self.phase_e_ablation,
            "relpath": self.relpath,
            "notes": list(self.notes),
        }


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def default_snapshot_path() -> Path:
    return _repo_root() / DISCLOSURE_SNAPSHOT_RELPATH


def _partition_from_shadow(shadow: Mapping[str, Any], domain: str) -> str | None:
    for item in shadow.get("domains") or ():
        if str(item.get("domain")) == domain:
            part = item.get("partition")
            return str(part) if part else None
    return None


def _load_accepted(
    conn,
    *,
    dataset_id: str,
    partition: str,
) -> dict[str, Any]:
    row = conn.execute(
        f"""
        SELECT dataset_id, partition_value, batch_id, contract_version,
               contract_hash, config_hash, row_count, content_hash,
               CAST(accepted_at AS VARCHAR)
          FROM {ACCEPTED_TABLE}
         WHERE dataset_id = ?
           AND replace(CAST(partition_value AS VARCHAR), '-', '') = ?
         ORDER BY accepted_at DESC
         LIMIT 1
        """,
        [dataset_id, partition],
    ).fetchone()
    if not row:
        raise DisclosureBoundaryError(
            dataset_id,
            reason="accepted_partition_missing_for_snapshot",
            detail=f"dataset_id={dataset_id!r} partition={partition!r}",
        )
    keys = (
        "dataset_id",
        "partition",
        "batch_id",
        "contract_version",
        "contract_hash",
        "config_hash",
        "row_count",
        "content_hash",
        "accepted_at",
    )
    payload = dict(zip(keys, row, strict=True))
    payload["partition"] = "".join(
        ch for ch in str(payload["partition"]) if ch.isdigit()
    )[:8]
    payload["row_count"] = int(payload["row_count"] or 0)
    return payload


def freeze_disclosure_dataset_snapshot(
    domain_conns: Mapping[str, Any],
    *,
    shadow: Any,
    path: Path | str | None = None,
) -> DisclosureDatasetSnapshot:
    """Freeze a minimal canary DatasetSnapshot when three-domain cutover is allowed."""

    shadow_payload = shadow.as_dict() if hasattr(shadow, "as_dict") else dict(shadow)
    if not cutover_allowed_from_shadow(shadow_payload):
        raise DisclosureBoundaryError(
            "disclosure",
            reason="dataset_snapshot_blocked_until_e0_cutover",
            detail=(
                "freeze requires three-domain shadow MATCH on partitions "
                f"serving the response; overall={shadow_payload.get('overall_status')!r}"
            ),
        )

    for domain in disclosure_domains():
        refuse_accepted_publication_claim(
            domain, "DatasetSnapshot", cutover_allowed=True
        )

    domains: dict[str, dict[str, Any]] = {}
    for domain in disclosure_domains():
        conn = domain_conns.get(domain)
        if conn is None:
            raise DisclosureBoundaryError(
                domain,
                reason="snapshot_domain_conn_missing",
                detail="each disclosure domain needs an accepted_partition connection",
            )
        partition = _partition_from_shadow(shadow_payload, domain)
        if not partition:
            raise DisclosureBoundaryError(
                domain,
                reason="snapshot_partition_missing",
                detail="shadow domain partition required for freeze",
            )
        accepted = _load_accepted(
            conn,
            dataset_id=_DATASET_BY_DOMAIN[domain],
            partition=partition,
        )
        domains[domain] = accepted

    frozen_at = datetime.now(timezone.utc).isoformat()
    parts = "-".join(
        f"{name}:{domains[name]['partition']}" for name in disclosure_domains()
    )
    snapshot_id = f"disclosure_e0_{parts}"
    snap = DisclosureDatasetSnapshot(
        snapshot_id=snapshot_id,
        frozen_at=frozen_at,
        scope="canary_accepted_partitions",
        cutover_allowed=True,
        domains=domains,
        shadow_overall_status=str(shadow_payload.get("overall_status") or ""),
        phase_e_ablation="blocked_canary_scope_only",
        relpath=DISCLOSURE_SNAPSHOT_RELPATH,
        notes=(
            "minimal_e0_gate_snapshot",
            "points_at_accepted_canary_partitions_only",
            "institution_follow_ablation_still_blocked",
            "feature_store_profiles_not_included",
        ),
    )

    target = Path(path) if path is not None else default_snapshot_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(snap.as_dict(), indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return snap


__all__ = [
    "DISCLOSURE_SNAPSHOT_RELPATH",
    "DisclosureDatasetSnapshot",
    "default_snapshot_path",
    "freeze_disclosure_dataset_snapshot",
]
