import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts import cron_daily  # noqa: E402
from services.data_lineage.registry import all_lineages  # noqa: E402
from services.data_lineage.run import run_lineage  # noqa: E402
from services.data_sources.clients_registry import all_clients  # noqa: E402
from services.data_sources.sources.aif10 import CAPABILITY_TO_REPORT, Aif10Source  # noqa: E402


def test_cron_status_uses_running_field_and_exposes_step_failures():
    assert cron_daily._coerce_bool("running")
    assert not cron_daily._coerce_bool("False")

    status, reason = cron_daily._sync_status_from_backend({
        "running": False,
        "steps": [
            {"step_id": "sync_raw", "status": "failed"},
            {"step_id": "sync_lhb", "status": "completed"},
        ],
    })

    assert status == "failed"
    assert "sync_raw" in reason


def test_cron_child_return_codes_preserve_critical_failures():
    assert cron_daily._phase_status_from_rc("health", 1)["status"] == "critical"
    assert cron_daily._phase_status_from_rc("drift", 2)["status"] == "critical"
    assert cron_daily._phase_status_from_rc("drift", 1)["status"] == "warn"
    assert cron_daily._phase_exit_severity({"status": "critical"}) == 2
    assert cron_daily._phase_exit_severity({"status": "warn"}) == 1
    assert cron_daily._phase_exit_severity({"phase": "sync", "status": "timeout"}) == 2


def test_cron_blocks_followups_after_running_sync_failures():
    assert cron_daily._sync_failure_blocks_followups({"phase": "sync", "status": "timeout"})
    assert cron_daily._sync_failure_blocks_followups({"phase": "sync", "status": "rejected"})
    assert cron_daily._sync_failure_blocks_followups({"phase": "sync", "status": "stale_running"})
    assert not cron_daily._sync_failure_blocks_followups({"phase": "sync", "status": "skipped"})
    assert not cron_daily._sync_failure_blocks_followups({"phase": "health", "status": "critical"})


def test_cron_detects_stale_backend_update_heartbeat():
    now = datetime(2026, 5, 5, 12, 0, 0)
    stale_status = {
        "running": True,
        "run_context": {
            "step_id": "sync_financial",
            "heartbeat_at": (now - timedelta(seconds=301)).isoformat(),
        },
    }
    fresh_status = {
        "running": True,
        "run_context": {
            "step_id": "sync_financial",
            "heartbeat_at": (now - timedelta(seconds=30)).isoformat(),
        },
    }

    reason = cron_daily._stale_running_reason(stale_status, stale_after_s=300, now=now)

    assert reason is not None
    assert "sync_financial" in reason
    assert cron_daily._stale_running_reason(fresh_status, stale_after_s=300, now=now) is None
    assert cron_daily._stale_running_reason({"running": False}, stale_after_s=300, now=now) is None


def test_cron_daily_includes_production_topk_and_source_watermarks():
    assert "watermarks" in cron_daily.ALL_PHASES
    assert "topk" in cron_daily.ALL_PHASES
    assert "candidate_eval" in cron_daily.ALL_PHASES
    assert cron_daily.ALL_PHASES.index("watermarks") < cron_daily.ALL_PHASES.index("topk")
    assert cron_daily.ALL_PHASES.index("topk") < cron_daily.ALL_PHASES.index("health")
    assert cron_daily.ALL_PHASES.index("drift") < cron_daily.ALL_PHASES.index("candidate_eval")
    assert cron_daily.ALL_PHASES.index("candidate_eval") < cron_daily.ALL_PHASES.index("audit")


def test_cron_candidate_eval_skips_without_explicit_model_id():
    result = cron_daily.phase_candidate_eval(model_id=None)

    assert result["status"] == "skipped"
    assert cron_daily._phase_exit_severity(result) == 0


