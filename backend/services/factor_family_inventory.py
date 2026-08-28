"""Factor-family inventory + gate matrix — structural SSOT gate (RX prereg).

Authority: docs/MASTER_TOPLEVEL_DESIGN.md §9.1 (因子族边界) + strategy_validation_contract.md §3.1 (窗口对齐)
Orthogonal to brick_registry / sync_registry / data_layers.

v1 scope (machine-only, no DuckDB):
  - version + families required fields + typed enums
  - defer/blocked reason honesty
  - bricks resolve in brick_registry (reference_nodes | bricks | feature_blocks | outputs)
  - sync_domains resolve in sync_registry domains ∪ known miaoxiang acquire ids
  - gate_matrix rows reference existing family_id + required gate fields
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import yaml

from services.brick_registry import DEFAULT_REGISTRY, load_registry

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INVENTORY = REPO_ROOT / "backend" / "config" / "factor_family_inventory.yaml"
DEFAULT_SYNC_REGISTRY = REPO_ROOT / "backend" / "config" / "sync_registry.yaml"
DEFAULT_SERVE_DERIVE = REPO_ROOT / "backend" / "config" / "serve_derive_closed_loop.yaml"

REQUIRED_FAMILY_FIELDS = frozenset(
    {
        "b_block",
        "frequency",
        "availability_axis",
        "sync_domains",
        "bricks",
        "refresh_owner",
        "coverage_start_policy",
        "stack_eligibility",
    }
)

FREQUENCIES = frozenset({"daily", "event", "quarterly_period", "on_demand"})
B_BLOCKS = frozenset({f"B{i}" for i in range(6)})
REFRESH_OWNERS = frozenset(
    {
        "acquire_formal_daily",
        "daily_process",
        "daily_acquire_catchup",
        "acquire_incremental_plus_dossier_derive",
        "acquire_period_gap_n1",
        "manual_only",
    }
)
COVERAGE_POLICIES = frozenset(
    {
        "registry_data_start",
        "accepted_frontier",
        "bounded_fill",
        "honest_sparse",
        "snapshot_bound",
    }
)
STACK_ELIGIBILITY = frozenset({"ready", "defer", "blocked"})
GATE_ON_FAIL = frozenset({"block_stack", "inconclusive_only", "warn"})

# Miaoxiang / land-only acquire paths documented outside sync_registry.domains.
EXTRA_SYNC_DOMAIN_IDS = frozenset({"holders_aif10", "org_holding"})

REQUIRED_GATE_FIELDS = frozenset(
    {"gate_id", "requires_families", "check", "on_fail"},
)


@dataclass(frozen=True)
class FactorFamilyInventory:
    version: int
    families: dict[str, dict[str, Any]]
    gate_matrix: list[dict[str, Any]]
    path: Path | None = None


def load_inventory(path: Path | None = None) -> FactorFamilyInventory:
    p = path or DEFAULT_INVENTORY
    raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("inventory root must be mapping")
    version = int(raw.get("version", 0))
    families = raw.get("families") or {}
    gate_matrix = raw.get("gate_matrix") or []
    if not isinstance(families, dict):
        raise ValueError("families must be mapping")
    if not isinstance(gate_matrix, list):
        raise ValueError("gate_matrix must be list")
    return FactorFamilyInventory(
        version=version,
        families=families,
        gate_matrix=gate_matrix,
        path=p,
    )


def _sync_registry_domain_ids(path: Path | None = None) -> set[str]:
    p = path or DEFAULT_SYNC_REGISTRY
    raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    domains = raw.get("domains") if isinstance(raw, dict) else None
    if not isinstance(domains, dict):
        return set()
    return set(domains.keys())


def _serve_derive_source_domains(path: Path | None = None) -> set[str]:
    p = path or DEFAULT_SERVE_DERIVE
    raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    out: set[str] = set()
    for surf in raw.get("surfaces") or []:
        if not isinstance(surf, dict):
            continue
        for dom in surf.get("source_domains") or []:
            out.add(str(dom))
    return out


def known_sync_domain_ids(
    *,
    sync_registry_path: Path | None = None,
    serve_derive_path: Path | None = None,
) -> set[str]:
    return (
        _sync_registry_domain_ids(sync_registry_path)
        | _serve_derive_source_domains(serve_derive_path)
        | set(EXTRA_SYNC_DOMAIN_IDS)
    )


def _brick_ids_from_registry(reg) -> set[str]:
    return reg.all_registered_ids() | reg.all_outputs()


def collect_violations(
    inv: FactorFamilyInventory | None = None,
    *,
    brick_registry_path: Path | None = None,
    sync_registry_path: Path | None = None,
    serve_derive_path: Path | None = None,
) -> list[str]:
    inv = inv or load_inventory()
    viol: list[str] = []
    if inv.version != 1:
        viol.append(f"inventory version must be 1 (got {inv.version})")
    if not inv.families:
        viol.append("families must be non-empty")

    try:
        reg = load_registry(brick_registry_path or DEFAULT_REGISTRY)
    except Exception as exc:  # noqa: BLE001
        viol.append(f"brick_registry load failed: {type(exc).__name__}: {exc}")
        reg = None

    brick_ids: set[str] = set()
    if reg is not None:
        brick_ids = _brick_ids_from_registry(reg)

    domain_ids = known_sync_domain_ids(
        sync_registry_path=sync_registry_path,
        serve_derive_path=serve_derive_path,
    )

    family_ids = set(inv.families.keys())
    for family_id, spec in inv.families.items():
        if not isinstance(spec, dict):
            viol.append(f"family {family_id}: body must be mapping")
            continue
        missing = REQUIRED_FAMILY_FIELDS - set(spec.keys())
        if missing:
            viol.append(
                f"family {family_id}: missing required fields {sorted(missing)}"
            )
        extra_keys = set(spec.keys()) - REQUIRED_FAMILY_FIELDS - {
            "defer_reason",
            "blocked_reason",
            "continuity_gate",
            "named_layers",
        }
        if extra_keys:
            viol.append(
                f"family {family_id}: unknown fields {sorted(extra_keys)}"
            )

        b_block = spec.get("b_block")
        if b_block not in B_BLOCKS:
            viol.append(f"family {family_id}: invalid b_block {b_block!r}")

        freq = spec.get("frequency")
        if freq not in FREQUENCIES:
            viol.append(f"family {family_id}: invalid frequency {freq!r}")

        axis = spec.get("availability_axis")
        if not isinstance(axis, str) or not axis.strip():
            viol.append(f"family {family_id}: availability_axis must be non-empty str")

        sync_domains = spec.get("sync_domains")
        if not isinstance(sync_domains, list):
            viol.append(f"family {family_id}: sync_domains must be list")
        else:
            for dom in sync_domains:
                if dom not in domain_ids:
                    viol.append(
                        f"family {family_id}: unknown sync_domain {dom!r}"
                    )

        bricks = spec.get("bricks")
        if not isinstance(bricks, list):
            viol.append(f"family {family_id}: bricks must be list")
        elif reg is not None:
            for bid in bricks:
                if bid not in brick_ids:
                    viol.append(
                        f"family {family_id}: brick not in brick_registry {bid!r}"
                    )

        ro = spec.get("refresh_owner")
        if ro not in REFRESH_OWNERS:
            viol.append(f"family {family_id}: invalid refresh_owner {ro!r}")

        csp = spec.get("coverage_start_policy")
        if csp not in COVERAGE_POLICIES:
            viol.append(
                f"family {family_id}: invalid coverage_start_policy {csp!r}"
            )

        named_layers = spec.get("named_layers")
        if named_layers is not None:
            if not isinstance(named_layers, list) or not named_layers:
                viol.append(
                    f"family {family_id}: named_layers must be non-empty list"
                )
            elif any(
                not isinstance(item, str) or not item.strip()
                for item in named_layers
            ):
                viol.append(
                    f"family {family_id}: named_layers items must be non-empty str"
                )

        se = spec.get("stack_eligibility")
        if se not in STACK_ELIGIBILITY:
            viol.append(
                f"family {family_id}: invalid stack_eligibility {se!r}"
            )
        if se == "defer" and not spec.get("defer_reason"):
            viol.append(
                f"family {family_id}: stack_eligibility=defer requires defer_reason"
            )
        if se == "blocked" and not spec.get("blocked_reason"):
            viol.append(
                f"family {family_id}: stack_eligibility=blocked requires blocked_reason"
            )

    if not inv.gate_matrix:
        viol.append("gate_matrix must be non-empty")

    for i, row in enumerate(inv.gate_matrix):
        if not isinstance(row, dict):
            viol.append(f"gate_matrix[{i}]: row must be mapping")
            continue
        missing = REQUIRED_GATE_FIELDS - set(row.keys())
        if missing:
            viol.append(f"gate_matrix[{i}]: missing fields {sorted(missing)}")
        gid = row.get("gate_id")
        if not isinstance(gid, str) or not gid.strip():
            viol.append(f"gate_matrix[{i}]: gate_id must be non-empty str")
        req = row.get("requires_families")
        if not isinstance(req, list) or not req:
            viol.append(f"gate_matrix[{i}]: requires_families must be non-empty list")
        else:
            for fid in req:
                if fid not in family_ids:
                    viol.append(
                        f"gate_matrix[{i}] gate_id={gid!r}: unknown family {fid!r}"
                    )
        check = row.get("check")
        if not isinstance(check, str) or not check.strip():
            viol.append(f"gate_matrix[{i}]: check must be non-empty str")
        on_fail = row.get("on_fail")
        if on_fail not in GATE_ON_FAIL:
            viol.append(f"gate_matrix[{i}]: invalid on_fail {on_fail!r}")

    gate_ids = [r.get("gate_id") for r in inv.gate_matrix if isinstance(r, dict)]
    if len(gate_ids) != len(set(gate_ids)):
        viol.append("gate_matrix gate_id values must be unique")

    return viol


def audit_report(
    inv: FactorFamilyInventory | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    inv = inv or load_inventory()
    viol = collect_violations(inv, **kwargs)
    return {
        "verdict": "PASS" if not viol else "FAIL",
        "version": inv.version,
        "family_count": len(inv.families),
        "gate_count": len(inv.gate_matrix),
        "violations": viol,
        "inventory_path": str(inv.path or DEFAULT_INVENTORY),
    }
