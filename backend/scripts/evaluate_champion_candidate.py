#!/usr/bin/env python3
"""Automate PIT audit + evidence bundle evaluation for champion candidates."""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.audit_registry_feature_pit import audit_registry_feature_pit  # noqa: E402
from scripts.build_challenger_evidence_bundle import build_evidence_bundle  # noqa: E402
from services.db import get_conn  # noqa: E402
from services.pipeline_manifest import git_commit_sha, record_pipeline_run, utc_now_iso  # noqa: E402
from services.schema_versions import record_actual_version  # noqa: E402


REPO = Path(__file__).resolve().parent.parent.parent

DDL = """
CREATE TABLE IF NOT EXISTS mart_champion_candidate_evaluation (
    evaluation_run_id TEXT PRIMARY KEY,
    model_id TEXT NOT NULL,
    status TEXT NOT NULL,
    pit_audit_run_id TEXT,
    pit_status TEXT,
    pit_violation_rows BIGINT,
    evidence_run_id TEXT,
    evidence_status TEXT,
    gate_status TEXT,
    failed_steps_json TEXT,
    config_json TEXT,
    started_at TEXT NOT NULL,
    ended_at TEXT NOT NULL,
    duration_s DOUBLE
);
CREATE INDEX IF NOT EXISTS idx_champion_candidate_eval_model
    ON mart_champion_candidate_evaluation(model_id, started_at DESC);
"""


def ensure_tables(conn: Any) -> None:
    conn.executescript(DDL)


def candidate_evaluation_status(*, pit_status: str, evidence_status: str, gate_status: str | None) -> str:
    if pit_status != "passed" or evidence_status != "success":
        return "failed"
    gate = str(gate_status or "").upper()
    if gate == "PASS":
        return "passed"
    if gate == "WAIT":
        return "waiting"
    return "failed"


