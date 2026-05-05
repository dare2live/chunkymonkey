import sys
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


def test_cron_daily_includes_production_topk_and_source_watermarks():
    assert "watermarks" in cron_daily.ALL_PHASES
    assert "topk" in cron_daily.ALL_PHASES
    assert cron_daily.ALL_PHASES.index("watermarks") < cron_daily.ALL_PHASES.index("topk")
    assert cron_daily.ALL_PHASES.index("topk") < cron_daily.ALL_PHASES.index("health")


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
