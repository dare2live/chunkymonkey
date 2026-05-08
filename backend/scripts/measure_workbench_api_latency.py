#!/usr/bin/env python3
"""Measure Workbench read-model API latency and record manifest evidence."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.db import get_conn  # noqa: E402
from services.pipeline_manifest import git_commit_sha, record_pipeline_run, utc_now_iso  # noqa: E402


REPO = Path(__file__).resolve().parent.parent.parent
WORKBENCH_ENDPOINTS = [
    "/api/workbench/overview",
    "/api/workbench/data-sources",
    "/api/workbench/pipelines",
    "/api/workbench/features",
    "/api/workbench/research",
    "/api/workbench/champion",
    "/api/workbench/recommendations",
    "/api/workbench/storage",
]


def _response_size_bytes(response: Any) -> int:
    content = getattr(response, "content", None)
    if content is not None:
        return len(content)
    text = getattr(response, "text", "")
    return len(str(text).encode("utf-8"))


def _response_json(response: Any) -> dict[str, Any]:
    json_func = getattr(response, "json", None)
    if callable(json_func):
        try:
            payload = json_func()
            return payload if isinstance(payload, dict) else {}
        except Exception:
            return {}
    content = getattr(response, "content", None)
    text = content.decode("utf-8") if isinstance(content, bytes) else getattr(response, "text", "")
    try:
        payload = json.loads(str(text or "{}"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def measure_endpoints(
    client: Any,
    *,
    endpoints: list[str] | None = None,
    max_latency_ms: float = 1500.0,
    require_materialized_contract: bool = True,
) -> dict[str, Any]:
    rows = []
    for endpoint in endpoints or WORKBENCH_ENDPOINTS:
        started = time.perf_counter()
        error = None
        status_code = None
        size_bytes = 0
        read_model_contract_ok = None
        read_model_source_mode = None
        recompute_on_read = None
        try:
            response = client.get(endpoint)
            status_code = int(getattr(response, "status_code", 0) or 0)
            size_bytes = _response_size_bytes(response)
            if status_code == 200 and require_materialized_contract:
                read_model = _response_json(response).get("read_model") or {}
                read_model_source_mode = read_model.get("source_mode")
                recompute_on_read = read_model.get("recompute_on_read")
                read_model_contract_ok = (
                    read_model_source_mode == "materialized_snapshot"
                    and recompute_on_read is False
                )
        except Exception as exc:  # pragma: no cover - defensive real-client path.
            error = str(exc)
        elapsed_ms = round((time.perf_counter() - started) * 1000.0, 3)
        contract_ok = read_model_contract_ok is not False
        ok = status_code == 200 and error is None and elapsed_ms <= max_latency_ms and contract_ok
        rows.append(
            {
                "endpoint": endpoint,
                "status_code": status_code,
                "elapsed_ms": elapsed_ms,
                "response_size_bytes": size_bytes,
                "budget_ms": float(max_latency_ms),
                "read_model_contract_ok": read_model_contract_ok,
                "read_model_source_mode": read_model_source_mode,
                "recompute_on_read": recompute_on_read,
                "ok": ok,
                "error": error,
            }
        )
    failed = [row for row in rows if row["status_code"] != 200 or row["error"]]
    slow = [row for row in rows if row["elapsed_ms"] > max_latency_ms and not row["error"]]
    contract_failed = [row for row in rows if row["read_model_contract_ok"] is False]
    return {
        "endpoint_count": len(rows),
        "max_latency_ms": max((row["elapsed_ms"] for row in rows), default=0.0),
        "avg_latency_ms": round(sum(row["elapsed_ms"] for row in rows) / max(len(rows), 1), 3),
        "slow_count": len(slow),
        "failed_count": len(failed),
        "contract_failed_count": len(contract_failed),
        "budget_ms": float(max_latency_ms),
        "status": "pass" if not slow and not failed and not contract_failed else "warn",
        "endpoints": rows,
    }


def run_workbench_api_latency(
    conn: Any,
    *,
    run_id: str,
    max_latency_ms: float = 1500.0,
    client: Any | None = None,
) -> dict[str, Any]:
    if client is None:
        from fastapi.testclient import TestClient  # noqa: WPS433
        from main import app  # noqa: WPS433

        client = TestClient(app)
    started_at = utc_now_iso()
    started = time.perf_counter()
    summary = measure_endpoints(client, max_latency_ms=max_latency_ms)
    duration_s = time.perf_counter() - started
    ended_at = utc_now_iso()
    record_pipeline_run(
        conn,
        run_id=run_id,
        pipeline_name="measure_workbench_api_latency",
        status="success" if summary["status"] == "pass" else "warn",
        started_at=started_at,
        ended_at=ended_at,
        duration_s=duration_s,
        commit_sha=git_commit_sha(REPO),
        input_tables=["workbench_read_models"],
        output_tables=[],
        gate_result=summary["status"],
        blockers=[row["endpoint"] for row in summary["endpoints"] if not row["ok"]],
        perf_summary=summary,
    )
    conn.commit()
    return {"run_id": run_id, **summary}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", default=f"workbench_api_latency_{time.strftime('%Y%m%d_%H%M%S')}")
    parser.add_argument("--max-latency-ms", type=float, default=1500.0)
    parser.add_argument("--fail-on-budget", action="store_true")
    args = parser.parse_args()
    conn = get_conn()
    try:
        result = run_workbench_api_latency(
            conn,
            run_id=args.run_id,
            max_latency_ms=args.max_latency_ms,
        )
    finally:
        conn.close()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if args.fail_on_budget and result["status"] != "pass" else 0


if __name__ == "__main__":
    raise SystemExit(main())
