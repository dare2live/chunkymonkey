#!/usr/bin/env bash
# Workflow checkpoint - business pipeline progress tracker.
#
# Outputs:
#   - analysis/workflow_checkpoint.json
#   - analysis/workflow_checkpoint.md
#
# This script is idempotent. It reads artifacts and DuckDB tables in read-only
# mode, then writes only the two analysis/workflow_checkpoint.* files.

set -euo pipefail

REPO_ROOT="${WORKFLOW_CHECKPOINT_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"
cd "$REPO_ROOT"

if [[ "${WORKFLOW_CHECKPOINT_LEGACY_PIPELINE:-0}" != "1" ]]; then
    mkdir -p analysis
    GENERATED_AT="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
    cat > analysis/workflow_checkpoint.json <<EOF
{
  "generated_at": "$GENERATED_AT",
  "schema_version": 2,
  "status": "inactive",
  "active": false,
  "current_step": "inactive",
  "next_step": "inactive",
  "resume_command": "echo inactive",
  "blockers": [],
  "message": "No active workflow pipeline is registered. Use goal.md plus live chunkyctl gates for current work.",
  "archive": {
    "legacy_gcp_checkpoint_md": "analysis/workflow_checkpoint_legacy_gcp_20260604.md",
    "legacy_gcp_checkpoint_json": "analysis/workflow_checkpoint_legacy_gcp_20260604.json"
  }
}
EOF
    cat > analysis/workflow_checkpoint.md <<EOF
# Workflow Checkpoint

