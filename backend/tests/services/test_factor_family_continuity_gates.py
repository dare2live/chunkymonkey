"""Factor-family frequency-typed continuity gate matrix (K2)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[3]


def _load_check_mod():
    path = REPO / "backend" / "scripts" / "check_factor_family_gates.py"
    spec = importlib.util.spec_from_file_location(
        "check_factor_family_gates", path
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_gates_mod():
    import sys

    backend = str(REPO / "backend")
    if backend not in sys.path:
        sys.path.insert(0, backend)
    from services import factor_family_continuity_gates as ffg

    return ffg


def test_live_continuity_gate_passes() -> None:
    ffg = _load_gates_mod()
    viol = ffg.collect_gate_violations()
    assert viol == [], viol


def test_check_script_main_passes() -> None:
    check = _load_check_mod()
    assert check.main(["--json"]) == 0


def test_event_family_forbids_calendar_gaps_mode(tmp_path: Path) -> None:
    ffg = _load_gates_mod()
    base = yaml.safe_load(
        (REPO / "backend" / "config" / "factor_family_inventory.yaml").read_text(
            encoding="utf-8"
        )
    )
    base["families"]["disclosure_holders_event"]["continuity_gate"] = {
        "mode": "calendar_gaps",
        "wired": "bad",
    }
    from services.factor_family_inventory import load_inventory

    inv_path = tmp_path / "inv.yaml"
    inv_path.write_text(yaml.safe_dump(base), encoding="utf-8")
    inv = load_inventory(inv_path)
    viol = ffg.collect_gate_violations(inv)
    assert any("calendar_gaps forbidden" in v for v in viol)


def test_defer_requires_typed_mode(tmp_path: Path) -> None:
    ffg = _load_gates_mod()
    base = yaml.safe_load(
        (REPO / "backend" / "config" / "factor_family_inventory.yaml").read_text(
            encoding="utf-8"
        )
    )
    base["families"]["org_disclosure_period"]["continuity_gate"] = {
        "mode": "calendar_gaps",
        "wired": "bad",
    }
    inv_path = tmp_path / "inv.yaml"
    inv_path.write_text(yaml.safe_dump(base), encoding="utf-8")
    from services.factor_family_inventory import load_inventory

    viol = ffg.collect_gate_violations(load_inventory(inv_path))
    assert any("period_gap_bounded" in v or "forbidden" in v for v in viol)


def test_all_families_declare_continuity_gate() -> None:
    path = REPO / "backend" / "config" / "factor_family_inventory.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    for fid, spec in data["families"].items():
        assert "continuity_gate" in spec, fid
        assert spec["continuity_gate"].get("mode")