def _persist_candidate_evaluation(
    conn: Any,
    *,
    evaluation_run_id: str,
    model_id: str,
    status: str,
    pit_audit_run_id: str,
    pit_result: dict[str, Any],
    evidence_result: dict[str, Any],
    failed_steps: list[str],
    gate_status: str | None,
    config: dict[str, Any],
    started_at: str,
    ended_at: str,
    duration_s: float,
) -> None:
    ensure_tables(conn)
    pit_status = str(pit_result.get("status") or "unknown")
    evidence_status = str(evidence_result.get("status") or "unknown")
    conn.execute(
        """
        INSERT OR REPLACE INTO mart_champion_candidate_evaluation
        (evaluation_run_id, model_id, status, pit_audit_run_id, pit_status,
         pit_violation_rows, evidence_run_id, evidence_status, gate_status,
         failed_steps_json, config_json, started_at, ended_at, duration_s)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            evaluation_run_id,
            model_id,
            status,
            pit_audit_run_id,
            pit_status,
            int(pit_result.get("violation_rows") or 0),
            evidence_result.get("evidence_run_id"),
            evidence_status,
            gate_status,
            json.dumps(failed_steps, ensure_ascii=False),
            json.dumps(config, ensure_ascii=False, sort_keys=True),
            started_at,
            ended_at,
            duration_s,
        ),
    )
    record_actual_version(conn, "mart_champion_candidate_evaluation")
    record_pipeline_run(
        conn,
        run_id=evaluation_run_id,
        pipeline_name="evaluate_champion_candidate",
        status=status,
        started_at=started_at,
        ended_at=ended_at,
        duration_s=duration_s,
        commit_sha=git_commit_sha(REPO),
        input_tables=["mart_multidim_model", config["feature_table"]],
        output_tables=[
            "mart_feature_pit_audit",
            "mart_challenger_evidence_bundle",
            "mart_champion_candidate_evaluation",
        ],
        model_id=model_id,
        gate_result=gate_status,
        blockers=failed_steps,
        perf_summary={
            "pit_result": pit_result,
            "evidence_result": evidence_result,
            "config": config,
        },
    )
    conn.commit()


def evaluate_champion_candidate(
    conn: Any | None = None,
    *,
    model_id: str,
    feature_table: str = "fact_feature_panel",
    feature_set_id: str = "production_registry",
    panel_feature_set_id: str | None = None,
    top_k: int = 50,
    horizons: str = "20",
    top_sizes: str = "20,50,100,200,500",
    cost_bps: float = 10.0,
    timeout: int = 900,
    pit_audit_run_id: str | None = None,
    connection_factory: Any | None = None,
) -> dict[str, Any]:
    started_at = utc_now_iso()
    started = time.perf_counter()
    evaluation_run_id = f"candidate_eval_{model_id}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"
    pit_audit_run_id = pit_audit_run_id or f"pit_registry_{model_id}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"

    owns_connection = conn is None
    connection_factory = connection_factory or get_conn
    if owns_connection:
        with connection_factory() as audit_conn:
            ensure_tables(audit_conn)
            pit_result = audit_registry_feature_pit(
                audit_conn,
                model_id=model_id,
                feature_table=feature_table,
                feature_set_id=panel_feature_set_id,
                audit_run_id=pit_audit_run_id,
            )
            audit_conn.commit()
    else:
        ensure_tables(conn)
        pit_result = audit_registry_feature_pit(
            conn,
            model_id=model_id,
            feature_table=feature_table,
            feature_set_id=panel_feature_set_id,
            audit_run_id=pit_audit_run_id,
        )
        conn.commit()

    evidence_result = build_evidence_bundle(
        model_id=model_id,
        feature_set_id=feature_set_id,
        feature_table=feature_table,
        panel_feature_set_id=panel_feature_set_id,
        top_k=top_k,
        horizons=horizons,
        top_sizes=top_sizes,
        cost_bps=cost_bps,
        timeout=timeout,
        pit_audit_run_id=pit_audit_run_id,
    )

    failed_steps = list(evidence_result.get("failed_steps") or [])
    pit_status = str(pit_result.get("status") or "unknown")
    evidence_status = str(evidence_result.get("status") or "unknown")
    gate_status = evidence_result.get("gate_status")
    status = candidate_evaluation_status(
        pit_status=pit_status,
        evidence_status=evidence_status,
        gate_status=gate_status,
    )
    ended_at = utc_now_iso()
    duration_s = time.perf_counter() - started
    config = {
        "feature_table": feature_table,
        "feature_set_id": feature_set_id,
        "panel_feature_set_id": panel_feature_set_id,
        "top_k": top_k,
        "horizons": horizons,
        "top_sizes": top_sizes,
        "cost_bps": cost_bps,
        "timeout": timeout,
    }

    if owns_connection:
        with connection_factory() as write_conn:
            _persist_candidate_evaluation(
                write_conn,
                evaluation_run_id=evaluation_run_id,
                model_id=model_id,
                status=status,
                pit_audit_run_id=pit_audit_run_id,
                pit_result=pit_result,
                evidence_result=evidence_result,
                failed_steps=failed_steps,
                gate_status=gate_status,
                config=config,
                started_at=started_at,
                ended_at=ended_at,
                duration_s=duration_s,
            )
    else:
        _persist_candidate_evaluation(
            conn,
            evaluation_run_id=evaluation_run_id,
            model_id=model_id,
            status=status,
            pit_audit_run_id=pit_audit_run_id,
            pit_result=pit_result,
            evidence_result=evidence_result,
            failed_steps=failed_steps,
            gate_status=gate_status,
            config=config,
            started_at=started_at,
            ended_at=ended_at,
            duration_s=duration_s,
        )
    return {
        "evaluation_run_id": evaluation_run_id,
        "model_id": model_id,
        "status": status,
        "pit_audit_run_id": pit_audit_run_id,
        "pit_status": pit_status,
        "pit_violation_rows": int(pit_result.get("violation_rows") or 0),
        "evidence_run_id": evidence_result.get("evidence_run_id"),
        "evidence_status": evidence_status,
        "gate_status": gate_status,
        "failed_steps": failed_steps,
        "duration_s": round(duration_s, 3),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-id", required=True)
    parser.add_argument("--feature-table", default="fact_feature_panel")
    parser.add_argument("--feature-set-id", default="production_registry")
    parser.add_argument("--panel-feature-set-id", default=None)
    parser.add_argument("--top-k", type=int, default=50)
    parser.add_argument("--horizons", default="20")
    parser.add_argument("--top-sizes", default="20,50,100,200,500")
    parser.add_argument("--cost-bps", type=float, default=10.0)
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--pit-audit-run-id", default=None)
    args = parser.parse_args()
    result = evaluate_champion_candidate(
        model_id=args.model_id,
        feature_table=args.feature_table,
        feature_set_id=args.feature_set_id,
        panel_feature_set_id=args.panel_feature_set_id,
        top_k=args.top_k,
        horizons=args.horizons,
        top_sizes=args.top_sizes,
        cost_bps=args.cost_bps,
        timeout=args.timeout,
        pit_audit_run_id=args.pit_audit_run_id,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
