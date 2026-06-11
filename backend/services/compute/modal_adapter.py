"""Modal execution-backend adapter for the experiment-job control plane.

This adapter is the bridge between the provider-agnostic experiment-job contract
(``services.experiment_jobs``) and the Modal serverless backend. It is the only
place that is allowed to talk to the Modal SDK, and it does so *after* the same
plan gate that the local backend uses has passed.

Design rules (mirror ``services/experiment_jobs.py``):
- The adapter does NOT reimplement the readiness gate. It calls
  ``ExperimentJobContract.plan(...)`` so input_snapshot / objective /
  rollback_plan + required gate evidence are validated by one code path.
- The adapter never submits work for a blocked plan. A blocked plan raises
  ``ModalSubmissionBlocked`` listing the same ``blocked_reasons`` the local
  control plane produces.
- The Modal SDK is imported lazily so this module (and the manifest contract)
  can be exercised in CI / unit tests without Modal credentials. Tests inject a
  fake SDK via the ``modal_sdk`` argument.
- Every submission produces an artifact manifest with the same shape as
  ``ExperimentJobPlan.to_report()`` plus Modal-specific provenance
  (input-snapshot hash, function reference, provider artifact path, budget).
  The manifest is the durable contract the controller and downstream auditors
  read; the live Modal call object is incidental.

Budget and the artifact contract live in ``config/experiment_jobs.yaml`` under
the ``modal`` backend, not hardcoded here.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
from typing import Any, Callable

from services.experiment_jobs import (
    DEFAULT_EXPERIMENT_JOB_CONTRACT,
    ExperimentJobContract,
    ExperimentJobPlan,
)

BACKEND_ID = "modal"

# Modal artifact provenance shape version. Bump when manifest fields change so
# downstream readers can detect schema drift. Math/contract constant, not a
# tunable threshold -> hardcode with comment is allowed per CLAUDE.md §3.
MANIFEST_SCHEMA_VERSION = 1


class ModalSubmissionBlocked(RuntimeError):
    """Raised when a Modal job is submitted with a blocked / incomplete plan.

    Carries the plan's ``blocked_reasons`` so the caller (and the controller)
    sees exactly the same gate failures the local backend would report.
    """

    def __init__(self, plan: ExperimentJobPlan) -> None:
        self.plan = plan
        self.blocked_reasons: tuple[str, ...] = plan.blocked_reasons
        reasons = ", ".join(plan.blocked_reasons) or "unknown"
        super().__init__(f"modal submission blocked for {plan.job_id!r}: {reasons}")


class ModalSdkUnavailable(RuntimeError):
    """Raised when the real Modal SDK is needed but not importable."""


def _input_snapshot_hash(input_snapshot: str) -> str:
    """Deterministic content-address of the input snapshot identifier.

    The snapshot string (e.g. ``smartmoney.duckdb@2026-06-05``) is the PIT
    anchor for the run. Hashing it gives the manifest a stable key the
    controller can compare across resubmissions without storing the raw path
    twice. sha256 truncated to 16 hex chars -> collision-safe for our volume.
    """
    digest = hashlib.sha256(input_snapshot.encode("utf-8")).hexdigest()
    return f"sha256:{digest[:16]}"


def _load_modal_sdk(modal_sdk: Any | None) -> Any:
    """Return the Modal SDK module, importing lazily unless one was injected."""
    if modal_sdk is not None:
        return modal_sdk
    try:
        import modal  # noqa: PLC0415 - lazy import keeps unit tests SDK-free
    except ImportError as exc:  # pragma: no cover - exercised via injected fake
        raise ModalSdkUnavailable(
            "modal SDK not installed; add 'modal' to backend/requirements.txt"
        ) from exc
    return modal


@dataclass(frozen=True)
class ModalJobHandle:
    """Handle returned by :func:`submit_job`.

    ``artifact_manifest`` is the durable contract. ``call`` is the live Modal
    function-call object (``modal.FunctionCall`` in production, a fake in tests)
    and may be ``None`` in dry-run mode.
    """

    job_id: str
    backend_id: str
    app_name: str
    fn_ref: str
    artifact_path: str
    artifact_manifest: dict[str, Any]
    call: Any | None = None
    blocked_reasons: tuple[str, ...] = field(default_factory=tuple)

    @property
    def submitted(self) -> bool:
        return not self.blocked_reasons and self.call is not None

    def to_report(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "backend_id": self.backend_id,
            "app_name": self.app_name,
            "fn_ref": self.fn_ref,
            "artifact_path": self.artifact_path,
            "submitted": self.submitted,
            "blocked_reasons": list(self.blocked_reasons),
            "artifact_manifest": self.artifact_manifest,
        }


def build_artifact_manifest(
    plan: ExperimentJobPlan,
    *,
    fn_ref: str,
    app_name: str,
    artifact_path: str,
    budget_monthly_usd: float | None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Build the provider artifact manifest for a (validated) Modal plan.

    Shape = ``ExperimentJobPlan.to_report()`` core fields + Modal provenance.
    Pure function: no SDK, no IO -> directly unit-testable.
    """
    created_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()
    plan_report = plan.to_report()
    return {
        "manifest_schema_version": MANIFEST_SCHEMA_VERSION,
        "backend_id": BACKEND_ID,
        "job_id": plan.job_id,
        "family_id": plan.family.family_id,
        "command_family": plan.family.command_family,
        "model_id": plan.model_id,
        "input_snapshot": plan.input_snapshot,
        "input_snapshot_hash": _input_snapshot_hash(str(plan.input_snapshot)),
        "objective": plan.objective,
        "rollback_plan": plan.rollback_plan,
        "gate_evidence": list(plan.gate_evidence),
        "required_gates": list(plan.family.required_gates),
        "artifact_contracts": plan_report["family"]["artifact_contracts"],
        "artifact_dir": plan.artifact_dir,
        "provider": {
            "name": BACKEND_ID,
            "app_name": app_name,
            "fn_ref": fn_ref,
            "artifact_path": artifact_path,
            "budget_monthly_usd": budget_monthly_usd,
        },
        "ready_to_run": plan.ready_to_run,
        "blocked_reasons": list(plan.blocked_reasons),
        "created_at": created_at,
    }


