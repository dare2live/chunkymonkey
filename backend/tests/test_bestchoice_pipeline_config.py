from __future__ import annotations

from pathlib import Path

import pytest

from services.bestchoice_config import DEFAULT_BESTCHOICE_PIPELINE_CONFIG, load_bestchoice_pipeline_config


def test_default_bestchoice_pipeline_config_is_loaded() -> None:
    cfg = DEFAULT_BESTCHOICE_PIPELINE_CONFIG
    assert cfg.bc_run_id == "bestchoice_formula_optuna_20260521_v1"
    assert cfg.context_exit_policy_run_id == "bestchoice_context_exit_v1_20260522"
    assert cfg.context_exit_policy_run_id_full == "bestchoice_context_exit_v1_20260522_full"
    assert cfg.walkforward_start_date == "2023-01-03"
    assert cfg.walkforward_train_end_date == "2024-12-31"
    assert cfg.walkforward_test_start_date == "2025-01-01"
    assert cfg.walkforward_test_end_date == "2026-04-13"
    assert cfg.walkforward_cutoffs == ("2024-06-01", "2025-01-01")
    assert cfg.context_exit_top_n == 100
    assert cfg.ensemble_train_start_date == "2024-01-02"
    assert cfg.ensemble_train_end_date == "2024-06-28"
    assert cfg.ensemble_test_start_date == "2024-07-01"
    assert cfg.ensemble_test_end_date == "2026-04-13"


def test_load_bestchoice_pipeline_config_rejects_missing_key(tmp_path: Path) -> None:
    path = tmp_path / "bestchoice_pipeline.yaml"
    path.write_text(
        "\n".join(
            [
                "context_exit_policy_run_id: bestchoice_context_exit_v1_20260522",
                "context_exit_policy_run_id_full: bestchoice_context_exit_v1_20260522_full",
                "walkforward_start_date: '2023-01-03'",
                "walkforward_train_end_date: '2024-12-31'",
                "walkforward_test_start_date: '2025-01-01'",
                "walkforward_test_end_date: '2026-04-13'",
                "walkforward_cutoffs:",
                "  - '2024-06-01'",
                "  - '2025-01-01'",
                "context_exit_top_n: 100",
                "ensemble_train_start_date: '2024-01-02'",
                "ensemble_train_end_date: '2024-06-28'",
                "ensemble_test_start_date: '2024-07-01'",
                "ensemble_test_end_date: '2026-04-13'",
            ]
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="missing bestchoice pipeline key bc_run_id"):
        load_bestchoice_pipeline_config(path)


def test_load_bestchoice_pipeline_config_rejects_bad_top_n(tmp_path: Path) -> None:
    path = tmp_path / "bestchoice_pipeline.yaml"
    path.write_text(
        "\n".join(
            [
                "bc_run_id: bestchoice_formula_optuna_20260521_v1",
                "context_exit_policy_run_id: bestchoice_context_exit_v1_20260522",
                "context_exit_policy_run_id_full: bestchoice_context_exit_v1_20260522_full",
                "walkforward_start_date: '2023-01-03'",
                "walkforward_train_end_date: '2024-12-31'",
                "walkforward_test_start_date: '2025-01-01'",
                "walkforward_test_end_date: '2026-04-13'",
                "walkforward_cutoffs:",
                "  - '2024-06-01'",
                "  - '2025-01-01'",
                "context_exit_top_n: 0",
                "ensemble_train_start_date: '2024-01-02'",
                "ensemble_train_end_date: '2024-06-28'",
                "ensemble_test_start_date: '2024-07-01'",
                "ensemble_test_end_date: '2026-04-13'",
            ]
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="context_exit_top_n must be positive"):
        load_bestchoice_pipeline_config(path)
