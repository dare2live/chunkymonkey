"""Phase F main_rally DatasetSnapshot freeze (F0).

Freezes accepted nominal K partitions, rally GT table content hashes +
``rally_gt.yaml`` config hash, and Tier1/2 accepted artifact days. Does not
rebuild GT, flip cutover, or emit StrategyRelease.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

import yaml

from services.data_sources.accepted_schema import ACCEPTED_TABLE
from services.data_sources.nominal_ohlcv_schema import DATASET_ID as NOMINAL_OHLCV_DATASET
from services.holdout_guard import training_cutoff_before_holdout
from services.research_runtime import (
    DatasetSnapshot,
    ResearchRuntimeError,
    SnapshotInputRef,
)

# Keep the on-disk freeze bounded (mirrors the prior ~121-day serving window)
# but always strictly before holdout.
_MAX_DEVELOPMENT_NOMINAL_DAYS = 130

MAIN_RALLY_SNAPSHOT_RELPATH = "data/lineage/main_rally_dataset_snapshot/snapshot.json"
SCOPE_CANARY = "canary_accepted_partitions"
SCOPE_BOUNDED = "bounded_accepted_partitions"
ABLATION_CANARY = "blocked_canary_scope_only"
ABLATION_BOUNDED = "bounded_scope_setup_entry_short_horizon"
STRATEGY_PACKAGE = "main_rally_v1"

_GT_TABLES = (
    "fact_rally_ground_truth",
    "fact_rally_negative",
    "fact_rally_strata",
)


@dataclass(frozen=True)
class MainRallyDatasetSnapshot:
    snapshot_id: str
    frozen_at: str
    scope: str
    cutover_allowed: bool
    strategy_package: str
    phase_f_ablation: str
    domains: dict[str, dict[str, Any]]
    relpath: str
    notes: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "snapshot_id": self.snapshot_id,
            "frozen_at": self.frozen_at,
            "scope": self.scope,
            "cutover_allowed": self.cutover_allowed,
            "strategy_package": self.strategy_package,
            "phase_f_ablation": self.phase_f_ablation,
            "domains": self.domains,
            "relpath": self.relpath,
            "notes": list(self.notes),
        }


class MainRallySnapshotError(RuntimeError):
    """Fail-closed snapshot freeze / load error."""


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def default_snapshot_path() -> Path:
    return _repo_root() / MAIN_RALLY_SNAPSHOT_RELPATH


def _compact_day(value: Any) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())[:8]


def _stable_hash(payload: Any) -> str:
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_cutover_allowed() -> bool:
    cfg_path = _repo_root() / "backend" / "config" / "tier12_publish.yaml"
    payload = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    cut = payload.get("consumer_cutover") or {}
    return bool(cut.get("cutover_allowed"))


def _list_accepted_nominal(
    conn,
) -> list[dict[str, Any]]:
    rows = conn.execute(
        f"""
        SELECT dataset_id,
               replace(CAST(partition_value AS VARCHAR), '-', '') AS partition,
               batch_id, contract_version, contract_hash, config_hash,
               row_count, content_hash,
               CAST(accepted_at AS VARCHAR) AS accepted_at
          FROM {ACCEPTED_TABLE}
         WHERE dataset_id = ?
         ORDER BY 2
        """,
        [NOMINAL_OHLCV_DATASET],
    ).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        # Positional — adapter Row key names for CAST expressions are unstable.
        vals = tuple(row)
        part = _compact_day(vals[1])
        if len(part) != 8:
            continue
        out.append(
            {
                "dataset_id": str(vals[0]),
                "partition": part,
                "batch_id": str(vals[2] or ""),
                "contract_version": str(vals[3] or ""),
                "contract_hash": str(vals[4] or ""),
                "config_hash": str(vals[5] or ""),
                "row_count": int(vals[6] or 0),
                "content_hash": str(vals[7] or ""),
                "accepted_at": str(vals[8] or ""),
            }
        )
    return out


def _hash_gt_table(conn, table: str) -> dict[str, Any]:
    n = int(conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0])
    # Content fingerprint without dumping all rows.
    if table == "fact_rally_ground_truth":
        row = conn.execute(
            f"""
            SELECT CAST(min(bottom_date) AS VARCHAR),
                   CAST(max(bottom_date) AS VARCHAR),
                   CAST(min(peak_date) AS VARCHAR),
                   CAST(max(peak_date) AS VARCHAR),
                   min(gain_to_peak_pct), max(gain_to_peak_pct),
                   any_value(taxonomy_version),
                   CAST(max(built_at) AS VARCHAR)
              FROM {table}
            """
        ).fetchone()
        fingerprint = {
            "min_bottom": row[0],
            "max_bottom": row[1],
            "min_peak": row[2],
            "max_peak": row[3],
            "min_gain": row[4],
            "max_gain": row[5],
            "taxonomy_version": row[6],
            "built_at": row[7],
            "row_count": n,
        }
    elif table == "fact_rally_negative":
        row = conn.execute(
            f"""
            SELECT CAST(min(entry_signal_date) AS VARCHAR),
                   CAST(max(entry_signal_date) AS VARCHAR),
                   any_value(taxonomy_version),
                   CAST(max(built_at) AS VARCHAR)
              FROM {table}
            """
        ).fetchone()
        fingerprint = {
            "min_entry": row[0],
            "max_entry": row[1],
            "taxonomy_version": row[2],
            "built_at": row[3],
            "row_count": n,
        }
    else:
        row = conn.execute(
            f"""
            SELECT CAST(min(bottom_date) AS VARCHAR),
                   CAST(max(bottom_date) AS VARCHAR),
                   CAST(max(built_at) AS VARCHAR)
              FROM {table}
            """
        ).fetchone()
        fingerprint = {
            "min_bottom": row[0],
            "max_bottom": row[1],
            "built_at": row[2],
            "row_count": n,
        }
    return {
        "row_count": n,
        "content_hash": _stable_hash(fingerprint),
        "fingerprint": fingerprint,
    }


def _tier12_accepted_partitions(artifact_dir: Path) -> list[str]:
    parts: list[str] = []
    if not artifact_dir.is_dir():
        return parts
    for path in sorted(artifact_dir.glob("accepted_*.json")):
        day = _compact_day(path.stem.replace("accepted_", ""))
        if len(day) == 8:
            parts.append(day)
    return parts


def freeze_main_rally_dataset_snapshot(
    *,
    nominal_conn=None,
    feature_store_conn=None,
    path: Path | str | None = None,
    extra_notes: Sequence[str] = (),
    through: str | None = None,
) -> MainRallyDatasetSnapshot:
    """Live-query accepted nominal + GT hashes; write frozen JSON.

    ``through`` defaults to the day before holdout. Serving-window dates at or
    after holdout_start must not enter a development freeze.
    """

    from services.data_access.resolver import connect_ro

    owned_nominal = False
    owned_fs = False
    conn = nominal_conn
    fs = feature_store_conn
    try:
        if conn is None:
            conn = connect_ro("tushare_raw")
            owned_nominal = True
        accepted = _list_accepted_nominal(conn)
        cutoff = _compact_day(through or training_cutoff_before_holdout())
        accepted = [row for row in accepted if row["partition"] <= cutoff]
        if len(accepted) > _MAX_DEVELOPMENT_NOMINAL_DAYS:
            accepted = accepted[-_MAX_DEVELOPMENT_NOMINAL_DAYS:]
        if not accepted:
            raise MainRallySnapshotError(
                "no accepted nominal_ohlcv partitions before holdout cutoff "
                f"{cutoff}"
            )

        if fs is None:
            fs = connect_ro("feature_store")
            owned_fs = True

        gt_tables: dict[str, Any] = {}
        taxonomy = ""
        for table in _GT_TABLES:
            meta = _hash_gt_table(fs, table)
            gt_tables[table] = meta
            if table == "fact_rally_ground_truth":
                taxonomy = str(
                    (meta.get("fingerprint") or {}).get("taxonomy_version") or ""
                )

        cfg_path = _repo_root() / "backend" / "config" / "rally_gt.yaml"
        cfg_hash = _file_sha256(cfg_path)
        cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
        if not taxonomy:
            taxonomy = str(cfg.get("taxonomy_version") or "")

        artifact_rel = "data/lineage/tier12_publish_batches"
        tier12_parts = [
            part
            for part in _tier12_accepted_partitions(_repo_root() / artifact_rel)
            if part <= cutoff
        ]
        cutover = _load_cutover_allowed()

        date_set = [a["partition"] for a in accepted]
        nom_content = _stable_hash([a["content_hash"] for a in accepted])
        nom_config = accepted[-1]["config_hash"] or _stable_hash(date_set)

        domains = {
            "nominal_ohlcv": {
                "dataset_id": NOMINAL_OHLCV_DATASET,
                "date_set": date_set,
                "accepted": accepted,
                "partition": date_set[-1],
                "content_hash": nom_content,
                "config_hash": nom_config,
                "row_count": sum(int(a["row_count"]) for a in accepted),
            },
            "rally_gt": {
                "taxonomy_version": taxonomy,
                "config_path": "backend/config/rally_gt.yaml",
                "config_hash": cfg_hash,
                "tables": gt_tables,
                # Labels are frozen evidence only — never candidate-generator inputs.
                "label_tables_not_for_candidates": True,
            },
            "tier12_accepted": {
                "partitions": tier12_parts,
                "artifact_dir": artifact_rel,
                "definition_version": "stock_state_stage_pattern_v1",
            },
        }

        frozen_at = datetime.now(timezone.utc).isoformat()
        scope = SCOPE_BOUNDED
        phase_f_ablation = ABLATION_BOUNDED
        snapshot_id = (
            f"main_rally_bounded_{date_set[0]}_{date_set[-1]}_"
            f"n{len(date_set)}_gt{taxonomy}"
        )
        notes = [
            "phase_f_f0_dataset_snapshot",
            "setup_entry_short_horizon_not_full_episode",
            "gt_labels_frozen_not_for_candidate_generator",
            "development_before_holdout",
            f"holdout_through={cutoff}",
            "no_optuna",
            "no_strategy_release",
            f"accepted_nominal_day_count={len(date_set)}",
            f"tier12_accepted={','.join(tier12_parts)}",
            f"cutover_allowed_echo={cutover}",
        ]
        notes.extend(str(n) for n in extra_notes if n)

        snap = MainRallyDatasetSnapshot(
            snapshot_id=snapshot_id,
            frozen_at=frozen_at,
            scope=scope,
            cutover_allowed=cutover,
            strategy_package=STRATEGY_PACKAGE,
            phase_f_ablation=phase_f_ablation,
            domains=domains,
            relpath=MAIN_RALLY_SNAPSHOT_RELPATH,
            notes=tuple(notes),
        )
        target = Path(path) if path is not None else default_snapshot_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(snap.as_dict(), indent=2, ensure_ascii=False, sort_keys=True)
            + "\n",
            encoding="utf-8",
        )
        return snap
    finally:
        if owned_fs and fs is not None:
            fs.close()
        if owned_nominal and conn is not None:
            conn.close()


def load_frozen_main_rally_snapshot(
    path: Path | str | None = None,
) -> dict[str, Any]:
    target = Path(path) if path is not None else default_snapshot_path()
    if not target.is_file():
        raise MainRallySnapshotError(f"main_rally DatasetSnapshot missing at {target}")
    payload = json.loads(target.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise MainRallySnapshotError("main_rally DatasetSnapshot must be an object")
    return payload


def dataset_snapshot_from_main_rally(
    payload: Mapping[str, Any],
    *,
    universe_id: str = "traded_on_observation_date",
) -> DatasetSnapshot:
    """Adapt frozen main_rally JSON into runtime ``DatasetSnapshot``."""

    snap_id = str(payload.get("snapshot_id") or "").strip()
    if not snap_id:
        raise ResearchRuntimeError("main_rally snapshot missing snapshot_id")
    domains = payload.get("domains") or {}
    if not isinstance(domains, Mapping) or not domains:
        raise ResearchRuntimeError("main_rally snapshot domains must be non-empty")

    inputs: list[SnapshotInputRef] = []
    all_dates: list[str] = []
    config_parts: list[str] = []
    content_parts: list[str] = []

    nominal = domains.get("nominal_ohlcv") or {}
    if isinstance(nominal, Mapping):
        date_set = [
            _compact_day(d)
            for d in (nominal.get("date_set") or ())
            if len(_compact_day(d)) == 8
        ]
        all_dates.extend(date_set)
        cfg = str(nominal.get("config_hash") or _stable_hash(date_set))
        content = str(nominal.get("content_hash") or _stable_hash(date_set))
        config_parts.append(cfg)
        content_parts.append(content)
        inputs.append(
            SnapshotInputRef(
                dataset_id=str(
                    nominal.get("dataset_id") or NOMINAL_OHLCV_DATASET
                ),
                partitions=tuple(sorted(set(date_set))),
                content_hash=content,
                config_hash=cfg,
            )
        )

    # Labels remain on disk as freeze evidence. Development ingress forbids
    # tier3.* inputs, so GT is not adapted into DatasetSnapshot.inputs.
    rally_gt = domains.get("rally_gt") or {}
    if isinstance(rally_gt, Mapping) and rally_gt:
        notes_extra_gt = True
    else:
        notes_extra_gt = False

    tier12 = domains.get("tier12_accepted") or {}
    if isinstance(tier12, Mapping):
        parts = [
            _compact_day(p)
            for p in (tier12.get("partitions") or ())
            if len(_compact_day(p)) == 8
        ]
        all_dates.extend(parts)
        content = _stable_hash(parts)
        config_parts.append(content)
        content_parts.append(content)
        if parts:
            inputs.append(
                SnapshotInputRef(
                    dataset_id="tier12.accepted_artifacts",
                    partitions=tuple(parts),
                    content_hash=content,
                    config_hash=content,
                )
            )

    if not inputs:
        raise ResearchRuntimeError("main_rally snapshot produced no inputs")
    if not all_dates:
        # GT-only freeze still needs bounds — use nominal if present else fail.
        raise ResearchRuntimeError("main_rally snapshot has no available_at dates")

    lower = min(all_dates)
    upper = max(all_dates)
    config_hash = _stable_hash(sorted(config_parts))
    content_hash = _stable_hash(
        {
            "snapshot_id": snap_id,
            "inputs": [i.as_dict() for i in inputs],
            "phase_f_ablation": payload.get("phase_f_ablation"),
            "strategy_package": payload.get("strategy_package"),
        }
    )
    notes = tuple(str(n) for n in (payload.get("notes") or ())) + (
        "adapted_from_main_rally_freeze",
        f"domains={','.join(sorted(domains))}",
        *(("gt_evidence_omitted_from_development_inputs",) if notes_extra_gt else ()),
    )
    frozen_at = str(
        payload.get("frozen_at") or datetime.now(timezone.utc).isoformat()
    )
    return DatasetSnapshot(
        snapshot_id=snap_id,
        inputs=tuple(inputs),
        universe_id=universe_id,
        config_hash=config_hash,
        available_at_lower=lower,
        available_at_upper=upper,
        content_hash=content_hash,
        frozen_at=frozen_at,
        source_kind="main_rally_freeze",
        notes=notes,
    )


__all__ = [
    "ABLATION_BOUNDED",
    "ABLATION_CANARY",
    "MAIN_RALLY_SNAPSHOT_RELPATH",
    "MainRallyDatasetSnapshot",
    "MainRallySnapshotError",
    "SCOPE_BOUNDED",
    "SCOPE_CANARY",
    "STRATEGY_PACKAGE",
    "dataset_snapshot_from_main_rally",
    "default_snapshot_path",
    "freeze_main_rally_dataset_snapshot",
    "load_frozen_main_rally_snapshot",
]
