import json
from pathlib import Path
import subprocess
import sys

import pytest

from conftest import duck_mem
from services.duck_adapter import connect as duck_connect
from services.storage_retention import (
    CandidateFeaturePanelRule,
    ProtectedArtifactRule,
    StorageRetentionPolicy,
    TableInventoryRule,
    execute_storage_cleanup,
    load_storage_retention_policy,
    plan_storage_cleanup,
)


def test_storage_retention_missing_optional_tables_is_empty_dry_run():
    conn = duck_mem()
    try:
        policy = StorageRetentionPolicy(
            protected_model_statuses=("champion",),
            candidate_feature_panels=(),
            model_prediction_tables=(),
            model_file_roots=(),
            optuna_study_roots=(),
            defaults={},
        )
        report = plan_storage_cleanup(conn, policy)

        assert report["mode"] == "dry_run"
        assert report["candidate_count"] == 0
        assert "requires_backup_before_delete" not in report
        assert report["delete_policy"] == "verified_direct_delete_no_archive"
    finally:
        conn.close()


def test_storage_retention_protects_lifecycle_models_and_referenced_feature_sets():
    conn = duck_mem()
    try:
        conn.executescript(
            """
            CREATE TABLE mart_model_lifecycle (
                model_id TEXT,
                status TEXT,
                training_config TEXT
            );
            CREATE TABLE mart_multidim_prediction (
                model_id TEXT,
                stock_code TEXT
            );
            CREATE TABLE fact_feature_panel_candidate (
                feature_set_id TEXT,
                stock_code TEXT,
                date TEXT,
                built_at TEXT
            );
            INSERT INTO mart_model_lifecycle VALUES
                ('champion_m', 'champion', '{"feature_set_id": "protected_set"}'),
                ('challenger_m', 'challenger', NULL),
                ('retired_m', 'retired', '{"feature_set_id": "old_set_1"}');
            INSERT INTO mart_multidim_prediction VALUES
                ('champion_m', '000001'),
                ('challenger_m', '000002'),
                ('retired_m', '000003'),
                ('unregistered_m', '000004');
            INSERT INTO fact_feature_panel_candidate VALUES
                ('protected_set', '000001', '2026-01-01', '2026-05-05T00:00:00'),
                ('latest_set', '000001', '2026-01-01', '2026-05-04T00:00:00'),
                ('old_set_1', '000001', '2026-01-01', '2026-05-03T00:00:00'),
                ('old_set_2', '000001', '2026-01-01', '2026-05-02T00:00:00'),
                ('old_set_3', '000001', '2026-01-01', '2026-05-01T00:00:00');
            """
        )

        report = plan_storage_cleanup(conn)
        model_candidates = {
            item["model_id"]
            for item in report["candidates"]
            if item["kind"] == "model_prediction_rows"
        }
        feature_candidates = {
            item["key_value"]
            for item in report["candidates"]
            if item["kind"] == "candidate_feature_panel"
        }

        assert "champion_m" not in model_candidates
        assert "challenger_m" not in model_candidates
        assert {"retired_m", "unregistered_m"}.issubset(model_candidates)
        assert "protected_set" not in feature_candidates
        assert "old_set_3" in feature_candidates
    finally:
        conn.close()


