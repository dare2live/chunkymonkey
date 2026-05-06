#!/usr/bin/env python3
"""Build a promotion evidence bundle for a challenger without promoting it."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.db import get_conn  # noqa: E402
from services.pipeline_manifest import git_commit_sha, record_pipeline_run, utc_now_iso  # noqa: E402
from services.schema_versions import record_actual_version  # noqa: E402


REPO = Path(__file__).resolve().parent.parent.parent
SCRIPT_DIR = Path(__file__).resolve().parent

DDL = """
CREATE TABLE IF NOT EXISTS mart_challenger_evidence_bundle (
    evidence_run_id TEXT PRIMARY KEY,
    model_id TEXT NOT NULL,
    status TEXT NOT NULL,
    steps_json TEXT NOT NULL,
    gate_run_id TEXT,
    gate_status TEXT,
    blockers_json TEXT,
    started_at TEXT NOT NULL,
    ended_at TEXT NOT NULL,
    duration_s DOUBLE
);
"""


def _run_step(name: str, cmd: list[str], *, timeout: int, success_codes: set[int] | None = None) -> dict[str, Any]:
    started = time.perf_counter()
    success_codes = success_codes or {0}
    try:
        result = subprocess.run(
            cmd,
            cwd=REPO,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
        )
        status = "success" if result.returncode in success_codes else "failed"
        return {
            "name": name,
            "status": status,
            "returncode": result.returncode,
            "duration_s": round(time.perf_counter() - started, 3),
            "command": " ".join(cmd),
            "output_tail": (result.stdout or "")[-4000:],
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "name": name,
            "status": "timeout",
            "returncode": None,
            "duration_s": round(time.perf_counter() - started, 3),
            "command": " ".join(cmd),
            "output_tail": (exc.stdout or "")[-4000:] if isinstance(exc.stdout, str) else "",
        }


def _latest_gate_for_model(conn, model_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT gate_run_id, promotion_status, decision, blockers_json
          FROM mart_tdx_keep_promotion_gate
         WHERE challenger_model_id = ?
         ORDER BY evaluated_at DESC
         LIMIT 1
        """,
        (model_id,),
    ).fetchone()
    return dict(row) if row else None


