"""Shared BestChoice pipeline defaults.

Keep repeated run IDs and walk-forward dates in one config-owned place instead
of repeating the same literals across scripts.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "bestchoice_pipeline.yaml"


@dataclass(frozen=True)
class BestChoicePipelineConfig:
    bc_run_id: str
    context_exit_policy_run_id: str
    context_exit_policy_run_id_full: str
    walkforward_start_date: str
    walkforward_train_end_date: str
    walkforward_test_start_date: str
    walkforward_test_end_date: str
    walkforward_cutoffs: tuple[str, ...]
    context_exit_top_n: int
    ensemble_train_start_date: str
    ensemble_train_end_date: str
    ensemble_test_start_date: str
    ensemble_test_end_date: str


def _load_yaml(path: Path) -> dict[str, Any]:
    loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(loaded, dict):
        raise ValueError(f"{path.name} must contain a mapping")
    return loaded


def _require_str(raw: dict[str, Any], key: str, raw_path: Path) -> str:
    value = raw[key]
    if isinstance(value, bool) or not isinstance(value, str) or not value.strip():
        raise ValueError(f"{raw_path.name}: {key} must be a non-empty string")
    return value.strip()


def _require_int(raw: dict[str, Any], key: str, raw_path: Path) -> int:
    value = raw[key]
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{raw_path.name}: {key} must be an integer")
    return value


def _require_str_list(raw: dict[str, Any], key: str, raw_path: Path) -> tuple[str, ...]:
    value = raw[key]
    if not isinstance(value, list) or not value:
        raise ValueError(f"{raw_path.name}: {key} must be a non-empty list")
    items: list[str] = []
    for idx, item in enumerate(value):
        if isinstance(item, bool) or not isinstance(item, str) or not item.strip():
            raise ValueError(f"{raw_path.name}: {key}[{idx}] must be a non-empty string")
        items.append(item.strip())
    return tuple(items)


def load_bestchoice_pipeline_config(path: Path | None = None) -> BestChoicePipelineConfig:
    raw_path = path or CONFIG_PATH
    raw = _load_yaml(raw_path)
    try:
        bc_run_id = _require_str(raw, "bc_run_id", raw_path)
        context_exit_policy_run_id = _require_str(raw, "context_exit_policy_run_id", raw_path)
        context_exit_policy_run_id_full = _require_str(raw, "context_exit_policy_run_id_full", raw_path)
        walkforward_start_date = _require_str(raw, "walkforward_start_date", raw_path)
        walkforward_train_end_date = _require_str(raw, "walkforward_train_end_date", raw_path)
        walkforward_test_start_date = _require_str(raw, "walkforward_test_start_date", raw_path)
        walkforward_test_end_date = _require_str(raw, "walkforward_test_end_date", raw_path)
        walkforward_cutoffs = _require_str_list(raw, "walkforward_cutoffs", raw_path)
        context_exit_top_n = _require_int(raw, "context_exit_top_n", raw_path)
        ensemble_train_start_date = _require_str(raw, "ensemble_train_start_date", raw_path)
        ensemble_train_end_date = _require_str(raw, "ensemble_train_end_date", raw_path)
        ensemble_test_start_date = _require_str(raw, "ensemble_test_start_date", raw_path)
        ensemble_test_end_date = _require_str(raw, "ensemble_test_end_date", raw_path)
    except KeyError as exc:
        raise ValueError(f"{raw_path.name}: missing bestchoice pipeline key {exc.args[0]}") from exc

    if context_exit_top_n <= 0:
        raise ValueError(f"{raw_path.name}: context_exit_top_n must be positive")

    return BestChoicePipelineConfig(
        bc_run_id=bc_run_id,
        context_exit_policy_run_id=context_exit_policy_run_id,
        context_exit_policy_run_id_full=context_exit_policy_run_id_full,
        walkforward_start_date=walkforward_start_date,
        walkforward_train_end_date=walkforward_train_end_date,
        walkforward_test_start_date=walkforward_test_start_date,
        walkforward_test_end_date=walkforward_test_end_date,
        walkforward_cutoffs=walkforward_cutoffs,
        context_exit_top_n=context_exit_top_n,
        ensemble_train_start_date=ensemble_train_start_date,
        ensemble_train_end_date=ensemble_train_end_date,
        ensemble_test_start_date=ensemble_test_start_date,
        ensemble_test_end_date=ensemble_test_end_date,
    )


DEFAULT_BESTCHOICE_PIPELINE_CONFIG = load_bestchoice_pipeline_config()