def _resolve_budget(contract: ExperimentJobContract) -> float | None:
    """Read the modal backend's declared monthly budget from the contract notes.

    Budget is declared in YAML (config/experiment_jobs.yaml modal.notes) so it
    is not hardcoded here. Notes carry a ``budget_monthly_usd=<n>`` token.
    """
    backend = contract.backends.get(BACKEND_ID)
    if backend is None:
        return None
    for note in backend.notes:
        token = str(note).strip()
        if token.startswith("budget_monthly_usd="):
            _, _, value = token.partition("=")
            try:
                return float(value.strip())
            except ValueError:
                return None
    return None


def submit_job(
    family: str,
    *,
    fn_ref: str,
    input_snapshot: str | None,
    objective: str | None,
    rollback_plan: str | None,
    gate_evidence: tuple[str, ...] | None = None,
    job_id: str | None = None,
    model_id: str | None = None,
    app_name: str | None = None,
    artifact_path: str | None = None,
    contract: ExperimentJobContract | None = None,
    modal_sdk: Any | None = None,
    dry_run: bool = False,
    now: datetime | None = None,
    fn_kwargs: dict[str, Any] | None = None,
) -> ModalJobHandle:
    """Validate + submit an experiment job to the Modal backend.

    Flow (single gate, reused from the local control plane):
      1. ``contract.plan(family, backend_id='modal', ...)`` validates
         input_snapshot / objective / rollback_plan + required gate evidence
         and that ``modal`` is an allowed + active backend for the family.
      2. If the plan is blocked -> raise ``ModalSubmissionBlocked`` (no SDK call).
      3. Build the artifact manifest (pure).
      4. ``dry_run`` -> return handle with manifest, no SDK call.
      5. Otherwise look up the Modal app + function via the SDK and ``spawn`` it.

    The Modal SDK is only touched in step 5, so steps 1-4 are fully testable
    without credentials. Tests inject ``modal_sdk`` (a fake) to assert step 5
    spawns the right function with the manifest, without hitting Modal.

    Returns a :class:`ModalJobHandle` whose ``artifact_manifest`` is the durable
    contract. Raises ``ModalSubmissionBlocked`` on a blocked plan and
    ``ModalSdkUnavailable`` when a live submit is requested but the SDK is gone.
    """
    contract = contract or DEFAULT_EXPERIMENT_JOB_CONTRACT
    resolved_fn_ref = str(fn_ref or "").strip()
    if not resolved_fn_ref:
        raise ValueError("fn_ref must be a non-empty 'app::function' reference")
    resolved_app_name, function_name = _split_fn_ref(resolved_fn_ref, app_name=app_name)

    plan = contract.plan(
        family,
        backend_id=BACKEND_ID,
        job_id=job_id,
        model_id=model_id,
        input_snapshot=input_snapshot,
        objective=objective,
        rollback_plan=rollback_plan,
        gate_evidence=gate_evidence,
    )
    if plan.blocked_reasons:
        raise ModalSubmissionBlocked(plan)

    resolved_artifact_path = (
        str(artifact_path).strip()
        if artifact_path
        else f"modal://{resolved_app_name}/{plan.job_id}"
    )
    budget = _resolve_budget(contract)
    manifest = build_artifact_manifest(
        plan,
        fn_ref=resolved_fn_ref,
        app_name=resolved_app_name,
        artifact_path=resolved_artifact_path,
        budget_monthly_usd=budget,
        now=now,
    )

    if dry_run:
        return ModalJobHandle(
            job_id=plan.job_id,
            backend_id=BACKEND_ID,
            app_name=resolved_app_name,
            fn_ref=resolved_fn_ref,
            artifact_path=resolved_artifact_path,
            artifact_manifest=manifest,
            call=None,
        )

    sdk = _load_modal_sdk(modal_sdk)
    function = _lookup_function(sdk, resolved_app_name, function_name)
    call = _spawn(function, manifest=manifest, fn_kwargs=fn_kwargs or {})
    return ModalJobHandle(
        job_id=plan.job_id,
        backend_id=BACKEND_ID,
        app_name=resolved_app_name,
        fn_ref=resolved_fn_ref,
        artifact_path=resolved_artifact_path,
        artifact_manifest=manifest,
        call=call,
    )


