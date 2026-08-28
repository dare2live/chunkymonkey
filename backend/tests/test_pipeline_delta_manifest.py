"""CX-1 delta manifest + DC skip + latency budget unit tests."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture(autouse=True)
def _isolated_pipeline_runtime_paths(tmp_path, monkeypatch):
    from services.pipeline import context
    from services.writer_lock import WRITER_LOCK_PATH_ENV

    monkeypatch.setenv(WRITER_LOCK_PATH_ENV, str(tmp_path / "pipeline-writer.lock"))
    monkeypatch.setattr(context, "DEGRADED_FLAG", tmp_path / "pipeline-alert.flag")


def test_decide_dc_skip_when_frontier_unchanged():
    from services.pipeline.delta_manifest import decide_dc_action, plan_process_steps

    decision = decide_dc_action(
        current_frontier="20260721",
        previous_frontier="20260721",
        advanced_partitions=[],
    )
    assert decision["action"] == "skip"
    assert decision["reason"] == "dc_frontier_unchanged"
    plan = plan_process_steps(dc_decision=decision)
    assert plan["dc_industry_view"]["action"] == "skip"
    assert plan["market_pulse"]["action"] == "run"
    assert plan["market_pulse"]["reason"] == "late_window_mandatory"


def test_decide_dc_run_when_frontier_advances():
    from services.pipeline.delta_manifest import decide_dc_action

    decision = decide_dc_action(
        current_frontier="20260722",
        previous_frontier="20260721",
        advanced_partitions=[],
    )
    assert decision["action"] == "run"
    assert decision["dc_frontier_advanced"] is True


def test_decide_dc_run_on_dc_provenance_even_if_frontier_same():
    from services.pipeline.delta_manifest import decide_dc_action

    decision = decide_dc_action(
        current_frontier="20260721",
        previous_frontier="20260721",
        advanced_partitions=[
            {
                "domain": "dc_member",
                "provenance": "drain:dc_member",
                "partition_value": "20260721",
            }
        ],
    )
    assert decision["action"] == "run"
    assert decision["reason"] == "dc_provenance_advanced"


def test_build_advanced_partitions_from_formal_and_drain():
    from services.pipeline.delta_manifest import build_advanced_partitions

    advanced = build_advanced_partitions(
        formal=[
            {"domain": "daily", "action": "skip", "eligible_end": "20260722"},
            {
                "domain": "daily",
                "action": "accepted",
                "eligible_end": "20260721",
                "dataset_id": "canonical_nominal_ohlcv_daily",
            },
        ],
        drain=[
            {"domain": "block_trade", "status": "clean", "refilled_rows": 0},
            {"domain": "moneyflow_dc", "status": "drained", "refilled_rows": 12},
        ],
    )
    assert len(advanced) == 2
    domains = {row["domain"] for row in advanced}
    assert "daily" in domains
    assert "moneyflow_dc" in domains


def test_budget_status_empty_process_pass_fail():
    from services.pipeline.delta_manifest import evaluate_budget_status

    plan_skip = {"dc_industry_view": {"action": "skip"}}
    status = evaluate_budget_status(
        stage_timing_s={"process": 12.0, "clean": 8.0, "acquire": 100.0},
        process_plan=plan_skip,
        budgets_s={
            "process_empty_increment_s": 90,
            "process_with_dc_rebuild_s": 360,
            "clean_qfq_from_accepted_s": 30,
            "acquire_soft_ceiling_s": 7200,
        },
    )
    assert status["process"] == "pass"
    assert status["clean"] == "pass"

    status_fail = evaluate_budget_status(
        stage_timing_s={"process": 120.0},
        process_plan=plan_skip,
        budgets_s={"process_empty_increment_s": 90},
    )
    assert status_fail["process"] == "fail"


def test_write_report_includes_delta_manifest(tmp_path, monkeypatch):
    from services.pipeline import store
    from services.pipeline.context import PipelineContext
    from services.pipeline.delta_manifest import empty_manifest, plan_process_steps

    monkeypatch.setattr(store, "REPO", tmp_path)
    (tmp_path / "data/reports").mkdir(parents=True)
    (tmp_path / "data/audit").mkdir(parents=True)

    ctx = PipelineContext(dry=True, date="20990101", log_path=tmp_path / "t.log")
    manifest = empty_manifest(run_date="20990101")
    manifest["process_plan"] = plan_process_steps(
        dc_decision={
            "action": "skip",
            "reason": "dc_frontier_unchanged",
            "dc_frontier_advanced": False,
        }
    )
    manifest["acquire_summary"]["formal"] = [
        {"domain": "daily", "action": "skip", "reason": "latest_eligible_already_accepted"}
    ]
    ctx.delta_manifest = manifest
    ctx.stage_timing_s = {"acquire": 1.0, "clean": 2.0, "process": 3.0, "store": 4.0, "total": 10.0}

    monkeypatch.setattr(store, "_dispatch_by_outcome", lambda *a, **k: None)
    monkeypatch.setattr(store, "_outcome_summary_banner", lambda *a, **k: None)

    output = store.write_report_and_alert(ctx)
    assert output["delta_manifest"]["schema_version"] == 1
    assert output["delta_manifest"]["process_plan"]["dc_industry_view"]["action"] == "skip"
    assert output["delta_manifest"]["process_plan"]["market_pulse"]["action"] == "run"
    assert output["stage_timing_s"]["process"] == 3.0
    assert "budget_status" in output
    report_path = tmp_path / "data/reports/daily_20990101.json"
    assert report_path.exists()
    saved = json.loads(report_path.read_text(encoding="utf-8"))
    assert saved["delta_manifest"]["acquire_summary"]["formal"][0]["action"] == "skip"


def test_process_skips_dc_when_plan_says_skip(tmp_path, monkeypatch):
    from services.pipeline import process
    from services.pipeline.context import PipelineContext
    from services.pipeline.delta_manifest import empty_manifest, plan_process_steps

    ctx = PipelineContext(dry=False, date="20990101", log_path=tmp_path / "t.log")
    manifest = empty_manifest(run_date="20990101")
    manifest["process_plan"] = plan_process_steps(
        dc_decision={
            "action": "skip",
            "reason": "dc_frontier_unchanged",
            "dc_frontier_advanced": False,
        }
    )
    ctx.delta_manifest = manifest

    calls: list[str] = []

    def _fake_run_script(rel_path, args=None, *, degraded_msg):
        calls.append(rel_path)
        return True

    monkeypatch.setattr(ctx, "run_script", _fake_run_script)

    def _ok_step(fn, *, degraded_msg):
        # Don't invoke real DB builders.
        name = getattr(fn, "__name__", "")
        if name == "_pulse_latest":
            ctx.delta_manifest.setdefault("process_outcome", {})
            # Simulate pulse always-run outcome without DB.
            ctx.log("[market_pulse] simulated")
        return True

    # Replace step to avoid DB, but still record pulse invariant via plan.
    monkeypatch.setattr(ctx, "step", _ok_step)
    process.run_process(ctx)
    assert calls == []  # DC script not invoked
    assert ctx.delta_manifest["process_outcome"]["dc_industry_view"]["action"] == "skip"
    assert ctx.delta_manifest["process_plan"]["market_pulse"]["action"] == "run"


def test_dc_as_of_roundtrip(tmp_path, monkeypatch):
    from services.pipeline import delta_manifest as dm

    marker = tmp_path / "dc_as_of.json"
    monkeypatch.setattr(dm, "DC_AS_OF_PATH", marker)
    assert dm.read_dc_as_of() is None
    dm.write_dc_as_of("20260721")
    assert dm.read_dc_as_of() == "20260721"


def test_run_and_record_captures_stage_timing(tmp_path, monkeypatch):
    from services.pipeline.context import PipelineContext
    from services.pipeline.stage_status import run_and_record

    ctx = PipelineContext(dry=True, date="20990101", log_path=tmp_path / "t.log")
    monkeypatch.setattr(
        "services.pipeline.stage_status._record_stage_best_effort",
        lambda *a, **k: None,
    )

    def _fn(_ctx):
        import time

        time.sleep(0.01)

    assert run_and_record(ctx, "acquire", _fn) is True
    assert run_and_record(ctx, "process", _fn) is True
    assert ctx.stage_timing_s["process"] >= 0.01
    # total must equal sum of stages, not compound prior totals
    expected = round(
        float(ctx.stage_timing_s["acquire"]) + float(ctx.stage_timing_s["process"]),
        3,
    )
    assert ctx.stage_timing_s["total"] == expected
    assert ctx.stage_timing_s["total"] < float(ctx.stage_timing_s["acquire"]) * 3


def test_dc_provenance_keeps_live_dc_chain_without_retired_dc_daily():
    from services.pipeline.delta_manifest import load_latency_budgets

    cfg = load_latency_budgets()
    domains = cfg["dc_provenance_domains"]
    assert "dc_index" in domains and "dc_member" in domains
    assert "sync:dc_index" in domains and "sync:dc_member" in domains
    assert "dc_daily" not in domains and "sync:dc_daily" not in domains
