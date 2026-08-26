from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from services.research_runtime import StrategySpec as RuntimeStrategySpec
from services.strategy_spec import (
    FROZEN_FORMULA_IDS,
    StrategySpec,
    StrategySpecError,
    disclosure_freeze_coverage,
    disclosure_freeze_coverage_days,
    load_all_strategy_packages,
    load_strategy_package,
    load_strategy_spec,
    verify_frozen_challenger,
)


def _follow_spec(**overrides: object) -> StrategySpec:
    payload: dict[str, object] = {
        "package_id": "institution_follow_v1",
        "spec_id": "institution_follow_v1",
        "candidate_generation": "holders_increase_notice",
        "ranking": "none_one_name_smoke",
        "sizing": "one_name_one_position",
        "entry_kind": "next_tradable_open",
        "entry_after": "notice_available_at",
        "exit_kind": "event_or_max_hold",
        "exit_event": "holders_decrease_notice",
        "pnl_source": "follower_next_open_to_exit_open",
        "paper_status": "smoke_ready",
        "max_chase_days": 3,
        "max_hold_calendar_days": 90,
        "named_not_run_max_hold_calendar_days": (180,),
        "applicable_states": (),
        "config_hash": "test-hash",
    }
    payload.update(overrides)
    return StrategySpec(**payload)  # type: ignore[arg-type]


def test_live_packages_load_three_master_boundaries() -> None:
    specs = load_all_strategy_packages()
    packages = {spec.package_id for spec in specs}
    assert packages == {"institution_follow_v1", "main_rally_v1", "formulas"}
    follow = load_strategy_spec("institution_follow_v1")
    assert follow.exit_kind == "event_or_max_hold"
    assert follow.max_hold_calendar_days == 90
    assert follow.named_not_run_max_hold_calendar_days == (180,)
    assert follow.pnl_source.startswith("follower_")
    rally = load_strategy_spec("main_rally_v1")
    assert rally.candidate_generation == "rally_setup_pivot_confirmed_base_days"
    assert rally.exit_kind == "not_implemented_full_episode"
    assert rally.paper_status == "setup_signal_only"
    formula_ids = [
        spec.spec_id.removeprefix("formulas:")
        for spec in specs
        if spec.package_id == "formulas"
    ]
    assert tuple(formula_ids) == FROZEN_FORMULA_IDS
    assert RuntimeStrategySpec is StrategySpec