def _split_fn_ref(fn_ref: str, *, app_name: str | None) -> tuple[str, str]:
    """Parse a ``"app::function"`` reference into (app_name, function_name)."""
    if "::" in fn_ref:
        app_part, _, function_part = fn_ref.partition("::")
        app_part = app_part.strip()
        function_part = function_part.strip()
    else:
        app_part = str(app_name or "").strip()
        function_part = fn_ref.strip()
    function_part = function_part or ""
    if not function_part:
        raise ValueError(f"fn_ref missing function name: {fn_ref!r}")
    if not app_part:
        raise ValueError(
            f"fn_ref {fn_ref!r} has no app; pass 'app::function' or app_name="
        )
    return app_part, function_part


def _lookup_function(sdk: Any, app_name: str, function_name: str) -> Any:
    """Resolve a deployed Modal Function via the SDK (real or injected fake)."""
    lookup: Callable[..., Any] | None = getattr(getattr(sdk, "Function", None), "lookup", None)
    if lookup is None:
        raise ModalSdkUnavailable("modal SDK has no Function.lookup; check SDK version")
    return lookup(app_name, function_name)


def _spawn(function: Any, *, manifest: dict[str, Any], fn_kwargs: dict[str, Any]) -> Any:
    """Spawn the Modal function asynchronously, passing the manifest through."""
    spawn = getattr(function, "spawn", None)
    if spawn is None:
        raise ModalSdkUnavailable("resolved modal function has no spawn(); check SDK version")
    return spawn(manifest=manifest, **fn_kwargs)
