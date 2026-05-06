"""Config-driven storage retention dry-run planner."""
from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable


CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "storage_retention.yaml"
REPO_ROOT = Path(__file__).resolve().parent.parent.parent


@dataclass(frozen=True)
class CandidateFeaturePanelRule:
    table: str
    key_column: str
    built_at_column: str = "built_at"
    retain_latest_keys: int = 3
    protect_if_referenced_by_lifecycle_training_config: bool = True
    protect_if_referenced_by_model_selection_run: bool = True


@dataclass(frozen=True)
class ModelPredictionRule:
    table: str
    model_id_column: str | None = None
    run_id_column: str | None = None
    model_id_source_table: str | None = None
    model_id_source_run_id_column: str = "run_id"
    model_id_source_model_id_column: str = "model_id"


@dataclass(frozen=True)
class StorageRetentionPolicy:
    protected_model_statuses: tuple[str, ...]
    candidate_feature_panels: tuple[CandidateFeaturePanelRule, ...]
    model_prediction_tables: tuple[ModelPredictionRule, ...]
    model_file_roots: tuple[str, ...]
    optuna_study_roots: tuple[str, ...]
    defaults: dict[str, Any]


def _as_tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, Iterable):
        return tuple(str(item) for item in value)
    return (str(value),)


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        import yaml  # type: ignore
    except Exception as exc:  # pragma: no cover - local runtime has PyYAML.
        raise RuntimeError("PyYAML is required to load storage_retention.yaml") from exc
    loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return loaded if isinstance(loaded, dict) else {}


def load_storage_retention_policy(path: str | Path | None = None) -> StorageRetentionPolicy:
    config_path = Path(path) if path is not None else CONFIG_PATH
    raw = _load_yaml(config_path)
    feature_rules = []
    for item in raw.get("candidate_feature_panels", []) or []:
        if not isinstance(item, dict):
            continue
        feature_rules.append(
            CandidateFeaturePanelRule(
                table=str(item["table"]),
                key_column=str(item.get("key_column", "feature_set_id")),
                built_at_column=str(item.get("built_at_column", "built_at")),
                retain_latest_keys=max(int(item.get("retain_latest_keys", 3) or 0), 0),
                protect_if_referenced_by_lifecycle_training_config=bool(
                    item.get("protect_if_referenced_by_lifecycle_training_config", True)
                ),
                protect_if_referenced_by_model_selection_run=bool(
                    item.get("protect_if_referenced_by_model_selection_run", True)
                ),
            )
        )
    prediction_rules = []
    for item in raw.get("model_prediction_tables", []) or []:
        if not isinstance(item, dict):
            continue
        prediction_rules.append(
            ModelPredictionRule(
                table=str(item["table"]),
                model_id_column=item.get("model_id_column"),
                run_id_column=item.get("run_id_column"),
                model_id_source_table=item.get("model_id_source_table"),
                model_id_source_run_id_column=str(item.get("model_id_source_run_id_column", "run_id")),
                model_id_source_model_id_column=str(item.get("model_id_source_model_id_column", "model_id")),
            )
        )
    return StorageRetentionPolicy(
        protected_model_statuses=_as_tuple(raw.get("protected_model_statuses")),
        candidate_feature_panels=tuple(feature_rules),
        model_prediction_tables=tuple(prediction_rules),
        model_file_roots=_as_tuple(raw.get("model_file_roots")),
        optuna_study_roots=_as_tuple(raw.get("optuna_study_roots")),
        defaults=raw.get("defaults", {}) if isinstance(raw.get("defaults"), dict) else {},
    )


def _table_exists(conn, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM information_schema.tables WHERE table_name = ? LIMIT 1",
        (table,),
    ).fetchone() is not None


def _columns(conn, table: str) -> set[str]:
    if not _table_exists(conn, table):
        return set()
    return {
        row[0]
        for row in conn.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = ?",
            (table,),
        ).fetchall()
    }


def _quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _safe_name(value: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in value)[:80] or "item"


