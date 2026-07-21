#!/usr/bin/env python3
"""S7 gate: legacy raw_tushare_* plane inventory completeness + role honesty.

Checks:
  1. Every sync_registry target_table ``raw_tushare_*`` is classified.
  2. Every data_access entity table ``raw_*`` is classified.
  3. Formal-domain raw tables must not be role=ssot; write must be forbidden.
  4. Membership L0 entity tables declared in inventory must be role=ssot
     (honest: no formal membership plane yet) and listed under
     membership_l0_entities.
  5. ``raw_tushare_daily`` must be role=fill (derive fill, not SSOT).

Run: PYTHONPATH=backend python backend/scripts/check_legacy_raw_plane.py
"""
from __future__ import annotations

import sys
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
        if meta.get("kind") != "membership_l0":
            continue
        if meta.get("role") != "ssot":
            viol.append(
                f"{table}: membership_l0 must stay role=ssot until formal membership plane"
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
    print("OK legacy_raw_plane: inventory complete; formal roles honest")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
