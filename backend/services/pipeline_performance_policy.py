"""Runtime budget policy for pipeline observability.

This policy is intentionally separate from pricing/label definitions so adding
or tuning monitored pipelines does not mutate model pricing policy hashes.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "pipeline_performance_policy.yaml"


@dataclass(frozen=True)
class PipelinePerformancePolicy:
    policy_id: str
    version: int
    long_running_fetch_or_feature_job_is_bug_until_proven_otherwise: bool
    progress_heartbeat_required_after_s: float
    long_run_requires_stage_timing_manifest: bool
    default_pipeline_duration_budget_s: float
    pipeline_duration_budgets_s: dict[str, float]

    def to_dict(self) -> dict[str, Any]:
        return {
            "policy_id": self.policy_id,
            "version": self.version,
            "long_running_fetch_or_feature_job_is_bug_until_proven_otherwise": (
                self.long_running_fetch_or_feature_job_is_bug_until_proven_otherwise
            ),
            "progress_heartbeat_required_after_s": self.progress_heartbeat_required_after_s,
            "long_run_requires_stage_timing_manifest": self.long_run_requires_stage_timing_manifest,
            "default_pipeline_duration_budget_s": self.default_pipeline_duration_budget_s,
            "pipeline_duration_budgets_s": dict(self.pipeline_duration_budgets_s),
        }


def _load_yaml(path: Path) -> dict[str, Any]:
    try:
        import yaml  # type: ignore
    except Exception as exc:  # pragma: no cover - local runtime has PyYAML.
        raise RuntimeError("PyYAML is required to load pipeline_performance_policy.yaml") from exc
    loaded = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}
    return loaded if isinstance(loaded, dict) else {}


def _float_map(value: Any) -> dict[str, float]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, float] = {}
    for key, raw in value.items():
        try:
            result[str(key)] = float(raw)
        except (TypeError, ValueError):
            continue
    return result


def load_pipeline_performance_policy(path: str | Path | None = None) -> PipelinePerformancePolicy:
    raw = _load_yaml(Path(path) if path is not None else CONFIG_PATH)
    return PipelinePerformancePolicy(
        policy_id=str(raw.get("policy_id") or "pipeline_performance_policy_v1"),
        version=int(raw.get("version") or 1),
        long_running_fetch_or_feature_job_is_bug_until_proven_otherwise=bool(
            raw.get("long_running_fetch_or_feature_job_is_bug_until_proven_otherwise", True)
        ),
        progress_heartbeat_required_after_s=float(raw.get("progress_heartbeat_required_after_s") or 30),
        long_run_requires_stage_timing_manifest=bool(
            raw.get("long_run_requires_stage_timing_manifest", True)
        ),
        default_pipeline_duration_budget_s=float(raw.get("default_pipeline_duration_budget_s") or 600),
        pipeline_duration_budgets_s=_float_map(raw.get("pipeline_duration_budgets_s")),
    )