def _model_id_values(conn, table: str, column: str) -> set[str]:
    if not _table_exists(conn, table) or column not in _columns(conn, table):
        return set()
    return {
        str(row["model_id"])
        for row in conn.execute(
            f"SELECT DISTINCT {_quote_ident(column)} AS model_id FROM {_quote_ident(table)} WHERE {_quote_ident(column)} IS NOT NULL"
        ).fetchall()
        if row["model_id"]
    }


def protected_model_id_reasons(conn, policy: StorageRetentionPolicy) -> dict[str, list[str]]:
    reasons: dict[str, list[str]] = {}

    def protect(model_id: str | None, reason: str) -> None:
        if not model_id:
            return
        reasons.setdefault(str(model_id), []).append(reason)

    statuses = set(policy.protected_model_statuses)
    if statuses and _table_exists(conn, "mart_model_lifecycle"):
        for row in conn.execute(
            "SELECT model_id, status FROM mart_model_lifecycle WHERE status IN (" +
            ",".join(["?"] * len(statuses)) + ")",
            tuple(statuses),
        ).fetchall():
            protect(row["model_id"], f"lifecycle_status:{row['status']}")
    for table, column, reason in (
        ("mart_challenger_evidence_bundle", "model_id", "evidence_bundle"),
        ("mart_tdx_keep_promotion_gate", "challenger_model_id", "promotion_gate_challenger"),
        ("mart_tdx_keep_promotion_gate", "champion_model_id", "promotion_gate_champion"),
        ("mart_pipeline_run_manifest", "model_id", "pipeline_manifest"),
    ):
        for model_id in _model_id_values(conn, table, column):
            protect(model_id, reason)
    for table in ("mart_daily_recommendation", "mart_daily_topk_view_cache"):
        cols = _columns(conn, table)
        if "model_id" not in cols:
            continue
        where = "WHERE model_id IS NOT NULL"
        if "is_primary" in cols:
            where += " AND is_primary = TRUE"
        for row in conn.execute(
            f"SELECT DISTINCT model_id FROM {_quote_ident(table)} {where}"
        ).fetchall():
            protect(row["model_id"], f"primary_output:{table}")
    return reasons


def protected_model_ids(conn, policy: StorageRetentionPolicy) -> set[str]:
    return set(protected_model_id_reasons(conn, policy))


def protected_feature_set_ids(
    conn,
    *,
    include_lifecycle_training_config: bool = True,
    include_model_selection_runs: bool = True,
) -> set[str]:
    if (
        not include_lifecycle_training_config
        or not _table_exists(conn, "mart_model_lifecycle")
        or "training_config" not in _columns(conn, "mart_model_lifecycle")
    ):
        protected: set[str] = set()
    else:
        protected = set()
        rows = conn.execute("SELECT training_config FROM mart_model_lifecycle").fetchall()
        for row in rows:
            raw = row["training_config"]
            if not raw:
                continue
            try:
                payload = json.loads(raw)
            except Exception:
                continue
            for key in ("feature_set_id", "retention_feature_set_id"):
                value = payload.get(key)
                if value:
                    protected.add(str(value))
    if include_model_selection_runs and _table_exists(conn, "mart_model_selection_run"):
        cols = _columns(conn, "mart_model_selection_run")
        if "feature_set_id" in cols:
            rows = conn.execute(
                """
                SELECT DISTINCT feature_set_id
                  FROM mart_model_selection_run
                 WHERE feature_set_id IS NOT NULL
                   AND feature_set_id <> ''
                """
            ).fetchall()
            protected.update(str(row["feature_set_id"]) for row in rows if row["feature_set_id"])
    if include_model_selection_runs and _table_exists(conn, "mart_hybrid_feature_panel_build"):
        cols = _columns(conn, "mart_hybrid_feature_panel_build")
        for col in ("output_feature_set_id", "base_feature_set_id", "extra_feature_set_id"):
            if col not in cols:
                continue
            rows = conn.execute(
                f"""
                SELECT DISTINCT {_quote_ident(col)} AS feature_set_id
                  FROM mart_hybrid_feature_panel_build
                 WHERE {_quote_ident(col)} IS NOT NULL
                   AND {_quote_ident(col)} <> ''
                """
            ).fetchall()
            protected.update(str(row["feature_set_id"]) for row in rows if row["feature_set_id"])
    return protected


