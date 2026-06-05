"""Experiment job family contract loader.

This module is the thin control-plane layer for data validation, backtesting,
model training, and parameter search jobs. It does not schedule work, call cloud
APIs, or reimplement domain gates. It validates that a requested job family is
registered, that the backend is allowed, and that expected artifacts/gates are
declared before a runnable script or future provider adapter is selected.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
import re

import yaml


CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "experiment_jobs.yaml"
_ID_RE = re.compile(r"^[a-z][a-z0-9_]*$")


def _load_yaml(path: Path) -> dict[str, Any]:
    loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(loaded, dict):
        raise ValueError(f"{path.name}: expected mapping")
    return loaded


def _require_id(value: Any, *, field_name: str, path: Path) -> str:
    text = str(value or "").strip()
    if not _ID_RE.match(text):
        raise ValueError(f"{path.name}: invalid {field_name}: {value!r}")
    return text


def _require_str(value: Any, *, field_name: str, path: Path) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{path.name}: {field_name} must be a non-empty string")
    return text


def _str_tuple(value: Any, *, field_name: str, path: Path, allow_empty: bool = False) -> tuple[str, ...]:
    if value is None:
        if allow_empty:
            return ()
        raise ValueError(f"{path.name}: {field_name} must be a non-empty list")
    if not isinstance(value, list):
        raise ValueError(f"{path.name}: {field_name} must be a list")
    items = tuple(str(item).strip() for item in value if str(item).strip())
    if len(items) != len(value) or (not items and not allow_empty):
        raise ValueError(f"{path.name}: {field_name} must contain non-empty strings")
    return items


def _parse_gate_evidence(raw_items: tuple[str, ...] | None) -> tuple[tuple[str, ...], set[str], tuple[str, ...]]:
    evidence: list[str] = []
    gate_ids: set[str] = set()
    blockers: list[str] = []
    for index, raw_item in enumerate(raw_items or (), start=1):
        text = str(raw_item or "").strip()
        if not text:
            continue
        if "=" not in text:
            blockers.append(f"malformed_gate_evidence:{index}")
            continue
        gate_id, evidence_value = (part.strip() for part in text.split("=", 1))
        if not _ID_RE.match(gate_id):
            blockers.append(f"invalid_gate_evidence:{index}")
            continue
        if not evidence_value:
            blockers.append(f"empty_gate_evidence:{gate_id}")
            continue
        evidence.append(f"{gate_id}={evidence_value}")
        gate_ids.add(gate_id)
    return tuple(evidence), gate_ids, tuple(blockers)


@dataclass(frozen=True)
class ExecutionBackend:
    backend_id: str
    status: str
    execution_mode: str
    artifact_mode: str
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def active(self) -> bool:
        return self.status == "active"

    def to_report(self) -> dict[str, Any]:
        return {
            "backend_id": self.backend_id,
            "status": self.status,
            "execution_mode": self.execution_mode,
            "artifact_mode": self.artifact_mode,
            "notes": list(self.notes),
        }


@dataclass(frozen=True)
class ArtifactContract:
    artifact_id: str
    kind: str
    required: bool
    path_glob: str | None = None
    table: str | None = None
    min_rows: int | None = None

    def to_report(self) -> dict[str, Any]:
        report: dict[str, Any] = {
            "id": self.artifact_id,
            "kind": self.kind,
            "required": self.required,
        }
        if self.path_glob is not None:
            report["path_glob"] = self.path_glob
        if self.table is not None:
            report["table"] = self.table
        if self.min_rows is not None:
            report["min_rows"] = self.min_rows
        return report


@dataclass(frozen=True)
class JobFamily:
    family_id: str
    owner_module: str
    purpose: str
    allowed_backends: tuple[str, ...]
    command_family: str
    required_plan_fields: tuple[str, ...]
    required_gates: tuple[str, ...]
    artifact_contracts: tuple[ArtifactContract, ...]

    def to_report(self) -> dict[str, Any]:
        return {
            "family_id": self.family_id,
            "owner_module": self.owner_module,
            "purpose": self.purpose,
            "allowed_backends": list(self.allowed_backends),
            "command_family": self.command_family,
            "required_plan_fields": list(self.required_plan_fields),
            "required_gates": list(self.required_gates),
            "artifact_contracts": [item.to_report() for item in self.artifact_contracts],
        }


@dataclass(frozen=True)
class ExperimentJobPlan:
    job_id: str
    family: JobFamily
    backend: ExecutionBackend
    model_id: str | None
    input_snapshot: str | None
    objective: str | None
    rollback_plan: str | None
    gate_evidence: tuple[str, ...]
    artifact_dir: str
    blocked_reasons: tuple[str, ...]

    @property
    def ready_to_run(self) -> bool:
        return not self.blocked_reasons

    def to_report(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "family": self.family.to_report(),
            "backend": self.backend.to_report(),
            "model_id": self.model_id,
            "input_snapshot": self.input_snapshot,
            "objective": self.objective,
            "rollback_plan": self.rollback_plan,
            "gate_evidence": list(self.gate_evidence),
            "artifact_dir": self.artifact_dir,
            "ready_to_run": self.ready_to_run,
            "blocked_reasons": list(self.blocked_reasons),
        }


@dataclass(frozen=True)
class ExperimentJobContract:
    version: int
    backends: dict[str, ExecutionBackend]
    families: dict[str, JobFamily]

    def require_backend(self, backend_id: str) -> ExecutionBackend:
        try:
            return self.backends[backend_id]
        except KeyError as exc:
            known = ", ".join(sorted(self.backends))
            raise KeyError(f"unknown experiment backend {backend_id!r}; known: {known}") from exc

    def require_family(self, family_id: str) -> JobFamily:
        try:
            return self.families[family_id]
        except KeyError as exc:
            known = ", ".join(sorted(self.families))
            raise KeyError(f"unknown experiment job family {family_id!r}; known: {known}") from exc

    def plan(
        self,
        family_id: str,
        *,
        backend_id: str = "local",
        job_id: str | None = None,
        model_id: str | None = None,
        input_snapshot: str | None = None,
        objective: str | None = None,
        rollback_plan: str | None = None,
        gate_evidence: tuple[str, ...] | None = None,
        artifact_dir: str | None = None,
    ) -> ExperimentJobPlan:
        family = self.require_family(family_id)
        backend = self.require_backend(backend_id)
        blocked: list[str] = []
        if backend.backend_id not in family.allowed_backends:
            blocked.append(f"backend_not_allowed:{backend.backend_id}")
        if not backend.active:
            blocked.append(f"backend_not_active:{backend.backend_id}:{backend.status}")
        field_values = {
            "input_snapshot": input_snapshot,
            "objective": objective,
            "rollback_plan": rollback_plan,
        }
        for field_name in family.required_plan_fields:
            if not str(field_values.get(field_name) or "").strip():
                blocked.append(f"missing_plan_field:{field_name}")
        evidence, evidence_gate_ids, evidence_blockers = _parse_gate_evidence(gate_evidence)
        blocked.extend(evidence_blockers)
        for gate in family.required_gates:
            if gate not in evidence_gate_ids:
                blocked.append(f"missing_gate_evidence:{gate}")
        resolved_job_id = job_id or "_".join(part for part in (family.family_id, model_id or "manual") if part)
        resolved_artifact_dir = artifact_dir or f"data/reports/experiment_jobs/{resolved_job_id}"
        return ExperimentJobPlan(
            job_id=resolved_job_id,
            family=family,
            backend=backend,
            model_id=model_id,
            input_snapshot=input_snapshot,
            objective=objective,
            rollback_plan=rollback_plan,
            gate_evidence=evidence,
            artifact_dir=resolved_artifact_dir,
            blocked_reasons=tuple(blocked),
        )

    def to_report(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "backends": {key: value.to_report() for key, value in sorted(self.backends.items())},
            "families": {key: value.to_report() for key, value in sorted(self.families.items())},
        }


def _parse_artifact(raw: dict[str, Any], path: Path) -> ArtifactContract:
    artifact_id = _require_id(raw.get("id"), field_name="artifact id", path=path)
    kind = _require_id(raw.get("kind"), field_name=f"{artifact_id}.kind", path=path)
    if kind == "table" and not raw.get("table"):
        raise ValueError(f"{path.name}: artifact {artifact_id} table kind requires table")
    if kind in {"file", "report"} and not raw.get("path_glob"):
        raise ValueError(f"{path.name}: artifact {artifact_id} {kind} kind requires path_glob")
    min_rows = raw.get("min_rows")
    if min_rows is not None:
        if isinstance(min_rows, bool) or not isinstance(min_rows, int) or min_rows < 0:
            raise ValueError(f"{path.name}: artifact {artifact_id} min_rows must be a non-negative integer")
    return ArtifactContract(
        artifact_id=artifact_id,
        kind=kind,
        required=bool(raw.get("required", True)),
        path_glob=str(raw["path_glob"]).strip() if raw.get("path_glob") else None,
        table=str(raw["table"]).strip() if raw.get("table") else None,
        min_rows=min_rows,
    )


def _parse_backend(backend_id: str, raw: dict[str, Any], path: Path) -> ExecutionBackend:
    return ExecutionBackend(
        backend_id=_require_id(backend_id, field_name="backend id", path=path),
        status=_require_id(raw.get("status"), field_name=f"{backend_id}.status", path=path),
        execution_mode=_require_id(raw.get("execution_mode"), field_name=f"{backend_id}.execution_mode", path=path),
        artifact_mode=_require_id(raw.get("artifact_mode"), field_name=f"{backend_id}.artifact_mode", path=path),
        notes=_str_tuple(raw.get("notes"), field_name=f"{backend_id}.notes", path=path, allow_empty=True),
    )


def _parse_family(family_id: str, raw: dict[str, Any], path: Path) -> JobFamily:
    artifacts = raw.get("artifact_contracts")
    if not isinstance(artifacts, list) or not artifacts:
        raise ValueError(f"{path.name}: family {family_id} must declare artifact_contracts")
    return JobFamily(
        family_id=_require_id(family_id, field_name="job family id", path=path),
        owner_module=_require_id(raw.get("owner_module"), field_name=f"{family_id}.owner_module", path=path),
        purpose=_require_str(raw.get("purpose"), field_name=f"{family_id}.purpose", path=path),
        allowed_backends=_str_tuple(raw.get("allowed_backends"), field_name=f"{family_id}.allowed_backends", path=path),
        command_family=_require_id(raw.get("command_family"), field_name=f"{family_id}.command_family", path=path),
        required_plan_fields=_str_tuple(
            raw.get("required_plan_fields"),
            field_name=f"{family_id}.required_plan_fields",
            path=path,
        ),
        required_gates=_str_tuple(raw.get("required_gates"), field_name=f"{family_id}.required_gates", path=path),
        artifact_contracts=tuple(_parse_artifact(item, path) for item in artifacts),
    )


def load_experiment_job_contract(path: str | Path | None = None) -> ExperimentJobContract:
    config_path = Path(path) if path is not None else CONFIG_PATH
    raw = _load_yaml(config_path)
    version = raw.get("version")
    if isinstance(version, bool) or not isinstance(version, int) or version <= 0:
        raise ValueError(f"{config_path.name}: version must be a positive integer")
    raw_backends = raw.get("backends")
    raw_families = raw.get("job_families")
    if not isinstance(raw_backends, dict) or not raw_backends:
        raise ValueError(f"{config_path.name}: backends must be a non-empty mapping")
    if not isinstance(raw_families, dict) or not raw_families:
        raise ValueError(f"{config_path.name}: job_families must be a non-empty mapping")
    backends = {
        str(backend_id): _parse_backend(str(backend_id), spec or {}, config_path)
        for backend_id, spec in raw_backends.items()
    }
    families = {
        str(family_id): _parse_family(str(family_id), spec or {}, config_path)
        for family_id, spec in raw_families.items()
    }
    for family in families.values():
        for backend_id in family.allowed_backends:
            if backend_id not in backends:
                raise ValueError(f"{config_path.name}: family {family.family_id} references unknown backend {backend_id}")
    return ExperimentJobContract(version=version, backends=backends, families=families)


DEFAULT_EXPERIMENT_JOB_CONTRACT = load_experiment_job_contract()
