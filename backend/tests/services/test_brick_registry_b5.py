"""B5 brick registry: L2 primitive vs L3 FeatureBlock + hop/raw/orphan gates."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[3]


def _load_check_mod():
    path = REPO / "backend" / "scripts" / "check_brick_registry.py"
    spec = importlib.util.spec_from_file_location("check_brick_registry", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_service_mod():
    import sys

    backend = str(REPO / "backend")
    if backend not in sys.path:
        sys.path.insert(0, backend)
    from services import brick_registry as br

    return br


def test_b5_registry_file_exists_and_versions() -> None:
    path = REPO / "backend" / "config" / "brick_registry.yaml"
    assert path.is_file()
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert int(data["version"]) == 1
    assert int(data["max_composite_hops"]) == 2
    assert "bricks" in data and "feature_blocks" in data


def test_b5_live_registry_classifies_l2_and_l3() -> None:
    br = _load_service_mod()
    reg = br.load_registry()
    layers = {bid: b.layer for bid, b in reg.bricks.items()}
    assert layers.get("price_kline_qfq_tushare") == "L2"
    assert layers.get("fact_stock_form_daily") == "L2"
    assert layers.get("tier1_stock_state_stage_pattern_v1") == "L2"
    assert layers.get("MarketContextSnapshot") == "L2"
    for fb_id, fb in reg.feature_blocks.items():
        assert fb.layer == "L3", fb_id
    # Known FeatureBlock IDs from services must be registered
    for expected in (
        "stock_state_stage_pattern_v0",
        "stock_state_stage_pattern_v1",
        "market_sensing_project_breadth_v0",
        "institution_event_holders_disclosure_v0",
    ):
        assert expected in reg.feature_blocks


def test_b5_discover_feature_block_ids_from_services() -> None:
    br = _load_service_mod()
    found = br.discover_feature_block_ids(REPO)
    assert "stock_state_stage_pattern_v1" in found
    assert "market_sensing_project_breadth_v0" in found
    assert "institution_event_holders_disclosure_v0" in found
    assert "stock_state_stage_pattern_v0" in found


def test_b5_live_gate_passes() -> None:
    check = _load_check_mod()
    viol = check.collect_violations()
    assert viol == [], viol


def test_b5_orphan_feature_block_fails(tmp_path: Path) -> None:
    br = _load_service_mod()
    # Minimal registry missing a discovered id
    reg_yaml = tmp_path / "brick_registry.yaml"
    reg_yaml.write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "max_composite_hops": 2,
                "reference_nodes": {
                    "accepted_nominal_ohlcv_daily": {"layer": "L1", "kind": "accepted_canonical"},
                },
                "bricks": {
                    "fact_stock_form_daily": {
                        "layer": "L2",
                        "kind": "primitive",
                        "depends_on": ["accepted_nominal_ohlcv_daily"],
                        "owners": ["backend/services/technical_states"],
                        "config_hash": "form_v0",
                        "availability_axis": "available_at",
                    }
                },
                "feature_blocks": {},
            }
        ),
        encoding="utf-8",
    )
    reg = br.load_registry(reg_yaml)
    orphans = br.orphan_feature_blocks(reg, discovered={"stock_state_stage_pattern_v1"})
    assert orphans == ["stock_state_stage_pattern_v1"]
    viol = br.collect_violations(reg, repo=REPO, discovered={"stock_state_stage_pattern_v1"})
    assert any("orphan feature_block" in v for v in viol)


def test_b5_rejects_silent_raw_bypass(tmp_path: Path) -> None:
    br = _load_service_mod()
    reg_yaml = tmp_path / "brick_registry.yaml"
    reg_yaml.write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "max_composite_hops": 2,
                "reference_nodes": {},
                "bricks": {
                    "bad_qfq": {
                        "layer": "L2",
                        "kind": "primitive",
                        "depends_on": ["raw_tushare_daily"],
                        "owners": ["backend/scripts/build_price_kline_qfq_tushare.py"],
                        "config_hash": "x",
                        "availability_axis": "available_at",
                    }
                },
                "feature_blocks": {},
            }
        ),
        encoding="utf-8",
    )
    reg = br.load_registry(reg_yaml)
    viol = br.collect_violations(reg, repo=REPO, discovered=set())
    assert any("silent raw bypass" in v for v in viol)


def test_b5_rejects_l2_depending_on_l3(tmp_path: Path) -> None:
    br = _load_service_mod()
    reg_yaml = tmp_path / "brick_registry.yaml"
    reg_yaml.write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "max_composite_hops": 2,
                "reference_nodes": {
                    "accepted_nominal_ohlcv_daily": {"layer": "L1", "kind": "accepted_canonical"},
                },
                "bricks": {
                    "prim": {
                        "layer": "L2",
                        "kind": "primitive",
                        "depends_on": ["fb_a"],
                        "owners": ["backend/services/main_rally_b1.py"],
                        "config_hash": "x",
                        "availability_axis": "available_at",
                    }
                },
                "feature_blocks": {
                    "fb_a": {
                        "layer": "L3",
                        "kind": "feature_block",
                        "depends_on": ["accepted_nominal_ohlcv_daily"],
                        "owners": ["backend/services/main_rally_b1.py"],
                        "config_hash": "y",
                        "availability_axis": "decision_time_visible_only",
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    reg = br.load_registry(reg_yaml)
    viol = br.collect_violations(reg, repo=REPO, discovered=set())
    assert any("L2" in v and "must not depend on L3" in v for v in viol)


def test_b5_rejects_composite_over_two_hops(tmp_path: Path) -> None:
    br = _load_service_mod()
    reg_yaml = tmp_path / "brick_registry.yaml"
    reg_yaml.write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "max_composite_hops": 2,
                "reference_nodes": {
                    "accepted_nominal_ohlcv_daily": {"layer": "L1", "kind": "accepted_canonical"},
                },
                "bricks": {
                    "prim": {
                        "layer": "L2",
                        "kind": "primitive",
                        "depends_on": ["accepted_nominal_ohlcv_daily"],
                        "owners": ["backend/services/technical_states"],
                        "config_hash": "x",
                        "availability_axis": "available_at",
                    }
                },
                "feature_blocks": {
                    "fb_c": {
                        "layer": "L3",
                        "kind": "feature_block",
                        "depends_on": ["prim"],
                        "owners": ["backend/services/main_rally_b1.py"],
                        "config_hash": "c",
                        "availability_axis": "decision_time_visible_only",
                    },
                    "fb_b": {
                        "layer": "L3",
                        "kind": "feature_block",
                        "depends_on": ["fb_c"],
                        "owners": ["backend/services/main_rally_b1.py"],
                        "config_hash": "b",
                        "availability_axis": "decision_time_visible_only",
                    },
                    "fb_a": {
                        "layer": "L3",
                        "kind": "feature_block",
                        "depends_on": ["fb_b"],
                        "owners": ["backend/services/main_rally_b1.py"],
                        "config_hash": "a",
                        "availability_axis": "decision_time_visible_only",
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    reg = br.load_registry(reg_yaml)
    depth = br.composite_hop_depth("fb_a", reg)
    assert depth > 2
    viol = br.collect_violations(reg, repo=REPO, discovered=set())
    assert any("exceeds max_composite_hops" in v for v in viol)


def test_b5_allows_l3_to_l3_to_l2_two_hops(tmp_path: Path) -> None:
    br = _load_service_mod()
    reg_yaml = tmp_path / "brick_registry.yaml"
    reg_yaml.write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "max_composite_hops": 2,
                "reference_nodes": {
                    "accepted_nominal_ohlcv_daily": {"layer": "L1", "kind": "accepted_canonical"},
                },
                "bricks": {
                    "prim": {
                        "layer": "L2",
                        "kind": "primitive",
                        "depends_on": ["accepted_nominal_ohlcv_daily"],
                        "owners": ["backend/services/technical_states"],
                        "config_hash": "x",
                        "availability_axis": "available_at",
                    }
                },
                "feature_blocks": {
                    "fb_b": {
                        "layer": "L3",
                        "kind": "feature_block",
                        "depends_on": ["prim"],
                        "owners": ["backend/services/main_rally_b1.py"],
                        "config_hash": "b",
                        "availability_axis": "decision_time_visible_only",
                    },
                    "fb_a": {
                        "layer": "L3",
                        "kind": "feature_block",
                        "depends_on": ["fb_b"],
                        "owners": ["backend/services/main_rally_b1.py"],
                        "config_hash": "a",
                        "availability_axis": "decision_time_visible_only",
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    reg = br.load_registry(reg_yaml)
    assert br.composite_hop_depth("fb_a", reg) == 2
    viol = br.collect_violations(reg, repo=REPO, discovered=set())
    assert not any("exceeds max_composite_hops" in v for v in viol)


def test_b5_check_script_json_shape() -> None:
    check = _load_check_mod()
    report = check.build_report()
    assert report["verdict"] in {"PASS", "FAIL"}
    assert "orphan_feature_blocks" in report
    assert "violations" in report
    assert "l2_count" in report and "l3_count" in report
