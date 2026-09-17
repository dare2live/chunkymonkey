"""Typed loader for ``backend/config/nominal_ohlcv_contract_versions.yaml``.

**Why this is a separate module from ``nominal_ohlcv_acquire_rules.py``**
(correction, blocking-finding repair pass: an earlier revision of this
docstring attributed this split to "spec 修订4" — that is wrong and has been
removed. 修订4 in this task's revision list is unrelated: it is "北交所与
股票池判定复用 services.universe" (see ``fuyao_daily_k.py:165`` for its real
citation). The actual authority for keeping hash constants in a typed
YAML+loader module, rather than hardcoded literals in a script, is the
业主 09-16 param rule quoted in this repair task's instructions: "版本号或
哈希常量...都属于参数...一律放进 backend/config/ 下的 typed YAML,由模块里的
loader 读取"— that rule postdates and supersedes the older cut-2 spec text
that had literally asked for these three hashes to be hardcoded
(``写死``) directly in ``restamp_nominal_ohlcv_contract.py``):
that module's config governs *acquisition* (which ``pre_close_origin`` values
are legal, what reference source/tolerance to use). This one governs
*rollback* — a completely different lifecycle concern (disaster recovery to a
prior contract version), consumed only by
``backend/scripts/restamp_nominal_ohlcv_contract.py``'s ``--to-v1`` path.
Keeping them apart means a change to acquisition rules can never accidentally
touch the rollback registry's fail-closed validation, and vice versa.

**What the values mean**: each ``rollback_targets`` entry records the
*complete* schema/config/contract hash triple that a named historical
``contract_version`` actually hashed to, at the moment that version was
current. These are one-time historical facts, not something any code path can
recompute today — the running code *is* the newer version, and its contract
factory only knows how to hash *its own* current shape. Recomputing an old
version's hash requires literally checking out that old code and running its
own contract factory (done once, offline, when this file was authored — see
the YAML header comment for the exact method and inputs). CI runs on a
shallow clone with no git history, so this file exists precisely so no
runtime path — script or test — ever needs ``git`` to answer "what did v1
hash to".

Fail-closed: unknown top-level keys, an empty or missing ``rollback_targets``
map, a per-version entry with unknown/missing keys, or any hash value that
isn't a 64-character lowercase hex string all raise ``ValueError``.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

import yaml

# backend/services/data_sources/nominal_ohlcv_contract_versions.py — two
# parents up is backend/.
_CONFIG_PATH = (
    Path(__file__).resolve().parent.parent.parent / "config" / "nominal_ohlcv_contract_versions.yaml"
)

_TOP_LEVEL_KEYS = frozenset({"rollback_targets"})
_TARGET_KEYS = frozenset({"schema_hash", "config_hash", "contract_hash", "derived_from"})
_HEX64_RE = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class RollbackTarget:
    contract_version: str
    schema_hash: str
    config_hash: str
    contract_hash: str
    derived_from: str


def load_rollback_targets(path: Path | None = None) -> Mapping[str, RollbackTarget]:
    """Load and validate every registered rollback target, keyed by
    ``contract_version`` (as a string — YAML integer-looking keys must be
    quoted in the source file so they round-trip as strings)."""

    cfg_path = path if path is not None else _CONFIG_PATH
    raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))

    if not isinstance(raw, dict) or set(raw) != _TOP_LEVEL_KEYS:
        raise ValueError(
            f"nominal_ohlcv_contract_versions: 顶层键必须恰好是 {sorted(_TOP_LEVEL_KEYS)}, "
            f"got {sorted(raw) if isinstance(raw, dict) else type(raw).__name__}"
        )

    targets_raw = raw["rollback_targets"]
    if not isinstance(targets_raw, dict) or not targets_raw:
        raise ValueError("nominal_ohlcv_contract_versions: rollback_targets 不能为空")

    targets: dict[str, RollbackTarget] = {}
    for version, entry in targets_raw.items():
        version_str = str(version)
        if not isinstance(entry, dict) or set(entry) != _TARGET_KEYS:
            raise ValueError(
                f"nominal_ohlcv_contract_versions: rollback_targets[{version_str!r}] 键必须恰好是 "
                f"{sorted(_TARGET_KEYS)}, got "
                f"{sorted(entry) if isinstance(entry, dict) else type(entry).__name__}"
            )
        for hash_key in ("schema_hash", "config_hash", "contract_hash"):
            value = entry[hash_key]
            if not isinstance(value, str) or not _HEX64_RE.match(value):
                raise ValueError(
                    f"nominal_ohlcv_contract_versions: rollback_targets[{version_str!r}]."
                    f"{hash_key} 必须是 64 位小写十六进制, got {value!r}"
                )
        derived_from = entry["derived_from"]
        if not isinstance(derived_from, str) or not derived_from.strip():
            raise ValueError(
                f"nominal_ohlcv_contract_versions: rollback_targets[{version_str!r}]."
                "derived_from 必须是非空字符串"
            )
        targets[version_str] = RollbackTarget(
            contract_version=version_str,
            schema_hash=entry["schema_hash"],
            config_hash=entry["config_hash"],
            contract_hash=entry["contract_hash"],
            derived_from=derived_from,
        )
    return targets


def load_rollback_target(contract_version: str, *, path: Path | None = None) -> RollbackTarget:
    """Look up one registered rollback target. Raises ``ValueError`` (not
    ``KeyError``) when unregistered — fail-closed like every other lookup in
    this module, so a caller cannot mistake a typo'd version for "no rollback
    needed"."""

    targets = load_rollback_targets(path)
    target = targets.get(str(contract_version))
    if target is None:
        raise ValueError(
            f"nominal_ohlcv_contract_versions: 未登记 contract_version={contract_version!r} "
            f"的回退目标 (已登记: {sorted(targets)})"
        )
    return target


__all__ = ["RollbackTarget", "load_rollback_target", "load_rollback_targets"]
