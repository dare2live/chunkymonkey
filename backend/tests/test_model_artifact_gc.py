from __future__ import annotations

from conftest import duck_mem
from services.model_artifact_gc import (
    execute_obsolete_model_artifact_delete,
    plan_obsolete_model_artifact_delete,
)
from services.pipeline_manifest import ensure_pipeline_manifest_schema


def test_model_artifact_gc_deletes_unprotected_model_scope(tmp_path) -> None:
    with duck_mem() as conn:
        ensure_pipeline_manifest_schema(conn)
        conn.executescript(
            """
            CREATE TABLE mart_model_lifecycle (
                model_id TEXT,
                status TEXT,
                created_at TEXT,
                updated_at TEXT,
                promoted_from TEXT
            );
            INSERT INTO mart_model_lifecycle VALUES
                ('old_m', 'challenger', '2026-05-01', '2026-05-01', NULL),
                ('keep_m', 'challenger', '2026-05-01', '2026-05-01', NULL),
                ('champion_m', 'champion', '2026-05-01', '2026-05-01', 'old_m');
            CREATE TABLE mart_multidim_model (
                model_id TEXT,
                label_name TEXT,
                pricing_policy_hash TEXT,
                created_at TEXT,
                feature_schema_version TEXT
            );
            INSERT INTO mart_multidim_model VALUES
                ('old_m', 'follow_net_return_60d', 'old_hash', '2026-05-01', 'schema_old'),
                ('keep_m', 'follow_net_return_60d', 'current_hash', '2026-05-01', 'schema_new'),
                ('champion_m', 'forward_ret_20d', NULL, '2026-05-01', 'schema_champion');
            CREATE TABLE mart_daily_recommendation (
                snapshot_date TEXT,
                stock_code TEXT,
                model_id TEXT,
                is_primary BOOLEAN,
                run_mode TEXT
            );
            INSERT INTO mart_daily_recommendation VALUES
                ('2026-05-06', '000001', 'old_m', false, 'shadow'),
                ('2026-05-06', '000002', 'keep_m', false, 'shadow'),
                ('2026-05-06', '000003', 'champion_m', true, 'champion');
            CREATE TABLE mart_multidim_prediction (model_id TEXT, stock_code TEXT, date TEXT);
            INSERT INTO mart_multidim_prediction VALUES
                ('old_m', '000001', '2026-05-06'),
                ('keep_m', '000002', '2026-05-06');
            CREATE TABLE mart_model_walkforward_fold (run_id TEXT, fold_id INTEGER, model_id TEXT);
            INSERT INTO mart_model_walkforward_fold VALUES
                ('wf_old', 1, 'old_m'),
                ('wf_keep', 1, 'keep_m');
            CREATE TABLE mart_model_walkforward_prediction (run_id TEXT, fold_id INTEGER, stock_code TEXT);
            INSERT INTO mart_model_walkforward_prediction VALUES
                ('wf_old', 1, '000001'),
                ('wf_keep', 1, '000002');
            INSERT INTO mart_pipeline_run_manifest (run_id, pipeline_name, status, model_id) VALUES
                ('train_old', 'train', 'success', 'old_m'),
                ('wf_old', 'walkforward', 'success', NULL),
                ('train_keep', 'train', 'success', 'keep_m');
            """
        )
        model_root = tmp_path / "models"
        model_root.mkdir()
        (model_root / "old_m.pkl").write_bytes(b"old")
        (model_root / "keep_m.pkl").write_bytes(b"keep")

        plan = plan_obsolete_model_artifact_delete(
            conn,
            keep_model_ids={"keep_m"},
            current_policy_hash="current_hash",
            model_root=model_root,
        )
        assert [candidate["model_id"] for candidate in plan["candidates"]] == ["champion_m", "old_m"]
        assert plan["blockers"] == []

        result = execute_obsolete_model_artifact_delete(
            conn,
            run_id="delete_model_gc_unit",
            approve=True,
            keep_model_ids={"keep_m"},
            current_policy_hash="current_hash",
            model_root=model_root,
        )

        assert result["deleted_files"] == 1
        assert not (model_root / "old_m.pkl").exists()
        assert (model_root / "keep_m.pkl").exists()
        assert conn.execute("SELECT COUNT(*) AS n FROM mart_multidim_model WHERE model_id = 'old_m'").fetchone()["n"] == 0
        assert conn.execute("SELECT COUNT(*) AS n FROM mart_multidim_model WHERE model_id = 'champion_m'").fetchone()["n"] == 0
        assert conn.execute("SELECT COUNT(*) AS n FROM mart_model_lifecycle WHERE model_id = 'old_m'").fetchone()["n"] == 0
        assert conn.execute("SELECT COUNT(*) AS n FROM mart_model_lifecycle WHERE model_id = 'champion_m'").fetchone()["n"] == 0
        assert conn.execute("SELECT COUNT(*) AS n FROM mart_daily_recommendation WHERE model_id = 'old_m'").fetchone()["n"] == 0
        assert conn.execute("SELECT COUNT(*) AS n FROM mart_daily_recommendation WHERE model_id = 'champion_m'").fetchone()["n"] == 0
        assert conn.execute("SELECT COUNT(*) AS n FROM mart_model_walkforward_prediction WHERE run_id = 'wf_old'").fetchone()["n"] == 0
        assert conn.execute("SELECT COUNT(*) AS n FROM mart_pipeline_run_manifest WHERE run_id = 'wf_old'").fetchone()["n"] == 0
        assert conn.execute("SELECT COUNT(*) AS n FROM mart_multidim_model WHERE model_id = 'keep_m'").fetchone()["n"] == 1
        assert conn.execute("SELECT COUNT(*) AS n FROM mart_data_deletion_record").fetchone()["n"] >= 1


