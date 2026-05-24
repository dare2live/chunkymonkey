from __future__ import annotations

import csv
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
ANALYSIS_DIR = ROOT / "analysis"
AGGREGATE_AUDIT = ANALYSIS_DIR / "formula_local_optuna_aggregate_audit.json"
CHECKPOINT_JSON = ANALYSIS_DIR / "workflow_checkpoint.json"
STOCK_FORMULA_BEST = ANALYSIS_DIR / "stock_formula_best.csv"
OUT_JSON = ANALYSIS_DIR / "operational_delivery_readiness.json"
OUT_MD = ANALYSIS_DIR / "operational_delivery_readiness.md"


def _run(name: str, command: list[str]) -> dict[str, Any]:
    proc = subprocess.run(command, cwd=ROOT, text=True, capture_output=True)
    return {
        "name": name,
        "command": command,
        "returncode": proc.returncode,
        "passed": proc.returncode == 0,
        "stdout": proc.stdout.strip(),
        "stderr": proc.stderr.strip(),
    }


def _csv_count(path: Path) -> int:
    with path.open(newline="", encoding="utf-8") as f:
        return sum(1 for _ in csv.DictReader(f))


def _json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    script_files = sorted(str(path.relative_to(ROOT)) for path in (ROOT / "scripts").glob("*.py"))
    gates = [
        _run(
            "py_compile",
            [
                sys.executable,
                "-m",
                "py_compile",
                "main.py",
                "compute.py",
                "execution_model.py",
                "formula_engine.py",
                *script_files,
            ],
        ),
        _run("execution_model_smoke", [sys.executable, "scripts/execution_model_smoke.py"]),
        _run("unified_data_smoke", [sys.executable, "scripts/unified_data_smoke.py"]),
        _run("strategy_rebuild_audit", [sys.executable, "scripts/strategy_rebuild_audit.py"]),
        _run(
            "formula_local_optuna_aggregate_audit",
            [sys.executable, "scripts/formula_local_optuna_aggregate_audit.py"],
        ),
        _run("git_diff_check", ["git", "diff", "--check"]),
        _run("workflow_checkpoint_brief", [sys.executable, "scripts/workflow_checkpoint.py", "--brief"]),
    ]
    aggregate = _json(AGGREGATE_AUDIT)
    checkpoint = _json(CHECKPOINT_JSON)
    batch = checkpoint.get("batch", {})
    source_rows = (
        checkpoint.get("state_stores", {})
        .get("research_cache", {})
        .get("manifest", {})
        .get("source_rows")
    )
    if isinstance(source_rows, str):
        source_rows = json.loads(source_rows)
    source_rows = source_rows or {}
    stock_formula_best_rows = _csv_count(STOCK_FORMULA_BEST)
    checks = [
        {
            "name": "final_gates_passed",
            "passed": all(gate["passed"] for gate in gates),
            "detail": ",".join(gate["name"] for gate in gates if not gate["passed"]),
        },
        {
            "name": "full_market_covered",
            "passed": batch.get("covered_stocks") == batch.get("market_total") == 5201,
            "detail": f"covered={batch.get('covered_stocks')} market_total={batch.get('market_total')}",
        },
        {
            "name": "aggregate_audit_passed",
            "passed": bool(aggregate.get("passed")),
            "detail": f"candidates={aggregate.get('candidate_count')} replacements={aggregate.get('replacement_count')}",
        },
        {
            "name": "state_stores_clean",
            "passed": checkpoint.get("consistency", {}).get("ready") is True
            and aggregate.get("incremental_eval_rows") == aggregate.get("drift_trigger_rows"),
            "detail": (
                f"consistency={checkpoint.get('consistency', {}).get('ready')} "
                f"incremental={aggregate.get('incremental_eval_rows')} "
                f"drift={aggregate.get('drift_trigger_rows')}"
            ),
        },
        {
            "name": "production_table_not_auto_overwritten",
            "passed": stock_formula_best_rows == source_rows.get("production"),
            "detail": f"stock_formula_best_rows={stock_formula_best_rows} source_rows={source_rows}",
        },
        {
            "name": "dry_run_replacements_separate",
            "passed": aggregate.get("replacement_count") == aggregate.get("candidate_count") == 1146,
            "detail": (
                f"replacement_count={aggregate.get('replacement_count')} "
                f"candidate_count={aggregate.get('candidate_count')}"
            ),
        },
        {
            "name": "no_active_worker_or_market_lock",
            "passed": not checkpoint.get("active_workers")
            and not checkpoint.get("market_db_locks", {}).get("holders"),
            "detail": (
                f"active_workers={len(checkpoint.get('active_workers') or [])} "
                f"market_locks={len(checkpoint.get('market_db_locks', {}).get('holders') or [])}"
            ),
        },
    ]
    ready = all(check["passed"] for check in checks)
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "operational_ready": ready,
        "checks": checks,
        "gates": gates,
        "summary": {
            "covered_stocks": batch.get("covered_stocks"),
            "market_total": batch.get("market_total"),
            "adoption_rows": aggregate.get("adoption_rows"),
            "candidate_count": aggregate.get("candidate_count"),
            "replacement_count": aggregate.get("replacement_count"),
            "research_cache_rows": aggregate.get("research_cache_rows"),
            "incremental_eval_rows": aggregate.get("incremental_eval_rows"),
            "drift_trigger_rows": aggregate.get("drift_trigger_rows"),
            "data_latest_date": aggregate.get("data_latest_date"),
            "production_stock_formula_best_rows": stock_formula_best_rows,
        },
        "production_merge_control": (
            "Ready for controlled production merge review. This audit does not write "
            "analysis/stock_formula_best.csv; human approval is still required before replacement."
        ),
    }
    OUT_JSON.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    lines = [
        "# Operational Delivery Readiness",
        "",
        f"- operational_ready: `{ready}`",
        f"- generated_at: `{report['generated_at']}`",
        f"- covered_stocks: `{report['summary']['covered_stocks']}` / `{report['summary']['market_total']}`",
        f"- candidates: `{report['summary']['candidate_count']}`",
        f"- replacements: `{report['summary']['replacement_count']}`",
        f"- data_latest_date: `{report['summary']['data_latest_date']}`",
        "",
        "## Checks",
        "",
    ]
    lines.extend(
        f"- [{'x' if check['passed'] else ' '}] `{check['name']}`: {check['detail']}"
        for check in checks
    )
    lines.extend(["", "## Gates", ""])
    lines.extend(
        f"- [{'x' if gate['passed'] else ' '}] `{gate['name']}`: returncode `{gate['returncode']}`"
        for gate in gates
    )
    lines.extend(
        [
            "",
            "## Production Merge Control",
            "",
            report["production_merge_control"],
        ]
    )
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"operational_delivery_audit: operational_ready={ready}")
    if not ready:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
