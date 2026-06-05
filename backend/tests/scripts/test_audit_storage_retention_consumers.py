from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "audit_storage_retention_consumers.py"
SPEC = importlib.util.spec_from_file_location("audit_storage_retention_consumers", SCRIPT_PATH)
audit_storage_retention_consumers = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = audit_storage_retention_consumers
SPEC.loader.exec_module(audit_storage_retention_consumers)


def _write_config(repo: Path, *, consumers: list[str]) -> Path:
    config_path = repo / "backend" / "config" / "storage_retention.yaml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        """
version: 1
table_inventory:
  - table: mart_example_panel
    classification: protected_runtime_panel
    owner: panel_pipeline_manifest
    db_alias: smartmoney
    truth_source: historical_panel_pipeline_output
    consumers:
""".lstrip()
        + "".join(f"      - {consumer}\n" for consumer in consumers)
        + """
    compaction_policy: protect_no_compaction_without_verified_copy
    retention_action: protect_until_runtime_consumers_are_migrated_or_retired
    reason: test fixture
""",
        encoding="utf-8",
    )
    return config_path


def _write_table_like_config(repo: Path, *, consumers: list[str]) -> Path:
    config_path = repo / "backend" / "config" / "storage_retention.yaml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        """
version: 1
table_inventory:
  - table_like: mart_example_cache_%
    exclude_tables:
      - mart_example_cache_manifest
    classification: cache_candidate
    owner: feature_rank_matrix_cache
    db_alias: smartmoney
    truth_source: feature_rank_matrix_cache_manifest
    consumers:
""".lstrip()
        + "".join(f"      - {consumer}\n" for consumer in consumers)
        + """
    delete_gates:
      - cache_manifest_stale_key_policy
    rollback_evidence:
      - cache_manifest_export
    compaction_policy: checkpoint_after_cache_policy_delete
    retention_action: define_stale_cache_policy_before_delete
    reason: test fixture
""",
        encoding="utf-8",
    )
    return config_path


def test_storage_retention_consumer_audit_fails_unknown_consumer(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path, consumers=["unknown_pending_codegraph"])
    script_path = tmp_path / "backend" / "scripts" / "live_consumer.py"
    script_path.parent.mkdir(parents=True, exist_ok=True)
    script_path.write_text(
        'PANEL = "mart_example_panel"\n'
        'VERSIONED_PANEL = "mart_example_panel_v2"\n',
        encoding="utf-8",
    )
    analysis_path = tmp_path / "analysis" / "note.md"
    analysis_path.parent.mkdir(parents=True, exist_ok=True)
    analysis_path.write_text("Historical note: mart_example_panel\n", encoding="utf-8")

    report = audit_storage_retention_consumers.build_report(repo=tmp_path, config_path=config_path)
    item = report["items"][0]

    assert report["verdict"] == "FAIL"
    assert item["unknown_consumers"] == ["unknown_pending_codegraph"]
    assert item["runtime_reference_count"] == 1
    assert len([ref for ref in item["references"] if ref["path"].endswith("live_consumer.py")]) == 1
    categories = {ref["category"] for ref in item["references"]}
    assert {"runtime_code", "analysis_history"} <= categories


def test_storage_retention_consumer_audit_accepts_known_consumer_and_keeps_runtime_evidence(
    tmp_path: Path,
) -> None:
    config_path = _write_config(tmp_path, consumers=["runtime_consumer_example"])
    script_path = tmp_path / "backend" / "scripts" / "live_consumer.py"
    script_path.parent.mkdir(parents=True, exist_ok=True)
    script_path.write_text('PANEL = "mart_example_panel"\n', encoding="utf-8")

    report = audit_storage_retention_consumers.build_report(repo=tmp_path, config_path=config_path)
    markdown = audit_storage_retention_consumers.render_markdown(report)
    item = report["items"][0]

    assert report["verdict"] == "PASS"
    assert report["summary"]["runtime_ref_tables"] == ["mart_example_panel"]
    assert item["runtime_reference_count"] == 1
    assert "| `mart_example_panel` | `PASS` | 1 | - |" in markdown


def test_storage_retention_consumer_audit_checks_table_like_refs(tmp_path: Path) -> None:
    config_path = _write_table_like_config(tmp_path, consumers=["feature_rank_matrix_cache"])
    script_path = tmp_path / "backend" / "scripts" / "cache_user.py"
    script_path.parent.mkdir(parents=True, exist_ok=True)
    script_path.write_text(
        'CACHE_TABLE = "mart_example_cache_alpha"\n'
        'EXCLUDED_TABLE = "mart_example_cache_manifest"\n',
        encoding="utf-8",
    )

    report = audit_storage_retention_consumers.build_report(repo=tmp_path, config_path=config_path)
    item = report["items"][0]

    assert report["verdict"] == "PASS"
    assert item["table"] == "mart_example_cache_%"
    assert item["runtime_reference_count"] == 1
    assert item["references"][0]["snippet"] == 'CACHE_TABLE = "mart_example_cache_alpha"'


def test_storage_retention_consumer_audit_fails_unknown_table_like_consumer(tmp_path: Path) -> None:
    config_path = _write_table_like_config(tmp_path, consumers=["unknown_pending_codegraph"])

    report = audit_storage_retention_consumers.build_report(repo=tmp_path, config_path=config_path)
    item = report["items"][0]

    assert report["verdict"] == "FAIL"
    assert item["unknown_consumers"] == ["unknown_pending_codegraph"]
