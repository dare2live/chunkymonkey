"""FND-GATE: foundation-done F1–F10 aggregate (typed walls + fail-closed)."""

from __future__ import annotations

import copy
import importlib.util
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[3]


def _load_mod():
    path = REPO / "backend" / "scripts" / "check_foundation_done.py"
    spec = importlib.util.spec_from_file_location("check_foundation_done", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_live_config_loads() -> None:
    mod = _load_mod()
    cfg = mod.load_config()
    assert int(cfg["version"]) == 1
    assert cfg["s7_ssot_wall"]["ssot"] == 23
    assert cfg["section_15"]["status"] == "PARTIAL"


def test_skip_live_aggregate_is_partial_not_fail() -> None:
    """Known walls + F8 PARTIAL must not exit-fail the gate process."""
    mod = _load_mod()
    report = mod.evaluate_foundation_done(skip_live=True)
    assert report["verdict"] in {"PASS", "PARTIAL"}
    assert report["summary"]["FAIL"] == 0
    assert report["phase_closure_ready"] is False
    by_id = {c["id"]: c for c in report["criteria"]}
    assert by_id["F2"]["verdict"] == "PASS"
    assert by_id["F2"]["typed_wall"] == "s7_23_hard_stop"
    assert by_id["F7"]["verdict"] == "PASS"
    assert by_id["F7"]["typed_wall"] == "org_provider_land_blocked"
    assert by_id["F4"]["typed_wall"] == "type_b_enrichment_defer"
    assert by_id["F8"]["verdict"] == "PARTIAL"
    assert mod.main(["--skip-live"]) == 0


def test_f2_fails_when_ssot_wall_drifts(tmp_path: Path) -> None:
    mod = _load_mod()
    cfg = mod.load_config()
    cfg = copy.deepcopy(cfg)
    cfg["s7_ssot_wall"]["ssot"] = 99
    result = mod.check_f2_s7_wall(cfg)
    assert result["verdict"] == "FAIL"
    assert "ssot=" in result["detail"]


def test_f8_partial_does_not_block_gate_exit() -> None:
    mod = _load_mod()
    cfg = mod.load_config()
    assert mod.check_f8_section15(cfg)["verdict"] == "PARTIAL"
    # Fake PASS without evidence must fail closed
    bad = copy.deepcopy(cfg)
    bad["section_15"]["status"] = "PASS"
    bad["section_15"]["evidence"] = {"knives": []}
    assert mod.check_f8_section15(bad)["verdict"] == "FAIL"


def test_f8_pass_requires_three_knives_and_ratio() -> None:
    mod = _load_mod()
    cfg = mod.load_config()
    cfg = copy.deepcopy(cfg)
    cfg["section_15"]["status"] = "PASS"
    cfg["section_15"]["evidence"] = {
        "knives": [
            {"name": "k1", "commits": 1, "pre_knife": True},
            {"name": "k2", "commits": 1, "pre_knife": True},
            {"name": "k3", "commits": 2, "pre_knife": True},
        ]
    }
    # mean commits/knife = 4/3 ≈ 1.333 ≤ 1.5
    assert mod.check_f8_section15(cfg)["verdict"] == "PASS"

    cfg["section_15"]["evidence"]["knives"][2]["commits"] = 3
    # mean = 5/3 ≈ 1.667 > 1.5
    assert mod.check_f8_section15(cfg)["verdict"] == "FAIL"


def test_f7_org_blocked_and_f9_strategy_markers() -> None:
    mod = _load_mod()
    assert mod.check_f7_org_blocked()["verdict"] == "PASS"
    cfg = mod.load_config()
    assert mod.check_f9_strategy_paused(cfg)["verdict"] == "PASS"
    assert mod.check_f10_dual_track(cfg)["verdict"] == "PASS"


def test_f9_fails_when_goal_loses_pause_marker(tmp_path: Path, monkeypatch) -> None:
    mod = _load_mod()
    fake_goal = tmp_path / "goal.md"
    fake_goal.write_text("no pause markers here\n", encoding="utf-8")
    monkeypatch.setattr(mod, "GOAL_MD", fake_goal)
    cfg = mod.load_config()
    assert mod.check_f9_strategy_paused(cfg)["verdict"] == "FAIL"


def test_phase_closure_ready_false_while_f8_partial() -> None:
    mod = _load_mod()
    report = mod.evaluate_foundation_done(skip_live=True)
    assert report["phase_closure_ready"] is False
    assert any(c["id"] == "F8" and c["verdict"] == "PARTIAL" for c in report["criteria"])


def test_config_yaml_declares_typed_walls() -> None:
    data = yaml.safe_load(
        (REPO / "backend" / "config" / "foundation_done.yaml").read_text(encoding="utf-8")
    )
    kinds = data["s7_ssot_wall"]["kinds"]
    assert kinds["sync_orphan"] == 14
    assert kinds["serve_l0_declared"] == 7
    assert kinds["blocked_no_publication"] == 2
    assert "enrichment_projection_partial" in data["b5"]["type_b_defer_codes"]
