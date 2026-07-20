"""Phase D persist: immutable ExperimentRun artifacts + real fold bind."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import persist_phase_d_experiment_runs as mod


REPO = Path(__file__).resolve().parents[3]


def test_persist_writes_immutable_run_and_manifest(tmp_path: Path) -> None:
    manifest = mod.persist(repo=tmp_path)
    assert manifest["kind"] == "phase_d_experiment_run_manifest"
    summary = manifest["summary"]
    assert summary["claimable"] is False
    assert summary["strategy_release"] is False
    assert summary["fold_protocol"] == "purged_walk_forward"
    assert summary["n_folds"] == 3
    assert summary["fold_ids"] == [
        "purged_fold_0",
        "purged_fold_1",
        "purged_fold_2",
    ]
    assert "backend/scripts/build_agent_board.py" in manifest["consumers"]

    run_path = tmp_path / manifest["runs"]["b0_bound"]
    payload = json.loads(run_path.read_text(encoding="utf-8"))
    assert payload["kind"] == "phase_d_experiment_run"
    assert payload["strategy_release"] is False
    assert payload["optuna"] is False
    assert payload["verdict"]["claimable"] is False
    prereg = payload["run"]["prereg"]
    assert prereg["claimable_target"] is False
    assert prereg["fold_embargo"]["protocol"] == "purged_walk_forward"
    assert prereg["fold_embargo"]["fold_ids"] == [
        "purged_fold_0",
        "purged_fold_1",
        "purged_fold_2",
    ]
    assert "bound_from_measured_walk_forward_plan" in prereg["fold_embargo"]["notes"]

    measured_path = tmp_path / manifest["runs"]["measured_offline"]
    measured = json.loads(measured_path.read_text(encoding="utf-8"))
    assert measured["path"] == "runtime_owned_measured_offline"
    assert measured["verdict"]["claimable"] is False
    assert measured["run"]["strategy_package"] == "phase_d_offline"
    assert measured["run"]["strategy_package"] != "institution_follow"
    m_measure = measured["run"]["artifact_manifest"]["measure"]
    assert m_measure["status"] == "measured"
    assert m_measure["paper_fills"] == "measured"
    assert isinstance(m_measure["total_return"], float)
    mo = summary["measured_offline"]
    assert mo["claimable"] is False
    assert mo["measure_status"] == "measured"
    assert mo["n_trades_completed"] == 2


def test_persist_idempotent_on_same_hashes(tmp_path: Path) -> None:
    first = mod.persist(repo=tmp_path)
    second = mod.persist(repo=tmp_path)
    # Same manifest reused — no rewrite, experiment_id stable.
    assert second["summary"]["experiment_id"] == first["summary"]["experiment_id"]
    assert second["frozen_at"] == first["frozen_at"]


def test_load_plan_fails_closed_without_walk_forward(tmp_path: Path) -> None:
    missing = tmp_path / "nope.json"
    with pytest.raises(SystemExit, match="missing"):
        mod.load_measured_walk_forward_plan(missing)

    no_plan = tmp_path / "b0.json"
    no_plan.write_text(
        json.dumps({"verdict_full": {"details": {}}}), encoding="utf-8"
    )
    with pytest.raises(SystemExit, match="walk_forward"):
        mod.load_measured_walk_forward_plan(no_plan)


def test_committed_artifact_matches_live_sources() -> None:
    """The committed lineage artifact must not drift from its declared inputs."""

    manifest_path = REPO / mod.OUT_DIR_REL / mod.MANIFEST_NAME
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["snapshot_hash"] == mod._sha256_file(
        REPO / manifest["snapshot_relpath"]
    )
    assert manifest["b0_artifact_hash"] == mod._sha256_file(
        REPO / manifest["b0_artifact_relpath"]
    )
    assert manifest["summary"]["claimable"] is False