def test_storage_retention_protects_evidence_and_primary_outputs():
    conn = duck_mem()
    try:
        conn.executescript(
            """
            CREATE TABLE mart_model_lifecycle (
                model_id TEXT,
                status TEXT,
                training_config TEXT
            );
            CREATE TABLE mart_challenger_evidence_bundle (
                model_id TEXT
            );
            CREATE TABLE mart_tdx_keep_promotion_gate (
                challenger_model_id TEXT,
                champion_model_id TEXT
            );
            CREATE TABLE mart_daily_recommendation (
                model_id TEXT,
                is_primary BOOLEAN
            );
            CREATE TABLE mart_multidim_prediction (
                model_id TEXT,
                stock_code TEXT
            );
            CREATE TABLE mart_model_explanation (
                model_id TEXT,
                snapshot_date TEXT
            );
            CREATE TABLE mart_daily_recommendation_explanation (
                model_id TEXT,
                snapshot_date TEXT,
                stock_code TEXT
            );
            INSERT INTO mart_model_lifecycle VALUES
                ('active_champion', 'champion', NULL),
                ('retired_but_evidence', 'retired', NULL),
                ('retired_gate_champion', 'retired', NULL),
                ('retired_primary', 'retired', NULL),
                ('delete_me', 'retired', NULL);
            INSERT INTO mart_challenger_evidence_bundle VALUES ('retired_but_evidence');
            INSERT INTO mart_tdx_keep_promotion_gate VALUES
                ('retired_but_evidence', 'active_champion'),
                ('new_challenger', 'retired_gate_champion');
            INSERT INTO mart_daily_recommendation VALUES ('retired_primary', TRUE);
            INSERT INTO mart_multidim_prediction VALUES
                ('retired_but_evidence', '000001'),
                ('retired_gate_champion', '000001'),
                ('retired_primary', '000002'),
                ('delete_me', '000003');
            INSERT INTO mart_model_explanation VALUES
                ('retired_primary', '2026-04-30'),
                ('delete_me', '2026-04-30');
            INSERT INTO mart_daily_recommendation_explanation VALUES
                ('retired_primary', '2026-04-30', '000001'),
                ('delete_me', '2026-04-30', '000002');
            """
        )

        report = plan_storage_cleanup(conn)
        model_candidates = {
            (item["table"], item["model_id"])
            for item in report["candidates"]
            if item["kind"] == "model_prediction_rows"
        }

        assert ("mart_multidim_prediction", "retired_but_evidence") not in model_candidates
        assert ("mart_multidim_prediction", "retired_gate_champion") not in model_candidates
        assert ("mart_multidim_prediction", "retired_primary") not in model_candidates
        assert ("mart_multidim_prediction", "delete_me") in model_candidates
        assert ("mart_model_explanation", "retired_primary") not in model_candidates
        assert ("mart_model_explanation", "delete_me") in model_candidates
        assert ("mart_daily_recommendation_explanation", "retired_primary") not in model_candidates
        assert ("mart_daily_recommendation_explanation", "delete_me") in model_candidates
        assert "evidence_bundle" in report["protected_model_reasons"]["retired_but_evidence"]
        assert "promotion_gate_champion" in report["protected_model_reasons"]["retired_gate_champion"]
        assert "primary_output:mart_daily_recommendation" in report["protected_model_reasons"]["retired_primary"]
    finally:
        conn.close()


def test_storage_retention_reports_protected_pit_f10_and_horizon_artifacts():
    conn = duck_mem()
    try:
        conn.executescript(
            """
            CREATE TABLE raw_tdx_f10_holder_research (
                stock_code TEXT,
                raw_hash TEXT
            );
            CREATE TABLE mart_feature_pit_coverage_summary (
                audit_run_id TEXT,
                feature_set_id TEXT
            );
            CREATE TABLE mart_stock_horizon_selection (
                run_id TEXT,
                feature_set_id TEXT
            );
            INSERT INTO raw_tdx_f10_holder_research VALUES ('000001', 'hash_1');
            INSERT INTO mart_feature_pit_coverage_summary VALUES ('pit_run', 'pit_set');
            INSERT INTO mart_stock_horizon_selection VALUES ('horizon_run', 'horizon_set');
            """
        )
        policy = StorageRetentionPolicy(
            protected_model_statuses=("champion",),
            candidate_feature_panels=(),
            model_prediction_tables=(),
            model_file_roots=(),
            optuna_study_roots=(),
            defaults={},
            protected_artifact_tables=(
                ProtectedArtifactRule("raw_tdx_f10_holder_research", "raw replay"),
                ProtectedArtifactRule("mart_feature_pit_coverage_summary", "pit evidence"),
                ProtectedArtifactRule("missing_artifact", "future table"),
            ),
        )

        report = plan_storage_cleanup(conn, policy)
        artifacts = {row["table"]: row for row in report["protected_artifact_tables"]}

        assert report["protected_artifact_table_count"] == 3
        assert artifacts["raw_tdx_f10_holder_research"]["exists"] is True
        assert artifacts["raw_tdx_f10_holder_research"]["row_count"] == 1
        assert artifacts["mart_feature_pit_coverage_summary"]["exists"] is True
        assert artifacts["missing_artifact"]["exists"] is False
        assert "pit_set" in report["protected_feature_set_reasons"]
        assert "feature_pit_coverage_summary" in report["protected_feature_set_reasons"]["pit_set"]
        assert "horizon_set" in report["protected_feature_set_reasons"]
        assert "stock_horizon_selection" in report["protected_feature_set_reasons"]["horizon_set"]
    finally:
        conn.close()