- generated_at: \`$GENERATED_AT\`
- status: \`inactive\`
- current_step: \`inactive\`
- next_step: \`inactive\`
- resume_command: \`echo inactive\`

No active multi-step workflow pipeline is registered.

Use \`goal.md\` plus live \`scripts/chunkyctl doctor --fast\` /
\`scripts/chunkyctl worktree --format markdown\` output for current work.

Historical GCP pipeline evidence was archived to:

- \`analysis/workflow_checkpoint_legacy_gcp_20260604.md\`
- \`analysis/workflow_checkpoint_legacy_gcp_20260604.json\`

To inspect the retired legacy checkpoint generator for tests or archaeology,
run with \`WORKFLOW_CHECKPOINT_LEGACY_PIPELINE=1\`. Do not use that mode as a
current recovery command.
EOF
    exit 0
fi

if [[ -z "${WORKFLOW_CHECKPOINT_MODEL_ID:-${MODEL_ID:-}}" ]]; then
    MODEL_ID="$(cat data/reports/stability_retrain/current.pointer 2>/dev/null | tr -d '[:space:]' || true)"
    if [[ -z "$MODEL_ID" ]]; then
        latest_best="$(ls -t data/reports/optuna/*.best.json 2>/dev/null | head -1 || true)"
        if [[ -n "$latest_best" ]]; then
            MODEL_ID="$(basename "$latest_best" .best.json)"
        fi
    fi
else
    MODEL_ID="${WORKFLOW_CHECKPOINT_MODEL_ID:-${MODEL_ID:-}}"
fi
export WORKFLOW_CHECKPOINT_ROOT="$REPO_ROOT"
export WORKFLOW_CHECKPOINT_MODEL_ID="$MODEL_ID"

python3 - <<'PY'
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(os.environ["WORKFLOW_CHECKPOINT_ROOT"]).resolve()
MODEL_ID = os.environ.get("WORKFLOW_CHECKPOINT_MODEL_ID", "").strip()
ANALYSIS_DIR = ROOT / "analysis"
OUT_JSON = ANALYSIS_DIR / "workflow_checkpoint.json"
OUT_MD = ANALYSIS_DIR / "workflow_checkpoint.md"
DB_PATH = ROOT / "data" / "smartmoney.duckdb"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


GENERATED_AT = utc_now()


def rel(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path)


def parse_dt(value: Any) -> datetime | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def model_created_at(model_id: str) -> datetime | None:
    match = re.search(r"(\d{8}T\d{6})$", model_id)
    if not match:
        return None
    try:
        return datetime.strptime(match.group(1), "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


MODEL_CREATED_AT = model_created_at(MODEL_ID)


def read_json(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def json_timestamp(data: dict[str, Any], path: Path) -> datetime | None:
    for key in ("generated_at", "audited_at", "run_at", "built_at", "at", "evaluated_at", "promoted_at"):
        dt = parse_dt(data.get(key))
        if dt:
            return dt
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    except OSError:
        return None


def is_fresh_for_model(data: dict[str, Any], path: Path) -> bool:
    if MODEL_CREATED_AT is None:
        return True
    dt = json_timestamp(data, path)
    return bool(dt and dt >= MODEL_CREATED_AT)


def model_matches(data: dict[str, Any]) -> bool:
    if not MODEL_ID:
        return False
    candidates = [
        data.get("model_id"),
        data.get("challenger_model_id"),
        data.get("champion_model_id"),
        (data.get("args") or {}).get("lambdamart_model_id") if isinstance(data.get("args"), dict) else None,
    ]
    return MODEL_ID in {str(item) for item in candidates if item is not None}


def json_has_nonempty_kpi(data: dict[str, Any]) -> bool:
    kpi = data.get("kpi")
    return isinstance(kpi, dict) and any(value is not None for value in kpi.values())


class Evidence:
    def __init__(self) -> None:
        self.expected: list[str] = []
        self.found: list[str] = []
        self.satisfied = False

    def expect(self, description: str) -> None:
        self.expected.append(description)

    def add(self, description: str, *, satisfies: bool = True) -> None:
        self.found.append(description)
        if satisfies:
            self.satisfied = True


def check_file(ev: Evidence, path: str, *, label: str | None = None, nonempty: bool = False) -> Path | None:
    p = ROOT / path
    desc = label or f"file:{path}"
    ev.expect(desc)
    if not p.exists():
        return None
    if nonempty and p.stat().st_size <= 0:
        ev.add(f"{desc} (empty)", satisfies=False)
        return None
    ev.add(desc)
    return p


def check_model_json(
    ev: Evidence,
    path: str,
    *,
    label: str | None = None,
    require_model: bool = True,
    require_fresh: bool = False,
    predicate: Any = None,
) -> dict[str, Any] | None:
    p = ROOT / path
    desc = label or f"json:{path}"
    ev.expect(desc)
    if not p.exists():
        return None
    data = read_json(p)
    if data is None:
        ev.add(f"{desc} (invalid json)", satisfies=False)
        return None
    reasons: list[str] = []
    ok = True
    if require_model and not model_matches(data):
        ok = False
        reasons.append("model_id mismatch")
    if require_fresh and not is_fresh_for_model(data, p):
        ok = False
        reasons.append("stale before model timestamp")
    if predicate is not None and not predicate(data):
        ok = False
        reasons.append("predicate not satisfied")
    suffix = "" if ok else f" ({', '.join(reasons)})"
    ev.add(f"{desc}{suffix}", satisfies=ok)
    return data if ok else None


DUCKDB_ERROR = ""
CONN = None
if DB_PATH.exists():
    try:
        import duckdb  # type: ignore

        CONN = duckdb.connect(str(DB_PATH), read_only=True)
    except Exception as exc:  # pragma: no cover - depends on local environment
        DUCKDB_ERROR = str(exc)


def table_exists(table: str) -> bool:
    if CONN is None:
        return False
    try:
        row = CONN.execute(
            "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='main' AND table_name=?",
            [table],
        ).fetchone()
        return bool(row and row[0])
    except Exception:
        return False


def db_count(ev: Evidence, table: str, where_sql: str, params: list[Any], *, label: str) -> int:
    ev.expect(f"db:{label}")
    if CONN is None or not table_exists(table):
        return 0
    try:
        row = CONN.execute(f"SELECT COUNT(*) FROM {table} WHERE {where_sql}", params).fetchone()
        count = int(row[0] or 0) if row else 0
    except Exception:
        return 0
    if count > 0:
        ev.add(f"db:{label} rows={count}")
    return count


def step_pull_predictions() -> Evidence:
    ev = Evidence()
    if MODEL_ID:
        check_file(ev, f"data/smartmoney_post_{MODEL_ID}.duckdb.bak", nonempty=True)
        check_file(ev, f"data/smartmoney_post_{MODEL_ID}.duckdb", nonempty=True)
        check_file(ev, f"data/phase5_exports/{MODEL_ID}/manifest.json", nonempty=True)
        db_count(
            ev,
            "mart_p0b_oos_predictions",
            "model_id=?",
            [MODEL_ID],
            label="mart_p0b_oos_predictions model_id",
        )
        db_count(
            ev,
            "mart_p0b_lambdamart_v6_predictions",
            "model_id=?",
            [MODEL_ID],
            label="mart_p0b_lambdamart_v6_predictions model_id",
        )
    return ev


def step_pre_sim_audit() -> Evidence:
    ev = Evidence()
    if MODEL_ID:
        check_model_json(ev, f"analysis/pre_sim_audit_{MODEL_ID}.json", require_fresh=False)
        check_model_json(ev, f"data/reports/pre_sim_audit_{MODEL_ID}.json", require_fresh=False)
        check_model_json(ev, f"data/reports/pit_audit_{MODEL_ID}.json", require_fresh=False)
        check_model_json(
            ev,
            "data/reports/pit_audit.json",
            label="json:data/reports/pit_audit.json fresh PASS",
            require_model=False,
            require_fresh=True,
            predicate=lambda data: data.get("n_total") and data.get("n_pass") == data.get("n_total"),
        )
        db_count(
            ev,
            "mart_champion_candidate_evaluation",
            "model_id=? AND lower(coalesce(pit_status, '')) IN ('pass', 'passed')",
            [MODEL_ID],
            label="mart_champion_candidate_evaluation PIT pass",
        )
    return ev


def step_paper_sim_execution() -> Evidence:
    ev = Evidence()
    if MODEL_ID:
        check_model_json(
            ev,
            f"data/reports/msaf_ensemble_phase5_{MODEL_ID}.json",
            predicate=lambda data: bool(data.get("results")) or int(data.get("n_signal_dates") or 0) > 0,
        )
        check_model_json(
            ev,
            f"data/reports/paper_sim_{MODEL_ID}.json",
            predicate=lambda data: bool(data.get("results")) or int(data.get("n_signal_dates") or 0) > 0,
        )
        check_model_json(
            ev,
            f"analysis/paper_sim_{MODEL_ID}.json",
            predicate=lambda data: bool(data.get("results")) or int(data.get("n_signal_dates") or 0) > 0,
        )
        db_count(
            ev,
            "mart_paper_sim_lambdamart_v6_kpi_compare",
            "model_id=?",
            [MODEL_ID],
            label="mart_paper_sim_lambdamart_v6_kpi_compare model_id",
        )
        db_count(
            ev,
            "mart_paper_sim_nav",
            "sim_run_id ILIKE ?",
            [f"%{MODEL_ID}%"],
            label="mart_paper_sim_nav sim_run_id contains model_id",
        )
    return ev


def step_kpi_ingestion() -> Evidence:
    ev = Evidence()
    if MODEL_ID:
        check_model_json(
            ev,
            f"data/reports/msaf_ensemble_phase5_{MODEL_ID}.json",
            label=f"json:data/reports/msaf_ensemble_phase5_{MODEL_ID}.json kpi",
            predicate=json_has_nonempty_kpi,
        )
        check_model_json(ev, f"data/reports/kpi_{MODEL_ID}.json", predicate=json_has_nonempty_kpi)
        check_model_json(ev, f"analysis/kpi_{MODEL_ID}.json", predicate=json_has_nonempty_kpi)
        if table_exists("mart_paper_sim_kpi") and table_exists("mart_paper_sim_lambdamart_v6_kpi_compare"):
            ev.expect("db:mart_paper_sim_kpi joined to model compare")
            try:
                row = CONN.execute(
                    """
                    SELECT COUNT(*)
                    FROM mart_paper_sim_kpi k
                    JOIN mart_paper_sim_lambdamart_v6_kpi_compare c
                      ON k.sim_run_id = c.sim_run_id
                    WHERE c.model_id = ?
                    """,
                    [MODEL_ID],
                ).fetchone()
                count = int(row[0] or 0) if row else 0
            except Exception:
                count = 0
            if count > 0:
                ev.add(f"db:mart_paper_sim_kpi joined to model compare rows={count}")
        else:
            ev.expect("db:mart_paper_sim_kpi joined to model compare")
    return ev


def step_kpi_comparison() -> Evidence:
    ev = Evidence()
    if MODEL_ID:
        check_model_json(ev, f"data/reports/kpi_compare_{MODEL_ID}.json")
        check_model_json(ev, f"analysis/kpi_compare_{MODEL_ID}.json")
        db_count(
            ev,
            "mart_paper_sim_lambdamart_v6_kpi_compare",
            "model_id=?",
            [MODEL_ID],
            label="mart_paper_sim_lambdamart_v6_kpi_compare model_id",
        )
    return ev


def step_pareto_gatekeeper() -> Evidence:
    ev = Evidence()
    if MODEL_ID:
        check_model_json(ev, f"data/reports/phase4_gate_{MODEL_ID}.json")
        check_model_json(ev, f"analysis/pareto_verdict_{MODEL_ID}.json")
        check_model_json(ev, f"data/reports/pareto_verdict_{MODEL_ID}.json")
        check_model_json(
            ev,
            "data/reports/phase4_gate_result.json",
            label="json:data/reports/phase4_gate_result.json matching model",
        )
        db_count(
            ev,
            "mart_tdx_keep_promotion_gate",
            "challenger_model_id=?",
            [MODEL_ID],
            label="mart_tdx_keep_promotion_gate challenger_model_id",
        )
    return ev


def step_decision() -> Evidence:
    ev = Evidence()
    if MODEL_ID:
        check_model_json(ev, f"analysis/decision_{MODEL_ID}.json")
        check_model_json(ev, f"data/reports/decision_{MODEL_ID}.json")
        check_model_json(ev, f"data/reports/promote_{MODEL_ID}.json")
        check_model_json(ev, f"data/reports/ensemble_decision_{MODEL_ID}.json")
        check_model_json(ev, f"data/reports/retrain_decision_{MODEL_ID}.json")
        db_count(
            ev,
            "mart_champion_model",
            "model_id=?",
            [MODEL_ID],
            label="mart_champion_model model_id",
        )
        db_count(
            ev,
            "mart_champion_candidate_evaluation",
            "model_id=? AND lower(coalesce(status, '')) IN ('pass', 'passed', 'promote', 'promoted', 'blocked', 'retrain', 'ensemble')",
            [MODEL_ID],
            label="mart_champion_candidate_evaluation final status",
        )
        db_count(
            ev,
            "mart_tdx_keep_promotion_gate",
            "challenger_model_id=? AND coalesce(decision, promotion_status, '') <> ''",
            [MODEL_ID],
            label="mart_tdx_keep_promotion_gate final decision",
        )
    return ev


STEP_SPECS = [
    ("pull_predictions", "verify local prediction artifacts", step_pull_predictions),
    ("pre_sim_audit", "pre-sim audit", step_pre_sim_audit),
    ("paper_sim_execution", "paper_sim execution", step_paper_sim_execution),
    ("kpi_ingestion", "KPI ingestion", step_kpi_ingestion),
    ("kpi_comparison", "KPI comparison", step_kpi_comparison),
    ("pareto_verdict_gatekeeper", "Pareto verdict gatekeeper", step_pareto_gatekeeper),
    ("decision", "decision promote/reject/retrain", step_decision),
]

RESUME_COMMANDS = {
    "pull_predictions": (
        f'find data/phase5_exports -maxdepth 3 -name "manifest.json" -print; '
        f'echo "Import existing local artifacts with backend/scripts/import_phase5_remote_predictions.py"'
    ),
    "pre_sim_audit": "PYTHONPATH=backend python backend/scripts/audit_pit_coverage.py --output-json data/reports/pit_audit.json",
    "paper_sim_execution": (
        f'PYTHONPATH=backend python backend/scripts/run_msaf_ensemble_paper_sim.py --compute-kpi --horizon 20d '
        f'--lambdamart-model-id "{MODEL_ID}" --output-json "data/reports/msaf_ensemble_phase5_{MODEL_ID}.json"'
    ),
    "kpi_ingestion": (
        f'PYTHONPATH=backend python backend/scripts/run_msaf_ensemble_paper_sim.py --compute-kpi --horizon 20d '
        f'--lambdamart-model-id "{MODEL_ID}" --output-json "data/reports/msaf_ensemble_phase5_{MODEL_ID}.json"'
    ),
    "kpi_comparison": f'PYTHONPATH=backend python backend/scripts/run_paper_sim_lambdamart_v6_compare.py --lambdamart-model-id "{MODEL_ID}"',
    "pareto_verdict_gatekeeper": (
        f'PYTHONPATH=backend python backend/scripts/run_phase4_gate_on_msaf.py --model-id "{MODEL_ID}" '
        f'--challenger-id "msaf_phase5_{MODEL_ID}" --output-json "data/reports/phase4_gate_{MODEL_ID}.json"'
    ),
    "decision": (
        f'PYTHONPATH=backend python backend/scripts/record_phase5_decision.py --model-id "{MODEL_ID}" '
        f'--phase4-json "data/reports/phase4_gate_{MODEL_ID}.json"'
    ),
}

steps: list[dict[str, Any]] = []
blockers: list[str] = []

if not MODEL_ID:
    blockers.append("model_id missing; set WORKFLOW_CHECKPOINT_MODEL_ID or create data/reports/stability_retrain/current.pointer")
if DUCKDB_ERROR:
    blockers.append(f"DuckDB read-only checks skipped: {DUCKDB_ERROR}")

for idx, (key, name, checker) in enumerate(STEP_SPECS, start=1):
    ev = checker()
    status = "done" if ev.satisfied else "missing"
    steps.append(
        {
            "step": idx,
            "key": key,
            "name": name,
            "status": status,
            "evidence": ev.expected,
            "evidence_found": ev.found,
        }
    )

pull_step = steps[0]
pull_has_weak_sentinel = any("monitor_done_" in item for item in pull_step["evidence_found"])
pull_has_strong = pull_step["status"] == "done"
if pull_has_weak_sentinel and not pull_has_strong:
    blockers.append(
        "pull sentinel exists without strong local evidence; verify the provider artifact manifest or local pull evidence and remove stale sentinel only after confirming it is wrong"
    )

first_missing = next((step for step in steps if step["status"] != "done"), None)
if first_missing is None:
    current_step: int | str = "all_done"
    next_step: int | str = "all_done"
    resume_command = "echo all_done"
else:
    current_step = int(first_missing["step"])
    next_step = int(first_missing["step"])
    resume_command = RESUME_COMMANDS.get(str(first_missing["key"]), "")
    blockers.append(f"missing evidence for step {first_missing['step']}: {first_missing['name']}")

payload = {
    "generated_at": GENERATED_AT,
    "model_id": MODEL_ID,
    "steps": steps,
    "current_step": current_step,
    "next_step": next_step,
    "resume_command": resume_command,
    "blockers": blockers,
    "last_verified": GENERATED_AT,
}

ANALYSIS_DIR.mkdir(parents=True, exist_ok=True)
OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def md_escape(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


lines = [
    "# Workflow Checkpoint",
    "",
    "Business-level pipeline tracker. Session-level state remains in SESSION_HANDOFF.md.",
    "",
    f"- generated_at: `{GENERATED_AT}`",
    f"- model_id: `{MODEL_ID or 'UNKNOWN'}`",
    f"- current_step: `{current_step}`",
    f"- next_step: `{next_step}`",
    f"- resume_command: `{resume_command}`",
    "",
    "## Steps",
    "",
    "| Step | Name | Status | Evidence Found |",
    "|---:|---|---|---|",
]
for step in steps:
    found = "<br>".join(md_escape(item) for item in step["evidence_found"]) or "-"
    lines.append(
        f"| {step['step']} | {md_escape(step['name'])} | {step['status']} | {found} |"
    )

lines.extend(["", "## Blockers", ""])
if blockers:
    lines.extend(f"- {item}" for item in blockers)
else:
    lines.append("- none")

lines.extend(
    [
        "",
        "## Expected Evidence",
        "",
    ]
)
for step in steps:
    lines.append(f"### Step {step['step']}: {step['name']}")
    lines.extend(f"- {item}" for item in step["evidence"])
    lines.append("")

OUT_MD.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")

if CONN is not None:
    CONN.close()

print(f"[workflow_checkpoint] wrote {rel(OUT_JSON)} and {rel(OUT_MD)}")
print(f"[workflow_checkpoint] model_id={MODEL_ID or 'UNKNOWN'} next_step={next_step}")
print(f"[workflow_checkpoint] resume_command={resume_command}")
PY
