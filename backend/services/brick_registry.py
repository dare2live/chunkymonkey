"""L2/L3 brick registry — strangler for FeatureBlock + primitive lineage gates.

Authority: analysis/data_brick_architecture_20260721.md (B5).
Orthogonal to legacy backend/config/data_layers.yaml (physical/wiped L2_feature/L3_model).

Rules enforced cheaply:
  - L2 primitives depend only on L1/L0 reference nodes or other L2 (no L3/L4)
  - L3 feature_blocks: max_composite_hops (default 2) along L3 chain
  - no silent raw_* / raw_tushare_* depends_on without allow_raw_bypass
  - every FEATURE_BLOCK_ID in backend/services must be registered (orphan = FAIL)
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REGISTRY = REPO_ROOT / "backend" / "config" / "brick_registry.yaml"

_FEATURE_BLOCK_ID_RE = re.compile(
    r'^FEATURE_BLOCK_ID\s*=\s*["\']([^"\']+)["\']\s*$',
    re.MULTILINE,
)
_RAW_DEP_RE = re.compile(r"^raw(_tushare)?_", re.IGNORECASE)

ALLOWED_LAYERS = frozenset({"L0", "L1", "L2", "L3"})
L2_KINDS = frozenset({"primitive"})
L3_KINDS = frozenset({"feature_block", "composite"})


@dataclass(frozen=True)
class RefNode:
    node_id: str
    layer: str
    kind: str


@dataclass(frozen=True)
class Brick:
    brick_id: str
    layer: str
    kind: str
    depends_on: tuple[str, ...]
    owners: tuple[str, ...]
    config_hash: str
    availability_axis: str
    outputs: tuple[str, ...] = ()
    status: str = "declared"
    allow_raw_bypass: bool = False
    notes: str = ""


@dataclass(frozen=True)
class BrickRegistry:
    version: int
    max_composite_hops: int
    reference_nodes: dict[str, RefNode]
    bricks: dict[str, Brick]
    feature_blocks: dict[str, Brick]
    path: Path | None = None

    def node_layer(self, node_id: str) -> str | None:
        if node_id in self.reference_nodes:
            return self.reference_nodes[node_id].layer
        # Prefer feature_blocks when ids collide (should not; load_registry rejects).
        if node_id in self.feature_blocks:
            return self.feature_blocks[node_id].layer
        if node_id in self.bricks:
            return self.bricks[node_id].layer
        return None

    def all_registered_ids(self) -> set[str]:
        return (
            set(self.reference_nodes)
            | set(self.bricks)
            | set(self.feature_blocks)
        )


def _as_str_tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, (list, tuple)):
        return tuple(str(x) for x in value)
    raise ValueError(f"expected list/str, got {type(value).__name__}")


def _parse_brick(brick_id: str, meta: dict[str, Any], *, default_layer: str) -> Brick:
    if not isinstance(meta, dict):
        raise ValueError(f"{brick_id}: entry must be a mapping")
    layer = str(meta.get("layer") or default_layer)
    kind = str(meta.get("kind") or ("feature_block" if default_layer == "L3" else "primitive"))
    config_hash = meta.get("config_hash")
    if not isinstance(config_hash, str) or not config_hash.strip():
        raise ValueError(f"{brick_id}: config_hash required")
    axis = meta.get("availability_axis")
    if not isinstance(axis, str) or not axis.strip():
        raise ValueError(f"{brick_id}: availability_axis required")
    return Brick(
        brick_id=brick_id,
        layer=layer,
        kind=kind,
        depends_on=_as_str_tuple(meta.get("depends_on")),
        owners=_as_str_tuple(meta.get("owners")),
        config_hash=config_hash.strip(),
        availability_axis=axis.strip(),
        outputs=_as_str_tuple(meta.get("outputs")),
        status=str(meta.get("status") or "declared"),
        allow_raw_bypass=bool(meta.get("allow_raw_bypass") or False),
        notes=str(meta.get("notes") or ""),
    )


def load_registry(path: Path | None = None) -> BrickRegistry:
    reg_path = path or DEFAULT_REGISTRY
    raw = yaml.safe_load(reg_path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"invalid brick_registry root at {reg_path}")
    version = int(raw.get("version") or 0)
    if version != 1:
        raise ValueError(f"brick_registry.yaml version must be 1 (got {version!r})")
    max_hops = int(raw.get("max_composite_hops") or 2)
    if max_hops < 1:
        raise ValueError("max_composite_hops must be >= 1")

    refs: dict[str, RefNode] = {}
    for node_id, meta in (raw.get("reference_nodes") or {}).items():
        if not isinstance(meta, dict):
            raise ValueError(f"reference_nodes.{node_id}: must be mapping")
        layer = str(meta.get("layer") or "")
        if layer not in ALLOWED_LAYERS:
            raise ValueError(f"reference_nodes.{node_id}: invalid layer {layer!r}")
        refs[str(node_id)] = RefNode(
            node_id=str(node_id),
            layer=layer,
            kind=str(meta.get("kind") or "reference"),
        )

    bricks: dict[str, Brick] = {}
    for brick_id, meta in (raw.get("bricks") or {}).items():
        bricks[str(brick_id)] = _parse_brick(str(brick_id), meta, default_layer="L2")

    feature_blocks: dict[str, Brick] = {}
    for fb_id, meta in (raw.get("feature_blocks") or {}).items():
        feature_blocks[str(fb_id)] = _parse_brick(str(fb_id), meta, default_layer="L3")

    overlap = set(bricks) & set(feature_blocks)
    if overlap:
        raise ValueError(
            "brick_id collision between bricks and feature_blocks: "
            + ", ".join(sorted(overlap))
        )
    ref_overlap = set(refs) & (set(bricks) | set(feature_blocks))
    if ref_overlap:
        raise ValueError(
            "node id collision with reference_nodes: "
            + ", ".join(sorted(ref_overlap))
        )

    return BrickRegistry(
        version=version,
        max_composite_hops=max_hops,
        reference_nodes=refs,
        bricks=bricks,
        feature_blocks=feature_blocks,
        path=reg_path,
    )


def discover_feature_block_ids(repo: Path | None = None) -> set[str]:
    """Scan backend/services for FEATURE_BLOCK_ID = \"...\" declarations."""
    root = (repo or REPO_ROOT) / "backend" / "services"
    found: set[str] = set()
    if not root.is_dir():
        return found
    for path in sorted(root.rglob("*.py")):
        if path.name.startswith("test_") or "/tests/" in path.as_posix():
            continue
        text = path.read_text(encoding="utf-8")
        for match in _FEATURE_BLOCK_ID_RE.finditer(text):
            found.add(match.group(1))
    return found


