#!/usr/bin/env python3
"""S7 gate: legacy raw_tushare_* plane inventory completeness + role honesty.

Checks:
  1. Every sync_registry target_table ``raw_tushare_*`` is classified.
  2. Every data_access entity table ``raw_*`` is classified.
  3. Formal-domain raw tables must not be role=ssot; write must be forbidden.
  4. Membership L0: role=ssot OR role=compatibility with publication_surface
     that DataAccess entity resolves to (≠ raw table).
  5. pulse_flow_builder tables must be role=compatibility with mart
     publication_surface.
  6. derive_input / identity_cache: role=compatibility + non-raw
     publication_surface.
  7. ``raw_tushare_daily`` must be role=fill (derive fill, not SSOT).
  8. serve_l0_leaf / multi_consumer: compatibility only when DataAccess
     already redirects to a non-raw publication_surface (forbids pulse-
     aggregate theater while drill/paper still need leaf grain).

Run: PYTHONPATH=backend python backend/scripts/check_legacy_raw_plane.py
"""
from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path
from typing import Any

import yaml

REPO = Path(__file__).resolve().parents[2]
INVENTORY_YAML = REPO / "backend" / "config" / "legacy_raw_plane.yaml"
SYNC_REGISTRY_YAML = REPO / "backend" / "config" / "sync_registry.yaml"
DATA_ACCESS_YAML = REPO / "backend" / "config" / "data_access.yaml"

ALLOWED_ROLES = frozenset({"ssot", "fill", "compatibility", "retired"})

# Mirrors services.data_sources.formal_boundaries formal domains → legacy raw table.
FORMAL_DOMAIN_RAW_TABLES: dict[str, str] = {
    "daily": "raw_tushare_daily",
    "stock_st": "raw_tushare_stock_st",
    "trade_cal": "raw_tushare_trade_cal",
    "margin": "raw_tushare_margin",
}

MEMBERSHIP_ENTITY_BY_RAW: dict[str, str] = {
    "raw_tushare_dc_member": "dc_member",
    "raw_tushare_index_member_all": "index_member_all",
}

# Serve leaf / multi-consumer: compatibility only when DataAccess already
# redirects to a non-raw publication surface (stock-day mart / accepted /
# seat plane). Pattern-B pulse_flow_builder (DataAccess stays on raw) is
# forbidden here — drill/paper/rebuild still need leaf grain.
SERVE_LEAF_ENTITY_BY_RAW: dict[str, str] = {
    "raw_tushare_limit_list_d": "limit_list_d",
    "raw_tushare_moneyflow": "moneyflow",
    "raw_tushare_moneyflow_dc": "moneyflow_dc",
}
MULTI_CONSUMER_ENTITY_BY_RAW: dict[str, str] = {
    "raw_tushare_index_daily": "index_daily",
    "raw_tushare_top_inst": "top_inst",
}


def _load_yaml(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"invalid yaml root at {path}")
    return data


def sync_registry_raw_tables() -> set[str]:
    reg = _load_yaml(SYNC_REGISTRY_YAML)
    out: set[str] = set()
    for spec in (reg.get("domains") or {}).values():
        if not isinstance(spec, dict):
            continue
        table = spec.get("target_table")
        if isinstance(table, str) and table.startswith("raw_tushare_"):
            out.add(table)
    return out


def data_access_raw_tables() -> set[str]:
    da = _load_yaml(DATA_ACCESS_YAML)
    out: set[str] = set()
    for ent in (da.get("entities") or {}).values():
        if not isinstance(ent, dict):
            continue
        table = ent.get("table")
        if isinstance(table, str) and table.startswith("raw_"):
            out.add(table)
    return out


def data_access_entity_table(entity: str) -> str | None:
    da = _load_yaml(DATA_ACCESS_YAML)
    ent = (da.get("entities") or {}).get(entity)
    if not isinstance(ent, dict):
        return None
    table = ent.get("table")
    return table if isinstance(table, str) else None


def role_counts() -> dict[str, int]:
    inv = _load_yaml(INVENTORY_YAML)
    tables = inv.get("tables") or {}
    counts: Counter[str] = Counter()
    for meta in tables.values():
        if isinstance(meta, dict):
            role = meta.get("role")
            if isinstance(role, str):
                counts[role] += 1
    return dict(counts)