def test_storage_retention_reports_table_inventory_without_delete_candidates():
    conn = duck_mem()
    try:
        conn.executescript(
            """
            CREATE TABLE raw_tdx_f10_holder_research (
                stock_code TEXT,
                raw_text TEXT
            );
            CREATE TABLE mart_feature_rank_matrix_cache_alpha (
                signal_date DATE,
                stock_code TEXT
            );
            CREATE TABLE mart_feature_rank_matrix_cache_manifest (
                cache_table TEXT
            );
            INSERT INTO raw_tdx_f10_holder_research VALUES
                ('000001', 'raw holder text');
            INSERT INTO mart_feature_rank_matrix_cache_alpha VALUES
                ('2026-06-04', '000001'),
                ('2026-06-04', '000002');
            INSERT INTO mart_feature_rank_matrix_cache_manifest VALUES
                ('mart_feature_rank_matrix_cache_alpha');
            """
        )
        policy = StorageRetentionPolicy(
            protected_model_statuses=("champion",),
            candidate_feature_panels=(),
            model_prediction_tables=(),
            model_file_roots=(),
            optuna_study_roots=(),
            defaults={},
            table_inventory=(
                TableInventoryRule(
                    table="raw_tdx_f10_holder_research",
                    classification="protected_raw_lineage",
                    owner="tdx_f10_source_ingestion",
                    truth_source="tdx_f10_raw_holder_snapshot",
                    consumers=("holder_replay",),
                    compaction_policy="protect_no_compaction_without_verified_copy",
                    retention_action="protect_replayability_before_any_retention_change",
                    reason="raw replay source",
                ),
                TableInventoryRule(
                    table="mart_feature_rank_matrix_cache_manifest",
                    classification="cache_manifest_evidence",
                    owner="feature_rank_matrix_cache",
                    truth_source="feature_rank_matrix_cache_identity_ledger",
                    consumers=("feature_rank_matrix_cache",),
                    delete_gates=("cache_policy_replacement_verified",),
                    rollback_evidence=("cache_manifest_export",),
                    compaction_policy="protect_until_cache_policy_then_checkpoint",
                    retention_action="protect_until_cache_policy_uses_manifest",
                    reason="cache identity ledger",
                ),
                TableInventoryRule(
                    table_like="mart_feature_rank_matrix_cache_%",
                    classification="cache_candidate",
                    owner="feature_rank_matrix_cache",
                    truth_source="feature_rank_matrix_cache_manifest",
                    consumers=("feature_rank_matrix_cache",),
                    delete_gates=("cache_manifest_stale_key_policy",),
                    rollback_evidence=("copied_duckdb_before_after_counts",),
                    compaction_policy="checkpoint_after_cache_policy_delete",
                    retention_action="define_stale_cache_policy_before_delete",
                    reason="persistent rank matrix cache",
                    exclude_tables=("mart_feature_rank_matrix_cache_manifest",),
                ),
            ),
        )

        report = plan_storage_cleanup(conn, policy)
        inventory = {item["table"]: item for item in report["table_inventory"]}

        assert report["candidate_count"] == 0
        assert report["table_inventory_count"] == 3
        assert inventory["raw_tdx_f10_holder_research"]["classification"] == "protected_raw_lineage"
        assert inventory["raw_tdx_f10_holder_research"]["row_count"] == 1
        assert inventory["raw_tdx_f10_holder_research"]["consumers"] == ["holder_replay"]
        assert inventory["raw_tdx_f10_holder_research"]["truth_source"] == "tdx_f10_raw_holder_snapshot"
        assert inventory["raw_tdx_f10_holder_research"]["compaction_policy"] == (
            "protect_no_compaction_without_verified_copy"
        )
        assert inventory["mart_feature_rank_matrix_cache_manifest"]["classification"] == "cache_manifest_evidence"
        assert inventory["mart_feature_rank_matrix_cache_manifest"]["row_count"] == 1
        assert inventory["mart_feature_rank_matrix_cache_manifest"]["delete_gates"] == [
            "cache_policy_replacement_verified"
        ]
        assert inventory["mart_feature_rank_matrix_cache_alpha"]["classification"] == "cache_candidate"
        assert inventory["mart_feature_rank_matrix_cache_alpha"]["row_count"] == 2
        assert inventory["mart_feature_rank_matrix_cache_alpha"]["rollback_evidence"] == [
            "copied_duckdb_before_after_counts"
        ]
        assert all(item["delete_candidate"] is False for item in report["table_inventory"])
        assert report["policy_contract"]["verdict"] == "PASS"
        assert report["policy_contract"]["checked_candidate_entries"] == 0
    finally:
        conn.close()