def orphan_feature_blocks(
    registry: BrickRegistry,
    *,
    discovered: Iterable[str] | None = None,
) -> list[str]:
    found = set(discovered) if discovered is not None else discover_feature_block_ids()
    missing = sorted(found - set(registry.feature_blocks))
    return missing


def composite_hop_depth(feature_block_id: str, registry: BrickRegistry) -> int:
    """Longest L3-edge chain from this feature_block down to a non-L3 node.

    L3 → L2 = 1 hop; L3 → L3 → L2 = 2 hops. Unresolved deps count as terminal.
    """
    memo: dict[str, int] = {}
    visiting: set[str] = set()

    def _depth(node_id: str) -> int:
        if node_id in memo:
            return memo[node_id]
        if node_id in visiting:
            # cycle — treat as over-cap so gate fails closed
            return registry.max_composite_hops + 1
        fb = registry.feature_blocks.get(node_id)
        if fb is None:
            memo[node_id] = 0
            return 0
        visiting.add(node_id)
        best = 0
        for dep in fb.depends_on:
            dep_layer = registry.node_layer(dep)
            if dep in registry.feature_blocks or dep_layer == "L3":
                best = max(best, 1 + _depth(dep))
            else:
                best = max(best, 1)
        visiting.remove(node_id)
        memo[node_id] = best
        return best

    return _depth(feature_block_id)