def test_model_artifact_gc_blocks_primary_dependency(tmp_path) -> None:
    with duck_mem() as conn:
        conn.executescript(
            """
            CREATE TABLE mart_model_lifecycle (model_id TEXT, status TEXT);
            INSERT INTO mart_model_lifecycle VALUES ('bad_primary_m', 'challenger');
            CREATE TABLE mart_multidim_model (
                model_id TEXT,
                pricing_policy_hash TEXT,
                label_name TEXT,
                created_at TEXT,
                feature_schema_version TEXT
            );
            INSERT INTO mart_multidim_model VALUES
                ('bad_primary_m', 'current_hash', 'follow_net_return_60d', '2026-05-01', 'schema');
            CREATE TABLE mart_daily_recommendation (
                snapshot_date TEXT,
                stock_code TEXT,
                model_id TEXT,
                is_primary BOOLEAN,
                run_mode TEXT
            );
            INSERT INTO mart_daily_recommendation VALUES
                ('2026-05-06', '000001', 'bad_primary_m', true, 'primary');
            """
        )

        plan = plan_obsolete_model_artifact_delete(
            conn,
            current_policy_hash="current_hash",
            model_root=tmp_path,
        )

        assert plan["candidate_count"] == 0
        assert plan["blocker_count"] == 1
        assert plan["blockers"][0]["type"] == "primary_recommendation_dependency"


def test_model_artifact_gc_deletes_orphan_model_rows_and_gate_refs(tmp_path) -> None:
    with duck_mem() as conn:
        ensure_pipeline_manifest_schema(conn)
        conn.executescript(
            """
            CREATE TABLE mart_model_lifecycle (model_id TEXT, status TEXT);
            INSERT INTO mart_model_lifecycle VALUES
                ('champion_m', 'champion'),
                ('current60_m', 'challenger');
            CREATE TABLE mart_multidim_model (
                model_id TEXT,
                pricing_policy_hash TEXT,
                label_name TEXT,
                created_at TEXT,
                feature_schema_version TEXT
            );
            INSERT INTO mart_multidim_model VALUES
                ('champion_m', 'current_hash', 'follow_net_return_60d', '2026-05-01', 'schema'),
                ('current60_m', 'current_hash', 'follow_net_return_60d', '2026-05-01', 'schema');
            CREATE TABLE mart_model_holding_topk_eval (run_id TEXT, model_id TEXT, n_signals INTEGER);
            INSERT INTO mart_model_holding_topk_eval VALUES
                ('eval_old', 'old_orphan_m', 10),
                ('eval_current', 'current60_m', 20);
            CREATE TABLE mart_tdx_keep_promotion_gate (
                gate_run_id TEXT,
                challenger_model_id TEXT,
                champion_model_id TEXT
            );
            INSERT INTO mart_tdx_keep_promotion_gate VALUES
                ('gate_old', 'old_orphan_m', 'champion_m'),
                ('gate_current', 'current60_m', 'champion_m');
            INSERT INTO mart_pipeline_run_manifest (run_id, pipeline_name, status, model_id) VALUES
                ('train_old_orphan', 'train_multidim_model', 'success', 'old_orphan_m'),
                ('train_current60', 'train_multidim_model', 'success', 'current60_m');
            """
        )

        plan = plan_obsolete_model_artifact_delete(
            conn,
            keep_model_ids={"current60_m"},
            current_policy_hash="current_hash",
            model_root=tmp_path,
        )

        assert [candidate["model_id"] for candidate in plan["candidates"]] == ["old_orphan_m"]
        candidate = plan["candidates"][0]
        assert candidate["table_counts"]["mart_model_holding_topk_eval"] == 1
        assert candidate["ref_table_counts"]["mart_tdx_keep_promotion_gate:challenger_model_id,champion_model_id"] == 1

        result = execute_obsolete_model_artifact_delete(
            conn,
            run_id="delete_orphan_model_rows_unit",
            approve=True,
            keep_model_ids={"current60_m"},
            current_policy_hash="current_hash",
            model_root=tmp_path,
        )

        assert result["deleted_rows"] >= 3
        assert conn.execute("SELECT COUNT(*) AS n FROM mart_model_holding_topk_eval WHERE model_id = 'old_orphan_m'").fetchone()["n"] == 0
        assert conn.execute("SELECT COUNT(*) AS n FROM mart_tdx_keep_promotion_gate WHERE gate_run_id = 'gate_old'").fetchone()["n"] == 0
        assert conn.execute("SELECT COUNT(*) AS n FROM mart_pipeline_run_manifest WHERE model_id = 'old_orphan_m'").fetchone()["n"] == 0
        assert conn.execute("SELECT COUNT(*) AS n FROM mart_model_holding_topk_eval WHERE model_id = 'current60_m'").fetchone()["n"] == 1
        assert conn.execute("SELECT COUNT(*) AS n FROM mart_tdx_keep_promotion_gate WHERE gate_run_id = 'gate_current'").fetchone()["n"] == 1