def test_storage_retention_reports_missing_inventory_patterns():
    conn = duck_mem()
    try:
        policy = StorageRetentionPolicy(
            protected_model_statuses=("champion",),
            candidate_feature_panels=(),
            model_prediction_tables=(),
            model_file_roots=(),
            optuna_study_roots=(),
            defaults={},
            table_inventory=(
                TableInventoryRule(
                    table="missing_panel",
                    classification="obsolete_candidate_requires_owner",
                    owner="panel_pipeline_manifest",
                    truth_source="historical_panel_pipeline_output",
                    consumers=("unknown_pending_codegraph",),
                    delete_gates=("codegraph_no_live_consumer",),
                    rollback_evidence=("copied_duckdb_execute_dry_run",),
                    compaction_policy="checkpoint_after_approved_delete",
                    retention_action="prove_no_consumer_and_reproducibility_before_delete",
                    reason="future or absent table",
                ),
                TableInventoryRule(
                    table_like="missing_cache_%",
                    classification="cache_candidate",
                    owner="feature_rank_matrix_cache",
                    truth_source="feature_rank_matrix_cache_manifest",
                    consumers=("feature_rank_matrix_cache",),
                    delete_gates=("cache_manifest_stale_key_policy",),
                    rollback_evidence=("cache_manifest_export",),
                    compaction_policy="checkpoint_after_cache_policy_delete",
                    retention_action="define_stale_cache_policy_before_delete",
                    reason="future cache pattern",
                ),
            ),
        )

        report = plan_storage_cleanup(conn, policy)
        inventory = {item["table"]: item for item in report["table_inventory"]}

        assert inventory["missing_panel"]["exists"] is False
        assert inventory["missing_panel"]["row_count"] is None
        assert inventory["missing_cache_%"]["exists"] is False
        assert inventory["missing_cache_%"]["row_count"] is None
        assert report["policy_contract"]["verdict"] == "FAIL"
        issues = {item["issue"] for item in report["policy_contract"]["violations"]}
        assert "unknown_consumer_proof" in issues
    finally:
        conn.close()


def test_storage_retention_policy_contract_blocks_owner_only_inventory():
    conn = duck_mem()
    try:
        policy = StorageRetentionPolicy(
            protected_model_statuses=("champion",),
            candidate_feature_panels=(),
            model_prediction_tables=(),
            model_file_roots=(),
            optuna_study_roots=(),
            defaults={},
            table_inventory=(
                TableInventoryRule(
                    table="owner_only_cache",
                    classification="cache_candidate",
                    owner="feature_rank_matrix_cache",
                    retention_action="define_stale_cache_policy_before_delete",
                    reason="owner-only entries are insufficient for deletion governance",
                ),
            ),
        )

        report = plan_storage_cleanup(conn, policy)

        assert report["policy_contract"]["verdict"] == "FAIL"
        issues = {item["issue"] for item in report["policy_contract"]["violations"]}
        assert {
            "missing_truth_source",
            "missing_consumers",
            "missing_compaction_policy",
            "missing_delete_gates",
            "missing_rollback_evidence",
        } <= issues
        with pytest.raises(RuntimeError, match="policy contract"):
            execute_storage_cleanup(conn, policy, approve=True)
    finally:
        conn.close()


def test_storage_retention_policy_contract_covers_executable_candidates():
    conn = duck_mem()
    try:
        conn.executescript(
            """
            CREATE TABLE fact_feature_panel_candidate (
                feature_set_id TEXT,
                stock_code TEXT,
                built_at TEXT
            );
            INSERT INTO fact_feature_panel_candidate VALUES
                ('new_set', '000001', '2026-05-06T00:00:00'),
                ('old_set', '000001', '2026-05-05T00:00:00');
            """
        )
        policy = StorageRetentionPolicy(
            protected_model_statuses=("champion",),
            candidate_feature_panels=(
                CandidateFeaturePanelRule(
                    table="fact_feature_panel_candidate",
                    key_column="feature_set_id",
                    built_at_column="built_at",
                    retain_latest_keys=1,
                ),
            ),
            model_prediction_tables=(),
            model_file_roots=(),
            optuna_study_roots=(),
            defaults={},
            table_inventory=(),
        )

        report = plan_storage_cleanup(conn, policy)

        assert report["candidate_count"] == 1
        assert report["policy_contract"]["verdict"] == "FAIL"
        assert {
            item["issue"] for item in report["policy_contract"]["violations"]
        } == {"candidate_without_inventory_contract"}
        with pytest.raises(RuntimeError, match="policy contract"):
            execute_storage_cleanup(conn, policy, approve=True)
    finally:
        conn.close()


def test_storage_retention_policy_contract_accepts_candidate_with_inventory_contract():
    conn = duck_mem()
    try:
        conn.executescript(
            """
            CREATE TABLE fact_feature_panel_candidate (
                feature_set_id TEXT,
                stock_code TEXT,
                built_at TEXT
            );
            INSERT INTO fact_feature_panel_candidate VALUES
                ('new_set', '000001', '2026-05-06T00:00:00'),
                ('old_set', '000001', '2026-05-05T00:00:00');
            """
        )
        policy = StorageRetentionPolicy(
            protected_model_statuses=("champion",),
            candidate_feature_panels=(
                CandidateFeaturePanelRule(
                    table="fact_feature_panel_candidate",
                    key_column="feature_set_id",
                    built_at_column="built_at",
                    retain_latest_keys=1,
                ),
            ),
            model_prediction_tables=(),
            model_file_roots=(),
            optuna_study_roots=(),
            defaults={},
            table_inventory=(
                TableInventoryRule(
                    table="fact_feature_panel_candidate",
                    classification="governed_candidate_panel",
                    owner="storage_retention_policy",
                    truth_source="candidate_feature_panel_retention_rules",
                    consumers=("model_selection_run",),
                    delete_gates=("protected_feature_set_reason_scan",),
                    rollback_evidence=("copied_duckdb_before_after_counts",),
                    compaction_policy="checkpoint_after_approved_delete",
                    retention_action="use_candidate_feature_panel_retention_rules",
                    reason="candidate panel cleanup is governed by key-level retention",
                ),
            ),
        )

        report = plan_storage_cleanup(conn, policy)

        assert report["candidate_count"] == 1
        assert report["policy_contract"]["verdict"] == "PASS"
        assert report["policy_contract"]["checked_candidate_entries"] == 1
    finally:
        conn.close()


