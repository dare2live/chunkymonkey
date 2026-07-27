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


def test_framework_check_fails_closed_while_live_inputs_are_blocked() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--framework", "--json"],
        cwd=REPO,
        env={**os.environ, "PYTHONPATH": str(REPO / "backend")},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2, result.stderr
    payload = json.loads(result.stdout)
    assert payload["framework_installed"] is True
    assert payload["framework_ready"] is False
    assert payload["execution_mode"] == "manual_only"
    assert payload["formal_rx_authorized"] is False
    assert payload["optuna_authorized"] is False
    assert payload["modal_authorized"] is False
    assert payload["live_inputs"]["ready"] is False
    assert payload["live_inputs"]["snapshots"]["main_rally"]["status"] == "BLOCKED"
    assert payload["live_inputs"]["snapshots"]["disclosure"]["status"] == "BLOCKED"


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