def test_follow_spec_load_does_not_run_formula_verifier(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("follow spec must not load frozen formula CSV")

    monkeypatch.setattr("services.strategy_spec.verify_frozen_challenger", boom)
    spec = load_strategy_spec("institution_follow_v1")
    assert spec.spec_id == "institution_follow_v1"


def test_formula_package_requires_verify_frozen_evidence() -> None:
    verify_frozen_challenger()
    spec = load_strategy_spec("formulas:gs_pullback_confirm")
    assert spec.frozen_artifact_sha256 is not None
    assert spec.paper_status == "synthetic_smoke_ready"
    assert spec.pnl_source == "challenger_next_open_to_exit_open"
    assert spec.exit_kind == "formula_exit_or_max_hold"
    assert spec.entry_kind == "next_tradable_open"
    assert spec.max_chase_days == 3
    assert spec.max_hold_calendar_days == 90


def test_construction_fails_closed_without_exit() -> None:
    with pytest.raises(StrategySpecError, match="missing_exit"):
        _follow_spec(exit_kind="", exit_event="", max_hold_calendar_days=None)


def test_construction_rejects_institution_alpha_as_follower_pnl() -> None:
    with pytest.raises(
        StrategySpecError, match="follower_pnl_must_not_use_institution_alpha"
    ):
        _follow_spec(pnl_source="holder_median_alpha")


def test_unknown_package_is_rejected() -> None:
    with pytest.raises(StrategySpecError, match="unknown_strategy_package"):
        _follow_spec(package_id="stock_screener")


def test_loader_fails_closed_when_follow_yaml_missing_exit(tmp_path: Path) -> None:
    src = Path(__file__).resolve().parents[3] / "backend" / "config" / "strategy_packages"
    for name in ("institution_follow_v1.yaml", "main_rally_v1.yaml", "formulas.yaml"):
        (tmp_path / name).write_text(
            (src / name).read_text(encoding="utf-8"),
            encoding="utf-8",
        )
    broken = yaml.safe_load((tmp_path / "institution_follow_v1.yaml").read_text(encoding="utf-8"))
    broken["exit_kind"] = ""
    broken["exit_event"] = ""
    broken["max_hold_calendar_days"] = None
    (tmp_path / "institution_follow_v1.yaml").write_text(
        yaml.safe_dump(broken, sort_keys=False),
        encoding="utf-8",
    )
    with pytest.raises(StrategySpecError, match="missing_exit"):
        load_all_strategy_packages(config_dir=tmp_path)


def test_loader_fails_closed_when_formula_yaml_missing_exit(tmp_path: Path) -> None:
    src = Path(__file__).resolve().parents[3] / "backend" / "config" / "strategy_packages"
    (tmp_path / "formulas.yaml").write_text(
        (src / "formulas.yaml").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    broken = yaml.safe_load((tmp_path / "formulas.yaml").read_text(encoding="utf-8"))
    broken["exit_kind"] = ""
    (tmp_path / "formulas.yaml").write_text(
        yaml.safe_dump(broken, sort_keys=False),
        encoding="utf-8",
    )
    with pytest.raises(StrategySpecError, match="missing_exit"):
        load_strategy_package("formulas", config_dir=tmp_path)


def test_loader_fails_closed_when_formula_yaml_wrong_chase(tmp_path: Path) -> None:
    src = Path(__file__).resolve().parents[3] / "backend" / "config" / "strategy_packages"
    (tmp_path / "formulas.yaml").write_text(
        (src / "formulas.yaml").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    broken = yaml.safe_load((tmp_path / "formulas.yaml").read_text(encoding="utf-8"))
    broken["max_chase_days"] = 5
    (tmp_path / "formulas.yaml").write_text(
        yaml.safe_dump(broken, sort_keys=False),
        encoding="utf-8",
    )
    with pytest.raises(StrategySpecError, match="formula_max_chase_days_must_be_3"):
        load_strategy_package("formulas", config_dir=tmp_path)


def test_disclosure_coverage_uses_freeze_partitions_not_ohlcv_span() -> None:
    payload = {
        "domains": {
            "holders_top10": {
                "accepted": [
                    {"partition": "20250331"},
                    {"partition": "20250430"},
                    {"partition": "20250530"},
                ]
            },
            "nominal_ohlcv": {
                "accepted": [{"partition": f"2019{i:04d}"} for i in range(102, 162)]
            },
            "org_holding": {"accepted": [{"partition": "20250430"}]},
        }
    }
    coverage = disclosure_freeze_coverage(payload)
    days = disclosure_freeze_coverage_days(payload)
    assert days == ("20250331", "20250430", "20250530")
    assert coverage["union_day_count"] == 3
    assert coverage["excluded_domains"] == ["nominal_ohlcv"]
    assert len(payload["domains"]["nominal_ohlcv"]["accepted"]) == 60
    assert coverage["union_day_count"] != 60


def test_live_disclosure_freeze_coverage_excludes_ohlcv_span() -> None:
    path = (
        Path(__file__).resolve().parents[3]
        / "data"
        / "lineage"
        / "disclosure_dataset_snapshot.json"
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    coverage = disclosure_freeze_coverage(payload)
    assert coverage["excluded_domains"] == ["nominal_ohlcv"]
    assert coverage["unclassified_domains"] == []
    holders = coverage["by_domain"]["holders_top10"]
    assert len(holders) == 8
    assert coverage["union_day_count"] != 1553
    union: set[str] = set()
    for name in ("holders_top10", "org_holding", "stk_holdertrade"):
        union.update(coverage["by_domain"][name])
    assert coverage["union_day_count"] == len(union)