def test_cron_main_runs_local_daily_phases_in_order(monkeypatch):
    calls = []
    recorded = {}

    monkeypatch.setattr(sys, "argv", ["cron_daily.py", "--skip-sync", "--only", "lineage,watermarks,topk,health,drift,candidate_eval,audit"])
    monkeypatch.setattr(cron_daily, "_acquire_cron_pipeline_lock", lambda **kwargs: {"acquired": True})
    monkeypatch.setattr(cron_daily, "_heartbeat_cron_pipeline_lock", lambda **kwargs: None)
    monkeypatch.setattr(cron_daily, "_release_cron_pipeline_lock", lambda **kwargs: {"released": True})

    def record_manifest(**kwargs):
        recorded.update(kwargs)

    monkeypatch.setattr(cron_daily, "_record_cron_manifest", record_manifest)
    monkeypatch.setattr(cron_daily, "phase_lineage", lambda: calls.append("lineage") or {"phase": "lineage", "status": "ok"})
    monkeypatch.setattr(cron_daily, "phase_watermarks", lambda: calls.append("watermarks") or {"phase": "watermarks", "status": "ok"})
    monkeypatch.setattr(cron_daily, "phase_topk", lambda **kwargs: calls.append("topk") or {"phase": "topk", "status": "ok"})
    monkeypatch.setattr(cron_daily, "phase_health", lambda: calls.append("health") or {"phase": "health", "status": "ok"})
    monkeypatch.setattr(cron_daily, "phase_drift", lambda: calls.append("drift") or {"phase": "drift", "status": "ok"})
    monkeypatch.setattr(cron_daily, "phase_candidate_eval", lambda **kwargs: calls.append("candidate_eval") or {"phase": "candidate_eval", "status": "skipped"})
    monkeypatch.setattr(cron_daily, "phase_audit", lambda: calls.append("audit") or {"phase": "audit", "status": "ok"})

    rc = cron_daily.main()

    assert rc == 0
    assert calls == ["lineage", "watermarks", "topk", "health", "drift", "candidate_eval", "audit"]
    assert recorded["exit_severity"] == 0
    assert [row["phase"] for row in recorded["results"]] == calls


def test_cron_main_blocks_duckdb_followups_after_sync_timeout(monkeypatch):
    calls = []
    recorded = {}

    monkeypatch.setattr(sys, "argv", ["cron_daily.py", "--only", "sync,watermarks,topk"])
    monkeypatch.setattr(cron_daily, "_acquire_cron_pipeline_lock", lambda **kwargs: {"acquired": True})
    monkeypatch.setattr(cron_daily, "_heartbeat_cron_pipeline_lock", lambda **kwargs: None)
    monkeypatch.setattr(cron_daily, "_release_cron_pipeline_lock", lambda **kwargs: {"released": True})
    monkeypatch.setattr(cron_daily, "_record_cron_manifest", lambda **kwargs: recorded.update(kwargs))
    monkeypatch.setattr(cron_daily, "phase_sync", lambda **kwargs: calls.append("sync") or {"phase": "sync", "status": "timeout"})
    monkeypatch.setattr(cron_daily, "phase_watermarks", lambda: calls.append("watermarks") or {"phase": "watermarks", "status": "ok"})
    monkeypatch.setattr(cron_daily, "phase_topk", lambda **kwargs: calls.append("topk") or {"phase": "topk", "status": "ok"})

    rc = cron_daily.main()

    assert rc == 2
    assert calls == ["sync"]
    assert recorded["exit_severity"] == 2
    assert recorded["results"] == [{"phase": "sync", "status": "timeout", "phase_elapsed_s": recorded["results"][0]["phase_elapsed_s"]}]


def test_aif10_registry_matches_live_special_reports():
    assert CAPABILITY_TO_REPORT["lhb_daily"] == "RPT_DAILYBILLBOARD_DETAILSNEW"
    assert CAPABILITY_TO_REPORT["qfii_holding_quarterly"] == "RPT_DMSK_HOLDERS"
    assert CAPABILITY_TO_REPORT["institution_survey"] == "RPT_ORG_SURVEYNEW"

    caps = {cap.name: cap for cap in Aif10Source().capabilities}
    assert "reportName=RPT_DMSK_HOLDERS" in caps["qfii_holding_quarterly"].notes
    assert "reportName=RPT_ORG_SURVEYNEW" in caps["institution_survey"].notes


def test_client_registry_uses_actual_updater_step_ids():
    survey_client = next(c for c in all_clients() if c.client_id == "institution_survey_client")
    assert survey_client.sync_step_id == "sync_surveys"


def test_lineage_registry_is_metadata_only_by_default():
    assert all(lineage.metadata_only for lineage in all_lineages())

    result = run_lineage("fact_holder_event/lag_window_v1", dry_run=True)

    assert result["status"] == "skipped"
    assert "metadata-only" in result["error"]
