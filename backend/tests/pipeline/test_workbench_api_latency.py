from __future__ import annotations

import json

import pytest

from conftest import duck_mem
from scripts.measure_workbench_api_latency import measure_endpoints, run_workbench_api_latency


pytestmark = pytest.mark.pipeline


class FakeResponse:
    def __init__(self, status_code: int, content: bytes = b"{}") -> None:
        self.status_code = status_code
        self.content = content


class FakeClient:
    def __init__(self, status_by_endpoint: dict[str, int]) -> None:
        self.status_by_endpoint = status_by_endpoint

    def get(self, endpoint: str) -> FakeResponse:
        return FakeResponse(self.status_by_endpoint.get(endpoint, 200), b'{"ok":true}')


def test_measure_endpoints_flags_failed_response():
    result = measure_endpoints(
        FakeClient({"/api/workbench/overview": 200, "/api/workbench/storage": 500}),
        endpoints=["/api/workbench/overview", "/api/workbench/storage"],
        max_latency_ms=1000.0,
    )

    assert result["status"] == "warn"
    assert result["failed_count"] == 1
    assert result["slow_count"] == 0
    assert result["endpoints"][1]["ok"] is False


def test_run_workbench_api_latency_records_manifest():
    with duck_mem() as conn:
        result = run_workbench_api_latency(
            conn,
            run_id="workbench_latency_unit",
            max_latency_ms=1000.0,
            client=FakeClient({endpoint: 200 for endpoint in ["/api/workbench/overview"]}),
        )

        manifest = conn.execute(
            """
            SELECT pipeline_name, status, gate_result, blockers_json, perf_summary_json
              FROM mart_pipeline_run_manifest
             WHERE run_id = 'workbench_latency_unit'
            """
        ).fetchone()
        perf = json.loads(manifest["perf_summary_json"])

        assert result["status"] == "pass"
        assert manifest["pipeline_name"] == "measure_workbench_api_latency"
        assert manifest["status"] == "success"
        assert manifest["gate_result"] == "pass"
        assert json.loads(manifest["blockers_json"]) == []
        assert perf["endpoint_count"] == 8
        assert perf["failed_count"] == 0
        assert perf["slow_count"] == 0
