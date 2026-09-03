"""Atomic research prereg + single-touch holdout token store (Tier3 research).

Closes the gap called out by the (retired) StrategyRelease contract §10 /
``holdout_guard``: training-boundary alone is not a release gate. This module
owns a **filesystem** evidence store with atomic create + single-touch consume.

Not a StrategyRelease runtime. Does not claim concurrent multi-writer safety
beyond atomic rename + exclusive create; tests cover double-touch fail-closed.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import yaml

REPO = Path(__file__).resolve().parents[2]
DEFAULT_STORE_DIR = REPO / "data" / "lineage" / "research_prereg"
HOLDOUT_POLICY_PATH = REPO / "backend" / "config" / "holdout_policy.yaml"
HOLDOUT_PROTOCOL_VERSION = "research_holdout_v1"


class ResearchPreregError(RuntimeError):
    """Fail-closed prereg / single-touch store error."""


def _stable_hash(payload: Any) -> str:
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def compute_holdout_scope_id(prereg_body: Mapping[str, Any]) -> str:
    """Stable global one-touch scope shared by B0–B5 of one protocol.

    Block, hypothesis, folds, dates, random token, and search parameters are
    intentionally excluded. The budget is anchored to the immutable snapshot,
    strategy, universe, protocol, and governed holdout-policy content.
    """

    body = dict(prereg_body)
    policy = yaml.safe_load(HOLDOUT_POLICY_PATH.read_text(encoding="utf-8")) or {}
    scope = {
        "protocol_version": str(
            body.get("holdout_protocol_version") or HOLDOUT_PROTOCOL_VERSION
        ),
        "holdout_policy_hash": _stable_hash(policy),
        "strategy_package": str(body.get("strategy_package") or ""),
        "snapshot_content_hash": str(
            body.get("snapshot_content_hash") or body.get("snapshot_id") or ""
        ),
        "universe_id": str(body.get("universe_id") or ""),
    }
    if not scope["strategy_package"] or not scope["snapshot_content_hash"]:
        # Generic primitive callers remain supported, but their scope is still
        # stable and cannot be reset by a fresh random token.
        scope["fallback_param_hash"] = compute_param_hash(body)
    return _stable_hash(scope)


@dataclass(frozen=True)
class RegisteredPrereg:
    prereg_id: str
    param_hash: str
    single_touch_token: str
    holdout_scope_id: str
    holdout_touched: bool
    registered_at: str
    payload: dict[str, Any]
    path: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "prereg_id": self.prereg_id,
            "param_hash": self.param_hash,
            "single_touch_token": self.single_touch_token,
            "holdout_scope_id": self.holdout_scope_id,
            "holdout_touched": self.holdout_touched,
            "registered_at": self.registered_at,
            "payload": dict(self.payload),
            "path": self.path,
        }


@dataclass(frozen=True)
class ExperimentPrereg:
    """Immutable prereg bound to a DatasetSnapshot before measure (contract §5).

    ``param_hash`` freezes the body; ``single_touch_token`` keys one-touch holdout.
    ``fold_embargo`` is a ``FoldEmbargoHooks`` (typed at call sites).
    """

    hypothesis: str
    primary_metric: str
    stop_conditions: tuple[str, ...]
    search_space: tuple[str, ...]
    fold_embargo: Any
    strategy_package: str
    block: str
    snapshot_id: str
    snapshot_content_hash: str
    universe_id: str
    config_hash: str
    available_at_lower: str
    available_at_upper: str
    random_seed: int
    claimable_target: bool
    holdout_protocol_version: str = HOLDOUT_PROTOCOL_VERSION
    holdout_scope_id: str = ""
    param_hash: str = ""
    single_touch_token: str = ""

    def __post_init__(self) -> None:
        for field, label in (
            (self.hypothesis, "hypothesis"),
            (self.primary_metric, "primary_metric"),
            (self.strategy_package, "strategy_package"),
            (self.block, "block"),
            (self.snapshot_id, "snapshot_id"),
            (self.snapshot_content_hash, "snapshot_content_hash"),
            (self.universe_id, "universe_id"),
            (self.config_hash, "config_hash"),
        ):
            if not str(field or "").strip():
                raise ValueError(f"{label} is required")
        lower = "".join(ch for ch in str(self.available_at_lower) if ch.isdigit())[:8]
        upper = "".join(ch for ch in str(self.available_at_upper) if ch.isdigit())[:8]
        if len(lower) != 8 or len(upper) != 8:
            raise ValueError("prereg available_at bounds must be YYYYMMDD")
        if lower > upper:
            raise ValueError("prereg available_at_lower must be <= available_at_upper")
        object.__setattr__(self, "available_at_lower", lower)
        object.__setattr__(self, "available_at_upper", upper)
        if self.claimable_target:
            raise ValueError(
                "Phase D prereg claimable_target must be false "
                "(no StrategyRelease / no claimable accept via runtime)"
            )
        scope_body = {
            "hypothesis": self.hypothesis,
            "primary_metric": self.primary_metric,
            "stop_conditions": list(self.stop_conditions),
            "search_space": list(self.search_space),
            "fold_embargo": self.fold_embargo.as_dict(),
            "strategy_package": self.strategy_package,
            "block": self.block,
            "snapshot_id": self.snapshot_id,
            "snapshot_content_hash": self.snapshot_content_hash,
            "universe_id": self.universe_id,
            "config_hash": self.config_hash,
            "available_at_lower": lower,
            "available_at_upper": upper,
            "random_seed": self.random_seed,
            "claimable_target": self.claimable_target,
            "holdout_protocol_version": self.holdout_protocol_version,
        }
        computed_scope = compute_holdout_scope_id(scope_body)
        if self.holdout_scope_id and self.holdout_scope_id != computed_scope:
            raise ValueError(
                "holdout_scope_id mismatch: "
                f"provided={self.holdout_scope_id!r} computed={computed_scope!r}"
            )
        object.__setattr__(self, "holdout_scope_id", computed_scope)
        body = {**scope_body, "holdout_scope_id": computed_scope}
        computed = compute_param_hash(body)
        if self.param_hash and self.param_hash != computed:
            raise ValueError(
                f"param_hash mismatch: provided={self.param_hash!r} computed={computed!r}"
            )
        object.__setattr__(self, "param_hash", computed)
        if not str(self.single_touch_token or "").strip():
            object.__setattr__(self, "single_touch_token", uuid.uuid4().hex)

    def as_dict(self) -> dict[str, Any]:
        return {
            "hypothesis": self.hypothesis,
            "primary_metric": self.primary_metric,
            "stop_conditions": list(self.stop_conditions),
            "search_space": list(self.search_space),
            "fold_embargo": self.fold_embargo.as_dict(),
            "strategy_package": self.strategy_package,
            "block": self.block,
            "snapshot_id": self.snapshot_id,
            "snapshot_content_hash": self.snapshot_content_hash,
            "universe_id": self.universe_id,
            "config_hash": self.config_hash,
            "available_at_lower": self.available_at_lower,
            "available_at_upper": self.available_at_upper,
            "random_seed": self.random_seed,
            "claimable_target": self.claimable_target,
            "holdout_protocol_version": self.holdout_protocol_version,
            "holdout_scope_id": self.holdout_scope_id,
            "param_hash": self.param_hash,
            "single_touch_token": self.single_touch_token,
        }


def compute_param_hash(prereg_body: Mapping[str, Any]) -> str:
    """Hash frozen prereg fields excluding touch token / registration metadata."""
    body = {
        k: v
        for k, v in dict(prereg_body).items()
        if k
        not in {
            "single_touch_token",
            "param_hash",
            "prereg_id",
            "registered_at",
            "holdout_touched",
            "path",
        }
    }
    return _stable_hash(body)


def default_store_dir() -> Path:
    return DEFAULT_STORE_DIR


def _token_path(store_dir: Path, token: str) -> Path:
    safe = "".join(ch for ch in token if ch.isalnum() or ch in "-_")
    if not safe or safe != token:
        raise ResearchPreregError(f"invalid single_touch_token={token!r}")
    return store_dir / f"{safe}.json"


def _scope_marker_path(store_dir: Path, scope_id: str) -> Path:
    safe = "".join(ch for ch in scope_id if ch.isalnum() or ch in "-_")
    if not safe or safe != scope_id:
        raise ResearchPreregError(f"invalid holdout_scope_id={scope_id!r}")
    return store_dir / f"{safe}.holdout_consumed"


def _fsync_dir(path: Path) -> None:
    fd = os.open(str(path), os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(dict(payload), fh, indent=2, ensure_ascii=False, sort_keys=True)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, path)
        _fsync_dir(path.parent)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise


def _exclusive_create_json(path: Path, payload: Mapping[str, Any]) -> None:
    """Atomically publish a complete JSON file only when ``path`` is absent."""

    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(dict(payload), fh, ensure_ascii=False, sort_keys=True)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.link(tmp_path, path)
        tmp_path.unlink()
        _fsync_dir(path.parent)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise


def register_prereg(
    prereg_body: Mapping[str, Any],
    *,
    store_dir: Path | str | None = None,
    single_touch_token: str | None = None,
    prereg_id: str | None = None,
) -> RegisteredPrereg:
    """Atomically register a prereg record with param_hash + single-touch token.

    Idempotent when the same ``param_hash`` + token already exists with equal
    payload. Conflicts (same token, different hash) fail closed.
    """
    root = Path(store_dir) if store_dir is not None else default_store_dir()
    root.mkdir(parents=True, exist_ok=True)
    param_hash = compute_param_hash(prereg_body)
    scope_id = str(
        prereg_body.get("holdout_scope_id")
        or compute_holdout_scope_id(prereg_body)
    )
    token = (single_touch_token or uuid.uuid4().hex).strip()
    if len(token) < 8:
        raise ResearchPreregError("single_touch_token too short")
    rid = (prereg_id or uuid.uuid4().hex[:16]).strip()
    path = _token_path(root, token)
    registered_at = datetime.now(timezone.utc).isoformat()
    record = {
        "prereg_id": rid,
        "param_hash": param_hash,
        "single_touch_token": token,
        "holdout_scope_id": scope_id,
        "holdout_touched": False,
        "registered_at": registered_at,
        "payload": dict(prereg_body),
    }

    if path.exists():
        existing = json.loads(path.read_text(encoding="utf-8"))
        validated = _validate_record(existing, expected_token=token, path=path)
        if validated.param_hash != param_hash:
            raise ResearchPreregError(
                f"single_touch_token={token!r} already registered with different param_hash"
            )
        if validated.holdout_scope_id != scope_id:
            raise ResearchPreregError(
                f"single_touch_token={token!r} registered with different holdout_scope_id"
            )
        return validated

    try:
        fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    except FileExistsError as exc:
        raise ResearchPreregError(
            f"single_touch_token={token!r} raced another writer"
        ) from exc
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(record, fh, indent=2, ensure_ascii=False, sort_keys=True)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        _fsync_dir(root)
    except Exception:
        path.unlink(missing_ok=True)
        raise

    return RegisteredPrereg(
        prereg_id=rid,
        param_hash=param_hash,
        single_touch_token=token,
        holdout_scope_id=scope_id,
        holdout_touched=False,
        registered_at=registered_at,
        payload=dict(prereg_body),
        path=str(path),
    )


def _validate_record(
    raw: Mapping[str, Any],
    *,
    expected_token: str,
    path: Path,
) -> RegisteredPrereg:
    payload_raw = raw.get("payload")
    if not isinstance(payload_raw, Mapping):
        raise ResearchPreregError(f"invalid prereg payload at {path}")
    payload = dict(payload_raw)
    token = str(raw.get("single_touch_token") or "")
    if token != expected_token:
        raise ResearchPreregError(
            f"prereg token mismatch at {path}: record={token!r} expected={expected_token!r}"
        )
    stored_hash = str(raw.get("param_hash") or "")
    computed_hash = compute_param_hash(payload)
    if not stored_hash or stored_hash != computed_hash:
        raise ResearchPreregError(
            f"prereg param_hash mismatch at {path}: "
            f"stored={stored_hash!r} computed={computed_hash!r}"
        )
    payload_hash = str(payload.get("param_hash") or stored_hash)
    if payload_hash != stored_hash:
        raise ResearchPreregError(f"payload param_hash mismatch at {path}")
    payload_token = str(payload.get("single_touch_token") or token)
    if payload_token != token:
        raise ResearchPreregError(f"payload token mismatch at {path}")
    scope_id = str(
        raw.get("holdout_scope_id")
        or payload.get("holdout_scope_id")
        or compute_holdout_scope_id(payload)
    )
    computed_scope = compute_holdout_scope_id(payload)
    if scope_id != computed_scope:
        raise ResearchPreregError(
            f"holdout_scope_id mismatch at {path}: "
            f"stored={scope_id!r} computed={computed_scope!r}"
        )
    marker = _scope_marker_path(path.parent, scope_id)
    legacy_marker = path.parent / f"{token}.holdout_consumed"
    touched = (
        bool(raw.get("holdout_touched"))
        or marker.exists()
        or legacy_marker.exists()
    )
    if marker.exists():
        try:
            marker_raw = json.loads(marker.read_text(encoding="utf-8"))
        except Exception as exc:
            raise ResearchPreregError(f"invalid holdout marker at {marker}") from exc
        if (
            str(marker_raw.get("holdout_scope_id") or "") != scope_id
            or str(marker_raw.get("purpose") or "") != "holdout"
        ):
            raise ResearchPreregError(f"holdout marker mismatch at {marker}")
    return RegisteredPrereg(
        prereg_id=str(raw.get("prereg_id") or ""),
        param_hash=stored_hash,
        single_touch_token=token,
        holdout_scope_id=scope_id,
        holdout_touched=touched,
        registered_at=str(raw.get("registered_at") or ""),
        payload=payload,
        path=str(path),
    )


def load_prereg_by_token(
    token: str,
    *,
    store_dir: Path | str | None = None,
) -> RegisteredPrereg:
    root = Path(store_dir) if store_dir is not None else default_store_dir()
    path = _token_path(root, token)
    if not path.exists():
        raise ResearchPreregError(f"unknown single_touch_token={token!r}")
    raw = json.loads(path.read_text(encoding="utf-8"))
    return _validate_record(raw, expected_token=token, path=path)


def consume_single_touch(
    token: str,
    *,
    store_dir: Path | str | None = None,
    purpose: str = "holdout",
) -> RegisteredPrereg:
    """Mark holdout as touched exactly once (atomic via O_EXCL marker).

    Concurrent callers: at most one creates ``{token}.holdout_consumed``;
    the loser fails closed. The JSON record is updated after the marker wins.
    """
    if purpose != "holdout":
        raise ResearchPreregError(f"unsupported touch purpose={purpose!r}")
    root = Path(store_dir) if store_dir is not None else default_store_dir()
    root.mkdir(parents=True, exist_ok=True)
    path = _token_path(root, token)
    if not path.exists():
        raise ResearchPreregError(f"unknown single_touch_token={token!r}")
    registered = load_prereg_by_token(token, store_dir=root)
    if registered.holdout_touched:
        raise ResearchPreregError(
            f"holdout_scope_id={registered.holdout_scope_id!r} already consumed "
            "for holdout"
        )

    marker = _scope_marker_path(root, registered.holdout_scope_id)
    marker_payload = {
        "single_touch_token": token,
        "holdout_scope_id": registered.holdout_scope_id,
        "param_hash": registered.param_hash,
        "purpose": purpose,
        "consumed_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        _exclusive_create_json(marker, marker_payload)
    except FileExistsError as exc:
        raise ResearchPreregError(
            f"single_touch_token={token!r} already consumed for holdout"
        ) from exc
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        validated = _validate_record(raw, expected_token=token, path=path)
        if bool(raw.get("holdout_touched")):
            # Marker won but JSON already marked — treat as consumed.
            raise ResearchPreregError(
                f"single_touch_token={token!r} already consumed for holdout"
            )
        raw["holdout_touched"] = True
        raw["holdout_touched_at"] = datetime.now(timezone.utc).isoformat()
        raw["holdout_touch_purpose"] = purpose
        _atomic_write_json(path, raw)
    except ResearchPreregError:
        # Marker already proves consumption — keep it so peers fail closed.
        raise
    except Exception:
        # The exclusive marker is the durable one-touch truth. Once published,
        # no later record-projection/fsync failure may reopen the budget.
        raise
    return load_prereg_by_token(token, store_dir=root)


__all__ = [
    "DEFAULT_STORE_DIR",
    "ExperimentPrereg",
    "HOLDOUT_PROTOCOL_VERSION",
    "RegisteredPrereg",
    "ResearchPreregError",
    "compute_holdout_scope_id",
    "compute_param_hash",
    "consume_single_touch",
    "default_store_dir",
    "load_prereg_by_token",
    "register_prereg",
]
