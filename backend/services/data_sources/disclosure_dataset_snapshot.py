"""Disclosure DatasetSnapshot freeze for Phase E.

Supports:
- canary single-partition freeze (``scope=canary_accepted_partitions``);
- bounded multi-partition freeze (``scope=bounded_accepted_partitions``)
  with explicit per-domain date sets + accepted hashes.

Institution_follow full B0→B4 ablation stays blocked until WF/paper exist;
measured B0 may attempt bare-K coverage under the bounded scope.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

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

SCOPE_CANARY = "canary_accepted_partitions"
SCOPE_BOUNDED = "bounded_accepted_partitions"
ABLATION_CANARY = "blocked_canary_scope_only"
ABLATION_BOUNDED = "bounded_scope_wf_paper_still_blocked"

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


def _compact_partition(value: Any) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())[:8]


def _partition_from_shadow(shadow: Mapping[str, Any], domain: str) -> str | None:
    for item in shadow.get("domains") or ():
        if str(item.get("domain")) == domain:
            part = item.get("partition")
            return _compact_partition(part) if part else None
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
    payload["partition"] = _compact_partition(payload["partition"])
    payload["row_count"] = int(payload["row_count"] or 0)
    return payload


def _normalize_partition_sets(
    partition_sets: Mapping[str, Sequence[str]] | None,
    *,
    shadow_payload: Mapping[str, Any],
) -> dict[str, list[str]]:
    """Resolve explicit date sets; default to one shadow serving partition."""

    out: dict[str, list[str]] = {}
    for domain in disclosure_domains():
        raw = list((partition_sets or {}).get(domain) or ())
        parts = sorted({_compact_partition(p) for p in raw if _compact_partition(p)})
        if not parts:
            shadow_part = _partition_from_shadow(shadow_payload, domain)
            if not shadow_part:
                raise DisclosureBoundaryError(
                    domain,
                    reason="snapshot_partition_missing",
                    detail="shadow domain partition or explicit partition_sets required",
                )
            parts = [shadow_part]
        out[domain] = parts
    return out


def freeze_disclosure_dataset_snapshot(
    domain_conns: Mapping[str, Any],
    *,
    shadow: Any,
    path: Path | str | None = None,
    partition_sets: Mapping[str, Sequence[str]] | None = None,
    extra_notes: Sequence[str] = (),
) -> DisclosureDatasetSnapshot:
    """Freeze DatasetSnapshot when three-domain cutover is allowed.

    Pass ``partition_sets`` with multiple dates per domain to freeze a
    ``bounded_accepted_partitions`` snapshot (explicit date sets + hashes).
    Omitting it keeps the canary single-partition freeze.
    """

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

    resolved = _normalize_partition_sets(partition_sets, shadow_payload=shadow_payload)
    multi = any(len(parts) > 1 for parts in resolved.values()) or bool(partition_sets)

    domains: dict[str, dict[str, Any]] = {}
    for domain in disclosure_domains():
        conn = domain_conns.get(domain)
        if conn is None:
            raise DisclosureBoundaryError(
                domain,
                reason="snapshot_domain_conn_missing",
                detail="each disclosure domain needs an accepted_partition connection",
            )
        accepted_rows = [
            _load_accepted(
                conn,
                dataset_id=_DATASET_BY_DOMAIN[domain],
                partition=part,
            )
            for part in resolved[domain]
        ]
        # Primary = latest partition (stable sort); full set under accepted[].
        primary = dict(accepted_rows[-1])
        domains[domain] = {
            **primary,
            "date_set": list(resolved[domain]),
            "accepted": accepted_rows,
        }

    frozen_at = datetime.now(timezone.utc).isoformat()
    if multi:
        scope = SCOPE_BOUNDED
        phase_e_ablation = ABLATION_BOUNDED
        parts_label = "-".join(
            f"{name}:{','.join(resolved[name])}" for name in disclosure_domains()
        )
        snapshot_id = f"disclosure_bounded_{parts_label}"
        notes = [
            "bounded_accepted_partitions_snapshot",
            "explicit_per_domain_date_sets",
            "institution_follow_full_ablation_still_blocked",
            "wf_paper_b1_still_residual",
            "feature_store_profiles_not_included",
        ]
    else:
        scope = SCOPE_CANARY
        phase_e_ablation = ABLATION_CANARY
        parts_label = "-".join(
            f"{name}:{resolved[name][0]}" for name in disclosure_domains()
        )
        snapshot_id = f"disclosure_e0_{parts_label}"
        notes = [
            "minimal_e0_gate_snapshot",
            "points_at_accepted_canary_partitions_only",
            "institution_follow_ablation_still_blocked",
            "feature_store_profiles_not_included",
        ]
    notes.extend(str(n) for n in extra_notes if n)

    snap = DisclosureDatasetSnapshot(
        snapshot_id=snapshot_id,
        frozen_at=frozen_at,
        scope=scope,
        cutover_allowed=True,
        domains=domains,
        shadow_overall_status=str(shadow_payload.get("overall_status") or ""),
        phase_e_ablation=phase_e_ablation,
        relpath=DISCLOSURE_SNAPSHOT_RELPATH,
        notes=tuple(notes),
    )

    target = Path(path) if path is not None else default_snapshot_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(snap.as_dict(), indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return snap


__all__ = [
    "ABLATION_BOUNDED",
    "ABLATION_CANARY",
    "DISCLOSURE_SNAPSHOT_RELPATH",
    "DisclosureDatasetSnapshot",
    "SCOPE_BOUNDED",
    "SCOPE_CANARY",
    "default_snapshot_path",
    "freeze_disclosure_dataset_snapshot",
]