def test_storage_retention_execute_requires_approval():
    conn = duck_mem()
    try:
        policy = StorageRetentionPolicy(
            protected_model_statuses=("champion",),
            candidate_feature_panels=(),
            model_prediction_tables=(),
            model_file_roots=(),
            optuna_study_roots=(),
            defaults={},
        )

        try:
            execute_storage_cleanup(conn, policy, approve=False)
        except RuntimeError as exc:
            assert "approve=True" in str(exc)
        else:
            raise AssertionError("expected approval error")
    finally:
        conn.close()


def test_storage_retention_execute_deletes_rows_directly_without_copying(tmp_path):
    conn = duck_mem()
    try:
        conn.executescript(
            """
            CREATE TABLE mart_model_lifecycle (
                model_id TEXT,
                status TEXT,
                training_config TEXT
            );
            CREATE TABLE mart_multidim_prediction (
                model_id TEXT,
                stock_code TEXT
            );
            INSERT INTO mart_model_lifecycle VALUES
                ('keep_m', 'champion', NULL),
                ('delete_m', 'retired', NULL);
            INSERT INTO mart_multidim_prediction VALUES
                ('keep_m', '000001'),
                ('delete_m', '000002'),
                ('delete_m', '000003');
            """
        )
        policy = StorageRetentionPolicy(
            protected_model_statuses=("champion",),
            candidate_feature_panels=(),
            model_prediction_tables=load_storage_retention_policy().model_prediction_tables,
            model_file_roots=(),
            optuna_study_roots=(),
            defaults={},
            table_inventory=(
                TableInventoryRule(
                    table="mart_multidim_prediction",
                    classification="model_prediction_rows",
                    owner="storage_retention_policy",
                    truth_source="mart_model_lifecycle",
                    consumers=("model_prediction_cleanup",),
                    delete_gates=("protected_model_id_scan",),
                    rollback_evidence=("copied_duckdb_before_after_counts",),
                    compaction_policy="checkpoint_after_approved_delete",
                    retention_action="delete_non_protected_model_predictions",
                    reason="unit test prediction rows can be deleted after lifecycle protection scan",
                ),
            ),
        )

        result = execute_storage_cleanup(
            conn,
            policy,
            approve=True,
            run_id="cleanup_unit",
        )
        remaining = conn.execute(
            "SELECT model_id, COUNT(*) AS n FROM mart_multidim_prediction GROUP BY model_id ORDER BY model_id"
        ).fetchall()
        backup_tables = [
            row[0]
            for row in conn.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_name LIKE 'backup_storage_cleanup_cleanup_unit%'"
            ).fetchall()
        ]

        assert result["mode"] == "execute_approved"
        assert result["executed_count"] == 1
        assert result["delete_policy"] == "verified_direct_delete_no_archive"
        assert result["direct_delete_confirmed"] is True
        assert [(row["model_id"], row["n"]) for row in remaining] == [("keep_m", 1)]
        assert backup_tables == []
        assert not (tmp_path / "manifest.json").exists()
    finally:
        conn.close()


def test_storage_retention_execute_deletes_model_files_directly(tmp_path):
    model_dir = tmp_path / "models"
    model_dir.mkdir()
    keep_file = model_dir / "keep_m.pkl"
    delete_file = model_dir / "delete_m.pkl"
    keep_file.write_bytes(b"keep")
    delete_file.write_bytes(b"delete")
    conn = duck_mem()
    try:
        conn.executescript(
            """
            CREATE TABLE mart_model_lifecycle (
                model_id TEXT,
                status TEXT,
                training_config TEXT
            );
            INSERT INTO mart_model_lifecycle VALUES
                ('keep_m', 'champion', NULL),
                ('delete_m', 'retired', NULL);
            """
        )
        policy = StorageRetentionPolicy(
            protected_model_statuses=("champion",),
            candidate_feature_panels=(),
            model_prediction_tables=(),
            model_file_roots=(str(model_dir),),
            optuna_study_roots=(),
            defaults={},
            table_inventory=(
                TableInventoryRule(
                    table=f"filesystem:{model_dir}",
                    classification="model_file_candidate",
                    owner="storage_retention_policy",
                    truth_source="mart_model_lifecycle",
                    consumers=("model_file_cleanup",),
                    delete_gates=("protected_model_id_scan",),
                    rollback_evidence=("copied_duckdb_before_after_counts",),
                    compaction_policy="not_applicable_filesystem_delete",
                    retention_action="delete_non_protected_model_files",
                    reason="unit test model file can be deleted after lifecycle protection scan",
                ),
            ),
        )

        result = execute_storage_cleanup(
            conn,
            policy,
            approve=True,
            run_id="cleanup_files",
        )

        assert keep_file.exists()
        assert not delete_file.exists()
        assert result["executed_count"] == 1
        assert result["direct_delete_confirmed"] is True
    finally:
        conn.close()