def collect_violations() -> list[str]:
    inv = _load_yaml(INVENTORY_YAML)
    if int(inv.get("version") or 0) != 1:
        return [f"legacy_raw_plane.yaml version must be 1 (got {inv.get('version')!r})"]
    tables = inv.get("tables") or {}
    if not isinstance(tables, dict) or not tables:
        return ["legacy_raw_plane.yaml tables: must be a non-empty mapping"]

    viol: list[str] = []
    classified = set(tables)

    for name, meta in tables.items():
        if not isinstance(meta, dict):
            viol.append(f"table {name!r}: meta must be a mapping")
            continue
        role = meta.get("role")
        if role not in ALLOWED_ROLES:
            viol.append(
                f"table {name!r}: role must be one of {sorted(ALLOWED_ROLES)}; got {role!r}"
            )

    for table in sorted(sync_registry_raw_tables() - classified):
        viol.append(f"unclassified sync_registry target_table: {table}")

    for table in sorted(data_access_raw_tables() - classified):
        viol.append(f"unclassified data_access entity table: {table}")

    for domain, expected_table in FORMAL_DOMAIN_RAW_TABLES.items():
        meta = tables.get(expected_table)
        if not isinstance(meta, dict):
            viol.append(
                f"formal domain {domain}: missing inventory entry for {expected_table}"
            )
            continue
        if meta.get("formal_domain") != domain:
            viol.append(
                f"{expected_table}: formal_domain must be {domain!r} "
                f"(got {meta.get('formal_domain')!r})"
            )
        if meta.get("role") == "ssot":
            viol.append(
                f"{expected_table}: formal domain {domain} must not be role=ssot "
                f"(use fill|compatibility)"
            )
        if meta.get("write") != "forbidden":
            viol.append(
                f"{expected_table}: formal domain {domain} write must be forbidden"
            )

    daily = tables.get("raw_tushare_daily") or {}
    if isinstance(daily, dict) and daily.get("role") != "fill":
        viol.append("raw_tushare_daily: derive fill table must be role=fill")

    membership_entities = inv.get("membership_l0_entities") or []
    if not isinstance(membership_entities, list) or not membership_entities:
        viol.append("membership_l0_entities: must list dc_member / index_member_all")
    else:
        required = {"dc_member", "index_member_all"}
        missing = required - {str(x) for x in membership_entities}
        if missing:
            viol.append(f"membership_l0_entities missing: {sorted(missing)}")

    for table, meta in tables.items():
        if not isinstance(meta, dict):
            continue
        if meta.get("kind") == "membership_l0":
            role = meta.get("role")
            if role == "ssot":
                continue
            if role != "compatibility":
                viol.append(
                    f"{table}: membership_l0 role must be ssot|compatibility "
                    f"(got {role!r})"
                )
                continue
            surface = meta.get("publication_surface")
            if not isinstance(surface, str) or not surface or surface == table:
                viol.append(
                    f"{table}: membership_l0 compatibility requires "
                    f"publication_surface ≠ raw table"
                )
                continue
            entity = MEMBERSHIP_ENTITY_BY_RAW.get(table)
            if entity is None:
                viol.append(f"{table}: membership_l0 missing entity mapping in gate")
                continue
            ent_table = data_access_entity_table(entity)
            if ent_table != surface:
                viol.append(
                    f"{table}: data_access entity {entity!r} table must be "
                    f"{surface!r} (got {ent_table!r})"
                )
            continue

        if meta.get("kind") == "pulse_flow_builder":
            if meta.get("role") != "compatibility":
                viol.append(
                    f"{table}: pulse_flow_builder must be role=compatibility "
                    f"(got {meta.get('role')!r})"
                )
            surface = meta.get("publication_surface")
            if not isinstance(surface, str) or not surface.startswith("mart_"):
                viol.append(
                    f"{table}: pulse_flow_builder requires mart_* publication_surface"
                )
            continue

        if meta.get("kind") in ("derive_input", "identity_cache"):
            if meta.get("role") != "compatibility":
                viol.append(
                    f"{table}: {meta.get('kind')} must be role=compatibility "
                    f"(got {meta.get('role')!r})"
                )
            surface = meta.get("publication_surface")
            if (
                not isinstance(surface, str)
                or not surface
                or surface == table
                or surface.startswith("raw_")
            ):
                viol.append(
                    f"{table}: {meta.get('kind')} requires non-raw "
                    f"publication_surface ≠ raw table"
                )
            continue

        if meta.get("kind") in ("serve_l0_leaf", "multi_consumer"):
            role = meta.get("role")
            if role == "ssot":
                continue
            if role != "compatibility":
                viol.append(
                    f"{table}: {meta.get('kind')} role must be ssot|compatibility "
                    f"(got {role!r})"
                )
                continue
            surface = meta.get("publication_surface")
            if (
                not isinstance(surface, str)
                or not surface
                or surface == table
                or surface.startswith("raw_")
            ):
                viol.append(
                    f"{table}: {meta.get('kind')} compatibility requires non-raw "
                    f"publication_surface (no alias / pulse-aggregate theater)"
                )
                continue
            entity_map = (
                SERVE_LEAF_ENTITY_BY_RAW
                if meta.get("kind") == "serve_l0_leaf"
                else MULTI_CONSUMER_ENTITY_BY_RAW
            )
            entity = entity_map.get(table)
            if entity is None:
                viol.append(
                    f"{table}: {meta.get('kind')} missing entity mapping in gate"
                )
                continue
            ent_table = data_access_entity_table(entity)
            if ent_table != surface:
                viol.append(
                    f"{table}: data_access entity {entity!r} table must be "
                    f"{surface!r} before role=compatibility (got {ent_table!r})"
                )

    return viol


def main(argv: list[str] | None = None) -> int:
    del argv  # unused; keep CLI shape stable
    try:
        viol = collect_violations()
    except Exception as exc:  # noqa: BLE001 — gate must fail closed
        print(f"FAIL legacy_raw_plane: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    if viol:
        print("FAIL legacy_raw_plane:", file=sys.stderr)
        for item in viol:
            print(f"  - {item}", file=sys.stderr)
        return 1
    counts = role_counts()
    print(
        "OK legacy_raw_plane: inventory complete; formal roles honest; "
        f"ssot={counts.get('ssot', 0)} fill={counts.get('fill', 0)} "
        f"compatibility={counts.get('compatibility', 0)} "
        f"retired={counts.get('retired', 0)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
