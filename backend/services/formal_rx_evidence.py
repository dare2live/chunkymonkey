"""Formal RX evidence checks: holdout policy alignment + opaque seal.

These validators replace the ``formal_evidence_validators_not_implemented``
stub. They do not claim StrategyRelease, run Optuna, or load holdout
partitions into a worker bundle.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

import yaml


REPO = Path(__file__).resolve().parents[2]
HOLDOUT_POLICY_RELPATH = "backend/config/holdout_policy.yaml"
HOLDOUT_SEAL_RELPATH = "data/lineage/holdout_seal.json"


def _compact_day(value: Any) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())[:8]


def holdout_policy_start(repo: Path | None = None) -> str:
    root = repo or REPO
    raw = yaml.safe_load(
        (root / HOLDOUT_POLICY_RELPATH).read_text(encoding="utf-8")
    )
    if not isinstance(raw, Mapping) or "holdout_start" not in raw:
        raise ValueError("holdout_policy.yaml missing holdout_start")
    return _compact_day(raw["holdout_start"])


def holdout_policy_hash(repo: Path | None = None) -> str:
    root = repo or REPO
    return hashlib.sha256(
        (root / HOLDOUT_POLICY_RELPATH).read_bytes()
    ).hexdigest()


def build_holdout_seal_payload(repo: Path | None = None) -> dict[str, Any]:
    root = repo or REPO
    holdout = holdout_policy_start(root)
    policy_hash = holdout_policy_hash(root)
    seal_hash = hashlib.sha256(
        f"{holdout}:{policy_hash}".encode("utf-8")
    ).hexdigest()
    return {
        "holdout_start": holdout,
        "policy_relpath": HOLDOUT_POLICY_RELPATH,
        "policy_hash": policy_hash,
        "seal_hash": seal_hash,
        "opaque": True,
        "partitions_omitted": True,
    }


def write_holdout_seal(repo: Path | None = None) -> dict[str, Any]:
    root = repo or REPO
    payload = build_holdout_seal_payload(root)
    path = root / HOLDOUT_SEAL_RELPATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return payload


def validate_formal_rx_evidence(
    bundle: Any,
    *,
    repo: Path | None = None,
) -> tuple[str, ...]:
    """Return fail-closed reasons; empty tuple means evidence checks passed."""

    root = repo or REPO
    reasons: list[str] = []
    try:
        policy_holdout = holdout_policy_start(root)
    except (OSError, ValueError, yaml.YAMLError):
        return ("holdout_policy_unreadable",)

    bundle_holdout = _compact_day(getattr(bundle, "holdout_start", ""))
    if bundle_holdout != policy_holdout:
        reasons.append("holdout_start_mismatch_vs_policy")

    upper = _compact_day(getattr(bundle, "available_at_upper", ""))
    if upper and policy_holdout and upper >= policy_holdout:
        reasons.append("bundle_upper_crosses_holdout")

    if getattr(bundle, "read_only", None) is not True:
        reasons.append("bundle_not_read_only")

    for attr in ("snapshot_id", "snapshot_content_hash", "snapshot_config_hash"):
        if not str(getattr(bundle, attr, "") or "").strip():
            reasons.append(f"missing_{attr}")

    seal_path = root / HOLDOUT_SEAL_RELPATH
    if not seal_path.is_file():
        reasons.append("sealed_holdout_ref_missing")
        return tuple(reasons)
    try:
        seal = json.loads(seal_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        reasons.append("sealed_holdout_ref_unreadable")
        return tuple(reasons)
    if not isinstance(seal, Mapping):
        reasons.append("sealed_holdout_ref_not_object")
        return tuple(reasons)

    expected = build_holdout_seal_payload(root)
    if str(seal.get("holdout_start") or "") != expected["holdout_start"]:
        reasons.append("sealed_holdout_start_mismatch")
    if str(seal.get("policy_hash") or "") != expected["policy_hash"]:
        reasons.append("sealed_holdout_policy_hash_mismatch")
    if str(seal.get("seal_hash") or "") != expected["seal_hash"]:
        reasons.append("sealed_holdout_hash_mismatch")
    if seal.get("opaque") is not True:
        reasons.append("sealed_holdout_not_opaque")
    if "partitions" in seal or "date_set" in seal:
        reasons.append("sealed_holdout_leaks_partitions")
    return tuple(reasons)