def test_storage_retention_reports_active_optuna_study_artifacts(tmp_path):
    study_dir = tmp_path / "studies"
    study_dir.mkdir()
    (study_dir / "feature_space.sqlite3").write_bytes(b"sqlite")
    config = tmp_path / "storage_retention.yaml"
    config.write_text(
        f"""
version: 1
protected_model_statuses:
  - champion
candidate_feature_panels: []
model_prediction_tables: []
model_file_roots: []
optuna_study_roots:
  - {study_dir}
""",
        encoding="utf-8",
    )
    conn = duck_mem()
    try:
        policy = load_storage_retention_policy(config)
        report = plan_storage_cleanup(conn, policy)

        assert report["active_optuna_study_count"] == 1
        assert report["active_optuna_study_artifacts"][0]["path"].endswith("feature_space.sqlite3")
    finally:
        conn.close()


def test_storage_retention_custom_policy_can_tighten_feature_set_retention(tmp_path):
    config = tmp_path / "storage_retention.yaml"
    config.write_text(
        """
version: 1
protected_model_statuses:
  - champion
candidate_feature_panels:
  - table: fact_feature_panel_candidate
    key_column: feature_set_id
    built_at_column: built_at
    retain_latest_keys: 1
model_prediction_tables: []
model_file_roots: []
optuna_study_roots: []
defaults:
  large_delete_row_threshold: 2
""",
        encoding="utf-8",
    )
    conn = duck_mem()
    try:
        conn.executescript(
            """
            CREATE TABLE fact_feature_panel_candidate (
                feature_set_id TEXT,
                stock_code TEXT,
                built_at TEXT
            );
            INSERT INTO fact_feature_panel_candidate VALUES
                ('new_set', '000001', '2026-05-05T00:00:00'),
                ('old_set', '000001', '2026-05-04T00:00:00');
            """
        )

        policy = load_storage_retention_policy(config)
        report = plan_storage_cleanup(conn, policy)

        assert report["candidate_count"] == 1
        assert report["candidates"][0]["key_value"] == "old_set"
        assert report["compaction"]["recommended"] is False
    finally:
        conn.close()


def test_storage_retention_recommends_compaction_for_large_deletes():
    conn = duck_mem()
    try:
        conn.executescript(
            """
            CREATE TABLE fact_feature_panel_candidate (
                feature_set_id TEXT,
                stock_code TEXT,
                built_at TEXT
            );
            INSERT INTO fact_feature_panel_candidate VALUES
                ('new_set', '000001', '2026-05-05T00:00:00'),
                ('old_set', '000001', '2026-05-04T00:00:00'),
                ('old_set', '000002', '2026-05-04T00:00:00');
            """
        )
        policy = StorageRetentionPolicy(
            protected_model_statuses=("champion",),
            candidate_feature_panels=(
                CandidateFeaturePanelRule(
                    table="fact_feature_panel_candidate",
                    key_column="feature_set_id",
                    built_at_column="built_at",
                    retain_latest_keys=1,
                ),
            ),
            model_prediction_tables=(),
            model_file_roots=(),
            optuna_study_roots=(),
            defaults={"large_delete_row_threshold": 2},
        )

        report = plan_storage_cleanup(conn, policy)

        assert report["candidate_count"] == 1
        assert report["compaction"]["recommended"] is True
        assert "CHECKPOINT" in report["compaction"]["commands"][0]
    finally:
        conn.close()


