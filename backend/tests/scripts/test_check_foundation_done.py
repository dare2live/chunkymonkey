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
    assert cfg["s7_ssot_wall"]["ssot"] == 14
    assert cfg["s7_ssot_wall"]["kinds"]["sync_orphan"] == 4
    assert cfg["section_15"]["status"] == "PASS"
    knives = cfg["section_15"]["evidence"]["knives"]
    assert len(knives) >= 3
    assert all(k.get("pre_knife") is True for k in knives)
    assert all(isinstance(k.get("commits"), int) and k["commits"] >= 1 for k in knives)


def test_skip_live_aggregate_is_partial_and_not_phase_closure_ready() -> None:
    """Skipping live evidence must never authorize foundation closure."""
    mod = _load_mod()
    report = mod.evaluate_foundation_done(skip_live=True)
    assert report["verdict"] == "PARTIAL"
    assert report["summary"]["FAIL"] == 0
    assert report["summary"]["PARTIAL"] == 1
    assert report["phase_closure_ready"] is False
    by_id = {c["id"]: c for c in report["criteria"]}
    assert by_id["F2"]["verdict"] == "PASS"
    assert by_id["F2"]["typed_wall"] == "s7_ssot_hard_stop"
    assert by_id["F7"]["verdict"] == "PASS"
    assert by_id["F7"]["typed_wall"] == "org_provider_land_blocked"
    assert by_id["F4"].get("typed_wall") is None
    assert "Type-B enrichment accepted" in by_id["F4"]["detail"]
    assert by_id["F6"]["verdict"] == "PARTIAL"
    assert by_id["F6"]["typed_wall"] == "live_foundation_evidence_skipped"
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


def test_f7_org_blocked_and_f10_dual_track() -> None:
    mod = _load_mod()
    assert mod.check_f7_org_blocked()["verdict"] == "PASS"
    cfg = mod.load_config()
    assert mod.check_f10_dual_track(cfg)["verdict"] == "PASS"


def test_f9_strategy_pause_retired_from_gate() -> None:
    """2026-09-02 业主拆锁: F9 不再是聚合门成员, 配置里也没有 strategy_pause 块。"""
    mod = _load_mod()
    report = mod.evaluate_foundation_done(skip_live=True)
    ids = [c["id"] for c in report["criteria"]]
    assert ids == ["F1", "F2", "F3", "F4", "F5", "F6", "F7", "F8", "F10"]
    assert "F9" not in ids
    assert tuple(mod.CRITERION_IDS) == tuple(ids)
    assert "strategy_pause" not in mod.load_config()
    assert not hasattr(mod, "check_f9_strategy_paused")


def test_phase_closure_ready_false_when_live_is_skipped() -> None:
    mod = _load_mod()
    report = mod.evaluate_foundation_done(skip_live=True)
    assert report["phase_closure_ready"] is False
    assert any(c["id"] == "F6" and c["verdict"] == "PARTIAL" for c in report["criteria"])


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
    assert kinds["sync_orphan"] == 4
    assert kinds["serve_l0_declared"] == 8
    assert kinds["blocked_no_publication"] == 2
    assert data["s7_ssot_wall"]["ssot"] == 14
    assert data["b5"]["type_b_defer_codes"] == []
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
