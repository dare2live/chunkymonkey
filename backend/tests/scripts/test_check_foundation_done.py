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
    assert cfg["section_15"]["status"] == "PASS"
    knives = cfg["section_15"]["evidence"]["knives"]
    assert len(knives) >= 3
    assert all(k.get("pre_knife") is True for k in knives)
    assert all(isinstance(k.get("commits"), int) and k["commits"] >= 1 for k in knives)


def test_skip_live_aggregate_is_pass_when_f8_closed() -> None:
    """Typed walls PASS + F8 PASS → aggregate PASS and phase_closure_ready."""
    mod = _load_mod()
    report = mod.evaluate_foundation_done(skip_live=True)
    assert report["verdict"] == "PASS"
    assert report["summary"]["FAIL"] == 0
    assert report["summary"]["PARTIAL"] == 0
    assert report["phase_closure_ready"] is True
    by_id = {c["id"]: c for c in report["criteria"]}
    assert by_id["F2"]["verdict"] == "PASS"
    assert by_id["F2"]["typed_wall"] == "s7_23_hard_stop"
    assert by_id["F7"]["verdict"] == "PASS"
    assert by_id["F7"]["typed_wall"] == "org_provider_land_blocked"
    assert by_id["F4"]["typed_wall"] == "type_b_enrichment_defer"
    assert by_id["F8"]["verdict"] == "PASS"
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
    # Live config is PASS after §15-VERIFY; PARTIAL path still exit-safe.
    cfg = copy.deepcopy(mod.load_config())
    cfg["section_15"]["status"] = "PARTIAL"
    cfg["section_15"]["reason"] = "test_partial"
    cfg["section_15"]["evidence"] = {"knives": []}
    assert mod.check_f8_section15(cfg)["verdict"] == "PARTIAL"
    # Fake PASS without evidence must fail closed
    bad = copy.deepcopy(mod.load_config())
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


def test_f8_live_config_passes_bar() -> None:
    mod = _load_mod()
    cfg = mod.load_config()
    result = mod.check_f8_section15(cfg)
    assert result["verdict"] == "PASS"
    mean = float(result["evidence"]["commits_per_knife"])
    assert mean <= float(cfg["section_15"]["max_commits_per_knife"])


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


def test_phase_closure_ready_true_when_all_pass() -> None:
    mod = _load_mod()
    report = mod.evaluate_foundation_done(skip_live=True)
    assert report["phase_closure_ready"] is True
    assert all(c["verdict"] == "PASS" for c in report["criteria"])


def test_phase_closure_ready_false_while_f8_partial() -> None:
    mod = _load_mod()
    cfg = copy.deepcopy(mod.load_config())
    cfg["section_15"]["status"] = "PARTIAL"
    cfg["section_15"]["reason"] = "forced_partial"
    cfg["section_15"]["evidence"] = {"knives": []}
    report = mod.evaluate_foundation_done(cfg=cfg, skip_live=True)
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
    assert data["section_15"]["max_commits_per_knife"] == 1.5
    assert data["section_15"]["required_consecutive_l3_knives"] == 3
    assert int(data["e0_breadth"]["min_org_accepted_stocks"]) >= 500


def test_f6_fails_on_org_canary_population(monkeypatch) -> None:
    mod = _load_mod()
    cfg = mod.load_config()

    def _thin(_cfg):
        return {
            "holders_partitions": 200,
            "stk_partitions": 10,
            "daily_partitions": 200,
            "org_partitions": 2,
            "org_max_accepted_stocks": 2,
            "holders_daily_overlap": 150,
            "stk_daily_overlap": 10,
            "holders_range": ["20250101", "20260721"],
            "stk_range": ["20250101", "20260715"],
        }, None

    monkeypatch.setattr(mod, "_e0_live_breadth", _thin)
    result = mod.check_f6_e0_breadth(cfg, skip_live=False)
    assert result["verdict"] == "FAIL"
    assert "org_max_accepted_stocks=2" in result["detail"]


def test_f6_passes_when_org_population_meets_floor(monkeypatch) -> None:
    mod = _load_mod()
    cfg = mod.load_config()

    def _ok(_cfg):
        return {
            "holders_partitions": 200,
            "stk_partitions": 10,
            "daily_partitions": 200,
            "org_partitions": 2,
            "org_max_accepted_stocks": 5520,
            "holders_daily_overlap": 150,
            "stk_daily_overlap": 10,
            "holders_range": ["20250101", "20260721"],
            "stk_range": ["20250101", "20260715"],
        }, None

    monkeypatch.setattr(mod, "_e0_live_breadth", _ok)
    result = mod.check_f6_e0_breadth(cfg, skip_live=False)
    assert result["verdict"] == "PASS"
    assert "max_stocks=5520" in result["detail"]