def test_storage_retention_protects_model_selection_feature_sets():
    conn = duck_mem()
    try:
        conn.executescript(
            """
            CREATE TABLE fact_feature_panel_candidate (
                feature_set_id TEXT,
                stock_code TEXT,
                built_at TEXT
            );
            CREATE TABLE mart_model_selection_run (
                run_id TEXT,
                feature_set_id TEXT,
                selected_features_json TEXT
            );
            INSERT INTO fact_feature_panel_candidate VALUES
                ('new_set', '000001', '2026-05-06T00:00:00'),
                ('selected_old_set', '000001', '2026-05-05T00:00:00'),
                ('delete_old_set', '000001', '2026-05-04T00:00:00');
            INSERT INTO mart_model_selection_run VALUES
                ('selected_run', 'selected_old_set', '["f1"]');
            """
        )
        policy = StorageRetentionPolicy(
            protected_model_statuses=("champion",),
            candidate_feature_panels=(
                CandidateFeaturePanelRule(
                    table="fact_feature_panel_candidate",
                    key_column="feature_set_id",
                    built_at_column="built_at",
                    retain_latest_keys=1,
                ),
            ),
            model_prediction_tables=(),
            model_file_roots=(),
            optuna_study_roots=(),
            defaults={},
        )

        report = plan_storage_cleanup(conn, policy)
        feature_candidates = {
            item["key_value"]
            for item in report["candidates"]
            if item["kind"] == "candidate_feature_panel"
        }

        assert "selected_old_set" not in feature_candidates
        assert "delete_old_set" in feature_candidates
    finally:
        conn.close()


def test_storage_retention_protects_feature_sets_from_evidence_profiles_and_schedule():
    conn = duck_mem()
    try:
        conn.executescript(
            """
            CREATE TABLE fact_feature_panel_candidate (
                feature_set_id TEXT,
                stock_code TEXT,
                built_at TEXT
            );
            CREATE TABLE mart_model_stability_search_summary (
                run_id TEXT,
                feature_set_id TEXT
            );
            CREATE TABLE mart_stock_horizon_profile (
                run_id TEXT,
                feature_set_id TEXT
            );
            CREATE TABLE mart_stock_horizon_selection (
                run_id TEXT,
                feature_set_id TEXT
            );
            CREATE TABLE mart_feature_pit_audit (
                audit_run_id TEXT,
                feature_set_id TEXT
            );
            CREATE TABLE mart_feature_pit_coverage_summary (
                audit_run_id TEXT,
                feature_set_id TEXT
            );
            CREATE TABLE mart_champion_candidate_evaluation (
                evaluation_run_id TEXT,
                config_json TEXT
            );
            CREATE TABLE mart_challenger_evidence_bundle (
                evidence_run_id TEXT,
                steps_json TEXT
            );
            CREATE TABLE mart_research_schedule_plan (
                run_id TEXT,
                command_json TEXT,
                resources_json TEXT,
                config_json TEXT
            );
            INSERT INTO fact_feature_panel_candidate VALUES
                ('new_set', '000001', '2026-05-10T00:00:00'),
                ('stability_set', '000001', '2026-05-09T00:00:00'),
                ('horizon_set', '000001', '2026-05-08T00:00:00'),
                ('horizon_selection_set', '000001', '2026-05-07T12:00:00'),
                ('pit_audit_set', '000001', '2026-05-07T08:00:00'),
                ('pit_coverage_set', '000001', '2026-05-07T04:00:00'),
                ('candidate_eval_set', '000001', '2026-05-07T00:00:00'),
                ('evidence_steps_set', '000001', '2026-05-06T00:00:00'),
                ('schedule_command_set', '000001', '2026-05-05T00:00:00'),
                ('schedule_resource_set', '000001', '2026-05-04T00:00:00'),
                ('delete_old_set', '000001', '2026-05-03T00:00:00');
            INSERT INTO mart_model_stability_search_summary VALUES
                ('stability_run', 'stability_set');
            INSERT INTO mart_stock_horizon_profile VALUES
                ('horizon_run', 'horizon_set');
            INSERT INTO mart_stock_horizon_selection VALUES
                ('horizon_selection_run', 'horizon_selection_set');
            INSERT INTO mart_feature_pit_audit VALUES
                ('pit_audit_run', 'pit_audit_set');
            INSERT INTO mart_feature_pit_coverage_summary VALUES
                ('pit_coverage_run', 'pit_coverage_set');
            INSERT INTO mart_champion_candidate_evaluation VALUES
                ('eval_run', '{"panel_feature_set_id": "candidate_eval_set"}');
            INSERT INTO mart_challenger_evidence_bundle VALUES
                ('evidence_run', '{"steps": [{"args": {"--feature-set-id": "evidence_steps_set"}}]}');
            INSERT INTO mart_research_schedule_plan VALUES
                (
                    'schedule_run',
                    '{"argv": ["python", "script.py", "--feature-set-id", "schedule_command_set"]}',
                    '{"feature_set_id": "schedule_resource_set"}',
                    '{"command": {"args": {"--retention-feature-set-id": "schedule_config_set"}}}'
                );
            INSERT INTO fact_feature_panel_candidate VALUES
                ('schedule_config_set', '000001', '2026-05-02T00:00:00');
            """
        )
        policy = StorageRetentionPolicy(
            protected_model_statuses=("champion",),
            candidate_feature_panels=(
                CandidateFeaturePanelRule(
                    table="fact_feature_panel_candidate",
                    key_column="feature_set_id",
                    built_at_column="built_at",
                    retain_latest_keys=1,
                ),
            ),
            model_prediction_tables=(),
            model_file_roots=(),
            optuna_study_roots=(),
            defaults={},
        )

        report = plan_storage_cleanup(conn, policy)
        feature_candidates = {
            item["key_value"]
            for item in report["candidates"]
            if item["kind"] == "candidate_feature_panel"
        }

        assert "delete_old_set" in feature_candidates
        assert "stability_set" not in feature_candidates
        assert "horizon_set" not in feature_candidates
        assert "horizon_selection_set" not in feature_candidates
        assert "pit_audit_set" not in feature_candidates
        assert "pit_coverage_set" not in feature_candidates
        assert "candidate_eval_set" not in feature_candidates
        assert "evidence_steps_set" not in feature_candidates
        assert "schedule_command_set" not in feature_candidates
        assert "schedule_resource_set" not in feature_candidates
        assert "schedule_config_set" not in feature_candidates
        assert "model_stability_summary" in report["protected_feature_set_reasons"]["stability_set"]
        assert "stock_horizon_profile" in report["protected_feature_set_reasons"]["horizon_set"]
        assert "stock_horizon_selection" in report["protected_feature_set_reasons"]["horizon_selection_set"]
        assert "feature_pit_audit" in report["protected_feature_set_reasons"]["pit_audit_set"]
        assert "feature_pit_coverage_summary" in report["protected_feature_set_reasons"]["pit_coverage_set"]
        assert "research_schedule_command" in report["protected_feature_set_reasons"]["schedule_command_set"]
    finally:
        conn.close()


