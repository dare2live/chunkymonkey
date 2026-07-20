"""B-pit mart cutover gate (fail-closed; default false)."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
import yaml

from services.b_pit_mart_cutover import (
    BPitMartCutoverConfig,
    BPitMartCutoverDecision,
    BPitMartCutoverError,
    BPitMartProductionRead,
    load_b_pit_mart_cutover_config,
    load_project_universe_breadth_as_mart_truth,
    resolve_b_pit_mart_cutover,
    resolve_b_pit_mart_production_read,
)

_REPO = Path(__file__).resolve().parents[3]
_CFG_PATH = _REPO / "backend" / "config" / "b_pit_mart_cutover.yaml"
_LIVE_SHADOW = _REPO / "data" / "lineage" / "b_pit_breadth_shadow"
_DEF_V = "market_sensing_project_breadth_v0"
_POLICY_HASH = "448b589a0e1e66095611108a0f6be807846e665a8b379b8bd89588d880f7f4dd"


def _shadow_payload(
    *,
    ratios_match_all: bool = True,
    diverge_day_count: int = 0,
    match_day_count: int = 120,
    day_count: int = 120,
    baseline_kind: str = "membership_restricted_proxy",
    policy_hash: str = _POLICY_HASH,
    window_start: str = "20260116",
    window_end: str = "20260717",
) -> dict:
    return {
        "kind": "b_pit_breadth_shadow_remeasure",
        "population_kind_formal": "project_universe_pit",
        "match_baseline_kind": baseline_kind,
        "universe_policy_id": "active_a_share_trading_universe",
        "universe_policy_hash": policy_hash,
        "cutover_allowed": False,
        "window": {
            "kind": "b_pit_breadth_shadow_window",
            "window_start": window_start,
            "window_end": window_end,
            "frontier_day": window_end,
            "day_count": day_count,
            "match_day_count": match_day_count,
            "diverge_day_count": diverge_day_count,
            "error_day_count": 0,
            "ratios_match_all": ratios_match_all,
            "cutover_allowed": False,
            "max_abs_ratio_delta": 0.0 if ratios_match_all else 0.01,
            "mean_abs_ratio_delta": 0.0 if ratios_match_all else 0.01,
        },
        "frontier_day": window_end,
        "notes": ["fixture_shadow_for_cutover_gate"],
    }


def _write_shadow(tmp_path: Path, payload: dict) -> Path:
    root = tmp_path / "b_pit_breadth_shadow"
    root.mkdir(parents=True, exist_ok=True)
    (root / "manifest.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    summary = {
        "kind": payload["kind"],
        "cutover_allowed": False,
        "match_baseline_kind": payload["match_baseline_kind"],
        "window": payload["window"],
        "frontier_day": payload["frontier_day"],
        "artifact": "manifest.json",
    }
    (root / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return root


def _opt_in_cfg(artifact_root: Path, **overrides: object) -> BPitMartCutoverConfig:
    raw = {
        "cutover_allowed": True,
        "expected_definition_version": _DEF_V,
        "expected_universe_policy_hash": _POLICY_HASH,
        "expected_match_baseline_kind": "membership_restricted_proxy",
        "expected_window_start": "20260116",
        "expected_window_end": "20260717",
        "require_ratios_match_all": True,
        "shadow_artifact_dir": str(artifact_root),
    }
    raw.update(overrides)
    return BPitMartCutoverConfig.from_mapping({"mart_cutover": raw})


def test_default_config_cutover_true_owner_opt_in() -> None:
    """Owner opt-in 2026-07-20: on-disk yaml flips B-pit mart cutover ON."""

    cfg = load_b_pit_mart_cutover_config(_CFG_PATH)
    assert cfg.cutover_allowed is True
    assert cfg.expected_definition_version == _DEF_V
    assert cfg.expected_universe_policy_hash == _POLICY_HASH
    assert cfg.expected_match_baseline_kind == "membership_restricted_proxy"
    assert cfg.expected_window_start == "20260116"
    assert cfg.expected_window_end == "20260717"


def test_default_yaml_with_live_match_artifact_mart_cutover() -> None:
    """Default yaml (ON) + live MATCH remeasure → MART_CUTOVER (not LEGACY)."""

    assert _LIVE_SHADOW.joinpath("manifest.json").is_file()
    decision = resolve_b_pit_mart_cutover(
        "20260717",
        artifact_root=_LIVE_SHADOW,
    )
    assert isinstance(decision, BPitMartCutoverDecision)
    assert decision.cutover_allowed is True
    assert decision.source == "project_universe_pit"
    assert decision.status == "MART_CUTOVER"
    assert "gates_passed" in decision.reasons
    assert decision.shadow_payload is not None


def test_explicit_disabled_config_stays_legacy_even_with_live_match() -> None:
    """Fail-closed still enforced when a config explicitly disables cutover."""

    cfg = BPitMartCutoverConfig.from_mapping(
        {"mart_cutover": {"cutover_allowed": False}}
    )
    decision = resolve_b_pit_mart_cutover(
        "20260717",
        config=cfg,
        artifact_root=_LIVE_SHADOW,
    )
    assert decision.cutover_allowed is False
    assert decision.source == "legacy_mart"
    assert decision.status == "LEGACY"
    assert "config_cutover_allowed_false" in decision.reasons
    assert decision.shadow_payload is None


def test_production_read_boundary_default_yaml_mart_cutover() -> None:
    read = resolve_b_pit_mart_production_read(
        "20260717",
        artifact_root=_LIVE_SHADOW,
    )
    assert isinstance(read, BPitMartProductionRead)
    assert read.status == "MART_CUTOVER"
    assert read.source == "project_universe_pit"
    assert read.uses_legacy is False
    assert read.cutover_allowed is True
    assert read.shadow_payload is not None
    assert "production_read_boundary_mart_cutover" in read.notes


def test_reject_enable_without_shadow_artifact(tmp_path: Path) -> None:
    empty = tmp_path / "empty_shadow"
    empty.mkdir()
    cfg = _opt_in_cfg(empty)
    decision = resolve_b_pit_mart_cutover("20260717", config=cfg, artifact_root=empty)
    assert decision.cutover_allowed is False
    assert decision.status == "BLOCKED"
    assert any("missing_shadow" in r for r in decision.reasons)


def test_reject_diverge_shadow(tmp_path: Path) -> None:
    root = _write_shadow(
        tmp_path,
        _shadow_payload(ratios_match_all=False, diverge_day_count=3, match_day_count=117),
    )
    cfg = _opt_in_cfg(root)
    decision = resolve_b_pit_mart_cutover("20260717", config=cfg, artifact_root=root)
    assert decision.cutover_allowed is False
    assert decision.status == "BLOCKED"
    assert any("ratios_not_match_all" in r or "diverge" in r for r in decision.reasons)


def test_reject_universe_policy_hash_mismatch(tmp_path: Path) -> None:
    root = _write_shadow(
        tmp_path, _shadow_payload(policy_hash="0" * 64)
    )
    cfg = _opt_in_cfg(root)
    decision = resolve_b_pit_mart_cutover("20260717", config=cfg, artifact_root=root)
    assert decision.cutover_allowed is False
    assert decision.status == "BLOCKED"
    assert any("universe_policy_hash_mismatch" in r for r in decision.reasons)


def test_reject_baseline_kind_mismatch(tmp_path: Path) -> None:
    root = _write_shadow(
        tmp_path,
        _shadow_payload(baseline_kind="accepted_canonical_unfiltered_proxy"),
    )
    cfg = _opt_in_cfg(root)
    decision = resolve_b_pit_mart_cutover("20260717", config=cfg, artifact_root=root)
    assert decision.cutover_allowed is False
    assert decision.status == "BLOCKED"
    assert any("match_baseline_kind_mismatch" in r for r in decision.reasons)


def test_reject_window_mismatch(tmp_path: Path) -> None:
    root = _write_shadow(
        tmp_path,
        _shadow_payload(window_start="20260201", window_end="20260717"),
    )
    cfg = _opt_in_cfg(root)
    decision = resolve_b_pit_mart_cutover("20260717", config=cfg, artifact_root=root)
    assert decision.cutover_allowed is False
    assert decision.status == "BLOCKED"
    assert any("window_start_mismatch" in r for r in decision.reasons)


def test_reject_day_outside_attested_window(tmp_path: Path) -> None:
    root = _write_shadow(tmp_path, _shadow_payload())
    cfg = _opt_in_cfg(root)
    decision = resolve_b_pit_mart_cutover("20251201", config=cfg, artifact_root=root)
    assert decision.cutover_allowed is False
    assert decision.status == "BLOCKED"
    assert any("trade_date_outside_shadow_window" in r for r in decision.reasons)


def test_reject_definition_version_mismatch(tmp_path: Path) -> None:
    root = _write_shadow(tmp_path, _shadow_payload())
    cfg = _opt_in_cfg(root, expected_definition_version="wrong_def_v0")
    decision = resolve_b_pit_mart_cutover("20260717", config=cfg, artifact_root=root)
    assert decision.cutover_allowed is False
    assert decision.status == "BLOCKED"
    assert any("definition_version" in r for r in decision.reasons)


def test_mart_cutover_when_all_gates_pass(tmp_path: Path) -> None:
    root = _write_shadow(tmp_path, _shadow_payload())
    cfg = _opt_in_cfg(root)
    decision = resolve_b_pit_mart_cutover("20260717", config=cfg, artifact_root=root)
    assert decision.cutover_allowed is True
    assert decision.source == "project_universe_pit"
    assert decision.status == "MART_CUTOVER"
    assert decision.shadow_payload is not None
    assert "config_explicit_opt_in" in decision.notes
    assert "shadow_match_attested" in decision.notes


def test_production_read_mart_cutover_exposes_shadow(tmp_path: Path) -> None:
    root = _write_shadow(tmp_path, _shadow_payload())
    cfg = _opt_in_cfg(root)
    read = resolve_b_pit_mart_production_read(
        "20260717", config=cfg, artifact_root=root
    )
    assert read.status == "MART_CUTOVER"
    assert read.uses_legacy is False
    assert read.cutover_allowed is True
    assert read.shadow_payload is not None
    assert read.shadow_payload["window"]["ratios_match_all"] is True

    loaded = load_project_universe_breadth_as_mart_truth(
        "20260717", config=cfg, artifact_root=root
    )
    assert loaded["kind"] == "b_pit_breadth_shadow_remeasure"
    assert loaded["window"]["ratios_match_all"] is True


def test_silent_mart_truth_read_refused_when_gate_disabled() -> None:
    """Explicit disabled config must refuse silent project-universe mart truth."""

    cfg = BPitMartCutoverConfig.from_mapping(
        {"mart_cutover": {"cutover_allowed": False}}
    )
    with pytest.raises(BPitMartCutoverError, match="resolver|gate|mart"):
        load_project_universe_breadth_as_mart_truth(
            "20260717", config=cfg, artifact_root=_LIVE_SHADOW
        )


def test_live_e86410d0_artifact_match_and_gate_now_true() -> None:
    """Live remeasure MATCH 120/120; owner opt-in yaml now true → MART_CUTOVER."""

    manifest = json.loads(
        (_LIVE_SHADOW / "manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["window"]["ratios_match_all"] is True
    assert manifest["window"]["match_day_count"] == 120
    assert manifest["window"]["diverge_day_count"] == 0
    assert manifest["match_baseline_kind"] == "membership_restricted_proxy"
    assert manifest["universe_policy_hash"] == _POLICY_HASH

    cfg = load_b_pit_mart_cutover_config(_CFG_PATH)
    assert cfg.cutover_allowed is True
    read = resolve_b_pit_mart_production_read("20260717")
    assert read.uses_legacy is False
    assert read.cutover_allowed is True
    assert read.status == "MART_CUTOVER"


def test_live_opt_in_fixture_passes_with_copied_e86410d0_artifact(
    tmp_path: Path,
) -> None:
    """Opt-in + copied live MATCH artifact → MART_CUTOVER (yaml still false)."""

    root = tmp_path / "copied_shadow"
    shutil.copytree(_LIVE_SHADOW, root)
    cfg = _opt_in_cfg(root)
    decision = resolve_b_pit_mart_cutover("20260717", config=cfg, artifact_root=root)
    assert decision.cutover_allowed is True
    assert decision.status == "MART_CUTOVER"
    # Owner opt-in flipped the on-disk yaml to true (2026-07-20).
    on_disk = yaml.safe_load(_CFG_PATH.read_text(encoding="utf-8"))
    assert on_disk["mart_cutover"]["cutover_allowed"] is True


def test_pulse_attest_default_now_mart_cutover() -> None:
    from services.market_pulse_b_pit_read import attest_pulse_b_pit_mart_production_read

    att = attest_pulse_b_pit_mart_production_read("20260717")
    assert att["uses_legacy"] is False
    assert att["cutover_allowed"] is True
    assert att["status"] == "MART_CUTOVER"
    assert att["source"] == "project_universe_pit"
    assert "gates_passed" in att["reasons"]
    assert "pulse_ui_attestation" in att["notes"]