def _candidate_feature_panel_cleanup(
    conn,
    policy: StorageRetentionPolicy,
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for rule in policy.candidate_feature_panels:
        protected_sets = protected_feature_set_ids(
            conn,
            include_lifecycle_training_config=rule.protect_if_referenced_by_lifecycle_training_config,
            include_model_selection_runs=rule.protect_if_referenced_by_model_selection_run,
        )
        cols = _columns(conn, rule.table)
        if rule.key_column not in cols:
            continue
        order_col = rule.built_at_column if rule.built_at_column in cols else rule.key_column
        rows = conn.execute(
            f"""
            SELECT {rule.key_column} AS key_value,
                   COUNT(*) AS row_count,
                   MAX({order_col}) AS last_built_at
              FROM {rule.table}
             GROUP BY {rule.key_column}
             ORDER BY MAX({order_col}) DESC NULLS LAST, {rule.key_column} DESC
            """
        ).fetchall()
        retained_keys = {row["key_value"] for row in rows[: rule.retain_latest_keys]}
        for row in rows[rule.retain_latest_keys :]:
            key_value = row["key_value"]
            if key_value in protected_sets:
                continue
            if key_value in retained_keys:
                continue
            candidates.append(
                {
                    "kind": "candidate_feature_panel",
                    "table": rule.table,
                    "key_column": rule.key_column,
                    "key_value": key_value,
                    "row_count": int(row["row_count"] or 0),
                    "last_built_at": row["last_built_at"],
                    "reason": f"older than latest {rule.retain_latest_keys} feature_set_id values",
                }
            )
    return candidates


def _model_prediction_cleanup(conn, policy: StorageRetentionPolicy) -> list[dict[str, Any]]:
    protected = protected_model_ids(conn, policy)
    candidates: list[dict[str, Any]] = []
    for rule in policy.model_prediction_tables:
        cols = _columns(conn, rule.table)
        if rule.model_id_column and rule.model_id_column in cols:
            rows = conn.execute(
                f"""
                SELECT {rule.model_id_column} AS model_id, COUNT(*) AS row_count
                  FROM {rule.table}
                 GROUP BY {rule.model_id_column}
                """
            ).fetchall()
            for row in rows:
                model_id = row["model_id"]
                if model_id not in protected:
                    candidates.append(
                        {
                            "kind": "model_prediction_rows",
                            "table": rule.table,
                            "model_id": model_id,
                            "row_count": int(row["row_count"] or 0),
                            "reason": "model is not champion/challenger/shadow",
                        }
                    )
            continue

        if (
            rule.run_id_column
            and rule.run_id_column in cols
            and rule.model_id_source_table
            and _table_exists(conn, rule.model_id_source_table)
        ):
            source_cols = _columns(conn, rule.model_id_source_table)
            if (
                rule.model_id_source_run_id_column not in source_cols
                or rule.model_id_source_model_id_column not in source_cols
            ):
                continue
            rows = conn.execute(
                f"""
                SELECT p.{rule.run_id_column} AS run_id,
                       f.{rule.model_id_source_model_id_column} AS model_id,
                       COUNT(*) AS row_count
                  FROM {rule.table} p
                  JOIN {rule.model_id_source_table} f
                    ON p.{rule.run_id_column} = f.{rule.model_id_source_run_id_column}
                 GROUP BY p.{rule.run_id_column}, f.{rule.model_id_source_model_id_column}
                """
            ).fetchall()
            for row in rows:
                if row["model_id"] not in protected:
                    candidates.append(
                        {
                            "kind": "model_prediction_rows",
                            "table": rule.table,
                            "run_id": row["run_id"],
                            "model_id": row["model_id"],
                            "row_count": int(row["row_count"] or 0),
                            "reason": "walk-forward run belongs to non-protected model",
                        }
                    )
    return candidates


def _model_file_cleanup(policy: StorageRetentionPolicy, protected: set[str]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for root_text in policy.model_file_roots:
        root = (REPO_ROOT / root_text).resolve()
        if not root.exists():
            continue
        for path in root.glob("*.pkl"):
            model_id = path.stem
            if model_id in protected:
                continue
            candidates.append(
                {
                    "kind": "model_file",
                    "path": str(path),
                    "model_id": model_id,
                    "bytes": path.stat().st_size,
                    "reason": "pkl model file is not protected by lifecycle status",
                }
            )
    return candidates


def active_optuna_study_artifacts(policy: StorageRetentionPolicy) -> list[dict[str, Any]]:
    artifacts: list[dict[str, Any]] = []
    for root_text in policy.optuna_study_roots:
        root = (REPO_ROOT / root_text).resolve()
        if not root.exists():
            continue
        for path in sorted(root.glob("*.sqlite3")):
            artifacts.append(
                {
                    "path": str(path),
                    "bytes": path.stat().st_size,
                    "reason": "persistent Optuna study artifact; preserve for resumable search",
                }
            )
    return artifacts


def compaction_guidance(policy: StorageRetentionPolicy, candidates: list[dict[str, Any]]) -> dict[str, Any]:
    threshold = int(policy.defaults.get("large_delete_row_threshold", 1_000_000) or 0)
    row_candidates = [
        item
        for item in candidates
        if item.get("kind") in {"candidate_feature_panel", "model_prediction_rows"}
        and int(item.get("row_count") or 0) >= threshold
    ]
    estimated_rows = sum(int(item.get("row_count") or 0) for item in row_candidates)
    recommended = bool(row_candidates)
    return {
        "recommended": recommended,
        "large_delete_row_threshold": threshold,
        "estimated_large_delete_rows": estimated_rows,
        "reason": (
            "large row deletes can leave DuckDB file space reusable but not immediately returned to filesystem"
            if recommended
            else None
        ),
        "commands": [
            "duckdb <database.duckdb> \"CHECKPOINT;\"",
            (
                "For full file compaction during a maintenance window: "
                "EXPORT DATABASE '<export_dir>'; create a fresh DuckDB database; "
                "IMPORT DATABASE '<export_dir>'; verify counts; then swap after backup."
            ),
        ] if recommended else [],
    }


def plan_storage_cleanup(
    conn,
    policy: StorageRetentionPolicy | None = None,
) -> dict[str, Any]:
    policy = policy or load_storage_retention_policy()
    protected_reasons = protected_model_id_reasons(conn, policy)
    protected = set(protected_reasons)
    candidates = [
        *_candidate_feature_panel_cleanup(conn, policy),
        *_model_prediction_cleanup(conn, policy),
        *_model_file_cleanup(policy, protected),
    ]
    optuna_artifacts = active_optuna_study_artifacts(policy)
    compaction = compaction_guidance(policy, candidates)
    return {
        "mode": "dry_run",
        "protected_model_statuses": list(policy.protected_model_statuses),
        "protected_model_ids": sorted(protected),
        "protected_model_reasons": protected_reasons,
        "active_optuna_study_artifacts": optuna_artifacts,
        "active_optuna_study_count": len(optuna_artifacts),
        "compaction": compaction,
        "candidate_count": len(candidates),
        "candidates": candidates,
        "requires_backup_before_delete": bool(policy.defaults.get("require_backup_before_delete", True)),
    }


def _backup_table_name(run_id: str, index: int, table: str) -> str:
    return f"backup_storage_cleanup_{_safe_name(run_id)}_{index:03d}_{_safe_name(table)}"


def _backup_and_delete_rows(
    conn,
    *,
    candidate: dict[str, Any],
    run_id: str,
    index: int,
) -> dict[str, Any]:
    table = str(candidate["table"])
    backup_table = _backup_table_name(run_id, index, table)
    if candidate["kind"] == "candidate_feature_panel":
        key_col = str(candidate["key_column"])
        key_value = candidate["key_value"]
        conn.execute(
            f"CREATE OR REPLACE TABLE {_quote_ident(backup_table)} AS "
            f"SELECT * FROM {_quote_ident(table)} WHERE {_quote_ident(key_col)} = ?",
            (key_value,),
        )
        before = conn.execute(f"SELECT COUNT(*) AS n FROM {_quote_ident(backup_table)}").fetchone()["n"]
        conn.execute(
            f"DELETE FROM {_quote_ident(table)} WHERE {_quote_ident(key_col)} = ?",
            (key_value,),
        )
        return {"backup_table": backup_table, "deleted_rows": int(before or 0)}
    if candidate["kind"] == "model_prediction_rows":
        if candidate.get("model_id") is not None:
            conn.execute(
                f"CREATE OR REPLACE TABLE {_quote_ident(backup_table)} AS "
                f"SELECT * FROM {_quote_ident(table)} WHERE model_id = ?",
                (candidate["model_id"],),
            )
            before = conn.execute(f"SELECT COUNT(*) AS n FROM {_quote_ident(backup_table)}").fetchone()["n"]
            conn.execute(
                f"DELETE FROM {_quote_ident(table)} WHERE model_id = ?",
                (candidate["model_id"],),
            )
            return {"backup_table": backup_table, "deleted_rows": int(before or 0)}
        if candidate.get("run_id") is not None:
            conn.execute(
                f"CREATE OR REPLACE TABLE {_quote_ident(backup_table)} AS "
                f"SELECT * FROM {_quote_ident(table)} WHERE run_id = ?",
                (candidate["run_id"],),
            )
            before = conn.execute(f"SELECT COUNT(*) AS n FROM {_quote_ident(backup_table)}").fetchone()["n"]
            conn.execute(
                f"DELETE FROM {_quote_ident(table)} WHERE run_id = ?",
                (candidate["run_id"],),
            )
            return {"backup_table": backup_table, "deleted_rows": int(before or 0)}
    return {"backup_table": None, "deleted_rows": 0}


def execute_storage_cleanup(
    conn,
    policy: StorageRetentionPolicy | None = None,
    *,
    approve: bool = False,
    run_id: str | None = None,
    backup_dir: str | Path | None = None,
) -> dict[str, Any]:
    policy = policy or load_storage_retention_policy()
    if not approve:
        raise RuntimeError("storage cleanup execution requires approve=True")
    run_id = run_id or f"storage_cleanup_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"
    report = plan_storage_cleanup(conn, policy)
    backup_root = Path(backup_dir) if backup_dir else (REPO_ROOT / "data" / "cleanup_backups" / run_id)
    backup_root.mkdir(parents=True, exist_ok=True)
    executed: list[dict[str, Any]] = []
    for index, candidate in enumerate(report["candidates"], start=1):
        item = dict(candidate)
        if candidate["kind"] in {"candidate_feature_panel", "model_prediction_rows"}:
            item.update(_backup_and_delete_rows(conn, candidate=candidate, run_id=run_id, index=index))
        elif candidate["kind"] == "model_file":
            source = Path(str(candidate["path"]))
            dest = backup_root / source.name
            if source.exists():
                shutil.copy2(source, dest)
                source.unlink()
                item.update({"backup_path": str(dest), "deleted_files": 1})
            else:
                item.update({"backup_path": None, "deleted_files": 0})
        executed.append(item)
    manifest = {
        "mode": "execute_approved",
        "run_id": run_id,
        "created_at": datetime.utcnow().isoformat(timespec="seconds"),
        "dry_run_report": report,
        "executed": executed,
    }
    (backup_root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    conn.commit()
    return {
        "mode": "execute_approved",
        "run_id": run_id,
        "backup_dir": str(backup_root),
        "candidate_count": report["candidate_count"],
        "compaction": report.get("compaction"),
        "executed_count": len(executed),
        "executed": executed,
    }