def test_storage_retention_cli_executes_against_copied_duckdb(tmp_path):
    source_db = tmp_path / "source.duckdb"
    copied_db = tmp_path / "copied.duckdb"
    config = tmp_path / "storage_retention.yaml"
    config.write_text(
        """
version: 1
protected_model_statuses:
  - champion
candidate_feature_panels: []
model_prediction_tables:
  - table: mart_multidim_prediction
    model_id_column: model_id
model_file_roots: []
optuna_study_roots: []
defaults:
table_inventory:
  - table: mart_multidim_prediction
    classification: model_prediction_rows
    owner: storage_retention_policy
    truth_source: mart_model_lifecycle
    consumers:
      - model_prediction_cleanup
    delete_gates:
      - protected_model_id_scan
    rollback_evidence:
      - copied_duckdb_before_after_counts
    compaction_policy: checkpoint_after_approved_delete
    retention_action: delete_non_protected_model_predictions
    reason: copied-db smoke test prediction rows can be deleted after lifecycle protection scan
""",
        encoding="utf-8",
    )
    source_conn = duck_connect(str(source_db))
    try:
        source_conn.executescript(
            """
            CREATE TABLE mart_model_lifecycle (
                model_id TEXT,
                status TEXT,
                training_config TEXT
            );
            CREATE TABLE mart_multidim_prediction (
                model_id TEXT,
                stock_code TEXT
            );
            INSERT INTO mart_model_lifecycle VALUES
                ('keep_m', 'champion', NULL),
                ('delete_m', 'retired', NULL);
            INSERT INTO mart_multidim_prediction VALUES
                ('keep_m', '000001'),
                ('delete_m', '000002'),
                ('delete_m', '000003');
            """
        )
    finally:
        source_conn.close()

    script = "backend/scripts/plan_storage_retention.py"
    result = subprocess.run(
        [
            sys.executable,
            script,
            "--config",
            str(config),
            "--copy-from",
            str(source_db),
            "--db-path",
            str(copied_db),
            "--overwrite-copy",
            "--execute-approved",
            "--run-id",
            "copied_cleanup_smoke",
        ],
        cwd=str(Path(__file__).resolve().parents[2]),
        text=True,
        capture_output=True,
        check=True,
    )
    payload = json.loads(result.stdout)
    copied_conn = duck_connect(str(copied_db))
    source_conn = duck_connect(str(source_db))
    try:
        copied_remaining = copied_conn.execute(
            """
            SELECT model_id, COUNT(*) AS n
              FROM mart_multidim_prediction
             GROUP BY model_id
             ORDER BY model_id
            """
        ).fetchall()
        source_remaining = source_conn.execute(
            """
            SELECT model_id, COUNT(*) AS n
              FROM mart_multidim_prediction
             GROUP BY model_id
             ORDER BY model_id
            """
        ).fetchall()
    finally:
        copied_conn.close()
        source_conn.close()

    assert payload["mode"] == "execute_approved"
    assert payload["executed_count"] == 1
    assert [(row["model_id"], row["n"]) for row in copied_remaining] == [("keep_m", 1)]
    assert [(row["model_id"], row["n"]) for row in source_remaining] == [("delete_m", 2), ("keep_m", 1)]
    assert payload["direct_delete_confirmed"] is True