def build_evidence_bundle(
    *,
    model_id: str,
    feature_set_id: str = "tdx_f10_gpcw_v1",
    feature_table: str = "fact_feature_panel",
    panel_feature_set_id: str | None = None,
    top_k: int = 50,
    horizons: str = "20",
    top_sizes: str = "20,50,100,200,500",
    cost_bps: float = 10.0,
    timeout: int = 900,
    pit_audit_run_id: str = "pit_tdx_f10_gpcw_v1",
) -> dict[str, Any]:
    evidence_run_id = f"evidence_{model_id}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"
    started_at = utc_now_iso()
    started = time.perf_counter()
    steps: list[dict[str, Any]] = []
    py = sys.executable
    panel_feature_set_id = panel_feature_set_id or None

    shadow_topk_cmd = [
        py,
        str(SCRIPT_DIR / "run_daily_topk.py"),
        "--model-id",
        model_id,
        "--mode",
        "shadow",
        "--top-k",
        str(top_k),
        "--track-id",
        f"shadow_{model_id}",
        "--feature-table",
        feature_table,
    ]
    if panel_feature_set_id:
        shadow_topk_cmd.extend(["--feature-set-id", panel_feature_set_id])

    holding_topk_cmd = [
        py,
        str(SCRIPT_DIR / "evaluate_holding_topk.py"),
        "--model-id",
        model_id,
        "--feature-table",
        feature_table,
        "--horizons",
        horizons,
        "--top-sizes",
        top_sizes,
        "--cost-bps",
        str(cost_bps),
    ]
    if panel_feature_set_id:
        holding_topk_cmd.extend(["--feature-set-id", panel_feature_set_id])

    step_specs = [
        (
            "shadow_topk",
            shadow_topk_cmd,
            {0},
        ),
        (
            "challenger_drift",
            [
                py,
                str(SCRIPT_DIR / "compute_feature_drift.py"),
                "--model-id",
                model_id,
                "--feature-table",
                feature_table,
                *(
                    ["--feature-set-id", panel_feature_set_id]
                    if panel_feature_set_id
                    else []
                ),
                "--top-n",
                "30",
            ],
            {0, 2},
        ),
        (
            "holding_topk",
            holding_topk_cmd,
            {0},
        ),
        (
            "portfolio_backtest",
            [
                py,
                str(SCRIPT_DIR / "backtest_model_portfolio.py"),
                "--model-id",
                model_id,
                "--cost-bps",
                str(cost_bps),
                "--top-sizes",
                "20",
                "--random-seeds",
                "0",
            ],
            {0},
        ),
        (
            "promotion_gate",
            [
                py,
                str(SCRIPT_DIR / "evaluate_tdx_keep_promotion_gate.py"),
                "--model-id",
                model_id,
                "--feature-table",
                feature_table,
                "--feature-set-id",
                panel_feature_set_id or feature_set_id,
                "--retention-feature-set-id",
                feature_set_id,
                "--pit-audit-run-id",
                pit_audit_run_id,
            ],
            {0},
        ),
    ]

    for name, cmd, success_codes in step_specs:
        steps.append(_run_step(name, cmd, timeout=timeout, success_codes=success_codes))

    ended_at = utc_now_iso()
    duration_s = time.perf_counter() - started
    failed_steps = [step for step in steps if step["status"] != "success"]
    status = "success" if not failed_steps else "failed"
    with get_conn() as conn:
        conn.executescript(DDL)
        gate = _latest_gate_for_model(conn, model_id)
        blockers = []
        if gate and gate.get("blockers_json"):
            try:
                blockers = json.loads(gate["blockers_json"])
            except Exception:
                blockers = [gate["blockers_json"]]
        conn.execute(
            """
            INSERT OR REPLACE INTO mart_challenger_evidence_bundle
            (evidence_run_id, model_id, status, steps_json, gate_run_id,
             gate_status, blockers_json, started_at, ended_at, duration_s)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                evidence_run_id,
                model_id,
                status,
                json.dumps(steps, ensure_ascii=False),
                gate.get("gate_run_id") if gate else None,
                gate.get("promotion_status") if gate else None,
                json.dumps(blockers, ensure_ascii=False),
                started_at,
                ended_at,
                duration_s,
            ),
        )
        record_actual_version(conn, "mart_challenger_evidence_bundle", "v1")
        record_pipeline_run(
            conn,
            run_id=evidence_run_id,
            pipeline_name="build_challenger_evidence_bundle",
            status=status,
            started_at=started_at,
            ended_at=ended_at,
            duration_s=duration_s,
            commit_sha=git_commit_sha(REPO),
            input_tables=["mart_multidim_model", feature_table],
            output_tables=[
                "mart_daily_recommendation",
                "mart_feature_drift",
                "mart_model_holding_topk_eval",
                "mart_model_portfolio_summary",
                "mart_tdx_keep_promotion_gate",
                "mart_challenger_evidence_bundle",
            ],
            model_id=model_id,
            gate_result=gate.get("promotion_status") if gate else None,
            blockers=blockers,
            perf_summary={
                "steps": steps,
                "failed_steps": [s["name"] for s in failed_steps],
                "feature_table": feature_table,
                "panel_feature_set_id": panel_feature_set_id,
                "retention_feature_set_id": feature_set_id,
                "pit_audit_run_id": pit_audit_run_id,
            },
        )
        conn.commit()

    return {
        "evidence_run_id": evidence_run_id,
        "model_id": model_id,
        "status": status,
        "gate_status": gate.get("promotion_status") if gate else None,
        "failed_steps": [s["name"] for s in failed_steps],
        "duration_s": round(duration_s, 3),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-id", required=True)
    parser.add_argument("--feature-set-id", default="tdx_f10_gpcw_v1")
    parser.add_argument("--feature-table", default="fact_feature_panel")
    parser.add_argument("--panel-feature-set-id", default=None)
    parser.add_argument("--top-k", type=int, default=50)
    parser.add_argument("--horizons", default="20")
    parser.add_argument("--top-sizes", default="20,50,100,200,500")
    parser.add_argument("--cost-bps", type=float, default=10.0)
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--pit-audit-run-id", default="pit_tdx_f10_gpcw_v1")
    args = parser.parse_args()
    result = build_evidence_bundle(
        model_id=args.model_id,
        feature_set_id=args.feature_set_id,
        feature_table=args.feature_table,
        panel_feature_set_id=args.panel_feature_set_id,
        top_k=args.top_k,
        horizons=args.horizons,
        top_sizes=args.top_sizes,
        cost_bps=args.cost_bps,
        timeout=args.timeout,
        pit_audit_run_id=args.pit_audit_run_id,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "success" else 2


if __name__ == "__main__":
    raise SystemExit(main())