def collect_violations(
    registry: BrickRegistry | None = None,
    *,
    repo: Path | None = None,
    discovered: set[str] | None = None,
) -> list[str]:
    reg = registry or load_registry()
    root = repo or REPO_ROOT
    viol: list[str] = []

    # --- schema / layer honesty ---
    for bid, brick in reg.bricks.items():
        if brick.layer != "L2":
            viol.append(f"brick {bid}: layer must be L2 (got {brick.layer!r})")
        if brick.kind not in L2_KINDS:
            viol.append(f"brick {bid}: kind must be primitive (got {brick.kind!r})")
        if not brick.owners:
            viol.append(f"brick {bid}: owners required")

    for fid, fb in reg.feature_blocks.items():
        if fb.layer != "L3":
            viol.append(f"feature_block {fid}: layer must be L3 (got {fb.layer!r})")
        if fb.kind not in L3_KINDS:
            viol.append(
                f"feature_block {fid}: kind must be feature_block|composite "
                f"(got {fb.kind!r})"
            )
        if not fb.owners:
            viol.append(f"feature_block {fid}: owners required")

    known = reg.all_registered_ids()

    def _check_deps(entry: Brick, *, label: str) -> None:
        for dep in entry.depends_on:
            if _RAW_DEP_RE.match(dep) and not entry.allow_raw_bypass:
                viol.append(
                    f"{label} {entry.brick_id}: silent raw bypass via depends_on={dep!r} "
                    "(set allow_raw_bypass only with explicit inventory evidence)"
                )
            if dep not in known:
                viol.append(
                    f"{label} {entry.brick_id}: unknown depends_on={dep!r} "
                    "(register as brick, feature_block, or reference_node)"
                )
                continue
            dep_layer = reg.node_layer(dep)
            if entry.layer == "L2" and dep_layer in {"L3", "L4"}:
                viol.append(
                    f"L2 {entry.brick_id}: must not depend on L3/L4 node {dep!r}"
                )
            if entry.layer == "L2" and dep_layer == "L2" and dep in reg.feature_blocks:
                viol.append(
                    f"L2 {entry.brick_id}: must not depend on L3 feature_block {dep!r}"
                )

    for brick in reg.bricks.values():
        _check_deps(brick, label="brick")
    for fb in reg.feature_blocks.values():
        _check_deps(fb, label="feature_block")

    # --- hop depth ---
    for fid in reg.feature_blocks:
        depth = composite_hop_depth(fid, reg)
        if depth > reg.max_composite_hops:
            viol.append(
                f"feature_block {fid}: composite hop depth {depth} exceeds "
                f"max_composite_hops={reg.max_composite_hops}"
            )

    # --- orphan FeatureBlocks in code ---
    found = discovered if discovered is not None else discover_feature_block_ids(root)
    for orphan in orphan_feature_blocks(reg, discovered=found):
        viol.append(
            f"orphan feature_block {orphan!r}: declared in backend/services "
            "but missing from brick_registry.yaml feature_blocks"
        )

    # --- owner paths must exist ---
    for bid, brick in reg.bricks.items():
        for owner in brick.owners:
            if not (root / owner).exists():
                viol.append(f"brick {bid}: owner path missing: {owner}")

    for fid, fb in reg.feature_blocks.items():
        for owner in fb.owners:
            if not (root / owner).exists():
                viol.append(f"feature_block {fid}: owner path missing: {owner}")

    # de-dupe while preserving order
    seen: set[str] = set()
    out: list[str] = []
    for item in viol:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def audit_report(
    registry: BrickRegistry | None = None,
    *,
    repo: Path | None = None,
) -> dict[str, Any]:
    reg = registry or load_registry()
    root = repo or REPO_ROOT
    found = discover_feature_block_ids(root)
    orphans = orphan_feature_blocks(reg, discovered=found)
    violations = collect_violations(reg, repo=root, discovered=found)
    return {
        "verdict": "PASS" if not violations else "FAIL",
        "version": reg.version,
        "max_composite_hops": reg.max_composite_hops,
        "l2_count": len(reg.bricks),
        "l3_count": len(reg.feature_blocks),
        "reference_count": len(reg.reference_nodes),
        "discovered_feature_blocks": sorted(found),
        "orphan_feature_blocks": orphans,
        "violations": violations,
    }


__all__ = [
    "Brick",
    "BrickRegistry",
    "RefNode",
    "audit_report",
    "collect_violations",
    "composite_hop_depth",
    "discover_feature_block_ids",
    "load_registry",
    "orphan_feature_blocks",
]
