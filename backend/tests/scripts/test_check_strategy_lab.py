from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from services.research_runtime import DatasetSnapshot, SnapshotInputRef
from services.strategy_lab import load_policy

from scripts import check_strategy_lab as checker


REPO = Path(__file__).resolve().parents[3]
SCRIPT = REPO / "backend" / "scripts" / "check_strategy_lab.py"


def test_framework_check_reports_live_inputs_when_ready() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--framework", "--json"],
        cwd=REPO,
        env={**os.environ, "PYTHONPATH": str(REPO / "backend")},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["framework_installed"] is True
    assert payload["framework_ready"] is True
    assert payload["execution_mode"] == "manual_only"
    assert payload["formal_rx_authorized"] is True
    assert payload["optuna_authorized"] is False
    assert payload["modal_authorized"] is False
    assert payload["live_inputs"]["ready"] is True
    assert payload["live_inputs"]["snapshots"]["main_rally"]["status"] == "READY"
    assert payload["live_inputs"]["snapshots"]["disclosure"]["status"] == "READY"
    assert payload["claimable"] is False
    assert payload["formal_rx_compute"]["allowed"] is True
    assert payload["formal_rx_compute"]["claimable"] is False
    assert payload["formal_rx_compute"]["reasons"] == []
    packages = payload["strategy_packages"]
    assert packages["loaded"] is True
    assert packages["claimable"] is False
    assert "institution_follow_v1" in packages["packages"]
    assert "institution_follow_v1" in packages["spec_ids"]
    coverage = payload["disclosure_coverage"]
    assert coverage["denominator"] == "disclosure_freeze_partitions"
    assert coverage["excluded_domains"] == ["nominal_ohlcv"]
    assert coverage["union_day_count"] != 1553
    assert len(coverage["by_domain"]["holders_top10"]) == 8
    ablation = payload["ablation_verdicts"]
    assert ablation["role"] == "ablation_only"
    assert ablation["claimable"] is False
    assert ablation["not_strategy_spec"] is True
    assert ablation["manifests"]["phase_e"]["present"] is True
    assert ablation["manifests"]["phase_f"]["present"] is True
    challenge = payload["formula_challenge"]
    assert challenge["status"] == "synthetic_smoke_ready"
    assert challenge["one_name_replay"] == "offline_day_membership_ready"
    assert challenge["live_pointer_bind"] == "one_name_ready"
    assert challenge["live_replay"] == "not_implemented"
    assert challenge["b5_ablation"] == "not_implemented"
    assert challenge["purged_wf"] == "not_implemented"
    assert challenge["holdout"] == "not_implemented"
    assert challenge["experiment_verdict"] == "not_implemented"
    assert challenge["absorb"] == "not_implemented"
    assert challenge["claimable"] is False
    follow_paper = payload["follow_spec_paper"]
    assert follow_paper["status"] == "snapshot_events_ready"
    assert follow_paper["ablation_json"] == "not_this_spec"
    assert follow_paper["claimable"] is False
    rally_paper = payload["rally_setup_paper"]
    assert rally_paper["status"] == "ready"
    assert rally_paper["full_episode"] == "not_implemented"
    assert rally_paper["claimable"] is False


def test_live_input_ready_path_uses_typed_validation_window(
    tmp_path: Path, monkeypatch
) -> None:
    (tmp_path / "backend" / "config").mkdir(parents=True)
    (tmp_path / "backend" / "config" / "holdout_policy.yaml").write_text(
        'holdout_start: "20250601"\n',
        encoding="utf-8",
    )
    main_path = (
        tmp_path / "data" / "lineage" / "main_rally_dataset_snapshot" / "snapshot.json"
    )
    disclosure_path = tmp_path / "data" / "lineage" / "disclosure_dataset_snapshot.json"
    main_path.parent.mkdir(parents=True)
    disclosure_path.parent.mkdir(parents=True, exist_ok=True)
    main_path.write_text("{}\n", encoding="utf-8")
    disclosure_path.write_text("{}\n", encoding="utf-8")
    snapshot = DatasetSnapshot(
        snapshot_id="ready-development",
        inputs=(
            SnapshotInputRef(
                dataset_id="tier0.market_data.nominal_ohlcv_daily",
                partitions=("20250508", "20250520"),
                content_hash="nominal-content",
                config_hash="nominal-config",
            ),
        ),
        universe_id="traded_on_observation_date",
        config_hash="snapshot-config",
        available_at_lower="20250508",
        available_at_upper="20250520",
        content_hash="snapshot-content",
        frozen_at="2026-07-27T00:00:00+00:00",
        source_kind="test",
        notes=(),
    )
    monkeypatch.setattr(checker, "dataset_snapshot_from_main_rally", lambda _: snapshot)
    monkeypatch.setattr(checker, "dataset_snapshot_from_disclosure", lambda _: snapshot)

    status = checker._live_input_status(load_policy(), repo=tmp_path)

    assert status["train_end"] == "20250512"
    assert status["ready"] is True
    assert status["snapshots"]["main_rally"]["status"] == "READY"
    assert status["snapshots"]["disclosure"]["status"] == "READY"
