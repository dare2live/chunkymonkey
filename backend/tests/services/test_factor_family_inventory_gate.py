"""Factor-family inventory structural gate (v1 stub SSOT)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[3]


def _load_check_mod():
    path = REPO / "backend" / "scripts" / "check_factor_family_inventory.py"
    spec = importlib.util.spec_from_file_location(
        "check_factor_family_inventory", path
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_service_mod():
    import sys

    backend = str(REPO / "backend")
    if backend not in sys.path:
        sys.path.insert(0, backend)
    from services import factor_family_inventory as ffi

    return ffi


def test_inventory_file_version_and_sections() -> None:
    path = REPO / "backend" / "config" / "factor_family_inventory.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert int(data["version"]) == 1
    assert "families" in data and "gate_matrix" in data
    assert len(data["families"]) >= 6
    assert len(data["gate_matrix"]) >= 5


def test_live_inventory_gate_passes() -> None:
    ffi = _load_service_mod()
    viol = ffi.collect_violations()
    assert viol == [], viol


def test_check_script_main_passes() -> None:
    check = _load_check_mod()
    assert check.main(["--json"]) == 0


def test_missing_required_family_field_fails(tmp_path: Path) -> None:
    ffi = _load_service_mod()
    inv_path = tmp_path / "factor_family_inventory.yaml"
    inv_path.write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "families": {
                    "broken_family": {
                        "b_block": "B0",
                        "frequency": "daily",
                        # missing sync_domains, bricks, etc.
                    }
                },
                "gate_matrix": [],
            }
        ),
        encoding="utf-8",
    )
    inv = ffi.load_inventory(inv_path)
    viol = ffi.collect_violations(inv)
    assert any("broken_family" in v and "missing required" in v for v in viol)


def test_gate_matrix_unknown_family_fails(tmp_path: Path) -> None:
    ffi = _load_service_mod()
    base = yaml.safe_load(
        (REPO / "backend" / "config" / "factor_family_inventory.yaml").read_text(
            encoding="utf-8"
        )
    )
    base["gate_matrix"].append(
        {
            "gate_id": "G_test_unknown",
            "requires_families": ["no_such_family"],
            "check": "noop",
            "on_fail": "warn",
        }
    )
    inv_path = tmp_path / "inv.yaml"
    inv_path.write_text(yaml.safe_dump(base), encoding="utf-8")
    viol = ffi.collect_violations(ffi.load_inventory(inv_path))
    assert any("no_such_family" in v for v in viol)


def test_broken_brick_ref_fails(tmp_path: Path) -> None:
    ffi = _load_service_mod()
    base = yaml.safe_load(
        (REPO / "backend" / "config" / "factor_family_inventory.yaml").read_text(
            encoding="utf-8"
        )
    )
    base["families"]["price_volume_daily"]["bricks"].append("not_a_registered_brick")
    inv_path = tmp_path / "inv.yaml"
    inv_path.write_text(yaml.safe_dump(base), encoding="utf-8")
    viol = ffi.collect_violations(ffi.load_inventory(inv_path))
    assert any("not_a_registered_brick" in v for v in viol)
